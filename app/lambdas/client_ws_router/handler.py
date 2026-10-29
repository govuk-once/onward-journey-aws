"""
WebSocket Client Router Lambda Function.

Handles client connection lifecycles ($connect, $disconnect) and routes incoming
user messages ($default).

Extracts authenticated actor identity strictly from the connection authoriser context.
Validates session state in DynamoDB:
- AI_CHAT: Dispatches frame and session context to Bedrock AgentCore.
- HUMAN_CHAT: Bypasses AgentCore and invokes CRM Tool Lambda directly.
Tracks session audit timestamps (created_at, updated_at).
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

AGENTCORE_CLIENT = boto3.client("bedrock-agentcore")
LAMBDA_CLIENT = boto3.client("lambda")
DYNAMODB_RESOURCE = boto3.resource("dynamodb")
SESSION_TTL_SECONDS = (
    14 * 86_400
)  # 14 days in seconds (14 * 86,400s) for session audit retention


def get_table():
    """Retrieve the DynamoDB sessions Table resource, failing fast if unconfigured."""
    table_name = os.environ.get("SESSIONS_TABLE_NAME")
    if not table_name:
        logger.error("Missing required environment variable: SESSIONS_TABLE_NAME")
        raise RuntimeError("Missing required environment variable: SESSIONS_TABLE_NAME")
    return DYNAMODB_RESOURCE.Table(table_name)


def get_session(thread_id: str) -> Optional[Dict[str, Any]]:
    table = get_table()
    response = table.get_item(Key={"session_id": thread_id})
    return response.get("Item")


def create_session(
    thread_id: str, actor_id: str, websocket_connection_id: str
) -> Dict[str, Any]:
    table = get_table()
    now_epoch = int(time.time())
    item = {
        "session_id": thread_id,
        "actor_id": actor_id,
        "websocket_connection_id": websocket_connection_id,
        "state": "AI_CHAT",
        "created_at": now_epoch,
        "updated_at": now_epoch,
        "ttl": now_epoch + SESSION_TTL_SECONDS,
    }
    table.put_item(Item=item)
    logger.info(f"Initialised session record for thread_id={thread_id}")
    return item


def touch_session(
    session: Dict[str, Any], websocket_connection_id: str
) -> Dict[str, Any]:
    """Refresh session updated_at, rolling TTL, and active connection ID."""
    table = get_table()
    now_epoch = int(time.time())
    new_ttl = now_epoch + SESSION_TTL_SECONDS
    thread_id = session["session_id"]

    table.update_item(
        Key={"session_id": thread_id},
        UpdateExpression="SET updated_at = :now, websocket_connection_id = :conn, #ttl = :ttl",
        ExpressionAttributeNames={"#ttl": "ttl"},
        ExpressionAttributeValues={
            ":now": now_epoch,
            ":conn": websocket_connection_id,
            ":ttl": new_ttl,
        },
    )

    session["updated_at"] = now_epoch
    session["websocket_connection_id"] = websocket_connection_id
    session["ttl"] = new_ttl
    return session


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
            f"Routing failed for thread_id={thread_id}: CRM_TOOL_LAMBDA_ARN environment variable is missing"
        )
        return {"statusCode": 500, "body": "Server configuration error"}

    if not crm_chat_id or not live_chat_identifier:
        logger.error(
            f"Routing to CRM failed for thread_id={thread_id}: "
            f"session is in HUMAN_CHAT but missing required attributes "
            f"(crm_chat_id={bool(crm_chat_id)}, live_chat_identifier={bool(live_chat_identifier)})"
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
        LAMBDA_CLIENT.invoke(
            FunctionName=crm_tool_arn,
            InvocationType="Event",
            Payload=json.dumps(payload).encode("utf-8"),
        )
        logger.info(
            f"Bypassed AI agent: forwarded message directly to CRM for thread_id={thread_id}"
        )
        return {"statusCode": 200, "body": "Message routed to human adviser"}

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "UnknownError")
        error_msg = e.response.get("Error", {}).get("Message", "No message provided")
        request_id = e.response.get("ResponseMetadata", {}).get("RequestId", "N/A")
        logger.error(
            f"AWS ClientError invoking CRM tool Lambda for thread_id={thread_id} | "
            f"Code={error_code} | RequestId={request_id} | Message={error_msg}",
            exc_info=True,
        )
        return {"statusCode": 500, "body": "Failed to route message to adviser"}


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
            f"Routing failed for websocket_connection_id={websocket_connection_id}: "
            f"AGENT_RUNTIME_ARN environment variable is missing"
        )
        return {"statusCode": 500, "body": "Server configuration error"}

    if not domain_name or not stage:
        logger.error(
            f"Routing to AgentCore failed for thread_id={thread_id}: "
            f"missing gateway context (domain_name={bool(domain_name)}, stage={bool(stage)})"
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

    try:
        AGENTCORE_CLIENT.invoke_agent_runtime(
            agentRuntimeArn=agent_runtime_arn,
            runtimeSessionId=thread_id,
            contentType="application/json",
            accept="application/json",
            payload=json.dumps(payload).encode("utf-8"),
        )
        logger.info(
            f"Successfully routed payload to AgentCore for websocket_connection_id={websocket_connection_id} | thread_id={thread_id}"
        )
        return {"statusCode": 200, "body": "Message routed"}

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "UnknownError")
        error_msg = e.response.get("Error", {}).get("Message", "No message provided")
        request_id = e.response.get("ResponseMetadata", {}).get("RequestId", "N/A")
        logger.error(
            f"AWS ClientError invoking AgentCore runtime for websocket_connection_id={websocket_connection_id} | "
            f"Code={error_code} | RequestId={request_id} | Message={error_msg}",
            exc_info=True,
        )
        return {"statusCode": 500, "body": "Failed to route message to AI agent"}


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Process incoming WebSocket frame and route according to lifecycle stage and state."""
    request_context = event.get("requestContext") or {}
    route_key = request_context.get("routeKey")
    websocket_connection_id = request_context.get("connectionId")
    domain_name = request_context.get("domainName")
    stage = request_context.get("stage")

    logger.info(
        f"Execution route={route_key} | WebSocketConnectionId={websocket_connection_id} | Stage={stage}"
    )

    if route_key == "$connect":
        logger.info(f"Client connected: {websocket_connection_id}")
        return {"statusCode": 200, "body": "Connected"}

    if route_key == "$disconnect":
        logger.info(f"Client disconnected: {websocket_connection_id}")
        return {"statusCode": 200, "body": "Disconnected"}

    if route_key == "$default":
        raw_body = event.get("body")

        if isinstance(raw_body, str):
            try:
                body = json.loads(raw_body)
            except json.JSONDecodeError:
                logger.warning(
                    f"Non-JSON payload received for websocket_connection_id={websocket_connection_id}"
                )
                body = {"message": raw_body}
        elif isinstance(raw_body, dict):
            body = raw_body
        else:
            body = {}

        user_message = body.get("message")
        thread_id = body.get("thread_id")

        if not user_message or not thread_id:
            logger.warning(
                f"Validation failed for websocket_connection_id={websocket_connection_id}: "
                f"missing required fields (message={bool(user_message)}, thread_id={bool(thread_id)})"
            )
            return {
                "statusCode": 400,
                "body": "Missing required payload attributes: 'message' or 'thread_id'",
            }

        thread_id = str(thread_id)

        if len(thread_id) < 33:
            logger.warning(
                f"Validation failed for websocket_connection_id={websocket_connection_id}: "
                f"thread_id length ({len(thread_id)}) is under minimum 33 chars"
            )
            return {
                "statusCode": 400,
                "body": "Invalid 'thread_id': Must be at least 33 characters long (e.g. UUIDv4 string)",
            }

        authorizer_context = request_context.get("authorizer") or {}
        actor_id = authorizer_context.get("actor_id")

        if not actor_id:
            logger.error(
                f"Authorisation context missing actor_id for websocket_connection_id={websocket_connection_id}"
            )
            return {
                "statusCode": 401,
                "body": "Unauthorised: Missing connection identity context",
            }

        session = get_session(thread_id)
        if session:
            session = touch_session(session, websocket_connection_id)
        else:
            session = create_session(thread_id, actor_id, websocket_connection_id)

        session_state = session.get("state")
        if not session_state:
            logger.error(f"Session {thread_id} in DynamoDB has no state attribute")
            return {"statusCode": 500, "body": "Invalid session state in database"}

        logger.info(
            f"Routing message for websocket_connection_id={websocket_connection_id} | thread_id={thread_id} | "
            f"state={session_state} | actor_id={actor_id} | message_length={len(user_message)}"
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
            f"Unrecognised session state: {session_state} for thread_id={thread_id}"
        )
        return {
            "statusCode": 500,
            "body": f"Unsupported session state: {session_state}",
        }

    logger.warning(
        f"Unsupported route received: route={route_key} | websocket_connection_id={websocket_connection_id}"
    )
    return {"statusCode": 400, "body": "Unsupported route"}
