"""
直接微信连接器

不做 CowAgent 子进程、不做补丁、不做适配器。
扫码登录微信 → 收消息 → 传给小十 → 发回复，完事。
"""

import asyncio
import concurrent.futures
import json
import logging
import os
import time
import uuid
from pathlib import Path

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

# ── 全局单例（供 REST API 读取状态） ──
_connector: "WeChatConnector | None" = None


def get_connector():
    return _connector


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
            with open(path, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"读取凭证失败: {e}")
    return {}


def _save_credentials(data, path=None):
    path = path or CREDENTIALS_PATH
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f)
        logger.info(f"凭证已保存到 {path}")
    except Exception as e:
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
    except Exception as e:
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


# ═══════════════════════════════════════════════
# 异步编排器调用（process_message 是 async 的）
# ═══════════════════════════════════════════════

def _call_orchestrator(orchestrator, text, session_id):
    """
    调用小十处理消息。
    process_message 是 async 的，但我们的轮询循环是同步的，
    所以用线程池跑 asyncio.run。
    """
    coro = orchestrator.process_message(text, session_id=session_id)
    try:
        asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()
    except RuntimeError:
        return asyncio.run(coro)


# ═══════════════════════════════════════════════
# 主连接器
# ═══════════════════════════════════════════════

class WeChatConnector:
    """
    微信连接器 — 直接连微信，不用 CowAgent。

    用法:
        connector = WeChatConnector(orchestrator)
        connector.run()  # 登录 + 消息轮询
    """

    def __init__(self, orchestrator, base_url=DEFAULT_BASE_URL):
        self.orchestrator = orchestrator
        self.base_url = base_url
        self.token = ""
        self.bot_id = ""
        self.started_at = 0
        self._stop = False
        self._get_updates_buf = ""
        self._received_msgs = set()
        self._context_tokens = {}
        self._last_user_id: str = ""

    def send_text(self, text: str, to_user: str = "") -> bool:
        """主动发送文本消息（供外部调用）"""
        target = to_user or self._last_user_id
        if not target or not self.token:
            logger.warning("微信主动发送失败: 无目标用户或未登录")
            return False
        try:
            context_token = self._context_tokens.get(target, "")
            _send_text(
                to=target, text=text,
                context_token=context_token,
                token=self.token, base_url=self.base_url,
            )
            logger.info("微信主动发送成功: %s", text[:30])
            return True
        except Exception as e:
            logger.warning("微信主动发送失败: %s", e)
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
            except Exception as e:
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
        except Exception as e:
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
            except Exception as e:
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
            except Exception as e:
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
                except Exception as e:
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
                return True

            time.sleep(QR_POLL_INTERVAL)

        print("二维码登录超时")
        _save_qr_to_file("", "expired")
        return False

    # ── 主循环 ──

    def run(self):
        """完整流程：登录 → 收消息 → 传给小十 → 发回复"""
        global _connector
        _connector = self

        if not self.login():
            logger.error("微信登录失败")
            return

        self.started_at = time.time()
        logger.info("微信登录成功，开始收消息...")
        self._poll_loop()

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
                for raw_msg in msgs:
                    try:
                        self._handle_message(raw_msg)
                    except Exception as e:
                        logger.error(f"处理消息异常: {e}")

            except Exception as e:
                if self._stop:
                    break
                consecutive_failures += 1
                logger.error(f"轮询异常: {e}")
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    time.sleep(BACKOFF_DELAY)
                else:
                    time.sleep(RETRY_DELAY)

        logger.info("消息轮询结束")

    def _handle_message(self, raw_msg):
        """处理一条消息"""
        msg_type = raw_msg.get("message_type", 0)
        if msg_type != 1:  # 只看用户消息
            return

        msg_id = str(raw_msg.get("message_id", raw_msg.get("seq", "")))
        if msg_id in self._received_msgs:
            return
        self._received_msgs.add(msg_id)

        from_user = raw_msg.get("from_user_id", "")
        context_token = raw_msg.get("context_token", "")
        if context_token and from_user:
            self._context_tokens[from_user] = context_token
        if from_user:
            self._last_user_id = from_user

        items = raw_msg.get("item_list", [])
        text = ""
        for item in items:
            if item.get("type") == 1:
                text_item = item.get("text_item", {})
                text = text_item.get("text", "")
                break

        if not text:
            return

        logger.info(f"微信消息: from={from_user} text={text[:50]}")

        try:
            result = _call_orchestrator(self.orchestrator, text, session_id=from_user)
            reply = result.get("reply", "")
            if reply:
                token = self._context_tokens.get(from_user, context_token)
                _send_text(
                    to=from_user, text=reply,
                    context_token=token,
                    token=self.token, base_url=self.base_url,
                )
                logger.info(f"小十回复已发送: {reply[:50]}")
        except Exception as e:
            logger.error(f"处理消息/发回复失败: {e}")

    # ── 状态 ──

    def get_status(self):
        return {
            "connected": bool(self.token),
            "uptime_seconds": time.time() - self.started_at if self.started_at else 0,
            "bot_id": self.bot_id,
        }

    def stop(self):
        self._stop = True
        logger.info("微信连接器已停止")
