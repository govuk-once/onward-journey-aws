"""
API Gateway Custom Authoriser - WebSocket $connect Handshake Token Verification.

Acts as a custom REQUEST authoriser for inbound WebSocket API Gateway connection
handshakes ($connect route). Validates client access by extracting a pre-shared
authorisation token from the connection query parameters (`auth_token`) and
matching it against a token-to-actor registry retrieved from AWS Secrets Manager
(cached in-memory across warm Lambda executions with a TTL).

Execution outcomes:
- Valid token: Generates an IAM 'Allow' policy scoped strictly to the incoming
      methodArn and attaches the resolved `actor_id` to the authoriser context.
- Invalid token or missing auth_token: Returns an IAM 'Deny' policy.
- Malformed invocation (missing methodArn) or Infra Failure: Fails fast and raises
      exceptions (e.g., ValueError, ClientError) resulting in API GW 500 errors.
"""

import json
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


# Initialise client with VPC Endpoint URL override if private DNS is disabled
ENDPOINT_URL = _resolve_endpoint_url(os.environ.get("SECRETS_ENDPOINT_URL"))
SECRETS_MANAGER_CLIENT = boto3.client(
    "secretsmanager",
    endpoint_url=ENDPOINT_URL,
)

SECRET_CACHE: Optional[Dict[str, str]] = None
CACHE_TIMESTAMP: float = 0.0
CACHE_TTL_SECONDS = (
    300  # 5mins in-memory retention before refreshing from Secrets Manager
)


def get_token_registry() -> Dict[str, str]:
    """Retrieve and parse the token registry from AWS Secrets Manager with a TTL cache."""
    global SECRET_CACHE, CACHE_TIMESTAMP

    if SECRET_CACHE is None or (time.time() - CACHE_TIMESTAMP > CACHE_TTL_SECONDS):
        secret_arn = os.environ.get("SECRET_ARN")
        if not secret_arn:
            logger.error("Missing required environment variable: SECRET_ARN")
            raise RuntimeError("Missing required environment variable: SECRET_ARN")

        logger.info("Fetching token registry from Secrets Manager: %s", secret_arn)

        response = SECRETS_MANAGER_CLIENT.get_secret_value(SecretId=secret_arn)
        raw_secret = response.get("SecretString")
        if not raw_secret:
            logger.error("Secret %s contains no SecretString payload", secret_arn)
            raise ValueError(f"Secret {secret_arn} does not contain text SecretString")

        try:
            parsed_secret = json.loads(raw_secret)
            SECRET_CACHE = parsed_secret
            CACHE_TIMESTAMP = time.time()
        except json.JSONDecodeError:
            logger.error(
                "Failed to parse token registry secret as JSON payload", exc_info=True
            )
            raise ValueError("Token registry secret is not valid JSON")

    return SECRET_CACHE


def generate_iam_policy(
    principal_id: str,
    effect: str,
    resource: str,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Construct standard IAM policy required by API Gateway Custom Authorisers.

    API Gateway v2 requires all context values to be primitive types (str, int, float, bool).
    """
    policy = {
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
    if context:
        policy["context"] = context
    return policy


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Validate WebSocket $connect handshake auth_token and map to an authorised actor_id."""
    method_arn = event.get("methodArn")
    if not method_arn:
        logger.error("Authoriser request rejected: missing mandatory 'methodArn'")
        raise ValueError("Missing required 'methodArn' in authoriser event")

    query_params = event.get("queryStringParameters") or {}

    # Case-insensitive lookup for auth_token query parameter
    raw_token = next(
        (v for k, v in query_params.items() if k.lower() == "auth_token"),
        None,
    )
    incoming_token = raw_token.strip() if raw_token else None

    # Safe log evaluating presence without leaking sensitive token credentials to CloudWatch
    logger.info(
        "Executing WebSocket authoriser for resource=%s | Has auth_token=%s",
        method_arn,
        bool(incoming_token),
    )

    if not incoming_token:
        logger.warning(
            "DENY: Connection rejected due to missing auth_token query parameter"
        )
        return generate_iam_policy("unauthorized", "Deny", method_arn)

    token_registry = get_token_registry()
    actor_id = token_registry.get(incoming_token)

    if not actor_id:
        logger.warning("DENY: Connection rejected due to unrecognised auth_token")
        return generate_iam_policy("unauthorized", "Deny", method_arn)

    logger.info("ALLOW: Authorised WebSocket connection for actor_id=%s", actor_id)
    return generate_iam_policy(
        principal_id=str(actor_id),
        effect="Allow",
        resource=method_arn,
        context={"actor_id": str(actor_id)},
    )
