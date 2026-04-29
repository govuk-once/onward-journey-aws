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

from utils.aws import get_bedrock_client

ENV_PREFIX = os.environ.get("ENV_PREFIX")
GATEWAY_URL = os.environ.get("GATEWAY_URL")
GATEWAY_ENDPOINT_URL = os.environ.get("GATEWAY_ENDPOINT_URL")
MEMORY_ID = os.environ.get("MEMORY_ID")
AGENT_RUNTIME_URL = os.environ.get("AGENT_RUNTIME_ENDPOINT_URL")
BEDROCK_RUNTIME_URL = os.environ.get("BEDROCK_RUNTIME_ENDPOINT")
SECRETS_ENDPOINT_URL = os.environ.get("SECRETS_ENDPOINT_URL")
AWS_REGION = "eu-west-2"

SYSTEM_PROMPT = """You are a specialised GOV.UK Contact Assistant.
Your primary duty is to provide contact details for specific government departments while filtering out irrelevant search results.

STRICT FILTERING RULES:
1. IDENTIFY: Determine exactly which government department the user is asking about (e.g., DWP, HMRC, Home Office).
2. FILTER: When you receive tool results, look at the 'service' and 'info' fields.
3. OMIT: If a result belongs to a DIFFERENT department than the one requested, you MUST NOT list its details.

KNOWLEDGE RETRIEVAL RULES:
1. MULTI-STEP SEARCH: If the user asks a "how-to" or policy question (e.g., "How do I renew my passport?"), you must:
   a. Call 'query_department_database' first to find the correct department and its 'knowledge_base_identifier'.
   b. Use that 'knowledge_base_identifier' as the 'kb_identifier' to call 'query_knowledge_base'.
2. ANSWER FROM KB: Provide answers based ONLY on the content returned from the Knowledge Base.
3. CITATION: If you use information from the KB, you MUST include the 'url' provided in the tool result as a reference.

ONWARD JOURNEY (LIVE CHAT) & CONTACT RULES:
1. MANDATORY CHECK: If a valid 'live_chat_identifier' is provided by the database, you MUST check if agents are available before responding by calling 'crm_live_chat_tools' with method='check_chat_availability'.
2. INTERPRET RESULTS & OFFER:
   - If the tool result contains "ONLINE": You MUST explicitly tell the user: "We have agents available right now. Would you like me to connect you to a live person?" If a wait time is available, tell the user what the estimated wait time is.
   - OFFER, DON'T FORCE: Inform the user and ASK if they would like to connect.
   - STOP AND WAIT: Do not call 'connect_to_live_chat' until the user explicitly says "Yes", "Please connect me", or similar.
3. PHONE FALLBACK: If 'live_chat_identifier' is missing, null, empty, or if agents are currently OFFLINE or an error occurs, you MUST provide the 'phone_number' as the primary contact method instead.
4. HANDOVER SUMMARY (BRIEFING NOTE): If the user agrees to connect, you must call method='connect_to_live_chat' and generate a 2-3 sentence 'summary'.
   - DESTINATION: A professional 'Briefing Note' for the human adviser via the 'connect_to_live_chat' tool.
   - SOURCE: Focus primarily on the current session's "Incomplete Task." Use Long-Term Memory (AgentCore) ONLY to identify if this is a repeat attempt or if there is a persistent blocker (e.g., "User has been unable to bypass the 'Submit' error for three sessions").
   - CONTENT: Identify the specific Government Service (e.g., Border Force, HMRC Tax), the specific goal (e.g., reporting a crime, checking a claim), and the immediate blocker that triggered this handoff.
   - EXCLUSION: Omit any historical context that is not directly relevant to the current service request.
   - ANCHORING THE SIGNAL: Once the tool returns a 'SIGNAL' string, you MUST confirm the connection to the user (e.g., "I'm connecting you now...") and then append the exact 'SIGNAL' string to the very end of your response.
     The signal is a 'Switchboard Trigger' for the frontend system; you must not modify it or add any text after it.
5. DO NOT source information outside of the tools available to you.

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
    # Construct the tool name dynamically using the Environment Prefix
    tool_name = f"{ENV_PREFIX}-rds-search-tool___query_department_database"

    call_payload = {
        "jsonrpc": "2.0",
        "id": f"call-{str(uuid.uuid4())[:8]}",
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": {"query": query},
        },
    }

    response = signed_gateway_post(call_payload)
    result_data = response.json()
    content = result_data.get("result", {}).get("content", [])
    return content[0]["text"] if content else "ERROR: No matching records found."

@tool
def query_knowledge_base(query: str, kb_identifier: str, config: RunnableConfig):
    """Queries a specific department's Knowledge Base for policy and help articles."""

    # STEP 1: The Call
    # Construct the tool name dynamically using the Environment Prefix
    tool_name = f"{ENV_PREFIX}-rds-search-tool___query_knowledge_base"

    call_payload = {
        "jsonrpc": "2.0",
        "id": f"kb-{str(uuid.uuid4())[:8]}",
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": {"query": query, "kb_identifier": kb_identifier},
        },
    }

    response = signed_gateway_post(call_payload)
    result_data = response.json()
    content = result_data.get("result", {}).get("content", [])
    return content[0]["text"] if content else "ERROR: No knowledge base articles found."

@tool
def crm_live_chat_tools(method: str, live_chat_identifier: str, reason: str, summary: str, config: RunnableConfig):
    """
    Handles CRM interactions (availability and handoff).
    'summary' should be a 2-3 sentence Briefing Note from long-term memory.
    'reason' should be a short explanation for the handoff.
    'method' MUST be exactly one of:
    - 'check_chat_availability': Use this first to see if agents are online.
    - 'connect_to_live_chat': Use this ONLY after the user agrees to connect.
    """


    actor_id = config["configurable"].get("actor_id")
    thread_id = config["configurable"].get("thread_id")

    # Map the method to the specific Gateway Target name defined in Terraform
    target_map = {
        "check_chat_availability": f"{ENV_PREFIX}-crm-availability",
        "connect_to_live_chat": f"{ENV_PREFIX}-crm-handoff"
    }

    target_name = target_map.get(method)

    if not target_name:
        return f"ERROR: Unknown crm method: {method}"

    call_payload = {
        "jsonrpc": "2.0",
        "id": f"gen-{str(uuid.uuid4())[:8]}",
        "method": "tools/call",
        "params": {
            "name": f"{target_name}___{method}",
            "arguments": {
                "method": method,
                "live_chat_identifier": live_chat_identifier,
                "reason": reason,
                "summary": summary,
                "actor_id": actor_id,
                "thread_id": thread_id
            },
        },
    }
    response = signed_gateway_post(call_payload)
    result_data = response.json()

    # Debug: Check for Gateway-level errors (Method not found, etc.)
    if "error" in result_data:
        print(f"GATEWAY ERROR: {json.dumps(result_data['error'])}")
        return f"ERROR: Gateway rejected call: {result_data['error'].get('message')}"

    content = result_data.get("result", {}).get("content", [])
    result_text = content[0]["text"] if content else "ERROR: crm service unavailable."

    # --- HANDOFF STATUS LOG ---
    if method == "connect_to_live_chat" and "SIGNAL" in result_text:
        print(f"METRIC | LiveHandoffInitiated | Target: {live_chat_identifier} | ID: {call_payload['id']}")

    return content[0]["text"] if content else "ERROR: crm service unavailable."


# Bind the tools to the LLM
# Tools are kept separate to allow the AI agent to choose the specific action.
tools = [query_department_database, query_knowledge_base, crm_live_chat_tools]
llm_with_tools = llm.bind_tools(tools)

def chatbot(state: State, config: RunnableConfig):
    """Primary reasoning node for the agent that uses the bound tools."""
    messages = state["messages"]


    # Call the model with the full, unfiltered state history.
    response = llm_with_tools.invoke(messages, config)

    return {"messages": [response]}

# Build the Graph
workflow = StateGraph(State)

# 1. Add Nodes
workflow.add_node("chatbot", chatbot)
workflow.add_node("execute_tools", ToolNode(tools))

# 2. Define Flow
workflow.add_edge(START, "chatbot")

# 3. The LLM Decision Point
workflow.add_conditional_edges(
    "chatbot",
    tools_condition,
    {
        "tools": "execute_tools",
        "__end__": END,
    },
)

# 4. The Return Loop
# Every tool result must return to the chatbot to sync conversation state.
workflow.add_edge("execute_tools", "chatbot")

# Initialise AgentCore Memory (The "Checkpointer")
checkpointer = AgentCoreMemorySaver(
    memory_id=MEMORY_ID,
    region_name=AWS_REGION,
    endpoint_url=f"https://{AGENT_RUNTIME_URL}" if AGENT_RUNTIME_URL else None,
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
    check_connection(AGENT_RUNTIME_URL, 443)
    check_connection(BEDROCK_RUNTIME_URL, 443)
    check_connection(SECRETS_ENDPOINT_URL, 443)

    def generate_stream():
        """Generator for real-time LangGraph message streaming."""
        print("Connecting to Bedrock AgentCore...")

        initial_input = {
            "messages": [
                SystemMessage(content=SYSTEM_PROMPT),
                ("user", str(user_input))
            ]
        }

        # Tracker to ensure we only print each fully-formed message once
        logged_message_ids = set()

        for chunk, metadata in app.stream(
            initial_input, config, stream_mode="messages"
        ):
            msg_id = getattr(chunk, 'id', None)
            node = metadata.get('langgraph_node', 'unknown')

            # --- DEBUG LOGGING LOGIC ---
            # Check if this chunk contains the actual payload we want to log
            has_content = bool(chunk.content)
            # IMPORTANT: For tool calls in streams, 'tool_call_chunks' is often used instead of 'tool_calls'
            has_tool_chunks = hasattr(chunk, 'tool_call_chunks') and len(chunk.tool_call_chunks) > 0
            is_tool_result = isinstance(chunk, ToolMessage)

            # Only log if we have actual data to show (content, tool args, or result)
            if msg_id and msg_id not in logged_message_ids and (has_content or has_tool_chunks or is_tool_result):
                msg_type = type(chunk).__name__

                if has_tool_chunks:
                    # Extract info. from the chunk
                    t_chunk = chunk.tool_call_chunks[0]
                    display_content = f"🛠️ TOOL CALL: {t_chunk.get('name')}"
                elif is_tool_result:
                    display_content = f"📥 TOOL RESULT: {str(chunk.content)[:100]}"
                else:
                    # Capture the start of the final response
                    text = chunk.content[0].get('text', '') if isinstance(chunk.content, list) else chunk.content
                    display_content = str(text)[:100].replace('\n', ' ')

                # Only mark as 'logged' if we actually found data, otherwise wait for next chunk of same ID
                if display_content:
                    print(f"--- GRAPH STEP | Node: {node} ---", flush=True)
                    print(f"TYPE: {msg_type} | ID: {msg_id} | Content: {display_content}...", flush=True)
                    logged_message_ids.add(msg_id)

            # --- YIELDING LOGIC (For the actual response stream) ---
            if node == "chatbot" and has_content:
                if isinstance(chunk.content, list):
                    for block in chunk.content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            yield block.get("text", "")
                # SAFETY: If the LLM returns a raw string instead of a block list
                elif isinstance(chunk.content, str):
                    yield chunk.content

    # --- MVP RESPONSE (NON-STREAMING) ---
    # Currently used for Function URL compatibility without streaming enabled.
    # Consumes the generator and returns a standard JSON object for testing.
    # NOTE: Disable this block when enabling the TODO Streaming mode below.
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

    #  --- TODO: TOKEN STREAMING IMPLEMENTATION ---
    # The infrastructure is 'Streaming Ready' (see Terraform: invoke_mode = RESPONSE_STREAM).
    # Next Step: Enable real-time response streaming for the Svelte frontend to reduce latency and improve UI responsiveness.
