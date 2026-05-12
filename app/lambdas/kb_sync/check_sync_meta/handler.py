"""
KB Sync: Check Sync Metadata Lambda.

This Lambda is part of the Knowledge Base (KB) synchronisation workflow.
It compares the modification timestamp from the remote provider with the
last recorded sync timestamp in the local database (RDS) to determine if
an update is necessary.
"""

import json
from utils.db import get_db_connection

def lambda_handler(event, context):
    """
    Compares remote and local KB metadata to decide if synchronisation is needed.

    Args:
        event (dict): The Lambda event object, expected to contain:
            - kb_identifier (str): Unique identifier for the Knowledge Base.
            - remote_modified_date (str): The latest modification date from the provider.
        context (LambdaContext): AWS Lambda context object.

    Returns:
        dict: A dictionary containing:
            - sync_required (bool): True if the remote data differs from local.
            - remote_modified_date (str): The remote date passed in the event.
            - local_date (str): The last recorded sync date in the local database.
            - kb_identifier (str): The KB ID being checked.

    Raises:
        Exception: If there is an issue connecting to or querying the database.
    """
    print(f"Received event: {json.dumps(event)}")
    kb_identifier = event.get("kb_identifier")
    remote_date = event.get("remote_modified_date")

    # 1. Check Local RDS
    conn = get_db_connection()
    try:
        local_meta = conn.run("SELECT last_modified FROM sync_kb_metadata WHERE kb_identifier = :id", id=kb_identifier)
        local_date = local_meta[0][0] if local_meta else None
    finally:
        conn.close()

    # 2. Decision
    # If remote_date is None (empty KB), we might still want to sync if local is not None
    sync_required = (remote_date != local_date) or (remote_date is None and local_date is not None)

    print(f"KB {kb_identifier}: Remote({remote_date}) vs Local({local_date}) -> Sync Required: {sync_required}")

    return {
        "sync_required": sync_required,
        "remote_modified_date": remote_date,
        "local_date": local_date,
        "kb_identifier": kb_identifier
    }
