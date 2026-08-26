"""
API Gateway Custom Authorizer - Content Guru Storm Cryptographic Signature Verification.

This Lambda function acts as a custom authorizer for inbound API Gateway
requests originating from Content Guru Storm webhooks. It validates the request
authenticity by calculating an HMAC-SHA256 signature over the raw HTTP request body
using a shared private secret retrieved from AWS Secrets Manager (cached in-memory
for warm executions) and comparing it against the incoming `X-Storm-Signature` header.

Based on the verification result, it generates and returns an IAM policy
(Allow/Deny) to API Gateway to either permit or reject the incoming request.
"""

import os
import hmac
import hashlib
import base64
import boto3

# Initialise client outside handler for connection reuse across warm starts
SECRETS_MANAGER_CLIENT = boto3.client("secretsmanager")
SECRET_CACHE = None


def get_signing_secret():
    """Retrieve private signing secret from AWS Secrets Manager with in-memory caching."""
    global SECRET_CACHE
    if SECRET_CACHE is None:
        secret_arn = os.environ["SECRET_ARN"]
        response = SECRETS_MANAGER_CLIENT.get_secret_value(SecretId=secret_arn)
        SECRET_CACHE = response.get("SecretString", "")
    return SECRET_CACHE


def generate_iam_policy(principal_id, effect, resource):
    """Construct standard IAM policy required by API Gateway Custom Authorisers."""
    return {
        "principalId": principal_id,
        "policyDocument": {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Action": "execute-api:Invoke",
                    "Effect": effect,
                    "Resource": resource,
                }
            ],
        },
    }


def lambda_handler(event, context):
    """Parse incoming Storm verification payload and compare HMAC-SHA256 signature hash."""
    method_arn = event.get("methodArn", "*")
    headers = event.get("headers") or {}
    principal_id = os.environ.get("PRINCIPAL_ID", "contentguru-authorizer")

    # Extract signature header (case-insensitive lookup with whitespace and casing normalization for X-Storm-Signature)
    raw_signature = next(
        (v for k, v in headers.items() if k.lower() == "x-storm-signature"), None
    )
    incoming_signature = raw_signature.strip().lower() if raw_signature else None

    payload_body = event.get("body") or ""
    is_base64_encoded = event.get("isBase64Encoded", False)

    # Safe debug logs: evaluate presence and length without dumping sensitive content
    has_signature = bool(incoming_signature)
    body_length = len(payload_body)
    print(
        f"DEBUG: Executing authorizer for resource={method_arn} | Has X-Storm-Signature={has_signature} | Body length={body_length} | Base64={is_base64_encoded}"
    )

    if not incoming_signature:
        print("DENY: Request rejected due to missing X-Storm-Signature header")
        return generate_iam_policy("unauthorized", "Deny", method_arn)

    try:
        signing_secret = get_signing_secret()

        # Extract raw body bytes based on API Gateway encoding flag
        if is_base64_encoded:
            body_bytes = base64.b64decode(payload_body)
        elif isinstance(payload_body, str):
            body_bytes = payload_body.encode("utf-8")
        else:
            body_bytes = payload_body

        # Calculate expected HMAC-SHA256 signature hash over raw body bytes
        expected_signature = hmac.new(
            signing_secret.encode("utf-8"),
            body_bytes,
            hashlib.sha256,
        ).hexdigest()

        is_valid = hmac.compare_digest(incoming_signature, expected_signature)
        print(f"DEBUG: Signature verification match={is_valid}")

        if is_valid:
            return generate_iam_policy(principal_id, "Allow", method_arn)
        else:
            print("DENY: Request rejected due to signature mismatch")
            return generate_iam_policy("unauthorized", "Deny", method_arn)

    except Exception as e:
        print(f"ERROR: Authorizer execution failed: {type(e).__name__}")
        return generate_iam_policy("unauthorized", "Deny", method_arn)
