"""拟人度「主动投递链」——主动消息的拆条 + 段间节奏（唯一真源）。

为什么独立成模块（2026-10-16 生产可达性修复）：主动消息有**两个**本机投递点 ——
① 单进程入口（`main.py`）的 wechat 通道发送器；② 多 worker 生产
（`deploy/ai-girlfriend.service` = `uvicorn api.run_api:app --workers 4`）下
**连接器宿主**的 `WeChatConnector._drain_outbox` —— run_api 的发送器只入队并等
回执，真实发送发生在连接器宿主。本链原先只在 `main` 内实现，只有 ① 能用：
② 若反向 import `main` 会把整个 API 栈拖进连接器（且 `python main.py` 形态下
会二次导入 main 模块）。故把本链下沉到本模块 —— 只依赖
`utils.humanize_config` 与连接器对象的鸭子类型，两个投递点共用同一实现；
`main` 侧再从本模块转出既有公共名（`main.humanize_proactive_split_enabled` /
`main.send_proactive_segmented` / `main.direct_send_proactive` /
`main.proactive_split_enabled`——2026-11-08 新增，投递失败归因闸，按
「用户 × 角色」作用域判定主动拆条开关），run_api 与既有测试的消费面不变。

配置真源 = `data/scheduler_config.json` 的 `humanize.proactive_split`（跨 worker
热读，无进程内缓存；读侧归 `utils.humanize_config`）。默认 `False` ⇒ 主动投递
逐字保持「整段直发」现状（零行为变更）。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("humanize_delivery")

_READ_WARNED = False


def humanize_proactive_split_enabled(
    user_key: str | None = None, character_id: str | None = None,
) -> bool:
    """热读拟人度配置：主动消息是否走「拆条 + 段间节奏」。

    每次调用直读配置文件/角色快照（**无进程内缓存**）——多 worker 改配置即时
    一致，与 `utils.reply_mode.read_reply_mode` 同源哲学。

    ``user_key`` + ``character_id`` 同时给出时按「用户 × 角色」快照读取
    （2026-11-08 D-A：per-user 覆盖层退役后，用户自己的「主动拆条」开关只
    存在于角色设置快照里）；缺任一 → 只读全局块（机器面/无角色上下文）。

    任何读取异常（模块缺席/文件缺失/字段越界）一律回落 False，即回落到
    「整段直发」的现行为：**绝不因配置不可读把主动消息判成投递失败**。
    """
    global _READ_WARNED
    try:
        from utils import humanize_config

        # 无角色上下文时**不带参**调用（机器面/控制台读全局块的既有契约）
        if user_key and character_id:
            cfg = humanize_config.read_humanize(user_key, character_id)
        else:
            cfg = humanize_config.read_humanize()
    except Exception:  # noqa: BLE001
        if not _READ_WARNED:
            _READ_WARNED = True
            logger.debug("拟人度配置不可读 → 主动消息按整段直发（零行为变更）")
        return False
    # 读侧容忍 dict / 配置对象两种形态（「非 dict / 缺失 ⇒ 回落默认」契约）
    value = (
        cfg.get("proactive_split")
        if isinstance(cfg, dict)
        else getattr(cfg, "proactive_split", None)
    )
    return bool(value)


def _scope_from(connector: Any, peer: str) -> tuple[str | None, str | None]:
    """从连接器取该 peer 的拟人度作用域（`humanize_scope` 鸭子类型）。

    连接器是唯一知道「会话键 + 当前角色」的对象（角色解析 owner 见
    `utils.character_resolver`）；取不到就返回 `(None, None)`，调用方按
    全局块读取（机器面/测试替身场景，零行为变更）。
    """
    scope = getattr(connector, "humanize_scope", None)
    if not callable(scope):
        return (None, None)
    try:
        user_key, character_id = scope(peer)
    except Exception as exc:  # noqa: BLE001
        logger.debug("拟人度作用域解析失败，按全局读取: %s", exc)
        return (None, None)
    return (str(user_key or "") or None, str(character_id or "") or None)


def proactive_split_enabled(connector: Any, peer: str) -> bool:
    """该 peer 的「主动拆条」是否开启（按连接器解析出的「用户 × 角色」作用域）。

    与 `direct_send_proactive` 共用同一判据：投递失败归因（`main.py` 的
    wechat 通道发送器区分「未完整受理」与「整段直发被拒」）必须读同一份
    作用域开关，否则「按角色开了主动拆条、全局关」时会把「未完整受理」
    误报成「微信定向投递失败」（两者都 raise RuntimeError，只错在语义）。
    """
    user_key, character_id = _scope_from(connector, peer)
    return humanize_proactive_split_enabled(user_key, character_id)


def _scoped_split_cap(connector: Any, peer: str) -> int:
    """该 peer 的拆条上限（「用户 × 角色」快照 → 全局块 → 出厂默认）。

    `send_proactive_segmented` 的受理判据必须用与发送侧**相同**的上限重算
    期望分段：发送侧 `WeChatConnector.send_reply_segments` 按作用域快照取
    上限，此处若按出厂默认重算，快照上限 ≠ 默认且单行超长消息（行内切块）
    时段界不同 ⇒ 完整受理被误判「未完整受理」（2026-11-08 发送链收口排查
    实证，钉死于
    `tests/test_humanize_delivery_scope_20261108.py::test_proactive_complete_judgement_uses_scoped_cap`）。

    连接器不提供作用域（机器面/测试替身）⇒ 读全局块；任何读取异常 ⇒
    出厂默认 —— 与连接器 `_split_cap_for` 的回落语义一致，判据绝不能把
    一条已发出的消息误判成失败。
    """
    user_key, character_id = _scope_from(connector, peer)
    from utils import humanize_config

    try:
        if user_key and character_id:
            cfg = humanize_config.read_humanize(user_key, character_id)
        else:
            cfg = humanize_config.read_humanize()
        return int(getattr(cfg, "max_segments", humanize_config.MAX_SEGMENTS))
    except Exception:  # noqa: BLE001
        return int(humanize_config.MAX_SEGMENTS)


def segment_sender(connector: Any):
    """取连接器的「拆条发送」实现；不支持时返回 None。

    公有名 `send_reply_segments` 是唯一入口（设计契约第 1 步已落地：
    `wechat_connector` 的私有 `_send_reply_segments` 公有化并移除）。这里只用
    `getattr` 容忍「连接器对象没有该方法」——此时返回 None 让调用方回落整段
    直发，而不是抛 AttributeError（否则一次接线疏漏会把通道反复置 None）。
    """
    return getattr(connector, "send_reply_segments", None)


def send_proactive_segmented_result(
    connector: Any, peer: str, msg: str,
) -> tuple[str, list[str]] | None:
    """拆条发送并返回 ``(已受理前缀, 期望段列表)``；连接器无拆条实现 → None。

    2026-11-12 复核修复：提醒线需要区分「完整受理 / 部分前缀」，旧的单一
    bool 会把部分受理折叠成失败并触发整条重发（已受理前缀被重复发送）。
    """
    send = segment_sender(connector)
    if send is None:
        return None
    from wechat_direct.wechat_connector import (
        _speaker_reply_hard_limit,
        split_reply_for_wechat,
    )

    lock = getattr(connector, "_peer_lock", None)
    session_key = getattr(connector, "_session_key", None)
    if callable(lock) and callable(session_key):
        with lock(session_key(peer)):
            accepted = send(peer, msg)
    else:
        accepted = send(peer, msg)
    expected = split_reply_for_wechat(
        msg,
        max_segments=_scoped_split_cap(connector, peer),
        hard_limit=_speaker_reply_hard_limit(),
    )
    return str(accepted or ""), expected


def send_proactive_segmented(connector: Any, peer: str, msg: str) -> bool | None:
    """把一条主动消息投进「拆条 + 段间节奏」链。

    返回三态：
    - ``True``  = **完整受理**（全部段都被微信 API 收下）
    - ``False`` = **未完整受理**（段循环 break/异常 → 只有前缀被受理，
      尾句用户没看到）
    - ``None``  = 连接器无拆条实现 → 调用方回落整段直发（**不算投递失败**，
      避免误报全失败）

    受理判据**复用主链**的 complete 谓词（`WeChatConnector._handle_message_serial`
    内 `publish` 的 ``accepted == "\\n".join(split_reply_for_wechat(reply,
    max_segments=self._split_cap_for(from_user)))``）——单一真源，不在此另造
    语义，主动线与主链因此逐字同构：**上限也按同一「用户 × 角色」作用域取**
    （`_scoped_split_cap`，2026-11-08 收口——旧实现按出厂默认重算，快照上限
    ≠ 默认时行内切块段界不同 ⇒ 完整受理被误判「未完整受理」）。前缀判成功
    会把用户从未看到的尾句记进历史（`scheduler._deliver` 用原文
    `_record_outbound`），故只有完整受理才算成功。

    锁：`send_text` 在 `_peer_lock` 内串行同 peer 的生成与发送，而段方法本身
    不持锁（connector 契约）——在此补上同一把锁，防主动消息与主回复对同一
    peer 交错。RLock 可重入，段方法日后自行加锁也不会自死锁。
    """
    result = send_proactive_segmented_result(connector, peer, msg)
    if result is None:
        return None
    accepted, expected = result
    return bool(accepted) and accepted == "\n".join(expected)


def direct_send_proactive_result(
    connector: Any, peer: str, msg: str,
) -> tuple[bool, bool]:
    """直发主动消息，返回 ``(是否有任意段被受理, 是否完整受理)``。"""
    if proactive_split_enabled(connector, peer):
        result = send_proactive_segmented_result(connector, peer, msg)
        if result is not None:
            accepted, expected = result
            return bool(accepted), accepted == "\n".join(expected)
    ok = bool(connector.send_text(msg, to_user=peer))
    return ok, ok


def direct_send_proactive(connector: Any, peer: str, msg: str) -> bool:
    """本机直发一条主动微信消息，返回是否**完整受理**。

    拟人度关闭（默认）⇒ 逐字走 `connector.send_text`（整段直发，零行为变更）；
    开启且连接器支持拆条 ⇒ 走「拆条 + 段间节奏」链；开启但连接器无拆条实现
    ⇒ 回落 `send_text`（不误报失败）。

    2026-11-08 D-A：「主动拆条」开关按 `connector.humanize_scope(peer)`
    解析的「用户 × 角色」快照读取；连接器不提供作用域时回落全局块。
    """
    return direct_send_proactive_result(connector, peer, msg)[1]
