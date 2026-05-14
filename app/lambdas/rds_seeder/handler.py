"""
RDS Data Seeder Lambda.

This Lambda handles the ingestion of CSV datasets from S3 into RDS PostgreSQL
tables. It dynamically creates tables based on a YAML configuration
and generates vector embeddings via Amazon Bedrock Titan v2 for RAG capabilities.
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
    Returns a 1024-dimension numerical vector for the provided text.
    """
    body = json.dumps({
        "inputText": text,
        "dimensions": 1024,
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
            - file_name (str): The name of the CSV file in S3.
            - table_name (str): The name of the target RDS table.
        context (LambdaContext): AWS Lambda context object.

    Returns:
        dict: A status report containing the success state and row count.
    """
    target_file = event.get("file_name")
    target_table = event.get("table_name")

    if not target_table or not target_file:
        raise Exception("Missing mandatory 'table_name' or 'file_name' in event payload.")

    # Load the master configuration provided by Terraform as a JSON string
    db_config_raw = os.environ.get("DB_CONFIG")
    if not db_config_raw:
        raise Exception("DB_CONFIG environment variable is missing.")

    config = json.loads(db_config_raw)

    # Locate the specific table definition from our YAML configuration
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

        # 1. TABLE INITIALISATION
        # To ensure the database matches the latest YAML/CSV schema, we drop
        # the existing table and recreate it. This removes stale data and locks.
        columns = table_conf["columns"]

        # Build column definitions
        col_parts = ["id SERIAL PRIMARY KEY"]
        for name, dtype in columns.items():
            col_parts.append(f"{name} {dtype}")

        col_definitions = ", ".join(col_parts)

        print(f"Initialising/recreating table: {target_table}")
        conn.run(f"DROP TABLE IF EXISTS {target_table} CASCADE;")
        conn.run(f"CREATE TABLE {target_table} ({col_definitions});")

        # 2. FETCH SOURCE DATA
        rows_processed = 0
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
            # Map CSV row data to the columns defined in the YAML - force blank strings to None for SQL NULL conversion
            # 'embedding' is excluded here as it is calculated via Bedrock below
            params = {
                name: (row.get(name) if row.get(name) != "" else None)
                for name in columns.keys() if name != "embedding"
            }

            # Semantic Embedding Logic
            if "embedding" in columns and embed_cols:
                # Construct a descriptive context string for high-quality vector embeddings
                # Concatenating columns specified in the YAML 'embedding_source_cols'
                text_to_embed = ". ".join(
                    [f"{col}: {row.get(col, '')}" for col in embed_cols]
                )
                try:
                    # Invoke Bedrock to generate a 1024-dimension vector
                    vector = get_embedding(bedrock, text_to_embed)
                     # pgvector explicitly requires square brackets [val1, val2, ...] -
                     # Do not pass the raw Python list, as standard Postgres arrays use {} and will crash
                    params["emb"] = "[" + ",".join(map(str, vector)) + "]"
                except Exception as e:
                    print(f"ERROR: Failed to generate embedding for row {i} in {target_table}: {e}")
                    continue

            # Build Dynamic INSERT statement based on the active parameter map
            col_names = ", ".join(params.keys()).replace("emb", "embedding")
            placeholders = ", ".join([f":{k}" for k in params.keys()])

            conn.run(
                f"INSERT INTO {target_table} ({col_names}) VALUES ({placeholders})",
                **params,
            )
            rows_processed += 1

            if rows_processed % 10 == 0:
                print(f"Processed {rows_processed} rows for {target_table}...")

        # Commit all successfully processed rows
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
