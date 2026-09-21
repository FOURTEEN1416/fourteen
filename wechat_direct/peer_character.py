"""通道内好友角色自选 — (owner_user_id, peer_wxid) → character_card_id。

用户裁决 2026-09-19：「让他们自己选」。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import select

logger = logging.getLogger("wechat.peer_character")

_CHOICE_CMD = re.compile(r"^\s*(?:角色|選角|选角)\s*$")
_CHOICE_NUM = re.compile(r"^\s*#?(\d{1,2})\s*$")


def session_key(owner_user_id: int, peer_wxid: str) -> str:
    """会话键构造（委托唯一真源 `utils.session_key.build`）。"""
    from utils.session_key import build

    return build(owner_user_id, peer_wxid)


async def get_peer_preference(db, owner_user_id: int, peer_wxid: str) -> str | None:
    from api.database import WechatPeerPreference

    result = await db.execute(
        select(WechatPeerPreference).where(
            WechatPeerPreference.owner_user_id == int(owner_user_id),
            WechatPeerPreference.peer_wxid == peer_wxid,
        )
    )
    row = result.scalar_one_or_none()
    return row.character_card_id if row else None


async def set_peer_preference(db, owner_user_id: int, peer_wxid: str, character_card_id: str) -> None:
    from api.database import WechatPeerPreference

    result = await db.execute(
        select(WechatPeerPreference).where(
            WechatPeerPreference.owner_user_id == int(owner_user_id),
            WechatPeerPreference.peer_wxid == peer_wxid,
        )
    )
    row = result.scalar_one_or_none()
    if row:
        row.character_card_id = character_card_id
    else:
        db.add(
            WechatPeerPreference(
                owner_user_id=int(owner_user_id),
                peer_wxid=peer_wxid,
                character_card_id=character_card_id,
            )
        )
    await db.flush()


def list_owner_characters(owner_user_id: int, user_id_filter: str | None = None) -> list[dict]:
    """列出通道所有者可见的角色卡（复用角色库，按 user_id 过滤）。"""
    try:
        from api.routers.character_routes import _list_all_characters  # type: ignore
    except Exception:  # noqa: BLE001
        _list_all_characters = None
    cards: list[dict] = []
    try:
        import json

        from utils.project_paths import project_path

        char_dir = project_path("config", "characters")
        if not char_dir.exists():
            return cards
        uid = str(owner_user_id)
        for p in sorted(char_dir.glob("*.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(data, dict):
                continue
            card_uid = str(data.get("user_id") or data.get("owner_user_id") or "default")
            # 所有者自己的卡 + 公共 default/preset
            if card_uid not in (uid, "default", "public", "") and (
                "user_id" in data or "owner_user_id" in data
            ):
                continue
            cards.append(
                {
                    "id": str(data.get("id") or p.stem),
                    "name": str(data.get("name") or p.stem),
                }
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("列出角色失败 owner=%s: %s", owner_user_id, e)
    return cards[:20]


def build_character_menu(cards: list[dict]) -> str:
    if not cards:
        return "当前还没有可选角色，先让号主在控制台创建一个吧。回复「角色」可再次查看。"
    lines = ["想和谁聊？回复序号选择角色："]
    for i, c in enumerate(cards, 1):
        lines.append(f"{i}. {c.get('name') or c.get('id')}")
    lines.append("（随时回复「角色」可重新选择）")
    return "\n".join(lines)


def is_character_command(text: str) -> bool:
    """是否为显式「角色」指令（无歧义措辞才拦截，普通聊天不受影响）。"""
    return bool(_CHOICE_CMD.match((text or "").strip()))


def try_handle_character_choice(
    text: str,
    owner_user_id: int | None,
    peer_wxid: str,
    cards: list[dict],
    pending_choices: dict[str, Any],
) -> dict | None:
    """同步侧解析选择指令；需要落库时返回动作，由异步层写 DB。

    返回 None 表示不是角色选择指令。
    返回 dict:
      {"action":"show_menu","menu": str}
      {"action":"selected","character_id": str,"message": str}

    ⚠️ P1-审查 item28：纯数字**只在有待确认菜单时**生效 —— 旧实现收到任意
    1~20 的数字都会劫持进角色流（用户回「1」「2」这种自然应答被吞掉），
    且 pending_choices 参数收了却从未使用。
    """
    if owner_user_id is None:
        return None
    raw = (text or "").strip()
    if _CHOICE_CMD.match(raw):
        return {"action": "show_menu", "menu": build_character_menu(cards)}
    m = _CHOICE_NUM.match(raw)
    if m and cards and pending_choices.get(peer_wxid):
        idx = int(m.group(1))
        if 1 <= idx <= len(cards):
            card = cards[idx - 1]
            return {
                "action": "selected",
                "character_id": str(card.get("id") or ""),
                "message": f"好的，接下来由「{card.get('name')}」陪你聊。直接说话就行～",
            }
    return None
