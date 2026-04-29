"""
KB Sync: Fetch Articles Lambda.
Dedicated to pulling Knowledge Base articles from CRM platforms.
"""

import os
import json
import requests
from typing import Dict, Any, List
from utils.aws import get_secrets_client
from utils.genesys_parser import parse_genesys_blocks
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

    articles = []

    # 2. Fetch Articles
    if config["platform"] == "genesys":
        token = _get_genesys_token(config, creds)
        headers = {"Authorization": f"Bearer {token}"}
        region = config['api_region']
        kb_uuid = creds.get("kb_id")

        url = f"https://api.{region}/api/v2/knowledge/knowledgebases/{kb_uuid}/documents"

        while url:
            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            for doc in data.get("entities", []):
                var_url = f"https://api.{region}/api/v2/knowledge/knowledgebases/{kb_uuid}/documents/{doc['id']}/variations"
                var_resp = requests.get(var_url, headers=headers, timeout=10)
                var_data = var_resp.json()

                if var_data.get("entities"):
                    raw_text = parse_genesys_blocks(var_data["entities"][0].get("body", {}).get("blocks", []))
                    articles.append({
                        "title": doc["title"],
                        "content": raw_text,
                        "kb_identifier": kb_id,
                        "external_id": doc["id"],
                        "external_url": f"https://genesys.cloud/kb/{kb_uuid}/article/{doc['id']}"
                    })

            url = data.get("nextUri")
            if url: url = f"https://api.{region}{url}"

    print(f"KB {kb_id}: Fetched {len(articles)} articles.")
    return {
        "articles": articles
    }
