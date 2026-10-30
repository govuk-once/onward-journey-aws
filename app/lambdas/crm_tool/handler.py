"""
CRM Tool Lambda.

Acts as a backend tool service for CRM operations, interfacing with external
CRM platforms (such as Content Guru Storm for DVLA) via the ProviderFactory.
Facilitates human-in-the-loop transitions and ingests ongoing user messages during
active adviser sessions.

Supported Methods:
    - check_chat_availability: Queries whether human advisers are online and accepting chats.
    - connect_to_live_chat: Generates metadata, routing tokens, and handoff signals for adviser transfer.
    - forward_user_message_to_crm: Ingests active user messages during an established human session.
"""

import json
import logging
import os
from typing import Any, Dict, Optional
from integrations.factory import Capability, ProviderFactory
from integrations.tooling import log_metric

logger = logging.getLogger()
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

SUPPORTED_METHODS = {
    "forward_user_message_to_crm",
    "check_chat_availability",
    "connect_to_live_chat",
}


def create_audit_json(event: Dict[str, Any]) -> str:
    """
    Scrubs the raw user message from the event payload and computes
    message_length for safe CloudWatch audit logging.
    """
    audit_event = {k: v for k, v in event.items() if k != "message"}
    if "message" in event:
        audit_event["message_length"] = len(event.get("message") or "")
    return json.dumps(audit_event)


def execute_crm_method(
    method: str, thread_id: str, chat_id: Optional[str], event: Dict[str, Any]
) -> Dict[str, Any]:
    """Routes the validated method to the appropriate CRM provider action."""

    # Reject unsupported methods immediately before provider resolution
    if method not in SUPPORTED_METHODS:
        logger.error(f"Unknown method: {method}")
        return {"error": f"METHOD_ERROR: {method} not supported."}

    # Phase 2: User message forwarding during an active human session.
    # TODO: Replace placeholder response with multi-provider dispatch via ProviderFactory
    #       once the DVLA Storm message forwarding API integration is implemented.
    if method == "forward_user_message_to_crm":
        logger.info(
            "Forwarding active user message to CRM for thread_id: %s", thread_id
        )
        return {"status": "success", "message": "User message forwarded to CRM"}

    # Phase 1: Resolve provider adapter via ProviderFactory for live chat orchestration.
    # TODO: Register concrete DVLA Storm adapter in ProviderFactory once the provider class is authored.
    provider = ProviderFactory.get_provider(chat_id, Capability.CHAT_AVAILABILITY)

    # Phase 1a: Pre-handoff availability check invoked by AgentCore orchestrator
    # TODO: Confirm Storm queue availability response schema matches orchestrator expectations.
    if method == "check_chat_availability":
        return provider.fetch_adviser_availability()

    # Phase 1b: Initial live chat session creation invoked by AgentCore orchestrator
    # TODO: Align handoff signal payload fields with DVLA Storm session initialisation contracts.
    if method == "connect_to_live_chat":
        result = provider.generate_handoff_signal(event)
        log_metric("LiveHandoffInitiated", {"Target": chat_id, "Trace": thread_id})
        return result


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Entry point for CRM operations, routing requests to provider integrations.

    Serves two distinct architectural callers:
        1. AgentCore Orchestrator (Synchronous JSON-RPC):
           Executes pre-handoff availability checks ('check_chat_availability') and
           session initialisation ('connect_to_live_chat').
        2. WebSocket Client Router (Asynchronous Event - AI Bypass):
           Ingests ongoing user chat messages ('forward_user_message_to_crm')
           whilst the session is in 'HUMAN_CHAT' state.

    Args:
        event: The Lambda invocation payload containing:
            - method (str, required): The target action ('check_chat_availability',
              'connect_to_live_chat', or 'forward_user_message_to_crm').
            - thread_id (str, required): Conversation thread identifier used for tracing.
            - live_chat_identifier (str, required for provider operations): Target service provider
              key (e.g. 'dvla-storm') resolved via ProviderFactory.
            - message (str, required for forwarding): Outbound user message text.
            - crm_chat_id (str, optional): External conversation identifier from CRM provider.
            - actor_id (str, optional): Authenticated user identifier for audit tracing.
        context: AWS Lambda execution context.

    Returns:
        Dict[str, Any]: Operation result dictionary or error payload.
    """
    method = event.get("method")
    thread_id = event.get("thread_id")
    chat_id = event.get("live_chat_identifier")

    audit_json = create_audit_json(event)

    # Fail fast and loudly if mandatory invocation parameters are missing
    for required_field in ["method", "thread_id"]:
        if not event.get(required_field):
            logger.error(
                "Missing mandatory '%s' parameter in event: %s",
                required_field,
                audit_json,
            )
            return {
                "error": f"VALIDATION_ERROR: Missing required '{required_field}' parameter."
            }

    # Record sanitised audit trace without leaking user message content
    logger.info(f"CRM TOOL CALL | ThreadId: {thread_id} | Event: {audit_json}")

    try:
        return execute_crm_method(method, thread_id, chat_id, event)
    except Exception as e:
        logger.error(f"CRM TOOL TOP-LEVEL FAILURE: {e}", exc_info=True)
        return {"error": "SERVICE_ERROR: Internal Tool Error."}
