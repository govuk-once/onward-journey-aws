"""
RDS Initialisation Lambda.

This Lambda handles automated setup tasks for the RDS PostgreSQL database:
1. Idempotently creates the 'rds_readonly_dept_contacts' user and syncs its password.
2. Ensures the 'pgvector' extension is installed.
3. Provisions the necessary tables for the Knowledge Base.
"""

import json
import os
from utils.db import get_db_connection, get_db_password

def lambda_handler(event, context):
    """
    Orchestrates the RDS setup process.
    """
    kb_config_raw = os.environ.get("KB_CONFIG")
    rds_readonly_secret_arn = os.environ.get("RDS_READONLY_SECRET_ARN")

    if not kb_config_raw:
        raise Exception("KB_CONFIG environment variable is missing.")

    config = json.loads(kb_config_raw)

    # 1. Connect as ADMIN to perform setup
    conn = get_db_connection()

    try:
        # --- PHASE 1: USER PROVISIONING ---
        if rds_readonly_secret_arn:
            rds_readonly_pass = get_db_password(rds_readonly_secret_arn)

            print("Syncing 'rds_readonly_dept_contacts' user...")
            # Check if user exists
            user_exists = conn.run("SELECT 1 FROM pg_roles WHERE rolname = 'rds_readonly_dept_contacts';")

            if not user_exists:
                print("Creating 'rds_readonly_dept_contacts' user...")
                conn.run(f"CREATE USER rds_readonly_dept_contacts WITH PASSWORD '{rds_readonly_pass}';")
            else:
                print("Updating 'rds_readonly_dept_contacts' password...")
                conn.run(f"ALTER USER rds_readonly_dept_contacts WITH PASSWORD '{rds_readonly_pass}';")

            # Ensure permissions are correct
            conn.run("GRANT CONNECT ON DATABASE gov_dept_contacts TO rds_readonly_dept_contacts;")
            conn.run("GRANT USAGE ON SCHEMA public TO rds_readonly_dept_contacts;")
            conn.run("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO rds_readonly_dept_contacts;")
            # Also grant on existing tables just in case
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
            if table_conf.get("primary_key", "id") == "id" and "id" not in columns:
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
        return {"status": "success", "message": "RDS setup and user sync complete."}

    except Exception as e:
        if conn:
            conn.run("ROLLBACK")
        print(f"DATABASE ERROR: {str(e)}")
        raise e
    finally:
        if conn:
            conn.close()
