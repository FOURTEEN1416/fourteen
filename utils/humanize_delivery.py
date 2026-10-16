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
`main.send_proactive_segmented` / `main.direct_send_proactive`），run_api 与
既有测试的消费面不变。

配置真源 = `data/scheduler_config.json` 的 `humanize.proactive_split`（跨 worker
热读，无进程内缓存；读侧归 `utils.humanize_config`）。默认 `False` ⇒ 主动投递
逐字保持「整段直发」现状（零行为变更）。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("humanize_delivery")

_READ_WARNED = False


def humanize_proactive_split_enabled() -> bool:
    """热读拟人度配置：主动消息是否走「拆条 + 段间节奏」。

    每次调用直读配置文件（**无进程内缓存**）——多 worker 改配置即时一致，
    与 `utils.reply_mode.read_reply_mode` 同源哲学。
    任何读取异常（模块缺席/文件缺失/字段越界）一律回落 False，即回落到
    「整段直发」的现行为：**绝不因配置不可读把主动消息判成投递失败**。
    """
    global _READ_WARNED
    try:
        from utils import humanize_config

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


def segment_sender(connector: Any):
    """取连接器的「拆条发送」实现；不支持时返回 None。

    公有名 `send_reply_segments` 是唯一入口（设计契约第 1 步已落地：
    `wechat_connector` 的私有 `_send_reply_segments` 公有化并移除）。这里只用
    `getattr` 容忍「连接器对象没有该方法」——此时返回 None 让调用方回落整段
    直发，而不是抛 AttributeError（否则一次接线疏漏会把通道反复置 None）。
    """
    return getattr(connector, "send_reply_segments", None)


def send_proactive_segmented(connector: Any, peer: str, msg: str) -> bool | None:
    """把一条主动消息投进「拆条 + 段间节奏」链。

    返回三态：
    - ``True``  = **完整受理**（全部段都被微信 API 收下）
    - ``False`` = **未完整受理**（段循环 break/异常 → 只有前缀被受理，
      尾句用户没看到）
    - ``None``  = 连接器无拆条实现 → 调用方回落整段直发（**不算投递失败**，
      避免误报全失败）

    受理判据**复用主链**的 complete 谓词（`WeChatConnector._handle_message_serial`
    内 `publish` 的 ``accepted == "\\n".join(split_reply_for_wechat(reply))``）——
    单一真源，不在此另造语义，主动线与主链因此逐字同构。前缀判成功会把用户
    从未看到的尾句记进历史（`scheduler._deliver` 用原文 `_record_outbound`），
    故只有完整受理才算成功。

    锁：`send_text` 在 `_peer_lock` 内串行同 peer 的生成与发送，而段方法本身
    不持锁（connector 契约）——在此补上同一把锁，防主动消息与主回复对同一
    peer 交错。RLock 可重入，段方法日后自行加锁也不会自死锁。
    """
    send = segment_sender(connector)
    if send is None:
        return None
    from wechat_direct.wechat_connector import split_reply_for_wechat

    lock = getattr(connector, "_peer_lock", None)
    session_key = getattr(connector, "_session_key", None)
    if callable(lock) and callable(session_key):
        with lock(session_key(peer)):
            accepted = send(peer, msg)
    else:
        accepted = send(peer, msg)
    return bool(accepted) and accepted == "\n".join(split_reply_for_wechat(msg))


def direct_send_proactive(connector: Any, peer: str, msg: str) -> bool:
    """本机直发一条主动微信消息，返回是否**完整受理**。

    拟人度关闭（默认）⇒ 逐字走 `connector.send_text`（整段直发，零行为变更）；
    开启且连接器支持拆条 ⇒ 走「拆条 + 段间节奏」链；开启但连接器无拆条实现
    ⇒ 回落 `send_text`（不误报失败）。
    """
    if humanize_proactive_split_enabled():
        accepted = send_proactive_segmented(connector, peer, msg)
        if accepted is not None:
            return accepted
    return bool(connector.send_text(msg, to_user=peer))
