import base64
import hashlib
import hmac
import pytest
from app.lambdas.api_gw_authorizer_signature.handler import lambda_handler

TEST_SECRET = "Mock-secret-key-123"
DEFAULT_SAMPLE_BODY = '{"event": "chat_message", "text": "Hello World"}'
DEFAULT_METHOD_ARN = (
    "arn:aws:execute-api:eu-west-2:12345:api/stage/POST/content-guru/webhook"
)


def _calculate_hmac(secret: str, body: str) -> str:
    """Helper to compute expected HMAC-SHA256 signature."""
    return hmac.new(
        secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256
    ).hexdigest()


@pytest.fixture(autouse=True)
def clear_handler_cache_before_each_test():
    """Reset the in-memory SECRET_CACHE before every test to ensure test isolation."""
    import app.lambdas.api_gw_authorizer_signature.handler as handler_module

    handler_module.SECRET_CACHE = None


@pytest.fixture
def mock_env_vars(monkeypatch):
    """Provide default environment variables for Lambda execution."""
    monkeypatch.setenv("SECRET_ARN", "arn:aws:secretsmanager:eu-west-2:123:secret:test")
    monkeypatch.setenv("PRINCIPAL_ID", "contentguru-webhook")


@pytest.fixture
def mock_signing_secret(mocker, mock_env_vars):
    """Mock get_signing_secret to bypass AWS Secrets Manager calls."""
    return mocker.patch(
        "app.lambdas.api_gw_authorizer_signature.handler.get_signing_secret",
        return_value=TEST_SECRET,
    )


@pytest.fixture
def build_event():
    """Factory fixture to generate standardised API Gateway authorizer event dicts."""

    def _builder(
        headers=None,
        body=DEFAULT_SAMPLE_BODY,
        method_arn=DEFAULT_METHOD_ARN,
        is_base64_encoded=False,
    ):
        event = {
            "headers": headers if headers is not None else {},
            "body": body,
            "methodArn": method_arn,
        }
        if is_base64_encoded:
            event["isBase64Encoded"] = True
        return event

    return _builder


class TestHeaderValidation:
    """Tests evaluating header verification prior to payload processing."""

    def test_returns_iam_deny_policy_when_signature_header_is_missing(
        self, build_event
    ):
        event = build_event(headers={})
        response = lambda_handler(event, None)

        assert response["policyDocument"]["Statement"][0]["Effect"] == "Deny"


class TestRawPayloadEvaluation:
    """Tests evaluating signature verification and IAM policy generation over unencoded string bodies."""

    def test_returns_iam_allow_policy_and_principal_for_valid_hmac(
        self, mock_signing_secret, build_event
    ):
        valid_sig = _calculate_hmac(TEST_SECRET, DEFAULT_SAMPLE_BODY)
        event = build_event(headers={"x-storm-signature": valid_sig})

        response = lambda_handler(event, None)

        assert response["policyDocument"]["Statement"][0]["Effect"] == "Allow"
        assert response["principalId"] == "contentguru-webhook"

    def test_returns_iam_deny_policy_for_invalid_hmac_hash(
        self, mock_signing_secret, build_event
    ):
        event = build_event(headers={"x-storm-signature": "tampered-hash-value"})

        response = lambda_handler(event, None)

        assert response["policyDocument"]["Statement"][0]["Effect"] == "Deny"


class TestBase64PayloadEvaluation:
    """Tests evaluating signature verification and IAM policy generation over base64-encoded request bodies."""

    def test_returns_iam_allow_policy_and_principal_for_valid_hmac(
        self, mock_signing_secret, build_event
    ):
        raw_body = DEFAULT_SAMPLE_BODY
        valid_sig = _calculate_hmac(TEST_SECRET, raw_body)
        base64_body = base64.b64encode(raw_body.encode("utf-8")).decode("utf-8")

        event = build_event(
            headers={"x-storm-signature": valid_sig},
            body=base64_body,
            is_base64_encoded=True,
        )

        response = lambda_handler(event, None)

        assert response["policyDocument"]["Statement"][0]["Effect"] == "Allow"
        assert response["principalId"] == "contentguru-webhook"

    def test_returns_iam_deny_policy_for_invalid_hmac_hash(
        self, mock_signing_secret, build_event
    ):
        raw_body = DEFAULT_SAMPLE_BODY
        base64_body = base64.b64encode(raw_body.encode("utf-8")).decode("utf-8")

        event = build_event(
            headers={"x-storm-signature": "tampered-hash-value"},
            body=base64_body,
            is_base64_encoded=True,
        )

        response = lambda_handler(event, None)

        assert response["policyDocument"]["Statement"][0]["Effect"] == "Deny"


class TestSignatureAuthorizerSecretCaching:
    """Tests verifying in-memory caching behaviour across cold and warm Lambda executions."""

    def test_queries_secrets_manager_once_on_cold_start_and_reuses_cached_secret(
        self, mocker, mock_env_vars, build_event
    ):
        mock_secrets_client = mocker.patch(
            "app.lambdas.api_gw_authorizer_signature.handler.SECRETS_MANAGER_CLIENT.get_secret_value"
        )
        mock_secrets_client.return_value = {"SecretString": TEST_SECRET}

        valid_sig = _calculate_hmac(TEST_SECRET, DEFAULT_SAMPLE_BODY)
        event = build_event(headers={"X-Storm-Signature": valid_sig})

        # First Invocation: Cold Start (fetches secret from Secrets Manager)
        lambda_handler(event, None)
        assert mock_secrets_client.call_count == 1

        # Second Invocation: Warm Execution (reuses in-memory cached secret)
        lambda_handler(event, None)
        assert (
            mock_secrets_client.call_count == 1
        )  # Verifies no additional API call was made
