import json
import pytest
from app.lambdas.crm_wh_router.handler import lambda_handler


@pytest.fixture
def mock_context():
    class Context:
        aws_request_id = "test-request-id-123"

    return Context()


def test_lambda_handler_valid_json(mock_context):
    """Test that a valid JSON payload returns a 200 OK acknowledgment."""
    event = {
        "body": json.dumps({"message": "test_payload", "type": "typing_indicator"})
    }

    response = lambda_handler(event, mock_context)

    assert response["statusCode"] == 200
    assert response["headers"]["Content-Type"] == "application/json"

    body = json.loads(response["body"])
    assert body["status"] == "acknowledged"
    assert body["request_id"] == "test-request-id-123"


def test_lambda_handler_invalid_json(mock_context):
    """Test that invalid JSON in the body returns a 400 Bad Request."""
    event = {"body": "{ invalid_json: "}

    response = lambda_handler(event, mock_context)

    assert response["statusCode"] == 400
    assert response["headers"]["Content-Type"] == "application/json"

    body = json.loads(response["body"])
    assert body["error"] == "Invalid JSON body"


def test_lambda_handler_empty_body(mock_context):
    """Test that a completely missing body is handled gracefully."""
    event = {}

    response = lambda_handler(event, mock_context)

    # Current implementation falls back to "{}" which is valid JSON,
    # so it should succeed rather than crash.
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["status"] == "acknowledged"
