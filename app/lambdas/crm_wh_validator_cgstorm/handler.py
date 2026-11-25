"""Content Guru Storm Webhook Cryptographic Validator Lambda Function.

Acts as the VPC-isolated ingress gatekeeper behind API Gateway (`POST /contentguru/webhook`)
for all inbound HTTP callbacks from Content Guru Storm.

All inbound Storm requests must carry a valid `WebhookSignature` header computed as
`base64(hmac_sha256(api_secret, raw_body))` over the exact unparsed wire body (rejected
with HTTP 401 if missing or invalid). Once authenticated, Storm sends two mutually
exclusive payload categories to this endpoint:

1. Webhook Registration Verification (`Event.Type` is `"Verify"` or `"WebhookVerification"`):
   - Sent synchronously by Storm when our registration script calls `POST /api/v1/webhooks`.
   - Must prove URL and secret ownership by returning HTTP 200 with
     `{"Response": "<hex_sha256(challenge + api_secret)>"}` before Storm saves the
     subscription. Never forwarded to `crm_wh_router`.

2. Live Chat Events (`Event.Type` is `"Message"`, `"Status"`, `"InteractionStatus"`, or `"Typing"`):
   - Sent during active adviser conversations after registration.
   - Validated events are handed off asynchronously (`InvocationType="Event"`) to
     `crm_wh_router` and this Lambda returns HTTP 200 immediately so downstream DynamoDB
     or WebSocket latency cannot cause Storm to time out and trigger duplicate retries.

Credential Handling:
- Fetches `api_secret` fresh from AWS Secrets Manager on every invocation (no module-level
  caching) so credential rotation or revocation takes effect immediately.

Error Handling & Retry Semantics:
- Client/Caller Errors (HTTP 400 / 401): Malformed payloads, invalid base64, or missing/invalid
  `WebhookSignature` headers return explicit 4xx responses so Storm drops the request without
  retrying and bad external traffic does not trigger Lambda error alarms.
- Infrastructure/Contract Faults (Unhandled -> HTTP 502): Secrets Manager failures, corrupt
  secret payloads, Lambda invoke errors, or invalid proxy event shapes bubble up unhandled so
  AWS Lambda records the error for CloudWatch alarms and API Gateway returns HTTP 502,
  signalling Storm to retry transient outages rather than dropping valid CRM messages.
"""

import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
from typing import Any, Dict, Optional

import boto3

logger = logging.getLogger()
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

DVLA_STORM_SECRET_ARN = os.environ["DVLA_STORM_SECRET_ARN"]
ROUTER_FUNCTION_NAME = os.environ["ROUTER_FUNCTION_NAME"]
SECRETS_ENDPOINT_URL = os.environ["SECRETS_ENDPOINT_URL"]

secrets_client = boto3.client("secretsmanager", endpoint_url=SECRETS_ENDPOINT_URL)
lambda_client = boto3.client("lambda")

VERIFICATION_EVENT_TYPES = ("Verify", "WebhookVerification")


def _response(status_code: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload),
    }


def _fetch_client_secret() -> str:
    """Fetches `api_secret` uncached so credential rotation or revocation applies immediately."""
    secret_resp = secrets_client.get_secret_value(SecretId=DVLA_STORM_SECRET_ARN)
    secret_data = json.loads(secret_resp["SecretString"])
    client_secret = secret_data["api_secret"]
    if not isinstance(client_secret, str) or not client_secret:
        raise ValueError("Secret 'api_secret' must be a non-empty string")
    return client_secret


def _extract_raw_body(event: Dict[str, Any]) -> Optional[str]:
    """Returns the unparsed wire body so HMAC-SHA256 signature checks match Storm's exact bytes."""
    raw_body = event.get("body")
    if raw_body is None:
        return ""
    if not isinstance(raw_body, str):
        raise TypeError(
            f"Expected event['body'] to be str or None, got {type(raw_body).__name__}"
        )
    if event.get("isBase64Encoded"):
        try:
            return base64.b64decode(raw_body).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return None
    return raw_body


def _get_header_case_insensitive(
    headers: Optional[Dict[str, Any]], target_name: str
) -> Optional[str]:
    if headers is None:
        return None
    if not isinstance(headers, dict):
        raise TypeError(
            f"Expected event['headers'] to be dict or None, got {type(headers).__name__}"
        )
    target_lower = target_name.lower()
    for key, value in headers.items():
        if key.lower() == target_lower and value is not None:
            return str(value).strip()
    return None


def _compute_webhook_signature(raw_body: str, client_secret: str) -> str:
    digest = hmac.new(
        client_secret.encode("utf-8"),
        raw_body.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def _compute_challenge_response(challenge: str, client_secret: str) -> str:
    return hashlib.sha256(f"{challenge}{client_secret}".encode("utf-8")).hexdigest()


def _get_event_envelope(payload: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return None
    envelope = payload.get("Event") if "Event" in payload else payload.get("event")
    return envelope if isinstance(envelope, dict) else None


def _is_registration_verification(payload: Any) -> bool:
    """Returns True strictly when `Event.Type` identifies a Storm registration handshake."""
    envelope = _get_event_envelope(payload)
    if not envelope:
        return False
    event_type = envelope.get("Type") if "Type" in envelope else envelope.get("type")
    return event_type in VERIFICATION_EVENT_TYPES


def _extract_verification_challenge(payload: Any) -> str:
    """Extracts the mandatory `Challenge` string from a verified registration payload."""
    envelope = _get_event_envelope(payload)
    if not envelope:
        raise ValueError("Verify event is missing Event envelope")

    body = envelope.get("Body") if "Body" in envelope else envelope.get("body")
    body_dict = body if isinstance(body, dict) else {}

    challenge = (
        envelope.get("Challenge")
        or envelope.get("challenge")
        or body_dict.get("Challenge")
        or body_dict.get("challenge")
    )
    if not isinstance(challenge, str) or not challenge:
        raise ValueError("Verify event is missing a valid Challenge string")

    return challenge


def _dispatch_to_router(raw_body: str, request_id: str) -> None:
    """Queues `raw_body` onto `crm_wh_router` via `InvocationType="Event"` so Storm
    receives an immediate HTTP 200 and cannot time out or replay events during
    downstream DynamoDB/WebSocket operations.
    """
    response = lambda_client.invoke(
        FunctionName=ROUTER_FUNCTION_NAME,
        InvocationType="Event",
        Payload=json.dumps({"body": raw_body}).encode("utf-8"),
    )

    status_code = response["StatusCode"]
    if status_code not in (200, 202):
        raise RuntimeError(
            f"Unexpected status code from async router invoke: {status_code}"
        )

    logger.info(
        "Dispatched validated Storm webhook event asynchronously to router",
        extra={
            "request_id": request_id,
            "router_function": ROUTER_FUNCTION_NAME,
        },
    )


def lambda_handler(event: Dict[str, Any], lambda_context: object) -> Dict[str, Any]:
    request_id = lambda_context.aws_request_id

    logger.info(
        "Received inbound Content Guru Storm webhook request",
        extra={"request_id": request_id},
    )

    raw_body = _extract_raw_body(event)
    if raw_body is None:
        logger.warning(
            "Rejected inbound webhook with invalid base64 body",
            extra={"request_id": request_id},
        )
        return _response(
            400, {"error": "Invalid base64 body", "request_id": request_id}
        )

    if not raw_body:
        logger.warning(
            "Rejected inbound webhook with empty body",
            extra={"request_id": request_id},
        )
        return _response(400, {"error": "Empty request body", "request_id": request_id})

    try:
        parsed_payload = json.loads(raw_body)
    except json.JSONDecodeError:
        logger.warning(
            "Rejected inbound webhook with malformed JSON body",
            extra={"request_id": request_id},
        )
        return _response(400, {"error": "Invalid JSON body", "request_id": request_id})

    client_secret = _fetch_client_secret()

    provided_signature = _get_header_case_insensitive(
        event.get("headers"), "WebhookSignature"
    )
    if not provided_signature:
        logger.warning(
            "Rejected inbound webhook missing WebhookSignature header",
            extra={"request_id": request_id},
        )
        return _response(
            401,
            {"error": "Missing WebhookSignature header", "request_id": request_id},
        )

    expected_signature = _compute_webhook_signature(raw_body, client_secret)
    if not hmac.compare_digest(provided_signature, expected_signature):
        logger.warning(
            "Rejected inbound webhook with invalid WebhookSignature",
            extra={"request_id": request_id},
        )
        return _response(
            401,
            {"error": "Invalid webhook signature", "request_id": request_id},
        )

    # Branch strictly by payload type so registration challenges and live events are isolated
    if _is_registration_verification(parsed_payload):
        try:
            challenge = _extract_verification_challenge(parsed_payload)
        except ValueError as e:
            logger.warning(
                f"Rejected malformed Verify payload: {e}",
                extra={"request_id": request_id},
            )
            return _response(400, {"error": str(e), "request_id": request_id})

        challenge_response = _compute_challenge_response(challenge, client_secret)
        logger.info(
            "Responding to Storm webhook verification challenge",
            extra={"request_id": request_id},
        )
        return _response(200, {"Response": challenge_response})

    _dispatch_to_router(raw_body, request_id)

    return _response(200, {"status": "accepted", "request_id": request_id})
