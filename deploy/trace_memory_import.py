"""Trace memory module imports to find the failing source."""
import os
import sys

os.environ["USE_SHISI_MEMORY"] = "true"
os.environ["USE_SHISI_RAG"] = "true"

# Monkey-patch __import__ to trace memory imports
_orig_import = __builtins__.__import__ if isinstance(__builtins__, dict) else __builtins__.__import__
_trace = []

def _traced_import(name, *args, **kwargs):
    if "memory" in name and not name.startswith("shisi") and not name.startswith("collections"):
        import traceback
        _trace.append(f"IMPORT: {name}")
        _trace.append("".join(traceback.format_stack()[-5:-1]))
    return _orig_import(name, *args, **kwargs)

if isinstance(__builtins__, dict):
    __builtins__["__import__"] = _traced_import
else:
    __builtins__.__import__ = _traced_import

try:
    from main import OptimizedOrchestrator
    print("IMPORT OK")
except Exception as e:
    print(f"IMPORT FAILED: {type(e).__name__}: {e}")
    print("\n--- TRACE ---")
    for line in _trace:
        print(line)
    print("--- END TRACE ---")
    sys.exit(1)
