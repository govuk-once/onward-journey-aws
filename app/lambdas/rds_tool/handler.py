"""
RDS Tool Lambda (MCP Server).
Handles semantic search requests forwarded by the AgentCore Gateway.
- query_department_database: Searches for contact details.
- query_knowledge_base: Searches for specific department policy/guidance.
"""

import os
import json
from utils.db import get_db_connection
from utils.aws import get_bedrock_client

def lambda_handler(event, context):
    """AgentCore Gateway sends the tool arguments inside the 'arguments' key."""

    print(f"📥 GATEWAY CALL: {json.dumps(event)}")

    # 1. Extract Method, Query and Request ID
    # AgentCore Gateway (MCP) passes the full target name as the method,
    # but our orchestrator usually appends the specific tool name with ___
    full_method = event.get("method", "")
    method = full_method.split("___")[-1] if "___" in full_method else full_method

    args = event.get("arguments", {})
    query = args.get("query") or event.get("query") or "No query provided"
    kb_id = args.get("kb_identifier")
    request_id = event.get("id")

    # Connect to RDS
    conn = get_db_connection()

    try:
        # --- BEGIN DEBUG BLOCK: DELETE AFTER TROUBLESHOOTING ---
        if method == "droptable":
            table_to_drop = args.get("table")
            if not table_to_drop:
                return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "table name required in arguments"}}

            print(f"🔥 [DEBUG] MANUAL DROP REQUESTED: {table_to_drop}")
            conn.run(f"DROP TABLE IF EXISTS {table_to_drop} CASCADE;")
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"content": [{"type": "text", "text": f"Successfully dropped table: {table_to_drop}"}]}
            }
        # --- END DEBUG BLOCK ---

        # 2. Generate Vector Embedding using Amazon Titan v2
        # We use 1024 dimensions and normalization to match our RDS pgvector settings
        bedrock = get_bedrock_client()
        embed_body = json.dumps({"inputText": query, "dimensions": 1024, "normalize": True})
        embed_resp = bedrock.invoke_model(
            modelId="amazon.titan-embed-text-v2:0",
            body=embed_body,
            contentType="application/json",
            accept="application/json"
        )
        embedding = json.loads(embed_resp["body"].read())["embedding"]

        if "query_knowledge_base" in method:
            # KB Search: Filter by the specific department KB identifier
            if not kb_id:
                print(f"❌ [ERROR] kb_identifier is required for {method}")
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32602, "message": "kb_identifier is required"}
                }

            # --- DEBUG LOGGING ---
            print(f"🔍 [DEBUG] KB Search START: kb_id='{kb_id}'")
            print(f"🔍 [DEBUG] DB_HOST={os.environ.get('DB_HOST')}")

            # Check total count vs identifier count to diagnose wiping/mismatch issues
            total_count = conn.run("SELECT COUNT(*) FROM knowledge_bases")[0][0]
            id_count = conn.run("SELECT COUNT(*) FROM knowledge_bases WHERE kb_identifier = :kb_id", kb_id=kb_id)[0][0]
            print(f"🔍 [DEBUG] KB Search: Total Rows={total_count}, Matches for '{kb_id}'={id_count}")

            results = conn.run(
                """
                SELECT title, content, external_url
                FROM knowledge_bases
                WHERE kb_identifier = :kb_id
                ORDER BY embedding <=> :embed::vector LIMIT 3
                """,
                kb_id=kb_id,
                embed=str(embedding),
            )
            formatted_results = [
                {"title": r[0], "content": r[1], "url": r[2]}
                for r in results
            ]
        else:
            # Default to Contact Search
            results = conn.run(
                """
                SELECT service_name, phone_number, live_chat_identifier, knowledge_base_identifier, description
                FROM dept_contacts_v3
                ORDER BY embedding <=> :embed::vector LIMIT 3
                """,
                embed=str(embedding),
            )
            formatted_results = [
                {
                    "service": r[0],
                    "phone": r[1],
                    "live_chat_identifier": r[2],
                    "knowledge_base_identifier": r[3],
                    "info": r[4]
                }
                for r in results
            ]
    finally:
        conn.close()

    # 4. Format results for the MCP-compatible response
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "content": [{"type": "text", "text": json.dumps(formatted_results)}]
        },
    }
