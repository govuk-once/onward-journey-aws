"""
WebSocket Client Router Lambda Function.

Handles client connection lifecycles ($connect, $disconnect) and routes incoming
user messages ($default).

Extracts authenticated actor identity strictly from the connection authoriser context.
Validates session state in DynamoDB:
- AI_CHAT: Dispatches frame and session context to Bedrock AgentCore.
- HUMAN_CHAT: Bypasses AgentCore and invokes CRM Tool Lambda directly.
Tracks session audit timestamps (created_at, updated_at).

# TODO: TACTICAL FAIL-FAST FOR USER TESTING PHASE
# To ensure maximum observability during moderated user research, this router intentionally
# returns HTTP 500 status codes when catching downstream integration faults or malformed payloads.
# A 500 status instructs API Gateway to sever the client's WebSocket connection (Close Code 1011).
#
# This "fail fast" behavior ensures that engineers monitoring Slack immediately
# spot bugs via connection drops, rather than masking failures behind silent frontend timeouts or
# getting users trapped in broken conversational states.
#
# FOR PRODUCTION:
# This approach must be refactored before integration with the parent chat application:
# 1. The API Gateway `$default` route must be configured with a Route Response integration.
# 2. Exceptions and validation errors caught here must return a `200 OK` (to keep the socket alive)
#    along with a standardised JSON error frame in the body (e.g. `{"type": "error", "message": "..."}`).
# 3. For downstream CRM failures during `HUMAN_CHAT`, the session state must be explicitly
#    auto-downgraded in DynamoDB back to `AI_CHAT` before returning the error frame to the user.
"""

import json
import logging
import os
import time
from typing import Any, Dict, Optional

import boto3
from botocore.exceptions import ClientError

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


AGENT_RUNTIME_ENDPOINT = _require_endpoint_url("AGENT_RUNTIME_ENDPOINT_URL")
AGENTCORE_CLIENT = boto3.client(
    "bedrock-agentcore",
    endpoint_url=AGENT_RUNTIME_ENDPOINT,
)
LAMBDA_ENDPOINT = _require_endpoint_url("LAMBDA_ENDPOINT_URL")
LAMBDA_CLIENT = boto3.client(
    "lambda",
    endpoint_url=LAMBDA_ENDPOINT,
)
DYNAMODB_RESOURCE = boto3.resource("dynamodb")

# 14 days in seconds (14 * 86,400s) for session audit retention
SESSION_TTL_SECONDS = 14 * 86_400
TABLE_CACHE: Optional[Any] = None


def get_table():
    """Retrieve the DynamoDB sessions Table resource with in-memory caching."""
    global TABLE_CACHE
    if TABLE_CACHE is None:
        table_name = os.environ.get("SESSIONS_TABLE_NAME")
        if not table_name:
            logger.error("Missing required environment variable: SESSIONS_TABLE_NAME")
            raise RuntimeError(
                "Missing required environment variable: SESSIONS_TABLE_NAME"
            )
        TABLE_CACHE = DYNAMODB_RESOURCE.Table(table_name)
    return TABLE_CACHE


def upsert_and_get_session(
    thread_id: str, actor_id: str, websocket_connection_id: str
) -> Dict[str, Any]:
    """
        Atomically upsert session state in DynamoDB and return the latest attributes.

        Uses an atomic `update_item` operation:
            - If the thread_id does not exist, DynamoDB initialises a new session item.
            - If the thread_id already exists, DynamoDB updates the timestamps, connection ID,
                and rolling TTL without resetting existing conversational state (e.g. HUMAN_CHAT).
    - Guarantees tenancy isolation by preventing any actor from writing to a session owned by another actor.
    """
    table = get_table()
    now_epoch = int(time.time())
    new_ttl = now_epoch + SESSION_TTL_SECONDS

    try:
        response = table.update_item(
            Key={"session_id": thread_id},
            UpdateExpression=(
                # 1. Attributes updated on EVERY interaction (rolling activity window):
                "SET updated_at = :now, "
                "websocket_connection_id = :conn, "
                "#ttl = :ttl, "
                # 2. Attributes set ONLY when the record is first created:
                #    if_not_exists ensures we do not overwrite the creator or reset state mid-conversation
                "actor_id = if_not_exists(actor_id, :actor), "
                "#state = if_not_exists(#state, :default_state), "
                "created_at = if_not_exists(created_at, :now)"
            ),
            # Security Guard: Allow the write if the session is brand new OR if the
            # authenticated caller matches the existing session owner.
            # Throws ConditionalCheckFailedException if another actor attempts to access this thread_id,
            # which intentionally bubbles up to crash the Lambda and sever the attacker's WebSocket connection.
            ConditionExpression="attribute_not_exists(actor_id) OR actor_id = :actor",
            # Aliases for DynamoDB reserved keywords ('ttl' and 'state')
            ExpressionAttributeNames={
                "#ttl": "ttl",
                "#state": "state",
            },
            ExpressionAttributeValues={
                ":now": now_epoch,
                ":conn": websocket_connection_id,
                ":ttl": new_ttl,
                ":actor": actor_id,
                ":default_state": "AI_CHAT",
            },
            # Instructs DynamoDB to return the full record immediately after the write, eliminating the need for a follow-up get_item call.
            ReturnValues="ALL_NEW",
        )
        return response.get("Attributes", {})
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            logger.error(
                "Session ownership violation: thread_id=%s is already claimed by another actor. "
                "Caller actor_id=%s does not match the session owner. Connection ID: %s",
                thread_id,
                actor_id,
                websocket_connection_id,
            )
        raise


def route_to_crm(
    user_message: str,
    thread_id: str,
    actor_id: str,
    crm_chat_id: Optional[str],
    live_chat_identifier: Optional[str],
) -> Dict[str, Any]:
    """Forward user message directly to CRM tool Lambda, bypassing Bedrock AgentCore."""
    crm_tool_arn = os.environ.get("CRM_TOOL_LAMBDA_ARN")
    if not crm_tool_arn:
        logger.error(
            "Routing failed for thread_id=%s: CRM_TOOL_LAMBDA_ARN environment variable is missing",
            thread_id,
        )
        raise RuntimeError("Missing required environment variable: CRM_TOOL_LAMBDA_ARN")

    if not crm_chat_id or not live_chat_identifier:
        logger.error(
            "Routing to CRM failed for thread_id=%s: session is in HUMAN_CHAT but missing required attributes (crm_chat_id=%s, live_chat_identifier=%s)",
            thread_id,
            bool(crm_chat_id),
            bool(live_chat_identifier),
        )
        return {
            "statusCode": 500,
            "body": "Invalid session: missing CRM routing attributes",
        }

    payload = {
        "method": "forward_user_message_to_crm",
        "message": user_message,
        "thread_id": thread_id,
        "actor_id": actor_id,
        "crm_chat_id": crm_chat_id,
        "live_chat_identifier": live_chat_identifier,
    }

    try:
        response = LAMBDA_CLIENT.invoke(
            FunctionName=crm_tool_arn,
            InvocationType="RequestResponse",
            Payload=json.dumps(payload).encode("utf-8"),
        )

        result = json.loads(response["Payload"].read())

        if "FunctionError" in response or "error" in result:
            error_detail = result.get("error") or result.get(
                "errorMessage", "Unknown error"
            )
            logger.error(
                "CRM tool execution failed for thread_id=%s: %s",
                thread_id,
                error_detail,
            )
            # TODO (Production Refactor): See top-level docstring. Replace 500 with session auto-downgrade and 200 error frame.
            return {
                "statusCode": 500,
                "body": f"CRM tool execution failed: {error_detail}",
            }

    except ClientError as e:
        logger.error(
            "Failed to invoke CRM tool for thread_id=%s: %s",
            thread_id,
            str(e),
            exc_info=True,
        )
        # TODO (Production Refactor): See top-level docstring. Replace 500 with session auto-downgrade and 200 error frame.
        return {
            "statusCode": 500,
            "body": f"Downstream CRM invoke error: {str(e)}",
        }

    logger.info(
        "Bypassed AI agent: forwarded message directly to CRM for thread_id=%s",
        thread_id,
    )
    return {"statusCode": 200, "body": "Message routed to human adviser"}


def route_to_agentcore(
    user_message: str,
    thread_id: str,
    actor_id: str,
    websocket_connection_id: str,
    domain_name: Optional[str],
    stage: Optional[str],
) -> Dict[str, Any]:
    """Forward message and session context to Bedrock AgentCore runtime."""
    agent_runtime_arn = os.environ.get("AGENT_RUNTIME_ARN")
    if not agent_runtime_arn:
        logger.error(
            "Routing failed for websocket_connection_id=%s: AGENT_RUNTIME_ARN environment variable is missing",
            websocket_connection_id,
        )
        raise RuntimeError("Missing required environment variable: AGENT_RUNTIME_ARN")

    if not domain_name or not stage:
        logger.error(
            "Routing to AgentCore failed for thread_id=%s: missing gateway context (domain_name=%s, stage=%s)",
            thread_id,
            bool(domain_name),
            bool(stage),
        )
        return {
            "statusCode": 500,
            "body": "Server configuration error: missing gateway context",
        }

    payload = {
        "message": user_message,
        "connection_id": websocket_connection_id,
        "domain_name": domain_name,
        "stage": stage,
        "actor_id": actor_id,
        "thread_id": thread_id,
    }

    AGENTCORE_CLIENT.invoke_agent_runtime(
        agentRuntimeArn=agent_runtime_arn,
        runtimeSessionId=thread_id,
        contentType="application/json",
        accept="application/json",
        payload=json.dumps(payload).encode("utf-8"),
    )
    logger.info(
        "Successfully routed payload to AgentCore for websocket_connection_id=%s | thread_id=%s",
        websocket_connection_id,
        thread_id,
    )
    return {"statusCode": 200, "body": "Message routed"}


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Process incoming WebSocket frame and route according to lifecycle stage and state."""
    request_context = event.get("requestContext") or {}
    route_key = request_context.get("routeKey")
    websocket_connection_id = request_context.get("connectionId")
    domain_name = request_context.get("domainName")
    stage = request_context.get("stage")

    logger.info(
        "Execution route=%s | WebSocketConnectionId=%s | Stage=%s",
        route_key,
        websocket_connection_id,
        stage,
    )

    if route_key == "$connect":
        logger.info("Client connected: %s", websocket_connection_id)
        return {"statusCode": 200, "body": "Connected"}

    if route_key == "$disconnect":
        logger.info("Client disconnected: %s", websocket_connection_id)
        return {"statusCode": 200, "body": "Disconnected"}

    if route_key == "$default":
        raw_body = event.get("body")

        if isinstance(raw_body, str):
            try:
                body = json.loads(raw_body)
            except json.JSONDecodeError:
                logger.warning(
                    "Non-JSON payload received for websocket_connection_id=%s",
                    websocket_connection_id,
                )
                body = {"message": raw_body}
        elif isinstance(raw_body, dict):
            body = raw_body
        else:
            body = {}

        user_message = body.get("message")
        thread_id = body.get("thread_id")

        if not user_message or not thread_id:
            logger.error(
                "Validation failed for websocket_connection_id=%s: missing required fields (message=%s, thread_id=%s)",
                websocket_connection_id,
                bool(user_message),
                bool(thread_id),
            )
            # TODO (Production Refactor): See top-level docstring. Replace 500 with 200 JSON error frame.
            return {
                "statusCode": 500,
                "body": "Missing required payload attributes: 'message' or 'thread_id'",
            }

        thread_id = str(thread_id)

        if len(thread_id) < 33:
            logger.error(
                "Validation failed for websocket_connection_id=%s: thread_id length (%s) is under minimum 33 chars",
                websocket_connection_id,
                len(thread_id),
            )
            # TODO (Production Refactor): See top-level docstring. Replace 500 with 200 JSON error frame.
            return {
                "statusCode": 500,
                "body": "Invalid 'thread_id': Must be at least 33 characters long (e.g. UUIDv4 string)",
            }

        authorizer_context = request_context.get("authorizer") or {}
        actor_id = authorizer_context.get("actor_id")

        if not actor_id:
            logger.error(
                "Authorisation context missing actor_id for websocket_connection_id=%s",
                websocket_connection_id,
            )
            return {
                "statusCode": 401,
                "body": "Unauthorised: Missing connection identity context",
            }

        session = upsert_and_get_session(thread_id, actor_id, websocket_connection_id)

        session_state = session.get("state")
        if not session_state:
            logger.error("Session %s in DynamoDB has no state attribute", thread_id)
            return {"statusCode": 500, "body": "Invalid session state in database"}

        logger.info(
            "Routing message for websocket_connection_id=%s | thread_id=%s | state=%s | actor_id=%s | message_length=%s",
            websocket_connection_id,
            thread_id,
            session_state,
            actor_id,
            len(user_message),
        )

        if session_state == "HUMAN_CHAT":
            return route_to_crm(
                user_message=user_message,
                thread_id=thread_id,
                actor_id=actor_id,
                crm_chat_id=session.get("crm_chat_id"),
                live_chat_identifier=session.get("live_chat_identifier"),
            )

        if session_state == "AI_CHAT":
            return route_to_agentcore(
                user_message=user_message,
                thread_id=thread_id,
                actor_id=actor_id,
                websocket_connection_id=websocket_connection_id,
                domain_name=domain_name,
                stage=stage,
            )

        logger.error(
            "Unrecognised session state: %s for thread_id=%s",
            session_state,
            thread_id,
        )
        return {
            "statusCode": 500,
            "body": f"Unsupported session state: {session_state}",
        }

    logger.warning(
        "Unsupported route received: route=%s | websocket_connection_id=%s",
        route_key,
        websocket_connection_id,
    )
    return {"statusCode": 400, "body": "Unsupported route"}
