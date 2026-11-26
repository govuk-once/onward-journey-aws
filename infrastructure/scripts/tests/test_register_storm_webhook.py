import io
import json
import logging
import subprocess
from types import SimpleNamespace
import urllib.error

import pytest

import infrastructure.scripts.register_storm_webhook as webhook_cli
from infrastructure.scripts.register_storm_webhook import (
    StormWebhookError,
    build_authenticated_webhook_uri,
    call_storm_webhook_api,
    extract_storm_credentials,
    extract_webhook_token,
    get_secret_json,
    main,
    mask_webhook_uri,
    resolve_environment,
    resolve_service_outputs,
)

STORM_CREDENTIALS = {
    "api_key": "01234567-89ab-cdef-0123-456789abcdef",
    "api_secret": "abcd1234efgh5678ijkl9012mnop3456",
    "base_url": "https://storm.example.com",
}
STORM_WEBHOOK_ENDPOINT = "https://storm.example.com/api/v1/webhooks"
BASE_WEBHOOK_URL = "https://api.example.com/ab1/contentguru/webhook"
SHARED_SECRET_NAME = "shared/crm-creds/dvla-storm-dev"
TOKEN_SECRET_NAME = "ab1/crm-creds/dvla-storm-inbound-webhook-url-token"
SAMPLE_TOKEN = "uuid-token-9999"
EXPECTED_EVENT_TYPES = ["Message", "InteractionStatus", "Typing"]
EXIT_SUCCESS = 0


def _mock_http_response(mocker, status_code: int = 200, body: bytes = b"{}"):
    response = mocker.MagicMock()
    response.getcode.return_value = status_code
    response.read.return_value = body
    response.__enter__.return_value = response
    return response


def _mock_http_error(
    status_code: int, body: bytes = b"Error"
) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url=STORM_WEBHOOK_ENDPOINT,
        code=status_code,
        msg="HTTP Error",
        hdrs=None,
        fp=io.BytesIO(body),
    )


@pytest.fixture
def mock_terraform(mocker):
    """Fake Terraform CLI workspace state, outputs, and subprocess runner."""
    terraform_state = SimpleNamespace(
        active_workspace="ab1",
        outputs={
            "dvla_storm_credentials_secret_name": SHARED_SECRET_NAME,
            "crm_contentguru_wh_url": f"{BASE_WEBHOOK_URL}/",
            "dvla_storm_inbound_webhook_token_secret_name": TOKEN_SECRET_NAME,
        },
        runner=None,
    )

    def fake_terraform_run(command, **kwargs):
        subcommand = command[2:]
        if subcommand == ["workspace", "show"]:
            stdout = terraform_state.active_workspace
        elif subcommand[:2] == ["output", "-raw"]:
            stdout = terraform_state.outputs.get(subcommand[2], "")
        else:
            raise AssertionError(f"Unexpected terraform command: {command}")

        return subprocess.CompletedProcess(
            args=command, returncode=0, stdout=f"{stdout}\n", stderr=""
        )

    terraform_state.runner = mocker.patch.object(
        webhook_cli.subprocess, "run", side_effect=fake_terraform_run
    )
    return terraform_state


@pytest.fixture
def mock_secrets_manager(mocker):
    """Fake AWS Secrets Manager store keyed by SecretId."""
    secrets = {
        SHARED_SECRET_NAME: json.dumps(STORM_CREDENTIALS),
        TOKEN_SECRET_NAME: json.dumps({SAMPLE_TOKEN: "dvla-storm"}),
    }

    def fake_get_secret_value(*, SecretId: str):
        if SecretId not in secrets:
            raise KeyError(f"Unexpected SecretId: {SecretId}")
        return {"SecretString": secrets[SecretId]}

    secrets_client = mocker.Mock(
        get_secret_value=mocker.Mock(side_effect=fake_get_secret_value)
    )
    mocker.patch.object(webhook_cli.boto3, "client", return_value=secrets_client)
    return secrets


class FakeStormApi:
    """Fake Storm HTTP API that records outbound requests and serves pre-configured responses in order."""

    def __init__(self, default_response):
        self.requests: list[dict] = []
        self.responses: list = [default_response]

    def __call__(self, request, timeout=30):
        self.requests.append(
            {
                "url": request.full_url,
                "method": request.get_method(),
                "headers": {
                    key.lower(): value for key, value in request.header_items()
                },
                "body": (
                    json.loads(request.data.decode("utf-8")) if request.data else None
                ),
            }
        )
        if not self.responses:
            raise AssertionError(
                f"Unexpected extra HTTP request: {request.get_method()} {request.full_url}"
            )
        outcome = self.responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def mock_storm_api(mocker):
    fake_api = FakeStormApi(_mock_http_response(mocker, 200, b'{"ok": true}'))
    mocker.patch.object(webhook_cli.urllib.request, "urlopen", side_effect=fake_api)
    return fake_api


class TestTerraformBoundary:
    def test_explicit_cli_environment_overrides_active_terraform_workspace(
        self, mock_terraform
    ):
        mock_terraform.active_workspace = "bc2"

        target_environment = resolve_environment(explicit_env="ab1")

        assert target_environment == "ab1"
        mock_terraform.runner.assert_not_called()

    @pytest.mark.parametrize("invalid_workspace", ["default", ""])
    def test_omitted_environment_rejects_default_or_empty_workspace(
        self, mock_terraform, invalid_workspace
    ):
        mock_terraform.active_workspace = invalid_workspace
        with pytest.raises(StormWebhookError, match="default"):
            resolve_environment(None)

    @pytest.mark.parametrize(
        "empty_output_name",
        [
            "crm_contentguru_wh_url",
            "dvla_storm_inbound_webhook_token_secret_name",
        ],
    )
    def test_raises_error_when_required_service_terraform_output_is_empty(
        self, mock_terraform, empty_output_name
    ):
        mock_terraform.outputs[empty_output_name] = ""
        with pytest.raises(StormWebhookError, match=empty_output_name):
            resolve_service_outputs("ab1")

    def test_raises_error_including_stderr_when_terraform_command_fails(self, mocker):
        mocker.patch.object(
            webhook_cli.subprocess,
            "run",
            side_effect=subprocess.CalledProcessError(
                returncode=1,
                cmd=["terraform"],
                stderr="Backend initialization required",
            ),
        )
        with pytest.raises(StormWebhookError, match="Backend initialization required"):
            resolve_service_outputs("ab1")


class TestValidationAndMasking:
    @pytest.mark.parametrize(
        "secret_response",
        [
            {},
            {"SecretString": ""},
            {"SecretString": "not-json"},
            {"SecretString": "{}"},
            {"SecretString": "[]"},
        ],
    )
    def test_rejects_missing_malformed_or_non_object_secrets(
        self, mocker, secret_response
    ):
        secrets_client = mocker.Mock(
            get_secret_value=mocker.Mock(return_value=secret_response)
        )
        with pytest.raises(ValueError, match="my-secret"):
            get_secret_json(secrets_client, "my-secret")

    def test_normalizes_whitespace_and_trailing_slashes_in_credentials(self):
        padded_credentials = {
            "api_key": f"  {STORM_CREDENTIALS['api_key']}  ",
            "api_secret": f"  {STORM_CREDENTIALS['api_secret']}  ",
            "base_url": f"{STORM_CREDENTIALS['base_url']}/",
        }
        assert extract_storm_credentials(padded_credentials) == STORM_CREDENTIALS

    @pytest.mark.parametrize("field_name", ["api_key", "api_secret"])
    @pytest.mark.parametrize(
        "invalid_value",
        [None, "", "   ", "REPLACE ME"],
        ids=["missing", "empty", "whitespace-only", "internal-whitespace"],
    )
    def test_rejects_empty_or_whitespace_containing_api_credentials(
        self, field_name, invalid_value
    ):
        with pytest.raises(ValueError, match=field_name):
            extract_storm_credentials({**STORM_CREDENTIALS, field_name: invalid_value})

    @pytest.mark.parametrize(
        ("token_registry", "expected_error_pattern"),
        [
            ({}, "No valid webhook URL token"),
            (
                {"": "dvla-storm", "inactive-token": "   "},
                "No valid webhook URL token",
            ),
            (
                {"token-1": "owner-1", "token-2": "owner-2"},
                "Expected exactly 1 active webhook token",
            ),
        ],
    )
    def test_requires_single_active_token_in_registry(
        self, token_registry, expected_error_pattern
    ):
        with pytest.raises(ValueError, match=expected_error_pattern):
            extract_webhook_token(token_registry)

    @pytest.mark.parametrize(
        "insecure_or_malformed_url",
        [
            "not-a-url",
            "http://api.example.com/webhook",
            "https:///missing-host",
            "file:///etc/passwd",
        ],
    )
    def test_rejects_non_https_or_malformed_webhook_urls(
        self, insecure_or_malformed_url
    ):
        with pytest.raises(StormWebhookError, match="HTTPS"):
            build_authenticated_webhook_uri(insecure_or_malformed_url, SAMPLE_TOKEN)

    def test_replaces_existing_token_and_redacts_it_for_logging(self):
        authenticated_uri = build_authenticated_webhook_uri(
            f"{BASE_WEBHOOK_URL}?Token=stale&env=ab1#frag",
            SAMPLE_TOKEN,
        )
        assert (
            authenticated_uri == f"{BASE_WEBHOOK_URL}?env=ab1&token={SAMPLE_TOKEN}#frag"
        )

        masked_uri = mask_webhook_uri(authenticated_uri)
        assert masked_uri == f"{BASE_WEBHOOK_URL}?env=ab1&token=***REDACTED***#frag"
        assert SAMPLE_TOKEN not in masked_uri


class TestStormHttpEdgeCases:
    def test_returns_raw_body_when_success_response_is_not_json(
        self, mocker, mock_storm_api
    ):
        mock_storm_api.responses = [
            _mock_http_response(mocker, 200, b"Webhook registered")
        ]
        result = call_storm_webhook_api(
            STORM_CREDENTIALS, "POST", payload={"url": BASE_WEBHOOK_URL}
        )
        assert result == {"status_code": 200, "body": {"raw": "Webhook registered"}}

    @pytest.mark.parametrize(
        ("failure_factory", "expected_match"),
        [
            (lambda: _mock_http_error(409, b"Conflict"), "--replace"),
            (lambda: _mock_http_error(502, b"Bad Gateway"), "HTTP 502: Bad Gateway"),
            (
                lambda: urllib.error.URLError("Connection refused"),
                "Connection refused",
            ),
        ],
        ids=["409-conflict", "502-bad-gateway", "network-error"],
    )
    def test_raises_storm_webhook_error_on_http_and_network_failures(
        self, mock_storm_api, failure_factory, expected_match
    ):
        mock_storm_api.responses = [failure_factory()]
        with pytest.raises(StormWebhookError, match=expected_match):
            call_storm_webhook_api(
                STORM_CREDENTIALS, "POST", payload={"url": BASE_WEBHOOK_URL}
            )

    def test_redacts_tokens_in_http_error_bodies(self, mock_storm_api):
        echoed_error = (
            f"Bad Request: invalid url {BASE_WEBHOOK_URL}?token={SAMPLE_TOKEN}".encode()
        )
        mock_storm_api.responses = [_mock_http_error(400, echoed_error)]

        with pytest.raises(StormWebhookError) as exc_info:
            call_storm_webhook_api(
                STORM_CREDENTIALS,
                "POST",
                payload={"url": f"{BASE_WEBHOOK_URL}?token={SAMPLE_TOKEN}"},
            )

        assert "***REDACTED***" in str(exc_info.value)
        assert SAMPLE_TOKEN not in str(exc_info.value)


class TestCliWorkflows:
    def test_rejects_mutually_exclusive_flags(self):
        with pytest.raises(SystemExit):
            main(["--delete", "--replace"])

    def test_registers_webhook_end_to_end_using_active_workspace(
        self, mock_terraform, mock_secrets_manager, mock_storm_api, caplog
    ):
        caplog.set_level(logging.INFO)

        assert main([]) == 0

        service_calls = [
            call
            for call in mock_terraform.runner.call_args_list
            if "crm_contentguru_wh_url" in call.args[0]
        ]
        assert service_calls[0].kwargs["env"]["TF_WORKSPACE"] == "ab1"

        assert len(mock_storm_api.requests) == 1
        captured_request = mock_storm_api.requests[0]
        assert captured_request["method"] == "POST"
        assert captured_request["url"] == STORM_WEBHOOK_ENDPOINT
        assert captured_request["headers"]["apikey"] == STORM_CREDENTIALS["api_key"]
        assert (
            captured_request["headers"]["apisecret"] == STORM_CREDENTIALS["api_secret"]
        )
        assert captured_request["body"] == {
            "url": f"{BASE_WEBHOOK_URL}?token={SAMPLE_TOKEN}",
            "eventTypes": EXPECTED_EVENT_TYPES,
        }
        assert SAMPLE_TOKEN not in caplog.text

    def test_dry_run_validates_config_and_redacts_logs_without_http_calls(
        self, mock_terraform, mock_secrets_manager, mock_storm_api, caplog
    ):
        caplog.set_level(logging.INFO)

        assert main(["-e", "ab1", "--dry-run"]) == 0

        assert mock_storm_api.requests == []
        assert "***REDACTED***" in caplog.text
        assert SAMPLE_TOKEN not in caplog.text

    def test_get_mode_redacts_token_and_skips_service_terraform_outputs(
        self, mocker, mock_terraform, mock_secrets_manager, mock_storm_api, caplog
    ):
        caplog.set_level(logging.INFO)
        mock_storm_api.responses = [
            _mock_http_response(
                mocker,
                200,
                json.dumps(
                    {"url": f"{BASE_WEBHOOK_URL}?token={SAMPLE_TOKEN}"}
                ).encode(),
            )
        ]

        assert main(["--get"]) == 0

        assert [request["method"] for request in mock_storm_api.requests] == ["GET"]
        assert "***REDACTED***" in caplog.text
        assert SAMPLE_TOKEN not in caplog.text
        assert mock_terraform.runner.call_count == 1

    @pytest.mark.parametrize(
        "raw_response_bytes",
        [
            json.dumps(
                {"webhook": {"url": f"{BASE_WEBHOOK_URL}?token={SAMPLE_TOKEN}"}}
            ).encode(),
            f"Registered {BASE_WEBHOOK_URL}?token={SAMPLE_TOKEN}".encode(),
        ],
        ids=["nested-json-schema", "non-json-text-body"],
    )
    def test_get_mode_redacts_tokens_even_if_storm_response_schema_changes(
        self,
        mocker,
        mock_terraform,
        mock_secrets_manager,
        mock_storm_api,
        caplog,
        raw_response_bytes,
    ):
        caplog.set_level(logging.INFO)
        mock_storm_api.responses = [
            _mock_http_response(mocker, 200, raw_response_bytes)
        ]

        assert main(["--get"]) == 0
        assert "***REDACTED***" in caplog.text
        assert SAMPLE_TOKEN not in caplog.text

    def test_get_mode_logs_message_when_no_webhook_is_registered(
        self, mock_terraform, mock_secrets_manager, mock_storm_api, caplog
    ):
        caplog.set_level(logging.INFO)
        mock_storm_api.responses = [_mock_http_error(404)]

        assert main(["--get"]) == 0
        assert "No webhook is currently registered" in caplog.text

    @pytest.mark.parametrize(
        "operator_input", ["no", "y", "", EOFError, KeyboardInterrupt]
    )
    @pytest.mark.parametrize(
        "cli_arguments", [["--delete"], ["-e", "ab1", "--replace"]]
    )
    def test_destructive_modes_abort_without_http_calls_unless_explicit_yes(
        self,
        mocker,
        mock_terraform,
        mock_secrets_manager,
        mock_storm_api,
        operator_input,
        cli_arguments,
    ):
        if isinstance(operator_input, type) and issubclass(
            operator_input, BaseException
        ):
            mocker.patch("builtins.input", side_effect=operator_input)
        else:
            mocker.patch("builtins.input", return_value=operator_input)

        assert main(cli_arguments) == 1
        assert mock_storm_api.requests == []

    @pytest.mark.parametrize("delete_outcome_status", [200, 404])
    def test_delete_mode_sends_delete_request_and_exits_cleanly_on_200_or_404(
        self,
        mocker,
        mock_terraform,
        mock_secrets_manager,
        mock_storm_api,
        caplog,
        delete_outcome_status,
    ):
        caplog.set_level(logging.INFO)
        mocker.patch("builtins.input", return_value="  YES  ")
        mock_storm_api.responses = [
            (
                _mock_http_response(mocker, 200)
                if delete_outcome_status == 200
                else _mock_http_error(404)
            )
        ]

        exit_code = main(["--delete"])

        assert exit_code == EXIT_SUCCESS
        assert [request["method"] for request in mock_storm_api.requests] == ["DELETE"]
        assert f"by sending DELETE {STORM_WEBHOOK_ENDPOINT}." in caplog.text
        assert (
            "Inbound events from Storm will stop immediately once deleted."
            in caplog.text
        )
        if delete_outcome_status == 200:
            assert "Storm webhook subscription deleted | status=200" in caplog.text
        else:
            assert "No webhook was registered in Storm (HTTP 404)." in caplog.text

    @pytest.mark.parametrize("prior_webhook_exists", [True, False])
    def test_replace_mode_deletes_then_registers_webhook_even_if_prior_webhook_was_missing(
        self,
        mocker,
        mock_terraform,
        mock_secrets_manager,
        mock_storm_api,
        caplog,
        prior_webhook_exists,
    ):
        caplog.set_level(logging.INFO)
        mocker.patch("builtins.input", return_value="yes")
        delete_step = (
            _mock_http_response(mocker, 200)
            if prior_webhook_exists
            else _mock_http_error(404)
        )
        mock_storm_api.responses = [
            delete_step,
            _mock_http_response(mocker, 200, b'{"ok": true}'),
        ]

        assert main(["-e", "ab1", "--replace"]) == EXIT_SUCCESS
        assert [request["method"] for request in mock_storm_api.requests] == [
            "DELETE",
            "POST",
        ]
        assert mock_storm_api.requests[1]["body"] == {
            "url": f"{BASE_WEBHOOK_URL}?token={SAMPLE_TOKEN}",
            "eventTypes": EXPECTED_EVENT_TYPES,
        }
        assert f"by sending DELETE {STORM_WEBHOOK_ENDPOINT}." in caplog.text
        assert (
            "Inbound events from Storm will stop immediately once deleted."
            in caplog.text
        )
        if prior_webhook_exists:
            assert "Existing subscription deleted | status=200" in caplog.text
        else:
            assert (
                "No existing webhook found (HTTP 404); proceeding to register."
                in caplog.text
            )
