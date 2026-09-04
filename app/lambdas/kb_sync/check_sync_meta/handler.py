"""
KB Sync: Check Sync Metadata Lambda.

This Lambda is part of the Knowledge Base (KB) synchronisation workflow.
It first verifies that the KB identifier is actively mapped to a department
in the database. If mapped, it compares the modification timestamp from the
remote provider with the last recorded sync timestamp in the local database (RDS)
to determine if an update is necessary.
"""

import os
import json
from utils.db import get_db_connection

CONTACTS_TABLE_NAME = os.environ.get("CONTACTS_TABLE_NAME")

def lambda_handler(event, context):
    """
    Validates KB mapping and compares remote and local KB metadata to decide if
    synchronisation is needed.

    Args:
        event (dict): The Lambda event object, expected to contain:
            - kb_identifier (str): Unique identifier for the Knowledge Base.
            - remote_modified_date (str): The latest modification date from the provider.
        context (LambdaContext): AWS Lambda context object.

    Returns:
        dict: A dictionary containing:
            - is_mapped (bool): True if the KB is actively mapped to a department.
            - sync_required (bool): True if the remote data differs from local.
            - remote_modified_date (str): The remote date passed in the event.
            - local_date (str): The last recorded sync date in the local database.
            - kb_identifier (str): The KB ID being checked.
            - reason (str, optional): Explains why a sync was aborted (if applicable).

    Raises:
        Exception: If there is an issue connecting to or querying the database.
    """
    print(f"Received event: {json.dumps(event)}")
    kb_identifier = event.get("kb_identifier")
    remote_date = event.get("remote_modified_date")

    conn = get_db_connection()
    try:
        # 1. Active Mapping Validation
        # Verify the KB is still mapped to an active department (seeded via CSV).
        # If the KB ID was removed from the CSV, this prevents ghost syncing.
        mapped_result = conn.run(
            f"SELECT 1 FROM {CONTACTS_TABLE_NAME} WHERE knowledge_base_identifier = :id",
            id=kb_identifier
        )
        is_mapped = bool(mapped_result)

        if not is_mapped:
            print(f"KB {kb_identifier} is unmapped. Flagging for downstream cleanup.")
            return {
                "sync_required": False,
                "is_mapped": False,
                "kb_identifier": kb_identifier,
                "reason": "Unmapped KB",
                "remote_modified_date": remote_date,
                "local_date": None
            }

        # 2. Local Sync Metadata Check
        # Retrieve the timestamp of the last successful sync for this KB.
        local_meta = conn.run(
            "SELECT last_modified FROM sync_kb_metadata WHERE kb_identifier = :id",
            id=kb_identifier
        )
        local_date = local_meta[0][0] if local_meta else None

    finally:
        conn.close()

    # 3. Synchronisation Decision
    # Sync if dates differ, OR if there's remote data but no local history (first-time sync).
    sync_required = (remote_date != local_date) or (remote_date is None and local_date is not None)

    print(f"KB {kb_identifier}: Remote({remote_date}) vs Local({local_date}) -> Sync Required: {sync_required}")

    return {
        "sync_required": sync_required,
        "is_mapped": True,
        "remote_modified_date": remote_date,
        "local_date": local_date,
        "kb_identifier": kb_identifier
    }
