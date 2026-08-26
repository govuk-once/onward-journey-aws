"""
WebSocket Client Router Lambda Function.

Handles client connection lifecycles ($connect, $disconnect) and routes incoming
user messages ($default). Dispatches messages and session context to the Bedrock
AgentCore runtime for AI processing, with future scope to dynamically route frames
to either AgentCore AI or the CRM live chat queue based on session state.
"""

import json
import logging
import os
import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialise client outside handler for connection reuse across warm starts
AGENTCORE_CLIENT = boto3.client("bedrock-agentcore")
AGENT_RUNTIME_ARN = os.environ.get("AGENT_RUNTIME_ARN", "")


def lambda_handler(event, context):
    """Process incoming WebSocket frame and route according to lifecycle stage."""
    request_context = event.get("requestContext", {})
    route_key = request_context.get("routeKey")
    connection_id = request_context.get("connectionId")
    domain_name = request_context.get("domainName")
    stage = request_context.get("stage")

    logger.info(
        f"Execution route={route_key} | ConnectionId={connection_id} | Stage={stage}"
    )

    if route_key == "$connect":
        logger.info(f"Client connected: {connection_id}")
        return {"statusCode": 200, "body": "Connected"}

    elif route_key == "$disconnect":
        logger.info(f"Client disconnected: {connection_id}")
        return {"statusCode": 200, "body": "Disconnected"}

    elif route_key == "$default":
        if not AGENT_RUNTIME_ARN:
            logger.error(
                f"Routing failed for connection={connection_id}: AGENT_RUNTIME_ARN environment variable is missing"
            )
            return {"statusCode": 500, "body": "Server configuration error"}

        raw_body = event.get("body", "{}")

        # Robust body extraction handling both stringified JSON and raw strings
        if isinstance(raw_body, str):
            try:
                body = json.loads(raw_body)
            except json.JSONDecodeError:
                logger.warning(
                    f"Non-JSON payload received for connection={connection_id}"
                )
                body = {"message": raw_body}
        else:
            body = raw_body or {}

        user_message = body.get("message")
        actor_id = body.get("actor_id")
        thread_id = body.get("thread_id")

        # Strict validation: require user_message, actor_id, and thread_id
        if not user_message or not actor_id or not thread_id:
            logger.warning(
                f"Validation failed for connection={connection_id}. "
                f"Missing required fields (message={bool(user_message)}, actor_id={bool(actor_id)}, thread_id={bool(thread_id)})"
            )
            return {
                "statusCode": 400,
                "body": "Missing required payload attributes: 'message', 'actor_id', or 'thread_id'",
            }

        logger.info(
            f"Routing message for connection={connection_id} | thread_id={thread_id} | "
            f"AgentCore Runtime ARN={AGENT_RUNTIME_ARN} | message_length={len(user_message)}"
        )

        # Package session context alongside user message for outbound socket pushes & memory isolation
        payload = {
            "message": user_message,
            "connection_id": connection_id,
            "domain_name": domain_name,
            "stage": stage,
            "actor_id": actor_id,
            "thread_id": thread_id,
        }

        try:
            # TODO: Add dynamic session evaluation to determine whether to route payload to AgentCore AI or CRM live chat queue
            AGENTCORE_CLIENT.invoke_agent_runtime(
                agentRuntimeArn=AGENT_RUNTIME_ARN,
                runtimeSessionId=connection_id,
                inputText=json.dumps(payload),
            )
            logger.info(
                f"Successfully routed payload for connection={connection_id} | thread_id={thread_id}"
            )

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "UnknownError")
            error_msg = e.response.get("Error", {}).get(
                "Message", "No message provided"
            )
            request_id = e.response.get("ResponseMetadata", {}).get("RequestId", "N/A")

            logger.error(
                f"AWS ClientError invoking AgentCore runtime for connection={connection_id} | "
                f"Code={error_code} | RequestId={request_id} | Message={error_msg}",
                exc_info=True,
            )
            return {"statusCode": 500, "body": "Failed to route message to AI agent"}

        except Exception as e:
            logger.error(
                f"Unexpected error invoking AgentCore runtime for connection={connection_id}: {type(e).__name__}",
                exc_info=True,
            )
            return {"statusCode": 500, "body": "Failed to route message to AI agent"}

        return {"statusCode": 200, "body": "Message routed"}

    logger.warning(
        f"Unsupported route received: route={route_key} | ConnectionId={connection_id}"
    )
    return {"statusCode": 400, "body": "Unsupported route"}
