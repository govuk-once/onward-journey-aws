"""
Shared configuration for CRM integrations.
"""

# --- GENESYS SANDBOX CONSTANTS ---
# These act as the 'glue' between RDS metadata and Genesys specific routing.
# TODO: Future enhancement: Move these IDs to an RDS table.
sandbox_queue_id = "7c1702bc-8f49-4cd6-96d4-51b6542b26f5"
sandbox_deploy_id = "a548193a-6a74-474d-8e2d-f0adb0f291b1"

CRM_CONFIG_MAP = {
    "hmp-track-001": {
        "platform": "genesys",
        "secret_path": "crm-creds/home-office-genesys",
        "api_region": "euw2.pure.cloud",
        "queue_id": sandbox_queue_id,
        "deploy_id": sandbox_deploy_id
    },
    "ho-visa-005": {
        "platform": "genesys",
        "secret_path": "crm-creds/home-office-genesys",
        "api_region": "euw2.pure.cloud",
        "queue_id": "ho-visa-queue-uuid",
        "deploy_id": "ho-visa-deploy-uuid"
    },
    "dvla-renew-003": {
        "platform": "genesys",
        "secret_path": "crm-creds/dvla-genesys",
        "api_region": "euw2.pure.cloud",
        "queue_id": "dvla-renew-uuid",
        "deploy_id": "dvla-renew-deploy-uuid"
    }
}
