#!/usr/bin/env python3
"""重构前预检脚本 — 检查系统健康状态"""

import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path


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

    char_dir = Path("data/characters")
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
        print("! data/characters/ 目录不存在（跳过检查）")

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

    # 检查 JWT_SECRET 是否已设置
    jwt_secret = os.environ.get("JWT_SECRET", "")
    if not jwt_secret:
        issues.append("JWT_SECRET 未设置，JWT 令牌将使用不安全默认值")
    elif len(jwt_secret) < 16:
        issues.append("JWT_SECRET 长度过短（至少 16 字符）")
    else:
        print("✓ JWT_SECRET 已配置")

    # 检查 API_KEY 是否为默认值
    api_key = os.environ.get("API_KEY", "")
    default_api_keys = {"", "changeme", "default", "test"}
    if api_key in default_api_keys:
        issues.append("API_KEY 未设置或为默认值，存在安全风险")
    else:
        print("✓ API_KEY 已配置")

    # 检查 AI_GF_ENV 是否为 prod
    ai_gf_env = os.environ.get("AI_GF_ENV", os.environ.get("ENV", ""))
    if ai_gf_env and ai_gf_env.lower() not in ("prod", "production"):
        print(f"⚠ AI_GF_ENV={ai_gf_env}（建议生产环境设为 prod）")
    elif ai_gf_env:
        print("✓ AI_GF_ENV=prod")
    else:
        issues.append("AI_GF_ENV 未设置，无法确认环境类型")

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
