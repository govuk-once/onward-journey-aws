"""
CRM Tool Lambda (MCP Server).
- fetch_adviser_availability: Queries if human staff are online to help.
- generate_handoff_signal: Prepares the switchboard data for the
  Frontend to transition the user from AI to a Human Adviser.
"""

import os
import json
import time
import uuid
import boto3
import requests
from abc import ABC, abstractmethod
from typing import Dict, Any, List

from utils.aws import get_secrets_client
from utils.genesys_parser import parse_genesys_blocks

# --- CONFIGURATION MAPPING ---
# This acts as the 'glue' between RDS metadata and Genesys specific routing.
# TODO: Future enhancement: These IDs could be moved to an RDS table.
sandbox_queue_id = "7c1702bc-8f49-4cd6-96d4-51b6542b26f5"
sandbox_deploy_id = "a548193a-6a74-474d-8e2d-f0adb0f291b1"

CRM_CONFIG_MAP = {
    "gate-hmp-track-001": {
        "platform": "genesys",
        "secret_path": "crm-creds/home-office-genesys",
        "api_region": "euw2.pure.cloud",
        "queue_id": sandbox_queue_id,
        "deploy_id": sandbox_deploy_id
    },
    "gate-ho-visas-005": {
        "platform": "genesys",
        "secret_path": "crm-creds/home-office-genesys",
        "api_region": "euw2.pure.cloud",
        "queue_id": sandbox_queue_id,
        "deploy_id": sandbox_deploy_id
    },
    "gate-dvla-renew-003": {
        "platform": "genesys",
        "secret_path": "crm-creds/dvla-genesys",
        "api_region": "euw2.pure.cloud",
        "queue_id": sandbox_queue_id,
        "deploy_id": sandbox_deploy_id
    }
}

# Initialise AWS Clients
secrets_client = get_secrets_client()
ENV_PREFIX = os.environ.get("ENV_PREFIX")

# --- BASE CRM INTEGRATION INTERFACE ---
class CrmIntegration(ABC):
    def __init__(self, chat_id: str, config: Dict[str, Any]):
        self.chat_id = chat_id
        self.config = config
        self.creds = self._load_dynamic_secrets()
        self._token = None
        self._token_expiry = 0

    def _load_dynamic_secrets(self) -> Dict[str, Any]:
        """Retrieve and cache the raw secret data from AWS Secrets Manager."""
        full_secret_name = f"{ENV_PREFIX}/{self.config['secret_path']}"
        print(f"Fetching {self.config['platform'].capitalize()} Secrets from AWS Secrets Manager")
        response = secrets_client.get_secret_value(SecretId=full_secret_name)
        return json.loads(response["SecretString"])

    @abstractmethod
    def fetch_adviser_availability(self) -> str: pass

    @abstractmethod
    def generate_handoff_signal(self, event: Dict[str, Any]) -> Dict[str, Any]: pass

    @abstractmethod
    def fetch_kb_metadata(self) -> Dict[str, Any]: pass

    @abstractmethod
    def fetch_kb_articles(self) -> List[Dict[str, Any]]: pass


# --- GENESYS IMPLEMENTATION ---
class GenesysIntegration(CrmIntegration):
    def _refresh_oauth_token(self) -> str:
        """Authenticate with Genesys Cloud and cache the bearer token."""
        if self._token and time.time() < (self._token_expiry - 60):
            return self._token

        print(f"🔐 Refreshing Genesys OAuth Token for {self.chat_id}...")
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

    def fetch_adviser_availability(self) -> str:
        """
        Performs a three-stage check to verify if a human adviser is available in Genesys.
        """
        token = self._refresh_oauth_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        region = self.config['api_region']
        q_id = self.config['queue_id']

        # --- PHASE 1: MEMBERSHIP CHECK ---
        # We query the Routing API to see if the department is staffed.
        # If 'joinedMemberCount' is 0, the queue is functionally 'Offline'.
        conf_url = f"https://api.{region}/api/v2/routing/queues/{q_id}"
        conf = requests.get(conf_url, headers=headers, timeout=5).json()
        if conf.get("joinedMemberCount", 0) == 0:
            print(f"QUEUE OFFLINE: {q_id} has no joined human advisers.")
            return "Live chat is currently unavailable."

        # --- PHASE 2: CHANNEL & PRESENCE CHECK ---
        # We filter for specific Routing Statuses (Qualifiers) to ensure the service is active.
        # We strictly only count IDLE (waiting) and INTERACTING (working) statuses.
        query_url = f"https://api.{region}/api/v2/analytics/queues/observations/query"
        query_payload = {
            "filter": {"type": "and", "predicates": [
                {"dimension": "queueId", "value": q_id},
                {"dimension": "mediaType", "value": "message"}
            ]},
            "metrics": ["oOnQueueUsers"]
        }
        obs_resp = requests.post(query_url, headers=headers, json=query_payload, timeout=5)
        print(f"DEBUG: Raw Analytics Response: {obs_resp.text}")

        obs_results = obs_resp.json().get("results", [{}])
        on_queue = 0

        # Genesys only returns 'qualifiers' (statuses) that have a count > 0.
        # We sum those who are IDLE (waiting), INTERACTING (busy), or COMMUNICATING (in session).
        if obs_results and "data" in obs_results[0]:
            for entry in obs_results[0]["data"]:
                if entry.get("metric") == "oOnQueueUsers" and entry.get("qualifier") in ["IDLE", "INTERACTING"]:
                    on_queue += entry.get("stats", {}).get("count", 0)

        print(f"DEBUG: Eligible human adviser count: {on_queue}")
        if on_queue == 0:
            return "Live chat is currently unavailable."

        # --- PHASE 3: WAIT TIME CHECK ---
        # Provides the current estimated wait time (EWT) for the queue.
        ewt_url = f"https://api.{region}/api/v2/routing/queues/{q_id}/estimatedwaittime"
        ewt_resp = requests.get(ewt_url, headers=headers, timeout=5).json()
        results = ewt_resp.get("results", [{}])
        wait_seconds = results[0].get("estimatedWaitTimeSeconds", 0) if results else 0
        wait_str = f"{wait_seconds // 60}m" if wait_seconds > 0 else "under 1 minute"

        return f"Live chat is AVAILABLE. Estimated wait: {wait_str}."

    def generate_handoff_signal(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """
        Prepares the connection parameters for the Genesys Web Messenger WebSocket.
        Returns a SIGNAL payload consumed by the Svelte frontend.
        """
        region = self.config['api_region']
        deploy_id = self.config['deploy_id']

        # --- PHASE 1: PREPARE SESSION PARAMETERS ---
        # Web Messaging identifies sessions using a persistent 'token' (UUID).
        # We use the thread_id to ensure the CRM session is linked to our AI thread.
        # NOTE: Ensure the Orchestrator tool passes 'thread_id' in the arguments.
        token = event.get("thread_id", str(uuid.uuid4()))
        actor_id = event.get("actor_id", "ANONYMOUS")

        print(f"🔗 [DEBUG] Preparing Web Messaging Signal | Region: {region} | DeployID: {deploy_id}")
        print(f"🔗 [DEBUG] Session Context | Token (Thread): {token} | Actor: {actor_id}")

        # --- PHASE 2: CONSTRUCT WEBSOCKET ADDRESS ---
        # The frontend connects directly to this wss:// endpoint to start the chat.
        websocket_url = f"wss://webmessaging.{region}/v1?deploymentId={deploy_id}"

        # --- PHASE 3: PACKAGE CONTEXT FOR FRONTEND ---
        # We bundle the context (Summary/Reason) so the Svelte frontend can send
        # it as 'customAttributes' during the WebSocket 'configureSession' step.
        handoff_config = {
            "action": "initiate_live_handoff",
            "websocketUrl": websocket_url,
            "token": token,
            "deploymentId": deploy_id,
            "region": region,
            "summary": event.get("summary", "No summary provided."),
            "reason": event.get("reason", "Standard AI Handoff"),
            "actor_id": actor_id
        }

        # Validation: Verify we have the absolute minimum required to connect
        if not deploy_id or not region:
            print(f"❌ [ERROR] Missing critical configuration! DeployID: {bool(deploy_id)}, Region: {bool(region)}")
            return {
                "content": [{
                    "type": "text",
                    "text": "SERVICE_ERROR: CRM configuration is incomplete."
                }]
            }

        print(f"✅ [DEBUG] Web Messaging Signal generated successfully for Token: {token[:8]}...")

        return {
            "content": [{
                "type": "text",
                "text": f"SIGNAL: initiate_live_handoff {json.dumps(handoff_config)}"
            }]
        }

    def fetch_kb_metadata(self) -> Dict[str, Any]:
        """Fetches the latest version/modification info for the Knowledge Base."""
        token = self._refresh_oauth_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        region = self.config['api_region']

        kb_id = self.creds.get("kb_id")
        if not kb_id:
            raise Exception("KB_ID missing from CRM secrets.")

        url = f"https://api.{region}/api/v2/knowledge/knowledgebases/{kb_id}"
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def fetch_kb_articles(self) -> List[Dict[str, Any]]:
        """Fetches and flattens all active articles from the Knowledge Base."""
        token = self._refresh_oauth_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        region = self.config['api_region']

        kb_id = self.creds.get("kb_id")
        if not kb_id:
            raise Exception("KB_ID missing from CRM secrets.")

        # 1. Get all document metadata
        url = f"https://api.{region}/api/v2/knowledge/knowledgebases/{kb_id}/documents"
        all_content = []

        while url:
            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            for doc in data.get("entities", []):
                # 2. Get variations for actual text
                var_url = f"https://api.{region}/api/v2/knowledge/knowledgebases/{kb_id}/documents/{doc['id']}/variations"
                var_resp = requests.get(var_url, headers=headers, timeout=10)
                var_data = var_resp.json()

                if var_data.get("entities"):
                    raw_text = parse_genesys_blocks(var_data["entities"][0].get("body", {}).get("blocks", []))
                    all_content.append({
                        "title": doc["title"],
                        "content": raw_text,
                        "kb_identifier": self.chat_id,
                        "external_id": doc["id"],
                        "external_url": f"https://genesys.cloud/kb/{kb_id}/article/{doc['id']}"
                    })

            url = data.get("nextUri")
            if url:
                url = f"https://api.{region}{url}"

        # TEMPORARY LOG: Display all data pulled from the KB for verification
        print(f"DEBUG: KB Data Pulled for {self.chat_id}: {json.dumps(all_content)}")

        return all_content


def lambda_handler(event, context):
    # Log the ID for tracing with AgentCore Memory. We check both 'session_id' and 'thread_id' for consistency.
    trace_id = event.get("thread_id") or event.get("session_id") or "UNKNOWN_SESSION"
    print(f"CRM TOOL CALL | Trace: {trace_id} | Event: {json.dumps(event)}")

    chat_id = event.get("live_chat_identifier")
    method = event.get("method")
    request_id = event.get("id")

    try:
        config = CRM_CONFIG_MAP.get(chat_id)
        if not config:
            print(f"❌ [ERROR] No config found for identifier: {chat_id}")
            return {"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": "CONFIG_ERROR: Service not found."}]}}

        # Selection logic (Platform-agnostic factory)
        integration = GenesysIntegration(chat_id, config) if config["platform"] == "genesys" else None

        if "check_chat_availability" in method:
            message = integration.fetch_adviser_availability()
            result = {"content": [{"type": "text", "text": message}]}

        elif "connect_to_live_chat" in method:
            result = integration.generate_handoff_signal(event)
            # --- HANDOFF STATUS LOG ---
            print(f"METRIC | LiveHandoffInitiated | Target: {chat_id} | Trace: {trace_id}")

        elif "fetch_kb_metadata" in method:
            result = integration.fetch_kb_metadata()

        elif "fetch_kb_articles" in method:
            result = integration.fetch_kb_articles()

        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    except Exception as e:
        print(f"❌ [ERROR] CRM TOOL TOP-LEVEL FAILURE: {str(e)}")
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"content": [{"type": "text", "text": "SERVICE_ERROR: Internal Tool Error."}]}
        }
