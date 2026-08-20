"""
GOV.UK Onward Journey - AgentCore Runtime Orchestrator.

This AgentCore Runtime serves as the central reasoning engine for the GOV.UK Contact
Assistant. It implements a StateGraph (via LangGraph) to manage multi-turn
conversations, persists state using Amazon Bedrock AgentCore, and
coordinates tool execution through a VPC-signed MCP Gateway.
"""
import json
import os
import socket
from typing import Annotated, TypedDict
import httpx

from langchain_aws import ChatBedrockConverse
from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph_checkpoint_aws import AgentCoreMemorySaver

from bedrock_agentcore import BedrockAgentCoreApp

from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client
from mcp import ClientSession

ENV_PREFIX = os.environ.get("ENV_PREFIX")
GATEWAY_URL = os.environ.get("GATEWAY_URL")
GATEWAY_ENDPOINT_URL = os.environ.get("GATEWAY_ENDPOINT_URL")
MEMORY_ID = os.environ.get("MEMORY_ID")
AGENT_RUNTIME_URL = os.environ.get("AGENT_RUNTIME_ENDPOINT_URL")
BEDROCK_RUNTIME_URL = os.environ.get("BEDROCK_RUNTIME_ENDPOINT")
SECRETS_ENDPOINT_URL = os.environ.get("SECRETS_ENDPOINT_URL")
AWS_REGION = "eu-west-2"

# Host header alignment for Private VPC Endpoint SigV4 signature validation
CANONICAL_HOST = GATEWAY_URL.replace("https://", "").replace("http://", "").split("/")[0]
VPCE_HOST = GATEWAY_ENDPOINT_URL.replace("https://", "").replace("http://", "").split("/")[0]

SYSTEM_PROMPT = """You are a specialised GOV.UK Contact Assistant.
Your primary duty is to provide contact details or policy guidance for specific government departments while filtering out irrelevant search results.

GLOBAL SEARCH FILTERING RULES (APPLIES TO ALL PHASES):
1. IDENTIFY: Determine exactly which government department the user is asking about (e.g., DWP, HMRC, Home Office).
2. DEPARTMENT DATABASE FILTER: When evaluating results from 'query_department_database', look at the 'service' and 'info' fields. OMIT any result that belongs to a DIFFERENT department than the one requested.
3. KNOWLEDGE BASE VALIDATION: When evaluating results from 'query_knowledge_base', look at the 'title' and 'content' fields to formulate your answer. Trust that because you passed a verified 'kb_identifier', the articles belong to the correct department.

STRICT PROTOCOL EXECUTION ORDER:

PHASE 1: KNOWLEDGE BASE RESOLUTION (FIRST LINE OF RESOLUTION)
1. For ALL incoming user queries, you MUST first execute a Knowledge Base lookup to see if the query can be resolved without human routing.
2. Call 'query_department_database' to find the correct department and retrieve its 'knowledge_base_identifier'.
3. If no 'knowledge_base_identifier' is returned, you MUST proceed directly to phase 2.
4. Immediately use that 'knowledge_base_identifier' as the 'kb_identifier' to call 'query_knowledge_base'.
5. EVALUATE RESOLUTION:
   - Look at the 'title' and 'content' fields returned by 'query_knowledge_base'.
   - IF A DIRECT ANSWER IS FOUND: Provide the answer based ONLY on that content and resolve the query.
   - INTERNAL DATA SECURITY: You MUST NOT include the 'url' in your response to the user. These are internal system links that the user cannot access. Provide only the text answer.
   - CRITICAL GATE: If resolved here, you MUST NOT check agent availability, mention live chat, or route to a human. Stop and resolve.

PHASE 2: HUMAN ROUTING & CONTACT FALLBACK
- ONLY if the query remains completely unresolved or unanswered after the Phase 1 Knowledge Base lookup, you may proceed to human routing or contact provision rules.

ONWARD JOURNEY (LIVE CHAT) & CONTACT RULES:
(Note: Only evaluate these if Phase 1 failed to resolve the query)
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
6. IMPORTANT: when providing contact details to the user, you MUST ALWAYS follow these rules:
    - ALWAYS use the exact, official service name provided in the database.
    - ALWAYS describe the service's scope using ONLY the 'info' field provided in the database - DO NOT DEVIATE FROM, OR EXPAND, THE SERVICE SCOPE IN YOUR DESCRIPTION TO THE USER.
    - Do NOT include irrelevant information that is unconnected to the user's query
    - DO NOT use or invent generic terms like "advice line", "helpline", "helpdesk", or "contact center" when referring to the service in your sentences unless these are part of the actual service name.

EXCEPTION RULES:
1. NO MATCH: If NO results match the requested department, inform the user you couldn't find a direct match but mention the closest government service available based on the database results.

STRICT FORMATTING RULES:
1. NO NARRATION / META-COMMENTARY: Do NOT talk about your tools, your logic, or your inner steps.
2. BAN ON INTERNAL TERMS: NEVER use the phrase "knowledge base", "database", or "tool" in your response to the user. Present the information authoritatively as your own knowledge (e.g., instead of "The knowledge base says passports take 3 weeks", just say "Standard passport applications take up to 3 weeks").
3. SILENT TOOL CALLS: Execute all tools completely silently behind the scenes.
4. FINAL OUTPUT ONLY: Your text response to the user must ONLY contain the final resolved answer or the official routing/contact details. No transitional filler text is allowed.
5. Start your response immediately with the department details or a helpful opening sentence that adheres to ALL the rules above.
6. Use Markdown (## for headers, * for bullets).
"""

app = BedrockAgentCoreApp()

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

llm = ChatBedrockConverse(
    model_id="eu.anthropic.claude-sonnet-4-5-20250929-v1:0",
    region_name=AWS_REGION,
    temperature=0,
    endpoint_url=f"https://{BEDROCK_RUNTIME_URL}" if BEDROCK_RUNTIME_URL else None,
)

# --- CUSTOM VPCE TRANSPORT & FACTORY HOOK ---
class VPCETransport(httpx.AsyncHTTPTransport):
    """
    Custom HTTP transport that routes TCP traffic physically to the VPC Endpoint IP
    while preserving the canonical Host header required by AWS IAM SigV4.
    """
    def __init__(self, vpce_host: str, canonical_host: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.vpce_host = vpce_host
        self.canonical_host = canonical_host

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        # 1. Present canonical Host to satisfy the AWS SigV4 security guard
        request.headers["Host"] = self.canonical_host
        # 2. Rewrite the underlying URL host so DNS resolves to the internal VPCE
        request.url = request.url.copy_with(host=self.vpce_host)
        return await super().handle_async_request(request)

def create_vpce_mcp_client(**kwargs) -> httpx.AsyncClient:
    """Factory function to inject custom transport into the official MCP SDK."""
    transport = VPCETransport(vpce_host=VPCE_HOST, canonical_host=CANONICAL_HOST)
    kwargs["transport"] = transport

    if "timeout" not in kwargs:
        kwargs["timeout"] = httpx.Timeout(300.0)

    return httpx.AsyncClient(**kwargs)
# --------------------------------------------

@tool
async def query_department_database(query: str, config: RunnableConfig):
    """Queries the gov department database for contact details and to retrieve the 'knowledge_base_identifier'.
    This should ALWAYS be the first tool called for any query to begin the Knowledge Base lookup phase.
    CRITICAL: Execute this tool completely silently. Do NOT stream any conversational text before or after calling this.
    """
    tool_name = f"{ENV_PREFIX}-rds-search-tool___query_department_database"

    mcp_client = aws_iam_streamablehttp_client(
        endpoint=GATEWAY_URL.rstrip("/"),
        aws_region=AWS_REGION,
        aws_service="bedrock-agentcore",
        httpx_client_factory=create_vpce_mcp_client
    )

    try:
        async with mcp_client as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()

                response = await session.call_tool(tool_name, arguments={"query": query})

                if response.isError:
                    return f"ERROR: Gateway returned error for {tool_name}"

                return response.content[0].text if response.content else "ERROR: No matching records found."
    except Exception as e:
        return f"ERROR: Gateway call failed: {str(e)}"

@tool
async def query_knowledge_base(query: str, kb_identifier: str, config: RunnableConfig):
    """Queries a specific department's Knowledge Base for policy and help articles.
    CRITICAL: Execute this tool completely silently. Do NOT tell the user you are checking a knowledge base.
    """
    tool_name = f"{ENV_PREFIX}-rds-search-tool___query_knowledge_base"

    mcp_client = aws_iam_streamablehttp_client(
        endpoint=GATEWAY_URL.rstrip("/"),
        aws_region=AWS_REGION,
        aws_service="bedrock-agentcore",
        httpx_client_factory=create_vpce_mcp_client
    )

    try:
        async with mcp_client as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()

                response = await session.call_tool(tool_name, arguments={"query": query, "kb_identifier": kb_identifier})

                if response.isError:
                    return f"ERROR: Gateway returned error for {tool_name}"

                return response.content[0].text if response.content else "ERROR: No knowledge base articles found."
    except Exception as e:
        return f"ERROR: Gateway call failed: {str(e)}"

@tool
async def crm_live_chat_tools(method: str, live_chat_identifier: str, reason: str, summary: str, config: RunnableConfig):
    """
    Handles CRM interactions (availability and handoff).
    CRITICAL: This tool must ONLY be called if the query could not be resolved by the Knowledge Base lookup phase.
    EXCEPTION: If 'query_knowledge_base' returns an error, is down, or indicates no articles are available,
    you may bypass the KB constraint and call this tool immediately to assist the user.
    CRITICAL: Execute this tool completely silently. Do NOT stream text explaining that you are checking for agents.
    'summary' should be a 2-3 sentence Briefing Note from long-term memory.
    'reason' should be a short explanation for the handoff.
    'method' MUST be exactly one of:
    - 'check_chat_availability': Use this first to see if agents are online.
    - 'connect_to_live_chat': Use this ONLY after the user agrees to connect.
    """
    actor_id = config["configurable"].get("actor_id")
    thread_id = config["configurable"].get("thread_id")

    target_map = {
        "check_chat_availability": f"{ENV_PREFIX}-crm-availability",
        "connect_to_live_chat": f"{ENV_PREFIX}-crm-handoff"
    }

    target_name = target_map.get(method)

    if not target_name:
        return f"ERROR: Unknown crm method: {method}"

    tool_name = f"{target_name}___{method}"

    mcp_client = aws_iam_streamablehttp_client(
        endpoint=GATEWAY_URL.rstrip("/"),
        aws_region=AWS_REGION,
        aws_service="bedrock-agentcore",
        httpx_client_factory=create_vpce_mcp_client
    )

    try:
        async with mcp_client as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()

                response = await session.call_tool(
                    tool_name,
                    arguments={
                        "method": method,
                        "live_chat_identifier": live_chat_identifier,
                        "reason": reason,
                        "summary": summary,
                        "actor_id": actor_id,
                        "thread_id": thread_id
                    }
                )

                if response.isError:
                    print(f"GATEWAY ERROR: {response.content}")
                    return f"ERROR: Gateway rejected call."

                result_text = response.content[0].text if response.content else "ERROR: crm service unavailable."

                # --- HANDOFF STATUS LOG ---
                if method == "connect_to_live_chat" and "SIGNAL" in result_text:
                    print(f"METRIC | LiveHandoffInitiated | Target: {live_chat_identifier} | Thread: {thread_id} | Actor: {actor_id}")

                return result_text
    except Exception as e:
        return f"ERROR: Gateway call failed: {str(e)}"

# Bind the tools to the LLM
# Tools are kept separate to allow the AI agent to choose the specific action.
tools = [query_department_database, query_knowledge_base, crm_live_chat_tools]
llm_with_tools = llm.bind_tools(tools)

async def chatbot(state: State, config: RunnableConfig):
    """Primary reasoning node for the agent that uses the bound tools."""
    messages = state["messages"]

    # Call the model asynchronously with the full, unfiltered state history.
    response = await llm_with_tools.ainvoke(messages, config)

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

graph_app = workflow.compile(checkpointer=checkpointer)

@app.entrypoint
async def orchestrator_entrypoint(event):
    """
    Orchestrator Entry Point: Processes user messages and executes the agent graph.

    This function handles the lifecycle of a single interaction:
    1. Parses the incoming message and session metadata (thread_id, actor_id).
    2. Verifies connectivity to required VPC endpoints (Bedrock, AgentCore, Secrets, Gateway).
    3. Initialises the conversation graph with a specialised system prompt.
    4. Executes the LangGraph workflow natively to resolve the user's query.

    Args:
        event (dict): Supports standard JSON payloads or API Gateway/Function URL 'body' strings.
    """
    print(f"Received event: {json.dumps(event)}")

    # Parse input from frontend
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

    # Fast fail if the physical gateway VPCE is unreachable
    vpce_host = GATEWAY_ENDPOINT_URL.replace("https://", "").split("/")[0]
    check_connection(vpce_host, 443)

    print("Connecting to Bedrock AgentCore...")

    initial_input = {
        "messages": [
            SystemMessage(content=SYSTEM_PROMPT),
            ("user", str(user_input))
        ]
    }

    # Execute the graph asynchronously via LangGraph
    print("Executing LangGraph workflow natively...")
    print("⚡ Starting execution...")
    result_state = await graph_app.ainvoke(initial_input, config)
    print("Execution finished successfully")

    # --- GRAPH STEP LOGS ---
    for msg in result_state.get("messages", []):
        msg_type = type(msg).__name__
        msg_id = getattr(msg, 'id', 'unknown')

        if hasattr(msg, 'tool_calls') and msg.tool_calls:
            for t_call in msg.tool_calls:
                print(f"--- GRAPH STEP ---", flush=True)
                print(f"TYPE: {msg_type} | ID: {msg_id} | Content: 🛠️ TOOL CALL: {t_call.get('name')}...", flush=True)
        elif isinstance(msg, ToolMessage):
            print(f"--- GRAPH STEP ---", flush=True)
            print(f"TYPE: {msg_type} | ID: {msg_id} | Content: 📥 TOOL RESULT: {str(msg.content)[:100]}...", flush=True)
        else:
            text = msg.content[0].get('text', '') if isinstance(msg.content, list) else msg.content
            if text:
                print(f"--- GRAPH STEP ---", flush=True)
                print(f"TYPE: {msg_type} | ID: {msg_id} | Content: {str(text)[:100].replace(chr(10), ' ')}...", flush=True)
    # --------------------------------

    # Extract the final response from the model
    final_message = result_state["messages"][-1].content

    # Safety wrapper: Bedrock Converse can return a list of dicts (content blocks)
    # or a raw string. We extract the text appropriately.
    if isinstance(final_message, list):
        text_response = "".join([
            block.get("text", "")
            for block in final_message
            if isinstance(block, dict) and block.get("type") == "text"
        ])
    else:
        text_response = str(final_message)

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({
            "response": text_response,
            "thread_id": thread_id,
            "actor_id": actor_id
        }),
    }

app.run()
