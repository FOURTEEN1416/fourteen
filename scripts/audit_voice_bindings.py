"""只读审计：角色音色绑定里「音色 owner ≠ 卡片 owner」的存量行。

背景（2026-10-30 语音域边界批）：写入口已强制 owner 校验，但**历史数据无法回溯**
——若修复前已有「私有卡绑了他人克隆音色」，`/voice/test` 与 chat 链
（`resolve_voice_spec` 只校验 W14 亲和）仍会真的用上他人音色。本脚本给出清单，
不改数据；清理需人工确认后走管理端重绑/解绑。

用法:
    python scripts/audit_voice_bindings.py           # 人读
    python scripts/audit_voice_bindings.py --json    # 机读

退出码: 0 = 无异常；1 = 发现疑似越权绑定（可直接做运维/CI 门禁）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shisi.voice.character_voice import (  # noqa: E402
    CharacterVoiceManager,
    audit_voice_owner_mismatches,
)
from utils.project_paths import project_path  # noqa: E402


def _card_owner_of(character_id: str) -> str:
    """卡片 id → 卡内 user_id（读不到/解析失败一律空串，不猜归属）。"""
    path = project_path("config", "characters") / f"{character_id}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(data.get("user_id") or "")


def main(argv: list[str]) -> int:
    rows = audit_voice_owner_mismatches(
        CharacterVoiceManager().list_bindings(), _card_owner_of,
    )
    if "--json" in argv:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    elif not rows:
        print("OK：未发现 owner 不一致的音色绑定")
    else:
        print(f"发现 {len(rows)} 条疑似越权绑定（音色 owner ≠ 卡片 owner）：")
        for row in rows:
            print(
                f"  - 角色 {row['character_id']}: voice_id={row['voice_id']} "
                f"（音色属于 {row['voice_owner']}，卡片属于 {row['card_owner']}）"
            )
        print("处理：确认后由 admin 在管理端重绑/解绑；本脚本不改数据。")
    return 1 if rows else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
