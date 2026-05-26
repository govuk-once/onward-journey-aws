"""
Shared configuration for CRM integrations.
"""

# --- GENESYS SANDBOX CONSTANTS ---
# These act as the 'glue' between RDS metadata and Genesys specific routing.
# TODO: Future enhancement: Move these IDs to an RDS table.

CRM_CONFIG_MAP = {
    "hmp-track-001": {
        "platform": "genesys",
        "secret_path": "crm-creds/hmp-genesys",
        "api_region": "euw2.pure.cloud",
    },
    "ho-visa-005": {
        "platform": "genesys",
        "secret_path": "crm-creds/home-office-genesys",
        "api_region": "euw2.pure.cloud",
    },
    "dvla-renew-003": {
        "platform": "genesys",
        "secret_path": "crm-creds/dvla-genesys",
        "api_region": "euw2.pure.cloud",
    }
}
