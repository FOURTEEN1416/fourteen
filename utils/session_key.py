"""会话键格式 —— 唯一真源（`owner:peer` 家族）。

为什么存在：会话键没有单一 owner，解析被手写复制到 6 处
（`api/run_api.py` ×2、`main.py`、`proactive/reminder_delivery.py`、
`proactive/scheduler.py` ×2、`wechat_direct/wechat_connector` 的正则），
各处**口径不一致**，已产生可证实的行为分裂（见下）。

**生产实测形态**（2026-09-21 生产库 `chat_history.session_id` 原样取样）：

    2:o9cq80-_yMyfY247BTeFL5JvNgoI@im.wechat
    4:o9cq805ifqDz9eaFN5YWuUFHF-10@im.wechat

即 **`owner:wxid@im.wechat`** —— ⚠️ `@im.wechat` 是**对端 wxid 自带的后缀**，
不是可随意剥离的"通道标记"（`connector._context_tokens` 的键、`send_text(to_user=)`
的目标、测试夹具 `"u1@im.wechat"` 三处一致）。**剥掉它是错的。**

各方言与构造点：

| 方言 | 构造点 | 形态 |
|------|--------|------|
| 微信 | `wechat_connector._session_key` | `N:wxid@im.wechat`（二段） |
| web  | `api/session_manager.create_session` | `N:web:8hex`（三段） |
| WS   | `api/websocket_server`（web 键前再缀 owner） | `N:N:web:8hex`（四段） |
| 遗留 | `_session_key`（owner 为 None 时） | `wxid@im.wechat`（一段） |

🔴 **由此产生的真实缺陷**（重扫实证）：`scheduler._is_wechat_session_key` 旧规则为
「含 `@im.wechat` **或** 二段式（左段数字）」—— 后者会把 **web 键 `N:web:hex`**
也判成微信（`split(":", 1)` 得 `(N, "web:hex")`，左段是数字即通过）。后果：
`_send_targeted()` 对 web 会话**拒绝回退 ws 广播**（按 P1-21「微信绝不广播」策略），
web 用户的定向主动消息/提醒静默不送达；而对同一个键，`reminder_delivery` 判为
非微信走 ws —— 两处对同一输入结论相反。判据现统一到本模块。

判据口径（与构造点一一对应，无启发式）：
- 键内含 `@im.wechat` 标记 → 微信（含遗留的一段式裸 peer）；
- 二段且左段为数字 → 微信；
- 三段及以上（`N:web:hex` / `N:N:web:hex`）→ **非微信**。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

WECHAT_CHANNEL = "im.wechat"

_OWNER_RE = re.compile(r"^\d+$")


@dataclass(frozen=True)
class SessionKey:
    """解析结果。

    ``peer`` = **原样**的对端标识（`owner:` 之后的部分，不做后缀剥离 ——
    微信 wxid 自带 `@im.wechat`，剥掉会导致发送目标错误）。
    ``channel`` = 能确定的通道标记；判不出时为空串。
    """

    owner: int | None
    peer: str
    channel: str
    segments: int

    @property
    def is_wechat(self) -> bool:
        return is_wechat(self)


def build(owner_user_id: int | str, peer: str) -> str:
    """构造会话键（微信侧即 ``f"{owner}:{peer}"``，peer 自带后缀时原样保留）。"""
    return f"{int(owner_user_id)}:{str(peer).strip()}"


def parse(session_key: str) -> SessionKey:
    """解析会话键（永不抛异常；无法解析时 ``peer`` 保留原串）。"""
    raw = str(session_key or "").strip()
    if not raw:
        return SessionKey(None, "", "", 0)
    parts = raw.split(":")
    # 通道标记：@im.wechat 可出现在整串任意位置（wxid 自带）
    marked_wechat = WECHAT_CHANNEL in raw
    if len(parts) == 1:
        return SessionKey(None, parts[0], WECHAT_CHANNEL if marked_wechat else "", 1)
    head = parts[0]
    owner = int(head) if _OWNER_RE.match(head) else None
    if len(parts) == 2:
        channel = WECHAT_CHANNEL if (marked_wechat or owner is not None) else ""
        return SessionKey(owner, parts[1], channel, 2)
    # 三段及以上：owner:channel:rest（web / WS 形态）
    mid = parts[1]
    channel = WECHAT_CHANNEL if marked_wechat else mid
    return SessionKey(owner, ":".join(parts[2:]), channel, len(parts))


def is_wechat(parsed: SessionKey) -> bool:
    """已解析键是否指向微信通道（判据见模块 docstring）。"""
    if not parsed.peer:
        return False
    if parsed.channel == WECHAT_CHANNEL:
        return True
    return parsed.segments == 2 and parsed.owner is not None


def is_wechat_key(session_key: str) -> bool:
    """会话键是否指向微信通道（判据见模块 docstring）。"""
    return is_wechat(parse(session_key))


def owner_of(session_key: str) -> int | None:
    return parse(session_key).owner


def peer_of(session_key: str) -> str:
    """取对端标识：`N:wxid@im.wechat` → `wxid@im.wechat`（后缀保留）；裸键原样返回。"""
    return parse(session_key).peer


def split_owner(session_key: str) -> tuple[str, str]:
    """把键切成 ``(owner 段, 其余段)`` —— **原样字符串**，不做 int 转换、不剥后缀。

    返回 ``(owner, rest)``；无法判定 owner 时 owner 为空串、rest 为原串。

    2026-09-22 收口：项目里曾有 4 处手写 ``split(":", 1)`` 做同一件事
    （`scheduler` ×2 用于"该键属于哪个 owner"、`user_scheduler` ×2 用于
    binding 回退查找、`structured_memory` 用于从历史行反推唯一 owner）。
    行为恰好都对，但**没走唯一 owner** ⇒ 会话键家族一旦再增方言就会各自漂移
    （本项目已经发生过一次：`@im.wechat` 后缀被猜错导致解析结论相反）。

    与 :func:`parse` 的差别：本函数**只做切分不做通道判定**，专供
    "拆出 owner 做归属比较/拼装"这类不需要判通道的场景，避免调用方
    为了拿 owner 而被迫依赖通道判据。
    """
    raw = str(session_key or "").strip()
    if not raw:
        return "", ""
    head, sep, rest = raw.partition(":")
    if not sep:
        return "", raw
    if _OWNER_RE.match(head):
        return head, rest
    return "", raw
