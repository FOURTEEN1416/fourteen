import traceback
import sys
import os

os.environ["USE_SHISI_MEMORY"] = "true"
os.environ["USE_SHISI_RAG"] = "true"

# Try to trace where 'memory' import happens
original_import = __builtins__.__import__

def tracing_import(name, *args, **kwargs):
    if name == "memory" or name.startswith("memory."):
        print(f"!!! IMPORT DETECTED: {name}", file=sys.stderr)
        traceback.print_stack(file=sys.stderr)
    return original_import(name, *args, **kwargs)

__builtins__.__import__ = tracing_import

try:
    from main import OptimizedOrchestrator
    orch = OptimizedOrchestrator()
    orch.initialize(config_dir="config")
    print("INIT OK")
except SystemExit as e:
    print(f"SystemExit: {e}")
except Exception as e:
    traceback.print_exc()
