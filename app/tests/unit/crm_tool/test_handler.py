import json
from unittest.mock import MagicMock, patch
import pytest

from app.lambdas.crm_tool.handler import (
    create_audit_json,
    lambda_handler,
)

TEST_THREAD_ID = "thread-12345"
TEST_CHAT_ID = "dvla-storm"


@pytest.fixture
def base_crm_event():
    return {
        "method": "forward_user_message_to_crm",
        "thread_id": TEST_THREAD_ID,
        "live_chat_identifier": TEST_CHAT_ID,
        "message": "Hi, I am Jane Doe. My NI number is QQ123456C. Can you help?",
        "actor_id": "tester-123",
    }


@pytest.fixture
def payload_guard():
    """
    Watches an event dictionary and asserts during fixture teardown
    that the payload was not mutated in place after guard.watch(event) was called.
    """
    state = {}

    class Guard:
        def watch(self, target: dict):
            state["target"] = target
            state["snapshot"] = target.copy()

    yield Guard()

    if "target" in state:
        assert (
            state["target"] == state["snapshot"]
        ), "Event dictionary was mutated in place"


@pytest.fixture
def mock_crm_adapter():
    with patch(
        "app.lambdas.crm_tool.handler.ProviderFactory.get_provider"
    ) as mock_factory:
        adapter_instance = MagicMock()
        mock_factory.return_value = adapter_instance
        yield adapter_instance


@pytest.fixture
def mock_log_metric():
    with patch("app.lambdas.crm_tool.handler.log_metric") as mock_log:
        yield mock_log


class TestAuditLogPIISanitisation:

    def test_strips_user_message_text_and_records_length_metric(
        self, base_crm_event, payload_guard
    ):
        payload_guard.watch(base_crm_event)
        result = json.loads(create_audit_json(base_crm_event))

        assert "message" not in result
        assert "Jane Doe" not in json.dumps(result)
        assert result["message_length"] == 59
        assert result["method"] == "forward_user_message_to_crm"
        assert result["thread_id"] == TEST_THREAD_ID

    def test_payload_without_message_key_omits_length_field(
        self, base_crm_event, payload_guard
    ):
        del base_crm_event["message"]
        payload_guard.watch(base_crm_event)

        result = json.loads(create_audit_json(base_crm_event))

        assert "message" not in result
        assert "message_length" not in result

    def test_none_message_value_records_zero_length(
        self, base_crm_event, payload_guard
    ):
        base_crm_event["message"] = None
        payload_guard.watch(base_crm_event)

        result = json.loads(create_audit_json(base_crm_event))

        assert "message" not in result
        assert result["message_length"] == 0


class TestMandatoryPayloadValidation:

    def test_missing_method_returns_validation_error(self, base_crm_event):
        del base_crm_event["method"]
        response = lambda_handler(base_crm_event, None)

        assert "error" in response
        assert "VALIDATION_ERROR" in response["error"]
        assert "method" in response["error"]

    def test_missing_thread_id_returns_validation_error(self, base_crm_event):
        del base_crm_event["thread_id"]
        response = lambda_handler(base_crm_event, None)

        assert "error" in response
        assert "VALIDATION_ERROR" in response["error"]
        assert "thread_id" in response["error"]


class TestCRMMethodRouting:

    def test_forward_user_message_returns_success_and_bypasses_crm_adapter(
        self, base_crm_event, mock_crm_adapter, payload_guard
    ):
        base_crm_event["method"] = "forward_user_message_to_crm"
        payload_guard.watch(base_crm_event)

        response = lambda_handler(base_crm_event, None)

        assert response == {
            "status": "success",
            "message": "User message forwarded to CRM",
        }
        mock_crm_adapter.fetch_adviser_availability.assert_not_called()

    def test_check_chat_availability_returns_available_when_advisers_online(
        self, base_crm_event, mock_crm_adapter, payload_guard
    ):
        expected_response = {"status": "available", "adviser_count": 4}
        base_crm_event["method"] = "check_chat_availability"
        mock_crm_adapter.fetch_adviser_availability.return_value = expected_response
        payload_guard.watch(base_crm_event)

        response = lambda_handler(base_crm_event, None)

        assert response == expected_response
        mock_crm_adapter.fetch_adviser_availability.assert_called_once()

    def test_check_chat_availability_returns_unavailable_when_queue_closed_or_full(
        self, base_crm_event, mock_crm_adapter, payload_guard
    ):
        expected_response = {
            "status": "unavailable",
            "reason": "outside_operating_hours",
        }
        base_crm_event["method"] = "check_chat_availability"
        mock_crm_adapter.fetch_adviser_availability.return_value = expected_response
        payload_guard.watch(base_crm_event)

        response = lambda_handler(base_crm_event, None)

        assert response == expected_response
        mock_crm_adapter.fetch_adviser_availability.assert_called_once()

    def test_connect_to_live_chat_generates_crm_handoff_and_logs_metric(
        self, base_crm_event, mock_crm_adapter, mock_log_metric, payload_guard
    ):
        expected_response = {
            "handoff": "initiated",
            "crm_chat_id": "crm-chat-999",
        }
        base_crm_event["method"] = "connect_to_live_chat"
        mock_crm_adapter.generate_handoff_signal.return_value = expected_response
        payload_guard.watch(base_crm_event)

        response = lambda_handler(base_crm_event, None)

        assert response == expected_response
        mock_crm_adapter.generate_handoff_signal.assert_called_once_with(base_crm_event)
        mock_log_metric.assert_called_once_with(
            "LiveHandoffInitiated", {"Target": TEST_CHAT_ID, "Trace": TEST_THREAD_ID}
        )

    def test_connect_to_live_chat_returns_crm_rejection_payload(
        self, base_crm_event, mock_crm_adapter, mock_log_metric, payload_guard
    ):
        expected_response = {
            "status": "rejected",
            "error_code": "CRM_CAPACITY_EXCEEDED",
        }
        base_crm_event["method"] = "connect_to_live_chat"
        mock_crm_adapter.generate_handoff_signal.return_value = expected_response
        payload_guard.watch(base_crm_event)

        response = lambda_handler(base_crm_event, None)

        assert response == expected_response
        mock_crm_adapter.generate_handoff_signal.assert_called_once_with(base_crm_event)
        mock_log_metric.assert_called_once_with(
            "LiveHandoffInitiated", {"Target": TEST_CHAT_ID, "Trace": TEST_THREAD_ID}
        )

    def test_unsupported_method_returns_method_error(
        self, base_crm_event, mock_crm_adapter
    ):
        base_crm_event["method"] = "unknown_method_xyz"

        response = lambda_handler(base_crm_event, None)

        assert response == {"error": "METHOD_ERROR: unknown_method_xyz not supported."}


class TestInternalErrorObfuscation:

    def test_unhandled_exception_during_availability_check_returns_generic_service_error(
        self, base_crm_event, mock_crm_adapter
    ):
        base_crm_event["method"] = "check_chat_availability"
        mock_crm_adapter.fetch_adviser_availability.side_effect = ConnectionError(
            "CRM Gateway timeout"
        )

        response = lambda_handler(base_crm_event, None)

        assert response == {"error": "SERVICE_ERROR: Internal Tool Error."}

    def test_crm_handoff_exception_aborts_metric_and_returns_generic_service_error(
        self, base_crm_event, mock_crm_adapter, mock_log_metric
    ):
        base_crm_event["method"] = "connect_to_live_chat"
        mock_crm_adapter.generate_handoff_signal.side_effect = TimeoutError(
            "Socket connection dropped"
        )

        response = lambda_handler(base_crm_event, None)

        assert response == {"error": "SERVICE_ERROR: Internal Tool Error."}
        mock_log_metric.assert_not_called()

    def test_unresolvable_provider_factory_returns_generic_service_error(
        self, base_crm_event
    ):
        base_crm_event["method"] = "check_chat_availability"
        with patch(
            "app.lambdas.crm_tool.handler.ProviderFactory.get_provider",
            side_effect=ValueError("Invalid provider identifier: unknown-crm"),
        ):
            response = lambda_handler(base_crm_event, None)

            assert response == {"error": "SERVICE_ERROR: Internal Tool Error."}
