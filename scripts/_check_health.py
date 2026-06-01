"""Verify health_check passes with lazy-load filter."""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.basicConfig(level=logging.WARNING)

from main import OptimizedOrchestrator

o = OptimizedOrchestrator()
o.initialize(config_dir="config")
h = o.health_check()
print(f"healthy: {h['healthy']}")

failing = []
for k, v in h.get("components", {}).items():
    if isinstance(v, dict):
        for kk, vv in v.items():
            if isinstance(vv, bool) and not vv:
                failing.append(f"{k}.{kk}")
print(f"failing: {failing if failing else '(none)'}")
