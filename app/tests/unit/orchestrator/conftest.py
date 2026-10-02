import os

# =====================================================================
# GLOBAL UNIT TEST ENVIRONMENT SETUP
# Pytest runs this file before importing any test modules in this directory.
# This prevents import-time crashes for missing infrastructure variables.
# =====================================================================

os.environ["ENV_PREFIX"] = "unit"
os.environ["AGENT_RUNTIME_ENDPOINT_URL"] = "agent.mock.aws"
os.environ["BEDROCK_RUNTIME_ENDPOINT"] = "bedrock.mock.aws"
os.environ["SECRETS_ENDPOINT_URL"] = "secrets.mock.aws"
os.environ["GATEWAY_URL"] = "https://mock.gateway.aws"
os.environ["GATEWAY_ENDPOINT_URL"] = "https://vpce-mock.aws"
