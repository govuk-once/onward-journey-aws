"""
KB Sync: Update Sync Metadata Lambda.

This Lambda is responsible for updating the 'sync_kb_metadata' table at the end
of a synchronisation workflow. It records the success or failure status,
the last modified date (if successful), and any error messages.
"""

from utils.db import get_db_connection

def lambda_handler(event, context):
    """
    Updates the sync metadata in the database.

    Args:
        event (dict): The Lambda event object, expected to contain:
            - kb_identifier (str): Unique identifier for the Knowledge Base.
            - status (str): The sync status ('SUCCESS' or 'FAILED').
            - remote_modified_date (str, optional): The timestamp of the remote KB.
            - error (str, optional): The error message if status is 'FAILED'.
        context (LambdaContext): AWS Lambda context object.

    Returns:
        dict: A status report.
    """
    kb_identifier = event.get("kb_identifier")
    status = event.get("status")
    remote_date = event.get("remote_modified_date")
    error = event.get("error")

    if not kb_identifier:
        raise Exception("kb_identifier is required")

    if status not in ("SUCCESS", "FAILED"):
        raise ValueError(f"Invalid sync status received: {status}")

    conn = get_db_connection()

    try:
        conn.run("BEGIN")

        if status == "SUCCESS":
            conn.run("""
                INSERT INTO sync_kb_metadata (kb_identifier, last_modified, sync_status, last_sync_error)
                VALUES (:kb_identifier, :date, :status, NULL)
                ON CONFLICT (kb_identifier) DO UPDATE SET
                    last_modified = EXCLUDED.last_modified,
                    sync_status = EXCLUDED.sync_status,
                    last_sync_error = NULL;
            """, kb_identifier=kb_identifier, date=remote_date, status=status)
        else:
            conn.run("""
                INSERT INTO sync_kb_metadata (kb_identifier, sync_status, last_sync_error)
                VALUES (:kb_identifier, :status, :error)
                ON CONFLICT (kb_identifier) DO UPDATE SET
                    sync_status = EXCLUDED.sync_status,
                    last_sync_error = EXCLUDED.last_sync_error;
            """, kb_identifier=kb_identifier, status=status, error=error)

        conn.run("COMMIT")

        return {
            "status": "success",
            "kb_identifier": kb_identifier,
            "sync_status": status
        }

    except Exception as e:
        conn.run("ROLLBACK")
        print(f"METADATA UPDATE ERROR [{kb_identifier}]: {str(e)}")
        raise e
    finally:
        conn.close()
