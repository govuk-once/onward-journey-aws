import os
import json
import pg8000.native
from .aws import get_secrets_client

def get_db_password() -> str:
    """Retrieves the DB password, handling both JSON and raw string formats."""
    secrets_client = get_secrets_client()
    secret_arn = os.environ["DB_SECRET_ARN"]

    response = secrets_client.get_secret_value(SecretId=secret_arn)
    raw_value = response["SecretString"].strip()

    # 1. If it looks like JSON, parse it and get the 'password' key
    if raw_value.startswith("{"):
        try:
            db_password_data = json.loads(raw_value)
            return str(db_password_data.get("password"))
        except json.JSONDecodeError:
            # Fallback if it's a weird string that happens to start with {
            return str(raw_value)

    # 2. Otherwise, it's the raw password string
    return str(raw_value)

def get_db_connection():
    """Establishes a connection to the RDS PostgreSQL database."""
    print(f"Connecting to {os.environ['DB_HOST']}...")
    return pg8000.native.Connection(
        user=os.environ["DB_USER"],
        password=get_db_password(),
        host=os.environ["DB_HOST"],
        database=os.environ["DB_NAME"],
        port=5432,
        timeout=120,
        tcp_keepalive=True,
    )
