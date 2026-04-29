"""
KB Sync: Check Metadata Lambda.
Fetches the last modified date from the CRM and compares it with RDS to decide if sync is needed.
"""

import os
import json
import requests
from typing import Dict, Any
from utils.aws import get_secrets_client
from utils.db import get_db_connection
from utils.config import CRM_CONFIG_MAP

secrets_client = get_secrets_client()
ENV_PREFIX = os.environ.get("ENV_PREFIX")

def _get_genesys_token(config, creds) -> str:
    auth_url = f"https://login.{config['api_region']}/oauth/token"
    resp = requests.post(
        auth_url,
        data={"grant_type": "client_credentials"},
        auth=(creds['client_id'], creds['client_secret']),
        timeout=10
    )
    resp.raise_for_status()
    return resp.json()["access_token"]

def lambda_handler(event, context):
    kb_id = event.get("kb_identifier")
    config = CRM_CONFIG_MAP.get(kb_id)

    if not config:
        raise Exception(f"Configuration not found for KB: {kb_id}")

    # 1. Load Secrets
    full_secret_name = f"{ENV_PREFIX}/{config['secret_path']}"
    secret_resp = secrets_client.get_secret_value(SecretId=full_secret_name)
    creds = json.loads(secret_resp["SecretString"])

    # 2. Fetch Remote Metadata
    remote_date = None
    if config["platform"] == "genesys":
        token = _get_genesys_token(config, creds)
        headers = {"Authorization": f"Bearer {token}"}
        kb_uuid = creds.get("kb_id")
        url = f"https://api.{config['api_region']}/api/v2/knowledge/knowledgebases/{kb_uuid}"
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        remote_date = resp.json().get("dateModified")

    # 3. Check Local RDS
    conn = get_db_connection()
    try:
        # Ensure metadata table exists
        conn.run("CREATE TABLE IF NOT EXISTS sync_kb_metadata (kb_identifier TEXT PRIMARY KEY, last_modified TEXT);")

        local_meta = conn.run("SELECT last_modified FROM sync_kb_metadata WHERE kb_identifier = :id", id=kb_id)
        local_date = local_meta[0][0] if local_meta else None
    finally:
        conn.close()

    # 4. Decision
    # If remote_date is None (empty KB), we might still want to sync if local is not None
    sync_required = (remote_date != local_date) or (remote_date is None and local_date is not None)

    print(f"KB {kb_id}: Remote({remote_date}) vs Local({local_date}) -> Sync Required: {sync_required}")

    return {
        "sync_required": sync_required,
        "remote_modified_date": remote_date
    }
