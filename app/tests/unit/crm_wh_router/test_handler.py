import json
from unittest.mock import patch
import pytest
from botocore.exceptions import ClientError

from app.lambdas.crm_wh_router.handler import lambda_handler


@pytest.fixture
def mock_context():
    class Context:
        aws_request_id = "test-request-id-123"

    return Context()


@pytest.fixture
def mock_table():
    with patch("app.lambdas.crm_wh_router.handler.table") as table:
        table.query.return_value = {
            "Items": [{"session_id": "sess-123", "websocket_connection_id": "conn-456"}]
        }
        yield table


@pytest.fixture
def mock_apigw():
    with patch("app.lambdas.crm_wh_router.handler.apigw_client") as apigw:
        yield apigw


class TestPayloadValidation:
    def test_invalid_json(self, mock_context):
        response = lambda_handler({"body": "{ invalid_json: "}, mock_context)

        assert response["statusCode"] == 400
        assert response["headers"]["Content-Type"] == "application/json"
        assert json.loads(response["body"])["error"] == "Invalid JSON body"

    def test_empty_body(self, mock_context):
        response = lambda_handler({}, mock_context)

        assert response["statusCode"] == 400
        assert (
            json.loads(response["body"])["error"]
            == "Missing required interaction identifier"
        )

    def test_missing_interaction_identifier(self, mock_context):
        event = {"body": json.dumps({"Event": {"Type": "Message", "Body": {}}})}
        response = lambda_handler(event, mock_context)

        assert response["statusCode"] == 400
        assert (
            json.loads(response["body"])["error"]
            == "Missing required interaction identifier"
        )


class TestMessageRouting:
    def test_typing_indicator_acknowledged(self, mock_context, mock_table, mock_apigw):
        event = {
            "body": json.dumps(
                {
                    "Event": {
                        "Type": "Typing",
                        "Body": {
                            "To": {"Id": "storm-chat-123"},
                            "TypingIndicator": "Started",
                        },
                    }
                }
            )
        }

        response = lambda_handler(event, mock_context)
        body = json.loads(response["body"])

        assert response["statusCode"] == 200
        assert body["status"] == "acknowledged"
        assert body["request_id"] == "test-request-id-123"

    def test_text_message_relayed_successfully(
        self, mock_context, mock_table, mock_apigw
    ):
        event = {
            "body": json.dumps(
                {
                    "Event": {
                        "Type": "Message",
                        "Timestamp": 1662645371669,
                        "Body": {
                            "ConversationId": "storm-chat-789",
                            "From": {"Id": "100", "Alias": "Sarah", "Type": "Agent"},
                            "MessageParts": [
                                {"Type": "Text", "Text": "Hello from adviser Sarah"}
                            ],
                        },
                    }
                }
            )
        }

        response = lambda_handler(event, mock_context)

        assert response["statusCode"] == 200
        assert json.loads(response["body"])["status"] == "acknowledged"

        mock_table.query.assert_called_once_with(
            IndexName="crm_chat_id-index",
            KeyConditionExpression="crm_chat_id = :cid",
            ExpressionAttributeValues={":cid": "storm-chat-789"},
            Limit=1,
        )

        mock_apigw.post_to_connection.assert_called_once()
        _, call_kwargs = mock_apigw.post_to_connection.call_args
        assert call_kwargs["ConnectionId"] == "conn-456"

        pushed_frame = json.loads(call_kwargs["Data"].decode("utf-8"))
        assert pushed_frame["type"] == "human_message"
        assert pushed_frame["text"] == "Hello from adviser Sarah"
        assert pushed_frame["metadata"]["agent_name"] == "Sarah"


class TestSessionLifecycleAndResilience:
    def test_status_ended_reverts_state_to_ai_chat(
        self, mock_context, mock_table, mock_apigw
    ):
        event = {
            "body": json.dumps(
                {
                    "Event": {
                        "Type": "Status",
                        "Timestamp": 1662644052816,
                        "Body": {
                            "To": {"Id": "storm-chat-789"},
                            "Status": "Ended",
                            "UserName": "Agent01",
                        },
                    }
                }
            )
        }

        response = lambda_handler(event, mock_context)

        assert response["statusCode"] == 200

        mock_table.update_item.assert_called_once()
        _, update_kwargs = mock_table.update_item.call_args
        assert update_kwargs["Key"] == {"session_id": "sess-123"}
        assert update_kwargs["ExpressionAttributeValues"][":ai_state"] == "AI_CHAT"
        assert ":new_ttl" in update_kwargs["ExpressionAttributeValues"]

        _, call_kwargs = mock_apigw.post_to_connection.call_args
        pushed_frame = json.loads(call_kwargs["Data"].decode("utf-8"))
        assert pushed_frame["type"] == "state_change"
        assert pushed_frame["state"] == "AI_CHAT"

    def test_stale_websocket_connection_drops_gracefully(
        self, mock_context, mock_table, mock_apigw
    ):
        mock_apigw.post_to_connection.side_effect = ClientError(
            {"Error": {"Code": "GoneException"}}, "PostToConnection"
        )

        event = {
            "body": json.dumps(
                {
                    "Event": {
                        "Type": "Message",
                        "Body": {
                            "ConversationId": "storm-chat-789",
                            "MessageParts": [{"Type": "Text", "Text": "Test message"}],
                        },
                    }
                }
            )
        }

        response = lambda_handler(event, mock_context)
        body = json.loads(response["body"])

        assert response["statusCode"] == 200
        assert body["status"] == "dropped"
        assert body["reason"] == "recipient_offline"

    def test_unknown_session_drops_gracefully(self, mock_context, mock_table):
        mock_table.query.return_value = {"Items": []}

        event = {
            "body": json.dumps(
                {
                    "Event": {
                        "Type": "Message",
                        "Body": {
                            "ConversationId": "unknown-chat-id",
                            "MessageParts": [{"Type": "Text", "Text": "Test message"}],
                        },
                    }
                }
            )
        }

        response = lambda_handler(event, mock_context)
        body = json.loads(response["body"])

        assert response["statusCode"] == 200
        assert body["status"] == "dropped"
        assert body["reason"] == "session_not_found"
