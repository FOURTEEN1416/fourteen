"""Test orchestrator initialize() to find the real failure."""
import os
import sys
import traceback

os.environ["USE_SHISI_MEMORY"] = "true"
os.environ["USE_SHISI_RAG"] = "true"

print("ENV: USE_SHISI_MEMORY =", os.environ.get("USE_SHISI_MEMORY"))
print("ENV: USE_SHISI_RAG =", os.environ.get("USE_SHISI_RAG"))

try:
    from main import OptimizedOrchestrator
    print("STEP 1: import OK")
    orch = OptimizedOrchestrator()
    print("STEP 2: instantiate OK")
    ok = orch.initialize(config_dir="config")
    print(f"STEP 3: initialize() returned {ok}")
    if not ok:
        sys.exit(1)
except ModuleNotFoundError as e:
    print(f"MODULE NOT FOUND: {e}")
    traceback.print_exc()
    sys.exit(1)
except Exception as e:
    print(f"EXCEPTION: {type(e).__name__}: {e}")
    traceback.print_exc()
    sys.exit(1)
