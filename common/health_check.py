from __future__ import annotations

import logging

logger = logging.getLogger("health_check")


def _is_healthy(result) -> bool:
    if isinstance(result, bool):
        return result
    if isinstance(result, dict):
        if "healthy" in result:
            return bool(result["healthy"])
        if "status" in result:
            return result["status"] in ("healthy", "ok", True)
        if any(v is False for v in result.values() if isinstance(v, bool)):
            logger.warning("Health check dict has False values but no explicit healthy/status key")
            return False
        return True
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
            except Exception as e:
                print(f"  [FAIL] {name} (error: {e})")
                all_ok = False
        else:
            print(f"  [OK] {name}")
    if all_ok:
        print("\n  [OK] 全部通过\n")
    else:
        print("\n  [WARN] 部分组件异常\n")
    return all_ok
