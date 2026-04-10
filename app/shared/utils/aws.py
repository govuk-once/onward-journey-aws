import os
import boto3

def get_secrets_client():
    """Returns a Secrets Manager client configured with VPC endpoint if available."""
    endpoint_url = os.environ.get("SECRETS_ENDPOINT_URL")
    return boto3.client(
        "secretsmanager",
        region_name="eu-west-2",
        endpoint_url=f"https://{endpoint_url}" if endpoint_url else None
    )

def get_bedrock_client():
    """Returns a Bedrock Runtime client configured with VPC endpoint if available."""
    endpoint_url = os.environ.get("BEDROCK_RUNTIME_ENDPOINT")
    return boto3.client(
        "bedrock-runtime",
        region_name="eu-west-2",
        endpoint_url=f"https://{endpoint_url}" if endpoint_url else None
    )
