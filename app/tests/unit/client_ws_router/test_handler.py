import json
from unittest.mock import patch
import pytest
from botocore.exceptions import ClientError

from app.lambdas.client_ws_router.handler import lambda_handler


@pytest.fixture
def base_event():
    """Provides a baseline API Gateway WebSocket event structure."""
    return {
        "requestContext": {
            "routeKey": "$default",
            "connectionId": "conn-12345",
            "domainName": "test.execute-api.eu-west-2.amazonaws.com",
            "stage": "stage-name",
        }
    }


@pytest.fixture
def valid_payload():
    """Provides a valid body payload matching handler requirements."""
    return {
        "action": "sendMessage",
        "message": "Hello AI",
        "actor_id": "user-789",
        "thread_id": "thread-001",
    }


@pytest.fixture(autouse=True)
def mock_env_and_client():
    """Patches environment variables and the global boto3 client across all test classes."""
    with patch(
        "app.lambdas.client_ws_router.handler.AGENT_RUNTIME_ARN",
        "arn:aws:bedrock:eu-west-2:123456789012:runtime/agent-123",
    ), patch("app.lambdas.client_ws_router.handler.AGENTCORE_CLIENT") as mock_client:
        yield mock_client


class TestConnectionLifecycle:
    """Tests WebSocket lifecycle events and route matching."""

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
    """Tests payload parsing and mandatory attribute checks on the $default route."""

    @pytest.mark.parametrize("missing_field", ["message", "actor_id", "thread_id"])
    def test_missing_required_attribute_returns_bad_request(
        self, base_event, valid_payload, missing_field, mock_env_and_client
    ):
        del valid_payload[missing_field]
        base_event["body"] = json.dumps(valid_payload)

        response = lambda_handler(base_event, None)

        assert response["statusCode"] == 400
        assert "Missing required payload attributes" in response["body"]
        mock_env_and_client.invoke_agent_runtime.assert_not_called()

    def test_non_json_string_body_fails_validation_gracefully(
        self, base_event, mock_env_and_client
    ):
        base_event["body"] = "plain text message without schema"

        response = lambda_handler(base_event, None)

        assert response["statusCode"] == 400
        assert "Missing required payload attributes" in response["body"]
        mock_env_and_client.invoke_agent_runtime.assert_not_called()


class TestAgentCoreRouting:
    """Tests successful invocation of the Bedrock AgentCore runtime."""

    def test_stringified_json_body_routes_successfully(
        self, base_event, valid_payload, mock_env_and_client
    ):
        base_event["body"] = json.dumps(valid_payload)

        response = lambda_handler(base_event, None)

        assert response["statusCode"] == 200
        assert response["body"] == "Message routed"

        mock_env_and_client.invoke_agent_runtime.assert_called_once()
        call_kwargs = mock_env_and_client.invoke_agent_runtime.call_args.kwargs

        assert call_kwargs["runtimeSessionId"] == "conn-12345"
        assert (
            call_kwargs["agentRuntimeArn"]
            == "arn:aws:bedrock:eu-west-2:123456789012:runtime/agent-123"
        )

        sent_payload = json.loads(call_kwargs["inputText"])
        assert sent_payload["message"] == "Hello AI"
        assert sent_payload["actor_id"] == "user-789"
        assert sent_payload["thread_id"] == "thread-001"
        assert sent_payload["connection_id"] == "conn-12345"

    def test_dict_body_routes_successfully(
        self, base_event, valid_payload, mock_env_and_client
    ):
        base_event["body"] = valid_payload

        response = lambda_handler(base_event, None)

        assert response["statusCode"] == 200
        assert response["body"] == "Message routed"
        mock_env_and_client.invoke_agent_runtime.assert_called_once()


class TestErrorHandling:
    """Tests handling of configuration errors and runtime invocation exceptions."""

    def test_missing_agent_runtime_arn_returns_server_error(
        self, base_event, valid_payload
    ):
        base_event["body"] = json.dumps(valid_payload)

        with patch("app.lambdas.client_ws_router.handler.AGENT_RUNTIME_ARN", ""):
            response = lambda_handler(base_event, None)

        assert response["statusCode"] == 500
        assert response["body"] == "Server configuration error"

    def test_boto3_client_error_returns_server_error(
        self, base_event, valid_payload, mock_env_and_client
    ):
        base_event["body"] = json.dumps(valid_payload)

        error_response = {
            "Error": {"Code": "AccessDeniedException", "Message": "Access Denied"},
            "ResponseMetadata": {"RequestId": "req-999"},
        }
        mock_env_and_client.invoke_agent_runtime.side_effect = ClientError(
            error_response, "InvokeAgentRuntime"
        )

        response = lambda_handler(base_event, None)

        assert response["statusCode"] == 500
        assert response["body"] == "Failed to route message to AI agent"

    def test_generic_exception_returns_server_error(
        self, base_event, valid_payload, mock_env_and_client
    ):
        base_event["body"] = json.dumps(valid_payload)
        mock_env_and_client.invoke_agent_runtime.side_effect = Exception(
            "Connection lost"
        )

        response = lambda_handler(base_event, None)

        assert response["statusCode"] == 500
        assert response["body"] == "Failed to route message to AI agent"
