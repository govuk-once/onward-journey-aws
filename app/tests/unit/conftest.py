import os
import sys
from pathlib import Path

# Resolve app directory
APP_ROOT = Path(__file__).resolve().parents[2]  # .../app

# Add app/shared so 'from integrations.factory import ...' resolves
SHARED_DIR = APP_ROOT / "shared"
if SHARED_DIR.is_dir() and str(SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(SHARED_DIR))

# Global environment defaults for unit test session
os.environ.setdefault("ENV_PREFIX", "unit")
os.environ.setdefault("AGENT_RUNTIME_ENDPOINT_URL", "agent.mock.aws")
os.environ.setdefault("BEDROCK_RUNTIME_ENDPOINT", "bedrock.mock.aws")
os.environ.setdefault("SECRETS_ENDPOINT_URL", "secrets.mock.aws")
os.environ.setdefault("GATEWAY_URL", "https://mock.gateway.aws")
os.environ.setdefault("GATEWAY_ENDPOINT_URL", "https://vpce-mock.aws")

# Client WebSocket Router & CRM Tool configuration
os.environ.setdefault(
    "AGENT_RUNTIME_ARN",
    "arn:aws:bedrock:eu-west-2:123456789012:runtime/agent-123",
)
os.environ.setdefault(
    "CRM_TOOL_LAMBDA_ARN",
    "arn:aws:lambda:eu-west-2:123456789012:function:crm-tool",
)
os.environ.setdefault("SESSIONS_TABLE_NAME", "unit-sessions")
