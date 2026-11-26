import os
import sys
from pathlib import Path

os.environ.setdefault("ENV_PREFIX", "localtest")
os.environ.setdefault("GATEWAY_URL", "https://mock.gateway.com")
os.environ.setdefault("GATEWAY_ENDPOINT_URL", "https://vpce-mock.aws.com")
os.environ.setdefault("MEMORY_ID", "mock-memory-id")
os.environ.setdefault("LOG_LEVEL", "INFO")

# =====================================================================
# DEEPEVAL PATH RESOLUTION
# Isolated to integration tests so `deepeval test run` resolves imports
# consistently alongside pytest's `pythonpath` config.
# =====================================================================
REPO_ROOT = Path(__file__).resolve().parents[3]
ORCHESTRATOR_ROOT = REPO_ROOT / "app" / "agentcore" / "orchestrator"

for path in (REPO_ROOT, ORCHESTRATOR_ROOT):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)
