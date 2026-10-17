"""
直接微信连接器

直连微信 API，扫码登录 → 收消息 → 传给小十 → 发回复。
"""

import asyncio
import base64
import binascii
import concurrent.futures
import json
import logging
import os
import random
import re
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

from utils.humanize_config import DELAY_MODES
from utils.humanize_config import MAX_SEGMENTS as _SPLIT_MAX_SEGMENTS
from utils.local_time import now_local

logger = logging.getLogger("wechat_direct")

# ── 微信 API 地址 ──
DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"

# ── iLink CDN 媒体（2026-10-12 多模态协议适配批）──
# photon-hq/wechat-ilink-client 逆向默认基址；服务端 media 里下发 full_url 时优先用之
DEFAULT_CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"
MEDIA_DOWNLOAD_TIMEOUT_S = 15.0   # 媒体下载超时（规格钉死 15s，失败降级不炸主流程）

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
# ABUSE-4（2026-10-09）：per-owner 入站日配额——开放注册下自建通道高频发消息
# 每条都走完整 orchestrator 主链（多段 LLM）。量级取「真人远达不到、脚本必触发」：
# HTTP/WS 侧同批收口为 500 msg/天（chat_routes._CHAT_MSGS_PER_DAY）。
_INBOUND_DAILY_LIMIT = 500

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


def _plane():
    """控制面 proactive.runtime_plane —— fail-soft：不可用时返回 None。

    连接器主循环不得因控制面故障而死；None 时所有接线点降级为旧的进程内
    行为（内存去重 / 无 desired 闸 / 内存追问账）。
    """
    try:
        from proactive import runtime_plane

        return runtime_plane
    except Exception as e:  # noqa: BLE001
        logger.debug("控制面不可用（降级进程内行为）: %s", e)
        return None


# ═══════════════════════════════════════════════
# iLink 媒体 item 解析（2026-10-12 多模态协议适配批）
#
# 生产 iLink 协议 item 枚举：1=文本 text_item.text；2=图片 image_item{media, aeskey,...}；
# 3=语音 voice_item{media, encode_type, text,...}（text=微信服务端 ASR，'if available'）。
# 依据：zongrongjin/weixin-ilink types.py:12-42 + photon-hq/wechat-ilink-client
# types.ts:64-142 双源一致。旧微信协议（34 语音 / 3 图片，base64 内联）兼容保留。
# ═══════════════════════════════════════════════


@dataclass
class MediaRef:
    """iLink 媒体引用（CDNMedia 归一化，只留下载所需三要素）。"""

    encrypt_query_param: str = ""
    full_url: str = ""            # 服务端下发完整 URL 时优先于 CDN 基址拼串
    aes_key: bytes | None = None  # 归一化 16 字节 AES key；None=无 key 走明文下载兜底


@dataclass
class ParsedMedia:
    """item_list 解析结果（纯数据，脱离 self 可直测）。"""

    text: str = ""                  # type=1 文本
    voice_text: str = ""            # type=3 voice_item.text（微信服务端 ASR，可缺省）
    image_payload: MediaRef | None = None  # type=2 图片 CDN 引用
    voice_media: MediaRef | None = None    # type=3 语音 CDN 引用
    legacy_voice_data: str = ""     # type=34 旧协议内联 base64（兼容保留，防其他部署形态）
    legacy_image_data: str = ""     # 旧协议 type=3 image_data 内联 base64（同上）
    unknown_types: list = field(default_factory=list)  # 未识别 item type（可观测）


def _parse_aes_key(raw) -> bytes | None:
    """归一化 iLink 媒体 AES key，兼容三种形态（weixin-ilink cdn.py parse_aes_key 同构）：

    - 32 字符裸 hex（image_item.aeskey：16 字节 raw key 的 hex 串，入站图片优先）；
    - base64 解出 16 raw bytes（入站图片 media.aes_key）；
    - base64 解出 32 字符 hex ASCII（语音/文件/视频：base64(hex_string) 形态）。
    解析失败返回 None → 调用方按明文引用兜底。
    """
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    if re.fullmatch(r"[0-9a-fA-F]{32}", s):
        return bytes.fromhex(s)
    try:
        decoded = base64.b64decode(s, validate=True)
    except (binascii.Error, ValueError):
        return None
    if len(decoded) == 16:
        return decoded
    if len(decoded) == 32:
        try:
            return bytes.fromhex(decoded.decode("ascii"))
        except (ValueError, UnicodeDecodeError):
            return None
    return None


def _aes_ecb_decrypt(data: bytes, key: bytes) -> bytes:
    """AES-128-ECB + PKCS7 unpad（iLink CDN 媒体解密，weixin-ilink cdn.py 同构）。"""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    if len(key) != 16:
        raise ValueError(f"AES key 必须 16 字节，得到 {len(key)}")
    if not data or len(data) % 16 != 0:
        raise ValueError("密文长度必须为 16 的倍数")
    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    padded = decryptor.update(data) + decryptor.finalize()
    pad = padded[-1]
    if not 1 <= pad <= 16 or padded[-pad:] != bytes([pad]) * pad:
        raise ValueError("PKCS7 填充非法（key 错误或密文损坏）")
    return padded[:-pad]


def parse_ilink_items(items) -> ParsedMedia:
    """解析 getupdates 消息的 item_list → ParsedMedia（纯函数：无网络、无 self）。

    旧协议同 type 双语义分叉：type=3 下有 voice_item 走新协议语音，
    有 image_item.image_data 走旧协议内联图片（防其他部署形态，不删）。
    """
    parsed = ParsedMedia()
    if not isinstance(items, (list, tuple)):
        return parsed
    for item in items:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type", 0)
        if item_type == 1:
            text_item = item.get("text_item") or {}
            parsed.text = str(text_item.get("text", "") or "")
        elif item_type == 2:
            image_item = item.get("image_item") or {}
            media = image_item.get("media") or {}
            # 入站图片 key 优先 item 级 aeskey（raw hex），回落 media.aes_key（base64）
            parsed.image_payload = MediaRef(
                encrypt_query_param=str(media.get("encrypt_query_param", "") or ""),
                full_url=str(media.get("full_url", "") or ""),
                aes_key=_parse_aes_key(image_item.get("aeskey")) or _parse_aes_key(media.get("aes_key")),
            )
        elif item_type == 3:
            voice_item = item.get("voice_item")
            if isinstance(voice_item, dict) and voice_item:
                if "voice_data" in voice_item:
                    parsed.legacy_voice_data = str(voice_item.get("voice_data", "") or "")
                else:
                    parsed.voice_text = str(voice_item.get("text", "") or "")
                    media = voice_item.get("media") or {}
                    # 语音无 item 级 aeskey，只有 media.aes_key（base64(hex32) 形态）
                    parsed.voice_media = MediaRef(
                        encrypt_query_param=str(media.get("encrypt_query_param", "") or ""),
                        full_url=str(media.get("full_url", "") or ""),
                        aes_key=_parse_aes_key(media.get("aes_key")),
                    )
            else:
                image_item = item.get("image_item")
                if isinstance(image_item, dict) and image_item.get("image_data"):
                    parsed.legacy_image_data = str(image_item.get("image_data", "") or "")
                else:
                    parsed.unknown_types.append(item_type)
        elif item_type == 34:
            voice_item = item.get("voice_item") or {}
            parsed.legacy_voice_data = str(voice_item.get("voice_data", "") or "")
        else:
            parsed.unknown_types.append(item_type)
    return parsed


def _read_voice_asr_config() -> dict:
    """读取 config/system.yaml → voice.asr（${ENV} 占位展开为环境变量值）。

    读取失败返回空 dict（= ASR 未启用语义）。_transcribe_voice 与
    _voice_asr_available 共用此真源，避免配置读法分叉。
    """
    try:
        import yaml

        cfg_path = Path(__file__).parent.parent / "config" / "system.yaml"
        if not cfg_path.exists():
            return {}
        with open(cfg_path, encoding="utf-8") as f:
            full = yaml.safe_load(f) or {}
        ac = ((full.get("voice") or {}).get("asr")) or {}
        for k, v in list(ac.items()):
            if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
                ac[k] = os.environ.get(v[2:-1], "")
        return dict(ac)
    except Exception:  # noqa: BLE001
        return {}


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

# 窗口临期阈值（小时）：最新 token 年龄超过此值但仍在 TTL 内 → "dying"。
# 给主动决策链一个「窗口将死」的提前量（生产实证：窗口死后决策链每 2h 白烧
# 一次 LLM，连续 11 次 prepare failed 才被发现——静默死是最大敌人）。
_TOKEN_DYING_AGE_HOURS = 20.0


def token_window_state(owner_user_id: int | str, slot: int = 0) -> dict:
    """探测某账号微信通道的「会话窗口」活性（纯读磁盘，不触内存实例）。

    微信平台约束（L2 既有脆弱性）：主动发送必须带未过期的 context_token，
    而 token 只在**用户给 bot 发消息**时刷新，TTL = ``_CONTEXT_TOKENS_TTL``
    （24h）。窗口一死，一切主动发送都被平台以 ``prepare failed`` 拒收
    （2026-10-17 生产实证：主通道 token 死后连续 11 次失败、白烧 11 次
    决策 LLM）。本函数是给决策链的**烧前确定性探针**——窗口已死的会话
    不该再烧决策调用。

    返回 ``{"state": ..., "age_hours": float|None, "ttl_hours": 24.0}``：
    - ``"fresh"``：窗口活（年龄 < 临期阈值）；
    - ``"dying"``：窗口将死（临期阈值 ≤ 年龄 < TTL）——仍可发，但值得告警；
    - ``"dead"``：窗口已死（年龄 ≥ TTL）；
    - ``"empty"``：凭证文件存在但无任何（有效）条目——用户从未在窗口期
      内说过话，发送同样必败，按死处理；
    - ``"missing"``：凭证文件不存在（通道从未产生 token）——按死处理；
    - ``"error"``：读盘/解析失败——**调用方应按「未知」放行**（探针故障
      不能阻塞决策链，宁宽勿误）。
    """
    result: dict[str, Any] = {
        "state": "error",
        "age_hours": None,
        "ttl_hours": _CONTEXT_TOKENS_TTL / 3600.0,
    }
    try:
        from wechat_direct import channel_paths

        path = channel_paths.context_tokens_path(int(owner_user_id), slot)
        if not path.exists():
            result["state"] = "missing"
            return result
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not data:
            result["state"] = "empty"
            return result
        best_ts = 0.0
        for entry in data.values():
            if isinstance(entry, dict):
                try:
                    best_ts = max(best_ts, float(entry.get("ts", 0) or 0))
                except (TypeError, ValueError):
                    continue
            else:
                # 旧格式（裸字符串 token，无时间戳）——无法判断活性，跳过
                continue
        if best_ts <= 0:
            result["state"] = "empty"
            return result
        age_h = (time.time() - best_ts) / 3600.0
        result["age_hours"] = round(age_h, 2)
        if age_h >= _CONTEXT_TOKENS_TTL / 3600.0:
            result["state"] = "dead"
        elif age_h >= _TOKEN_DYING_AGE_HOURS:
            result["state"] = "dying"
        else:
            result["state"] = "fresh"
        return result
    except Exception:  # noqa: BLE001
        return result

# ═══════════════════════════════════════════════════════════════
#  「一句一句发」拆条（上限 2 条，2026-10-12 用户裁决收敛）
#
#  2026-09-19 用户反馈：「一个正常人，怎么会一次性发那么回一大段？」
#  —— 旧实现把 LLM 整段当一条消息发出，体感像公告。当时补了拆条。
#
#  2026-10-12 用户裁决：「下一轮对话何时进行由 LLM 决策，不是数秒之内的
#  多次回复，要像真人一样隔一段时间再回，类似主动关怀的主动消息。」
#  —— 旧上限 4 段会让一条长回复在数秒内连发 4~6 条（拆条轰炸），同样违背
#  该裁决。上限收敛为 **2 条**；接话/再开口的时机唯一 Owner = proactive
#  决策链（proactive/llm_proactive.decide_proactive + scheduler 节奏双闸）。
# ═══════════════════════════════════════════════════════════════

# 拆条上限：最多 2 条（超出部分并入第 2 条，行间换行保留，内容无损）。
# 🔴 单一真源 = `utils.humanize_config.MAX_SEGMENTS`（用户裁决锁 2），见文件头
# import 处的 `_SPLIT_MAX_SEGMENTS` 别名 —— 本处不另立字面量。
# 单条长度上限（超长行在行内按句末标点再切）
_SPLIT_MAX_CHARS = 45
# 控制面 outbox 消费 tick（秒）。原挂在追问守护线程的 tick 上；追问链退役后
# outbox 消费改为独立守护线程 —— 不能挂长轮询主循环（LONG_POLL_TIMEOUT=35
# 会让回执等待全部超时）。
_OUTBOX_TICK = 5.0


def _split_overlong_line(line: str, max_chars: int) -> list[str]:
    """单行超长时按句末标点切句并贪心合并到 ≤ max_chars（只在行内合并）。"""
    if len(line) <= max_chars:
        return [line]
    parts: list[str] = []
    buf = ""
    for ch in line:
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
    merged: list[str] = []
    for s in parts:
        if merged and len(merged[-1]) + len(s) <= max_chars:
            merged[-1] += s
        else:
            merged.append(s)
    return [s for s in merged if s] or [line]


def split_reply_for_wechat(
    reply: str,
    max_segments: int = _SPLIT_MAX_SEGMENTS,
    max_chars: int = _SPLIT_MAX_CHARS,
) -> list[str]:
    """把一条回复拆成**最多 2 条**「微信里一句一句发」的消息。

    ⚠️ 2026-09-19 用户反馈：「一个正常人，怎么会一次性发那么回一大段？
    ……不应该一句一句的吗？」—— 旧实现把 LLM 返回的整段（含多行）**当一条消息**
    发出去，体感像在看公告而不是聊天。
    ⚠️ 2026-10-12 用户裁决（见文件头）：拆条上限 4 → **2** —— 超上限的剩余
    内容并入最后一条，**行间换行原样保留**（真人也发带换行的长消息），
    内容逐字无损。

    规则：
    1. 先按换行切块；单行短回复（≤ max_chars）原样一条发出，不折腾
    2. 超长块再按句末标点（。！？…）切句，贪心合并到 ≤ max_chars（仅行内）
    3. 块数 ≤ 上限时逐块各成一条；超上限时前 max_segments-1 块各自成条，
       其余按**原始分隔符**（行界=换行、行内=无）并入最后一条

    返回 [] 表示无可发送内容（调用方应回落原文本）。
    """
    text = (reply or "").strip()
    if not text:
        return []
    # 注意：**带换行就必须拆**（哪怕总长短）—— 否则会带着空行整段发出，
    # 正是用户抱怨的「一次发一大段」。短且单行才原样发。
    if "\n" not in text and len(text) <= max_chars:
        return [text]

    # 原子块：每行一块，超长行在行内再切。seps[i] 是块 i 在**原文里**的
    # 前置分隔符：首块 ""；行界 "\n"；同一超长行内切出的块之间 ""（原文本就
    # 无分隔）—— 并条时按它拼回，保证内容逐字无损。
    units: list[str] = []
    seps: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line:
            for j, piece in enumerate(_split_overlong_line(line, max_chars)):
                seps.append("\n" if (j == 0 and units) else "")
                units.append(piece)
    if not units:
        return [text]

    max_segments = max(1, int(max_segments))
    if len(units) <= max_segments:
        return units

    # 超上限：前 max_segments-1 块各自成条，其余按原始分隔符并入最后一条。
    # seps[k] 是最后一条独立消息与并条消息之间的边界 —— 它就是「分条」本身，
    # 消耗为消息间隔而不进正文（否则第 2 条会带前导换行）。
    # 拼回不变式："\n".join(segs) == 多行原文 / "".join(segs) == 单行原文。
    k = max_segments - 1
    tail = units[k] + "".join(
        sep + unit for sep, unit in zip(seps[k + 1:], units[k + 1:], strict=True)
    )
    segments = [*units[:k], tail]
    return [s for s in segments if s] or [text]


# ── 段间停顿档位（拟人度 humanize）────────────────────────────────
# 用户可调「打字快慢」档位；默认档位对**有正文的普通段**逐位复现旧式：
#   auto / dynamic —— 按上一段字数逐字估算（走配置 per_char_ms，默认 45ms/字）
#   fast / normal / slow —— 打字速度预设（25 / 45 / 90 ms/字）
#   fixed —— 固定延时（配置 fixed_ms）
#   random —— 区间随机（配置 random_min_ms..random_max_ms）
#   off —— 不延时（段间连发）
# 上下钳（delay_floor_ms / delay_ceil_ms，默认 400/1600ms）对以上各档统一生效。
# ⚠️ 零变更的关键：ms→s 的换算必须落在**参数层**（``45/1000.0 == 0.045``、
# ``400/1000.0 == 0.4``、``1600/1000.0 == 1.6`` 均为同一 double），否则
# ``len*45/1000.0`` 与 ``len*0.045`` 在部分长度上差 1 ULP（n=9→0.40499999999999997
# ≠0.405；n=20→0.8999999999999999≠0.9）。参数层换算下 auto 档逐位等于旧式。
#
# ⚠️ 已知**且有意**的一处非零变更（2026-10-16 复审 P2 澄清口径，非缺陷）：
# **纯指令段**（只含注入侧标记、无可读正文，如 ``[主动]``）段间由旧式
# ``max(0.4, min(1.6, len*0.045)) = 0.4s`` 收敛为 **0.0s**（见下方
# ``is_pure_instruction`` 短路）——「无正文可停」语义，且仅影响这类段，
# 普通段（``'在呢'`` 等）仍是 0.4s。该行为由
# ``tests/test_humanize_delay_tiers.py::test_pure_instruction_detected_and_zero_delay``
# 钉死；对外表述不得再说「默认档位下全链逐字零变更」。
_TIER_PRESET_PER_CHAR_MS = {"fast": 25, "normal": 45, "slow": 90}
_TIER_ALIASES = {"auto": "dynamic"}          # 用户档位名 auto ≡ 契约名 dynamic
_DYNAMIC_TIERS = frozenset({"dynamic", "fast", "normal", "slow"})
# 可落盘档位（dynamic/fixed/random/off）由 `utils.humanize_config.DELAY_MODES`
# **单一真源**派生 —— 读侧 `read_humanize` 只放行这些；API 的
# `DELAY_MODE_PATTERN` 同源。连接器额外认 `fast/normal/slow` 三个**仅 mode=
# 参数可达**的打字速度预设（不落盘）。2026-10-16 复审 P2「词表分裂」收口。
_KNOWN_TIERS = _DYNAMIC_TIERS | frozenset(DELAY_MODES)


def _resolve_delay_tier(mode: str | None) -> str:
    """把档位名归一为 dynamic/fast/normal/slow/fixed/random/off；未知回落 dynamic。"""
    m = str(mode or "").strip().lower()
    m = _TIER_ALIASES.get(m, m)
    return m if m in _KNOWN_TIERS else "dynamic"


def _is_bracket_segment(text: str) -> bool:
    """整段被括号包裹（爱语把括号旁白段单列、延时另给）。

    默认 ``bracket_delay_ms=0`` ⇒ 该分支惰性（不改变现行为）。
    """
    s = str(text or "").strip()
    return len(s) >= 2 and s[0] in "[（(" and s[-1] in "]）)"


def is_pure_instruction(text: str) -> bool:
    """纯指令/系统标记段判定：只含注入侧元数据（无可读正文）时为 True。

    复用既有**单一真源** ``utils.prompt_sanitize._strip_system_markers`` —— 其行首
    标记正则由 ``orchestrator.context_budget.OUTBOUND_CHANNEL_MARKERS`` 生成
    （``[主动]``/``[定时提醒]``），另含 ``[MM-DD HH:MM]`` 时间前缀与行尾
    ``（距上一条 X）``间隔注记。**不新开标记清单**：真源加通道标记时本判定自动跟随。
    剥完为空 ⇒ 该段无正文（如 ``[主动]``），段间无需「打字停顿」，零延时。

    空串/纯空白**不算**指令（避免误伤 ``_segment_delay`` 的 0.4s 下限断言与
    「无内容可停」的语义）；普通表情 ``[微笑]`` 不匹配标记正则，保持非零延时。
    """
    s = str(text or "")
    if not s.strip():
        return False
    try:
        from utils.prompt_sanitize import _strip_system_markers
    except Exception:  # noqa: BLE001
        return False
    try:
        return _strip_system_markers(s) == ""
    except Exception:  # noqa: BLE001
        return False


def _segment_delay(prev: str, *, mode: str | None = None) -> float:
    """两段之间的「打字停顿」（秒）。

    档位见上方 ``_TIER_PRESET_PER_CHAR_MS`` 说明；``mode=None`` 时取配置
    ``humanize.delay_mode``（默认 dynamic＝现行为）。纯指令段零延时。

    配置经 ``utils.humanize_config.read_humanize`` **热读**（跨 worker 即时生效，
    无进程内缓存）；**读取失败一律回落旧式公式** —— 段循环对异常是 ``break``
    （未受理前缀即停），配置不可读绝不能把整条回复截断，故此处必须 fail-soft。

    ⚠️ 位置签名恒为单参 ``prev``：既有契约测试以 ``lambda text: 0``
    monkeypatch 本函数（tests/test_wechat_reply_split.py:89,125、
    tests/test_followup_retired.py:100），``mode`` 只能作 keyword-only 追加。
    """
    if is_pure_instruction(prev):
        return 0.0
    try:
        from utils import humanize_config

        cfg = humanize_config.read_humanize()
    except Exception as exc:  # noqa: BLE001
        logger.debug("[wx][step=humanize_cfg_unreadable] %s", exc)
        # 旧式公式 = 拟人度引入前的现行为，作为配置不可读时的安全底线。
        return max(0.4, min(1.6, len(prev) * 0.045))

    tier = _resolve_delay_tier(cfg.delay_mode if mode is None else mode)
    if tier == "off":
        return 0.0
    if tier == "fixed":
        base = cfg.fixed_ms / 1000.0
    elif tier == "random":
        base = random.randint(cfg.random_min_ms, cfg.random_max_ms) / 1000.0
    else:
        per_char_ms = _TIER_PRESET_PER_CHAR_MS.get(tier, cfg.per_char_ms)
        base = len(prev) * (per_char_ms / 1000.0)
    if cfg.bracket_delay_ms > 0 and _is_bracket_segment(prev):
        base = cfg.bracket_delay_ms / 1000.0
    return max(cfg.delay_floor_ms / 1000.0, min(cfg.delay_ceil_ms / 1000.0, base))


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


def _call_user_manager(
    mgr, user_id, text, attachments=None, reply_sender=None, message_id: str = "",
):
    """
    调用女友管理器处理消息（多用户路由）。

    process_message 是 async 的，但轮询/消息线程是同步的。旧实现每条消息
    asyncio.run 开一个**新事件循环**，而 orchestrator/session_locks 按会话
    缓存 asyncio.Lock 跨线程复用 → 同用户连发第二条时锁挂在新循环上，
    唤醒永远丢失（P1-审查 item27）。现统一委托进程常驻共享循环。
    attachments: 多模态附件（图片 content part 列表），可为 None。
    message_id: 入站消息 id（缺陷 I）—— 桥到共享循环时 ContextVar **不**随之
        传播，只能作为实参送进去，由 `UserManager.process_message` 在回合起点绑定。
    """
    from utils.async_utils import run_on_shared_loop

    async def publish(result: dict) -> str:
        return await asyncio.to_thread(reply_sender, str(result.get("reply") or ""))

    kwargs = {"attachments": attachments}
    if reply_sender is not None:
        kwargs["reply_sender"] = publish
    if message_id:
        kwargs["message_id"] = str(message_id)
    coro = mgr.process_message(user_id, text, **kwargs)
    try:
        # 带发送回调时必须等待确认结束；桥接层超时后协程仍在运行，提前释放
        # 好友锁会让下一轮与上一轮的迟到发送交错。生成/网络超时由各自边界负责。
        return run_on_shared_loop(coro, timeout=None if reply_sender is not None else 120)
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
        # 🔴 D16 污染源根治（2026-10-13）：owner_user_id=None 的「遗留全局
        # 通道」没有任何合法产品语义——非独立用户、绕过 Registry 与
        # MAX_CHANNELS_PER_USER 配额、绕过扫码人守卫（09-19 报障「别人连的
        # 是管理员通道」的架构残根）。fail-closed：构造即拒绝。
        if owner_user_id is None:
            raise ValueError(
                "WeChatConnector 必须绑定 owner_user_id（D16：谁扫码谁聊，"
                "一人一 bot 一条通道）；遗留全局通道形态已废除"
            )
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

        self._peer_locks: dict[str, threading.RLock] = {}
        self._last_user_id: str = ""
        # D16（2026-10-12 裁决）：谁扫码谁聊——本通道扫码人的微信 id
        # （凭证里的 ilink_user_id / user_id 字段）。聊天侧用它识别本人，
        # 非扫码人消息按异常拒绝。旧「好友自选角色」机制随本裁决整体退役。
        self.scanner_wxid: str = ""
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
        # ABUSE-4：入站日配额记账（per-owner，本地日界滚动；进程内计数）
        self._inbound_daily_limit = _INBOUND_DAILY_LIMIT
        self._inbound_daily_date = ""
        self._inbound_daily_count = 0

    def _session_key(self, peer_wxid: str) -> str:
        """会话隔离键：owner 通道下 peer 好友。遗留全局通道保持 peer_wxid 原样。"""
        if self.owner_user_id is None:
            return peer_wxid
        return f"{int(self.owner_user_id)}:{peer_wxid}"

    @staticmethod
    def _peer_wxid_from_session(session_key: str) -> str:
        """从会话隔离键还原裸 peer wxid（`N:wxid` → `wxid`；裸形态原样返回）。

        发送 API 与 `_context_tokens` 的键都是**裸 wxid**，而会话登记用的是
        `N:wxid` 形态 —— 直接拿 session_key 当发送目标与查 token，两者都错
        （2026-09-20 实证：invalid arguments + token 恒空）。
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
        target = self._resolve_send_target(to_user, "文本")
        if not target:
            return False
        with self._peer_lock(self._session_key(target)):
            return self._send_text_unlocked(text, target)

    def _send_text_unlocked(self, text: str, to_user: str) -> bool:
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
                # P0 隐私：日志只留元数据（错误码/target/长度），不落消息原文
                logger.warning(
                    "微信主动发送失败: %s | target=%s context_token=%s text_len=%d",
                    errmsg, target,
                    "有" if context_token else "空（用户需先给机器人发一条消息）",
                    len(text),
                )
                return False
            # P0 隐私：原文（含截断前缀）不落日志，只记 target 与长度
            logger.info("微信主动发送成功: target=%s text_len=%d", target, len(text))
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
        # D16：扫码人身份预载——凭证里有 ilink_user_id 就先装上（token 快速
        # 重连路径不触碰凭证字段，但新建实例必须能识别本人）
        try:
            _pre = _load_credentials(self._credentials_path)
            self.scanner_wxid = str(_pre.get("user_id") or "")
        except Exception:  # noqa: BLE001
            pass
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
            self.scanner_wxid = str(creds.get("user_id") or "")
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
                self.scanner_wxid = str(status_resp.get("ilink_user_id") or "")
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
        # 控制面 outbox 消费守护线程（只启一次；run() 可能因重连被多次调用）。
        # 原挂在追问守护线程的 5 秒 tick 上；追问链已退役（2026-10-12，见
        # docs/DELETION_LOG.md），outbox 消费改为独立 tick —— 仍不能挂长轮询
        # 主循环（LONG_POLL_TIMEOUT=35 会让回执等待全部超时）。
        if not getattr(self, "_outbox_thread_started", False):
            self._outbox_thread_started = True
            threading.Thread(
                target=self._outbox_thread, name="wx-outbox", daemon=True,
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
            # 块2缺陷B：desired state 是跨进程全局真源 —— 用户从任一 worker 停用
            # （registry.disconnect 已写 desired=disabled）后，跑在另一进程里的
            # 宿主在下一次循环自停：停止入站消费、停追问、释放 presence 租约，
            # 同 owner 的另一 slot 不受影响。控制面读数失败不阻断（保守续跑）。
            rp = _plane()
            if rp is not None and self.owner_user_id is not None:
                try:
                    if rp.get_desired(self.owner_user_id, self.slot) == "disabled":
                        logger.info(
                            "通道已被全局停用，轮询自停 owner=%s slot=%s",
                            self.owner_user_id, self.slot,
                        )
                        self._stop = True
                        with suppress(Exception):
                            rp.presence_release(self.owner_user_id, self.slot)
                        # 回收本进程持有的实例与 flock —— 否则别的 worker 重连
                        # （start_login）永远抢不到通道锁（恒 429）。
                        with suppress(Exception):
                            from wechat_direct.connector_registry import get_registry

                            get_registry().disconnect(self.owner_user_id, self.slot)
                        break
                    rp.heartbeat(self.owner_user_id, self.slot)
                except Exception as e:  # noqa: BLE001
                    logger.debug("控制面 desired/心跳读写失败（本轮忽略）: %s", e)
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
                    # （追问链已退役：入站不再有需要提前取消的定时任务）
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

    # ── 会话辅助 ──────────────────────────────────────────────

    def _peer_lock(self, session_key: str):
        """同一好友的生成、拆条发送与确认串行；不同好友互不阻塞。"""
        with self._state_lock:
            return self._peer_locks.setdefault(session_key, threading.RLock())

    def _drain_outbox(self) -> None:
        """消费点名本 (owner, slot) 或未点名 slot 的待投微信命令。

        块1缺陷A/C：投递统一经控制面 outbox，CAS 认领保证一行恰由一个宿主
        发送（同 owner 双 slot 不再混投）；回执写回后调用方以受理为准。
        由独立守护线程按 5 秒 tick 消费（原挂在追问守护线程上；追问链已
        退役，见 docs/DELETION_LOG.md 2026-10-12）—— 不能挂长轮询主循环
        （LONG_POLL_TIMEOUT=35 会让回执等待全部超时）。

        2026-10-16 拆条链可达性：kind=``proactive`` 的行改经**两个投递点共用
        的唯一真源** ``utils.humanize_delivery.direct_send_proactive`` —— 生产
        为 uvicorn 4 worker，run_api 的定向分支只入队并等回执，**真实发送点
        就在本方法**；此前本方法直调 ``send_text``，`humanize.proactive_split`
        开启后定向主动投递仍整段直发（功能开了不生效）。``reminder`` 等其它
        kind 不属「主动消息拆条」语义，路径不动。
        默认（``proactive_split=False``/配置不可读）逐字回落 ``send_text``，
        本行行为不变。
        """
        rp = _plane()
        if rp is None or self.owner_user_id is None:
            return
        for _ in range(8):  # 每 tick 有界消费，防止积压饿死同线程其它工作
            try:
                row = rp.claim_next_send(
                    channel="wechat",
                    owner_id=self.owner_user_id,
                    slot=self.slot,
                )
            except Exception as e:  # noqa: BLE001
                logger.debug("[wx][step=outbox_claim_failed] %s", e)
                return
            if row is None:
                return
            text = str(row.get("message") or "")
            session_key = str(row.get("session_key") or "")
            peer = str(row.get("peer") or "") or (
                self._peer_wxid_from_session(session_key) if session_key else ""
            )
            cid = int(row["id"])
            if not text or not peer:
                try:
                    rp.complete_send(cid, ok=False, reason="no_target")
                except Exception as e:  # noqa: BLE001
                    logger.debug("[wx][step=outbox_complete_failed] %s", e)
                continue
            if not self.token:
                # 本机 slot 离线不是这条消息的死刑：另一 worker 的同 owner 在线
                # slot 可能投得了 → 退回 pending（认领上限防乒乓，块3）。
                try:
                    rp.requeue_send(cid, reason="channel_offline")
                except Exception as e:  # noqa: BLE001
                    logger.debug("[wx][step=outbox_requeue_failed] %s", e)
                continue
            kind = str(row.get("kind") or "")
            ok = False
            try:
                # 拆条链可达性（补全，见上方 docstring）：kind=proactive 经
                # 两个投递点共用的唯一真源 `utils.humanize_delivery.direct_send_proactive`
                # —— 默认（proactive_split=False / 配置不可读）逐字回落
                # `send_text`（零行为变更）；开启且连接器支持拆条时才走
                # 「拆条 + 段间节奏」。reminder 等其它 kind 不属主动拆条语义，
                # 路径不动（即便开关开启）。
                if kind == "proactive":
                    from utils.humanize_delivery import direct_send_proactive

                    ok = direct_send_proactive(self, peer, text)
                else:
                    ok = self.send_text(text, to_user=peer)
            except Exception as e:  # noqa: BLE001
                logger.warning("[wx][step=outbox_send_error] %s", e)
            try:
                rp.complete_send(
                    cid, ok=ok, reason="" if ok else "send_api_rejected",
                    slot=self.slot,
                )
            except Exception as e:  # noqa: BLE001
                logger.debug("[wx][step=outbox_complete_failed] %s", e)

    def _outbox_thread(self) -> None:
        """守护线程：按 tick 消费控制面 outbox（见 _drain_outbox 说明）。"""
        while not self._stop:
            time.sleep(_OUTBOX_TICK)
            self._drain_outbox()

    # （D16 退役，2026-10-12）好友「角色」自选指令链已整体拆除：peer_character
    # 模块删除，角色选择唯一入口 = 网页控制台 /characters/{id}/activate，
    # 聊天侧由 activate 同步的扫码人绑定（wechat_bindings）决定角色。

    def send_reply_segments(self, peer: str, reply: str, *, context_token: str = "") -> str:
        """拆条投递一条回复，**只返回 API 已明确受理的前缀**；失败段及后续不进入历史。

        设计契约第 1 步：由私有 ``_send_reply_segments`` 公有化（主链 publish 与
        主动线 wiring 共用同一实现），**不留私有别名 shim**。

        锁契约（单一口径）：本方法**不自持** ``_peer_lock`` —— 调用方负责持锁。
        主链由 ``_handle_message_serial`` 外层 ``with self._peer_lock(...)`` 覆盖
        生成与发送全程；主动线由 ``main.send_proactive_segmented`` 在外部补同一把
        可重入 RLock（RLock，重复获取不自死锁）。**不得在本方法内再持一半**。
        """
        segments = split_reply_for_wechat(reply)
        accepted = []
        for idx, segment in enumerate(segments):
            try:
                if idx:
                    time.sleep(_segment_delay(segments[idx - 1]))
                response = _send_text(
                    to=peer, text=segment,
                    context_token=self._get_context_token(peer) or context_token,
                    token=self.token, base_url=self.base_url,
                )
                ok, error = _api_ok(response)
                if not ok:
                    logger.warning("[wx][step=reply_send_failed] peer=%s part=%s error=%s", peer, idx + 1, error)
                    break
                accepted.append(segment)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[wx][step=reply_send_failed] peer=%s part=%s error=%s", peer, idx + 1, exc)
                break
        return "\n".join(accepted)

    def _inbound_quota_allow(self) -> bool:
        """per-owner 入站日配额记账（ABUSE-4，2026-10-09）。

        - 放在幂等认领（inbound_claim）**之前**：超限消息不认领、不落幂等账，
          重放/幂等语义零触碰；也在 ``_peer_lock`` 之外（不加重串行等待）。
        - 本地日界滚动（now_local），进程内计数；配额含失败消息（防绕过试错）。
        - 遗留全局通道（owner_user_id=None）无法归属账号 → 调用方跳过（既有行为）。
        """
        today = now_local().date().isoformat()
        with self._state_lock:
            if self._inbound_daily_date != today:
                self._inbound_daily_date = today
                self._inbound_daily_count = 0
            if self._inbound_daily_count >= self._inbound_daily_limit:
                return False
            self._inbound_daily_count += 1
            return True

    def _handle_message(self, raw_msg):
        """处理一条消息（全链路结构化日志：接收 → 路由 → LLM → 回复）"""
        msg_type = raw_msg.get("message_type", 0)
        if msg_type not in (1, 3, 34):  # 放行用户文本(1)/图片(3)/语音(34)，其余（系统通知、自发回显等）仍丢弃
            # 可观测（2026-10-12）：iLink 下用户消息 message_type 恒为 1（媒体语义在 item 层），
            # 落到这里的多为 bot 自发回显(2)/系统通知——静默丢弃改为 INFO 记账
            logger.info(
                "[wx][step=msg_type_skipped] msg_type=%s from=%s",
                msg_type, raw_msg.get("from_user_id", ""),
            )
            return

        # ABUSE-4：入站日配额快速失败（per-owner）。只对真实用户消息计数，
        # 超限在幂等认领与串行处理之前直接丢弃（不回复、不落账）。
        if self.owner_user_id is not None and not self._inbound_quota_allow():
            logger.warning(
                "[wx][step=inbound_quota_exceeded] owner=%s msg_type=%s peer=%s",
                self.owner_user_id, msg_type, raw_msg.get("from_user_id", ""),
            )
            return

        msg_id = str(raw_msg.get("message_id", raw_msg.get("seq", "")))
        from_user = raw_msg.get("from_user_id", "")
        context_token = raw_msg.get("context_token", "")
        # 块9缺陷I：入站幂等升级为控制面持久账 —— 处理**前**认领、完整处理后
        # done、异常 release（允许合法重试）。同 message_id 的重放（get_updates
        # 重发 / 宿主崩溃后新进程接管）不再重复执行工具与 LLM 回合。
        # 内存 _received_msgs 降级为兜底：仅控制面不可用/无 owner/无 id 时使用。
        rp = _plane()
        use_plane = rp is not None and bool(msg_id) and self.owner_user_id is not None
        plane_claimed = False
        if use_plane:
            try:
                plane_claimed = bool(
                    rp.inbound_claim(
                        msg_id,
                        self._session_key(from_user) if from_user else "",
                    )
                )
            except Exception as e:  # noqa: BLE001
                logger.debug("[wx][step=inbound_claim_failed] msg_id=%s error=%s", msg_id, e)
                plane_claimed = True  # 控制面故障不阻断入站（降级旧链路）
            if not plane_claimed:
                logger.info(
                    "[wx][step=inbound_replay_skipped] msg_id=%s owner=%s",
                    msg_id, self.owner_user_id,
                )
                return
        else:
            with self._state_lock:
                if msg_id in self._received_msgs:
                    return
                self._received_msgs[msg_id] = True
                while len(self._received_msgs) > _RECEIVED_MSGS_MAX:
                    self._received_msgs.popitem(last=False)
        self._cleanup_context_tokens()

        if context_token and from_user:
            with self._state_lock:
                self._context_tokens[from_user] = {"token": context_token, "ts": time.time()}
            # 落盘：服务重启后仍可在窗口期内主动发送（见 CONTEXT_TOKENS_PATH 注释）
            self._save_context_tokens()
        if not from_user:
            return
        with self._state_lock:
            self._last_user_id = from_user

        try:
            with self._peer_lock(self._session_key(from_user)):
                self._handle_message_serial(raw_msg, msg_id, from_user, context_token)
        except Exception:
            if use_plane and plane_claimed:
                with suppress(Exception):
                    rp.inbound_release(msg_id)
            raise
        if use_plane and plane_claimed:
            try:
                rp.inbound_done(msg_id)
            except Exception as e:  # noqa: BLE001
                logger.debug("[wx][step=inbound_done_failed] msg_id=%s error=%s", msg_id, e)

    def _handle_message_serial(self, raw_msg, msg_id, from_user, context_token):
        """扫码人从入站处理到最后一段受理/落库的完整串行区间。"""
        # D16 通道语义守卫（2026-10-12 裁决）：谁扫码谁聊，一对一。非扫码人
        # 发来的消息在产品语义中不存在（生产取证：活跃通道第三方消息 0 条），
        # 拒绝处理（不解析、不进 LLM、不落任何记忆）。遗留全局通道（无 owner）
        # 不适用；凭证缺扫码人 id 属登录链异常，fail-open 放行但必须告警。
        if self.owner_user_id is not None:
            if self.scanner_wxid:
                if str(from_user) != self.scanner_wxid:
                    logger.warning(
                        "[wx][step=non_scanner_rejected] msg_id=%s from=%s owner=%s",
                        msg_id, from_user, self.owner_user_id,
                    )
                    return
            else:
                logger.error(
                    "[wx][step=scanner_id_missing] owner=%s 凭证缺扫码人 id，"
                    "fail-open 放行（登录链需修复）", self.owner_user_id,
                )
        # iLink item 解析（1=文本/2=图片/3=语音；旧协议 34 语音、3 图片内联 base64 兼容保留）
        parsed = parse_ilink_items(raw_msg.get("item_list", []))
        text = parsed.text
        voice_data = parsed.legacy_voice_data
        image_data = parsed.legacy_image_data
        if parsed.unknown_types:
            # 未识别 item（47 表情等）：INFO 记账后按无内容处理
            logger.info(
                "[wx][step=media_unknown_type] msg_id=%s user=%s item_types=%s",
                msg_id, from_user, parsed.unknown_types,
            )

        # ── iLink 语音（item type=3）：voice_item.text 为微信服务端 ASR（'if available'，
        #    不保证存在）——非空直接作正文；缺省走媒体下载+本地 ASR，仍不可得回受控话术 ──
        if not text and parsed.voice_text:
            text = parsed.voice_text
            # P0 隐私：转写原文不落日志，只记 msg_id 与长度
            logger.info("[wx][step=voice_text] msg_id=%s user=%s text_len=%d",
                        msg_id, from_user, len(text))
            self._record_media_step(from_user, "voice_text", msg_id=msg_id,
                                    text_len=len(text))
        elif not text and parsed.voice_media is not None:
            text = self._voice_media_to_text(parsed.voice_media, msg_id, from_user)
            if not text:
                # 转写不可得：不拿空文本进 LLM 主链，受控话术告知（不静默丢）
                self._send_media_unsupported_notice(from_user, context_token,
                                                    kind="voice_unsupported")
                return

        # ── iLink 图片（item type=2）：CDN 引用下载 → base64 → 复用既有 image_data 附件链 ──
        if parsed.image_payload is not None:
            mode, _vision_model, _max_bytes = self._image_mode()
            if mode == "off":
                logger.info("[wx][step=image_unsupported] msg_id=%s user=%s reason=mode_off",
                            msg_id, from_user)
                self._record_media_step(from_user, "image_unsupported",
                                        msg_id=msg_id, reason="mode_off")
            else:
                raw = self._download_media(parsed.image_payload, msg_id=msg_id,
                                           user=from_user, kind="image")
                if raw:
                    # 上限按原始字节在下载点判定：base64 串长 ≈ 4/3 原始大小，
                    # 4.5MB 图 b64 后 6MB 会被误判超限（有效上限变 3.75MB）。
                    # 超限置空 image_data，由下方守卫接住（有配文走文本）。
                    if len(raw) > _max_bytes:
                        logger.info("[wx][step=image_unsupported] msg_id=%s user=%s "
                                    "reason=oversized bytes=%d",
                                    msg_id, from_user, len(raw))
                        self._record_media_step(from_user, "image_unsupported",
                                                msg_id=msg_id, reason="oversized",
                                                bytes=len(raw))
                    else:
                        image_data = base64.b64encode(raw).decode("ascii")
                else:
                    logger.info("[wx][step=image_download_failed] msg_id=%s user=%s",
                                msg_id, from_user)
                    self._record_media_step(from_user, "image_download_failed",
                                            msg_id=msg_id)
            if not image_data and not text:
                # 图片收到但不可用且无文本 → 受控降级固定话术（池在 fallback_lines），不静默丢
                logger.info("[wx][step=image_unsupported] msg_id=%s user=%s reason=no_usable_image",
                            msg_id, from_user)
                self._record_media_step(from_user, "image_unsupported",
                                        msg_id=msg_id, reason="no_usable_image")
                self._send_media_unsupported_notice(from_user, context_token)
                return

        if not text and not voice_data and not image_data:
            logger.debug("消息无文本/语音/图片内容 msg_id=%s user=%s", msg_id, from_user)
            return

        # （D16 退役）好友「角色」自选指令拦截已于 2026-10-12 随 peer 机制整体
        # 拆除：角色选择唯一入口 = 网页控制台 /characters/{id}/activate。

        # ── 图片（V2）：image_data → 附件直传（B）或描述注入（A 降级）──
        attachments: list = []
        if image_data:
            mode, vision_model, max_bytes = self._image_mode()
            if mode != "off":
                from multimodal.image_attachment import build_attachments

                parts = build_attachments([image_data])
                # 上限按原始字节判定（base64 串长 ≈ 4/3 原始大小；旧协议内联图
                # 不经下载点检查，此处兜底保持同一口径）
                raw_len = len(base64.b64decode(image_data)) if parts else 0
                oversized = not parts or raw_len > max_bytes
                if not oversized and (mode == "direct" or vision_model):
                    attachments = parts  # B：多模态直传
                    logger.info(
                        "[wx][step=image_direct] msg_id=%s user=%s parts=%d",
                        msg_id, from_user, len(parts),
                    )
                    self._record_media_step(from_user, "image_direct",
                                            msg_id=msg_id, parts=len(parts),
                                            bytes=raw_len)
                elif mode in ("auto", "describe"):
                    desc = self._describe_image(image_data)  # A：降级为描述注入
                    if desc:
                        # 图文并存：配文在前、所见作图片上下文行（`or` 短路会整体丢图片语义）。
                        # 2026-10-13 拟人化：标记从「[图片：…]」（第三方报告腔）改
                        # 「[你亲眼看到：…]」（亲历腔）——角色按 persona 侧铁则以
                        # 「我刚看到」的口吻自然说起，不复述描述文本。
                        text = (
                            f"{text}\n[你亲眼看到：{desc}]" if text else f"[你亲眼看到：{desc}]"
                        )
                        logger.info(
                            "[wx][step=image_describe] msg_id=%s user=%s len=%d",
                            msg_id, from_user, len(desc),
                        )

        # ── 语音转文字（候选 A，2026-08-28）：voice_data(base64 silk) → ASR → text ──
        if not text and voice_data:
            asr_text = self._transcribe_voice(voice_data)
            if asr_text:
                text = asr_text
                # P0 隐私：转写原文不落日志，只记 msg_id 与长度
                logger.info("[wx][step=asr] msg_id=%s user=%s text_len=%d",
                            msg_id, from_user, len(text))
            else:
                logger.info("[wx][step=asr_unavailable] msg_id=%s user=%s", msg_id, from_user)

        # 终局守卫（2026-10-12）：图片在但 A/B 均未产出可用形态且无任何文本——
        # 损坏 base64 / describe 失败残留不得拿空正文进 LLM 主链。
        if image_data and not attachments and not text:
            logger.info("[wx][step=image_unsupported] msg_id=%s user=%s reason=no_usable_image",
                        msg_id, from_user)
            self._record_media_step(from_user, "image_unsupported",
                                    msg_id=msg_id, reason="no_usable_image")
            self._send_media_unsupported_notice(from_user, context_token)
            return

        from utils.local_time import now_local

        today = now_local().strftime("%Y-%m-%d")
        with self._state_lock:
            if today != self._last_day:
                self._messages_today = 0
                self._last_day = today
            self._messages_today += 1
            _today_count = self._messages_today
        self._merge_session_state({"messages_today": _today_count})

        t_start = time.perf_counter()
        session_key = self._session_key(from_user)
        # P0 隐私：入站消息原文不落日志，只记 msg_id/session_key/长度
        logger.info(
            "[wx][step=receive] msg_id=%s owner=%s peer=%s session=%s text_len=%d",
            msg_id, self.owner_user_id, from_user, session_key, len(text),
        )

        sent_reply: dict[str, Any] = {}

        def publish(reply: str) -> str:
            if sent_reply:
                return str(sent_reply["accepted"])
            accepted = self.send_reply_segments(from_user, reply, context_token=context_token)
            sent_reply.update(accepted=accepted, complete=bool(accepted) and accepted == "\n".join(split_reply_for_wechat(reply)))
            return accepted

        try:
            result = _call_user_manager(
                self.user_manager, session_key, text, attachments, reply_sender=publish,
                message_id=msg_id,
            )
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
        # 编排器已经定稿；传输层只拆条，不二次改写正文。
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
            # P0 隐私：回复原文不落日志，只记 msg_id/session_key/长度/耗时
            logger.info(
                "[wx][step=llm_done] msg_id=%s session=%s reply_len=%d elapsed=%.2fs llm_time=%s",
                msg_id, session_key, len(reply), t_elapsed, process_time,
            )

        # 早期拒绝（尚未进入模型）可能未调用发送回调；统一出口只发送一次。
        if not sent_reply:
            publish(reply)
        if not sent_reply.get("complete"):
            return
        # 追问链已退役（2026-10-12）：回复后隔多久、要不要再接话，
        # 唯一 Owner = proactive 决策链（decide_proactive + 节奏双闸冷却）。
        logger.info("[wx][step=reply_accepted] msg_id=%s session=%s", msg_id, session_key)

        voice_result = result.get("voice")
        if voice_result:
            try:
                # W7：统一音频对象携带实际格式（云端 mp3 / SAPI 兜底 wav），
                # 转码按真实格式驱动，不再硬编 mp3
                from voice.audio_converter import AudioFormatConverter
                from voice.audio_result import coerce_voice_payload
                voice_data, voice_fmt = coerce_voice_payload(voice_result)
                converter = AudioFormatConverter()
                fmt = "silk"
                silk_audio = converter.to_silk(voice_data, voice_fmt)
                if silk_audio is None:
                    fmt = "amr"
                    silk_audio = converter.to_amr(voice_data, voice_fmt)
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

    def _download_media(self, ref: MediaRef, timeout: float = MEDIA_DOWNLOAD_TIMEOUT_S,
                        msg_id: str = "", user: str = "", kind: str = "media") -> bytes | None:
        """iLink CDN 媒体引用 → 原始字节（AES-128-ECB/PKCS7 解密；无 key 走明文兜底）。

        优先服务端下发的 full_url，缺省拼 {CDN 基址}/download?encrypted_query_param=...
        （基址为 photon-hq 逆向默认值，线上变更时以服务端下发为准）。任何失败返回
        None（INFO 记账，不炸主流程）。
        """
        if ref is None or (not ref.full_url and not ref.encrypt_query_param):
            logger.info("[wx][step=media_download_failed] msg_id=%s user=%s kind=%s reason=empty_ref",
                        msg_id, user, kind)
            return None
        url = ref.full_url or (
            f"{DEFAULT_CDN_BASE_URL}/download?encrypted_query_param={quote(ref.encrypt_query_param, safe='')}"
        )
        try:
            resp = requests.get(url, timeout=timeout)
            resp.raise_for_status()
            data = resp.content
            if ref.aes_key:
                data = _aes_ecb_decrypt(data, ref.aes_key)
            # P0 隐私：媒体内容不落日志，只记字节数与是否解密
            logger.info(
                "[wx][step=media_downloaded] msg_id=%s user=%s kind=%s bytes=%d encrypted=%s",
                msg_id, user, kind, len(data), ref.aes_key is not None,
            )
            return data or None
        except Exception as e:  # noqa: BLE001
            logger.info("[wx][step=media_download_failed] msg_id=%s user=%s kind=%s error=%s",
                        msg_id, user, kind, e)
            return None

    def _voice_asr_available(self) -> bool:
        """本地 ASR 是否可用（voice.asr.enabled 且 api_base 非空；与 _transcribe_voice 同源配置）。"""
        cfg = _read_voice_asr_config()
        return bool(cfg.get("enabled", False)) and bool(cfg.get("api_base"))

    def _voice_media_to_text(self, ref: MediaRef, msg_id: str, from_user: str) -> str:
        """iLink 语音媒体引用 → 文本：CDN 下载 SILK → 本地 ASR（配置驱动）。

        返回空串表示不可得（ASR 未启用/下载失败/转写为空），调用方跳过该消息；
        失败原因落 [wx][step=voice_unavailable] 的 reason 字段。
        """
        if not self._voice_asr_available():
            logger.info("[wx][step=voice_unavailable] msg_id=%s user=%s reason=asr_disabled",
                        msg_id, from_user)
            self._record_media_step(from_user, "voice_unavailable",
                                    msg_id=msg_id, reason="asr_disabled")
            return ""
        silk = self._download_media(ref, msg_id=msg_id, user=from_user, kind="voice")
        if not silk:
            logger.info("[wx][step=voice_unavailable] msg_id=%s user=%s reason=download_failed",
                        msg_id, from_user)
            self._record_media_step(from_user, "voice_unavailable",
                                    msg_id=msg_id, reason="download_failed")
            return ""
        asr_text = self._transcribe_voice(base64.b64encode(silk).decode("ascii"))
        if not asr_text:
            logger.info("[wx][step=voice_unavailable] msg_id=%s user=%s reason=asr_empty",
                        msg_id, from_user)
            self._record_media_step(from_user, "voice_unavailable",
                                    msg_id=msg_id, reason="asr_empty")
        return asr_text

    def _send_media_unsupported_notice(
        self, peer: str, context_token: str = "", kind: str = "image_unsupported",
    ) -> None:
        """媒体收到但不可用且无文本时的受控降级话术（池在 utils/fallback_lines，不硬编码）。

        kind 对应 fallback_lines 池名（image_unsupported / voice_unsupported）。
        注意：这是当轮即时告知，不是「定时再补一句」——接话时机 Owner 仍是 proactive。
        """
        try:
            from utils.fallback_lines import get_fallback_line
            from utils.reply_mode import read_reply_mode

            line = get_fallback_line(None, kind, read_reply_mode())
        except Exception:  # noqa: BLE001
            logger.debug("[wx][step=fallback_line_failed] 降级话术获取失败，跳过媒体占位回复")
            return
        try:
            self.send_reply_segments(peer, line, context_token=context_token)
        except Exception as e:  # noqa: BLE001
            logger.warning("[wx][step=reply_send_failed] peer=%s error=%s", peer, e)

    def _record_media_step(self, from_user: str, step: str, **meta: Any) -> None:
        """媒体处理 step 落账 event_ledger（event_type=media_step）。

        动机：媒体量/成功率统计——app.log 10MB 轮转只存约 3 天，账本 prune 30 天
        正好匹配统计窗。payload 只含 step/reason/bytes/text_len/msg_id 等元数据
        （P0：无消息原文）；try/except 全包 fail-soft，落账故障不炸消息主流程。
        """
        try:
            from shisi.agent_plane.event_ledger import default_ledger

            default_ledger().append(
                session_key=self._session_key(from_user),
                event_type="media_step",
                actor="wechat_connector",
                payload={"step": step, **meta},
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("[wx][step=media_step_ledger_failed] error=%s", e)

    def _image_mode(self) -> tuple:
        """读 config/system.yaml → multimodal.image；返回 (mode, vision_model, max_bytes)。

        未配置或读取失败时按 auto/无视觉模型处理（即自动降级为描述注入）。
        max_bytes 是「图片原始字节」上限（2026-10-12 起下载点按 len(raw) 判定；
        base64 串长 ≈ 4/3 原始大小，不作判定依据）。
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
                self._asr_handler = ASRHandler(_read_voice_asr_config())
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
        from utils.local_time import now_local

        today = now_local().strftime("%Y-%m-%d")
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
