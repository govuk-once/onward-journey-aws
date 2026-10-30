import json
from unittest.mock import MagicMock
import pytest
from botocore.exceptions import ClientError

from app.lambdas.api_gw_authorizer_token.handler import (
    _resolve_endpoint_url,
    lambda_handler,
)

TEST_TOKEN = "test-token-uuid-1234"
TEST_ACTOR_ID = "tester-001"
TEST_METHOD_ARN = "arn:aws:execute-api:eu-west-2:123456789012:api-id/dev/$connect"


@pytest.fixture(autouse=True)
def clear_handler_cache_before_each_test():
    """Reset the in-memory SECRET_CACHE and TTL timestamp before every test to ensure test isolation."""
    import app.lambdas.api_gw_authorizer_token.handler as handler_module

    handler_module.SECRET_CACHE = None
    handler_module.CACHE_TIMESTAMP = 0.0


@pytest.fixture
def mock_env_vars(monkeypatch):
    """Provide default environment variables for Lambda execution."""
    monkeypatch.setenv(
        "SECRET_ARN", "arn:aws:secretsmanager:eu-west-2:123:secret:ws-tokens"
    )
    monkeypatch.setenv("CACHE_TTL_SECONDS", "300")


@pytest.fixture
def mock_registry(mocker, mock_env_vars):
    """Mock get_token_registry to supply pre-configured token mappings."""
    registry = {
        TEST_TOKEN: TEST_ACTOR_ID,
        "second-token-abcd": "tester-002",
    }
    return mocker.patch(
        "app.lambdas.api_gw_authorizer_token.handler.get_token_registry",
        return_value=registry,
    )


@pytest.fixture
def build_event():
    """Factory fixture to generate standardised API Gateway WebSocket authoriser events."""

    def _builder(query_params=None, method_arn=TEST_METHOD_ARN):
        event = {
            "queryStringParameters": query_params if query_params is not None else {},
        }
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

    def test_returns_iam_allow_with_actor_id_for_valid_token(
        self, mock_registry, build_event
    ):
        event = build_event(query_params={"auth_token": TEST_TOKEN})
        response = lambda_handler(event, None)

        assert response["policyDocument"]["Statement"][0]["Effect"] == "Allow"
        assert response["context"] == {"actor_id": TEST_ACTOR_ID}

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
        assert response["context"]["actor_id"] == TEST_ACTOR_ID

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


class TestAuthoriserCachingAndResilience:
    def test_queries_secrets_manager_once_on_cold_start_and_reuses_cache(
        self, mocker, mock_env_vars, build_event
    ):
        mock_secrets_client = mocker.patch(
            "app.lambdas.api_gw_authorizer_token.handler.SECRETS_MANAGER_CLIENT.get_secret_value"
        )
        mock_secrets_client.return_value = {
            "SecretString": json.dumps({TEST_TOKEN: TEST_ACTOR_ID})
        }

        event = build_event(query_params={"auth_token": TEST_TOKEN})

        first_resp = lambda_handler(event, None)
        assert first_resp["policyDocument"]["Statement"][0]["Effect"] == "Allow"
        assert mock_secrets_client.call_count == 1

        second_resp = lambda_handler(event, None)
        assert second_resp["policyDocument"]["Statement"][0]["Effect"] == "Allow"
        assert mock_secrets_client.call_count == 1

    def test_invalidates_cache_after_ttl_expires(
        self, mocker, mock_env_vars, build_event
    ):
        mock_secrets_client = mocker.patch(
            "app.lambdas.api_gw_authorizer_token.handler.SECRETS_MANAGER_CLIENT.get_secret_value"
        )
        mock_secrets_client.side_effect = [
            {"SecretString": json.dumps({TEST_TOKEN: TEST_ACTOR_ID})},
            {"SecretString": json.dumps({"rotated-token-5678": "new-actor"})},
        ]

        mock_time = mocker.patch(
            "app.lambdas.api_gw_authorizer_token.handler.time.time"
        )
        mock_time.return_value = 1000.0

        # First call populates cache
        resp1 = lambda_handler(
            build_event(query_params={"auth_token": TEST_TOKEN}), None
        )
        assert resp1["policyDocument"]["Statement"][0]["Effect"] == "Allow"
        assert mock_secrets_client.call_count == 1

        # Second call within TTL (100s later) reuses cached token
        mock_time.return_value = 1100.0
        resp2 = lambda_handler(
            build_event(query_params={"auth_token": TEST_TOKEN}), None
        )
        assert resp2["policyDocument"]["Statement"][0]["Effect"] == "Allow"
        assert mock_secrets_client.call_count == 1

        # Third call exceeds TTL (400s later) refreshes and uses rotated secret
        mock_time.return_value = 1400.0
        resp3 = lambda_handler(
            build_event(query_params={"auth_token": "rotated-token-5678"}), None
        )
        assert resp3["policyDocument"]["Statement"][0]["Effect"] == "Allow"
        assert resp3["principalId"] == "new-actor"
        assert mock_secrets_client.call_count == 2

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

    def test_raises_error_when_secrets_manager_raises_client_error(
        self, mocker, mock_env_vars, build_event
    ):
        mock_secrets_client = mocker.patch(
            "app.lambdas.api_gw_authorizer_token.handler.SECRETS_MANAGER_CLIENT.get_secret_value"
        )
        mock_secrets_client.side_effect = ClientError(
            {
                "Error": {
                    "Code": "ResourceNotFoundException",
                    "Message": "Secret not found",
                }
            },
            "GetSecretValue",
        )

        event = build_event(query_params={"auth_token": TEST_TOKEN})

        with pytest.raises(ClientError):
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
