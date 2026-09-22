"""
直接微信连接器

直连微信 API，扫码登录 → 收消息 → 传给小十 → 发回复。
"""

import asyncio
import base64
import concurrent.futures
import json
import logging
import os
import re
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import suppress
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger("wechat_direct")

# ── 微信 API 地址 ──
DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"

# ── 登录相关 ──
QR_LOGIN_TIMEOUT_S = 480
QR_MAX_REFRESHES = 5
QR_POLL_INTERVAL = 1

# ── 消息轮询 ──
LONG_POLL_TIMEOUT = 35
MAX_CONSECUTIVE_FAILURES = 5
RETRY_DELAY = 5
BACKOFF_DELAY = 60

# ── 内存泄漏防护 ──
_RECEIVED_MSGS_MAX = 10000       # _received_msgs 最大条目数
_CONTEXT_TOKENS_TTL = 86400      # _context_tokens 条目 TTL（秒），默认24小时
_PEER_CHOICE_TTL = 600.0         # 好友「角色」菜单的选择待确认有效期（秒）

# ── 连接状态持久化（解决前端状态时连时断问题） ──
# ⚠️ 2026-09-19：全局单例路径仅保留给「无 owner 的遗留 admin 通道」兼容读取；
# 用户通道一律走 data/wechat_sessions/<user_id>/slotN/state.json
_STATE_FILE = Path(__file__).parent.parent / "data" / "wechat_state.json"

# ── P1-审查 item29（2026-09-21）：状态文件必须互斥读写 + 原子写 ──
# 旧 save_session_state 是「内存 dict 整文件覆盖」的无锁 RMW：login 线程写
# connected 与轮询线程写 last_activity 交错时互相抹掉对方；且非原子写，
# 崩溃留下半截 JSON → load_session_state 落进 except，状态恒读成 idle。
_FILE_STATE_LOCK = threading.RLock()


def _atomic_write_json(path: Path | str, payload: Any) -> None:
    """tmp + os.replace 原子写 JSON（tmp 名带 pid，避免并发写者互踩）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".tmp.{p.name}.{os.getpid()}")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, p)


def load_session_state(user_id: int, slot: int = 0) -> dict:
    """读取 per-user 通道状态；不存在则返回 idle 结构（绝不读全局他人状态）。"""
    from wechat_direct import channel_paths

    empty = {
        "connected": False,
        "started_at": 0,
        "bot_id": "",
        "last_activity": 0,
        "messages_today": 0,
        "reconnect_attempts": 0,
        "status": "idle",
        "owner_user_id": int(user_id),
        "slot": int(slot),
    }
    path = channel_paths.state_path(user_id, slot)
    try:
        if path.exists():
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                empty.update(data)
                empty["owner_user_id"] = int(user_id)
                empty["slot"] = int(slot)
    except Exception as e:  # noqa: BLE001
        logger.debug("读取用户通道状态失败 user=%s slot=%s: %s", user_id, slot, e)
    return empty


def save_session_state(user_id: int, slot: int, data: dict) -> None:
    from wechat_direct import channel_paths

    path = channel_paths.state_path(user_id, slot)
    try:
        payload = dict(data)
        payload["owner_user_id"] = int(user_id)
        payload["slot"] = int(slot)
        with _FILE_STATE_LOCK:
            _atomic_write_json(path, payload)
    except Exception as e:  # noqa: BLE001
        logger.debug("保存用户通道状态失败 user=%s slot=%s: %s", user_id, slot, e)


def update_session_state(user_id: int, slot: int, updates: dict) -> dict:
    """加锁读-改-写用户通道状态文件，返回合并后的完整状态（item29）。"""
    with _FILE_STATE_LOCK:
        state = load_session_state(user_id, slot)
        state.update(updates)
        save_session_state(user_id, slot, state)
        return state


def save_session_qrcode(user_id: int, slot: int, qrcode_url: str = "", status: str = "waiting") -> None:
    from wechat_direct import channel_paths

    path = channel_paths.qrcode_path(user_id, slot)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "qrcode_url": qrcode_url,
                    "status": status,
                    "timestamp": time.time(),
                    "owner_user_id": int(user_id),
                    "slot": int(slot),
                },
                f,
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("保存用户二维码失败 user=%s: %s", user_id, e)


def load_session_qrcode(user_id: int, slot: int = 0) -> dict:
    from wechat_direct import channel_paths

    path = channel_paths.qrcode_path(user_id, slot)
    if not path.exists():
        return {"qrcode_url": "", "status": "idle", "timestamp": 0}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {"qrcode_url": "", "status": "idle", "timestamp": 0}
    except Exception:  # noqa: BLE001
        return {"qrcode_url": "", "status": "idle", "timestamp": 0}


def _load_state() -> dict:
    """读取持久化的连接状态，供 REST API 在 connector 实例重建时仍返回稳定状态。"""
    try:
        if _STATE_FILE.exists():
            with open(_STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception as e:  # noqa: BLE001
        logger.debug("读取微信状态文件失败: %s", e)
    return {
        "connected": False,
        "started_at": 0,
        "bot_id": "",
        "last_activity": 0,
        "messages_today": 0,
        "reconnect_attempts": 0,
    }


def _save_state(data: dict) -> None:
    """持久化连接状态（遗留全局通道）。"""
    try:
        with _FILE_STATE_LOCK:
            _atomic_write_json(_STATE_FILE, data)
    except Exception as e:  # noqa: BLE001
        logger.debug("保存微信状态文件失败: %s", e)


def _merge_state(updates: dict) -> dict:
    """合并并保存状态更新（同锁读-改-写，item29）。"""
    with _FILE_STATE_LOCK:
        state = _load_state()
        state.update(updates)
        _save_state(state)
        return state


# ── 遗留全局单例（仅 admin 兼容层；用户通道用 ConnectorRegistry） ──
_connector: "WeChatConnector | None" = None


def get_connector(user_id: int | None = None, slot: int = 0):
    """有 user_id 时从 Registry 取；否则返回遗留全局单例（admin）。"""
    if user_id is not None:
        from wechat_direct.connector_registry import get_registry

        return get_registry().get(user_id, slot)
    return _connector


def get_wechat_state(user_id: int | None = None, slot: int = 0) -> dict:
    """状态读取：指定 user_id 时**只**返回该用户通道；否则返回遗留全局状态。

    用户侧 API 必须传 user_id，禁止用本函数无参形态给普通用户展示「已连接」。
    """
    if user_id is not None:
        from wechat_direct.connector_registry import get_registry

        return get_registry().primary_status(user_id)
    conn = _connector
    if conn:
        if conn.token:
            st = conn.get_status()
            st["owner_user_id"] = getattr(conn, "owner_user_id", None)
            return st
        return {**_load_state(), "connected": False}
    return _load_state()


# ═══════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════

CREDENTIALS_PATH = os.path.expanduser("~/.weixin_cow_credentials.json")

# context_token 持久化路径。
# 为什么必须落盘：context_token 只在**收到用户消息**时写入（内存字典），
# 服务一重启就清空；而主动发送（ASE / 重要日期）依赖它。实测空 token 发送
# 返回 {"ret": -2, "errmsg": "prepare failed"} —— 2026-09-19 生产事故：
# 重启后所有主动消息都失败，用户零接收，而日志四层都报「已投递」。
# 锚定仓库根 data/（与 proactive_state.json 同目录），不走相对 CWD。
CONTEXT_TOKENS_PATH = str(
    Path(__file__).resolve().parent.parent / "data" / "wechat_context_tokens.json"
)

# ═══════════════════════════════════════════════════════════════
#  对话内追问（2026-09-19）
#
#  用户反馈：「只有我发一条消息他才会回一条消息……我不接着发消息他就不回」
#  —— 体感是「一问一答的客服」，不是「聊天」。
#
#  真人不只是等对方开口：回复完对方没接话时，会自己再补一句。
#  既有的 ASE 主动消息做不到这件事 —— 它是 5 分钟 tick + 30 分钟冷却 +
#  每日 8 条的**后台引擎**，消息也是通用的（想你/无聊/担心），
#  与刚聊的话题无关。所以这里补上**对话内**的短时追问：
#  回复成功后登记，到点对方仍未接话 → 用刚才的上下文再补一句。
# ═══════════════════════════════════════════════════════════════

# 追问延迟序列的**默认值**（web 控制端可改；写入 data/scheduler_config.json 的
# follow_up 块，与免打扰时段同一份跨 worker 真源）。
# 值域守卫见 read_follow_up_config()，避免控制端写入非法值把行为搞坏。
FOLLOW_UP_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "delay1_seconds": 45,
    "delay2_seconds": 150,
    "daily_max": 12,
}
# 扫描间隔（秒）
_FOLLOWUP_TICK = 5.0
# 追问文本长度上限
_FOLLOWUP_MAX_CHARS = 40
# 追问上下文条数（读持久化 chat_history，含双方）
_FOLLOWUP_CONTEXT_TURNS = 8
# 「一句一句发」的拆分上限：单条最多 4 段、每段最多 45 字
_SPLIT_MAX_SEGMENTS = 4
_SPLIT_MAX_CHARS = 45


def split_reply_for_wechat(
    reply: str,
    max_segments: int = _SPLIT_MAX_SEGMENTS,
    max_chars: int = _SPLIT_MAX_CHARS,
) -> list[str]:
    """把一条回复拆成若干「微信里一句一句发」的短消息。

    ⚠️ 2026-09-19 用户反馈：「一个正常人，怎么会一次性发那么回一大段？
    ……不应该一句一句的吗？」—— 旧实现把 LLM 返回的整段（含多行）**当一条消息**
    发出去，体感像在看公告而不是聊天。

    规则：
    1. 先按换行切块；短回复（≤ max_chars）原样一条发出，不折腾
    2. 超长块再按句末标点（。！？…；）切句，贪心合并到 ≤ max_chars
    3. 段数封顶 max_segments：多出来的并进最后一段，避免变成刷屏

    返回 [] 表示无可发送内容（调用方应回落原文本）。
    """
    text = (reply or "").strip()
    if not text:
        return []
    # 注意：**带换行就必须拆**（哪怕总长短）—— 否则会带着空行整段发出，
    # 正是用户抱怨的「一次发一大段」。短且单行才原样发。
    if "\n" not in text and len(text) <= max_chars:
        return [text]

    blocks: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line:
            blocks.append(line)
    if not blocks:
        return [text]

    # 逐行处理：**每行各自成一条**（模型用换行分隔的就是一句句独立的话，
    # 跨行合并会糊成「第0句第1句第2句…」这种读不通的一串）。
    # 只有**单行本身超长**时，才在该行内部按句末标点切句并合并。
    sentences: list[str] = []
    for block in blocks:
        if len(block) <= max_chars:
            sentences.append(block)
            continue
        parts: list[str] = []
        buf = ""
        for ch in block:
            buf += ch
            if ch in "。！？!?…":
                parts.append(buf.strip())
                buf = ""
        if buf.strip():
            rest = buf.strip()
            while len(rest) > max_chars:      # 长句无标点收尾 → 硬切
                parts.append(rest[:max_chars])
                rest = rest[max_chars:]
            if rest:
                parts.append(rest)
        # 仅在同一行内部合并，避免跨句糊在一起
        merged_line: list[str] = []
        for s in parts:
            if merged_line and len(merged_line[-1]) + len(s) <= max_chars:
                merged_line[-1] += s
            else:
                merged_line.append(s)
        sentences.extend(merged_line)

    if len(sentences) > max_segments:
        head = sentences[: max_segments - 1]
        # 尾部**逐字拼接**（不插入任何分隔符）：宁可读起来略紧凑，
        # 也绝不改写模型的原话 —— 内容必须无损。
        tail = "".join(sentences[max_segments - 1:])
        sentences = [*head, tail]
    return [s for s in sentences if s] or [text]


def _segment_delay(prev: str) -> float:
    """两段之间的「打字停顿」：按上一段长度估算，0.4~1.6s（别让人等太久）。"""
    return max(0.4, min(1.6, len(prev) * 0.045))
# 追问配置所在文件（与 scheduler 的 _CONFIG_PATH 同一份真源）
_SCHEDULER_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "scheduler_config.json"
)


def read_follow_up_config() -> dict[str, Any]:
    """读取「对话内追问」配置：文件值覆盖默认值，并做值域守卫。

    真源 = `data/scheduler_config.json` 的 `follow_up` 块（web 控制端写入、
    所有 uvicorn worker 共读，与 quiet_hours 同一机制）。
    文件缺失/损坏/字段非法一律回落默认值 —— 配置问题不该让功能失效或失控。
    """
    cfg = dict(FOLLOW_UP_DEFAULTS)
    try:
        if _SCHEDULER_CONFIG_PATH.exists():
            raw = json.loads(_SCHEDULER_CONFIG_PATH.read_text(encoding="utf-8")) or {}
            fu = raw.get("follow_up") or {}
            for key in cfg:
                if fu.get(key) is not None:
                    cfg[key] = fu[key]
    except Exception:  # noqa: BLE001
        pass
    with suppress(Exception):
        cfg["enabled"] = bool(cfg["enabled"])
        cfg["delay1_seconds"] = max(5, min(3600, int(cfg["delay1_seconds"])))
        # 第二轮下限 60s（2026-09-21 自问自答根治）：生产真源里曾被写成 10s，
        # 两条追问相隔 10 秒到达 → 用户侧看到的是她连发两句自问自答。
        # 下限而非改默认值：控制端写多小都拦得住，且不碰用户数据文件。
        cfg["delay2_seconds"] = max(60, min(7200, int(cfg["delay2_seconds"])))
        if cfg["delay2_seconds"] < cfg["delay1_seconds"]:
            cfg["delay2_seconds"] = cfg["delay1_seconds"]
        cfg["daily_max"] = max(0, min(200, int(cfg["daily_max"])))
    return cfg


def _load_credentials(path=None):
    path = path or CREDENTIALS_PATH
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"读取凭证失败: {e}")
    return {}


def _save_credentials(data, path=None):
    path = path or CREDENTIALS_PATH
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f)
        logger.info(f"凭证已保存到 {path}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"保存凭证失败: {e}")


def _save_qr_to_file(qrcode_url="", status="waiting"):
    """把二维码信息写到文件，前端 API 能读到"""
    try:
        project_root = Path(__file__).parent.parent
        qr_file = project_root / "data" / "wechat_qrcode.json"
        qr_file.parent.mkdir(parents=True, exist_ok=True)
        with open(qr_file, "w") as f:
            json.dump({
                "qrcode_url": qrcode_url,
                "status": status,
                "timestamp": time.time(),
            }, f)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"保存二维码到文件失败: {e}")


def _random_wechat_uin():
    import base64
    import random
    val = random.randint(0, 0xFFFFFFFF)
    return base64.b64encode(str(val).encode("utf-8")).decode("utf-8")


_CHANNEL_VERSION = "2.0.0"
_CLIENT_VERSION = "131072"


def _build_headers(token=""):
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": _random_wechat_uin(),
        "iLink-App-Id": "bot",
        "iLink-App-ClientVersion": _CLIENT_VERSION,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _ensure_trailing_slash(url: str) -> str:
    return url if url.endswith("/") else url + "/"


# ═══════════════════════════════════════════════
# 微信 API 调用（极简版，只用了 WeixinApi 的一小部分）
# ═══════════════════════════════════════════════

def _fetch_qr_code(base_url=DEFAULT_BASE_URL):
    """获取微信二维码"""
    url = _ensure_trailing_slash(base_url) + "ilink/bot/get_bot_qrcode?bot_type=3"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _poll_qr_status(qrcode, base_url=DEFAULT_BASE_URL, timeout=35):
    """轮询二维码扫码状态"""
    from urllib.parse import quote
    url = (_ensure_trailing_slash(base_url) +
           f"ilink/bot/get_qrcode_status?qrcode={quote(qrcode)}")
    headers = {
        "iLink-App-Id": "bot",
        "iLink-App-ClientVersion": _CLIENT_VERSION,
    }
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.Timeout:
        return {"status": "wait"}


def _api_ok(resp: Any) -> tuple[bool, str]:
    """判定微信 API 返回是否**业务成功**，返回 (ok, 错误说明)。

    ⚠️ 2026-09-19 生产事故：旧实现只检查 HTTP 状态码，**从不看业务返回码
    `ret`**。发送接口在会话窗口失效时返回 `{"ret": -2, "errmsg": "prepare
    failed"}`，HTTP 200 —— 于是整条链路四层都把「发送失败」报成「成功」，
    用户在微信里一条都没收到，而日志连续多日显示「主动消息已投递: wechat」。
    实测原始返回（空 context_token）：
        {"ret": -2, "errmsg": "prepare failed"}
    """
    if not isinstance(resp, dict):
        return False, f"响应格式异常: {type(resp).__name__}"
    if resp.get("timeout"):
        return False, "请求超时（送达状态未知，按失败处理）"
    ret = resp.get("ret")
    if ret is None:
        # 部分端点不返回 ret；以 errcode/errmsg 兜底
        if resp.get("errcode") or resp.get("errmsg"):
            return False, str(resp.get("errmsg") or resp.get("errcode"))
        return True, ""
    try:
        code = int(ret)
    except (TypeError, ValueError):
        return False, f"ret 非数值: {ret!r}"
    if code == 0:
        return True, ""
    return False, f"ret={code} errmsg={resp.get('errmsg', '') or '-'}"


def _post_api(endpoint, body, token="", base_url=DEFAULT_BASE_URL, timeout=15):
    """调用微信 API"""
    url = _ensure_trailing_slash(base_url) + endpoint
    headers = _build_headers(token)
    body.setdefault("base_info", {}).setdefault("channel_version", _CHANNEL_VERSION)
    try:
        resp = requests.post(url, json=body, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.Timeout:
        logger.debug(f"API超时: {endpoint}")
        # ⚠️ 旧实现返回 {"ret": 0, "msgs": []} —— 对 getupdates 是「无新消息」，
        # 但对 sendmessage 等于把**超时**伪装成**成功**。加 timeout 标记，
        # 由 _api_ok() 判定为失败，同时不破坏轮询侧对 ret/msgs 的既有读取。
        return {"ret": 0, "msgs": [], "timeout": True}
    except Exception as e:
        logger.error(f"API错误 {endpoint}: {e}")
        raise


def _get_updates(get_updates_buf="", token="", base_url=DEFAULT_BASE_URL, timeout=LONG_POLL_TIMEOUT):
    """长轮询收消息"""
    return _post_api("ilink/bot/getupdates", {
        "get_updates_buf": get_updates_buf,
    }, token=token, base_url=base_url, timeout=timeout + 5)


def _send_text(to, text, context_token, token="", base_url=DEFAULT_BASE_URL):
    """发文本消息"""
    return _post_api("ilink/bot/sendmessage", {
        "msg": {
            "from_user_id": "",
            "to_user_id": to,
            "client_id": uuid.uuid4().hex[:16],
            "message_type": 2,
            "message_state": 2,
            "item_list": [{"type": 1, "text_item": {"text": text}}],
            "context_token": context_token,
        }
    }, token=token, base_url=base_url)


def _send_voice_message(to, audio_data_b64, duration_ms, context_token,
                        token="", base_url=DEFAULT_BASE_URL, fmt="silk"):
    """发语音消息 (message_type=34)"""
    item = {
        "type": 34,
        "voice_item": {
            "voice_data": audio_data_b64,
            "duration": duration_ms,
            "voice_format": fmt,
        },
    }
    return _post_api("ilink/bot/sendmessage", {
        "msg": {
            "from_user_id": "",
            "to_user_id": to,
            "client_id": uuid.uuid4().hex[:16],
            "message_type": 34,
            "message_state": 2,
            "item_list": [item],
            "context_token": context_token,
        }
    }, token=token, base_url=base_url)


def _send_image_message(to, image_data_b64, context_token,
                        token="", base_url=DEFAULT_BASE_URL, image_type="png"):
    """发图片消息 (message_type=3)"""
    item = {
        "type": 3,
        "image_item": {
            "image_data": image_data_b64,
            "image_format": image_type,
        },
    }
    return _post_api("ilink/bot/sendmessage", {
        "msg": {
            "from_user_id": "",
            "to_user_id": to,
            "client_id": uuid.uuid4().hex[:16],
            "message_type": 3,
            "message_state": 2,
            "item_list": [item],
            "context_token": context_token,
        }
    }, token=token, base_url=base_url)


def _send_emoji_message(to, emoji_md5, context_token,
                        token="", base_url=DEFAULT_BASE_URL):
    """发表情消息 (message_type=47)"""
    item = {
        "type": 47,
        "emoji_item": {
            "md5": emoji_md5,
        },
    }
    return _post_api("ilink/bot/sendmessage", {
        "msg": {
            "from_user_id": "",
            "to_user_id": to,
            "client_id": uuid.uuid4().hex[:16],
            "message_type": 47,
            "message_state": 2,
            "item_list": [item],
            "context_token": context_token,
        }
    }, token=token, base_url=base_url)


# ═══════════════════════════════════════════════
# 异步编排器调用（process_message 是 async 的）
# ═══════════════════════════════════════════════

def _run_async_coro(coro):
    """在新事件循环中运行一个协程，返回结果。"""
    return asyncio.run(coro)


def _call_user_manager(mgr, user_id, text, attachments=None):
    """
    调用女友管理器处理消息（多用户路由）。

    process_message 是 async 的，但轮询/消息线程是同步的。旧实现每条消息
    asyncio.run 开一个**新事件循环**，而 orchestrator/session_locks 按会话
    缓存 asyncio.Lock 跨线程复用 → 同用户连发第二条时锁挂在新循环上，
    唤醒永远丢失（P1-审查 item27）。现统一委托进程常驻共享循环。
    attachments: 多模态附件（图片 content part 列表），可为 None。
    """
    from utils.async_utils import run_on_shared_loop

    coro = mgr.process_message(user_id, text, attachments=attachments)
    try:
        return run_on_shared_loop(coro, timeout=120)
    except concurrent.futures.TimeoutError:
        logger.warning("处理消息超时 (user=%s)", user_id)
        return {"reply": "", "error": "timeout"}


def _log_msg_task_failure(future: "concurrent.futures.Future") -> None:
    """P1-审查 item26：submit 后的 Future 被丢弃 → 消息线程内崩溃完全静默。

    挂 done 回调把异常打出来（含堆栈），否则一条消息消失得无声无息。
    """
    if future.cancelled():
        return
    exc = future.exception()
    if exc is not None:
        logger.error("[wx][step=msg_task_failed] 消息处理线程崩溃: %s", exc, exc_info=exc)


# ═══════════════════════════════════════════════
# 主连接器
# ═══════════════════════════════════════════════

class WeChatConnector:
    """
    微信连接器 — 直连微信 API（多用户版）

    用法:
        # 遗留：管理员全局通道（不推荐新代码）
        connector = WeChatConnector(user_manager)
        # 推荐：每人独立通道
        connector = WeChatConnector(
            user_manager,
            owner_user_id=42,
            session_dir=Path("data/wechat_sessions/42/slot0"),
        )
        connector.run()
    """

    def __init__(
        self,
        user_manager,
        base_url=DEFAULT_BASE_URL,
        owner_user_id: int | None = None,
        slot: int = 0,
        session_dir: Path | str | None = None,
        credentials_path: str | None = None,
        state_path: str | None = None,
        qrcode_path: str | None = None,
        context_tokens_path: str | None = None,
    ):
        self.user_manager = user_manager
        self.orchestrator = getattr(user_manager, "_orch", None)
        self.base_url = base_url
        self.owner_user_id = owner_user_id
        self.slot = int(slot)
        self.session_dir = Path(session_dir) if session_dir else None
        # per-user 路径：owner 通道必须显式提供；遗留全局通道用模块级默认
        self._credentials_path = credentials_path or (
            str(self.session_dir / "credentials.json") if self.session_dir else CREDENTIALS_PATH
        )
        self._state_path = state_path or (
            str(self.session_dir / "state.json") if self.session_dir else str(_STATE_FILE)
        )
        self._qrcode_path = qrcode_path or (
            str(self.session_dir / "qrcode.json") if self.session_dir else None
        )
        self._context_tokens_path = context_tokens_path or (
            str(self.session_dir / "context_tokens.json")
            if self.session_dir
            else CONTEXT_TOKENS_PATH
        )
        self.token = ""
        self.bot_id = ""
        self.started_at = 0
        self._stop = False
        self._get_updates_buf = ""
        # P1-审查 item26：去重表 / context_tokens / _last_user_id / 计数
        # 都在 _msg_executor 多线程里被读写，旧实现裸奔（OrderedDict 的
        # `in` + 赋值两步非原子 → 同一条消息可被双线程各处理一次）。
        self._state_lock = threading.RLock()
        self._received_msgs: OrderedDict = OrderedDict()  # 有序字典，支持按插入顺序淘汰
        self._context_tokens: dict = {}  # {user_id: {"token": str, "ts": float}}
        self._load_context_tokens()

        # ── 对话内追问状态（见文件头「对话内追问」）──
        self._pending_followups: dict[str, dict[str, Any]] = {}  # {user_id: {step,due,last_reply}}
        self._followup_lock = threading.Lock()
        self._followup_daily: dict[str, int] = {}   # {user_id: 当日已追问条数}
        self._followup_daily_date = ""
        self._last_user_id: str = ""
        # 好友自选角色（P1-审查 item28 接线）：peer_wxid → (菜单过期时刻, 发菜单时的卡列表快照)
        self._peer_choice_pending: dict[str, tuple[float, list[dict]]] = {}
        # 每条消息在独立线程中处理，避免阻塞轮询循环
        self._msg_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=2 if owner_user_id is not None else 4,
            thread_name_prefix=f"wx_msg_{owner_user_id or 'legacy'}",
        )
        # 统计
        self._messages_today = 0
        self._last_day = time.strftime("%Y-%m-%d")
        self._last_activity = 0
        self._reconnect_attempts = 0

    def _session_key(self, peer_wxid: str) -> str:
        """会话隔离键：owner 通道下 peer 好友。遗留全局通道保持 peer_wxid 原样。"""
        if self.owner_user_id is None:
            return peer_wxid
        return f"{int(self.owner_user_id)}:{peer_wxid}"

    @staticmethod
    def _peer_wxid_from_session(session_key: str) -> str:
        """从会话隔离键还原裸 peer wxid（`N:wxid` → `wxid`；裸形态原样返回）。

        2026-09-20 修复追问链 ret=-3：`_schedule_followup` 登记的键是 session_key，
        而 `_send_text` 的 `to_user_id` 与 `_context_tokens` 的键都是**裸 wxid** —
        旧实现直接拿 session_key 当发送目标与查 token，两者都错（invalid arguments +
        token 恒空），追问全线失败。
        """
        m = re.match(r"^\d+:(.+)$", session_key or "")
        return m.group(1) if m else session_key

    def _merge_session_state(self, updates: dict) -> dict:
        if self.owner_user_id is not None:
            return update_session_state(self.owner_user_id, self.slot, updates)
        return _merge_state(updates)

    def _load_local_state(self) -> dict:
        if self.owner_user_id is not None:
            return load_session_state(self.owner_user_id, self.slot)
        return _load_state()

    def _save_local_qr(self, qrcode_url: str = "", status: str = "waiting") -> None:
        if self.owner_user_id is not None:
            save_session_qrcode(self.owner_user_id, self.slot, qrcode_url, status)
        else:
            _save_qr_to_file(qrcode_url, status)

    def _resolve_send_target(self, to_user: str, kind: str) -> str:
        """主动发送目标解析（P1-审查 item30：隔离护栏补腿）。

        owner 通道必须显式 to_user —— 禁止回退到 `_last_user_id`（那可能是
        别人的会话）；遗留全局通道保留回退。返回空串表示应拒绝发送。
        """
        if self.owner_user_id is not None:
            if not to_user:
                logger.warning(
                    "用户通道主动发送%s被拒绝：必须显式指定 to_user（owner=%s）",
                    kind, self.owner_user_id,
                )
                return ""
            return to_user
        return to_user or self._last_user_id

    def send_text(self, text: str, to_user: str = "") -> bool:
        """主动发送文本消息（供外部调用）

        ⚠️ 用户独立通道：禁止依赖 `_last_user_id` 回退到可能属于他人会话的目标。
        owner 通道必须显式传 to_user（好友 wxid）。
        """
        target = self._resolve_send_target(to_user, "文本")
        if not target or not self.token:
            logger.warning("微信主动发送失败: 无目标用户或未登录")
            return False
        try:
            context_token = self._get_context_token(target)
            resp = _send_text(
                to=target, text=text,
                context_token=context_token,
                token=self.token, base_url=self.base_url,
            )
            ok, errmsg = _api_ok(resp)
            if not ok:
                logger.warning(
                    "微信主动发送失败: %s | target=%s context_token=%s text=%.30s",
                    errmsg, target,
                    "有" if context_token else "空（用户需先给机器人发一条消息）",
                    text,
                )
                return False
            logger.info("微信主动发送成功: %s", text[:30])
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("微信主动发送失败: %s", e)
            return False

    def send_voice(self, audio_bytes: bytes, to_user: str = "",
                   duration_ms: int = 0, fmt: str = "silk") -> bool:
        """发送语音消息（2026-09-19 起校验业务返回码，见 send_text）"""
        target = self._resolve_send_target(to_user, "语音")
        if not target or not self.token or not audio_bytes:
            logger.warning("发送语音失败: 无目标用户或未登录或无音频数据")
            return False
        try:
            audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
            context_token = self._get_context_token(target)
            resp = _send_voice_message(
                to=target, audio_data_b64=audio_b64,
                duration_ms=duration_ms, context_token=context_token,
                token=self.token, base_url=self.base_url, fmt=fmt,
            )
            ok, errmsg = _api_ok(resp)
            if not ok:
                logger.warning(
                    "语音发送失败: %s | target=%s context_token=%s",
                    errmsg, target, "有" if context_token else "空",
                )
                return False
            logger.info("语音发送成功: %d bytes, fmt=%s", len(audio_bytes), fmt)
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("语音发送失败: %s", e)
            return False

    def send_image(self, image_bytes: bytes, to_user: str = "",
                   image_type: str = "png") -> bool:
        """发送图片消息（2026-09-19 起校验业务返回码，见 send_text）"""
        target = self._resolve_send_target(to_user, "图片")
        if not target or not self.token or not image_bytes:
            logger.warning("发送图片失败: 无目标用户或未登录或无图片数据")
            return False
        try:
            image_b64 = base64.b64encode(image_bytes).decode("utf-8")
            context_token = self._get_context_token(target)
            resp = _send_image_message(
                to=target, image_data_b64=image_b64,
                context_token=context_token,
                token=self.token, base_url=self.base_url,
                image_type=image_type,
            )
            ok, errmsg = _api_ok(resp)
            if not ok:
                logger.warning(
                    "图片发送失败: %s | target=%s context_token=%s",
                    errmsg, target, "有" if context_token else "空",
                )
                return False
            logger.info("图片发送成功: %d bytes", len(image_bytes))
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("图片发送失败: %s", e)
            return False

    def send_emoji(self, emoji_md5: str, to_user: str = "") -> bool:
        """发送表情消息（2026-09-19 起校验业务返回码，见 send_text）"""
        target = self._resolve_send_target(to_user, "表情")
        if not target or not self.token or not emoji_md5:
            logger.warning("发表情失败: 无目标用户或未登录或无表情数据")
            return False
        try:
            context_token = self._get_context_token(target)
            resp = _send_emoji_message(
                to=target, emoji_md5=emoji_md5,
                context_token=context_token,
                token=self.token, base_url=self.base_url,
            )
            ok, errmsg = _api_ok(resp)
            if not ok:
                logger.warning(
                    "表情发送失败: %s | target=%s context_token=%s",
                    errmsg, target, "有" if context_token else "空",
                )
                return False
            logger.info("表情发送成功: md5=%s", emoji_md5)
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("表情发送失败: %s", e)
            return False

    # ── 登录 ──

    def login(self):
        """登微信 — 先放二维码，再试保存的凭证（per-user 路径）"""
        # 0. 快速重连：如果当前已有 token（从 _poll_loop 调用的重连），先试一次
        if self.token:
            logger.info("尝试用现有 token 快速重连...")
            try:
                test = _get_updates("", self.token, self.base_url, timeout=5)
                if test.get("ret") != -14 and test.get("errcode") != -14:
                    logger.info("现有 token 仍有效，重连成功")
                    self._save_local_qr("", "connected")
                    return True
            except Exception as e:  # noqa: BLE001
                logger.debug(f"快速重连测试失败: {e}")
            logger.warning("现有 token 已失效，需要重新扫码")
            self.token = ""

        # 1. 先生成二维码（确保前端随时能读到）
        qrcode = None
        qrcode_url = ""
        try:
            qr_resp = _fetch_qr_code(self.base_url)
            qrcode = qr_resp.get("qrcode", "")
            qrcode_url = qr_resp.get("qrcode_img_content", "")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"生成二维码失败（不影响后续）: {e}")

        # 2. 有二维码就先写到文件
        if qrcode_url:
            self._save_local_qr(qrcode_url, "waiting")
            print(f"\n二维码已就绪: {qrcode_url}\n")
        else:
            self._save_local_qr("", "idle")

        # 3. 再试保存的凭证
        creds = _load_credentials(self._credentials_path)
        if creds.get("token"):
            logger.info("有保存的凭证，尝试快速登录...")
            self.token = creds["token"]
            self.base_url = creds.get("base_url", self.base_url)
            self.bot_id = creds.get("bot_id", "")
            try:
                test = _get_updates("", self.token, self.base_url, timeout=3)
                if test.get("ret") != -14 and test.get("errcode") != -14:
                    logger.info("保存的凭证有效，跳过扫码")
                    self._save_local_qr(qrcode_url or "", "connected")
                    return True
            except Exception as e:  # noqa: BLE001
                logger.debug(f"凭证快速测试失败: {e}")
            logger.warning("保存的凭证已过期，等待扫码登录")
            self.token = ""

        # 4. 没有可用凭证 → 等扫码
        if not qrcode:
            logger.error("没有二维码也没有凭证，无法登录")
            return False

        logger.info("等待微信扫码...")
        # 轮询扫码状态
        refresh_count = 0
        deadline = time.time() + QR_LOGIN_TIMEOUT_S

        while time.time() < deadline:
            if self._stop:
                return False

            try:
                status_resp = _poll_qr_status(qrcode, self.base_url)
            except Exception as e:  # noqa: BLE001
                logger.error(f"轮询二维码失败: {e}")
                time.sleep(3)
                continue

            status = status_resp.get("status", "wait")

            if status == "wait":
                pass
            elif status == "scaned":
                print("已扫码，请在手机上确认...")
            elif status == "expired":
                refresh_count += 1
                if refresh_count >= QR_MAX_REFRESHES:
                    print("二维码刷太多次了，重启试试")
                    return False
                print(f"二维码过期，刷新中（{refresh_count}/{QR_MAX_REFRESHES}）...")
                try:
                    qr_resp = _fetch_qr_code(self.base_url)
                    qrcode = qr_resp.get("qrcode", "")
                    qrcode_url = qr_resp.get("qrcode_img_content", "")
                    self._save_local_qr(qrcode_url, "waiting")
                    print(f"新二维码: {qrcode_url}")
                except Exception as e:  # noqa: BLE001
                    logger.error(f"刷新二维码失败: {e}")
                    return False
            elif status == "confirmed":
                bot_token = status_resp.get("bot_token", "")
                bot_id = status_resp.get("ilink_bot_id", "")
                result_base_url = status_resp.get("baseurl", self.base_url)

                if not bot_token or not bot_id:
                    logger.error("登录确认但没拿到 token 或 bot_id")
                    return False

                # 存凭证，下次直接连（per-user 路径）
                _save_credentials(
                    {
                        "token": bot_token,
                        "base_url": result_base_url,
                        "bot_id": bot_id,
                        "user_id": status_resp.get("ilink_user_id", ""),
                        "owner_user_id": self.owner_user_id,
                        "slot": self.slot,
                    },
                    self._credentials_path,
                )

                self._save_local_qr(qrcode_url, "connected")
                print(f"微信登录成功！bot_id={bot_id} owner={self.owner_user_id}")

                self.token = bot_token
                self.base_url = result_base_url
                self.bot_id = bot_id
                self._reconnect_attempts = 0
                self._merge_session_state({
                    "connected": True,
                    "bot_id": bot_id,
                    "status": "connected",
                    "last_activity": time.time(),
                })
                return True

            time.sleep(QR_POLL_INTERVAL)

        print("二维码登录超时")
        self._save_local_qr("", "expired")
        self._merge_session_state({"connected": False, "status": "idle"})
        return False

    # ── 主循环 ──

    def run(self):
        """完整流程：登录 → 收消息 → 传给小十 → 发回复"""
        global _connector
        if self.owner_user_id is None:
            # 仅遗留全局通道写入单例；用户通道用 Registry
            _connector = self

        if not self.login():
            logger.error("微信登录失败 owner=%s", self.owner_user_id)
            self._reconnect_attempts += 1
            self._merge_session_state({
                "connected": False,
                "status": "error",
                "reconnect_attempts": self._reconnect_attempts,
            })
            return

        self.started_at = time.time()
        self._merge_session_state({
            "connected": True,
            "status": "connected",
            "started_at": self.started_at,
            "bot_id": self.bot_id,
            "reconnect_attempts": 0,
        })
        logger.info("微信登录成功，开始收消息... owner=%s slot=%s", self.owner_user_id, self.slot)
        # 对话内追问守护线程（只启一次；run() 可能因重连被多次调用）
        if not getattr(self, "_followup_thread_started", False):
            self._followup_thread_started = True
            threading.Thread(
                target=self._followup_thread, name="wx-followup", daemon=True,
            ).start()
        self._poll_loop()
        # 轮询退出时持久化断开状态（保留 started_at 方便排查）
        self._merge_session_state({"connected": False, "status": "disconnected"})

    def _poll_loop(self):
        """消息轮询循环"""
        logger.info("进入消息轮询")
        consecutive_failures = 0
        session_errors = 0

        while not self._stop:
            try:
                resp = _get_updates(
                    self._get_updates_buf,
                    token=self.token,
                    base_url=self.base_url,
                )

                ret = resp.get("ret", 0)
                errcode = resp.get("errcode", 0)

                if resp.get("timeout"):
                    # P1-审查 item31：_post_api 超时伪装成 {"ret":0,"msgs":[]}，
                    # 旧轮询把它当健康空轮询并把 consecutive_failures 清零 ——
                    # 网络半死状态下会零退避地持续快轮。按失败处理走退避。
                    consecutive_failures += 1
                    logger.warning(
                        "轮询请求超时（按失败处理，连续 %d/%d 次）owner=%s",
                        consecutive_failures, MAX_CONSECUTIVE_FAILURES, self.owner_user_id,
                    )
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        time.sleep(BACKOFF_DELAY)
                    else:
                        time.sleep(RETRY_DELAY)
                    continue

                if ret != 0 or errcode != 0:
                    if errcode == -14 or ret == -14:
                        session_errors += 1
                        if session_errors < 3:
                            logger.warning(
                                "会话错误 -14（%d/3），%d秒后重试...",
                                session_errors, RETRY_DELAY * session_errors
                            )
                            time.sleep(RETRY_DELAY * session_errors)
                            continue
                        logger.error("会话过期（连续%d次-14），重新登录...", session_errors)
                        self._save_local_qr("", "idle")
                        if os.path.exists(self._credentials_path):
                            os.remove(self._credentials_path)
                        if self.login():
                            self._get_updates_buf = ""
                            consecutive_failures = 0
                            session_errors = 0
                            continue
                        else:
                            logger.error("重新登录失败，5分钟后重试")
                            time.sleep(300)
                            continue

                    consecutive_failures += 1
                    logger.error(f"轮询错误: ret={ret} errcode={errcode}")
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        time.sleep(BACKOFF_DELAY)
                    else:
                        time.sleep(RETRY_DELAY)
                    continue

                consecutive_failures = 0
                session_errors = 0

                new_buf = resp.get("get_updates_buf", "")
                if new_buf:
                    self._get_updates_buf = new_buf

                msgs = resp.get("msgs", [])
                if msgs:
                    self._last_activity = time.time()
                    self._merge_session_state({"last_activity": self._last_activity})
                for raw_msg in msgs:
                    # 修复 P0-WX2：消息处理放到独立线程，避免阻塞轮询循环
                    # 导致连接状态抖动或心跳超时。
                    # P1-审查 item26：Future 必须挂失败回调，否则线程内异常静默吞掉。
                    fut = self._msg_executor.submit(self._handle_message, raw_msg)
                    fut.add_done_callback(_log_msg_task_failure)

            except Exception as e:  # noqa: BLE001
                if self._stop:
                    break
                consecutive_failures += 1
                logger.error(f"轮询异常: {e}")
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    time.sleep(BACKOFF_DELAY)
                else:
                    time.sleep(RETRY_DELAY)

        logger.info("消息轮询结束")

    def _get_context_token(self, user_id: str) -> str:
        """获取用户的 context_token；**过期不返回**。

        过期的 token 发出去必然换来 `prepare failed`，不如显式返回空 ——
        这样失败日志能直接指出「会话窗口已失效，用户需重新发一条消息」。
        """
        entry = self._context_tokens.get(user_id)
        if entry is None:
            return ""
        if isinstance(entry, dict):
            ts = float(entry.get("ts", 0) or 0)
            if ts and (time.time() - ts) > _CONTEXT_TOKENS_TTL:
                return ""
            return entry.get("token", "")  # type: ignore[no-any-return]
        # 兼容旧格式（直接存储的字符串）
        return str(entry)

    def _load_context_tokens(self) -> None:
        """从磁盘恢复 context_token（重启后仍可在窗口期内主动发送）。"""
        path = self._context_tokens_path
        try:
            if not os.path.exists(path):
                return
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return
            now = time.time()
            alive = {
                uid: entry for uid, entry in data.items()
                if isinstance(entry, dict)
                and entry.get("token")
                and (now - float(entry.get("ts", 0) or 0)) <= _CONTEXT_TOKENS_TTL
            }
            self._context_tokens = alive
            logger.info(
                "已恢复 %d 条 context_token（跳过 %d 条过期/无效）",
                len(alive), len(data) - len(alive),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("context_token 恢复失败（忽略）: %s", e)

    def _save_context_tokens(self) -> None:
        """落盘 context_token（锁内快照 + 原子写），避免服务重启丢失会话窗口。"""
        path = self._context_tokens_path
        try:
            with self._state_lock:
                snapshot = dict(self._context_tokens)
            _atomic_write_json(path, snapshot)
        except Exception as e:  # noqa: BLE001
            logger.warning("context_token 保存失败（忽略）: %s", e)

    def _cleanup_context_tokens(self):
        """清理过期的 context_token 条目，防止内存泄漏（与消息线程互斥，item26）"""
        with self._state_lock:
            now = time.time()
            expired = [
                uid for uid, entry in self._context_tokens.items()
                if isinstance(entry, dict) and (now - entry.get("ts", 0)) > _CONTEXT_TOKENS_TTL
            ]
            for uid in expired:
                del self._context_tokens[uid]
            if not expired:
                return
        self._save_context_tokens()
        logger.debug("Cleaned up %d expired context_tokens entries", len(expired))

    # ── 对话内追问（见文件头说明）─────────────────────────────

    def _memory_service(self) -> Any | None:
        """本会话记忆服务（对话历史唯一真源的入口），装配缺失时返回 None。"""
        orch = self.orchestrator
        comps = getattr(orch, "components", None) if orch else None
        return (comps or {}).get("memory")

    def _session_messages(self, session_key: str, keep: int = 10) -> list[dict[str, str]]:
        """按会话读最近若干条**持久化**对话（与主链同一真源）。"""
        getter = getattr(self._memory_service(), "get_chat_context", None)
        if getter is None or not session_key:
            return []
        try:
            messages, _summary = getter(session_id=session_key, keep_recent=keep)
        except Exception as e:  # noqa: BLE001
            logger.warning("[wx][step=history_read_failed] session=%s error=%s", session_key, e)
            return []
        return list(messages or [])

    def _record_outbound(self, text: str, session_key: str) -> None:
        """她主动说的话回写会话历史（自问自答根治；写入唯一 owner 在记忆层）。

        🔴 2026-09-22：必须带 character_id —— 出站 assistant 行此前无归属，
        而角色过滤含 `OR character_id = ''`（空归属恒保留）→ 切角色后新角色
        继承上一角色台词。
        """
        recorder = getattr(self._memory_service(), "record_outbound_message", None)
        if recorder is None or not session_key or not text:
            return
        try:
            recorder(
                message=text,
                session_id=session_key,
                character_id=self._resolve_character_id(session_key),
                channel="wechat",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("[wx][step=outbound_record_failed] session=%s error=%s", session_key, e)

    @staticmethod
    def _resolve_character_id(session_key: str) -> str:
        """会话键 → 角色 id（唯一 owner：utils.character_resolver）。

        延迟导入避免与 utils 层形成模块级循环依赖。
        """
        try:
            from utils import character_resolver

            try:
                from api.deps import deps

                gf = getattr(deps, "gf", None)
            except Exception:  # noqa: BLE001
                gf = None
            return character_resolver.resolve_character_id(session_key, gf)
        except Exception as e:  # noqa: BLE001
            logger.debug("[wx] 角色解析失败 session=%s: %s", session_key, e)
            return "default"

    def _schedule_followup(self, user_id: str, bot_reply: str) -> None:
        """回复成功后登记一次待发追问（参数取自 web 控制端可调的配置）。"""
        if not user_id or not bot_reply:
            return
        cfg = read_follow_up_config()
        if not cfg["enabled"]:
            return
        with self._followup_lock:
            if self._in_quiet_hours():
                return
            self._pending_followups[user_id] = {
                "step": 0,
                "due": time.time() + float(cfg["delay1_seconds"]),
                "last_reply": bot_reply,
            }

    def _cancel_followup(self, user_id: str) -> None:
        """用户接话 → 取消待发追问。"""
        with self._followup_lock:
            self._pending_followups.pop(user_id, None)

    def _in_quiet_hours(self) -> bool:
        """免打扰时段判定（读跨 worker 真源 data/scheduler_config.json）。"""
        start, end = 23, 7
        try:
            cfg_path = Path(__file__).resolve().parent.parent / "data" / "scheduler_config.json"
            if cfg_path.exists():
                qh = (json.loads(cfg_path.read_text(encoding="utf-8")) or {}).get("quiet_hours") or {}
                start, end = int(qh.get("start", 23)), int(qh.get("end", 7))
        except Exception:  # noqa: BLE001
            pass
        hour = time.localtime().tm_hour
        if start == end:
            return False
        return (start <= hour < end) if start < end else (hour >= start or hour < end)

    def _followup_budget_ok(self, user_id: str) -> bool:
        """单用户每日追问上限（web 可调；独立预算，不吃 ASE 的 8 条）。"""
        today = time.strftime("%Y-%m-%d")
        if today != self._followup_daily_date:
            self._followup_daily_date = today
            self._followup_daily.clear()
        return self._followup_daily.get(user_id, 0) < int(read_follow_up_config()["daily_max"])

    def _followup_thread(self) -> None:
        """守护线程：到点且用户仍未接话 → 生成并发送一条追问。"""
        while not self._stop:
            time.sleep(_FOLLOWUP_TICK)
            now = time.time()
            due: list[tuple[str, dict[str, Any]]] = []
            with self._followup_lock:
                for uid, st in list(self._pending_followups.items()):
                    if now >= st.get("due", 0):
                        due.append((uid, st))
                        del self._pending_followups[uid]
            for uid, st in due:
                try:
                    self._send_followup(uid, st)
                except Exception as e:  # noqa: BLE001
                    logger.warning("[wx][step=followup_error] user=%s error=%s", uid, e)

    def _send_followup(self, user_id: str, st: dict[str, Any]) -> None:
        cfg = read_follow_up_config()
        if not cfg["enabled"]:
            return
        if self._in_quiet_hours() or not self._followup_budget_ok(user_id):
            return
        if not user_id or not self.token:
            return

        step = int(st.get("step", 0)) + 1

        # ⚠️ 2026-09-19 用户反馈：「追问没有和上下文形成逻辑，而是强行地插入一句
        # 「在吗？」「人呢？」」—— 旧实现只把**上一句 AI 回复**塞进 prompt。
        # 2026-09-21 再根治：上下文不再另起一份进程内 deque（重启即空、与主链
        # 两套真源），直接读持久化 chat_history；同时**发前复查最后一条是谁说的**
        # ——旧取消逻辑靠内存里的 pending 表，线程取走待发后用户接话就取消不掉。
        history = self._session_messages(user_id, keep=_FOLLOWUP_CONTEXT_TURNS)
        if history and history[-1].get("role") == "user":
            with self._followup_lock:
                self._pending_followups.pop(user_id, None)
            logger.info("[wx][step=followup_skip_replied] user=%s", user_id)
            return
        last_reply = str(st.get("last_reply", "") or (history[-1].get("content") if history else ""))[:80]
        # 往来历史经 history= 以**正确的 role** 传给模型（不是"我/对方"文本转写）——
        # 自问自答的一条实证成因：模型分不清哪句是自己说的，转写标签帮不了它。
        hist = [
            {"role": "assistant" if m.get("role") == "assistant" else "user",
             "content": str(m.get("content", ""))[:200]}
            for m in history
            if str(m.get("content", "")).strip()
        ]
        prompt = (
            "上面是你们的真实对话记录，你说了最后那句"
            f"「{last_reply}」之后对方就没再回你。\n"
            "现在你自己再补一句 —— **必须接着上面的聊天内容**："
            "可以问对方刚提到的那件具体事，"
            "也可以就那件事说一句自己的感受或想法。\n"
            "不要重复你刚说过的话。口语化，15 字以内，只输出这一句话。"
        )
        text = self._generate_followup(
            prompt, last_reply=last_reply, session_key=user_id, history=hist,
        )
        if not text:
            return
        # 2026-09-20 修复：user_id 是会话隔离键（`N:wxid`），而发送 API 与
        # context_token 查找要的都是**裸 peer wxid** —— 直接传会 ret=-3。
        peer = self._peer_wxid_from_session(user_id)
        if not self.send_text(text, to_user=peer):
            logger.warning("[wx][step=followup_send_failed] user=%s step=%d", user_id, step)
            return
        # 自问自答根治：这句追问必须进历史，否则下一轮她不记得自己问过什么
        self._record_outbound(text, user_id)

        self._followup_daily[user_id] = self._followup_daily.get(user_id, 0) + 1
        logger.info(
            "[wx][step=followup_sent] user=%s step=%d text=%r", user_id, step, text[:60],
        )
        # 第二轮（delay2）之后不再追 —— 追问上限固定为 2 次，避免无限骚扰；
        # 每日总量由 daily_max 兜底。
        if step < 2:
            with self._followup_lock:
                if user_id not in self._pending_followups:
                    self._pending_followups[user_id] = {
                        "step": step,
                        "due": time.time() + float(cfg["delay2_seconds"]),
                        "last_reply": text,
                    }

    def _followup_system_prompt(self, session_key: str) -> str:
        """追问用的角色 system —— 与主链**同一身份源**。

        2026-09-21 自问自答根治：旧实现 `_generate_followup` 是 `chat_sync(query=...)`
        **裸调用**（无 system、无历史），模型以通用助手口吻产出，人称与上一句她的
        回复对不上，用户侧体感就是「她在跟自己说话」。这里复用 persona 服务，
        身份只来自该会话绑定的角色卡（内置「十四」与文件卡同构，无第二套路径）。
        """
        persona = (getattr(self.orchestrator, "components", None) or {}).get("persona")
        builder = getattr(persona, "build_system_prompt", None)
        if builder is None:
            return ""
        cid = ""
        try:
            get_char = getattr(self.user_manager, "get_user_character", None)
            if get_char is not None and session_key:
                cid = str(get_char(session_key) or "")
        except Exception as e:  # noqa: BLE001
            logger.debug("[wx] 追问角色解析失败 session=%s: %s", session_key, e)
        try:
            return str(builder(character_id=cid or None) or "")
        except Exception as e:  # noqa: BLE001
            logger.warning("[wx][step=followup_persona_failed] session=%s error=%s", session_key, e)
            return ""

    def _generate_followup(
        self,
        prompt: str,
        last_reply: str = "",
        session_key: str = "",
        history: list[dict[str, str]] | None = None,
    ) -> str:
        """用角色口吻 + 真实往来历史生成一句追问；失败/不合规返回空串（宁可不发）。"""
        orch = self.orchestrator
        llm = (getattr(orch, "components", None) or {}).get("llm") if orch else None
        if llm is None or not hasattr(llm, "chat_sync"):
            return ""
        try:
            raw = llm.chat_sync(
                query=prompt,
                system_prompt=self._followup_system_prompt(session_key),
                history=history or None,
                max_tokens=60,
                temperature=0.95,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("[wx][step=followup_gen_failed] error=%s", e)
            return ""
        text = str(raw or "").strip().strip('"\'“”‘’「」『』')
        if "\n" in text:
            text = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        # 简单的合规守卫：过短/过长/含推理腔/含空话催促 一律丢弃（宁可不发，也不发怪话）
        if len(text) < 2 or len(text) > _FOLLOWUP_MAX_CHARS:
            return ""
        if any(marker in text for marker in ("特定时间点", "时间段", "作为AI", "要求：", "消息：")):
            return ""
        # 与内容无关的通用催促语：模型偶尔会无视指令直接吐这些
        if any(generic in text for generic in ("在吗", "人呢", "怎么不理", "睡着了", "还在吗")):
            return ""
        # 与上一句几乎重复的也丢弃
        if last_reply and text.strip() == last_reply.strip():
            return ""
        return text

    # ── 好友「角色」自选指令链（P1-审查 item28：整链接线）────────
    # AGENTS v1.16 记载的「回复『角色』弹菜单、回序号切换」此前只有纯函数
    # （peer_character.py）而无任何调用者 —— 功能实际不存在。此处接线：
    # _handle_message 在进主 LLM 前拦截显式指令/待确认序号，切换后
    # 落库（wechat_peer_preferences）+ 热更绑定缓存与运行中实例。

    def _send_peer_text(self, to: str, text: str) -> bool:
        """角色指令链的底层直发（不走主 LLM 链）。"""
        try:
            resp = _send_text(
                to=to, text=text,
                context_token=self._get_context_token(to),
                token=self.token, base_url=self.base_url,
            )
            ok, errmsg = _api_ok(resp)
            if not ok:
                logger.warning("[wx][step=peer_cmd_send_failed] to=%s errmsg=%s", to, errmsg)
            return ok
        except Exception as e:  # noqa: BLE001
            logger.warning("[wx][step=peer_cmd_send_error] to=%s error=%s", to, e)
            return False

    def _apply_peer_character(self, peer_wxid: str, card_id: str) -> bool:
        """把好友选择的角色持久化并热更到运行中实例（同步线程 → 共享循环桥接）。"""
        from utils.async_utils import run_on_shared_loop

        owner = self.owner_user_id
        if owner is None or not card_id:
            return False

        async def _persist() -> None:
            from api.database import _async_session
            from wechat_direct.peer_character import set_peer_preference

            async with _async_session() as db:
                await set_peer_preference(db, int(owner), peer_wxid, card_id)
                await db.commit()

        try:
            run_on_shared_loop(_persist(), timeout=10)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[wx][step=peer_char_persist_failed] owner=%s peer=%s card=%s error=%s",
                owner, peer_wxid, card_id, e,
            )
            return False

        mgr = self.user_manager
        if mgr is not None:
            pref_key = f"pref:{int(owner)}:{peer_wxid}"
            pref_data = {
                "wxid": peer_wxid,
                "user_id": int(owner),
                "character_card_id": card_id,
            }

            async def _sync_cache() -> None:
                await mgr.upsert_binding(pref_key, pref_data)

            try:
                run_on_shared_loop(_sync_cache(), timeout=5)
            except Exception as e:  # noqa: BLE001
                logger.warning("[wx][step=peer_char_cache_sync_failed] %s: %s", pref_key, e)
            try:
                # 会话实例热切（首条消息后实例已存在；不存在时返回 False，
                # 新实例首建会经绑定缓存拿到正确角色）
                mgr.set_user_character(f"{int(owner)}:{peer_wxid}", card_id)
            except Exception as e:  # noqa: BLE001
                logger.warning("[wx][step=peer_char_instance_sync_failed] %s: %s", peer_wxid, e)
        logger.info(
            "[wx][step=peer_char_applied] owner=%s peer=%s card=%s", owner, peer_wxid, card_id,
        )
        return True

    def _try_peer_character_flow(self, peer_wxid: str, text: str) -> bool:
        """处理好友角色指令；返回 True = 该消息已被角色流消费，不再进主 LLM。"""
        from wechat_direct.peer_character import (
            build_character_menu,
            is_character_command,
            list_owner_characters,
            try_handle_character_choice,
        )

        owner = self.owner_user_id
        if owner is None or not peer_wxid:
            return False
        raw = (text or "").strip()
        if not raw:
            return False

        now = time.time()
        entry = self._peer_choice_pending.get(peer_wxid)
        if entry is not None and entry[0] < now:
            self._peer_choice_pending.pop(peer_wxid, None)
            entry = None

        if not is_character_command(raw) and entry is None:
            return False

        pending_view = {peer_wxid: entry} if entry is not None else {}
        # 序号映射到**发菜单时的卡列表快照**（菜单期间卡列表变化也不错位）
        cards = entry[1] if entry is not None else list_owner_characters(int(owner))
        act = try_handle_character_choice(raw, int(owner), peer_wxid, cards, pending_view)
        if act is None:
            # 越界/无卡片的裸数字 → 作废待确认，放行回普通对话（不困住用户）
            self._peer_choice_pending.pop(peer_wxid, None)
            return False

        if act["action"] == "show_menu":
            # 菜单永远展示最新卡列表（cards 无 pending 时已是最新）
            fresh = cards if entry is None else list_owner_characters(int(owner))
            self._peer_choice_pending[peer_wxid] = (now + _PEER_CHOICE_TTL, fresh)
            self._send_peer_text(peer_wxid, build_character_menu(fresh))
            return True

        self._peer_choice_pending.pop(peer_wxid, None)
        if self._apply_peer_character(peer_wxid, str(act.get("character_id") or "")):
            self._send_peer_text(peer_wxid, str(act.get("message", "已切换角色。")))
        else:
            self._send_peer_text(peer_wxid, "角色切换没有成功，稍后再回复「角色」试试")
        return True

    def _handle_message(self, raw_msg):
        """处理一条消息（全链路结构化日志：接收 → 路由 → LLM → 回复）"""
        msg_type = raw_msg.get("message_type", 0)
        if msg_type not in (1, 3, 34):  # 放行用户文本(1)/图片(3)/语音(34)，其余（系统通知、自发回显等）仍丢弃
            return

        msg_id = str(raw_msg.get("message_id", raw_msg.get("seq", "")))
        with self._state_lock:
            if msg_id in self._received_msgs:
                return
            self._received_msgs[msg_id] = True
            while len(self._received_msgs) > _RECEIVED_MSGS_MAX:
                self._received_msgs.popitem(last=False)
        self._cleanup_context_tokens()

        from_user = raw_msg.get("from_user_id", "")
        context_token = raw_msg.get("context_token", "")
        if context_token and from_user:
            with self._state_lock:
                self._context_tokens[from_user] = {"token": context_token, "ts": time.time()}
            # 落盘：服务重启后仍可在窗口期内主动发送（见 CONTEXT_TOKENS_PATH 注释）
            self._save_context_tokens()
        if from_user:
            with self._state_lock:
                self._last_user_id = from_user
            # 用户接话了 → 取消该用户的待发追问（追问只在"对方没接话"时才发）。
            # 2026-09-20 修复：待发追问以**会话隔离键**（`N:wxid`）登记，
            # 取消时必须用同一形态 —— 旧实现传裸 wxid 永远匹配不上，
            # 用户接话后追问照样发（追问时序错乱 + 白耗每日预算）。
            self._cancel_followup(self._session_key(from_user))

        items = raw_msg.get("item_list", [])
        text = ""
        voice_data = ""
        image_data = ""
        for item in items:
            item_type = item.get("type", 0)
            if item_type == 1:
                text_item = item.get("text_item", {})
                text = text_item.get("text", "")
            elif item_type == 34:
                voice_item = item.get("voice_item", {})
                voice_data = voice_item.get("voice_data", "")
            elif item_type == 3:
                image_item = item.get("image_item", {})
                image_data = image_item.get("image_data", "")

        if not text and not voice_data and not image_data:
            logger.debug("消息无文本/语音/图片内容 msg_id=%s user=%s", msg_id, from_user)
            return

        # ── 好友「角色」自选指令拦截（P1-审查 item28 接线）：仅纯文本消息参与 ──
        if (
            self.owner_user_id is not None
            and text.strip()
            and not voice_data
            and not image_data
            and self._try_peer_character_flow(from_user, text)
        ):
            return

        # ── 图片（V2）：image_data → 附件直传（B）或描述注入（A 降级）──
        attachments: list = []
        if image_data:
            mode, vision_model, max_bytes = self._image_mode()
            if mode != "off":
                from multimodal.image_attachment import build_attachments

                parts = build_attachments([image_data])
                oversized = len(image_data or "") > max_bytes
                if parts and not oversized and (mode == "direct" or vision_model):
                    attachments = parts  # B：多模态直传
                    logger.info(
                        "[wx][step=image_direct] msg_id=%s user=%s parts=%d",
                        msg_id, from_user, len(parts),
                    )
                elif mode in ("auto", "describe"):
                    desc = self._describe_image(image_data)  # A：降级为描述注入
                    if desc:
                        text = text or desc
                        logger.info(
                            "[wx][step=image_describe] msg_id=%s user=%s len=%d",
                            msg_id, from_user, len(desc),
                        )

        # ── 语音转文字（候选 A，2026-08-28）：voice_data(base64 silk) → ASR → text ──
        if not text and voice_data:
            asr_text = self._transcribe_voice(voice_data)
            if asr_text:
                text = asr_text
                logger.info("[wx][step=asr] msg_id=%s user=%s text=%r", msg_id, from_user, text[:60])
            else:
                logger.info("[wx][step=asr_unavailable] msg_id=%s user=%s", msg_id, from_user)

        today = time.strftime("%Y-%m-%d")
        with self._state_lock:
            if today != self._last_day:
                self._messages_today = 0
                self._last_day = today
            self._messages_today += 1
            _today_count = self._messages_today
        self._merge_session_state({"messages_today": _today_count})

        t_start = time.perf_counter()
        session_key = self._session_key(from_user)
        logger.info(
            "[wx][step=receive] msg_id=%s owner=%s peer=%s session=%s text=%r",
            msg_id, self.owner_user_id, from_user, session_key, text[:80],
        )

        try:
            result = _call_user_manager(self.user_manager, session_key, text, attachments)
            t_elapsed = time.perf_counter() - t_start
        except Exception as e:  # noqa: BLE001
            logger.exception(
                "[wx][step=route_error] msg_id=%s session=%s error=%s",
                msg_id, session_key, e,
            )
            try:
                from utils.fallback_lines import get_fallback_line
                from utils.reply_mode import read_reply_mode
                _exc_text = get_fallback_line(None, "exception", read_reply_mode())
            except Exception:  # noqa: BLE001
                _exc_text = "刚才好像出问题了，再说一次好吗"
            try:
                _send_text(
                    to=from_user, text=_exc_text,
                    context_token=self._get_context_token(from_user) or context_token,
                    token=self.token, base_url=self.base_url,
                )
            except Exception:  # noqa: BLE001
                logger.exception("[wx][step=notify_fail] msg_id=%s session=%s", msg_id, session_key)
            return

        if not isinstance(result, dict):
            logger.warning(
                "[wx][step=route_bad_result] msg_id=%s session=%s result_type=%s",
                msg_id, session_key, type(result),
            )
            result = {}

        reply = result.get("reply", "")
        error = result.get("error", "")
        process_time = result.get("process_time")

        if not reply:
            logger.warning(
                "[wx][step=empty_reply] msg_id=%s session=%s error=%s elapsed=%.2fs",
                msg_id, session_key, error or "unknown", t_elapsed,
            )
            # A2：兜底句角色化 + 当日去重；沉浸式无括号
            try:
                from utils.fallback_lines import get_fallback_line
                from utils.reply_mode import read_reply_mode
                # character_id 可能藏在 result 里；拿不到则用 default 池
                _cid = result.get("character_id") or result.get("character") or None
                reply = get_fallback_line(_cid, "empty_reply", read_reply_mode())
            except Exception:  # noqa: BLE001
                reply = "刚才没接上，你再说一句？"
        else:
            logger.info(
                "[wx][step=llm_done] msg_id=%s session=%s reply=%r elapsed=%.2fs llm_time=%s",
                msg_id, session_key, reply[:80], t_elapsed, process_time,
            )

        try:
            token = self._get_context_token(from_user) or context_token
            # 「一句一句发」：整段按换行/句末标点拆成多条短消息，段间加打字停顿
            segments = split_reply_for_wechat(reply) or [reply]
            for idx, seg in enumerate(segments):
                if idx:
                    time.sleep(_segment_delay(segments[idx - 1]))
                resp = _send_text(
                    to=from_user, text=seg,
                    context_token=token,
                    token=self.token, base_url=self.base_url,
                )
                # 2026-09-19：回复路径同样必须校验业务返回码 —— 旧实现只要不抛异常
                # 就记 [step=reply_sent]，接口 ret<0（如 prepare failed）时同样会被
                # 记成"已回复"，与主动消息那条链是同一种谎报。
                ok, errmsg = _api_ok(resp)
                if not ok:
                    logger.warning(
                        "[wx][step=reply_send_failed] msg_id=%s session=%s part=%d/%d error=%s",
                        msg_id, session_key, idx + 1, len(segments), errmsg,
                    )
                    return
            logger.info(
                "[wx][step=reply_sent] msg_id=%s session=%s parts=%d reply=%r",
                msg_id, session_key, len(segments), reply[:80],
            )
            # 回复成功 → 登记对话内追问（以最后一段作为"刚说的话"）
            # 往来历史不落内存副本 —— 追问侧直接读持久化 chat_history（唯一真源）
            self._schedule_followup(session_key, segments[-1] if segments else reply)
        except Exception as e:  # noqa: BLE001
            logger.exception(
                "[wx][step=reply_send_failed] msg_id=%s session=%s error=%s",
                msg_id, session_key, e,
            )
            return

        voice_result = result.get("voice")
        if voice_result:
            try:
                from voice.audio_converter import AudioFormatConverter
                converter = AudioFormatConverter()
                fmt = "silk"
                silk_audio = converter.to_silk(voice_result, "mp3")
                if silk_audio is None:
                    fmt = "amr"
                    silk_audio = converter.to_amr(voice_result, "mp3")
                if silk_audio:
                    duration_ms = result.get("voice_duration_ms", 3000)
                    self.send_voice(silk_audio, to_user=from_user,
                                    duration_ms=duration_ms, fmt=fmt)
            except Exception as e:  # noqa: BLE001
                logger.warning("[wx][step=voice_failed] msg_id=%s user=%s error=%s",
                               msg_id, from_user, e)

        sticker_result = result.get("sticker")
        if sticker_result:
            try:
                sticker_path = sticker_result.get("path", "")
                if sticker_path:
                    from pathlib import Path as _Path
                    img_bytes = _Path(sticker_path).read_bytes()
                    if img_bytes:
                        self.send_image(img_bytes, to_user=from_user)
            except Exception as e:  # noqa: BLE001
                logger.warning("[wx][step=sticker_failed] msg_id=%s user=%s error=%s",
                               msg_id, from_user, e)

    def _image_mode(self) -> tuple:
        """读 config/system.yaml → multimodal.image；返回 (mode, vision_model, max_bytes)。

        未配置或读取失败时按 auto/无视觉模型处理（即自动降级为描述注入）。
        """
        cfg = getattr(self, "_image_cfg", None)
        if cfg is None:
            cfg = {"mode": "auto", "vision_model": "", "max_bytes": 5242880}
            try:
                import yaml
                cfg_path = Path(__file__).parent.parent / "config" / "system.yaml"
                if cfg_path.exists():
                    with open(cfg_path, encoding="utf-8") as f:
                        full = yaml.safe_load(f) or {}
                    img = ((full.get("multimodal") or {}).get("image")) or {}
                    cfg = {
                        "mode": img.get("mode", cfg["mode"]),
                        "vision_model": img.get("vision_model", cfg["vision_model"]),
                        "max_bytes": img.get("max_bytes", cfg["max_bytes"]),
                    }
            except Exception:
                pass
            self._image_cfg = cfg
        return str(cfg.get("mode", "auto")), (cfg.get("vision_model") or ""), int(cfg.get("max_bytes", 5242880))

    def _describe_image(self, image_data: str) -> str:
        """降级路径：用既有 VisionHandler 生成描述文本（图片不落盘）。"""
        try:
            from multimodal.multimodal_processor import VisionHandler

            if getattr(self, "_vision_handler", None) is None:
                llm = None
                if self.orchestrator:
                    llm = getattr(self.orchestrator, "components", {}).get("llm")
                self._vision_handler = VisionHandler(llm)
            result = _run_async_coro(self._vision_handler.process(image_data))
            desc = (result or {}).get("text", "") or ""
            if desc.startswith("[收到一张图片"):
                return ""
            return desc
        except Exception as e:
            logger.warning("图片描述失败: %s", e)
            return ""

    def _transcribe_voice(self, voice_data_b64: str) -> str:
        """微信语音 → 文字（ASRHandler 配置驱动；未启用返回空串走原占位提示）。"""
        try:

            from multimodal.multimodal_processor import ASRHandler

            if getattr(self, "_asr_handler", None) is None:
                cfg: dict[str, object] = {}
                try:
                    import yaml
                    cfg_path = Path(__file__).parent.parent / "config" / "system.yaml"
                    if cfg_path.exists():
                        with open(cfg_path, encoding="utf-8") as f:
                            full = yaml.safe_load(f) or {}
                        import os
                        vc = full.get("voice") or {}
                        ac = vc.get("asr") or {}
                        for k, v in ac.items():
                            if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
                                ac[k] = os.environ.get(v[2:-1], "")
                        cfg = ac
                except Exception:
                    cfg = {}
                self._asr_handler = ASRHandler(cfg)
            handler: ASRHandler = self._asr_handler
            result = _run_async_coro(handler.process(voice_data_b64, source_format="silk"))
            text = (result or {}).get("text", "")
            if text.startswith("[语音消息"):
                return ""
            return text
        except Exception as e:
            logger.warning("ASR 转录失败: %s", e)
            return ""

    # ── 状态 ──

    def get_status(self):
        today = time.strftime("%Y-%m-%d")
        if today != self._last_day:
            self._messages_today = 0
            self._last_day = today
        return {
            "connected": bool(self.token),
            "uptime_seconds": time.time() - self.started_at if self.started_at else 0,
            "bot_id": self.bot_id,
            "last_activity": self._last_activity or None,
            "messages_today": self._messages_today,
            "reconnect_attempts": self._reconnect_attempts,
        }

    def stop(self):
        """停止微信连接器并清理资源。

        只关闭本实例的消息处理线程池；LLM 编排统一走进程常驻共享循环
        （utils.async_utils.get_shared_loop），无全局池可关（P1-审查 item27）。
        """
        self._stop = True
        # 关闭本实例的消息处理线程池
        with suppress(Exception):
            self._msg_executor.shutdown(wait=False)
        # 持久化断开状态（per-user 或遗留全局）
        self._merge_session_state({"connected": False, "status": "disconnected"})
        logger.info("微信连接器已停止 owner=%s slot=%s", self.owner_user_id, self.slot)
