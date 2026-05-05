import time
import uuid
import requests
import json
from typing import Dict, Any, List, Optional
from utils.genesys_parser import parse_genesys_blocks
from integrations.base import BaseCrmProvider

class GenesysProvider(BaseCrmProvider):
    """
    Unified Genesys Cloud Provider.
    Handles Auth, Knowledge Base (KB), and Live Chat capabilities.
    """

    # --- AUTH & CORE ---

    def _refresh_oauth_token(self) -> str:
        if self._token and time.time() < (self._token_expiry - 60):
            return self._token

        print(f"🔐 Refreshing Genesys OAuth Token for {self.identifier}...")
        auth_url = self.get_auth_url()
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

    def get_api_url(self, path: str) -> str:
        region = self.config['api_region']
        if not path.startswith('/'):
            path = f'/{path}'
        return f"https://api.{region}{path}"

    def get_auth_url(self) -> str:
        region = self.config['api_region']
        return f"https://login.{region}/oauth/token"

    def get_standard_headers(self) -> Dict[str, str]:
        token = self._refresh_oauth_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

    # --- KB CAPABILITIES ---

    def fetch_remote_modified_date(self) -> Optional[str]:
        headers = self.get_standard_headers()
        kb_uuid = self.creds.get("kb_id")
        url = self.get_api_url(f"/api/v2/knowledge/knowledgebases/{kb_uuid}")

        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        return resp.json().get("dateModified")

    def fetch_articles(self) -> List[Dict[str, Any]]:
        headers = self.get_standard_headers()
        kb_uuid = self.creds.get("kb_id")
        articles = []

        url = self.get_api_url(f"/api/v2/knowledge/knowledgebases/{kb_uuid}/documents")

        while url:
            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            for doc in data.get("entities", []):
                var_url = self.get_api_url(f"/api/v2/knowledge/knowledgebases/{kb_uuid}/documents/{doc['id']}/variations")
                var_resp = requests.get(var_url, headers=headers, timeout=10)
                var_data = var_resp.json()

                if var_data.get("entities"):
                    raw_text = parse_genesys_blocks(var_data["entities"][0].get("body", {}).get("blocks", []))
                    articles.append({
                        "title": doc["title"],
                        "content": raw_text,
                        "kb_identifier": self.identifier,
                        "external_id": doc["id"],
                        "external_url": f"https://genesys.cloud/kb/{kb_uuid}/article/{doc['id']}"
                    })

            next_uri = data.get("nextUri")
            url = self.get_api_url(next_uri) if next_uri else None

        return articles

    # --- LIVE CHAT CAPABILITIES ---

    def fetch_adviser_availability(self) -> str:
        headers = self.get_standard_headers()
        q_id = self.config['queue_id']

        conf_url = self.get_api_url(f"/api/v2/routing/queues/{q_id}")
        conf = requests.get(conf_url, headers=headers, timeout=5).json()
        if conf.get("joinedMemberCount", 0) == 0:
            return "Live chat is currently unavailable."

        query_url = self.get_api_url("/api/v2/analytics/queues/observations/query")
        query_payload = {
            "filter": {"type": "and", "predicates": [
                {"dimension": "queueId", "value": q_id},
                {"dimension": "mediaType", "value": "message"}
            ]},
            "metrics": ["oOnQueueUsers"]
        }
        obs_resp = requests.post(query_url, headers=headers, json=query_payload, timeout=5)
        obs_results = obs_resp.json().get("results", [{}])
        on_queue = 0

        if obs_results and "data" in obs_results[0]:
            for entry in obs_results[0]["data"]:
                if entry.get("metric") == "oOnQueueUsers" and entry.get("qualifier") in ["IDLE", "INTERACTING"]:
                    on_queue += entry.get("stats", {}).get("count", 0)

        if on_queue == 0:
            return "Live chat is currently unavailable."

        ewt_url = self.get_api_url(f"/api/v2/routing/queues/{q_id}/estimatedwaittime")
        ewt_resp = requests.get(ewt_url, headers=headers, timeout=5).json()
        results = ewt_resp.get("results", [{}])
        wait_seconds = results[0].get("estimatedWaitTimeSeconds", 0) if results else 0
        wait_str = f"{wait_seconds // 60}m" if wait_seconds > 0 else "under 1 minute"

        return f"Live chat is AVAILABLE. Estimated wait: {wait_str}."

    def generate_handoff_signal(self, event: Dict[str, Any]) -> Dict[str, Any]:
        region = self.config['api_region']
        deploy_id = self.config['deploy_id']
        token = event.get("thread_id", str(uuid.uuid4()))
        actor_id = event.get("actor_id", "ANONYMOUS")

        websocket_url = f"wss://webmessaging.{region}/v1?deploymentId={deploy_id}"

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

        if not deploy_id or not region:
            return {
                "content": [{
                    "type": "text",
                    "text": "SERVICE_ERROR: CRM configuration is incomplete."
                }]
            }

        return {
            "content": [{
                "type": "text",
                "text": f"SIGNAL: initiate_live_handoff {json.dumps(handoff_config)}"
            }]
        }
