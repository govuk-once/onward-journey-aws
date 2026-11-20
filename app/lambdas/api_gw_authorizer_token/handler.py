"""
API Gateway Custom Authoriser - Shared URL Query-Parameter Token Verification.

Acts as a custom REQUEST authoriser for inbound API Gateway endpoints (both
WebSocket `$connect` handshakes and REST webhook gateways). Validates access by
extracting a pre-shared token from the query parameter named by `TOKEN_QUERY_PARAM`
(e.g. `auth_token` or `token`) and matching it in constant time against a
token-to-principal registry fetched fresh from AWS Secrets Manager on each invocation.

Authoriser context (`CONTEXT_KEY`):
- Configured (e.g. `CONTEXT_KEY=actor_id` for WebSocket `$connect`): Attaches
      `{"actor_id": principal_id}` to the IAM policy `context` so downstream routers
      can enforce per-user session ownership via `requestContext.authorizer.actor_id`.
- Unset (e.g. inbound CRM webhook gateways): Omits the `context` block and relies
      solely on `principalId` for gateway-level authentication and audit logging,
      as webhook events are routed by payload identifiers rather than user identity.

Execution outcomes:
- Valid token: Generates an IAM 'Allow' policy scoped strictly to the incoming
      methodArn (with optional `CONTEXT_KEY` mapping when configured).
- Invalid, duplicate, or missing token parameter: Returns an IAM 'Deny' policy.
- Malformed invocation (missing methodArn) or Infra Failure: Fails fast and raises
      exceptions resulting in API GW 500 errors.
"""

import hmac
import json
import logging
import os
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


def _require_endpoint_url(env_var_name: str) -> str:
    """Validate and format a mandatory VPC endpoint URL on Lambda cold start."""
    raw_url = os.environ.get(env_var_name)
    resolved = _resolve_endpoint_url(raw_url)
    if not resolved:
        logger.error("Missing required environment variable: %s", env_var_name)
        raise RuntimeError(f"Missing required environment variable: {env_var_name}")
    return resolved


ENDPOINT_URL = _require_endpoint_url("SECRETS_ENDPOINT_URL")
SECRETS_MANAGER_CLIENT = boto3.client(
    "secretsmanager",
    endpoint_url=ENDPOINT_URL,
)


def get_token_registry() -> Dict[str, str]:
    """Retrieve and validate the token-to-principal registry from AWS Secrets Manager."""
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
    except json.JSONDecodeError:
        logger.error(
            "Failed to parse token registry secret as JSON payload", exc_info=True
        )
        raise ValueError("Token registry secret is not valid JSON")

    if not isinstance(parsed_secret, dict) or not parsed_secret:
        logger.error("Token registry secret must be a non-empty JSON object")
        raise ValueError("Token registry secret must be a non-empty JSON object")

    validated_registry: Dict[str, str] = {}
    for token_key, principal_val in parsed_secret.items():
        if (
            not isinstance(token_key, str)
            or not token_key.strip()
            or not isinstance(principal_val, str)
            or not principal_val.strip()
        ):
            logger.error("Token registry secret contains invalid key/value entry")
            raise ValueError(
                "Token registry secret entries must map non-empty strings to non-empty strings"
            )
        validated_registry[token_key.strip()] = principal_val.strip()

    return validated_registry


def _resolve_principal_id(
    incoming_token: str, token_registry: Dict[str, str]
) -> Optional[str]:
    """Match incoming_token against registry keys using constant-time comparison."""
    matched_principal_id: Optional[str] = None
    for registered_token, candidate_principal_id in token_registry.items():
        if hmac.compare_digest(incoming_token, registered_token):
            matched_principal_id = candidate_principal_id
    return matched_principal_id


def generate_iam_policy(
    principal_id: str,
    effect: str,
    resource: str,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Construct standard IAM policy required by API Gateway Custom Authorisers.

    API Gateway requires all context values to be primitive types (str, int, float, bool).
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
    """Validate query-string token and map to an authorised principal_id."""
    method_arn = event.get("methodArn")
    if not method_arn:
        logger.error("Authoriser request rejected: missing mandatory 'methodArn'")
        raise ValueError("Missing required 'methodArn' in authoriser event")

    token_param_name = (os.environ.get("TOKEN_QUERY_PARAM") or "").strip()
    if not token_param_name:
        logger.error("Missing required environment variable: TOKEN_QUERY_PARAM")
        raise RuntimeError("Missing required environment variable: TOKEN_QUERY_PARAM")

    multi_params = event.get("multiValueQueryStringParameters") or {}
    matching_multi = [
        v
        for k, vals in multi_params.items()
        if k.lower() == token_param_name.lower() and isinstance(vals, list)
        for v in vals
    ]
    if len(matching_multi) > 1:
        logger.warning(
            "DENY: Request rejected due to duplicate %s query parameters",
            token_param_name,
        )
        return generate_iam_policy("unauthorized", "Deny", method_arn)

    query_params = event.get("queryStringParameters") or {}

    # Case-insensitive lookup for configured token query parameter
    raw_token = next(
        (v for k, v in query_params.items() if k.lower() == token_param_name.lower()),
        None,
    )
    incoming_token = raw_token.strip() if isinstance(raw_token, str) else None

    # Safe log evaluating presence without leaking sensitive token credentials to CloudWatch
    logger.info(
        "Executing token authoriser for resource=%s | Param=%s | Has token=%s",
        method_arn,
        token_param_name,
        bool(incoming_token),
    )

    if not incoming_token:
        logger.warning(
            "DENY: Request rejected due to missing %s query parameter",
            token_param_name,
        )
        return generate_iam_policy("unauthorized", "Deny", method_arn)

    token_registry = get_token_registry()
    principal_id = _resolve_principal_id(incoming_token, token_registry)

    if not principal_id:
        logger.warning(
            "DENY: Request rejected due to unrecognised %s",
            token_param_name,
        )
        return generate_iam_policy("unauthorized", "Deny", method_arn)

    context_key = (os.environ.get("CONTEXT_KEY") or "").strip()
    policy_context = {context_key: principal_id} if context_key else None

    logger.info("ALLOW: Authorised request for principal=%s", principal_id)
    return generate_iam_policy(
        principal_id=principal_id,
        effect="Allow",
        resource=method_arn,
        context=policy_context,
    )
