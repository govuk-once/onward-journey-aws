import json
from unittest.mock import MagicMock, patch
import pytest
from botocore.exceptions import ClientError

from app.lambdas.client_ws_router.handler import (
    _resolve_endpoint_url,
    create_session,
    get_table,
    lambda_handler,
)

TEST_AGENT_ARN = "arn:aws:bedrock:eu-west-2:123456789012:runtime/agent-123"
TEST_CRM_ARN = "arn:aws:lambda:eu-west-2:123456789012:function:crm-tool"
TEST_CONNECTION_ID = "conn-12345"

# Bedrock AgentCore runtimeSessionId enforces a strict 33-256 character length constraint (e.g. UUIDv4)
TEST_THREAD_ID = (
    "12345678-1234-1234-1234-123456789012"  # 36 chars (meets >= 33 requirement)
)

# Fixed epoch timestamp for deterministic audit and TTL assertions (14 days = 1,209,600s)
FIXED_EPOCH = 1700000000
EXPECTED_TTL = FIXED_EPOCH + (14 * 86_400)


@pytest.fixture(autouse=True)
def clear_handler_cache_before_each_test():
    """Reset the in-memory TABLE_CACHE before every test to ensure test isolation."""
    import app.lambdas.client_ws_router.handler as handler_module

    handler_module.TABLE_CACHE = None


@pytest.fixture
def fixed_time():
    """Freezes time.time to return FIXED_EPOCH for exact timestamp and TTL validation."""
    with patch(
        "app.lambdas.client_ws_router.handler.time.time",
        return_value=FIXED_EPOCH,
    ):
        yield FIXED_EPOCH


@pytest.fixture
def base_event():
    """Provides a baseline API Gateway WebSocket event structure."""
    return {
        "requestContext": {
            "routeKey": "$default",
            "connectionId": TEST_CONNECTION_ID,
            "domainName": "test.execute-api.eu-west-2.amazonaws.com",
            "stage": "dev",
            "authorizer": {
                "actor_id": "auth-actor-uuid",
            },
        }
    }


@pytest.fixture
def valid_payload():
    """Provides a valid body payload matching handler requirements."""
    return {
        "message": "Hello AI",
        "thread_id": TEST_THREAD_ID,
    }


@pytest.fixture
def mock_table():
    with patch("app.lambdas.client_ws_router.handler.get_table") as mock_get_table:
        table = MagicMock()
        mock_get_table.return_value = table
        # Default mock: active AI_CHAT session
        table.get_item.return_value = {
            "Item": {
                "session_id": TEST_THREAD_ID,
                "state": "AI_CHAT",
                "created_at": FIXED_EPOCH,
                "updated_at": FIXED_EPOCH,
            }
        }
        yield table


@pytest.fixture(autouse=True)
def mock_clients():
    """Patches global AWS SDK clients across all test classes."""
    with patch(
        "app.lambdas.client_ws_router.handler.AGENTCORE_CLIENT"
    ) as mock_agentcore, patch(
        "app.lambdas.client_ws_router.handler.LAMBDA_CLIENT"
    ) as mock_lambda:
        yield {
            "agentcore": mock_agentcore,
            "lambda_client": mock_lambda,
        }


class TestEndpointUrlResolution:
    """Tests verifying scheme normalisation for botocore endpoint compatibility."""

    @pytest.mark.parametrize(
        "raw_url, expected",
        [
            ("agent.mock.aws", "https://agent.mock.aws"),
            ("  agent.mock.aws  ", "https://agent.mock.aws"),
            ("https://custom.endpoint.aws", "https://custom.endpoint.aws"),
            ("http://localhost:8000", "http://localhost:8000"),
            (None, None),
            ("", None),
            ("   ", None),
        ],
    )
    def test_resolve_endpoint_url_normalises_schemes(self, raw_url, expected):
        assert _resolve_endpoint_url(raw_url) == expected


class TestConfiguration:

    def test_get_table_raises_runtime_error_when_sessions_table_name_missing(
        self, monkeypatch
    ):
        """Verifies get_table fails fast if SESSIONS_TABLE_NAME is absent from the environment."""
        monkeypatch.delenv("SESSIONS_TABLE_NAME", raising=False)

        with pytest.raises(
            RuntimeError,
            match="Missing required environment variable: SESSIONS_TABLE_NAME",
        ):
            get_table()

    def test_get_table_reuses_cached_table_instance(self, monkeypatch):
        """Verifies get_table caches the Table resource in-memory across warm calls."""
        monkeypatch.setenv("SESSIONS_TABLE_NAME", "test-sessions-table")
        first_instance = get_table()
        second_instance = get_table()

        assert first_instance is second_instance


class TestConnectionLifecycle:

    def test_connect_route_returns_ok(self, base_event):
        base_event["requestContext"]["routeKey"] = "$connect"
        response = lambda_handler(base_event, None)

        assert response["statusCode"] == 200
        assert response["body"] == "Connected"

    def test_disconnect_route_returns_ok(self, base_event):
        base_event["requestContext"]["routeKey"] = "$disconnect"
        response = lambda_handler(base_event, None)

        assert response["statusCode"] == 200
        assert response["body"] == "Disconnected"

    def test_unsupported_route_returns_bad_request(self, base_event):
        base_event["requestContext"]["routeKey"] = "$unknown"
        response = lambda_handler(base_event, None)

        assert response["statusCode"] == 400
        assert response["body"] == "Unsupported route"


class TestPayloadValidation:

    def test_missing_message_returns_bad_request(
        self, base_event, valid_payload, mock_table, mock_clients
    ):
        del valid_payload["message"]
        base_event["body"] = json.dumps(valid_payload)

        response = lambda_handler(base_event, None)

        assert response["statusCode"] == 400
        assert "Missing required payload attributes" in response["body"]
        mock_table.get_item.assert_not_called()
        mock_clients["agentcore"].invoke_agent_runtime.assert_not_called()

    def test_missing_thread_id_returns_bad_request(
        self, base_event, valid_payload, mock_table, mock_clients
    ):
        del valid_payload["thread_id"]
        base_event["body"] = json.dumps(valid_payload)

        response = lambda_handler(base_event, None)

        assert response["statusCode"] == 400
        assert "Missing required payload attributes" in response["body"]
        mock_table.get_item.assert_not_called()
        mock_clients["agentcore"].invoke_agent_runtime.assert_not_called()

    @pytest.mark.parametrize(
        "invalid_id",
        [
            "short-id-123",
            "12345678-1234-1234-1234-12345678",  # Exactly 32 characters (1 char short of 33)
        ],
    )
    def test_thread_id_below_minimum_length_returns_bad_request(
        self, base_event, valid_payload, invalid_id, mock_table, mock_clients
    ):
        """Verifies that thread_ids under Bedrock's 33-character minimum are rejected before database or model calls."""
        valid_payload["thread_id"] = invalid_id
        base_event["body"] = json.dumps(valid_payload)

        response = lambda_handler(base_event, None)

        assert response["statusCode"] == 400
        assert "Invalid 'thread_id'" in response["body"]
        mock_table.get_item.assert_not_called()
        mock_clients["agentcore"].invoke_agent_runtime.assert_not_called()

    def test_non_json_string_body_fails_validation_gracefully(
        self, base_event, mock_table, mock_clients
    ):
        base_event["body"] = "plain text message without thread_id"

        response = lambda_handler(base_event, None)

        assert response["statusCode"] == 400
        assert "Missing required payload attributes" in response["body"]
        mock_table.get_item.assert_not_called()
        mock_clients["agentcore"].invoke_agent_runtime.assert_not_called()


class TestAuthoriserContext:

    def test_extracts_actor_id_from_authorizer_context(
        self, base_event, valid_payload, mock_table, mock_clients
    ):
        base_event["requestContext"]["authorizer"] = {"actor_id": "authorizer-actor"}
        base_event["body"] = json.dumps(valid_payload)

        response = lambda_handler(base_event, None)

        assert response["statusCode"] == 200
        call_kwargs = mock_clients["agentcore"].invoke_agent_runtime.call_args.kwargs
        sent_payload = json.loads(call_kwargs["payload"])
        assert sent_payload["actor_id"] == "authorizer-actor"

    def test_ignores_spoofed_actor_id_in_body_and_strictly_uses_authorizer_context(
        self, base_event, valid_payload, mock_table, mock_clients
    ):
        """Ensures a client cannot spoof identity by injecting an actor_id into the message body."""
        valid_payload["actor_id"] = "malicious-spoofed-actor"
        base_event["requestContext"]["authorizer"] = {
            "actor_id": "verified-authorizer-actor"
        }
        base_event["body"] = json.dumps(valid_payload)

        response = lambda_handler(base_event, None)

        assert response["statusCode"] == 200
        call_kwargs = mock_clients["agentcore"].invoke_agent_runtime.call_args.kwargs
        sent_payload = json.loads(call_kwargs["payload"])
        assert sent_payload["actor_id"] == "verified-authorizer-actor"
        assert sent_payload["actor_id"] != "malicious-spoofed-actor"

    def test_returns_unauthorised_when_actor_id_missing_from_context(
        self, base_event, valid_payload, mock_table, mock_clients
    ):
        base_event["requestContext"]["authorizer"] = {}
        base_event["body"] = json.dumps(valid_payload)

        response = lambda_handler(base_event, None)

        assert response["statusCode"] == 401
        assert "Unauthorised" in response["body"]
        mock_table.get_item.assert_not_called()
        mock_clients["agentcore"].invoke_agent_runtime.assert_not_called()


class TestAgentCoreRouting:

    def test_stringified_json_body_routes_and_matches_agentcore_contract(
        self, base_event, valid_payload, mock_table, mock_clients
    ):
        base_event["body"] = json.dumps(valid_payload)

        response = lambda_handler(base_event, None)

        assert response["statusCode"] == 200
        assert response["body"] == "Message routed"

        mock_clients["agentcore"].invoke_agent_runtime.assert_called_once()
        call_kwargs = mock_clients["agentcore"].invoke_agent_runtime.call_args.kwargs

        assert call_kwargs["runtimeSessionId"] == TEST_THREAD_ID
        assert call_kwargs["agentRuntimeArn"] == TEST_AGENT_ARN
        assert call_kwargs["contentType"] == "application/json"
        assert call_kwargs["accept"] == "application/json"

        sent_payload = json.loads(call_kwargs["payload"])
        assert sent_payload == {
            "message": "Hello AI",
            "connection_id": TEST_CONNECTION_ID,
            "domain_name": "test.execute-api.eu-west-2.amazonaws.com",
            "stage": "dev",
            "actor_id": "auth-actor-uuid",
            "thread_id": TEST_THREAD_ID,
        }

    def test_dict_body_routes_successfully(
        self, base_event, valid_payload, mock_table, mock_clients
    ):
        base_event["body"] = valid_payload

        response = lambda_handler(base_event, None)

        assert response["statusCode"] == 200
        assert response["body"] == "Message routed"
        mock_clients["agentcore"].invoke_agent_runtime.assert_called_once()


class TestSessionManagementAndRouting:

    def test_creates_session_with_audit_timestamps_when_existing_session_not_found(
        self, base_event, valid_payload, mock_table, mock_clients, fixed_time
    ):
        mock_table.get_item.return_value = {}
        base_event["body"] = json.dumps(valid_payload)

        response = lambda_handler(base_event, None)

        assert response["statusCode"] == 200
        mock_table.put_item.assert_called_once()
        mock_table.update_item.assert_not_called()

        put_call_kwargs = mock_table.put_item.call_args.kwargs
        assert (
            put_call_kwargs["ConditionExpression"] == "attribute_not_exists(session_id)"
        )

        put_item = put_call_kwargs["Item"]
        assert put_item["session_id"] == TEST_THREAD_ID
        assert put_item["websocket_connection_id"] == TEST_CONNECTION_ID
        assert put_item["state"] == "AI_CHAT"
        assert put_item["created_at"] == FIXED_EPOCH
        assert put_item["updated_at"] == FIXED_EPOCH
        assert put_item["ttl"] == EXPECTED_TTL
        mock_clients["agentcore"].invoke_agent_runtime.assert_called_once()

    def test_creates_session_recovers_gracefully_on_concurrent_race_condition(
        self, mock_table, fixed_time
    ):
        """Verifies that create_session gracefully handles ConditionalCheckFailedException when another worker initialised the session first."""
        mock_table.put_item.side_effect = ClientError(
            {
                "Error": {
                    "Code": "ConditionalCheckFailedException",
                    "Message": "The conditional request failed",
                }
            },
            "PutItem",
        )
        mock_table.get_item.return_value = {
            "Item": {
                "session_id": TEST_THREAD_ID,
                "state": "AI_CHAT",
                "created_at": FIXED_EPOCH - 10,
                "updated_at": FIXED_EPOCH - 10,
            }
        }

        session = create_session(TEST_THREAD_ID, "auth-actor", TEST_CONNECTION_ID)

        assert session["session_id"] == TEST_THREAD_ID
        assert session["updated_at"] == FIXED_EPOCH
        mock_table.update_item.assert_called_once()

    def test_updates_timestamp_and_rolling_ttl_on_existing_session(
        self, base_event, valid_payload, mock_table, mock_clients, fixed_time
    ):
        base_event["body"] = json.dumps(valid_payload)
        response = lambda_handler(base_event, None)

        assert response["statusCode"] == 200
        mock_table.update_item.assert_called_once()
        mock_table.put_item.assert_not_called()

        update_args = mock_table.update_item.call_args.kwargs
        assert update_args["Key"] == {"session_id": TEST_THREAD_ID}
        assert update_args["ExpressionAttributeValues"][":conn"] == TEST_CONNECTION_ID
        assert update_args["ExpressionAttributeValues"][":now"] == FIXED_EPOCH
        assert update_args["ExpressionAttributeValues"][":ttl"] == EXPECTED_TTL

    def test_routes_to_crm_tool_and_bypasses_agentcore_when_state_is_human_chat(
        self, base_event, valid_payload, mock_table, mock_clients
    ):
        mock_table.get_item.return_value = {
            "Item": {
                "session_id": TEST_THREAD_ID,
                "state": "HUMAN_CHAT",
                "crm_chat_id": "storm-interaction-987",
                "live_chat_identifier": "dvla-storm",
                "created_at": FIXED_EPOCH,
                "updated_at": FIXED_EPOCH,
            }
        }
        base_event["body"] = json.dumps(valid_payload)

        response = lambda_handler(base_event, None)

        assert response["statusCode"] == 200
        assert response["body"] == "Message routed to human adviser"

        mock_clients["agentcore"].invoke_agent_runtime.assert_not_called()
        mock_clients["lambda_client"].invoke.assert_called_once()
        invoke_kwargs = mock_clients["lambda_client"].invoke.call_args.kwargs

        assert invoke_kwargs["FunctionName"] == TEST_CRM_ARN
        assert invoke_kwargs["InvocationType"] == "Event"
        crm_payload = json.loads(invoke_kwargs["Payload"])

        assert crm_payload == {
            "method": "forward_user_message_to_crm",
            "message": "Hello AI",
            "thread_id": TEST_THREAD_ID,
            "actor_id": "auth-actor-uuid",
            "crm_chat_id": "storm-interaction-987",
            "live_chat_identifier": "dvla-storm",
        }


class TestErrorHandlingAndValidation:

    @pytest.mark.parametrize(
        "session_attrs",
        [
            {"live_chat_identifier": "dvla-storm"},  # missing crm_chat_id
            {"crm_chat_id": "storm-interaction-987"},  # missing live_chat_identifier
            {},  # missing both
        ],
    )
    def test_human_chat_missing_required_routing_attributes_returns_500(
        self, base_event, valid_payload, mock_table, mock_clients, session_attrs
    ):
        session_data = {
            "session_id": TEST_THREAD_ID,
            "state": "HUMAN_CHAT",
            **session_attrs,
        }
        mock_table.get_item.return_value = {"Item": session_data}
        base_event["body"] = json.dumps(valid_payload)

        response = lambda_handler(base_event, None)

        assert response["statusCode"] == 500
        assert "missing CRM routing attributes" in response["body"]
        mock_clients["lambda_client"].invoke.assert_not_called()

    def test_unrecognised_session_state_returns_500(
        self, base_event, valid_payload, mock_table, mock_clients
    ):
        mock_table.get_item.return_value = {
            "Item": {
                "session_id": TEST_THREAD_ID,
                "state": "INVALID_STATE",
            }
        }
        base_event["body"] = json.dumps(valid_payload)

        response = lambda_handler(base_event, None)

        assert response["statusCode"] == 500
        assert "Unsupported session state" in response["body"]
        mock_clients["agentcore"].invoke_agent_runtime.assert_not_called()
        mock_clients["lambda_client"].invoke.assert_not_called()

    def test_session_missing_state_attribute_returns_500(
        self, base_event, valid_payload, mock_table, mock_clients
    ):
        mock_table.get_item.return_value = {
            "Item": {
                "session_id": TEST_THREAD_ID,
            }
        }
        base_event["body"] = json.dumps(valid_payload)

        response = lambda_handler(base_event, None)

        assert response["statusCode"] == 500
        assert "Invalid session state in database" in response["body"]
        mock_clients["agentcore"].invoke_agent_runtime.assert_not_called()
        mock_clients["lambda_client"].invoke.assert_not_called()

    def test_missing_agent_runtime_arn_returns_500(
        self, base_event, valid_payload, mock_table, mock_clients, monkeypatch
    ):
        monkeypatch.delenv("AGENT_RUNTIME_ARN", raising=False)
        base_event["body"] = json.dumps(valid_payload)

        response = lambda_handler(base_event, None)

        assert response["statusCode"] == 500
        assert response["body"] == "Server configuration error"
        mock_clients["agentcore"].invoke_agent_runtime.assert_not_called()

    def test_missing_crm_tool_arn_in_human_chat_returns_500(
        self, base_event, valid_payload, mock_table, mock_clients, monkeypatch
    ):
        mock_table.get_item.return_value = {
            "Item": {
                "session_id": TEST_THREAD_ID,
                "state": "HUMAN_CHAT",
                "crm_chat_id": "storm-interaction-987",
                "live_chat_identifier": "dvla-storm",
            }
        }
        monkeypatch.delenv("CRM_TOOL_LAMBDA_ARN", raising=False)
        base_event["body"] = json.dumps(valid_payload)

        response = lambda_handler(base_event, None)

        assert response["statusCode"] == 500
        assert response["body"] == "Server configuration error"
        mock_clients["lambda_client"].invoke.assert_not_called()

    def test_missing_gateway_context_returns_500(
        self, base_event, valid_payload, mock_table, mock_clients
    ):
        del base_event["requestContext"]["domainName"]
        base_event["body"] = json.dumps(valid_payload)

        response = lambda_handler(base_event, None)

        assert response["statusCode"] == 500
        assert "missing gateway context" in response["body"]
        mock_clients["agentcore"].invoke_agent_runtime.assert_not_called()

    def test_agentcore_client_error_returns_500(
        self, base_event, valid_payload, mock_table, mock_clients
    ):
        mock_clients["agentcore"].invoke_agent_runtime.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "Access Denied"}},
            "InvokeAgentRuntime",
        )
        base_event["body"] = json.dumps(valid_payload)

        response = lambda_handler(base_event, None)

        assert response["statusCode"] == 500
        assert response["body"] == "Failed to route message to AI agent"

    def test_crm_tool_client_error_returns_500(
        self, base_event, valid_payload, mock_table, mock_clients
    ):
        mock_table.get_item.return_value = {
            "Item": {
                "session_id": TEST_THREAD_ID,
                "state": "HUMAN_CHAT",
                "crm_chat_id": "storm-interaction-987",
                "live_chat_identifier": "dvla-storm",
            }
        }
        mock_clients["lambda_client"].invoke.side_effect = ClientError(
            {
                "Error": {
                    "Code": "ResourceNotFoundException",
                    "Message": "Function Not Found",
                }
            },
            "Invoke",
        )
        base_event["body"] = json.dumps(valid_payload)

        response = lambda_handler(base_event, None)

        assert response["statusCode"] == 500
        assert response["body"] == "Failed to route message to adviser"
