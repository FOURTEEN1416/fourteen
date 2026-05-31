from __future__ import annotations

import logging

logger = logging.getLogger("health_check")

_HEALTH_OK_KEYS = {"base_prompt_cached", "evolution_count", "original_anchors",
                   "anchor_integrity", "chromadb", "prompt_mode"}


def _is_healthy(result) -> bool:
    if isinstance(result, bool):
        return result
    if isinstance(result, dict):
        if "healthy" in result:
            return bool(result["healthy"])
        if "status" in result:
            return result["status"] in ("healthy", "ok", True)
        non_ok = any(v is False for k, v in result.items()
                     if isinstance(v, bool) and k not in _HEALTH_OK_KEYS)
        if non_ok:
            logger.warning("Health check dict has False values: %s",
                           {k: v for k, v in result.items() if v is False})
            return False
        return not result.get("error")
    return True


def health_check_all(components: dict) -> bool:
    all_ok = True
    print("\n[健康检查]")
    for name, component in components.items():
        if hasattr(component, "health_check"):
            try:
                status = component.health_check()
                ok = _is_healthy(status)
                print(f"  {'[OK]' if ok else '[FAIL]'} {name}")
                if not ok:
                    logger.warning("%s health check failed: %s", name, status)
                    all_ok = False
            except Exception as e:  # noqa: BLE001
                print(f"  [FAIL] {name} (error: {e})")
                all_ok = False
        else:
            print(f"  [OK] {name}")
    if all_ok:
        print("\n  [OK] 全部通过\n")
    else:
        print("\n  [WARN] 部分组件异常\n")
    return all_ok
