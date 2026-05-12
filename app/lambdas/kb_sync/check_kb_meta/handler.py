"""
KB Sync: Check KB Metadata Lambda.

This Lambda is part of the Knowledge Base (KB) synchronisation workflow.
Its role is to fetch the latest 'modified' timestamp from the remote KB
provider (e.g., Genesys) to determine if a local sync is required.
"""

import json
from integrations.factory import ProviderFactory, Capability

def lambda_handler(event, context):
    """
    Fetches the remote modification date for a specific Knowledge Base.

    Args:
        event (dict): The Lambda event object, expected to contain:
            - kb_identifier (str): Unique identifier for the Knowledge Base.
        context (LambdaContext): AWS Lambda context object.

    Returns:
        dict: A dictionary containing:
            - kb_identifier (str): The KB ID queried.
            - remote_modified_date (str): ISO 8601 timestamp of remote change.
            - platform (str): The name of the provider platform (e.g., 'genesys').

    Raises:
        Exception: If the provider cannot be instantiated or metadata fetch fails.
    """
    print(f"Received event: {json.dumps(event)}")
    kb_identifier = event.get("kb_identifier")

    try:
        provider = ProviderFactory.get_provider(kb_identifier, Capability.KB_SYNC_META)
        remote_date = provider.fetch_remote_modified_date()

        print(f"KB {kb_identifier}: Remote modified date is {remote_date}")

        return {
            "kb_identifier": kb_identifier,
            "remote_modified_date": remote_date,
            "platform": provider.config["platform"]
        }
    except Exception as e:
        print(f"❌ [ERROR] KB Metadata Check Failure: {str(e)}")
        raise
