#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = [
#   "boto3>=1.42.5",
#   "botocore[crt]",
# ]
# ///
"""Register, inspect, replace, or delete the inbound Content Guru Storm webhook URL.

Resolves the target workspace's API Gateway webhook URL and Secrets Manager
names from Terraform outputs, fetches the Storm API credentials and inbound
webhook URL token from AWS Secrets Manager, combines them into
``<webhook_url>?token=<token>``, and manages the webhook subscription on the
Content Guru Storm Web Chat API.
"""

import argparse
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Protocol
import urllib.error
import urllib.parse
import urllib.request

import boto3

logger = logging.getLogger("register_storm_webhook")

STORM_WEBHOOK_PATH = "/api/v1/webhooks"
REPO_ROOT = Path(__file__).resolve().parents[2]


class StormWebhookError(RuntimeError):
    """Raised when Storm webhook configuration, validation, or API calls fail."""


class SecretsManagerClient(Protocol):
    def get_secret_value(self, *, SecretId: str) -> dict[str, Any]: ...


def _validate_https_url(raw_url: str, label: str) -> str:
    """Ensure a URL is well-formed, uses HTTPS, and contains a valid host."""
    cleaned = raw_url.strip()
    parsed = urllib.parse.urlsplit(cleaned)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise StormWebhookError(f"{label} must be a valid HTTPS URL (got '{raw_url}')")
    return cleaned


def _run_terraform(tf_dir: Path, *args: str, workspace: str | None = None) -> str:
    """Execute a Terraform CLI command and return stripped stdout without mutating workspace state."""
    env = os.environ.copy()
    if workspace:
        env["TF_WORKSPACE"] = workspace

    try:
        result = subprocess.run(
            ["terraform", f"-chdir={tf_dir}", *args],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise StormWebhookError(f"Terraform command failed: {detail}") from exc

    return result.stdout.strip()


def resolve_environment(
    explicit_env: str | None,
    tf_dir: Path = REPO_ROOT / "infrastructure" / "services",
) -> str:
    """Return explicit environment name or fall back to the active Terraform workspace."""
    if explicit_env and explicit_env.strip():
        return explicit_env.strip()

    workspace = _run_terraform(tf_dir, "workspace", "show")
    if not workspace or workspace == "default":
        raise StormWebhookError(
            "No --environment provided and active Terraform workspace is 'default'"
        )
    return workspace


def resolve_shared_secret_name(
    shared_tf_dir: Path = REPO_ROOT / "infrastructure" / "shared",
) -> str:
    """Resolve the shared Storm credentials secret name from Terraform outputs."""
    secret_name = _run_terraform(
        shared_tf_dir, "output", "-raw", "dvla_storm_credentials_secret_name"
    )
    if not secret_name:
        raise StormWebhookError(
            "Terraform output 'dvla_storm_credentials_secret_name' returned an empty string"
        )
    return secret_name


def resolve_service_outputs(
    environment: str,
    services_tf_dir: Path = REPO_ROOT / "infrastructure" / "services",
) -> dict[str, str]:
    """Resolve the workspace webhook URL and token secret name from Terraform outputs."""
    logger.info(
        "Resolving Terraform outputs from %s for workspace=%s",
        services_tf_dir,
        environment,
    )
    webhook_url = _run_terraform(
        services_tf_dir,
        "output",
        "-raw",
        "crm_contentguru_wh_url",
        workspace=environment,
    ).rstrip("/")
    token_secret_name = _run_terraform(
        services_tf_dir,
        "output",
        "-raw",
        "dvla_storm_inbound_webhook_token_secret_name",
        workspace=environment,
    )

    if not webhook_url:
        raise StormWebhookError(
            "Terraform output 'crm_contentguru_wh_url' returned an empty string"
        )
    if not token_secret_name:
        raise StormWebhookError(
            "Terraform output 'dvla_storm_inbound_webhook_token_secret_name' returned an empty string"
        )

    return {
        "webhook_url": _validate_https_url(
            webhook_url, "Terraform crm_contentguru_wh_url"
        ),
        "token_secret_name": token_secret_name,
    }


def get_secret_json(
    secrets_client: SecretsManagerClient, secret_id: str
) -> dict[str, Any]:
    """Fetch and parse a non-empty JSON object secret from AWS Secrets Manager."""
    response = secrets_client.get_secret_value(SecretId=secret_id)
    raw_secret = response.get("SecretString")
    if not raw_secret:
        raise ValueError(f"Secret '{secret_id}' does not contain a SecretString")

    try:
        parsed = json.loads(raw_secret)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Secret '{secret_id}' is not valid JSON") from exc

    if not isinstance(parsed, dict) or not parsed:
        raise ValueError(f"Secret '{secret_id}' must be a non-empty JSON object")

    return parsed


def extract_storm_credentials(secret_data: dict[str, Any]) -> dict[str, str]:
    """Validate and extract required Storm API credentials and HTTPS base URL."""
    creds: dict[str, str] = {}
    for key in ("api_key", "api_secret"):
        val = secret_data.get(key)
        if not isinstance(val, str) or not val.strip() or " " in val.strip():
            raise ValueError(
                f"Storm credentials secret has an invalid or empty '{key}' value"
            )
        creds[key] = val.strip()

    raw_base_url = secret_data.get("base_url")
    if not isinstance(raw_base_url, str) or not raw_base_url.strip():
        raise ValueError("Storm credentials secret is missing 'base_url'")

    creds["base_url"] = _validate_https_url(
        raw_base_url, "Storm credentials base_url"
    ).rstrip("/")
    return creds


def extract_webhook_token(token_registry: dict[str, Any]) -> str:
    """Extract the single active webhook URL token from the Secrets Manager registry."""
    valid_tokens = [
        token.strip()
        for token, owner in token_registry.items()
        if isinstance(token, str)
        and token.strip()
        and isinstance(owner, str)
        and owner.strip()
    ]
    if not valid_tokens:
        raise ValueError("No valid webhook URL token found in Secrets Manager registry")
    if len(valid_tokens) > 1:
        raise ValueError(
            f"Expected exactly 1 active webhook token in Secrets Manager registry, found {len(valid_tokens)}"
        )

    return valid_tokens[0]


def build_authenticated_webhook_uri(base_webhook_url: str, token: str) -> str:
    """Append or replace the ?token=<token> query parameter on an HTTPS webhook URL."""
    validated_url = _validate_https_url(base_webhook_url, "Webhook base URL")
    cleaned_token = token.strip()
    if not cleaned_token:
        raise ValueError("Webhook token must be a non-empty string")

    parsed = urllib.parse.urlsplit(validated_url)
    filtered_params = [
        (k, v)
        for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if k.lower() != "token"
    ]
    filtered_params.append(("token", cleaned_token))

    return urllib.parse.urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urllib.parse.urlencode(filtered_params),
            parsed.fragment,
        )
    )


def mask_webhook_uri(uri: str) -> str:
    """Fully redact any token query parameter value so zero secret characters are logged."""
    parsed = urllib.parse.urlsplit(uri)
    if not parsed.query:
        return uri

    masked_params = [
        (key, "***REDACTED***" if key.lower() == "token" else val)
        for key, val in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    ]
    return urllib.parse.urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urllib.parse.urlencode(masked_params, safe="*"),
            parsed.fragment,
        )
    )


def confirm_webhook_deletion(storm_base_url: str, action_description: str) -> bool:
    """Warn the operator and require explicit 'yes' confirmation before deleting a webhook."""
    target_url = f"{storm_base_url.rstrip('/')}{STORM_WEBHOOK_PATH}"
    logger.warning(
        "WARNING: About to %s by sending DELETE %s. "
        "Inbound events from Storm will stop immediately once deleted.",
        action_description,
        target_url,
    )
    try:
        answer = input("Type 'yes' to confirm deletion of the existing Storm webhook: ")
    except (EOFError, KeyboardInterrupt):
        print()
        return False

    return answer.strip().lower() == "yes"


def call_storm_webhook_api(
    storm_creds: dict[str, str],
    method: str,
    payload: dict[str, Any] | None = None,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    """Execute an authenticated GET, POST, or DELETE request against Storm's webhook endpoint."""
    base_url = _validate_https_url(storm_creds["base_url"], "Storm base URL").rstrip(
        "/"
    )
    target_url = f"{base_url}{STORM_WEBHOOK_PATH}"
    headers = {
        "Accept": "application/json",
        "apiKey": storm_creds["api_key"],
        "apiSecret": storm_creds["api_secret"],
        "User-Agent": "GovAccount1",
    }
    data: bytes | None = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        url=target_url,
        data=data,
        method=method,
        headers=headers,
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status_code = response.getcode()
            raw_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        if exc.code == 409:
            raise StormWebhookError(
                "Storm webhook subscription already exists (HTTP 409). "
                "Re-run with --replace to delete the existing subscription first."
            ) from exc
        if exc.code == 404 and method in ("GET", "DELETE"):
            return {"status_code": 404, "body": {}}
        raise StormWebhookError(
            f"Storm API {method} {target_url} failed with HTTP {exc.code}: {error_body}"
        ) from exc
    except urllib.error.URLError as exc:
        raise StormWebhookError(
            f"Failed to reach Storm API at {target_url}: {exc.reason}"
        ) from exc

    try:
        parsed_body: Any = json.loads(raw_body) if raw_body.strip() else {}
    except json.JSONDecodeError:
        parsed_body = {"raw": raw_body}

    return {"status_code": status_code, "body": parsed_body}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Register, inspect, replace, or delete the inbound Content Guru Storm webhook URL.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-e",
        "--environment",
        default=None,
        help="Target environment / Terraform workspace (defaults to active Terraform workspace)",
    )

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--get",
        action="store_true",
        help="Fetch and display the currently registered Storm webhook URL (masked)",
    )
    mode_group.add_argument(
        "--replace",
        action="store_true",
        help="Delete any existing Storm webhook subscription before registering",
    )
    mode_group.add_argument(
        "--delete",
        action="store_true",
        help="Delete the currently registered Storm webhook subscription without replacing it",
    )
    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve secrets and print target details without calling Storm",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    args = parse_args(argv)

    secrets_client = boto3.client("secretsmanager")
    shared_secret_name = resolve_shared_secret_name()
    storm_secret = get_secret_json(secrets_client, shared_secret_name)
    storm_creds = extract_storm_credentials(storm_secret)

    if args.get:
        current = call_storm_webhook_api(storm_creds, "GET")
        if current["status_code"] == 404:
            logger.info("No webhook is currently registered in Storm.")
            return 0
        body = current["body"]
        registered_url = body.get("url") if isinstance(body, dict) else None
        display = mask_webhook_uri(registered_url) if registered_url else body
        logger.info(
            "Current Storm webhook | status=%s | url=%s",
            current["status_code"],
            display,
        )
        return 0

    if args.delete:
        if not confirm_webhook_deletion(
            storm_creds["base_url"],
            "permanently delete the active Storm webhook subscription",
        ):
            logger.info("Deletion cancelled by user; no changes made.")
            return 1

        del_result = call_storm_webhook_api(storm_creds, "DELETE")
        if del_result["status_code"] == 404:
            logger.info("No webhook was registered in Storm (HTTP 404).")
        else:
            logger.info(
                "Storm webhook subscription deleted | status=%s",
                del_result["status_code"],
            )
        return 0

    environment = resolve_environment(args.environment)
    service_outputs = resolve_service_outputs(environment)
    token_registry = get_secret_json(
        secrets_client, service_outputs["token_secret_name"]
    )
    webhook_token = extract_webhook_token(token_registry)
    authenticated_uri = build_authenticated_webhook_uri(
        service_outputs["webhook_url"], webhook_token
    )

    logger.info(
        "Prepared Storm webhook subscription | environment=%s | target=%s%s | uri=%s",
        environment,
        storm_creds["base_url"],
        STORM_WEBHOOK_PATH,
        mask_webhook_uri(authenticated_uri),
    )

    if args.dry_run:
        logger.info("Dry run enabled; skipping Storm API request.")
        return 0

    if args.replace:
        if not confirm_webhook_deletion(
            storm_creds["base_url"],
            f"replace the existing Storm webhook subscription with environment '{environment}'",
        ):
            logger.info("Replacement cancelled by user; no changes made.")
            return 1

        del_result = call_storm_webhook_api(storm_creds, "DELETE")
        if del_result["status_code"] == 404:
            logger.info("No existing webhook found (HTTP 404); proceeding to register.")
        else:
            logger.info(
                "Existing subscription deleted | status=%s", del_result["status_code"]
            )

    result = call_storm_webhook_api(
        storm_creds,
        "POST",
        payload={
            "url": authenticated_uri,
            "eventTypes": ["Message", "InteractionStatus", "Typing"],
        },
    )
    logger.info(
        "Storm webhook registered successfully | status=%s | response=%s",
        result["status_code"],
        result["body"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
