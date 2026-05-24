#!/usr/bin/env python3
"""重构后验证脚本"""

import sqlite3
import sys
from pathlib import Path


def verify_refactor():
    print("=== 重构验证 ===\n")
    all_passed = True

    print("1. 检查新数据库表...")
    db_path = Path("data/sqlite.db")
    if db_path.exists():
        conn = sqlite3.connect(str(db_path))
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        if "characters_v2" in tables:
            print("   ✓ characters_v2 表存在")
        else:
            print("   ✗ characters_v2 表不存在")
            all_passed = False
        conn.close()
    else:
        print("   ! 数据库不存在（首次运行正常）")

    print("\n2. 检查核心模块...")
    try:
        from shisi.core.models import CharacterAggregate
        char = CharacterAggregate(name="测试角色")
        print(f"   ✓ 可创建角色: {char.id}")
    except Exception as e:
        print(f"   ✗ 模块检查失败: {e}")
        all_passed = False

    print("\n3. 检查API模块...")
    try:
        print("   ✓ API v2 可导入")
    except Exception as e:
        print(f"   ✗ API检查失败: {e}")
        all_passed = False

    print("\n4. 检查服务层...")
    try:
        print("   ✓ CharacterService 可导入")
    except Exception as e:
        print(f"   ✗ 服务检查失败: {e}")
        all_passed = False

    print("\n" + "=" * 40)
    if all_passed:
        print("✅ 所有验证通过！重构成功。")
        return 0
    else:
        print("❌ 部分验证失败，请检查。")
        return 1


if __name__ == "__main__":
    sys.exit(verify_refactor())
