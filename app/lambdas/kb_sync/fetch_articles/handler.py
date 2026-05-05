"""
KB Sync: Fetch Articles Lambda.

This Lambda is part of the Knowledge Base (KB) synchronization workflow.
It retrieves all relevant articles from the configured remote KB provider
(e.g., Genesys) for downstream processing and synchronization.
"""

import json
from integrations.factory import ProviderFactory, Capability

def lambda_handler(event, context):
    """
    Fetches articles from the remote Knowledge Base provider.

    Args:
        event (dict): The Lambda event object, expected to contain:
            - kb_identifier (str): Unique identifier for the Knowledge Base.
        context (LambdaContext): AWS Lambda context object.

    Returns:
        dict: A dictionary containing:
            - articles (list): A list of article objects retrieved from the provider.

    Raises:
        Exception: If the provider cannot be instantiated or article fetching fails.
    """
    print(f"Received event: {json.dumps(event)}")
    kb_id = event.get("kb_identifier")

    try:
        provider = ProviderFactory.get_provider(kb_id, Capability.KB_FETCH)

        articles = provider.fetch_articles()

        print(f"KB {kb_id}: Fetched {len(articles)} articles.")
        return {
            "articles": articles
        }
    except Exception as e:
        print(f"❌ [ERROR] KB Article Fetch Failure: {str(e)}")
        raise
