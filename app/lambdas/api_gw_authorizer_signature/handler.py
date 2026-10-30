"""
API Gateway Custom Authorizer - Content Guru Storm Cryptographic Signature Verification.

This Lambda function acts as a custom authorizer for inbound API Gateway
requests originating from Content Guru Storm webhooks. It validates the request
authenticity by calculating an HMAC-SHA256 signature over the raw HTTP request body
using a shared private secret retrieved from AWS Secrets Manager (cached in-memory
with a TTL for warm executions) and comparing it against the incoming `X-Storm-Signature`.

Execution outcomes:
- Valid signature: Generates an IAM 'Allow' policy scoped strictly to the incoming methodArn.
- Invalid or missing signature: Returns an IAM 'Deny' policy.
- Malformed invocation or Infra Failure: Fails fast and raises exceptions (e.g., ValueError).
"""

import base64
import hashlib
import hmac
import logging
import os
import time
from typing import Any, Dict, Optional

import boto3

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())


def _resolve_endpoint_url(endpoint_url: Optional[str]) -> Optional[str]:
    """Ensure the endpoint URL has a valid scheme required by botocore."""
    if not endpoint_url:
        return None
    cleaned = endpoint_url.strip()
    if not cleaned:
        return None
    if not cleaned.startswith(("http://", "https://")):
        return f"https://{cleaned}"
    return cleaned


ENDPOINT_URL = _resolve_endpoint_url(os.environ.get("SECRETS_ENDPOINT_URL"))
SECRETS_MANAGER_CLIENT = boto3.client(
    "secretsmanager",
    endpoint_url=ENDPOINT_URL,
)

# 5 minutes (300s) in-memory registry retention before refreshing from Secrets Manager
CACHE_TTL_SECONDS = 300
SECRET_CACHE: Optional[str] = None
CACHE_TIMESTAMP: float = 0.0


def get_signing_secret() -> str:
    """Retrieve private signing secret from AWS Secrets Manager with a TTL cache."""
    global SECRET_CACHE, CACHE_TIMESTAMP

    if SECRET_CACHE is None or (time.time() - CACHE_TIMESTAMP > CACHE_TTL_SECONDS):
        secret_arn = os.environ.get("SECRET_ARN")
        if not secret_arn:
            logger.error("Missing required environment variable: SECRET_ARN")
            raise RuntimeError("Missing required environment variable: SECRET_ARN")

        logger.info("Fetching signing secret from Secrets Manager: %s", secret_arn)

        response = SECRETS_MANAGER_CLIENT.get_secret_value(SecretId=secret_arn)
        raw_secret = response.get("SecretString")

        if not raw_secret:
            logger.error("Secret %s contains no SecretString payload", secret_arn)
            raise ValueError(f"Secret {secret_arn} does not contain text SecretString")

        SECRET_CACHE = raw_secret
        CACHE_TIMESTAMP = time.time()

    return SECRET_CACHE


def generate_iam_policy(
    principal_id: str, effect: str, resource: str
) -> Dict[str, Any]:
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


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Parse incoming Storm verification payload and compare HMAC-SHA256 signature hash."""
    method_arn = event.get("methodArn")
    if not method_arn:
        logger.error("Authoriser request rejected: missing mandatory 'methodArn'")
        raise ValueError("Missing required 'methodArn' in authoriser event")

    headers = event.get("headers") or {}
    principal_id = os.environ.get("PRINCIPAL_ID", "contentguru-authorizer")

    # Extract signature header (case-insensitive lookup with whitespace and casing normalization)
    raw_signature = next(
        (v for k, v in headers.items() if k.lower() == "x-storm-signature"), None
    )
    incoming_signature = raw_signature.strip().lower() if raw_signature else None

    payload_body = event.get("body") or ""
    is_base64_encoded = event.get("isBase64Encoded", False)

    # Safe debug logs: evaluate presence and length without dumping sensitive content
    has_signature = bool(incoming_signature)
    body_length = len(payload_body)
    logger.info(
        "Executing authorizer for resource=%s | Has X-Storm-Signature=%s | Body length=%d | Base64=%s",
        method_arn,
        has_signature,
        body_length,
        is_base64_encoded,
    )

    if not incoming_signature:
        logger.warning("DENY: Request rejected due to missing X-Storm-Signature header")
        return generate_iam_policy("unauthorized", "Deny", method_arn)

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
    logger.info("Signature verification match=%s", is_valid)

    if is_valid:
        return generate_iam_policy(principal_id, "Allow", method_arn)
    else:
        logger.warning("DENY: Request rejected due to signature mismatch")
        return generate_iam_policy("unauthorized", "Deny", method_arn)
