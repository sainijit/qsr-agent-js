import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for svc in ("gateway", "persister", "ai_router", "dispatch", "dashboard_api", "lead_service", "agent_worker", "followup_scheduler"):
    sys.path.insert(0, str(ROOT / "services" / svc))
sys.path.insert(0, str(ROOT / "libs" / "zuvo_common"))

os.environ.setdefault("JWT_SECRET", "unit-test-secret-unit-test-secret-32b")
os.environ.setdefault("META_APP_SECRET", "unit-test-app-secret")
os.environ.setdefault("META_VERIFY_TOKEN", "unit-verify")
os.environ.setdefault("TENANT_ROUTING_PATH", str(ROOT / "config" / "tenant-routing.yaml"))
os.environ.setdefault("TENANT_POLICY_ROOT", str(ROOT))
os.environ.setdefault("ENABLE_SIMULATOR", "true")

JAIPUR = "3f6c2a10-6d1e-4f0b-9a51-1c2d3e4f5a01"
DELHI = "7b9e4c20-2a3f-4d5e-8f61-2b3c4d5e6f02"
