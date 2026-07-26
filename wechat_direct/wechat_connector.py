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
import time
import uuid
from collections import OrderedDict
from contextlib import suppress
from pathlib import Path

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
        return {"ret": 0, "msgs": []}
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


def _call_user_manager(mgr, user_id, text):
    """
    调用女友管理器处理消息（多用户路由）。
    process_message 是 async 的，但轮询循环是同步的，
    用全局共享线程池跑 asyncio.run（避免每次创建/销毁线程池的开销）。
    """
    coro = mgr.process_message(user_id, text)
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
        """主动发送文本消息（供外部调用）"""
        target = to_user or self._last_user_id
        if not target or not self.token:
            logger.warning("微信主动发送失败: 无目标用户或未登录")
            return False
        try:
            context_token = self._get_context_token(target)
            _send_text(
                to=target, text=text,
                context_token=context_token,
                token=self.token, base_url=self.base_url,
            )
            logger.info("微信主动发送成功: %s", text[:30])
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("微信主动发送失败: %s", e)
            return False

    def send_voice(self, audio_bytes: bytes, to_user: str = "",
                   duration_ms: int = 0, fmt: str = "silk") -> bool:
        """发送语音消息"""
        target = to_user or self._last_user_id
        if not target or not self.token or not audio_bytes:
            logger.warning("发送语音失败: 无目标用户或未登录或无音频数据")
            return False
        try:
            audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
            context_token = self._get_context_token(target)
            _send_voice_message(
                to=target, audio_data_b64=audio_b64,
                duration_ms=duration_ms, context_token=context_token,
                token=self.token, base_url=self.base_url, fmt=fmt,
            )
            logger.info("语音发送成功: %d bytes, fmt=%s", len(audio_bytes), fmt)
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("语音发送失败: %s", e)
            return False

    def send_image(self, image_bytes: bytes, to_user: str = "",
                   image_type: str = "png") -> bool:
        """发送图片消息"""
        target = to_user or self._last_user_id
        if not target or not self.token or not image_bytes:
            logger.warning("发送图片失败: 无目标用户或未登录或无图片数据")
            return False
        try:
            image_b64 = base64.b64encode(image_bytes).decode("utf-8")
            context_token = self._get_context_token(target)
            _send_image_message(
                to=target, image_data_b64=image_b64,
                context_token=context_token,
                token=self.token, base_url=self.base_url,
                image_type=image_type,
            )
            logger.info("图片发送成功: %d bytes", len(image_bytes))
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("图片发送失败: %s", e)
            return False

    def send_emoji(self, emoji_md5: str, to_user: str = "") -> bool:
        """发送表情消息"""
        target = to_user or self._last_user_id
        if not target or not self.token or not emoji_md5:
            logger.warning("发表情失败: 无目标用户或未登录或无表情数据")
            return False
        try:
            context_token = self._get_context_token(target)
            _send_emoji_message(
                to=target, emoji_md5=emoji_md5,
                context_token=context_token,
                token=self.token, base_url=self.base_url,
            )
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
        """获取用户的 context_token，并清理过期条目"""
        entry = self._context_tokens.get(user_id)
        if entry is None:
            return ""
        if isinstance(entry, dict):
            return entry.get("token", "")  # type: ignore[no-any-return]
        # 兼容旧格式（直接存储的字符串）
        return str(entry)

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
            logger.debug("Cleaned up %d expired context_tokens entries", len(expired))

    def _handle_message(self, raw_msg):
        """处理一条消息"""
        msg_type = raw_msg.get("message_type", 0)
        if msg_type != 1:  # 只看用户消息
            return

        msg_id = str(raw_msg.get("message_id", raw_msg.get("seq", "")))
        if msg_id in self._received_msgs:
            return
        self._received_msgs[msg_id] = True
        # 超过最大条目时清理最早的记录，防止内存无限增长
        while len(self._received_msgs) > _RECEIVED_MSGS_MAX:
            self._received_msgs.popitem(last=False)
        # 定期清理过期的 context_tokens
        self._cleanup_context_tokens()

        from_user = raw_msg.get("from_user_id", "")
        context_token = raw_msg.get("context_token", "")
        if context_token and from_user:
            self._context_tokens[from_user] = {"token": context_token, "ts": time.time()}
        if from_user:
            self._last_user_id = from_user

        items = raw_msg.get("item_list", [])
        text = ""
        voice_data = ""
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
                image_item.get("image_data", "")

        if not text and not voice_data:
            return

        # 跨天清零今日消息计数
        today = time.strftime("%Y-%m-%d")
        if today != self._last_day:
            self._messages_today = 0
            self._last_day = today
        self._messages_today += 1
        _merge_state({"messages_today": self._messages_today})

        logger.info(f"微信消息: from={from_user} text={text[:50]}")

        try:
            result = _call_user_manager(self.user_manager, from_user, text)
            if not isinstance(result, dict):
                logger.warning("UserManager 返回非字典结果: %s", type(result))
                result = {}
            reply = result.get("reply", "")
            error = result.get("error", "")
            if not reply:
                if error:
                    logger.warning("处理消息返回错误 (user=%s): %s", from_user, error)
                else:
                    logger.warning("LLM 返回空回复 (user=%s)，发送兜底提示", from_user)
                reply = "（我暂时不知道该怎么回复，可以再说一次吗？）"

            token = self._get_context_token(from_user) or context_token
            _send_text(
                to=from_user, text=reply,
                context_token=token,
                token=self.token, base_url=self.base_url,
            )
            logger.info(f"回复已发送给 {from_user}: {reply[:50]}")

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
                    logger.warning("语音发送降级失败: %s", e)

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
                    logger.warning("表情包发送失败: %s", e)
        except Exception as e:  # noqa: BLE001
            logger.exception(f"处理消息/发回复失败: {e}")

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
