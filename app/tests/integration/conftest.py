import sys
import os

# =====================================================================
# DEEPEVAL PATH RESOLUTION
# Isolated to integration tests.
# =====================================================================
integration_dir = os.path.dirname(os.path.abspath(__file__))

# Go up 3 levels: integration -> tests -> app -> root
project_root = os.path.abspath(os.path.join(integration_dir, "../../../"))
orchestrator_root = os.path.join(project_root, "app", "agentcore", "orchestrator")

if project_root not in sys.path:
    sys.path.insert(0, project_root)
if orchestrator_root not in sys.path:
    sys.path.insert(0, orchestrator_root)
