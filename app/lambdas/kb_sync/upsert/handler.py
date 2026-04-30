"""
KB Sync: Upsert Article Lambda.
Handles article chunking, embedding generation, and RDS loading for a SINGLE article.
"""

import os
import json
from typing import Dict, Any
from utils.db import get_db_connection
from utils.aws import get_bedrock_client

def get_embedding(bedrock_client, text):
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
    Receives a SINGLE article and overarching metadata.
    """
    art = event.get("article", {})
    kb_id = event.get("kb_identifier")
    remote_date = event.get("remote_modified_date")

    if not art:
        return {"status": "skipped", "message": "No article data provided"}

    bedrock = get_bedrock_client()
    conn = get_db_connection()

    try:
        # --- TRANSFORM: Chunking & Embedding ---
        # Future expansion: Add recursive character chunking here.
        text_to_embed = f"Title: {art['title']}. Content: {art['content']}"
        vector = get_embedding(bedrock, text_to_embed)
        vector_str = "[" + ",".join(map(str, vector)) + "]"

        # --- LOAD: Atomic Upsert ---
        conn.run("BEGIN")

        conn.run("""
            INSERT INTO knowledge_bases (external_id, title, content, kb_identifier, external_url, embedding)
            VALUES (:eid, :title, :content, :kb, :url, :emb::vector)
            ON CONFLICT (external_id) DO UPDATE SET
                title = EXCLUDED.title,
                content = EXCLUDED.content,
                embedding = EXCLUDED.embedding,
                kb_identifier = EXCLUDED.kb_identifier;
        """,
        eid=art["external_id"], title=art["title"], content=art["content"],
        kb=kb_id, url=art["external_url"], emb=vector_str)

        # TODO: Future enhancement: Move this metadata update to a dedicated 'Finalize' step
        # outside the Step Function Map state. Currently, if the batch fails halfway,
        # the 'last_modified' date is still updated, which might cause the next sync
        # to skip the remaining failed articles.
        if remote_date:
            conn.run("""
                INSERT INTO sync_kb_metadata (kb_identifier, last_modified)
                VALUES (:kb, :date)
                ON CONFLICT (kb_identifier) DO UPDATE SET last_modified = EXCLUDED.last_modified;
            """, kb=kb_id, date=remote_date)

        conn.run("COMMIT")

        return {
            "status": "success",
            "external_id": art["external_id"],
            "kb_identifier": kb_id
        }

    except Exception as e:
        conn.run("ROLLBACK")
        print(f"UPSERT ERROR [{art.get('external_id')}]: {str(e)}")
        raise e
    finally:
        conn.close()
