"""
MCP Tools and VPC Transport logic for the AgentCore Orchestrator.
"""
import os
import httpx
import logging
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig

from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client
from mcp import ClientSession

# Initialize logger (inherits configuration from orchestrator.py)
logger = logging.getLogger("bedrock_agentcore.app")

ENV_PREFIX = os.environ.get("ENV_PREFIX")
GATEWAY_URL = os.environ.get("GATEWAY_URL")
GATEWAY_ENDPOINT_URL = os.environ.get("GATEWAY_ENDPOINT_URL")
AWS_REGION = "eu-west-2"

# Host header alignment for Private VPC Endpoint SigV4 signature validation
CANONICAL_HOST = GATEWAY_URL.replace("https://", "").replace("http://", "").split("/")[0]
VPCE_HOST = GATEWAY_ENDPOINT_URL.replace("https://", "").replace("http://", "").split("/")[0]

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

# Export tools for graph binding
AGENT_TOOLS = [query_department_database, query_knowledge_base, crm_live_chat_tools]
