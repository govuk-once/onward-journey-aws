"""
RDS Tool Lambda.

This Lambda function acts as a semantic search tool for the GOV.UK Contact
Assistant. It processes queries forwarded by the AgentCore Gateway,
generates vector embeddings for the query text, and performs high-speed
vector similarity searches (using pgvector) against the RDS database.

Supported Methods:
    - query_department_database: Searches for general department contact
      details and service descriptions.
    - query_knowledge_base: Searches for specific policy and guidance
      articles within a department's knowledge base.
"""

import json
from utils.db import get_db_connection
from utils.aws import get_bedrock_client

def lambda_handler(event, context):
    """
    Entry point for RDS search tool requests, routing to the appropriate search method.

    Args:
        event (dict): The Lambda event object from the MCP Gateway, containing:
            - method (str): The search method to execute.
            - arguments (dict): Search parameters including:
                - query (str): The natural language query to search for.
                - kb_identifier (str, optional): Required for KB searches.
            - id (str|int): A unique identifier for the request.
        context (LambdaContext): AWS Lambda context object.

    Returns:
        dict: A JSON-RPC 2.0 formatted dictionary containing the search results
            as a JSON-serialised string within an MCP content block.
    """

    print(f"📥 GATEWAY EVENT: {json.dumps(event)}")

    # 1. Extract the Tool Name from the Gateway Context
    custom_context = {}
    if hasattr(context, 'client_context') and context.client_context:
        custom_context = context.client_context.custom or {}

    full_method = custom_context.get("bedrockAgentCoreToolName", "")
    request_id = custom_context.get("bedrockAgentCoreMcpMessageId", "fallback-id")

    # Clean the prefix
    method = full_method.split("___")[-1] if "___" in full_method else full_method
    print(f"🔍 RESOLVED METHOD: {method}")

    # 2. Extract arguments
    query = event.get("query", "No query provided")
    kb_identifier = event.get("kb_identifier")

    # 3. Generate Vector Embedding using Amazon Titan v2
    # We use 1024 dimensions and normalisation to match our RDS pgvector settings
    bedrock = get_bedrock_client()
    embed_body = json.dumps({"inputText": query, "dimensions": 1024, "normalize": True})
    embed_resp = bedrock.invoke_model(
        modelId="amazon.titan-embed-text-v2:0",
        body=embed_body,
        contentType="application/json",
        accept="application/json"
    )
    embedding = json.loads(embed_resp["body"].read())["embedding"]

    # 4. Connect and Query RDS using pgvector similarity
    conn = get_db_connection()

    try:
        if "query_knowledge_base" in method:
            # KB Search: Filter by the specific department KB identifier
            if not kb_identifier:
                print(f"❌ [ERROR] kb_identifier is required for {method}")
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32602, "message": "kb_identifier is required"}
                }

            results = conn.run(
                """
                SELECT title, content, external_url
                FROM knowledge_base_articles
                WHERE kb_identifier = :kb_identifier
                ORDER BY embedding <=> :embed::vector LIMIT 3
                """,
                kb_identifier=kb_identifier,
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

    # 5. Format results for the MCP-compatible response
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "content": [{"type": "text", "text": json.dumps(formatted_results)}]
        },
    }
