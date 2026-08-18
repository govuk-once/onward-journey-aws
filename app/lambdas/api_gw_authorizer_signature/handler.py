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
    principal_id = os.environ.get("PRINCIPAL_ID", "content-guru-authorizer")

    # Extract signature header (case-insensitive header lookup)
    incoming_signature = next(
        (v for k, v in headers.items() if k.lower() == "x-storm-signature"), None
    )
    payload_body = event.get("body") or ""

    if not incoming_signature or not payload_body:
        return generate_iam_policy("unauthorized", "Deny", method_arn)

    try:
        signing_secret = get_signing_secret()

        # Calculate expected HMAC-SHA256 signature hash
        expected_signature = hmac.new(
            signing_secret.encode("utf-8"),
            payload_body.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        # Prevent timing attacks: hmac.compare_digest compares all characters in equal time regardless of where a mismatch occurs, preventing attackers from guessing the hash.
        if hmac.compare_digest(incoming_signature, expected_signature):
            return generate_iam_policy(principal_id, "Allow", method_arn)
        else:
            return generate_iam_policy("unauthorized", "Deny", method_arn)

    except Exception as e:
        print(f"Error executing signature authoriser: {str(e)}")
        return generate_iam_policy("unauthorized", "Deny", method_arn)
