"""
直接微信连接器

直连微信 API，扫码登录 → 收消息 → 传给小十 → 发回复。
"""

import asyncio
import atexit
import base64
import concurrent.futures
import json
import logging
import os
import threading
import time
import uuid
from collections import OrderedDict, deque
from contextlib import suppress
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger("wechat_direct")

# ── 共享线程池（供 _call_user_manager 复用，避免反复创建/销毁） ──
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="wx_async")

# ── 进程退出时自动关闭线程池，防止资源泄漏 ──
def _shutdown_executor():
    try:
        _executor.shutdown(wait=False)
        logger.info("全局线程池已关闭 (atexit)")
    except Exception:  # noqa: BLE001
        pass

atexit.register(_shutdown_executor)

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

# ── 连接状态持久化（解决前端状态时连时断问题） ──
_STATE_FILE = Path(__file__).parent.parent / "data" / "wechat_state.json"


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
    """持久化连接状态。"""
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        logger.debug("保存微信状态文件失败: %s", e)


def _merge_state(updates: dict) -> dict:
    """合并并保存状态更新。"""
    state = _load_state()
    state.update(updates)
    _save_state(state)
    return state


# ── 全局单例（供 REST API 读取状态） ──
_connector: "WeChatConnector | None" = None


def get_connector():
    return _connector


def get_wechat_state() -> dict:
    """供外部 REST API 调用的稳定状态读取（优先内存实例，回退持久化文件）。"""
    conn = _connector
    if conn:
        if conn.token:
            return conn.get_status()
        # 内存实例存在但无 token，说明已断开或尚未登录成功
        return {**_load_state(), "connected": False}
    return _load_state()


def _clear_credentials(path=None):
    path = path or CREDENTIALS_PATH
    if os.path.exists(path):
        os.remove(path)
        logger.info("已清除保存的微信凭证")


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
# 追问用的上下文轮数（真实往来条数，含双方）
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
        cfg["delay2_seconds"] = max(5, min(7200, int(cfg["delay2_seconds"])))
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
    process_message 是 async 的，但轮询循环是同步的，
    用全局共享线程池跑 asyncio.run（避免每次创建/销毁线程池的开销）。
    attachments: 多模态附件（图片 content part 列表），可为 None。
    """
    coro = mgr.process_message(user_id, text, attachments=attachments)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # 当前无线程事件循环，直接运行
        return asyncio.run(coro)

    # 已有事件循环（例如在异步 FastAPI handler 中），用线程池执行
    try:
        future = _executor.submit(_run_async_coro, coro)
        return future.result(timeout=120)
    except concurrent.futures.TimeoutError:
        logger.warning("处理消息超时 (user=%s)", user_id)
        return {"reply": "", "error": "timeout"}
    except RuntimeError as e:
        if "shutdown" in str(e).lower():
            logger.warning("全局线程池已关闭，降级为同步运行: %s", e)
            return asyncio.run(coro)
        raise


# ═══════════════════════════════════════════════
# 主连接器
# ═══════════════════════════════════════════════

class WeChatConnector:
    """
    微信连接器 — 直连微信 API（多用户版）

    用法:
        connector = WeChatConnector(user_manager)
        connector.run()  # 登录 + 消息轮询
    """

    def __init__(self, user_manager, base_url=DEFAULT_BASE_URL):
        self.user_manager = user_manager
        self.orchestrator = getattr(user_manager, "_orch", None)
        self.base_url = base_url
        self.token = ""
        self.bot_id = ""
        self.started_at = 0
        self._stop = False
        self._get_updates_buf = ""
        self._received_msgs: OrderedDict = OrderedDict()  # 有序字典，支持按插入顺序淘汰
        self._context_tokens: dict = {}  # {user_id: {"token": str, "ts": float}}
        self._load_context_tokens()

        # ── 对话内追问状态（见文件头「对话内追问」）──
        self._pending_followups: dict[str, dict[str, Any]] = {}  # {user_id: {step,due,last_reply}}
        self._followup_lock = threading.Lock()
        self._followup_daily: dict[str, int] = {}   # {user_id: 当日已追问条数}
        self._followup_daily_date = ""
        # 最近若干轮真实往来（供追问用真实上下文，而不是硬插一句"人呢"）
        self._recent_exchanges: dict[str, deque] = {}
        self._last_user_id: str = ""
        # 每条消息在独立线程中处理，避免阻塞轮询循环
        self._msg_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="wx_msg"
        )
        # 统计
        self._messages_today = 0
        self._last_day = time.strftime("%Y-%m-%d")
        self._last_activity = 0
        self._reconnect_attempts = 0

    def send_text(self, text: str, to_user: str = "") -> bool:
        """主动发送文本消息（供外部调用）

        ⚠️ 2026-09-19：改为**校验业务返回码**。旧实现丢弃 `_send_text()` 的返回
        字典，只要不抛异常就 `return True` 并打印「微信主动发送成功」——
        而 `ret=-2 "prepare failed"`（会话窗口失效）走的正是这条路径，
        导致上层误判送达成功（用户实际零接收，且配额被记账）。
        """
        target = to_user or self._last_user_id
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
        target = to_user or self._last_user_id
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
        target = to_user or self._last_user_id
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
        target = to_user or self._last_user_id
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
        """登微信 — 先放二维码，再试保存的凭证"""
        # 0. 快速重连：如果当前已有 token（从 _poll_loop 调用的重连），先试一次
        if self.token:
            logger.info("尝试用现有 token 快速重连...")
            try:
                test = _get_updates("", self.token, self.base_url, timeout=5)
                if test.get("ret") != -14 and test.get("errcode") != -14:
                    logger.info("现有 token 仍有效，重连成功")
                    _save_qr_to_file("", "connected")
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
            _save_qr_to_file(qrcode_url, "waiting")
            print(f"\n二维码已就绪: {qrcode_url}\n")
        else:
            _save_qr_to_file("", "idle")

        # 3. 再试保存的凭证
        creds = _load_credentials()
        if creds.get("token"):
            logger.info("有保存的凭证，尝试快速登录...")
            self.token = creds["token"]
            self.base_url = creds.get("base_url", self.base_url)
            self.bot_id = creds.get("bot_id", "")
            try:
                test = _get_updates("", self.token, self.base_url, timeout=3)
                if test.get("ret") != -14 and test.get("errcode") != -14:
                    logger.info("保存的凭证有效，跳过扫码")
                    _save_qr_to_file(qrcode_url or "", "connected")
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
                    _save_qr_to_file(qrcode_url, "waiting")
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

                # 存凭证，下次直接连
                _save_credentials({
                    "token": bot_token,
                    "base_url": result_base_url,
                    "bot_id": bot_id,
                    "user_id": status_resp.get("ilink_user_id", ""),
                })

                _save_qr_to_file(qrcode_url, "connected")
                print(f"微信登录成功！bot_id={bot_id}")

                self.token = bot_token
                self.base_url = result_base_url
                self.bot_id = bot_id
                self._reconnect_attempts = 0
                _merge_state({
                    "connected": True,
                    "bot_id": bot_id,
                    "last_activity": time.time(),
                })
                return True

            time.sleep(QR_POLL_INTERVAL)

        print("二维码登录超时")
        _save_qr_to_file("", "expired")
        _merge_state({"connected": False})
        return False

    # ── 主循环 ──

    def run(self):
        """完整流程：登录 → 收消息 → 传给小十 → 发回复"""
        global _connector
        _connector = self

        if not self.login():
            logger.error("微信登录失败")
            self._reconnect_attempts += 1
            _merge_state({
                "connected": False,
                "reconnect_attempts": self._reconnect_attempts,
            })
            return

        self.started_at = time.time()
        _merge_state({
            "connected": True,
            "started_at": self.started_at,
            "bot_id": self.bot_id,
            "reconnect_attempts": 0,
        })
        logger.info("微信登录成功，开始收消息...")
        # 对话内追问守护线程（只启一次；run() 可能因重连被多次调用）
        if not getattr(self, "_followup_thread_started", False):
            self._followup_thread_started = True
            threading.Thread(
                target=self._followup_thread, name="wx-followup", daemon=True,
            ).start()
        self._poll_loop()
        # 轮询退出时持久化断开状态（保留 started_at 方便排查）
        _merge_state({"connected": False})

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
                        _save_qr_to_file("", "idle")
                        if os.path.exists(CREDENTIALS_PATH):
                            os.remove(CREDENTIALS_PATH)
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
                    _merge_state({"last_activity": self._last_activity})
                for raw_msg in msgs:
                    # 修复 P0-WX2：消息处理放到独立线程，避免阻塞轮询循环
                    # 导致连接状态抖动或心跳超时。
                    self._msg_executor.submit(self._handle_message, raw_msg)

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
        try:
            if not os.path.exists(CONTEXT_TOKENS_PATH):
                return
            with open(CONTEXT_TOKENS_PATH, encoding="utf-8") as f:
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
        """原子落盘 context_token，避免服务重启丢失会话窗口。"""
        try:
            Path(CONTEXT_TOKENS_PATH).parent.mkdir(parents=True, exist_ok=True)
            tmp = CONTEXT_TOKENS_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._context_tokens, f, ensure_ascii=False, indent=2)
            os.replace(tmp, CONTEXT_TOKENS_PATH)
        except Exception as e:  # noqa: BLE001
            logger.warning("context_token 保存失败（忽略）: %s", e)

    def _cleanup_context_tokens(self):
        """清理过期的 context_token 条目，防止内存泄漏"""
        now = time.time()
        expired = [
            uid for uid, entry in self._context_tokens.items()
            if isinstance(entry, dict) and (now - entry.get("ts", 0)) > _CONTEXT_TOKENS_TTL
        ]
        for uid in expired:
            del self._context_tokens[uid]
        if expired:
            self._save_context_tokens()
            logger.debug("Cleaned up %d expired context_tokens entries", len(expired))

    # ── 对话内追问（见文件头说明）─────────────────────────────

    def _remember_exchange(self, user_id: str, user_text: str, bot_reply: str) -> None:
        """记住最近几轮真实往来，供追问使用真实上下文。"""
        if not user_id:
            return
        buf = self._recent_exchanges.get(user_id)
        if buf is None:
            buf = self._recent_exchanges[user_id] = deque(maxlen=_FOLLOWUP_CONTEXT_TURNS)
        if user_text:
            buf.append(("对方", str(user_text)[:120]))
        if bot_reply:
            buf.append(("我", str(bot_reply)[:160]))

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
        last_reply = str(st.get("last_reply", ""))[:80]

        # ⚠️ 2026-09-19 用户反馈：「追问没有和上下文形成逻辑，而是强行地插入一句
        # 「在吗？」「人呢？」」—— 旧实现只把**上一句 AI 回复**塞进 prompt，
        # 等于没有上下文，于是只能产出通用催促语。现改为带上真实往来记录。
        history = self._recent_exchanges.get(user_id)
        ctx = "\n".join(f"{who}：{line}" for who, line in history) if history else ""
        prompt = (
            "下面是你们刚才的真实聊天记录（按时间顺序）：\n"
            f"{ctx}\n\n"
            f"你最后说的是：「{last_reply}」\n"
            "对方之后就没再回你了。现在你要像真人一样自己再补一句 —— "
            "**必须接着上面的聊天内容**：可以问他/她刚提到的那件具体事，"
            "也可以就那件事说一句自己的感受或想法。\n"
            "严禁「在吗」「人呢」「怎么不理我」「你是不是睡着了」这类与内容无关的空话，"
            "严禁重复你刚说过的话。口语化，15 字以内，只输出这一句话。"
        )
        text = self._generate_followup(prompt, last_reply=last_reply)
        if not text:
            return
        if not self.send_text(text, to_user=user_id):
            logger.warning("[wx][step=followup_send_failed] user=%s step=%d", user_id, step)
            return

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

    def _generate_followup(self, prompt: str, last_reply: str = "") -> str:
        """用角色 LLM 生成一句追问；失败/不合规返回空串（宁可不发）。"""
        orch = self.orchestrator
        llm = (getattr(orch, "components", None) or {}).get("llm") if orch else None
        if llm is None or not hasattr(llm, "chat_sync"):
            return ""
        try:
            raw = llm.chat_sync(query=prompt, max_tokens=60, temperature=0.95)
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

    def _handle_message(self, raw_msg):
        """处理一条消息（全链路结构化日志：接收 → 路由 → LLM → 回复）"""
        msg_type = raw_msg.get("message_type", 0)
        if msg_type not in (1, 3, 34):  # 放行用户文本(1)/图片(3)/语音(34)，其余（系统通知、自发回显等）仍丢弃
            return

        msg_id = str(raw_msg.get("message_id", raw_msg.get("seq", "")))
        if msg_id in self._received_msgs:
            return
        self._received_msgs[msg_id] = True
        while len(self._received_msgs) > _RECEIVED_MSGS_MAX:
            self._received_msgs.popitem(last=False)
        self._cleanup_context_tokens()

        from_user = raw_msg.get("from_user_id", "")
        context_token = raw_msg.get("context_token", "")
        if context_token and from_user:
            self._context_tokens[from_user] = {"token": context_token, "ts": time.time()}
            # 落盘：服务重启后仍可在窗口期内主动发送（见 CONTEXT_TOKENS_PATH 注释）
            self._save_context_tokens()
        if from_user:
            self._last_user_id = from_user
            # 用户接话了 → 取消该用户的待发追问（追问只在"对方没接话"时才发）
            self._cancel_followup(from_user)

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
        if today != self._last_day:
            self._messages_today = 0
            self._last_day = today
        self._messages_today += 1
        _merge_state({"messages_today": self._messages_today})

        t_start = time.perf_counter()
        logger.info(
            "[wx][step=receive] msg_id=%s user=%s text=%r",
            msg_id, from_user, text[:80],
        )

        try:
            result = _call_user_manager(self.user_manager, from_user, text, attachments)
            t_elapsed = time.perf_counter() - t_start
        except Exception as e:  # noqa: BLE001
            logger.exception(
                "[wx][step=route_error] msg_id=%s user=%s error=%s",
                msg_id, from_user, e,
            )
            try:
                _send_text(
                    to=from_user, text="（消息处理异常，请稍后重试）",
                    context_token=self._get_context_token(from_user) or context_token,
                    token=self.token, base_url=self.base_url,
                )
            except Exception:  # noqa: BLE001
                logger.exception("[wx][step=notify_fail] msg_id=%s user=%s", msg_id, from_user)
            return

        if not isinstance(result, dict):
            logger.warning(
                "[wx][step=route_bad_result] msg_id=%s user=%s result_type=%s",
                msg_id, from_user, type(result),
            )
            result = {}

        reply = result.get("reply", "")
        error = result.get("error", "")
        process_time = result.get("process_time")

        if not reply:
            logger.warning(
                "[wx][step=empty_reply] msg_id=%s user=%s error=%s elapsed=%.2fs",
                msg_id, from_user, error or "unknown", t_elapsed,
            )
            reply = "（我暂时不知道该怎么回复，可以再说一次吗？）"
        else:
            logger.info(
                "[wx][step=llm_done] msg_id=%s user=%s reply=%r elapsed=%.2fs llm_time=%s",
                msg_id, from_user, reply[:80], t_elapsed, process_time,
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
                        "[wx][step=reply_send_failed] msg_id=%s user=%s part=%d/%d error=%s",
                        msg_id, from_user, idx + 1, len(segments), errmsg,
                    )
                    return
            logger.info(
                "[wx][step=reply_sent] msg_id=%s user=%s parts=%d reply=%r",
                msg_id, from_user, len(segments), reply[:80],
            )
            # 记录本轮真实往来（追问要用它做上下文，不能凭空"人呢"）
            self._remember_exchange(from_user, text, reply)
            # 回复成功 → 登记对话内追问（以最后一段作为"刚说的话"）
            self._schedule_followup(from_user, segments[-1] if segments else reply)
        except Exception as e:  # noqa: BLE001
            logger.exception(
                "[wx][step=reply_send_failed] msg_id=%s user=%s error=%s",
                msg_id, from_user, e,
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

        注意: 不再关闭全局共享线程池（_executor），否则重新连接后
        消息处理会报错 cannot schedule new futures after shutdown。
        只关闭本实例的消息处理线程池。
        """
        self._stop = True
        # 关闭本实例的消息处理线程池
        with suppress(Exception):
            self._msg_executor.shutdown(wait=False)
        # 持久化断开状态
        _merge_state({"connected": False})
        logger.info("微信连接器已停止")
