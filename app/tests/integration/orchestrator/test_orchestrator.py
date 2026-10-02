import pytest
import asyncio
import json
import os
from unittest.mock import patch, AsyncMock
import logging

from tests.integration.orchestrator import mock_data
from deepeval import assert_test
from deepeval.metrics import GEval
from deepeval.test_case import LLMTestCase, SingleTurnParams
from deepeval.models import AmazonBedrockModel

# =====================================================================
# 1. ENVIRONMENT SETUP
# =====================================================================
os.environ["ENV_PREFIX"] = "localtest"
os.environ["GATEWAY_URL"] = "https://mock.gateway.com"
os.environ["GATEWAY_ENDPOINT_URL"] = "https://vpce-mock.aws.com"
os.environ["MEMORY_ID"] = "mock-memory-id"
os.environ["LOG_LEVEL"] = "INFO"

logger = logging.getLogger(__name__)

# Safely import the orchestrator and components after patching the environment
from app.agentcore.orchestrator.orchestrator import orchestrator_entrypoint, workflow
import app.agentcore.orchestrator.orchestrator as orchestrator_module
from langgraph.checkpoint.memory import MemorySaver

# =====================================================================
# 2. FIXTURES & MOCKS
# =====================================================================

@pytest.fixture(autouse=True)
def mock_network_and_memory():
    """
    Intercept network calls and replace Bedrock Agent memory with a local
    LangGraph MemorySaver for blazingly fast, isolated multi-turn tests.
    """
    with patch("socket.create_connection"):
        # Dynamically recompile the graph with in-memory persistence
        orchestrator_module.graph_app = workflow.compile(checkpointer=MemorySaver())
        yield

@pytest.fixture
def llm_judges():
    """Setup DeepEval metrics using AWS Bedrock."""
    bedrock_judge = AmazonBedrockModel(
        model="eu.anthropic.claude-sonnet-4-6",
        region="eu-west-2",
        generation_kwargs={"temperature": 0.0},
    )

    agent_routing_metric = GEval(
        name="Orchestrator Routing & Formatting",
        model=bedrock_judge,
        threshold=0.8,
        evaluation_params=[
            SingleTurnParams.INPUT,
            SingleTurnParams.ACTUAL_OUTPUT,
            SingleTurnParams.CONTEXT,
        ],
        criteria="""
        Evaluate if the agent correctly handled the user's query based on the context provided.

        RULES:
        1. KNOWLEDGE BASE RESOLUTION: If the context contains a direct answer to the query, the agent MUST provide it clearly. The agent MUST also provide the department's phone number alongside the answer if one is available in the context. It MUST NOT offer a live chat connection if the query is already answered.
        2. ACCURACY CONSTRAINT: The phone number and official service name provided in the output MUST exactly match the data provided in the context.
        3. ESCALATION: If the context does NOT contain the answer, the agent MUST offer live chat if available, or provide the phone number if offline.
        4. HANDOFF EXCEPTION: If the user is successfully being handed off to a live agent, the AI does NOT need to provide the phone number.

        SCORING DIRECTIVE: You MUST award a perfect 1.0 score if the agent answers the question correctly, includes the required phone number from the context (unless handing off to live chat), and ensures all details are 100% accurate. Penalize the score if a required phone number is missing, or if it is hallucinated/incorrect.
        """
    )
    return [agent_routing_metric]

@pytest.fixture
def mock_mcp_session():
    """
    Mocks the MCP transport and ClientSession in the new tools.py architecture.
    """
    # 1. Neutralize the physical HTTP transport wrapper
    with patch("tools.aws_iam_streamablehttp_client") as mock_client:
        # Use AsyncMocks to prevent the SDK from crashing on iteration
        mock_client.return_value.__aenter__.return_value = (AsyncMock(), AsyncMock(), AsyncMock())

        # 2. Mock the MCP session logic itself
        with patch("tools.ClientSession") as mock_session_cls:
            session_instance = AsyncMock()
            mock_session_cls.return_value.__aenter__.return_value = session_instance
            yield session_instance

def create_mock_mcp_tool_call(db_response, kb_response, crm_availability=None, crm_handoff=None):
    """Factory to intercept LangChain tool calls and return mock MCP responses."""
    async def mock_call_tool(name, arguments, **kwargs):
        class MockContent:
            def __init__(self, text):
                self.text = text
        class MockResponse:
            def __init__(self, text, is_error=False):
                self.content = [MockContent(text)] if text else []
                self.isError = is_error

        if "query_department_database" in name:
            return MockResponse(db_response)
        elif "query_knowledge_base" in name:
            return MockResponse(kb_response)
        elif "check_chat_availability" in name and crm_availability:
            return MockResponse(crm_availability)
        elif "connect_to_live_chat" in name and crm_handoff:
            return MockResponse(crm_handoff)

        return MockResponse(f"ERROR: Mock not configured for tool: {name}", is_error=True)
    return mock_call_tool

def invoke_agent_sync(event):
    """
    Helper to consume the async token stream synchronously.
    Isolates LangGraph's strict timeout rules from DeepEval's background logic.
    """
    async def _run_stream():
        response_text = ""
        # Handle cases where orchestrator_entrypoint is decorated or direct
        entry_func = getattr(orchestrator_entrypoint, "__wrapped__", orchestrator_entrypoint)

        async for frame in entry_func(event):
            data = json.loads(frame)
            if data.get("type") == "chunk":
                response_text += data.get("text", "")
        return response_text

    loop = asyncio.new_event_loop()
    try:
        task = loop.create_task(_run_stream())
        return loop.run_until_complete(task)
    finally:
        loop.close()

# =====================================================================
# 3. TEST CASES
# =====================================================================

def test_knowledge_base_resolution(mock_mcp_session, llm_judges):
    """
    Agent finds an answer in the Knowledge Base and resolves
    the query without attempting to hand off to a human.
    """
    logger.info("TEST: Agent resolves query with Knowledge Base, no human connection")

    mock_db_result = json.dumps([mock_data.test_contact_passport_tracking])
    mock_kb_result = json.dumps(mock_data.test_kb_passport)

    mock_mcp_session.call_tool.side_effect = create_mock_mcp_tool_call(
        db_response=mock_db_result, kb_response=mock_kb_result
    )

    test_query = "How long will my new passport take to arrive?"
    event = {
        "body": json.dumps({"message": test_query, "thread_id": "t-001", "actor_id": "u-123"})
    }

    actual_response = invoke_agent_sync(event)

    # --- Assert VISIBILITY (Tool Trajectory) ---
    assert mock_mcp_session.call_tool.call_count == 2, f"Expected 2 tool calls, got {mock_mcp_session.call_tool.call_count}"
    calls = mock_mcp_session.call_tool.call_args_list
    assert "query_department_database" in calls[0][0][0], "Agent failed to query DB first"
    assert "query_knowledge_base" in calls[1][0][0], "Agent failed to query KB second"

    # --- Assert RULE ADHERENCE ---
    assert "kb-passports" not in actual_response, "Agent leaked internal ID"
    assert "knowledge base" not in actual_response.lower(), "Agent used banned internal terminology"

    # --- Assert OUTPUT QUALITY ---
    test_case = LLMTestCase(
        input=test_query,
        actual_output=actual_response,
        context=[mock_db_result, mock_kb_result],
    )
    assert_test(test_case, llm_judges)


def test_kb_failure_routes_to_crm(mock_mcp_session, llm_judges):
    """
    Turn 1: KB lookup fails -> checks CRM -> prompts user.
    Turn 2: User confirms -> Agent emits routing signal.
    """
    logger.info("TEST: Agent resolves query with no Knowledge Base article found, initiates human connection")

    mock_db_result = json.dumps([mock_data.test_contact_hmrc])
    mock_kb_result = "ERROR: No knowledge base articles found."
    mock_crm_availability_result = "Live chat is AVAILABLE. Estimated wait: under 1 minute."
    #TODO: change/remove mock_crm_handoff_result to reflect new handoff
    mock_crm_handoff_result = "SIGNAL: initiate_live_handoff {'connection_details' : 'test'}"

    mock_mcp_session.call_tool.side_effect = create_mock_mcp_tool_call(
        db_response=mock_db_result,
        kb_response=mock_kb_result,
        crm_availability=mock_crm_availability_result,
        crm_handoff=mock_crm_handoff_result,
    )

    # ==========================================
    # TURN 1: Initial Query & Fallback
    # ==========================================
    test_query_1 = "I have a highly specific tax question."
    event_1 = {"body": json.dumps({"message": test_query_1, "thread_id": "t-002", "actor_id": "u-456"})}

    actual_response_1 = invoke_agent_sync(event_1)

    # 1.1 Assert VISIBILITY
    assert mock_mcp_session.call_tool.call_count == 3
    calls_turn_1 = mock_mcp_session.call_tool.call_args_list
    assert "query_department_database" in calls_turn_1[0][0][0]
    assert "query_knowledge_base" in calls_turn_1[1][0][0]
    assert "check_chat_availability" in calls_turn_1[2][0][0]

    # 1.2 Assert ROUTING PROTOCOL
    response_lower_1 = actual_response_1.lower()
    assert "under 1 minute" in response_lower_1, "Agent failed to relay wait time"

    # 1.3 Assert QUALITY
    test_case_1 = LLMTestCase(
        input=test_query_1,
        actual_output=actual_response_1,
        context=[mock_kb_result, mock_db_result, mock_crm_availability_result],
    )
    assert_test(test_case_1, llm_judges)

    # ==========================================
    # TURN 2: User Confirms Handoff
    # ==========================================
    test_query_2 = "Yes, please connect me."
    event_2 = {"body": json.dumps({"message": test_query_2, "thread_id": "t-002", "actor_id": "u-456"})}

    actual_response_2 = invoke_agent_sync(event_2)

    # 2.1 Assert VISIBILITY
    assert mock_mcp_session.call_tool.call_count == 4
    assert "connect_to_live_chat" in mock_mcp_session.call_tool.call_args_list[3][0][0]

    # 2.2 TODO: change/remove mock_crm_handoff_result to reflect new handoff
    assert "SIGNAL: initiate_live_handoff" not in actual_response_2

    # 2.3 Assert QUALITY
    test_case_2 = LLMTestCase(
        input=test_query_2,
        actual_output=actual_response_2,
        context=[actual_response_1],
    )
    assert_test(test_case_2, llm_judges)


def test_kb_not_relevant_routes_to_crm_live_chat(mock_mcp_session, llm_judges):
    """
    Turn 1: KB returns results but not relevant to query -> checks CRM -> prompts user.
    Turn 2: User confirms -> Agent connects to chat.
    """
    logger.info("TEST: Agent handles non-relevant KB article, initiates human connection")

    mock_db_result = json.dumps([mock_data.test_contact_passport_tracking])
    mock_kb_result = json.dumps(mock_data.test_kb_passport)
    mock_crm_availability_result = "Live chat is AVAILABLE. Estimated wait: 5 minutes."
    #TODO: change/remove mock_crm_handoff_result to reflect new handoff
    mock_crm_handoff_result = "SIGNAL: initiate_live_handoff {'connection_details' : 'test'}"

    mock_mcp_session.call_tool.side_effect = create_mock_mcp_tool_call(
        db_response=mock_db_result,
        kb_response=mock_kb_result,
        crm_availability=mock_crm_availability_result,
        crm_handoff=mock_crm_handoff_result,
    )

    # ==========================================
    # TURN 1: Initial Query & Fallback
    # ==========================================
    test_query_1 = "I have a question about tracking my passport renewal application that isn't on the website."
    event_1 = {"body": json.dumps({"message": test_query_1, "thread_id": "t-003", "actor_id": "u-789"})}

    actual_response_1 = invoke_agent_sync(event_1)

    # 1.1 Assert VISIBILITY
    assert mock_mcp_session.call_tool.call_count == 3
    calls_turn_1 = mock_mcp_session.call_tool.call_args_list
    assert "query_department_database" in calls_turn_1[0][0][0]
    assert "query_knowledge_base" in calls_turn_1[1][0][0]
    assert "check_chat_availability" in calls_turn_1[2][0][0]

    # 1.2 Assert ROUTING PROTOCOL
    response_lower_1 = actual_response_1.lower()
    assert "5 minutes" in response_lower_1

    # 1.3 Assert QUALITY
    test_case_1 = LLMTestCase(
        input=test_query_1,
        actual_output=actual_response_1,
        context=[mock_kb_result, mock_db_result, mock_crm_availability_result],
    )
    assert_test(test_case_1, llm_judges)

    # ==========================================
    # TURN 2: User Confirms Handoff
    # ==========================================
    test_query_2 = "Yes, please connect me."
    event_2 = {"body": json.dumps({"message": test_query_2, "thread_id": "t-003", "actor_id": "u-789"})}

    actual_response_2 = invoke_agent_sync(event_2)

    # 2.1 Assert VISIBILITY
    assert mock_mcp_session.call_tool.call_count == 4
    assert "connect_to_live_chat" in mock_mcp_session.call_tool.call_args_list[3][0][0]

    # 2.2 Assert QUALITY
    test_case_2 = LLMTestCase(
        input=test_query_2,
        actual_output=actual_response_2,
        context=[actual_response_1],
    )
    assert_test(test_case_2, llm_judges)


def test_kb_not_relevant_routes_to_crm_no_live_chat(mock_mcp_session, llm_judges):
    """
    KB returns results but not relevant to query -> checks CRM -> no agents available -> provides contact details.
    """
    logger.info("TEST: Agent handles irrelevant KB return and no live agents available - returns contact details")

    mock_db_result = json.dumps([mock_data.test_contact_passport_tracking])
    mock_kb_result = json.dumps(mock_data.test_kb_passport)
    mock_crm_availability_result = "Live chat is currently unavailable."

    mock_mcp_session.call_tool.side_effect = create_mock_mcp_tool_call(
        db_response=mock_db_result,
        kb_response=mock_kb_result,
        crm_availability=mock_crm_availability_result,
    )

    test_query = "I have a highly specific question about tracking my passport renewal application that isn't on the website."
    event = {"body": json.dumps({"message": test_query, "thread_id": "t-004", "actor_id": "u-999"})}

    actual_response = invoke_agent_sync(event)

    # --- Assert VISIBILITY ---
    assert mock_mcp_session.call_tool.call_count == 3
    calls = mock_mcp_session.call_tool.call_args_list
    assert "query_department_database" in calls[0][0][0]
    assert "query_knowledge_base" in calls[1][0][0]
    assert "check_chat_availability" in calls[2][0][0]

    # --- Assert QUALITY ---
    test_case = LLMTestCase(
        input=test_query,
        actual_output=actual_response,
        context=[mock_kb_result, mock_db_result, mock_crm_availability_result],
    )
    assert_test(test_case, llm_judges)
