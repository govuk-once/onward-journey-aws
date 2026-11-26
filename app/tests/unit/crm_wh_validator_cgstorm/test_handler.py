import base64
import hashlib
import hmac
import json
import os
from types import SimpleNamespace

import pytest

# Required module-level environment variables before importing the Lambda handler
os.environ.setdefault(
    "DVLA_STORM_SECRET_ARN",
    "arn:aws:secretsmanager:eu-west-2:123456789012:secret:dvla-storm",
)
os.environ.setdefault("ROUTER_FUNCTION_NAME", "crm-wh-router-test")
os.environ["SECRETS_ENDPOINT_URL"] = "https://secretsmanager.eu-west-2.amazonaws.com"

import lambdas.crm_wh_validator_cgstorm.handler as validator

SAMPLE_API_SECRET = "abcd1234efgh5678ijkl9012mnop3456"
ROTATED_API_SECRET = "zzzz9999yyyy8888xxxx7777wwww6666"
SAMPLE_REQUEST_ID = "req-abc-123"
SAMPLE_CHALLENGE = "challenge-token-xyz"


def _compute_valid_signature(raw_body: str, secret: str = SAMPLE_API_SECRET) -> str:
    digest = hmac.new(
        secret.encode("utf-8"),
        raw_body.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def _build_api_gateway_event(
    raw_body: str | None,
    *,
    signature: str | None = None,
    header_name: str = "WebhookSignature",
    headers: dict | None = ...,  # sentinel so passing headers=None is preserved
    is_base64_encoded: bool = False,
) -> dict:
    """Builds an API Gateway proxy event, auto-signing raw_body unless headers are overridden."""
    if headers is ...:
        if signature is None and isinstance(raw_body, str):
            signature = _compute_valid_signature(raw_body)
        headers = {header_name: signature} if signature is not None else {}

    wire_body = (
        base64.b64encode(raw_body.encode("utf-8")).decode("utf-8")
        if is_base64_encoded and isinstance(raw_body, str)
        else raw_body
    )
    return {
        "headers": headers,
        "body": wire_body,
        "isBase64Encoded": is_base64_encoded,
    }


class FakeSecretsManager:
    """Fake Secrets Manager client returning a configurable secret payload or raising an error."""

    def __init__(self, secret_payload: dict):
        self.secret_string: str = json.dumps(secret_payload)
        self.error: Exception | None = None
        self.call_count: int = 0
        self.requested_secret_ids: list[str] = []

    def get_secret_value(self, *, SecretId: str) -> dict:
        self.call_count += 1
        self.requested_secret_ids.append(SecretId)
        if self.error is not None:
            raise self.error
        return {"SecretString": self.secret_string}


class FakeRouterLambda:
    """Fake Lambda client recording async router invocations and returning a configurable status."""

    def __init__(self, status_code: int = 202):
        self.status_code: int = status_code
        self.error: Exception | None = None
        self.invocations: list[dict] = []

    def invoke(self, *, FunctionName: str, InvocationType: str, Payload: bytes) -> dict:
        self.invocations.append(
            {
                "function_name": FunctionName,
                "invocation_type": InvocationType,
                "payload": json.loads(Payload.decode("utf-8")),
            }
        )
        if self.error is not None:
            raise self.error
        return {"StatusCode": self.status_code}


@pytest.fixture
def lambda_context():
    return SimpleNamespace(aws_request_id=SAMPLE_REQUEST_ID)


@pytest.fixture
def mock_secrets_manager(mocker):
    fake_secrets = FakeSecretsManager({"api_secret": SAMPLE_API_SECRET})
    mocker.patch.object(validator, "secrets_client", fake_secrets)
    return fake_secrets


@pytest.fixture
def mock_router_lambda(mocker):
    fake_router = FakeRouterLambda(status_code=202)
    mocker.patch.object(validator, "lambda_client", fake_router)
    return fake_router


class TestInboundRequestBodyValidation:
    @pytest.mark.parametrize(
        "empty_event",
        [
            {"headers": {}, "body": None},
            {"headers": {}, "body": ""},
            {"headers": {}},
        ],
        ids=["none-body", "empty-string-body", "missing-body-key"],
    )
    def test_inbound_request_with_empty_or_missing_body_returns_400(
        self, lambda_context, mock_secrets_manager, mock_router_lambda, empty_event
    ):
        response = validator.lambda_handler(empty_event, lambda_context)

        assert response["statusCode"] == 400
        assert json.loads(response["body"]) == {
            "error": "Empty request body",
            "request_id": SAMPLE_REQUEST_ID,
        }
        assert mock_secrets_manager.call_count == 0
        assert mock_router_lambda.invocations == []

    @pytest.mark.parametrize(
        "invalid_base64_body",
        [
            "not-valid-base64!!!",
            base64.b64encode(b"\xff\xfe\xfd").decode("ascii"),
        ],
        ids=["corrupt-base64", "non-utf8-bytes"],
    )
    def test_inbound_request_with_invalid_base64_body_returns_400(
        self,
        lambda_context,
        mock_secrets_manager,
        mock_router_lambda,
        invalid_base64_body,
    ):
        event = {
            "headers": {},
            "body": invalid_base64_body,
            "isBase64Encoded": True,
        }

        response = validator.lambda_handler(event, lambda_context)

        assert response["statusCode"] == 400
        assert json.loads(response["body"])["error"] == "Invalid base64 body"
        assert mock_secrets_manager.call_count == 0
        assert mock_router_lambda.invocations == []

    def test_inbound_request_with_malformed_json_body_returns_400(
        self, lambda_context, mock_secrets_manager, mock_router_lambda
    ):
        event = _build_api_gateway_event("{not valid json")

        response = validator.lambda_handler(event, lambda_context)

        assert response["statusCode"] == 400
        assert json.loads(response["body"])["error"] == "Invalid JSON body"
        assert mock_secrets_manager.call_count == 0
        assert mock_router_lambda.invocations == []


class TestInboundSignatureAuthentication:
    @pytest.mark.parametrize(
        "headers",
        [
            None,
            {},
            {"WebhookSignature": None},
            {"WebhookSignature": ""},
            {"WebhookSignature": "   "},
        ],
        ids=[
            "none-headers",
            "empty-headers",
            "null-value",
            "empty-string",
            "whitespace",
        ],
    )
    def test_inbound_request_missing_signature_header_returns_401(
        self, lambda_context, mock_secrets_manager, mock_router_lambda, headers
    ):
        raw_body = json.dumps({"Event": {"Type": "Message"}})
        event = _build_api_gateway_event(raw_body, headers=headers)

        response = validator.lambda_handler(event, lambda_context)

        assert response["statusCode"] == 401
        assert (
            json.loads(response["body"])["error"] == "Missing WebhookSignature header"
        )
        assert mock_router_lambda.invocations == []

    @pytest.mark.parametrize(
        "bad_signature",
        [
            "not-a-valid-hmac",
            _compute_valid_signature(
                '{"Event": {"Type": "Message"}}', secret="wrong-secret"
            ),
            _compute_valid_signature(
                '{"Event": {"Type": "Tampered"}}', secret=SAMPLE_API_SECRET
            ),
        ],
        ids=["garbage-signature", "wrong-secret", "tampered-body"],
    )
    def test_inbound_request_with_invalid_signature_returns_401(
        self, lambda_context, mock_secrets_manager, mock_router_lambda, bad_signature
    ):
        raw_body = json.dumps({"Event": {"Type": "Message"}})
        event = _build_api_gateway_event(raw_body, signature=bad_signature)

        response = validator.lambda_handler(event, lambda_context)

        assert response["statusCode"] == 401
        assert json.loads(response["body"])["error"] == "Invalid webhook signature"
        assert mock_router_lambda.invocations == []

    @pytest.mark.parametrize(
        "header_name",
        ["WebhookSignature", "webhooksignature", "WEBHOOKSIGNATURE"],
    )
    def test_inbound_request_accepts_signature_header_case_insensitively_and_trims_whitespace(
        self, lambda_context, mock_secrets_manager, mock_router_lambda, header_name
    ):
        raw_body = json.dumps({"Event": {"Type": "Message"}})
        padded_signature = f"  {_compute_valid_signature(raw_body)}  "
        event = _build_api_gateway_event(
            raw_body, signature=padded_signature, header_name=header_name
        )

        response = validator.lambda_handler(event, lambda_context)

        assert response["statusCode"] == 200
        assert len(mock_router_lambda.invocations) == 1

    def test_inbound_request_validates_signature_against_decoded_base64_body(
        self, lambda_context, mock_secrets_manager, mock_router_lambda
    ):
        raw_body = '{  "Event": { "Type": "Message" }  }'
        event = _build_api_gateway_event(raw_body, is_base64_encoded=True)

        response = validator.lambda_handler(event, lambda_context)

        assert response["statusCode"] == 200
        assert mock_router_lambda.invocations[0]["payload"] == {"body": raw_body}

    def test_inbound_request_fetches_secret_fresh_so_rotation_takes_effect_immediately(
        self, lambda_context, mock_secrets_manager, mock_router_lambda
    ):
        raw_body = json.dumps({"Event": {"Type": "Message"}})
        old_secret_event = _build_api_gateway_event(
            raw_body, signature=_compute_valid_signature(raw_body, SAMPLE_API_SECRET)
        )
        new_secret_event = _build_api_gateway_event(
            raw_body, signature=_compute_valid_signature(raw_body, ROTATED_API_SECRET)
        )

        # First call succeeds with initial secret
        assert (
            validator.lambda_handler(old_secret_event, lambda_context)["statusCode"]
            == 200
        )

        # Rotate secret in Secrets Manager
        mock_secrets_manager.secret_string = json.dumps(
            {"api_secret": ROTATED_API_SECRET}
        )

        # Old secret is immediately rejected; new secret is immediately accepted
        assert (
            validator.lambda_handler(old_secret_event, lambda_context)["statusCode"]
            == 401
        )
        assert (
            validator.lambda_handler(new_secret_event, lambda_context)["statusCode"]
            == 200
        )
        assert mock_secrets_manager.call_count == 3


class TestWebhookRegistrationHandshake:
    @pytest.mark.parametrize("event_type", ["Verify", "WebhookVerification"])
    def test_webhook_handshake_verify_event_returns_challenge_hash_without_calling_router(
        self, lambda_context, mock_secrets_manager, mock_router_lambda, event_type
    ):
        raw_body = json.dumps(
            {"Event": {"Type": event_type, "Challenge": SAMPLE_CHALLENGE}}
        )
        event = _build_api_gateway_event(raw_body)

        response = validator.lambda_handler(event, lambda_context)

        expected_hash = hashlib.sha256(
            f"{SAMPLE_CHALLENGE}{SAMPLE_API_SECRET}".encode("utf-8")
        ).hexdigest()
        assert response["statusCode"] == 200
        assert json.loads(response["body"]) == {"Response": expected_hash}
        assert mock_router_lambda.invocations == []

    @pytest.mark.parametrize(
        "verify_payload",
        [
            {"Event": {"Type": "Verify", "Challenge": SAMPLE_CHALLENGE}},
            {"event": {"type": "Verify", "challenge": SAMPLE_CHALLENGE}},
            {"Event": {"Type": "Verify", "Body": {"Challenge": SAMPLE_CHALLENGE}}},
            {"event": {"type": "Verify", "body": {"challenge": SAMPLE_CHALLENGE}}},
        ],
        ids=[
            "Event.Challenge",
            "event.challenge",
            "Event.Body.Challenge",
            "event.body.challenge",
        ],
    )
    def test_registration_handshake_extracts_challenge_from_supported_payload_formats(
        self, lambda_context, mock_secrets_manager, mock_router_lambda, verify_payload
    ):
        event = _build_api_gateway_event(json.dumps(verify_payload))

        response = validator.lambda_handler(event, lambda_context)

        expected_hash = hashlib.sha256(
            f"{SAMPLE_CHALLENGE}{SAMPLE_API_SECRET}".encode("utf-8")
        ).hexdigest()
        assert response["statusCode"] == 200
        assert json.loads(response["body"]) == {"Response": expected_hash}

    @pytest.mark.parametrize(
        "malformed_verify_payload",
        [
            {"Event": {"Type": "Verify"}},
            {"Event": {"Type": "Verify", "Challenge": ""}},
            {"Event": {"Type": "Verify", "Challenge": 12345}},
        ],
        ids=["missing-challenge", "empty-challenge", "non-string-challenge"],
    )
    def test_registration_handshake_without_valid_challenge_returns_400(
        self,
        lambda_context,
        mock_secrets_manager,
        mock_router_lambda,
        malformed_verify_payload,
    ):
        event = _build_api_gateway_event(json.dumps(malformed_verify_payload))

        response = validator.lambda_handler(event, lambda_context)

        assert response["statusCode"] == 400
        assert "Challenge" in json.loads(response["body"])["error"]
        assert mock_router_lambda.invocations == []


class TestInboundChatEventForwarding:
    @pytest.mark.parametrize(
        "event_type", ["Message", "Status", "InteractionStatus", "Typing"]
    )
    @pytest.mark.parametrize(
        "raw_body_builder",
        [
            lambda event_type: f'{{ "Event": {{ "Type": "{event_type}",  "Text": "   here\'s some whitespace.  " }} }}',
            lambda event_type: f'{{"Event":{{"Type":"{event_type}","Text":"UPPERCASE TEXT"}}}}',
            lambda event_type: f'{{"Event": {{"Type": "{event_type}", "Text": "lowercase text"}}}}',
            lambda event_type: f'{{"Event": {{"Type": "{event_type}", "Text": "camelCaseText"}}}}',
        ],
        ids=[
            "extra-whitespace",
            "uppercase-text",
            "lowercase-text",
            "camelcase-text",
        ],
    )
    def test_inbound_chat_event_forwards_exact_unmodified_raw_body_to_router(
        self,
        lambda_context,
        mock_secrets_manager,
        mock_router_lambda,
        event_type,
        raw_body_builder,
    ):
        raw_body = raw_body_builder(event_type)
        event = _build_api_gateway_event(raw_body)

        response = validator.lambda_handler(event, lambda_context)

        assert response["statusCode"] == 200
        assert json.loads(response["body"]) == {
            "status": "accepted",
            "request_id": SAMPLE_REQUEST_ID,
        }
        assert mock_router_lambda.invocations == [
            {
                "function_name": validator.ROUTER_FUNCTION_NAME,
                "invocation_type": "Event",
                "payload": {"body": raw_body},
            }
        ]


class TestUnhandledInfrastructureFaults:
    @pytest.mark.parametrize(
        "malformed_event",
        [
            {"headers": {}, "body": {"already": "parsed"}},
            {
                "headers": ["not-a-dict"],
                "body": json.dumps({"Event": {"Type": "Message"}}),
            },
        ],
        ids=["non-string-body", "non-dict-headers"],
    )
    def test_invalid_api_gateway_event_shape_raises_unhandled_type_error(
        self, lambda_context, mock_secrets_manager, malformed_event
    ):
        with pytest.raises(TypeError):
            validator.lambda_handler(malformed_event, lambda_context)

    @pytest.mark.parametrize(
        "corrupt_secret_string",
        [
            json.dumps({}),
            json.dumps({"api_secret": ""}),
            json.dumps({"api_secret": 12345}),
            "not-json",
        ],
        ids=[
            "missing-api-secret",
            "empty-api-secret",
            "non-string-api-secret",
            "invalid-json",
        ],
    )
    def test_corrupt_secret_payload_raises_unhandled_error(
        self, lambda_context, mock_secrets_manager, corrupt_secret_string
    ):
        mock_secrets_manager.secret_string = corrupt_secret_string
        event = _build_api_gateway_event(json.dumps({"Event": {"Type": "Message"}}))

        with pytest.raises((KeyError, ValueError)):
            validator.lambda_handler(event, lambda_context)

    def test_secrets_manager_failure_raises_unhandled_error(
        self, lambda_context, mock_secrets_manager
    ):
        mock_secrets_manager.error = RuntimeError("Secrets Manager unavailable")
        event = _build_api_gateway_event(json.dumps({"Event": {"Type": "Message"}}))

        with pytest.raises(RuntimeError, match="Secrets Manager unavailable"):
            validator.lambda_handler(event, lambda_context)

    def test_router_lambda_invoke_failure_raises_unhandled_error(
        self, lambda_context, mock_secrets_manager, mock_router_lambda
    ):
        mock_router_lambda.error = RuntimeError("Lambda invoke throttled")
        event = _build_api_gateway_event(json.dumps({"Event": {"Type": "Message"}}))

        with pytest.raises(RuntimeError, match="Lambda invoke throttled"):
            validator.lambda_handler(event, lambda_context)

    def test_unexpected_router_invoke_status_code_raises_unhandled_error(
        self, lambda_context, mock_secrets_manager, mock_router_lambda
    ):
        mock_router_lambda.status_code = 500
        event = _build_api_gateway_event(json.dumps({"Event": {"Type": "Message"}}))

        with pytest.raises(RuntimeError, match="500"):
            validator.lambda_handler(event, lambda_context)
