"""
RDS Knowledge Base Initialisation Lambda.

This Lambda ensures that the necessary tables for the external Knowledge Base
sync process exist in the RDS database.
"""

import json
import os
from utils.db import get_db_connection

def lambda_handler(event, context):
    """
    Initialises Knowledge Base tables without dropping existing data.

    1. Retrieves table schemas from the environment (KB_CONFIG).
    2. Ensures pgvector extension is installed.
    3. Idempotently creates tables defined in the configuration.
    """
    kb_config_raw = os.environ.get("KB_CONFIG")
    if not kb_config_raw:
        raise Exception("KB_CONFIG environment variable is missing.")

    config = json.loads(kb_config_raw)

    # Establish database connection
    conn = get_db_connection()

    try:
        # Ensure pgvector extension is available
        # This is performed outside the transaction to prevent parallel race conditions
        # (duplicate key errors) when multiple Lambdas trigger simultaneously.
        conn.run("CREATE EXTENSION IF NOT EXISTS vector;")

        # Start transaction
        conn.run("BEGIN")

        for table_conf in config.get("tables", []):
            target_table = table_conf["name"]
            columns = table_conf["columns"]

            # Build column definitions
            col_parts = []
            if table_conf.get("primary_key", "id") == "id" and "id" not in columns:
                col_parts.append("id SERIAL PRIMARY KEY")

            for name, dtype in columns.items():
                col_parts.append(f"{name} {dtype}")

            col_definitions = ", ".join(col_parts)

            print(f"Initialising table (if not exists): {target_table}")
            conn.run(f"CREATE TABLE IF NOT EXISTS {target_table} ({col_definitions});")

        # Commit transaction
        conn.run("COMMIT")
        print("Knowledge Base initialisation complete.")
        return {"status": "success", "message": "KB tables initialised safely."}

    except Exception as e:
        conn.run("ROLLBACK")
        print(f"DATABASE ERROR: {str(e)}")
        raise e
    finally:
        conn.close()
