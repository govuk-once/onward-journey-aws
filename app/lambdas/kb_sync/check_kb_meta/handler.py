"""
KB Sync: Check KB Metadata Lambda.
"""

import os
import json
import requests
import time
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

from utils.aws import get_secrets_client
from utils.config import CRM_CONFIG_MAP

# Initialise AWS Clients
secrets_client = get_secrets_client()
ENV_PREFIX = os.environ.get("ENV_PREFIX")

# --- BASE KB INTEGRATION INTERFACE ---
class KbIntegration(ABC):
    def __init__(self, kb_id: str, config: Dict[str, Any]):
        self.kb_id = kb_id
        self.config = config
        self.creds = self._load_dynamic_secrets()
        self._token = None
        self._token_expiry = 0

    def _load_dynamic_secrets(self) -> Dict[str, Any]:
        """Retrieve the raw secret data from AWS Secrets Manager."""
        full_secret_name = f"{ENV_PREFIX}/{self.config['secret_path']}"
        print(f"Fetching {self.config['platform'].capitalize()} Secrets for KB: {self.kb_id}")
        response = secrets_client.get_secret_value(SecretId=full_secret_name)
        return json.loads(response["SecretString"])

    @abstractmethod
    def fetch_remote_modified_date(self) -> Optional[str]:
        """Fetch the 'last modified' timestamp from the remote platform."""
        pass

# --- GENESYS IMPLEMENTATION ---
class GenesysKbIntegration(KbIntegration):
    def _refresh_oauth_token(self) -> str:
        """Authenticate with Genesys Cloud and cache the bearer token."""
        if self._token and time.time() < (self._token_expiry - 60):
            return self._token

        auth_url = f"https://login.{self.config['api_region']}/oauth/token"
        resp = requests.post(
            auth_url,
            data={"grant_type": "client_credentials"},
            auth=(self.creds['client_id'], self.creds['client_secret']),
            timeout=10
        )
        resp.raise_for_status()

        data = resp.json()
        self._token = data["access_token"]
        self._token_expiry = time.time() + data["expires_in"]
        return self._token

    def fetch_remote_modified_date(self) -> Optional[str]:
        token = self._refresh_oauth_token()
        headers = {"Authorization": f"Bearer {token}"}
        kb_uuid = self.creds.get("kb_id")
        url = f"https://api.{self.config['api_region']}/api/v2/knowledge/knowledgebases/{kb_uuid}"

        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        return resp.json().get("dateModified")

# --- FACTORY ---
def get_kb_integration(kb_id: str, config: Dict[str, Any]) -> KbIntegration:
    platform = config.get("platform")
    if platform == "genesys":
        return GenesysKbIntegration(kb_id, config)
    # Future platforms (e.g., Salesforce, ServiceNow) can be added here
    raise ValueError(f"Unsupported platform: {platform}")

def lambda_handler(event, context):
    print(f"Received event: {json.dumps(event)}")
    kb_id = event.get("kb_identifier")
    config = CRM_CONFIG_MAP.get(kb_id)

    if not config:
        raise Exception(f"Configuration not found for KB: {kb_id}")

    try:
        integration = get_kb_integration(kb_id, config)
        remote_date = integration.fetch_remote_modified_date()

        print(f"KB {kb_id}: Remote modified date is {remote_date}")

        return {
            "kb_identifier": kb_id,
            "remote_modified_date": remote_date,
            "platform": config["platform"]
        }
    except Exception as e:
        print(f"❌ [ERROR] KB Metadata Check Failure: {str(e)}")
        raise
