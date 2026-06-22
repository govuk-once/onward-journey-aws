import pytest
import json
import os
from unittest.mock import patch
import botocore.client
from tests.orchestrator import mock_data

# =====================================================================
# 1. ENVIRONMENT & MOCK SETUP (Must run before handler import)
# =====================================================================

os.environ["ENV_PREFIX"] = "localtest"
os.environ["GATEWAY_URL"] = "https://mock.gateway.com"
os.environ["GATEWAY_ENDPOINT_URL"] = "vpce-mock.aws.com"
os.environ["MEMORY_ID"] = "mock-memory-id"

patch("socket.create_connection").start()

@pytest.fixture(autouse=True)
def mock_bedrock_memory():
    """
    Intercept Bedrock Agent memory calls to keep tests isolated.
    Uses a closure to maintain state locally per-test, avoiding global variables.
    """
    local_memory_store = []

    orig_api_call = botocore.client.BaseClient._make_api_call

    def mock_boto3_api_call(self, operation_name, kwargs):
        if operation_name == 'ListEvents':
            return {"events": local_memory_store}

        if operation_name in ['PutEvents', 'CreateEvent']:
            local_memory_store.append(kwargs)
            return {}

        return orig_api_call(self, operation_name, kwargs)

    with patch("botocore.client.BaseClient._make_api_call", new=mock_boto3_api_call):
        yield local_memory_store

# =====================================================================
# 2. IMPORTS
# =====================================================================

# Safely import the handler after patching the environment
from lambdas.orchestrator.handler import lambda_handler

from deepeval import assert_test
from deepeval.test_case import LLMTestCase
from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric
from deepeval.models import AmazonBedrockModel

# =====================================================================
# 3. DEEPEVAL JUDGE CONFIGURATION
# =====================================================================

@pytest.fixture
def llm_judges():
    """Setup DeepEval metrics using AWS Bedrock via local SSO credentials."""

    bedrock_judge = AmazonBedrockModel(
        model="eu.anthropic.claude-sonnet-4-6",
        region="eu-west-2",
        generation_kwargs={"temperature": 0.0}
    )

    return [
        AnswerRelevancyMetric(threshold=0.7, model=bedrock_judge),
        FaithfulnessMetric(threshold=0.8, model=bedrock_judge)
    ]

# =====================================================================
# 4. MOCK GATEWAY FACTORY
# =====================================================================

def create_mock_gateway(db_response, kb_response, crm_availability_response=None, crm_handoff_response=None):
    """
    Factory to intercept the LangChain tool calls destined for the VPC gateway.
    """
    def mock_post(payload):
        class MockResponse:
            def json(self):
                if payload.get("method") == "initialize":
                    return {"result": {"capabilities": {}}}

                tool_name = payload["params"]["name"]

                if "query_department_database" in tool_name:
                    content_text = db_response
                elif "query_knowledge_base" in tool_name:
                    content_text = kb_response
                elif "check_chat_availability" in tool_name and crm_availability_response:
                    content_text = crm_availability_response
                elif "connect_to_live_chat" in tool_name and crm_handoff_response:
                    content_text = crm_handoff_response
                else:
                    content_text = f"ERROR: Mock not configured for tool: {tool_name}"

                return {
                    "result": {
                        "content": [{"text": content_text}]
                    }
                }
        return MockResponse()
    return mock_post

# =====================================================================
# 5. TEST CASES
# =====================================================================

@patch("lambdas.orchestrator.handler.signed_gateway_post")
def test_knowledge_base_resolution(mock_gateway, llm_judges):
    """
    Agent finds an answer in the Knowledge Base and resolves
    the query without attempting to hand off to a human.
    """

    # 1. Setup deterministic tool returns
    mock_db_result = json.dumps([mock_data.test_contact_passport_tracking, mock_data.test_contact_visa_application, mock_data.test_contact_uc_helpline])
    mock_kb_result = json.dumps(mock_data.test_kb_passport)

    mock_gateway.side_effect = create_mock_gateway(
        db_response=mock_db_result,
        kb_response=mock_kb_result
    )

    # 2. Invoke Lambda Handler locally
    event = {
        "body": json.dumps({
            "message": "How long will my new passport take to arrive?",
            "thread_id": "test-thread-001",
            "actor_id": "user-123"
        })
    }

    response = lambda_handler(event, context=None)
    body = json.loads(response["body"])
    actual_response = body["response"]

    # 3. Assert VISIBILITY (Agent trajectory)
    assert mock_gateway.call_count == 3, f"Expected 3 calls, got {mock_gateway.call_count}"     # todo: fix comment, ensure we assert actual agents called in order^^^

    init_call = mock_gateway.call_args_list[0][0][0]
    db_call = mock_gateway.call_args_list[1][0][0]
    kb_call = mock_gateway.call_args_list[2][0][0]

    # Assert actual tools called in order
    assert init_call.get("method") == "initialize"
    assert "query_department_database" in db_call["params"]["name"]
    assert "query_knowledge_base" in kb_call["params"]["name"]

    # 4. Assert RULE ADHERENCE
    assert "kb-passports" not in actual_response, "Agent leaked internal ID"
    assert "knowledge base" not in actual_response.lower(), "Agent used banned internal terminology"

    # 5. Assert OUTPUT QUALITY (DeepEval LLM Judge)
    test_case = LLMTestCase(
        input="How long will my new passport take to arrive?",
        actual_output=actual_response,
        retrieval_context=[mock_kb_result]
    )
    assert_test(test_case, llm_judges)


@patch("lambdas.orchestrator.handler.signed_gateway_post")
def test_kb_failure_routes_to_crm(mock_gateway, llm_judges):
    """
    Turn 1: KB lookup fails -> checks CRM -> prompts user.
    Turn 2: User confirms -> Agent emits routing SIGNAL.
    """

    # 0.1 Setup mock data
    mock_db_result = json.dumps([mock_data.test_contact_hmrc, mock_data.test_contact_dwp, mock_data.test_contact_uc_helpline])
    mock_kb_result = "ERROR: No knowledge base articles found."
    mock_crm_availability_result = "Live chat is AVAILABLE. Estimated wait: under 1 minute."
    mock_crm_handoff_result = "SIGNAL: initiate_live_handoff {'connection_details' : 'test'}"

    mock_gateway.side_effect = create_mock_gateway(
        db_response=mock_db_result,
        kb_response=mock_kb_result,
        crm_availability_response=mock_crm_availability_result,
        crm_handoff_response=mock_crm_handoff_result
    )

    # ==========================================
    # TURN 1: Initial Query & Fallback
    # ==========================================

    event_turn_1 = {
        "body": json.dumps({
            "message": "I have a highly specific tax question that isn't on the website.",
            "thread_id": "test-thread-002",
            "actor_id": "user-456"
        })
    }

    response_1 = lambda_handler(event_turn_1, context=None)
    actual_response_1 = json.loads(response_1["body"])["response"]

    # --- Turn 1 Assertions ---

    # 1.1 Assert VISIBILITY (Agent trajectory)
    assert mock_gateway.call_count == 4, "Expected 4 calls, got {mock_gateway.call_count}"

    db_call_payload = mock_gateway.call_args_list[1][0][0]
    kb_call_payload = mock_gateway.call_args_list[2][0][0]
    crm_call_payload = mock_gateway.call_args_list[3][0][0]

    assert "query_department_database" in db_call_payload["params"]["name"]
    assert "query_knowledge_base" in kb_call_payload["params"]["name"]
    assert "check_chat_availability" in crm_call_payload["params"]["name"]

    # 1.2 Assert ROUTING PROTOCOL
    response_lower_1 = actual_response_1.lower()
    assert "available right now" in response_lower_1 or "connect you" in response_lower_1
    assert "under 1 minute" in response_lower_1

    # 1.3 Assert Turn 1 QUALITY
    test_case_1 = LLMTestCase(
        input="I have a highly specific tax question that isn't on the website.",
        actual_output=actual_response_1,
        retrieval_context=[mock_kb_result, mock_db_result, mock_crm_availability_result]
    )
    assert_test(test_case_1, llm_judges)

    # ==========================================
    # TURN 2: User Confirms Handoff
    # ==========================================

    event_turn_2 = {
        "body": json.dumps({
            "message": "Yes, please connect me.",
            "thread_id": "test-thread-002",
            "actor_id": "user-456"
        })
    }

    response_2 = lambda_handler(event_turn_2, context=None)
    actual_response_2 = json.loads(response_2["body"])["response"]

    # --- Turn 2 Assertions ---

    # 2.1 Assert VISIBILITY (Agent trajectory)
    assert mock_gateway.call_count == 5, "Expected 5th call for connect to live chat tool, got got {mock_gateway.call_count} calls"
    handoff_call_payload = mock_gateway.call_args_list[4][0][0]
    assert "connect_to_live_chat" in handoff_call_payload["params"]["name"]

    # 2.2. Assert ACTION SIGNAL
    assert "SIGNAL: initiate_live_handoff" in actual_response_2, "Agent failed to emit the routing signal"

    # 2.3. Assert Turn 2 QUALITY
    test_case_2 = LLMTestCase(
        input="Yes, please connect me.",
        actual_output=actual_response_2,
        # Previous response passed as context so judge knows what the user is saying yes to
        retrieval_context=[actual_response_1]
    )
    assert_test(test_case_2, llm_judges)
