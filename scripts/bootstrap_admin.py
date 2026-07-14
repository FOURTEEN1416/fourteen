#!/usr/bin/env python3
"""
引导脚本 — 创建第一个管理员用户

用法:
    python scripts/bootstrap_admin.py                    # 随机生成密码
    python scripts/bootstrap_admin.py --password MySecurePass123  # 指定密码

环境变量:
    ADMIN_EMAIL, ADMIN_PASSWORD 可替代命令行参数

设计说明:
    项目要求 admin 角色才能访问管理后台（POST /api/auth/register 默认 role=viewer），
    因此第一个 admin 用户无法通过 API 创建，必须通过本脚本绕过权限检查。
    本脚本只会在数据库中没有 admin 用户时创建，已存在则跳过。
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

# 确保项目根在 sys.path
_project_root = Path(__file__).parent.parent.absolute()
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from api.database import User, _async_session, Base  # noqa: E402


def _hash_password(password: str) -> str:
    """哈希密码（绕过 passlib 兼容性问题，直接调 bcrypt）"""
    import bcrypt
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

logger = logging.getLogger("bootstrap_admin")

DEFAULT_EMAIL = "admin@fourteen.local"
DEFAULT_USERNAME = "admin"
DEFAULT_DISPLAY_NAME = "系统管理员"


def _get_password(args_password: str | None) -> str:
    """获取管理员密码：优先命令行参数，其次环境变量，最后生成随机密码"""
    import secrets
    import string
    if args_password:
        return args_password
    env_pass = os.environ.get("ADMIN_PASSWORD")
    if env_pass:
        return env_pass
    # 生成随机密码
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    password = ''.join(secrets.choice(alphabet) for _ in range(20))
    print(f"[SECURITY] 已生成随机管理员密码: {password}")
    print("[SECURITY] 请妥善保存，此密码不会再次显示。")
    return password


async def bootstrap_admin(email: str, password: str, username: str, display_name: str) -> bool:
    """创建第一个 admin 用户（如已存在则跳过）。返回是否新创建。"""
    async with _async_session() as session:
        # 检查是否已有 admin 用户
        from sqlalchemy import select
        result = await session.execute(
            select(User).where(User.role == "admin").limit(1)
        )
        existing = result.scalar_one_or_none()

        if existing:
            logger.info("已有管理员用户: %s (%s, role=%s)", existing.email, existing.username, existing.role)
            return False

        # 创建 admin 用户
        user = User(
            email=email,
            username=username,
            hashed_password=_hash_password(password),
            display_name=display_name,
            role="admin",
            is_active=True,
            is_verified=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        logger.info("✅ 管理员用户创建成功: %s (id=%d, role=%s)", user.email, user.id, user.role)
        return True


def main():
    parser = argparse.ArgumentParser(description="引导创建第一个管理员用户")
    parser.add_argument("--email", default=os.environ.get("ADMIN_EMAIL", DEFAULT_EMAIL))
    parser.add_argument("--password", default=None,
                        help="管理员密码（不提供则从环境变量 ADMIN_PASSWORD 或随机生成）")
    parser.add_argument("--username", default=DEFAULT_USERNAME)
    parser.add_argument("--display-name", default=DEFAULT_DISPLAY_NAME)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # 安全获取密码：优先命令行参数，其次环境变量，最后随机生成
    password = _get_password(args.password)

    # 先确保表存在
    asyncio.run(_ensure_tables())

    created = asyncio.run(
        bootstrap_admin(
            email=args.email,
            password=password,
            username=args.username,
            display_name=args.display_name,
        )
    )

    if created:
        print(f"\n[OK] 管理员账号已创建！")
        print(f"   邮箱: {args.email}")
        print(f"   角色: admin")
        print(f"\n => 访问 http://localhost:5173/login 登录")
    else:
        print(f"\n[i] 管理员用户已存在，跳过创建。")
        print(f"   邮箱: {args.email}")
        print(f"\n => 访问 http://localhost:5173/login 登录")

    return 0 if created else 0


async def _ensure_tables():
    """确保数据库表已创建"""
    from sqlalchemy import inspect

    async with _async_session() as session:
        async with session.bind.connect() as conn:
            tables_exist = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).has_table("users")
            )
            if not tables_exist:
                await conn.run_sync(Base.metadata.create_all)
                logger.info("数据库表已自动创建")


if __name__ == "__main__":
    sys.exit(main())
