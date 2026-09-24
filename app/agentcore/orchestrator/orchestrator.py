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
import asyncio
import logging
from typing import Annotated, TypedDict
import httpx
import boto3
from botocore.exceptions import ClientError

from langchain_aws import ChatBedrockConverse
from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph_checkpoint_aws import AgentCoreMemorySaver

from bedrock_agentcore import BedrockAgentCoreApp

from botocore.config import Config

from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client
from mcp import ClientSession

from prompts import SYSTEM_PROMPT

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


app = BedrockAgentCoreApp()

# Configure logging for AgentCore Runtime
log_level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
log_level = getattr(logging, log_level_name, logging.INFO)

logger = logging.getLogger("bedrock_agentcore.app")
logger.setLevel(log_level)

# Only set logger level *after* instantiating app
for handler in logger.handlers:
    handler.setLevel(log_level)

def check_connection(host, port):
    """Utility to verify VPC endpoint connectivity."""
    try:
        socket.create_connection((host, port), timeout=2)
        logger.info("✅ Connection to %s successful", host)
    except Exception:
        logger.error("❌ Connection to %s failed", host)


class State(TypedDict):
    """LangGraph state schema."""
    messages: Annotated[list, add_messages]


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
                    logger.error("GATEWAY ERROR: %s", response.content)
                    return f"ERROR: Gateway rejected call."

                result_text = response.content[0].text if response.content else "ERROR: crm service unavailable."

                # --- HANDOFF STATUS LOG ---
                # TODO: refine this when headless handoff work complete

                if method == "connect_to_live_chat" and not result_text.startswith("ERROR"):
                    logger.info(
                        "METRIC | LiveHandoffInitiated | Target: %s | Thread: %s | Actor: %s",
                        live_chat_identifier,
                        thread_id,
                        actor_id
                    )

                return result_text
    except Exception as e:
        return f"ERROR: Gateway call failed: {str(e)}"

# --- GRAPH SETUP ---
tools = [query_department_database, query_knowledge_base, crm_live_chat_tools]

async def chatbot(state: State, config: RunnableConfig):
    """Primary reasoning node for the agent that uses the bound tools."""
    llm_with_tools = config["configurable"]["llm_with_tools"]

    messages = state["messages"]
    full_response = None

    # Iterate through the token chunks as they arrive from AWS
    async for chunk in llm_with_tools.astream(messages, config):
        if full_response is None:
            full_response = chunk
        else:
            # Accumulate the chunks into a single message for the graph state
            full_response += chunk

    # Return the fully accumulated message to sync the conversation state
    return {"messages": [full_response]}

# Initialise AgentCore Memory (The "Checkpointer")
checkpointer = AgentCoreMemorySaver(
    memory_id=MEMORY_ID,
    region_name=AWS_REGION,
    endpoint_url=f"https://{AGENT_RUNTIME_URL}" if AGENT_RUNTIME_URL else None,
)

# Build the Graph
workflow = StateGraph(State)

# 1. Add Nodes
workflow.add_node("chatbot", chatbot)
workflow.add_node("execute_tools", ToolNode(tools))

# 2. Define Flow
workflow.add_edge(START, "chatbot")

# 3. The LLM Decision Point
workflow.add_conditional_edges("chatbot", tools_condition, {"tools": "execute_tools", "__end__": END})

# 4. The Return Loop
# Every tool result must return to the chatbot to sync conversation state.
workflow.add_edge("execute_tools", "chatbot")

graph_app = workflow.compile(checkpointer=checkpointer)
# --------------------------

@app.entrypoint
async def orchestrator_entrypoint(event):
    """
    Orchestrator Entry Point: Processes user messages and executes the agent graph.

    This function handles the lifecycle of a single interaction:
    1. Parses the incoming message and session metadata (thread_id, actor_id).
    2. Verifies connectivity to required VPC endpoints (Bedrock, AgentCore, Secrets, Gateway).
    3. Initialises the conversation graph with a specialised system prompt.
    4. Executes the LangGraph workflow natively and streams tokens back to the client.

    Args:
        event (dict): Supports standard JSON payloads or API Gateway/Function URL 'body' strings.
    """
    has_body = bool(event.get("body"))
    event_keys = list(event.keys())
    logger.info("Received event | Has body=%s | Event keys=%s", has_body, event_keys)

    # Parse input from frontend
    if isinstance(event.get("body"), str):
        body = json.loads(event["body"])
    else:
        body = event

    user_input = body.get("message")
    if not user_input:
        yield json.dumps({"error": "No 'message' found in request payload"})
        return

    # For LangGraph state persistence.
    thread_id = body.get("thread_id")
    actor_id = body.get("actor_id")

    # Instantiate LLM once per request to guarantee fresh IAM/STS credentials
    llm = ChatBedrockConverse(
        model_id="eu.anthropic.claude-sonnet-4-5-20250929-v1:0",
        region_name=AWS_REGION,
        temperature=0,
        endpoint_url=f"https://{BEDROCK_RUNTIME_URL}" if BEDROCK_RUNTIME_URL else None,
    )
    llm_with_tools = llm.bind_tools(tools)

    # Config for LangGraph state (thread), AgentCore identity (actor), and LLM injection
    config = {
        "configurable": {
            "thread_id": thread_id,
            "actor_id": actor_id,
            "llm_with_tools": llm_with_tools
        }
    }

    # Extract WebSocket metadata provided by the Router Lambda
    connection_id = body.get("connection_id")
    domain_name = body.get("domain_name")
    stage = body.get("stage")

   # Initialize the API Gateway Management client for WebSocket pushes
    apigw_client = None
    if connection_id and domain_name and stage:
        endpoint_url = f"https://{domain_name}/{stage}"

        # Widen the connection pool to 100 to allow concurrent token threads
        boto_config = Config(
            connect_timeout=2,
            read_timeout=2,
            max_pool_connections=100
        )

        apigw_client = boto3.client(
            'apigatewaymanagementapi',
            endpoint_url=endpoint_url,
            region_name=AWS_REGION,
            config=boto_config
        )

    # Network Checks
    check_connection(AGENT_RUNTIME_URL, 443)
    check_connection(BEDROCK_RUNTIME_URL, 443)
    check_connection(SECRETS_ENDPOINT_URL, 443)

    # Fast fail if the physical gateway VPCE is unreachable
    vpce_host = GATEWAY_ENDPOINT_URL.replace("https://", "").split("/")[0]
    check_connection(vpce_host, 443)

    logger.info("Connecting to Bedrock AgentCore...")

    initial_input = {
        "messages": [
            SystemMessage(content=SYSTEM_PROMPT),
            ("user", str(user_input))
        ]
    }

    logger.info("Executing LangGraph workflow natively...")
    logger.info("⚡ Starting streaming execution...")

    logged_tool_ids = set()
    final_response_text = ""
    client_disconnected = asyncio.Event()

    def _post_to_connection_sync(client, conn_id, data):
        """Synchronous wrapper to execute the boto3 call."""
        try:
            client.post_to_connection(ConnectionId=conn_id, Data=data)
            return True
        except ClientError as e:
            if e.response.get('Error', {}).get('Code') == 'GoneException':
                logger.warning("Client %s disconnected (GoneException).", conn_id)
                return False
            logger.error("Boto3 ClientError pushing to WebSocket: %s", str(e), exc_info=True)
            return True
        except Exception as e:
            logger.error("Error pushing to WebSocket: %s", str(e), exc_info=True)
            return True

    ws_queue = asyncio.Queue()

    async def ws_consumer():
        """Single background worker that processes chunks sequentially (FIFO)."""
        is_dead = False
        while True:
            data = await ws_queue.get()
            if data is None:  # Sentinel value to terminate the loop
                ws_queue.task_done()
                break

            # If connection is dead, rapidly drain the queue without hitting network
            if is_dead:
                ws_queue.task_done()
                continue

            # Post sequentially to guarantee order
            success = await asyncio.to_thread(_post_to_connection_sync, apigw_client, connection_id, data)
            ws_queue.task_done()

            # If GoneException, flag as dead and signal the main Bedrock loop
            if not success:
                is_dead = True
                client_disconnected.set()

    consumer_task = None
    if apigw_client and connection_id:
        consumer_task = asyncio.create_task(ws_consumer())

    # Use astream with stream_mode="messages" to get real-time tokens
    async for chunk, metadata in graph_app.astream(
        initial_input, config, stream_mode="messages"
    ):
        #  Abort Bedrock generation if client dropped
        if client_disconnected.is_set():
            logger.info("Aborting Bedrock stream early due to closed WebSocket.")
            break

        node = metadata.get('langgraph_node', 'unknown')
        msg_id = getattr(chunk, 'id', None)

        has_content = bool(chunk.content)
        has_tool_chunks = hasattr(chunk, 'tool_call_chunks') and len(chunk.tool_call_chunks) > 0
        is_tool_result = isinstance(chunk, ToolMessage)

        # --- LOG TOOL CALLS ---
        # Log tool calls/results - only print once per ID
        if msg_id and msg_id not in logged_tool_ids:
            if has_tool_chunks:
                logger.info("🛠️ TOOL CALL: %s", chunk.tool_call_chunks[0].get('name'))
                logged_tool_ids.add(msg_id)
            elif is_tool_result:
                logger.info("📥 TOOL RESULT: %s...", str(chunk.content)[:200])
                logged_tool_ids.add(msg_id)

        # --- WEBSOCKET TEXT PUSH LOGIC & ACCUMULATION ---
        if node == "chatbot" and has_content:
            tokens_to_send = []
            if isinstance(chunk.content, list):
                for block in chunk.content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        tokens_to_send.append(block.get("text", ""))
            elif isinstance(chunk.content, str):
                tokens_to_send.append(chunk.content)

            for token in tokens_to_send:
                if token:
                    final_response_text += token
                    chunk_frame = json.dumps({"type": "chunk", "text": token})
                    if apigw_client and connection_id:
                        # Instantly buffer into the queue (non-blocking)
                        ws_queue.put_nowait(chunk_frame.encode('utf-8'))
                    else:
                        yield chunk_frame  # Console fallback

    # Safely evaluate response AI text without fully dumping it at INFO level
    if final_response_text:
        clean_log_text = final_response_text.strip().replace('\n', ' ')
        logger.info("🤖 FINAL AI RESPONSE length=%d", len(clean_log_text))
        logger.debug("🤖 FINAL AI RESPONSE text=%s", clean_log_text)

    logger.info("Execution finished successfully")

    # --- Push Completion Frame & Drain Queue ---
    if apigw_client and connection_id:
        done_frame = json.dumps({"type": "done"})
        ws_queue.put_nowait(done_frame.encode('utf-8'))

        # Tell the consumer to shut down
        ws_queue.put_nowait(None)

        # Wait for all chunks in the queue to be successfully sent
        await ws_queue.join()
        # Wait for the worker task to cleanly exit
        await consumer_task
    else:
        yield json.dumps({"type": "done"})
