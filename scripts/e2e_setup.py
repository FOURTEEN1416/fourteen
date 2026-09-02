"""SP-12 E2E 前置：独立数据库 + 种子 admin 账号（零污染本地/生产数据）。

用法：python scripts/e2e_setup.py
产物：data/e2e_users.db（users 表含 admin / E2eTest#2026）
后端启动：APP_DATABASE_URL="sqlite+aiosqlite:///data/e2e_users.db" API_KEY_ENABLED=false \
          python -m uvicorn api.run_api:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.absolute()))

E2E_DB = Path("data/e2e_users.db")
E2E_URL = f"sqlite+aiosqlite:///data/e2e_users.db"
ADMIN_EMAIL = "e2e-admin@test.local"
ADMIN_USER = "e2e-admin"
ADMIN_PASSWORD = "E2eTest#2026"


def main() -> None:
    os.environ["APP_DATABASE_URL"] = E2E_URL
    E2E_DB.parent.mkdir(parents=True, exist_ok=True)
    if E2E_DB.exists():
        E2E_DB.unlink()
    from api.database import User, init_db  # noqa: E402

    import asyncio

    async def _seed() -> None:
        await init_db()
        from api.database import _async_session as async_session
        from api.auth_jwt import hash_password

        async with async_session() as session:
            session.add(
                User(
                    email=ADMIN_EMAIL,
                    username=ADMIN_USER,
                    hashed_password=hash_password(ADMIN_PASSWORD),
                    role="admin",
                    is_active=True,
                    is_verified=True,
                )
            )
            await session.commit()

    asyncio.run(_seed())
    print(f"E2E DB ready: {E2E_DB} | admin: {ADMIN_EMAIL} / {ADMIN_PASSWORD}")


if __name__ == "__main__":
    main()
