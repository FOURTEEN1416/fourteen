#!/usr/bin/env python3
"""重构前预检脚本 — 检查系统健康状态"""

import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

# 项目根入 path，便于导入 runtime_config 真源
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from api.runtime_config import (  # noqa: E402
    UNSAFE_API_KEYS,
    is_explicit_dev,
    is_production,
    resolve_api_key_enabled,
)

# JWT 最低长度与 api/auth_jwt.py 生产门一致（审查 F-low-4：两处阈值曾不一致）
_JWT_MIN_LEN = 32


def preflight_check():
    issues = []

    db_path = Path("data/sqlite.db")
    try:
        conn = sqlite3.connect(str(db_path))
        conn.execute("SELECT 1")
        conn.close()
        print("✓ 数据库连接正常")
    except Exception as e:  # noqa: BLE001
        issues.append(f"数据库连接失败: {e}")

    char_dir = Path("config/characters")
    if char_dir.exists():
        json_files = list(char_dir.glob("*.json"))
        print(f"✓ 发现 {len(json_files)} 个角色文件")
        for f in json_files[:5]:
            try:
                import json
                with open(f, encoding="utf-8") as fp:
                    json.load(fp)
            except Exception as e:  # noqa: BLE001
                issues.append(f"角色文件损坏 {f}: {e}")
    else:
        print("! config/characters/ 目录不存在（跳过检查）")
        print("  注：config/characters/ 在 .gitignore 中，公开仓克隆不会自带角色卡；部署需单独投递")

    stat = shutil.disk_usage(".")
    free_gb = stat.free / (1024**3)
    if free_gb < 1:
        issues.append(f"磁盘空间不足: {free_gb:.2f}GB")
    else:
        print(f"✓ 磁盘空间充足: {free_gb:.2f}GB")

    result = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
    if result.stdout.strip():
        issues.append("有未提交的更改，请先提交")
    else:
        print("✓ Git工作区干净")

    # ── 安全配置检查 ──
    print("\n--- 安全配置检查 ---")

    _prod = is_production()
    _dev = is_explicit_dev()
    print(f"环境判定: production={_prod} explicit_dev={_dev} "
          f"(AI_GF_ENV/APP_ENV/ENV 真源)")

    # 检查 JWT_SECRET 是否已设置（与 auth_jwt 同阈值；fail-closed 语义）
    jwt_secret = os.environ.get("JWT_SECRET", "").strip()
    if not jwt_secret:
        if _dev:
            print("⚠ JWT_SECRET 未设置（显式 dev 允许 DEV 兜底，不安全）")
        else:
            issues.append(
                "JWT_SECRET 未设置。非显式 dev 环境下 api.auth_jwt 将拒绝启动（fail-closed）。"
                "生成：openssl rand -base64 48"
            )
    elif len(jwt_secret) < _JWT_MIN_LEN:
        issues.append(f"JWT_SECRET 长度过短（至少 {_JWT_MIN_LEN} 字符，与 auth_jwt 生产门一致）")
    else:
        print("✓ JWT_SECRET 已配置")

    # 检查 API_KEY：占位符/弱默认 + 认证启用状态（审查 F-high-1）
    api_key = os.environ.get("API_KEY", "").strip()
    api_key_enabled = resolve_api_key_enabled()  # 口径唯一真源

    if api_key_enabled and api_key in UNSAFE_API_KEYS:
        if _dev:
            print("⚠ API_KEY 为公开占位符/弱默认（显式 dev 允许，不安全）")
        else:
            issues.append(
                "API_KEY 未设置或为公开占位符/弱默认"
                "（含 CHANGE_ME_TO_STRONG_RANDOM_KEY_32_CHARS_MIN），"
                "且 API Key 认证已启用。非显式 dev 时应用启动将 fail-closed。"
                "请设置：openssl rand -base64 32"
            )
    elif api_key_enabled and api_key:
        print("✓ API_KEY 已配置")
    else:
        print("! API Key 认证未启用（API_KEY_ENABLED=false）")

    # 检查环境标记
    ai_gf_env = os.environ.get("AI_GF_ENV") or os.environ.get("APP_ENV") or os.environ.get("ENV") or ""
    if ai_gf_env and ai_gf_env.lower() not in ("prod", "production"):
        print(f"⚠ 环境标记={ai_gf_env}（生产部署建议设为 prod）")
    elif ai_gf_env:
        print("✓ 环境标记=prod")
    else:
        if _dev:
            print("✓ 环境标记未设但检测到显式 dev 路径")
        else:
            issues.append(
                "AI_GF_ENV/APP_ENV/ENV 未设置：非显式 dev，JWT/API 占位符路径均 fail-closed。"
                "生产设 AI_GF_ENV=prod；本地开发设 AI_GF_ENV=dev。"
            )

    if issues:
        print("\n❌ 检查失败，请解决以下问题:")
        for issue in issues:
            print(f"  - {issue}")
        sys.exit(1)
    else:
        print("\n✅ 所有检查通过，可以开始重构")
        return True


if __name__ == "__main__":
    preflight_check()
