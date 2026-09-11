"""
KB Sync: Cleanup Unmapped KB Lambda.

This Lambda is responsible for maintaining data integrity by acting as a
garbage collector. It is triggered by the Step Functions state machine when
a Knowledge Base identifier is no longer mapped to an active department
in the database (e.g., removed from the seed CSV). It safely purges all
orphaned vector embeddings and sync history for that specific KB.
"""

from utils.db import get_db_connection

def lambda_handler(event, context):
    """
    Executes a transactional database purge for an unmapped Knowledge Base.

    Args:
        event (dict): The Lambda event payload, typically routed from the
            'CheckSyncMeta' step, expected to contain:
            - kb_identifier (str): The unique identifier of the unmapped KB.
        context (LambdaContext): AWS Lambda context object.

    Returns:
        dict: A status dictionary confirming the purge operation:
            - status (str): 'success' upon successful deletion.
            - action (str): Describes the action taken ('purged_unmapped_kb').
            - kb_identifier (str): The KB ID that was purged.

    Raises:
        ValueError: If 'kb_identifier' is missing from the event payload.
        Exception: If a database transaction fails during the deletion process.
    """
    kb_identifier = event.get("kb_identifier")

    if not kb_identifier:
        raise ValueError("kb_identifier is required for cleanup.")

    print(f"Initiating purge for unmapped KB: {kb_identifier}")
    conn = get_db_connection()

    try:
        # Wrap deletions in a transaction to ensure both tables are cleaned
        # successfully, preventing partial data leaks.
        conn.run("BEGIN")

        # 1. Purge vector embeddings and article content
        conn.run("DELETE FROM knowledge_base_articles WHERE kb_identifier = :id", id=kb_identifier)

        # 2. Purge the sync state tracker
        conn.run("DELETE FROM sync_kb_metadata WHERE kb_identifier = :id", id=kb_identifier)

        conn.run("COMMIT")

        print(f"Successfully purged all data for {kb_identifier}.")
        return {
            "status": "success",
            "action": "purged_unmapped_kb",
            "kb_identifier": kb_identifier
        }
    except Exception as e:
        conn.run("ROLLBACK")
        print(f"PURGE ERROR [{kb_identifier}]: {str(e)}")
        raise e
    finally:
        conn.close()
