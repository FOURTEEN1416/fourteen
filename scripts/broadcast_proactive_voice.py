"""主动语音消息广播（2026-11-08 用户裁决：「催动主动消息，给每一个用户发一条用语音的主动消息」）。

为什么是脚本而不是调度器开关：微信 iLink 的**会话窗口 24h TTL** 是硬约束
（v1.52 已定性）——窗口关闭的用户收不到任何消息，广播必须在「有没有活的
context_token」这一层先分流；同时这是一次性/可重复的手动催动，不需要常驻
调度逻辑。默认 **dry-run**，加 ``--execute`` 才真发。

用法（服务器上，仓库根目录）::

    .venv-cpu/bin/python scripts/broadcast_proactive_voice.py            # 干跑，只打印计划
    .venv-cpu/bin/python scripts/broadcast_proactive_voice.py --execute  # 真发（仅窗口内用户）
    .venv-cpu/bin/python scripts/broadcast_proactive_voice.py --execute --include-stale
                                                                         # 连过期窗口也尝试（平台必拒）

安全与隐私：凭据只在本进程内读取，**不打印 token**；peer 打码输出；文本与音频
不落日志（只记长度/字节数）。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SESSIONS_DIR = Path("data/wechat_sessions")
USERS_DB = Path("data/users.db")
DEFAULT_MAX_AGE_HOURS = 24.0
DEFAULT_DELAY_SECONDS = 2.0
_LINE_FALLBACK = "在忙吗？我这边刚忙完，突然想跟你说句话。"


def _mask(peer: str) -> str:
    p = str(peer or "")
    return (p[:8] + "…") if len(p) > 8 else p


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def collect_targets(
    sessions_dir: Path = SESSIONS_DIR,
    users_db: Path = USERS_DB,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
) -> list[dict]:
    """枚举可见会话 → 投递目标（含窗口新鲜度与绑定角色）。

    返回项：``{user_id, peer, fresh, age_hours, token, base_url, context_token,
    character_id}``；token/base_url 仅内部使用，打印时脱敏。
    """
    bindings: dict[int, str] = {}
    try:
        conn = sqlite3.connect(str(users_db))
        try:
            for uid, cid in conn.execute(
                "SELECT user_id, character_card_id FROM wechat_bindings"
            ):
                bindings[int(uid)] = str(cid or "default")
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        pass

    now = time.time()
    out: list[dict] = []
    if not sessions_dir.is_dir():
        return out
    for uid_dir in sorted(sessions_dir.iterdir(), key=lambda p: p.name):
        if not uid_dir.is_dir() or not uid_dir.name.isdigit():
            continue
        uid = int(uid_dir.name)
        for slot_dir in sorted(uid_dir.iterdir()):
            cred = _read_json(slot_dir / "credentials.json")
            tokens = _read_json(slot_dir / "context_tokens.json")
            if not cred.get("token"):
                continue
            for peer, meta in tokens.items():
                ts = float((meta or {}).get("ts") or 0)
                age_h = (now - ts) / 3600.0 if ts else 9999.0
                out.append(
                    {
                        "user_id": uid,
                        "peer": str(peer),
                        "fresh": age_h < float(max_age_hours),
                        "age_hours": round(age_h, 1),
                        "token": str(cred.get("token") or ""),
                        "base_url": str(cred.get("base_url") or ""),
                        "context_token": str((meta or {}).get("token") or ""),
                        "character_id": bindings.get(uid, "default"),
                    }
                )
    return out


def _character_persona(character_id: str) -> tuple[str, str]:
    """(名字, 人设摘要)。内置 default 读 persona.yaml，其余读卡文件。"""
    try:
        if character_id and character_id != "default":
            card = _read_json(Path("config/characters") / f"{character_id}.json")
            name = str(card.get("name") or "").strip() or "她"
            desc = str(card.get("description") or card.get("personality") or "").strip()
            return name, desc[:400]
    except Exception:  # noqa: BLE001
        pass
    try:
        import yaml

        raw = yaml.safe_load(Path("config/persona.yaml").read_text(encoding="utf-8")) or {}
        name = str(raw.get("name") or "十四").strip()
        desc = str(raw.get("description") or raw.get("personality") or "").strip()
        return name, desc[:400]
    except Exception:  # noqa: BLE001
        return "十四", ""


def generate_line(llm, character_id: str) -> str:
    """一次 LLM 调用生成角色口吻的一两句主动语音文案；失败回退固定句。"""
    name, desc = _character_persona(character_id)
    system = (
        f"你就是{name}。{desc}\n"
        "现在你要主动给对方发一条**语音**消息（系统会把你说的话转成语音发出去）。"
        "用一两句话，口语、自然、带你的性格，不要客套模板，不要解释这是语音消息。"
    )
    try:
        text = asyncio.run(
            llm.chat(
                query="（主动发一条语音消息：随便开个话头，问对方在干嘛/说你在做什么）",
                system_prompt=system,
                temperature=0.9,
                max_tokens=120,
            )
        )
        text = str(text or "").strip().strip("“”\"'")
        if len(text) >= 4:
            return text[:120]
    except Exception as e:  # noqa: BLE001
        print(f"  ! LLM 生成失败（{type(e).__name__}），用固定句")
    return _LINE_FALLBACK


def _record_outbound(text: str, tgt: dict) -> None:
    """语音内容回写对话历史（下一轮聊天她记得自己主动发过）。"""
    try:
        from shisi.memory.legacy.structured_memory import StructuredMemory

        sm = StructuredMemory("data/sqlite.db")
        try:
            session = f"{tgt['user_id']}:{tgt['peer']}@im.wechat"
            sm.add_chat(
                "assistant", text, session_id=session,
                character_id=str(tgt.get("character_id") or "default"),
                user_key=session, channel="proactive_voice",
            )
        finally:
            sm.close()
    except Exception as e:  # noqa: BLE001
        print(f"  ! 历史回写失败（不影响投递）: {type(e).__name__}")


def _load_llm():
    try:
        from llm_provider import get_llm
        from observability.config_manager import ConfigManager

        cfg = ConfigManager(config_dir="config").config
        return get_llm(
            provider=cfg.llm.provider,
            # 与 orchestrator._init_mixin 同一真源（models_priority）
            models_config=getattr(cfg.llm, "models_priority", None),
            config=cfg.llm,
        )
    except Exception as e:  # noqa: BLE001
        print(f"! LLM 初始化失败（将用固定句）: {type(e).__name__}")
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="给每个用户发一条语音主动消息（dry-run 默认）")
    ap.add_argument("--execute", action="store_true", help="真发（默认只打印计划）")
    ap.add_argument("--include-stale", action="store_true",
                    help="连 24h 窗口已过的用户也尝试（平台会拒，仅用于验证拒绝行为）")
    ap.add_argument("--max-age-hours", type=float, default=DEFAULT_MAX_AGE_HOURS)
    ap.add_argument("--delay", type=float, default=DEFAULT_DELAY_SECONDS,
                    help="每个用户之间的间隔秒数（防限流）")
    ap.add_argument("--limit", type=int, default=0, help="最多处理几个用户（0=全部）")
    ap.add_argument("--text", default="", help="固定文案（跳过 LLM，便于验证）")
    ap.add_argument("--user", type=int, action="append", default=[],
                    help="只处理指定 user_id（可重复，便于定向验证）")
    args = ap.parse_args(argv)

    targets = collect_targets(max_age_hours=args.max_age_hours)
    if args.user:
        targets = [t for t in targets if t["user_id"] in set(args.user)]
    if not targets:
        print("没有可投递目标（data/wechat_sessions 下无凭据/无 context_token）")
        return 1
    if args.limit > 0:
        targets = targets[: args.limit]

    fresh = [t for t in targets if t["fresh"]]
    stale = [t for t in targets if not t["fresh"]]
    print(f"目标 {len(targets)} 条 | 窗口内 {len(fresh)} | 已过期 {len(stale)}"
          f"（>{args.max_age_hours:.0f}h，平台不会投递）")
    for t in targets:
        state = "窗口内" if t["fresh"] else f"过期 {t['age_hours']}h"
        print(f"  - user {t['user_id']} peer={_mask(t['peer'])} 角色={t['character_id']} [{state}]")
    if not args.execute:
        print("\n[dry-run] 未发送。加 --execute 真发。")
        return 0

    sendable = fresh + (stale if args.include_stale else [])
    if not sendable:
        print("没有窗口内用户可发；过期用户需用户先发一条消息激活 24h 窗口。")
        return 2

    llm = _load_llm() if not args.text else None
    ok_n = fail_n = skip_n = 0
    for i, tgt in enumerate(sendable):
        print(f"\n[user {tgt['user_id']}] peer={_mask(tgt['peer'])} 角色={tgt['character_id']}")
        if not tgt["fresh"] and not args.include_stale:
            skip_n += 1
            continue
        text = args.text or (generate_line(llm, tgt["character_id"]) if llm else _LINE_FALLBACK)
        print(f"  文案 {len(text)} 字")
        # 合成+发送统一走共享模块（与常驻主动线同一 owner）
        from utils.proactive_voice_delivery import send_proactive_voice

        session_key = f"{tgt['user_id']}:{tgt['peer']}@im.wechat"
        ok, err, _cid = send_proactive_voice(session_key, text, tgt["character_id"])
        if ok:
            ok_n += 1
            _record_outbound(text, tgt)
            print("  ✓ 语音条已发送")
        else:
            fail_n += 1
            print(f"  ✗ 发送失败: {err}")
        if i < len(sendable) - 1 and args.delay > 0:
            time.sleep(args.delay)
    print(f"\n完成：成功 {ok_n} / 失败 {fail_n} / 跳过 {skip_n}")
    return 0 if fail_n == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
