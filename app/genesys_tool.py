import os
import json
import time
import uuid
import boto3
import requests
from typing import Dict, Any

_cached_token = None
_token_expiry = 0
_cached_creds = None # Stores unpacked JSON from Secrets Manager


SECRET_ARN = os.environ.get("GENESYS_SECRET_ARN")
API_REGION = os.environ.get("GENESYS_API_REGION")

secrets_client = boto3.client("secretsmanager")

def get_genesys_secrets():
    """
    Retrieve and cache the raw secret data from AWS Secrets Manager.
    This includes client_id, client_secret, and org_id.
    """
    global _cached_creds
    if _cached_creds:
        return _cached_creds

    print("Fetching Genesys Secrets from AWS Secrets Manager")
    response = secrets_client.get_secret_value(SecretId=SECRET_ARN)
    _cached_creds = json.loads(response["SecretString"])
    return _cached_creds

def get_genesys_token():
    """
    Authenticate with Genesys Cloud and cache the bearer token.
    """
    global _cached_token, _token_expiry
    if _cached_token and time.time() < (_token_expiry - 60):
        return _cached_token

    creds = get_genesys_secrets()
    client_id = creds.get("client_id")
    client_secret = creds.get("client_secret")

    if not client_id or not client_secret:
        raise Exception("SECRET_ERROR: client_id or client_secret missing from secret.")

    print("🔐 Refreshing Genesys OAuth Token...")
    auth_url = f"https://login.{API_REGION}/oauth/token"
    resp = requests.post(
        auth_url,
        data={"grant_type": "client_credentials"},
        auth=(client_id, client_secret),
        timeout=10
    )
    
    if resp.status_code != 200:
        raise Exception(f"Genesys Auth Failed: {resp.text}")

    data = resp.json()
    _cached_token = data["access_token"]
    _token_expiry = time.time() + data["expires_in"]
    return _cached_token

def get_queue_status(queue_id: str):
    """Uses Analytics Observation Query to find available agents."""
    token = get_genesys_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    
    # 1. Observation Query for Real-time Availability
    # Checks for agents 'On Queue'.
    query_url = f"https://api.{API_REGION}/api/v2/analytics/queues/observations/query"
    query_payload = {
        "filter": {"type": "and", "predicates": [{"dimension": "queueId", "value": queue_id}]},
        "metrics": ["oOnQueueUsers"]
    }
    obs_resp = requests.post(query_url, headers=headers, json=query_payload, timeout=5)
    
    # 2. Estimated Wait Time (as a secondary check)
    ewt_url = f"https://api.{API_REGION}/api/v2/routing/queues/{queue_id}/estimatedwaittime"
    ewt_resp = requests.get(ewt_url, headers=headers, timeout=5)
    
    # Extract the count of users currently On Queue from the analytics results
    obs_data = obs_resp.json().get("results", [{}])[0].get("data", [])
    on_queue_count = 0
    for entry in obs_data:
        if entry.get("metric") == "oOnQueueUsers":
            on_queue_count = entry.get("stats", {}).get("count", 0)

    wait_seconds = ewt_resp.json().get("results", [{}])[0].get("estimatedWaitTimeSeconds", 0) if ewt_resp.status_code == 200 else None

    return {
        "is_available": on_queue_count > 0,
        "wait_time": f"{wait_seconds // 60}m" if wait_seconds else "under 1 minute"
    }

def lambda_handler(event: Dict[str, Any], context: Any):
    # Log the Session ID for tracing with AgentCore Memory
    session_id = event.get("session_id", "UNKNOWN_SESSION")
    print(f"GENESYS TOOL CALL | Session: {session_id} | Event: {json.dumps(event)}")

    method = event.get("method", "") 
    args = event.get("arguments", {})
    chat_id = args.get("live_chat_identifier")
    request_id = event.get("id")

    # MAPPING: Identifier -> (Deployment ID, Queue ID)
    # This acts as the 'glue' between RDS metadata and Genesys specific routing.
    # TODO: Future enhancement: These IDs will be moved to an RDS table.
    config_map = {
        "gate-dwp-uc-004": ("dwp-deploy-uuid", "dwp-queue-uuid"), # mock uuids
        "gate-hmrc-tax-002": ("hmrc-deploy-uuid", "hmrc-queue-uuid"), # mock uuids
        "gate-dvla-renew-003": ("a548193a-6a74-474d-8e2d-f0adb0f291b1", "7c1702bc-8f49-4cd6-96d4-51b6542b26f5")
    }
    
    deploy_id, queue_id = config_map.get(chat_id, (None, None))

    if not deploy_id:
        return {
            "jsonrpc": "2.0", 
            "id": request_id, 
            "result": {"content": [{"type": "text", "text": "CONFIG_ERROR: Service configuration not found for identifier."}]}
        }

    try:
        # CHOICE 1: AVAILABILITY CHECK
        creds = get_genesys_secrets()
        org_id = creds.get("org_id")

        if not org_id:
             raise Exception("CONFIG_ERROR: org_id missing from secret.")

        if "check_chat_availability" in method:
            status = get_queue_status(queue_id)
            msg = f"Live chat is AVAILABLE. Estimated wait: {status['wait_time']}." if status["is_available"] else "Live chat is currently unavailable."
            return {
                "jsonrpc": "2.0", 
                "id": request_id, 
                "result": {"content": [{"type": "text", "text": msg}]}
            }

        # CHOICE 2: CONNECTION HANDOFF
        # Returns a SIGNAL payload consumed by the Svelte setupGenesysSocket function.
        elif "connect_to_live_chat" in method:
            handoff_config = {
                "action": "initiate_live_handoff",
                "organizationId": org_id,
                "deploymentId": deploy_id,
                "region": API_REGION, 
                "token": str(uuid.uuid4()), # Maps to sessionToken in frontend
                "reason": args.get("reason", "AI Handover: Intent not captured"), # Provides a short category for Genesys routing (e.g., the 'Subject' line)
                "summary": args.get("summary", "No summary provided."), # Briefing note from LangGraph/AgentCore
                "customAttributes": {
                    "externalSessionId": session_id,
                    "department": chat_id,
                    "source": "onward-journey-ai",
                }
            }
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [
                        {
                            "type": "text", 
                            "text": f"SIGNAL: initiate_live_handoff {json.dumps(handoff_config)}"
                        }
                    ]
                }
            }

    except Exception as e:
        print(f"GENESYS ERROR: {str(e)}")
        return {
            "jsonrpc": "2.0", 
            "id": request_id, 
            "result": {"content": [{"type": "text", "text": "SERVICE_ERROR: Error connecting to chat service."}]}
        }
    