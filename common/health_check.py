from __future__ import annotations

import logging

logger = logging.getLogger("health_check")


def health_check_all(components: dict) -> bool:
    all_ok = True
    print("\n[健康检查]")
    for name, component in components.items():
        if hasattr(component, "health_check"):
            try:
                status = component.health_check()
                ok = True
                if isinstance(status, dict):
                    ok = not any(v is False for v in status.values())
                print(f"  {'✅' if ok else '❌'} {name}")
                if not ok:
                    logger.warning("%s health check failed: %s", name, status)
                    all_ok = False
            except Exception as e:
                print(f"  ❌ {name} (error: {e})")
                all_ok = False
        else:
            print(f"  ✅ {name} (no check)")
    if all_ok:
        print("\n  ✅ 全部通过\n")
    else:
        print("\n  ⚠️ 部分组件异常\n")
    return all_ok
