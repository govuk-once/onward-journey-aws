"""
CRM Tool Lambda.

This Lambda function acts as a tool server for CRM-related operations,
primarily interacting with external CRM providers (e.g. Genesys) via the
ProviderFactory. It facilitates human-in-the-loop transitions by checking
adviser availability and generating handoff signals.

Supported Methods:
    - check_chat_availability: Determines if human advisers are currently
      available to handle a live chat request.
    - connect_to_live_chat: Generates the necessary metadata and handoff
      signals to transition a user from the AI assistant to a human adviser.
"""

import json
from integrations.factory import ProviderFactory, Capability
from integrations.tooling import log_metric

def lambda_handler(event, context):
    """
    Entry point for CRM tool requests, routing to the appropriate provider method.

    Args:
        event (dict): The Lambda event object, expected to contain:
            - method (str): The name of the tool method to execute.
            - live_chat_identifier (str): Unique ID for the specific chat service.
            - thread_id (str, optional): Identifier for the conversation thread.
            - session_id (str, optional): Identifier for the user session.
        context (LambdaContext): AWS Lambda context object.

    Returns:
        dict/str: The raw tool execution result or an error dictionary. AgentCore Gateway
            handles the JSON-RPC serialization.
    """
    # Log the ID for tracing with AgentCore Memory. We check both 'session_id' and 'thread_id' for consistency.
    trace_id = event.get("thread_id") or event.get("session_id") or "UNKNOWN_SESSION"
    print(f"CRM TOOL CALL | Trace: {trace_id} | Event: {json.dumps(event)}")

    chat_id = event.get("live_chat_identifier")
    method = event.get("method")

    try:
        provider = ProviderFactory.get_provider(chat_id, Capability.CHAT_AVAILABILITY)

        if "check_chat_availability" in method:
            return provider.fetch_adviser_availability()

        elif "connect_to_live_chat" in method:
            result = provider.generate_handoff_signal(event)
            # --- HANDOFF STATUS LOG ---
            log_metric("LiveHandoffInitiated", {"Target": chat_id, "Trace": trace_id})
            return result

        else:
            print(f"❌ [ERROR] Unknown method: {method}")
            return {"error": f"METHOD_ERROR: {method} not supported."}

    except Exception as e:
        print(f"❌ [ERROR] CRM TOOL TOP-LEVEL FAILURE: {str(e)}")
        return {"error": "SERVICE_ERROR: Internal Tool Error."}
