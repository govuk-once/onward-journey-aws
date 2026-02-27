"""
GOV.UK Onward Journey - AgentCore/Lambda Hybrid Orchestrator.
Manages LangGraph state and Bedrock AgentCore interactions.
"""

import boto3
import json
import os
import socket
import uuid
import requests
from typing import Annotated, TypedDict
from botocore.config import Config

from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from langchain_aws import ChatBedrockConverse
from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph_checkpoint_aws import AgentCoreMemorySaver


GATEWAY_URL = os.environ.get("GATEWAY_URL")
GATEWAY_ENDPOINT_URL = os.environ.get("GATEWAY_ENDPOINT_URL")
MEMORY_ID = os.environ.get("MEMORY_ID")
# TODO: Make naming of endpoint URLs consistent
ENDPOINT_URL = os.environ.get("AGENT_RUNTIME_ENDPOINT_URL")
BEDROCK_RUNTIME_URL = os.environ.get("BEDROCK_RUNTIME_ENDPOINT")
SECRETS_ENDPOINT_URL = os.environ.get("SECRETS_ENDPOINT_URL")
AWS_REGION = "eu-west-2"

SYSTEM_PROMPT = """You are a specialized GOV.UK Contact Assistant.
Your primary duty is to provide contact details for specific government departments while filtering out irrelevant search results.

STRICT FILTERING RULES:
1. IDENTIFY: Determine exactly which government department the user is asking about (e.g., DWP, HMRC, Home Office).
2. FILTER: When you receive tool results, look at the 'service' and 'info' fields.
3. OMIT: If a result belongs to a DIFFERENT department than the one requested, you MUST NOT list its details.

ONWARD JOURNEY (LIVE CHAT) & CONTACT RULES:
1. CHECK AVAILABILITY: If a valid 'live_chat_identifier' is provided by the database, you MUST check if agents are available before responding.
2. OFFER CHAT: If agents are available, offer the user the option to connect to a live person.
3. HANDOVER SUMMARY (BRIEFING NOTE): If the user agrees to connect, you must generate a 2-3 sentence 'summary'.
   - DESTINATION: A professional 'Briefing Note' for the human advisor via the 'connect_to_live_chat' tool.
   - SOURCE: Focus primarily on the current session's "Incomplete Task." Use Long-Term Memory (AgentCore) ONLY to identify if this is a repeat attempt or if there is a persistent blocker (e.g., "User has been unable to bypass the 'Submit' error for three sessions").
   - CONTENT: Identify the specific Government Service (e.g., Border Force, HMRC Tax), the specific goal (e.g., reporting a crime, checking a claim), and the immediate blocker that triggered this handoff.
   - EXCLUSION: Omit any historical context that is not directly relevant to the current service request.
4. PHONE FALLBACK: If 'live_chat_identifier' is missing, null, empty, or if agents are currently OFFLINE, you MUST provide the 'phone_number' as the primary contact method instead.

EXCEPTION RULES:
1. NO MATCH: If NO results match the requested department, inform the user you couldn't find a direct match but mention the closest government service available based on the database results.

STRICT FORMATTING RULES:
1. Start your response immediately with the department details or a helpful opening sentence.
2. Use Markdown (## for headers, * for bullets).
"""


def check_connection(host, port):
    """Utility to verify VPC endpoint connectivity."""
    try:
        socket.create_connection((host, port), timeout=2)
        print(f"✅ Connection to {host} successful")
    except Exception:
        print(f"❌ Connection to {host} failed")


class State(TypedDict):
    """LangGraph state schema."""

    messages: Annotated[list, add_messages]


# Setup the Model (Claude 4.5 Sonnet)
llm = ChatBedrockConverse(
    model_id="eu.anthropic.claude-sonnet-4-5-20250929-v1:0",
    region_name=AWS_REGION,
    temperature=0,
    # FORCE the LLM to use the VPC Endpoint
    endpoint_url=f"https://{BEDROCK_RUNTIME_URL}" if BEDROCK_RUNTIME_URL else None,
)

custom_config = Config(
    connect_timeout=30,
    read_timeout=120,
    retries={"max_attempts": 0},
)

# Internal helper for signed MCP Gateway calls
def signed_gateway_post(payload):
    """Helper to sign and send requests to the VPC endpoint."""
    session_http = requests.Session()
    creds = boto3.Session().get_credentials()
    public_host = GATEWAY_URL.replace("https://", "").split("/")[0]
    vpc_destination = f"https://{GATEWAY_ENDPOINT_URL}/mcp"

    req = AWSRequest(
        method="POST",
        url=GATEWAY_URL,
        data=json.dumps(payload),
        headers={"Content-Type": "application/json", "Host": public_host},
    )
    SigV4Auth(creds, "bedrock-agentcore", AWS_REGION).add_auth(req)
    return session_http.post(
        vpc_destination, data=req.data, headers=dict(req.headers), timeout=30
    )

@tool
def query_department_database(query: str, config: RunnableConfig):
    """Queries the gov department database for contact details via the Gateway MCP endpoint."""

    # STEP 1: MCP Handshake
    signed_gateway_post({
        "jsonrpc": "2.0", "id": "init-1", "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "onward-journey-orchestrator", "version": "1.0.0"}},
    })

    # STEP 2: The Call
    call_payload = {
        "jsonrpc": "2.0",
        "id": f"call-{str(uuid.uuid4())[:8]}",
        "method": "tools/call",
        "params": {
            "name": "sw-rds-search-tool___query_department_database",
            "arguments": {"query": query},
        },
    }

    response = signed_gateway_post(call_payload)
    result_data = response.json()
    content = result_data.get("result", {}).get("content", [])
    return content[0]["text"] if content else "ERROR: No matching records found."

@tool
def genesys_live_chat_tools(method: str, live_chat_identifier: str, reason: str = None, summary: str = None):
    """
    Handles Genesys Cloud interactions (availability and handoff).
    'summary' should be a 2-3 sentence Briefing Note from long-term memory.
    """
    call_payload = {
        "jsonrpc": "2.0",
        "id": f"gen-{str(uuid.uuid4())[:8]}",
        "method": "tools/call",
        "params": {
            "name": f"sw-genesys-tool___{method}",
            "arguments": {
                "live_chat_identifier": live_chat_identifier,
                "reason": reason,
                "summary": summary
            },
        },
    }
    response = signed_gateway_post(call_payload)
    result_data = response.json()
    content = result_data.get("result", {}).get("content", [])
    return content[0]["text"] if content else "ERROR: Genesys service unavailable."

# --- ONWARD JOURNEY ROUTER ---
def onward_journey_router(state: State):
    """
    Traffic controller for the Onward Journey.
    Determines if we should check Genesys after an RDS search.
    """
    last_message = state["messages"][-1]

    # If the database search returned a chat ID, move to Genesys Check.
    # Otherwise, return to the chatbot to present the results (e.g., phone).
    if isinstance(last_message, ToolMessage) and "live_chat_identifier" in last_message.content:
        return "check_availability"

    return "finish_response"

# Bind the tools to the LLM
tools = [query_department_database, genesys_live_chat_tools]
llm_with_tools = llm.bind_tools(tools)

def chatbot(state: State):
    """Primary reasoning node for the agent that uses the bound tools."""
    sys_msg = SystemMessage(content=SYSTEM_PROMPT)
    filtered_messages = [msg for msg in state["messages"] if not isinstance(msg, SystemMessage)]
    call_history = [sys_msg] + filtered_messages
    response = llm_with_tools.invoke(call_history)
    return {"messages": [response]}

# Build the Graph
workflow = StateGraph(State)

# 1. Add Nodes
workflow.add_node("chatbot", chatbot)
workflow.add_node("rds_search", ToolNode([query_department_database]))
workflow.add_node("genesys_check", ToolNode([genesys_live_chat_tools]))

# 2. Define Flow
workflow.add_edge(START, "chatbot")

# 3. The LLM Decision Point
workflow.add_conditional_edges(
    "chatbot",
    tools_condition,
    {
        "tools": "rds_search",  # LLM wants to search RDS
        "__end__": END,
    },
)

# 4. The Onward Journey Switch
# Routes the result of the RDS search to either Genesys or back to the bot
workflow.add_conditional_edges(
    "rds_search",
    onward_journey_router,
    {
        "check_availability": "genesys_check",
        "finish_response": "chatbot"
    }
)

# 5. Return to Chatbot
workflow.add_edge("genesys_check", "chatbot")

# Initialise AgentCore Memory (The "Checkpointer")
checkpointer = AgentCoreMemorySaver(
    memory_id=MEMORY_ID,
    region_name=AWS_REGION,
    endpoint_url=f"https://{ENDPOINT_URL}" if ENDPOINT_URL else None,
)
app = workflow.compile(checkpointer=checkpointer)


def lambda_handler(event, context):
    """
    GOV.UK Onward Journey - Orchestrator Entry Point.
    Handles LangGraph execution with state persistence via Bedrock AgentCore.
    """
    print(f"Received event: {json.dumps(event)}")

    # Parse input from Svelte frontend
    # If called via Function URL/API Gateway, the payload is in 'body' as a string.
    # If called via CLI 'aws lambda invoke', the payload is usually the event itself.
    if isinstance(event.get("body"), str):
        body = json.loads(event["body"])
    else:
        body = event

    user_input = body.get("message")
    if not user_input:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "No 'message' found in request payload"}),
        }

    # For LangGraph state persistence.
    thread_id = body.get("thread_id")

    # For state ownership.
    # Maps to Bedrock AgentCore identity requirements for memory isolation.
    actor_id = body.get("actor_id")

    # Config for LangGraph state (thread) and AgentCore identity (actor).
    config = {"configurable": {"thread_id": thread_id, "actor_id": actor_id}}

    # Network Checks
    check_connection(ENDPOINT_URL, 443)
    check_connection(BEDROCK_RUNTIME_URL, 443)
    check_connection(SECRETS_ENDPOINT_URL, 443)

    def generate_stream():
        """Generator for real-time LangGraph message streaming."""
        print("Connecting to Bedrock AgentCore...")

        for chunk, metadata in app.stream(
            {"messages": [("user", str(user_input))]}, config, stream_mode="messages"
        ):
            if chunk.content:
                print(f"Received chunk from node: {metadata.get('langgraph_node')}")
                # Only yield if the chunk comes from the 'chatbot' node
                if metadata.get("langgraph_node") == "chatbot":
                    # If content is a list (common with Claude 4.5/3.5 content blocks), we need to extract the text.
                    if chunk.content:
                        if isinstance(chunk.content, list):
                            for block in chunk.content:
                                if (
                                    isinstance(block, dict)
                                    and block.get("type") == "text"
                                ):
                                    yield block.get("text", "")
                        else:
                            yield chunk.content

    # --- TEST CODE ---
    # Currently used for Function URL compatibility without streaming enabled.
    # Consumes the generator and returns a standard JSON object for testing.
    print("⚡ Starting stream consumption...")
    full_response = "".join(list(generate_stream()))
    print("Stream finished successfully")

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(
            {"response": full_response, "thread_id": thread_id, "actor_id": actor_id}
        ),
    }

    # --- FURURE CODE: TOKEN STREAMING ---
    # Enable this when using Lambda Web Adapter or InvokeWithResponseStream.
    # return {
    #     "statusCode": 200,
    #     "headers": {
    #         "Content-Type": "text/event-stream",
    #         "Cache-Control": "no-cache",
    #         "Connection": "keep-alive",
    #     },
    #     "body": generate_stream(),
    # }
