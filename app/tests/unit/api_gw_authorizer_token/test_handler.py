import hmac
import json
import pytest
from botocore.exceptions import ClientError

from app.lambdas.api_gw_authorizer_token.handler import (
    _require_endpoint_url,
    _resolve_endpoint_url,
    lambda_handler,
)

TEST_TOKEN = "test-token-uuid-1234"
TEST_PRINCIPAL_ID = "tester-001"
TEST_METHOD_ARN = "arn:aws:execute-api:eu-west-2:123456789012:api-id/dev/$connect"


@pytest.fixture(autouse=True)
def mock_env_vars(monkeypatch):
    """Provide default environment variables for Lambda execution."""
    monkeypatch.setenv(
        "SECRET_ARN", "arn:aws:secretsmanager:eu-west-2:123:secret:ws-tokens"
    )
    monkeypatch.setenv("TOKEN_QUERY_PARAM", "auth_token")
    monkeypatch.setenv("CONTEXT_KEY", "actor_id")


@pytest.fixture
def mock_registry(mocker, mock_env_vars):
    """Mock get_token_registry to supply pre-configured token mappings."""
    registry = {
        TEST_TOKEN: TEST_PRINCIPAL_ID,
        "second-token-abcd": "tester-002",
    }
    return mocker.patch(
        "app.lambdas.api_gw_authorizer_token.handler.get_token_registry",
        return_value=registry,
    )


@pytest.fixture
def build_event():
    """Factory fixture to generate standardised API Gateway authoriser events."""

    def _builder(
        query_params=None,
        multi_value_query_params=None,
        method_arn=TEST_METHOD_ARN,
    ):
        event = {
            "queryStringParameters": query_params if query_params is not None else {},
        }
        if multi_value_query_params is not None:
            event["multiValueQueryStringParameters"] = multi_value_query_params
        if method_arn is not None:
            event["methodArn"] = method_arn
        return event

    return _builder


class TestEndpointUrlResolution:
    """Tests verifying scheme normalisation for botocore endpoint compatibility."""

    @pytest.mark.parametrize(
        "raw_url, expected",
        [
            ("secrets.mock.aws", "https://secrets.mock.aws"),
            ("  secrets.mock.aws  ", "https://secrets.mock.aws"),
            ("https://custom.endpoint.aws", "https://custom.endpoint.aws"),
            ("http://localhost:4566", "http://localhost:4566"),
            (None, None),
            ("", None),
            ("   ", None),
        ],
    )
    def test_resolve_endpoint_url_normalises_schemes(self, raw_url, expected):
        assert _resolve_endpoint_url(raw_url) == expected

    def test_require_endpoint_url_returns_normalised_url_when_present(
        self, monkeypatch
    ):
        monkeypatch.setenv("TEST_ENDPOINT", "custom.secrets.aws")
        assert _require_endpoint_url("TEST_ENDPOINT") == "https://custom.secrets.aws"

    @pytest.mark.parametrize("empty_val", [None, "", "   "])
    def test_require_endpoint_url_raises_runtime_error_when_missing_or_blank(
        self, monkeypatch, empty_val
    ):
        if empty_val is None:
            monkeypatch.delenv("TEST_ENDPOINT", raising=False)
        else:
            monkeypatch.setenv("TEST_ENDPOINT", empty_val)

        with pytest.raises(
            RuntimeError,
            match="Missing required environment variable: TEST_ENDPOINT",
        ):
            _require_endpoint_url("TEST_ENDPOINT")


class TestQueryParamValidation:
    def test_returns_iam_deny_when_query_params_missing(self, build_event):
        event = build_event(query_params=None)
        response = lambda_handler(event, None)

        assert response["policyDocument"]["Statement"][0]["Effect"] == "Deny"
        assert response["policyDocument"]["Statement"][0]["Resource"] == TEST_METHOD_ARN
        assert response["principalId"] == "unauthorized"
        assert "context" not in response

    def test_returns_iam_deny_when_auth_token_param_is_missing(self, build_event):
        event = build_event(query_params={"other_param": "value"})
        response = lambda_handler(event, None)

        assert response["policyDocument"]["Statement"][0]["Effect"] == "Deny"
        assert response["policyDocument"]["Statement"][0]["Resource"] == TEST_METHOD_ARN

    @pytest.mark.parametrize("blank_token", ["", "   ", "\t\n"])
    def test_returns_iam_deny_when_auth_token_is_blank_or_whitespace(
        self, build_event, blank_token
    ):
        event = build_event(query_params={"auth_token": blank_token})
        response = lambda_handler(event, None)

        assert response["policyDocument"]["Statement"][0]["Effect"] == "Deny"

    def test_returns_iam_deny_when_token_is_not_in_registry(
        self, mock_registry, build_event
    ):
        event = build_event(query_params={"auth_token": "unknown-token-value"})
        response = lambda_handler(event, None)

        assert response["policyDocument"]["Statement"][0]["Effect"] == "Deny"

    def test_returns_iam_deny_when_duplicate_token_params_supplied(
        self, mock_registry, build_event
    ):
        event = build_event(
            query_params={"auth_token": TEST_TOKEN},
            multi_value_query_params={"auth_token": [TEST_TOKEN, "second-token"]},
        )
        response = lambda_handler(event, None)

        assert response["policyDocument"]["Statement"][0]["Effect"] == "Deny"

    def test_returns_iam_allow_with_context_when_context_key_configured(
        self, mock_registry, build_event
    ):
        event = build_event(query_params={"auth_token": TEST_TOKEN})
        response = lambda_handler(event, None)

        assert response["policyDocument"]["Statement"][0]["Effect"] == "Allow"
        assert response["principalId"] == TEST_PRINCIPAL_ID
        assert response["context"] == {"actor_id": TEST_PRINCIPAL_ID}

    @pytest.mark.parametrize(
        "key, token_val",
        [
            ("AUTH_TOKEN", TEST_TOKEN),
            ("Auth_Token", TEST_TOKEN),
            ("auth_token", f"  {TEST_TOKEN}  "),
        ],
    )
    def test_handles_casing_and_whitespace_normalisation(
        self, mock_registry, build_event, key, token_val
    ):
        event = build_event(query_params={key: token_val})
        response = lambda_handler(event, None)

        assert response["policyDocument"]["Statement"][0]["Effect"] == "Allow"
        assert response["context"]["actor_id"] == TEST_PRINCIPAL_ID

    def test_supports_custom_token_query_param_without_context_for_webhook_gateway(
        self, monkeypatch, mocker, build_event
    ):
        monkeypatch.setenv("TOKEN_QUERY_PARAM", "token")
        monkeypatch.delenv("CONTEXT_KEY", raising=False)
        mocker.patch(
            "app.lambdas.api_gw_authorizer_token.handler.get_token_registry",
            return_value={TEST_TOKEN: "dvla-storm"},
        )

        event = build_event(query_params={"token": TEST_TOKEN})
        response = lambda_handler(event, None)

        assert response["policyDocument"]["Statement"][0]["Effect"] == "Allow"
        assert response["principalId"] == "dvla-storm"
        assert "context" not in response

    @pytest.mark.parametrize("empty_val", [None, "", "   "])
    def test_raises_error_when_token_query_param_environment_variable_missing(
        self, build_event, monkeypatch, empty_val
    ):
        if empty_val is None:
            monkeypatch.delenv("TOKEN_QUERY_PARAM", raising=False)
        else:
            monkeypatch.setenv("TOKEN_QUERY_PARAM", empty_val)

        event = build_event(query_params={"auth_token": TEST_TOKEN})

        with pytest.raises(
            RuntimeError,
            match="Missing required environment variable: TOKEN_QUERY_PARAM",
        ):
            lambda_handler(event, None)

    @pytest.mark.parametrize("missing_arn", [None, ""])
    def test_missing_or_blank_method_arn_raises_value_error(
        self, mock_registry, build_event, missing_arn
    ):
        event = build_event(
            query_params={"auth_token": TEST_TOKEN}, method_arn=missing_arn
        )
        with pytest.raises(
            ValueError, match="Missing required 'methodArn' in authoriser event"
        ):
            lambda_handler(event, None)


class TestAuthoriserSecretRetrievalAndResilience:
    def test_retrieves_and_parses_valid_token_registry_from_secrets_manager(
        self, mocker, mock_env_vars, build_event
    ):
        mock_secrets_client = mocker.patch(
            "app.lambdas.api_gw_authorizer_token.handler.SECRETS_MANAGER_CLIENT.get_secret_value",
            return_value={"SecretString": json.dumps({TEST_TOKEN: TEST_PRINCIPAL_ID})},
        )

        response = lambda_handler(
            build_event(query_params={"auth_token": TEST_TOKEN}), None
        )

        assert response["policyDocument"]["Statement"][0]["Effect"] == "Allow"
        assert response["principalId"] == TEST_PRINCIPAL_ID
        mock_secrets_client.assert_called_once_with(
            SecretId="arn:aws:secretsmanager:eu-west-2:123:secret:ws-tokens"
        )

    def test_raises_error_on_corrupt_secret_json(
        self, mocker, mock_env_vars, build_event
    ):
        mock_secrets_client = mocker.patch(
            "app.lambdas.api_gw_authorizer_token.handler.SECRETS_MANAGER_CLIENT.get_secret_value"
        )
        mock_secrets_client.return_value = {"SecretString": "{ not_valid_json: "}

        event = build_event(query_params={"auth_token": TEST_TOKEN})

        with pytest.raises(ValueError, match="Token registry secret is not valid JSON"):
            lambda_handler(event, None)

    @pytest.mark.parametrize(
        "invalid_secret",
        [
            "{}",
            '["not", "a", "dict"]',
            json.dumps({TEST_TOKEN: "   "}),
            json.dumps({TEST_TOKEN: None}),
        ],
    )
    def test_raises_error_on_invalid_secret_schema_or_blank_principal_id(
        self, mocker, mock_env_vars, build_event, invalid_secret
    ):
        mock_secrets_client = mocker.patch(
            "app.lambdas.api_gw_authorizer_token.handler.SECRETS_MANAGER_CLIENT.get_secret_value"
        )
        mock_secrets_client.return_value = {"SecretString": invalid_secret}

        event = build_event(query_params={"auth_token": TEST_TOKEN})

        with pytest.raises(ValueError, match="Token registry secret"):
            lambda_handler(event, None)

    def test_raises_error_when_secret_arn_environment_variable_missing(
        self, build_event, monkeypatch
    ):
        monkeypatch.delenv("SECRET_ARN", raising=False)

        event = build_event(query_params={"auth_token": TEST_TOKEN})

        with pytest.raises(
            RuntimeError, match="Missing required environment variable: SECRET_ARN"
        ):
            lambda_handler(event, None)
