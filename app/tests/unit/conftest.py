import os

os.environ.setdefault("ENV_PREFIX", "unit")
os.environ.setdefault("AGENT_RUNTIME_ENDPOINT_URL", "agent.mock.aws")
os.environ.setdefault("BEDROCK_RUNTIME_ENDPOINT", "bedrock.mock.aws")
os.environ.setdefault("SECRETS_ENDPOINT_URL", "secrets.mock.aws")
os.environ.setdefault("GATEWAY_URL", "https://mock.gateway.aws")
os.environ.setdefault("GATEWAY_ENDPOINT_URL", "https://vpce-mock.aws")
