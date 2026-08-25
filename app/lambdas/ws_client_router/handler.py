"""
WebSocket Client Router Lambda Function.

Handles client connection lifecycles ($connect, $disconnect) and routes incoming
user messages ($default). Currently dispatches messages and session context to the
Bedrock AgentCore runtime for AI processing, with future scope to dynamically route
frames to either AgentCore AI or the CRM live chat queue based on active session state.
"""

import json
import logging
import os
import boto3

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
        raw_body = event.get("body", "{}")

        # Robust body extraction handling both stringified JSON and raw strings
        if isinstance(raw_body, str):
            try:
                body = json.loads(raw_body)
            except json.JSONDecodeError:
                body = {"message": raw_body}
        else:
            body = raw_body or {}

        user_message = body.get("message", "")

        logger.info(
            f"Routing message from connection={connection_id} to AgentCore Runtime ARN={AGENT_RUNTIME_ARN} (length={len(user_message)})"
        )

        # Package session context alongside user message for outbound socket pushes
        payload = {
            "message": user_message,
            "connection_id": connection_id,
            "domain_name": domain_name,
            "stage": stage,
        }

        try:
            # TODO: Add dynamic session evaluation to determine whether to route payload to AgentCore AI or CRM live chat queue
            AGENTCORE_CLIENT.invoke_agent_runtime(
                agentRuntimeArn=AGENT_RUNTIME_ARN,
                runtimeSessionId=connection_id,
                inputText=json.dumps(payload),
            )
            logger.info(f"Successfully routed payload for connection={connection_id}")

        except Exception as e:
            logger.error(
                f"Error invoking AgentCore runtime: {type(e).__name__} - {str(e)}"
            )
            return {"statusCode": 500, "body": "Failed to route message to AI agent"}

        return {"statusCode": 200, "body": "Message routed"}

    return {"statusCode": 400, "body": "Unsupported route"}
