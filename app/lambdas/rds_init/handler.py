"""
RDS Initialisation Lambda.

This Lambda handles automated setup tasks for the RDS PostgreSQL database:
1. Idempotently creates the 'rds_readonly_dept_contacts' user (w/IAM auth).
2. Ensures the 'pgvector' extension is installed.
3. Provisions the necessary tables for the Knowledge Base.
"""

import json
import os
from utils.db import get_db_connection

def lambda_handler(event, context):
    """
    Orchestrates the RDS setup process.
    """
    kb_config_raw = os.environ.get("KB_CONFIG")

    if not kb_config_raw:
        raise Exception("KB_CONFIG environment variable is missing.")

    config = json.loads(kb_config_raw)

    # 1. Connect as ADMIN to perform setup
    conn = get_db_connection()

    try:
        # --- PHASE 1: USER PROVISIONING (IAM AUTH) ---
        print("Provisioning 'rds_readonly_dept_contacts' for IAM Auth...")
        # Create the user without a password, link it to IAM, and restrict to SELECT
        conn.run("""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'rds_readonly_dept_contacts') THEN
                    CREATE USER rds_readonly_dept_contacts;
                END IF;
            END
            $$;
        """)
        conn.run("GRANT rds_iam TO rds_readonly_dept_contacts;")
        conn.run("GRANT CONNECT ON DATABASE gov_dept_contacts TO rds_readonly_dept_contacts;")
        conn.run("GRANT USAGE ON SCHEMA public TO rds_readonly_dept_contacts;")
        conn.run("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO rds_readonly_dept_contacts;")
        conn.run("GRANT SELECT ON ALL TABLES IN SCHEMA public TO rds_readonly_dept_contacts;")

        # --- PHASE 2: EXTENSIONS ---
        print("Ensuring pgvector extension is available...")
         # This is performed outside the transaction to prevent parallel race conditions
        # (duplicate key errors) when multiple Lambdas trigger simultaneously.
        conn.run("CREATE EXTENSION IF NOT EXISTS vector;")

        # --- PHASE 3: TABLES ---
        conn.run("BEGIN")
        for table_conf in config.get("tables", []):
            target_table = table_conf["name"]
            columns = table_conf["columns"]

            col_parts = []

            # Check if ANY column definition already contains "PRIMARY KEY" (case-insensitive)
            has_explicit_pk = any("PRIMARY KEY" in str(dtype).upper() for dtype in columns.values())

            # Only auto-inject the default 'id' if there isn't already an explicit primary key
            if not has_explicit_pk and table_conf.get("primary_key", "id") == "id" and "id" not in columns:
                col_parts.append("id SERIAL PRIMARY KEY")

            for name, dtype in columns.items():
                col_parts.append(f"{name} {dtype}")

            col_definitions = ", ".join(col_parts)

            print(f"Initialising table (if not exists): {target_table}")
            conn.run(f"CREATE TABLE IF NOT EXISTS {target_table} ({col_definitions});")

            # Explicitly grant select on the newly created table
            conn.run(f"GRANT SELECT ON {target_table} TO rds_readonly_dept_contacts;")

        conn.run("COMMIT")
        print("RDS initialisation complete.")
        return {"status": "success", "message": "RDS setup and IAM user provisioning complete."}

    except Exception as e:
        if conn:
            conn.run("ROLLBACK")
        print(f"DATABASE ERROR: {str(e)}")
        raise e
    finally:
        if conn:
            conn.close()
