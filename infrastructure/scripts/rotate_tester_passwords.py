#!/usr/bin/env python3
"""Rotate HTTP Basic Auth passwords for targeted AWS Amplify tester branches."""

import argparse
import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError

SERVICES_DIR = Path(__file__).resolve().parent.parent / "services"
TARGET_PATTERN = re.compile(r"tester-([1-9][0-9]*)")
DEFAULT_REGION = "eu-west-2"


def parse_targets(raw_targets: list[str]) -> list[str]:
    """Validate and deduplicate target branch names (e.g. 'tester-1' -> '1')."""
    indices: list[str] = []
    for target in raw_targets:
        match = TARGET_PATTERN.fullmatch(str(target))
        if not match:
            raise ValueError(
                f'Invalid target "{target}". Expected format: "tester-1".'
            )
        indices.append(match.group(1))
    return list(dict.fromkeys(indices))


def load_targets_from_file(path: Path) -> list[str]:
    """Read a JSON array of target branch names from a file."""
    if not path.is_file():
        raise FileNotFoundError(f"Targets file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a JSON array.")
    return parse_targets(data)


def check_for_drift(tf_dir: Path) -> None:
    """Abort if there are unrelated pending Terraform changes in the workspace."""
    print("Pre-flight: Checking for unrelated Terraform drift...")
    result = subprocess.run(
        ["terraform", f"-chdir={tf_dir}", "plan", "-detailed-exitcode", "-input=false"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return
    if result.returncode == 2:
        raise RuntimeError(
            "Unrelated pending Terraform changes detected. "
            "Run 'terraform plan' in infrastructure/services/ and resolve drift "
            "before rotating passwords."
        )
    raise RuntimeError(
        f"Pre-flight terraform plan failed (exit {result.returncode}):\n{result.stderr}"
    )


def rotate_in_terraform(tf_dir: Path, indices: list[str]) -> None:
    """Run a single terraform apply -replace for the targeted password resources."""
    replace_args = [
        f'-replace=random_password.tester_passwords["{idx}"]' for idx in indices
    ]
    print("Step 1/2: Rotating passwords in Terraform state and Secrets Manager...")
    subprocess.run(
        ["terraform", f"-chdir={tf_dir}", "apply", *replace_args],
        check=True,
    )


def get_terraform_output(tf_dir: Path, name: str) -> str:
    """Fetch a raw Terraform output value."""
    result = subprocess.run(
        ["terraform", f"-chdir={tf_dir}", "output", "-raw", name],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def list_all_branches(amplify_client, app_id: str) -> list[str]:
    """Retrieve all Amplify branch names using pagination."""
    paginator = amplify_client.get_paginator("list_branches")
    branches: list[str] = []
    for page in paginator.paginate(appId=app_id):
        branches.extend(b["branchName"] for b in page.get("branches", []))
    return branches


def find_branch_name(branches: list[str], idx: str) -> str:
    """Match a unique Amplify branch name for the given tester index."""
    prefix = f"tester-{idx}-"
    matches = [name for name in branches if name.startswith(prefix)]
    if not matches:
        raise LookupError(f"Could not find Amplify branch for tester-{idx}")
    if len(matches) > 1:
        raise LookupError(
            f"Multiple Amplify branches matched tester-{idx}: {matches}"
        )
    return matches[0]


def sync_branch_credentials(
    amplify_client,
    secrets_client,
    app_id: str,
    secret_id: str,
    indices: list[str],
) -> None:
    """Push updated credentials from Secrets Manager to targeted Amplify branches."""
    print("Step 2/2: Syncing new credentials to targeted AWS Amplify branches...")
    secret_value = secrets_client.get_secret_value(SecretId=secret_id)["SecretString"]
    credentials_map = json.loads(secret_value)
    all_branches = list_all_branches(amplify_client, app_id)

    # Validate all branches and secrets upfront before mutating any branch
    pending_updates: list[tuple[str, str, str]] = []
    for idx in indices:
        branch_name = find_branch_name(all_branches, idx)
        entry = credentials_map.get(f"Tester {idx}")
        if not entry:
            raise LookupError(f"Could not find 'Tester {idx}' in Secrets Manager")

        raw_creds = f"{entry['username']}:{entry['password']}".encode("utf-8")
        creds_b64 = base64.b64encode(raw_creds).decode("utf-8")
        pending_updates.append((idx, branch_name, creds_b64))

    for idx, branch_name, creds_b64 in pending_updates:
        amplify_client.update_branch(
            appId=app_id,
            branchName=branch_name,
            enableBasicAuth=True,
            basicAuthCredentials=creds_b64,
        )
        print(f"  Updated Basic Auth for {branch_name} (Tester {idx})")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rotate Basic Auth passwords for specific Amplify tester branches."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "targets",
        nargs="*",
        default=[],
        help='Target branches to rotate (e.g. "tester-1" "tester-4").',
    )
    group.add_argument(
        "--targets-file",
        type=Path,
        help="Path to a JSON file containing an array of target branch names.",
    )
    parser.add_argument(
        "--sync-only",
        action="store_true",
        help="Skip Terraform rotation and only sync existing Secrets Manager credentials to Amplify.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.targets_file:
            indices = load_targets_from_file(args.targets_file)
        else:
            indices = parse_targets(args.targets)

        if not indices:
            print("No targets specified. Nothing to rotate.")
            return 0

        app_id = get_terraform_output(SERVICES_DIR, "amplify_app_id")
        secret_id = get_terraform_output(SERVICES_DIR, "tester_credentials_secret_id")

        region = (
            os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION")
            or DEFAULT_REGION
        )
        amplify_client = boto3.client("amplify", region_name=region)
        secrets_client = boto3.client("secretsmanager", region_name=region)

        if args.sync_only:
            targets_summary = ", ".join(f"tester-{idx}" for idx in indices)
            print(f"--sync-only specified: skipping Terraform rotation for {targets_summary}.")
        else:
            # Fail fast on bad targets before rotating in Terraform
            all_branches = list_all_branches(amplify_client, app_id)
            for idx in indices:
                find_branch_name(all_branches, idx)

            check_for_drift(SERVICES_DIR)
            targets_summary = ", ".join(f"tester-{idx}" for idx in indices)
            print(f"Targets to rotate: {targets_summary}")
            rotate_in_terraform(SERVICES_DIR, indices)

        sync_branch_credentials(
            amplify_client=amplify_client,
            secrets_client=secrets_client,
            app_id=app_id,
            secret_id=secret_id,
            indices=indices,
        )
    except (
        ValueError,
        FileNotFoundError,
        LookupError,
        RuntimeError,
        BotoCoreError,
        ClientError,
    ) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(
            f"Error: Terraform command failed (exit {exc.returncode}).",
            file=sys.stderr,
        )
        return exc.returncode

    print("Password rotation complete. Secrets Manager and Amplify are in sync.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
