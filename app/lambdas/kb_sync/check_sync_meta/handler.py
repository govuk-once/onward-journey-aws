"""
KB Sync: Check Sync Metadata Lambda
"""

import os
import json
from utils.db import get_db_connection

def lambda_handler(event, context):
    print(f"Received event: {json.dumps(event)}")
    kb_id = event.get("kb_identifier")
    remote_date = event.get("remote_modified_date")

    # 1. Check Local RDS
    conn = get_db_connection()
    try:
        # Ensure metadata table exists
        conn.run("CREATE TABLE IF NOT EXISTS sync_kb_metadata (kb_identifier TEXT PRIMARY KEY, last_modified TEXT);")

        local_meta = conn.run("SELECT last_modified FROM sync_kb_metadata WHERE kb_identifier = :id", id=kb_id)
        local_date = local_meta[0][0] if local_meta else None
    finally:
        conn.close()

    # 2. Decision
    # If remote_date is None (empty KB), we might still want to sync if local is not None
    sync_required = (remote_date != local_date) or (remote_date is None and local_date is not None)

    print(f"KB {kb_id}: Remote({remote_date}) vs Local({local_date}) -> Sync Required: {sync_required}")

    return {
        "sync_required": sync_required,
        "remote_modified_date": remote_date,
        "kb_identifier": kb_id
    }
