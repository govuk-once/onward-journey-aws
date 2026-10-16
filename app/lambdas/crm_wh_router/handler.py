"""CRM Webhook Router Lambda Function.

Ingests authenticated inbound webhooks from Content Guru Storm via API Gateway.
Maps incoming CRM chat IDs to active user sessions by querying the DynamoDB
sessions table via the crm_chat_id Global Secondary Index (GSI), and relays
adviser events (messages, typing indicators) to the client's open WebSocket connection.

When an adviser concludes a conversation (Status: Ended), this handler reverts
the session state to AI_CHAT, refreshes the 14-day audit retention TTL, and
pushes a termination event so the frontend seamlessly switches back to the AI assistant.
"""

import json
import logging
import os
import time
from typing import Any, Dict, Optional, Tuple

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

# Initialise AWS clients outside the handler to reuse TCP connections across warm starts
SESSIONS_TABLE_NAME = os.getenv("SESSIONS_TABLE_NAME", "")
SESSIONS_GSI_NAME = os.getenv("SESSIONS_GSI_NAME", "crm_chat_id-index")
WEBSOCKET_ENDPOINT = os.getenv("WEBSOCKET_ENDPOINT", "")

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(SESSIONS_TABLE_NAME) if SESSIONS_TABLE_NAME else None

# ApiGatewayManagementApi requires an explicit HTTPS endpoint URL for WebSocket callbacks
apigw_client = (
    boto3.client("apigatewaymanagementapi", endpoint_url=WEBSOCKET_ENDPOINT)
    if WEBSOCKET_ENDPOINT
    else None
)

# 14 days in seconds (14 * 86,400 = 1,209,600s) for audit compliance and TTL cleanup
SESSION_TTL_SECONDS = 14 * 86_400


def _response(status_code: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Builds a standardised JSON response for API Gateway."""
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload),
    }


def _parse_storm_payload(payload: Any) -> Tuple[Optional[str], str, Dict[str, Any]]:
    """Unwraps Content Guru Storm envelopes and extracts the interaction ID and event type.

    Storm webhook payloads use inconsistent schemas across event types:
    - 'Message' events store the chat interaction ID at Body.ConversationId
    - 'Status' and 'Typing' events store the chat interaction ID at Body.To.Id

    Returns:
        A tuple of (crm_chat_id, event_type, event_body).
    """
    if not isinstance(payload, dict):
        return None, "Unknown", {}

    # Extract Content Guru Storm event envelope
    event_envelope = payload.get("Event")
    if not isinstance(event_envelope, dict):
        return None, "Unknown", {}

    event_type = event_envelope.get("Type", "Message")
    body_candidate = event_envelope.get("Body")
    event_body = body_candidate if isinstance(body_candidate, dict) else {}
    to_obj = event_body.get("To")
    to_id = to_obj.get("Id") if isinstance(to_obj, dict) else None
    crm_chat_id = event_body.get("ConversationId") or to_id

    return crm_chat_id, str(event_type), event_body


def _get_session_by_crm_chat_id(
    crm_chat_id: str, request_id: str
) -> Optional[Dict[str, Any]]:
    """Queries the GSI on the sessions table to find the matching user session."""
    if not table:
        logger.error(
            "DynamoDB sessions table is not configured",
            extra={"request_id": request_id},
        )
        return None

    try:
        response = table.query(
            IndexName=SESSIONS_GSI_NAME,
            KeyConditionExpression="crm_chat_id = :cid",
            ExpressionAttributeValues={":cid": crm_chat_id},
            Limit=1,
        )
        items = response.get("Items", [])
        return items[0] if items else None
    except ClientError as e:
        logger.error(
            f"DynamoDB GSI query failed for crm_chat_id={crm_chat_id}: {e}",
            extra={"request_id": request_id, "crm_chat_id": crm_chat_id},
            exc_info=True,
        )
        return None


def _revert_state_to_ai_chat(session_id: str, request_id: str) -> None:
    """Reverts conversation state to AI_CHAT and resets the 14-day audit retention TTL."""
    if not table:
        logger.error(
            "DynamoDB sessions table is not configured",
            extra={"request_id": request_id},
        )
        return

    now_epoch = int(time.time())
    new_ttl = now_epoch + SESSION_TTL_SECONDS

    try:
        table.update_item(
            Key={"session_id": session_id},
            UpdateExpression="SET #s = :ai_state, #ttl = :new_ttl",
            ExpressionAttributeNames={
                "#s": "state",
                "#ttl": "ttl",
            },
            ExpressionAttributeValues={
                ":ai_state": "AI_CHAT",
                ":new_ttl": new_ttl,
            },
        )
        logger.info(
            f"Reverted state to AI_CHAT and refreshed TTL (+14 days) for session_id={session_id}",
            extra={"request_id": request_id, "session_id": session_id},
        )
    except ClientError as e:
        logger.error(
            f"Failed to revert state for session_id={session_id}: {e}",
            extra={"request_id": request_id, "session_id": session_id},
            exc_info=True,
        )


def _extract_message_text(body: Dict[str, Any]) -> str:
    """Extracts plain text from Content Guru MessageParts."""
    message_parts = body.get("MessageParts")
    if isinstance(message_parts, list):
        for part in message_parts:
            if isinstance(part, dict) and part.get("Type") == "Text":
                return str(part.get("Text", ""))
    return ""


def _extract_agent_name(body: Dict[str, Any]) -> Optional[str]:
    """Extracts the adviser's display name or username across Storm event formats."""
    from_obj = body.get("From")
    if isinstance(from_obj, dict) and from_obj.get("Alias"):
        return from_obj["Alias"]
    return body.get("UserName")


def _push_to_websocket(
    connection_id: str,
    frame: Dict[str, Any],
    session_id: str,
    request_id: str,
) -> bool:
    """Pushes a message frame to the client WebSocket, handling stale connections."""
    if not apigw_client:
        logger.error(
            "ApiGatewayManagementApi client is not configured",
            extra={"request_id": request_id},
        )
        return False

    try:
        apigw_client.post_to_connection(
            ConnectionId=connection_id,
            Data=json.dumps(frame).encode("utf-8"),
        )
        logger.info(
            f"Relayed frame to WebSocket connection_id={connection_id}",
            extra={
                "request_id": request_id,
                "session_id": session_id,
                "connection_id": connection_id,
            },
        )
        return True
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code")
        if error_code == "GoneException":
            # 410 GoneException: The client has disconnected or closed their browser tab
            logger.warning(
                f"Stale WebSocket connection detected (GoneException): connection_id={connection_id}",
                extra={
                    "request_id": request_id,
                    "session_id": session_id,
                    "connection_id": connection_id,
                },
            )
        else:
            logger.error(
                f"Failed to post to WebSocket connection_id={connection_id}: {e}",
                extra={
                    "request_id": request_id,
                    "session_id": session_id,
                    "connection_id": connection_id,
                },
                exc_info=True,
            )
        return False


def lambda_handler(event: Dict[str, Any], lambda_context: object) -> Dict[str, Any]:
    """Ingests Storm webhooks, resolves active user sessions, and relays messages."""
    # Fail fast if lambda_context does not satisfy the AWS Lambda runtime contract
    request_id = lambda_context.aws_request_id

    # Tie execution context to the CloudWatch log stream for traceability
    logger.info("Ingesting CRM webhook payload", extra={"request_id": request_id})

    # 1. Parse JSON body (reject malformed payloads with 400 Bad Request)
    try:
        raw_payload = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        logger.error(
            "Failed to parse JSON body from incoming webhook",
            extra={"request_id": request_id},
        )
        return _response(400, {"error": "Invalid JSON body", "request_id": request_id})

    # 2. Extract Storm envelope, interaction ID, and event type
    crm_chat_id, event_type, event_body = _parse_storm_payload(raw_payload)

    if not crm_chat_id:
        logger.warning(
            "Webhook rejected: missing mandatory interaction identifier",
            extra={
                "request_id": request_id,
                "payload_keys": (
                    list(raw_payload.keys()) if isinstance(raw_payload, dict) else []
                ),
            },
        )
        return _response(
            400,
            {
                "error": "Missing required interaction identifier",
                "request_id": request_id,
            },
        )

    # 3. Lookup active session in DynamoDB
    # Return 200 OK with a 'dropped' status rather than 404 to acknowledge receipt
    # and prevent Content Guru Storm's webhook engine from triggering automated retry storms.
    session = _get_session_by_crm_chat_id(crm_chat_id, request_id)
    if not session:
        logger.warning(
            f"No session found for crm_chat_id={crm_chat_id}. Dropping webhook frame.",
            extra={"request_id": request_id, "crm_chat_id": crm_chat_id},
        )
        return _response(
            200,
            {
                "status": "dropped",
                "reason": "session_not_found",
                "request_id": request_id,
            },
        )

    session_id = session.get("session_id")
    websocket_connection_id = session.get("websocket_connection_id")

    if not websocket_connection_id:
        logger.warning(
            f"Session {session_id} has no active connection registered. Dropping frame.",
            extra={
                "request_id": request_id,
                "session_id": session_id,
                "crm_chat_id": crm_chat_id,
            },
        )
        return _response(
            200,
            {
                "status": "dropped",
                "reason": "recipient_offline",
                "request_id": request_id,
            },
        )

    # 4. Handle chat termination or in-flight adviser events
    # Content Guru emits 'Status' events with Status 'Ended' when an interaction closes
    is_chat_ended = event_type == "Status" and event_body.get("Status") == "Ended"

    if is_chat_ended:
        _revert_state_to_ai_chat(session_id, request_id)
        ws_frame = {
            "type": "state_change",
            "state": "AI_CHAT",
            "reason": "adviser_disconnected",
            "text": "The adviser has ended the chat. You are now reconnected with GOV.UK Chat.",
            "message": "The adviser has ended the chat. You are now reconnected with GOV.UK Chat.",
        }
    else:
        message_text = _extract_message_text(event_body)
        ws_frame = {
            "type": (
                "human_message" if event_type == "Message" else event_type.lower()
            ),
            "text": message_text,
            "message": message_text,
            "sender": "adviser",
            "metadata": {
                "agent_name": _extract_agent_name(event_body),
                "typing_indicator": event_body.get("TypingIndicator"),
            },
        }

    # 5. Push formatted frame to the client's active WebSocket connection
    delivered = _push_to_websocket(
        websocket_connection_id, ws_frame, session_id, request_id
    )
    if not delivered:
        return _response(
            200,
            {
                "status": "dropped",
                "reason": "recipient_offline",
                "request_id": request_id,
            },
        )

    return _response(200, {"status": "acknowledged", "request_id": request_id})
