import os
import json
import boto3
import pg8000.native
from .aws import get_secrets_client

def get_db_password(secret_arn: str = None) -> str:
    """Retrieves the DB password, handling both JSON and raw string formats."""
    secrets_client = get_secrets_client()
    target_arn = secret_arn or os.environ.get("DB_SECRET_ARN")

    if not target_arn:
        raise KeyError("DB_SECRET_ARN environment variable is missing.")

    response = secrets_client.get_secret_value(SecretId=target_arn)
    raw_value = response["SecretString"].strip()

    if raw_value.startswith("{"):
        try:
            db_password_data = json.loads(raw_value)
            return str(db_password_data.get("password"))
        except json.JSONDecodeError:
            return str(raw_value)

    return str(raw_value)

def get_db_connection():
    """Establishes a connection using either Secrets Manager or IAM Database Authentication."""
    host = os.environ["DB_HOST"]
    user = os.environ["DB_USER"]
    database = os.environ["DB_NAME"]
    secret_arn = os.environ.get("DB_SECRET_ARN")

    print(f"Connecting to {host} as user {user}...")

    # If no secret ARN is provided, dynamically generate a short-lived IAM Auth Token
    if not secret_arn:
        print("Using AWS IAM Database Authentication token.")
        rds_client = boto3.client("rds", region_name=os.environ.get("AWS_REGION", "eu-west-2"))
        db_password = rds_client.generate_db_auth_token(
            DBHostname=host,
            Port=5432,
            DBUsername=user
        )
    else:
        print("Using Secrets Manager static password entry.")
        db_password = get_db_password(secret_arn)

    return pg8000.native.Connection(
        user=user,
        password=db_password,
        host=host,
        database=database,
        port=5432,
        timeout=120,
        tcp_keepalive=True,
        ssl_context=True # IAM Auth requires SSL
    )
