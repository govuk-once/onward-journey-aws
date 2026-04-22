"""
RDS Data Seeder Lambda.
Handles the ingestion of CSV datasets from S3 into RDS PostgreSQL tables,
generating vector embeddings via Amazon Bedrock Titan v2 for RAG capabilities.
"""

import csv
import json
import os
import boto3

from utils.db import get_db_connection
from utils.aws import get_bedrock_client

def get_embedding(bedrock_client, text):
    """
    Invokes the Amazon Titan Text Embeddings v2 model via Bedrock.
    Matches the local prototype configuration (1024 dimensions, normalized).
    """
    dimensions = 1024
    body = json.dumps({
        "inputText": text,
        "dimensions": dimensions,
        "normalize": True
    })

    try:
        response = bedrock_client.invoke_model(
            body=body,
            modelId="amazon.titan-embed-text-v2:0",
            contentType="application/json",
            accept="application/json"
        )
        return json.loads(response.get("body").read())["embedding"]
    except Exception as e:
        print(f"Embedding API Error: {e}")
        # Return a zero-vector fallback to prevent the entire batch from failing,
        # matching the error handling style of the local prototype.
        return [0.0] * dimensions


def sync_knowledge_base(conn, bedrock):
    """
    Dedicated logic for Knowledge Base Sync-on-Change.
    Iterates through configured CRM KBs and updates RDS if remote changes are detected.
    This function is intended to be called by a scheduled EventBridge trigger or manually.
    """
    lambda_client = boto3.client("lambda")
    crm_tool_arn = os.environ.get("CRM_TOOL_LAMBDA_ARN")

    # 1. Ensure Metadata and KB Tables exist
    conn.run("CREATE TABLE IF NOT EXISTS sync_kb_metadata (kb_identifier TEXT PRIMARY KEY, last_modified TEXT);")
    conn.run("CREATE TABLE IF NOT EXISTS genesys_kb (id SERIAL PRIMARY KEY, external_id TEXT UNIQUE, title TEXT, content TEXT, kb_identifier TEXT, external_url TEXT, embedding vector(1024));")

    # 2. Dynamically fetch identifiers from the contacts database.
    # This ensures any new service added to the database is automatically synced.
    try:
        rows = conn.run("SELECT DISTINCT live_chat_identifier FROM dept_contacts_v2 WHERE live_chat_identifier IS NOT NULL;")
        identifiers = [r[0] for r in rows]
        print(f"Sync Engine: Identified {len(identifiers)} sources from dept_contacts_v2.")
    except Exception as e:
        print(f"WARNING: Could not fetch identifiers from dept_contacts_v2 (perhaps table is empty?): {e}")
        # Fallback to empty list to prevent crash
        identifiers = []

    for kb_id in identifiers:
        try:
            # A. Fetch Remote Version from CRM Tool
            meta_payload = {"method": "fetch_kb_metadata", "live_chat_identifier": kb_id, "id": f"sync-meta-{kb_id}"}
            meta_resp = lambda_client.invoke(FunctionName=crm_tool_arn, Payload=json.dumps(meta_payload))

            # Parse the response from CRM tool
            payload_content = json.loads(meta_resp["Payload"].read().decode())
            if "error" in payload_content.get("result", {}):
                print(f"SKIPPING {kb_id}: CRM tool reported error: {payload_content['result']['error']}")
                continue

            remote_meta = payload_content["result"]
            remote_date = remote_meta.get("dateModified")

            # B. Check Local Version in RDS
            local_meta = conn.run("SELECT last_modified FROM sync_kb_metadata WHERE kb_identifier = :id", id=kb_id)
            local_date = local_meta[0][0] if local_meta else None

            if local_date == remote_date:
                print(f"SKIPPING: KB {kb_id} is up to date (Modified: {remote_date})")
                continue

            print(f"SYNCING: KB {kb_id} (Remote: {remote_date} | Local: {local_date})")

            # C. Fetch Full Flattened Articles
            art_payload = {"method": "fetch_kb_articles", "live_chat_identifier": kb_id, "id": f"sync-art-{kb_id}"}
            art_resp = lambda_client.invoke(FunctionName=crm_tool_arn, Payload=json.dumps(art_payload))
            articles = json.loads(art_resp["Payload"].read().decode())["result"]

            # D. Atomic Upsert with Vector Embedding
            conn.run("BEGIN")
            for i, art in enumerate(articles):
                # Construct descriptive context string for high-quality vector embeddings
                text_to_embed = f"Title: {art['title']}. Content: {art['content']}"
                vector = get_embedding(bedrock, text_to_embed)
                vector_str = "[" + ",".join(map(str, vector)) + "]"

                conn.run("""
                    INSERT INTO genesys_kb (external_id, title, content, kb_identifier, external_url, embedding)
                    VALUES (:eid, :title, :content, :kb, :url, :emb::vector)
                    ON CONFLICT (external_id) DO UPDATE SET
                        title = EXCLUDED.title,
                        content = EXCLUDED.content,
                        embedding = EXCLUDED.embedding;
                """,
                eid=art["external_id"], title=art["title"], content=art["content"],
                kb=kb_id, url=art["external_url"], emb=vector_str)

                # PROGRESS LOG: Print every article to confirm work is happening
                if i % 5 == 0:
                    print(f"[{kb_id}] Synced {i+1}/{len(articles)} articles...")
            # E. Update Metadata to reflect successful sync
            conn.run("INSERT INTO sync_kb_metadata (kb_identifier, last_modified) VALUES (:id, :date) ON CONFLICT (kb_identifier) DO UPDATE SET last_modified = EXCLUDED.last_modified;", id=kb_id, date=remote_date)
            conn.run("COMMIT")
            print(f"SUCCESS: Synced {len(articles)} articles for {kb_id}")

        except Exception as e:
            conn.run("ROLLBACK")
            print(f"ERROR: Failed to sync KB {kb_id}: {str(e)}")


def lambda_handler(event, context):
    """
    Main entry point for the RDS Seeder.
    Processes a specific S3 CSV file and performs an atomic update on its
    corresponding PostgreSQL table, including vector embedding generation.
    """
    # Check for independent Knowledge Base sync trigger (e.g. from EventBridge or Manual)
    sync_type = event.get("sync_type")

    # Initialise service clients
    bedrock = get_bedrock_client()

    # Establish database connection using shared utility
    conn = get_db_connection()

    try:
        # Ensure pgvector extension is available in the database
        # This is performed outside the transaction to prevent parallel race conditions
        # (duplicate key errors) when multiple Lambdas trigger simultaneously.
        conn.run("CREATE EXTENSION IF NOT EXISTS vector;")

        # Route to dedicated KB sync logic if requested
        if sync_type == "kb_sync":
            sync_knowledge_base(conn, bedrock)
            return {"status": "success", "mode": "kb_sync"}

        # --- EXISTING CSV SEEDING LOGIC ---
        target_file = event["file_name"]
        target_table = event["table_name"]

        # Load the master configuration provided by Terraform as a JSON string
        config = json.loads(os.environ["DB_CONFIG"])

        # Locate the specific table definition from our YAML configuration
        table_conf = next((t for t in config["tables"] if t["name"] == target_table), None)
        if not table_conf:
            raise Exception(f"Configuration for table '{target_table}' not found in YAML.")

        # Initialise service clients
        s3 = boto3.client("s3")

        # Wrap data ingestion in a transaction for atomicity
        conn.run("BEGIN")

        # 1. TABLE INITIALIZATION
        # To ensure the database matches the latest YAML/CSV schema, we drop
        # the existing table and recreate it. This removes stale data and locks.
        columns = table_conf["columns"]
        col_definitions = ", ".join(
            [f"{name} {dtype}" for name, dtype in columns.items()]
        )

        print(f"Initialising/recreating table: {target_table}")
        conn.run(f"DROP TABLE IF EXISTS {target_table} CASCADE;")
        conn.run(
            f"CREATE TABLE {target_table} (id SERIAL PRIMARY KEY, {col_definitions});"
        )

        # 2. FETCH SOURCE DATA
        print(f"Fetching source data from S3: mock/{target_file}")
        s3_res = s3.get_object(
            Bucket=os.environ["BUCKET_NAME"], Key=f"mock/{target_file}"
        )

        # 'utf-8-sig' handles potential Byte Order Marks (BOM) from Excel CSV exports
        lines = s3_res["Body"].read().decode("utf-8-sig").splitlines()
        reader = csv.DictReader(lines)

        # 3. DYNAMIC INGESTION & EMBEDDING
        embed_cols = table_conf.get("embedding_source_cols", [])

        print(f"Starting ingestion for {target_table}...")
        for i, row in enumerate(reader):
            # Map CSV row data to the columns defined in the YAML - force blank strings to None for SQL NULL conversion
            # 'embedding' is excluded here as it is calculated via Bedrock below
            params = {
                name: (row.get(name) if row.get(name) != "" else None)
                for name in columns.keys() if name != "embedding"
            }

            # Semantic Embedding Logic
            # If the schema defines an 'embedding' column and source columns are provided:
            if "embedding" in columns and embed_cols:
                # Construct a descriptive context string for high-quality vector embeddings
                # Concatenating columns specified in the YAML 'embedding_source_cols'
                text_to_embed = ". ".join(
                    [f"{col}: {row.get(col, '')}" for col in embed_cols]
                )

                # Invoke Bedrock to generate a 1024-dimension vector
                vector = get_embedding(bedrock, text_to_embed)

                # Format for pgvector input: [val1, val2, ...]
                params["emb"] = "[" + ",".join(map(str, vector)) + "]"

            # Build Dynamic INSERT statement based on the active parameter map
            col_names = ", ".join(params.keys()).replace("emb", "embedding")
            placeholders = ", ".join([f":{k}" for k in params.keys()])

            conn.run(
                f"INSERT INTO {target_table} ({col_names}) VALUES ({placeholders})",
                **params,
            )

            # Log progress to monitor health and runtime in CloudWatch
            if i % 10 == 0:
                print(f"Successfully processed {i} rows for {target_table}...")

        # Commit only if every row in the file was processed successfully
        conn.run("COMMIT")
        print(f"Ingestion complete for {target_table}. Total rows processed: {i + 1}")
        return {"status": "success", "table": target_table, "rows_processed": i + 1}

    except Exception as e:
        # Roll back the transaction if any row or embedding call fails
        conn.run("ROLLBACK")
        print(f"DATABASE ERROR: {str(e)}")
        raise e
    finally:
        conn.close()
