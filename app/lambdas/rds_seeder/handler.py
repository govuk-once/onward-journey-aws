"""
RDS Data Seeder Lambda.

This Lambda handles the ingestion of CSV datasets from S3 into RDS PostgreSQL
tables. It dynamically creates tables based on a YAML configuration and
generates vector embeddings via Amazon Bedrock for RAG capabilities.
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
    Matches the local prototype configuration (1024 dimensions, normalised).
    """
    dimensions = 1024
    body = json.dumps({
        "inputText": text,
        "dimensions": dimensions,
        "normalize": True
    })

    response = bedrock_client.invoke_model(
        body=body,
        modelId="amazon.titan-embed-text-v2:0",
        contentType="application/json",
        accept="application/json"
    )
    return json.loads(response.get("body").read())["embedding"]


def lambda_handler(event, context):
    """
    Orchestrates the database seeding process for a specific table.

    1. Retrieves table schema and source file info from the event and environment.
    2. Drops and recreates the target table to ensure a clean state.
    3. Fetches the source CSV from S3.
    4. Iteratively generates embeddings for specified columns and inserts rows.
    5. Commits the transaction upon successful completion.

    Args:
        event (dict): The Lambda event object, expected to contain:
            - file_name (str, optional): The name of the CSV file in S3. If omitted,
                the table will be created without any initial data.
            - table_name (str): The name of the target RDS table.
        context (LambdaContext): AWS Lambda context object.

    Returns:
        dict: A status report containing the success state and row count.

    Raises:
        Exception: If table configuration is missing, S3 fetch fails, or
            database operations encounter an error.
    """
    # 1. VALIDATION & CONFIGURATION
    target_file = event.get("file_name")
    target_table = event.get("table_name")
    if not target_table:
        raise Exception("Missing mandatory 'table_name' in event payload.")

    db_config_raw = os.environ.get("DB_CONFIG")
    if not db_config_raw:
        raise Exception("DB_CONFIG environment variable is missing.")

    config = json.loads(db_config_raw)

    table_conf = next((t for t in config["tables"] if t["name"] == target_table), None)
    if not table_conf:
        raise Exception(f"Configuration for table '{target_table}' not found in YAML.")

    # Initialise service clients
    bedrock = get_bedrock_client()

    # Establish database connection using shared utility
    conn = get_db_connection()

    try:
        # Ensure pgvector extension is available in the database
        # This is performed outside the transaction to prevent parallel race conditions
        # (duplicate key errors) when multiple Lambdas trigger simultaneously.
        conn.run("CREATE EXTENSION IF NOT EXISTS vector;")

        # Wrap data ingestion in a transaction for atomicity
        conn.run("BEGIN")

        # 2. TABLE INITIALISATION
        columns = table_conf["columns"]

        # Build column definitions, handling primary keys carefully
        col_parts = []
        if table_conf.get("primary_key", "id") == "id" and "id" not in columns:
            col_parts.append("id SERIAL PRIMARY KEY")

        for name, dtype in columns.items():
            col_parts.append(f"{name} {dtype}")

        col_definitions = ", ".join(col_parts)

        print(f"Initialising/recreating table: {target_table}")
        conn.run(f"DROP TABLE IF EXISTS {target_table} CASCADE;")
        conn.run(f"CREATE TABLE {target_table} ({col_definitions});")

        # 2. FETCH SOURCE DATA & INGEST (Skip if no source_file provided)
        rows_processed = 0
        if target_file:
            print(f"Fetching source data from S3: mock/{target_file}")
            s3 = boto3.client("s3")
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
                # Map CSV row data to the columns defined in the YAML
                params = {
                    name: (row.get(name) if row.get(name) != "" else None)
                    for name in columns.keys() if name != "embedding"
                }

                # Semantic Embedding Logic
                if "embedding" in columns and embed_cols:
                    text_to_embed = ". ".join(
                        [f"{col}: {row.get(col, '')}" for col in embed_cols]
                    )
                    try:
                        vector = get_embedding(bedrock, text_to_embed)
                        params["emb"] = "[" + ",".join(map(str, vector)) + "]"
                    except Exception as e:
                        print(f"ERROR: Failed to generate embedding for row {i} in {target_table}: {e}")
                        print(f"Skipping row {i} to prevent batch failure.")
                        continue

                # Build Dynamic INSERT statement
                col_names = ", ".join(params.keys()).replace("emb", "embedding")
                placeholders = ", ".join([f":{k}" for k in params.keys()])

                conn.run(
                    f"INSERT INTO {target_table} ({col_names}) VALUES ({placeholders})",
                    **params,
                )
                rows_processed += 1

                if rows_processed % 10 == 0:
                    print(f"Successfully processed {rows_processed} rows for {target_table}...")
        else:
            print(f"No source file provided for {target_table}. Created empty table.")

        # Commit only if every row in the file was processed successfully
        conn.run("COMMIT")
        print(f"Ingestion complete for {target_table}. Total rows processed: {rows_processed}")
        return {"status": "success", "table": target_table, "rows_processed": rows_processed}

    except Exception as e:
        # Roll back the transaction if any row or embedding call fails
        conn.run("ROLLBACK")
        print(f"DATABASE ERROR: {str(e)}")
        raise e
    finally:
        conn.close()
