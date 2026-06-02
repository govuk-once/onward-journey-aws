"""
KB Sync: Upsert Article Lambda.

This Lambda is the final processing stage for a single Knowledge Base article
within the synchronisation workflow. It transforms the article text into
vector embeddings using Amazon Bedrock and performs an atomic upsert into
the local RDS database.
"""

import json
from utils.db import get_db_connection
from utils.aws import get_bedrock_client

def get_embedding(bedrock_client, text):
    """
    Generates a vector embedding for the provided text using Amazon Bedrock.
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
    Processes a single article: generates embeddings and upserts into the database.

    Args:
        event (dict): The Lambda event object, expected to contain:
            - article (dict): The article data (title, content, external_id, etc.).
            - kb_identifier (str): Unique identifier for the parent Knowledge Base.
            - remote_modified_date (str, optional): The timestamp of the remote KB.
        context (LambdaContext): AWS Lambda context object.

    Returns:
        dict: A status report containing the success state and article/KB identifiers.

    Raises:
        Exception: If embedding generation or database operations fail.
    """
    art = event.get("article", {})
    kb_identifier = event.get("kb_identifier")
    remote_date = event.get("remote_modified_date")

    if not art:
        return {"status": "skipped", "message": "No article data provided"}

    bedrock = get_bedrock_client()
    conn = get_db_connection()

    try:
        # --- TRANSFORM: Chunking & Embedding ---
        text_to_embed = f"Title: {art['title']}. Content: {art['content']}"
        vector = get_embedding(bedrock, text_to_embed)
        vector_str = "[" + ",".join(map(str, vector)) + "]"

        # --- LOAD: Atomic Upsert ---
        conn.run("BEGIN")

        conn.run("""
            INSERT INTO knowledge_base_articles (external_id, title, content, kb_identifier, external_url, embedding)
            VALUES (:eid, :title, :content, :kb_identifier, :url, :emb::vector)
            ON CONFLICT (external_id) DO UPDATE SET
                title = EXCLUDED.title,
                content = EXCLUDED.content,
                embedding = EXCLUDED.embedding,
                kb_identifier = EXCLUDED.kb_identifier;
        """,
        eid=art["external_id"], title=art["title"], content=art["content"],
        kb_identifier=kb_identifier, url=art["external_url"], emb=vector_str)

        conn.run("COMMIT")

        return {
            "status": "success",
            "external_id": art["external_id"],
            "kb_identifier": kb_identifier
        }

    except Exception as e:
        conn.run("ROLLBACK")
        print(f"UPSERT ERROR [{art.get('external_id')}]: {str(e)}")
        raise e
    finally:
        conn.close()
