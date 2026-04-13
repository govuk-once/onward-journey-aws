"""
RDS Tool Lambda (MCP Server).
Handles semantic search requests forwarded by the AgentCore Gateway.
"""

import os
import json
from utils.db import get_db_connection
from utils.aws import get_bedrock_client

def lambda_handler(event, context):
    """AgentCore Gateway sends the tool arguments inside the 'arguments' key."""

    print(f"📥 GATEWAY CALL: {json.dumps(event)}")

    # 1. Extract Query and Request ID
    query = (
        event.get("arguments", {}).get("query")
        or event.get("query")
        or "No query provided"
    )
    request_id = event.get("id")

    # 2. Generate Vector Embedding using Amazon Titan v2
    # We use 1024 dimensions to match our RDS pgvector settings
    bedrock = get_bedrock_client()
    embed_body = json.dumps({"inputText": query, "dimensions": 1024, "normalize": True})
    embed_resp = bedrock.invoke_model(
        modelId="amazon.titan-embed-text-v2:0", body=embed_body
    )
    embedding = json.loads(embed_resp["body"].read())["embedding"]

    # 3. Connect and Query RDS using pgvector similarity
    conn = get_db_connection()

    results = conn.run(
        """
        SELECT service_name, phone_number, live_chat_identifier, description
        FROM dept_contacts_v2
        ORDER BY embedding <=> :embed::vector LIMIT 3
        """,
        embed=str(embedding),
    )
    conn.close()

    # 4. Format results for the MCP-compatible response
    formatted_results = [
        {"service": r[0], "phone": r[1], "live_chat_identifier": r[2], "info": r[3]}
        for r in results
    ]

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "content": [{"type": "text", "text": json.dumps(formatted_results)}]
        },
    }
