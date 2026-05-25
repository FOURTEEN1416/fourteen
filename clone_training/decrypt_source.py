"""
WeChat Decrypt 数据源适配器 — 集成 ylytdeng/wechat-decrypt

使用 wechat-decrypt 提取微信 4.x 数据库的加密密钥、解密数据库、
然后读取解密后的聊天记录，输出为标准对话格式。

工作流程：
  1. 检测 wechat-decrypt 是否安装（third_party/wechat-decrypt/）
  2. 检测微信是否正在运行（Weixin.exe）
  3. 自动提取密钥 → 解密数据库 → 读取消息
  4. 输出 [{"user": .., "reply": .., ...}] 格式

用法:
    from clone_training.decrypt_source import DecryptSource
    ds = DecryptSource()
    conversations = ds.extract("目标联系人wxid或备注名")
"""

from __future__ import annotations

import hashlib
import importlib.util as _importlib_util
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("clone.decrypt_source")

# ── 路径常量 ──
PROJECT_ROOT = Path(__file__).parent.parent.absolute()
DECRYPT_DIR = PROJECT_ROOT / "third_party" / "wechat-decrypt"
DECRYPT_CONFIG = DECRYPT_DIR / "config.json"
DECRYPT_KEYS = DECRYPT_DIR / "all_keys.json"
DECRYPT_OUTPUT = DECRYPT_DIR / "decrypted"

# ── wechat-decrypt 路径处理（使用 importlib 避免修改全局 sys.path） ──
_WD_PATH = DECRYPT_DIR

# wechat-decrypt 的消息解析模块（可选，有降级）
# 使用 importlib.util 动态加载，避免修改 sys.path 导致命名冲突

_wd_mcp = None
_wd_resolve_sender = None

# 尝试动态加载 mcp_server 模块
try:
    _mcp_spec = _importlib_util.spec_from_file_location(
        "wd_mcp_server", _WD_PATH / "mcp_server.py"
    )
    if _mcp_spec and _mcp_spec.loader:
        _wd_mcp = _importlib_util.module_from_spec(_mcp_spec)
        _mcp_spec.loader.exec_module(_wd_mcp)
except Exception:  # noqa: BLE001
    _wd_mcp = None

# 尝试动态加载 chat_export_helpers 模块
try:
    _helpers_spec = _importlib_util.spec_from_file_location(
        "wd_chat_export_helpers", _WD_PATH / "chat_export_helpers.py"
    )
    if _helpers_spec and _helpers_spec.loader:
        _wd_helpers = _importlib_util.module_from_spec(_helpers_spec)
        _helpers_spec.loader.exec_module(_wd_helpers)
        _wd_resolve_sender = getattr(_wd_helpers, "_resolve_sender", None)
except Exception:  # noqa: BLE001
    _wd_resolve_sender = None


class DecryptSourceError(Exception):
    """解密数据源异常"""


class DecryptSource:
    """wechat-decrypt 数据源适配器"""

    def __init__(self, decrypt_path: str | None = None):
        self.decrypt_path = Path(decrypt_path) if decrypt_path else DECRYPT_DIR
        self._keys: dict = {}
        self._contacts: dict = {}
        self._self_wxid: str = ""
        self._ready = False
        self._table_cache: dict[str, list[dict]] = {}  # wxid → 消息表列表缓存

    # ── 公开接口 ──────────────────────────────────

    def health_check(self) -> dict:
        """检查 wechat-decrypt 是否就绪"""
        status = {
            "wechat_decrypt_found": self.decrypt_path.exists(),
            "config_found": (self.decrypt_path / "config.json").exists(),
            "keys_found": (self.decrypt_path / "all_keys.json").exists(),
            "decrypted_dir_exists": (DECRYPT_OUTPUT).exists(),
            "wechat_running": self._is_wechat_running(),
            "ready": False,
        }
        status["ready"] = all([
            status["wechat_decrypt_found"],
            status["wechat_running"],
        ])
        return status

    def ensure_ready(self, force_extract: bool = False) -> None:
        """确保 wechat-decrypt 就绪：提取密钥 → 解密数据库"""
        if self._ready and not force_extract:
            return

        # 1. 检查 wechat-decrypt 是否存在
        if not self.decrypt_path.exists():
            raise DecryptSourceError(
                f"wechat-decrypt 未找到！请先安装：\n"
                f"  1. git clone https://github.com/ylytdeng/wechat-decrypt.git\n"
                f"     到 {self.decrypt_path}\n"
                f"  2. pip install -r {self.decrypt_path / 'requirements.txt'}"
            )

        # 2. 检查微信是否运行
        if not self._is_wechat_running():
            raise DecryptSourceError("微信未运行！请先打开微信 4.x 并登录")

        # 3. 提取密钥（如需要）
        keys_path = self.decrypt_path / "all_keys.json"
        if not keys_path.exists() or force_extract:
            logger.info("正在提取微信数据库密钥（需管理员权限）...")
            self._run_find_keys()

        # 4. 解密数据库（如需要）
        if not DECRYPT_OUTPUT.exists() or force_extract:
            logger.info("正在解密微信数据库...")
            self._run_decrypt_db()

        # 5. 加载密钥和联系人缓存
        self._load_keys()
        self._load_contacts()
        if force_extract:
            self._table_cache.clear()

        self._ready = True
        logger.info("✅ wechat-decrypt 就绪")

    def get_contacts(self, keyword: str = "") -> list[dict[str, Any]]:
        """获取所有联系人列表"""
        self._ensure_contacts_loaded()
        if not keyword:
            return [
                {"username": k, "display_name": v}
                for k, v in self._contacts.items()
            ]
        keyword = keyword.lower()
        results = []
        for username, display in self._contacts.items():
            if keyword in username.lower() or keyword in display.lower():
                results.append({"username": username, "display_name": display})
        return results

    def extract(
        self,
        target: str,
        max_messages: int = 5000,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict[str, Any]]:
        """提取指定联系人的聊天对话

        Args:
            target: 联系人 wxid / 备注名 / 昵称
            max_messages: 最大消息数
            date_from: 起始日期 "2024-01-01"
            date_to: 截止日期 "2024-12-31"

        Returns:
            标准对话格式 [{"user":.., "reply":.., "timestamp":.., "is_self":.., "source":"decrypt"}]
        """
        self.ensure_ready()

        # 解析目标联系人 wxid
        target_wxid = self._resolve_target(target)
        if not target_wxid:
            logger.warning("未找到联系人: %s", target)
            return []

        logger.info("提取联系人 [%s] %s 的聊天记录...", target_wxid, self._contacts.get(target_wxid, ""))

        # 查找对应的消息表
        msg_tables = self._find_message_tables(target_wxid)
        if not msg_tables:
            logger.warning("未找到 %s 的消息表", target_wxid)
            return []

        # 读取消息
        messages = []
        for table in msg_tables:
            rows = self._query_messages(table["db_path"], table["table_name"], target_wxid, date_from, date_to, max_messages)
            messages.extend(rows)

        if not messages:
            logger.warning("无文本消息记录")
            return []

        # 按时间排序
        messages.sort(key=lambda m: m.get("timestamp", 0))

        # 截取
        if len(messages) > max_messages:
            messages = messages[:max_messages]

        # 转为对话对
        conversations = self._build_conversations(messages)
        logger.info("提取: %d 条文本消息 → %d 轮对话", len(messages), len(conversations))
        return conversations

    # ── 内部实现 ──────────────────────────────────

    def _ensure_contacts_loaded(self):
        """确保联系人已加载"""
        if not self._contacts:
            self.ensure_ready()

    def _is_wechat_running(self) -> bool:
        """检查 Weixin.exe 是否在运行"""
        try:
            import psutil
            for proc in psutil.process_iter(["name"]):
                if proc.info.get("name") in ("Weixin.exe", "WeChat.exe", "WeChat"):
                    return True
            return False
        except ImportError:
            # 兜底：用 tasklist
            try:
                result = subprocess.run(
                    ["tasklist", "/FI", "IMAGENAME eq Weixin.exe"],
                    capture_output=True, text=True, timeout=5,
                )
                return "Weixin.exe" in result.stdout
            except Exception:  # noqa: BLE001
                return True  # 无法检测时乐观返回

    def _run_find_keys(self) -> None:
        """运行 wechat-decrypt 的密钥提取"""
        find_keys = self.decrypt_path / "find_all_keys_windows.py"
        if not find_keys.exists():
            find_keys = self.decrypt_path / "find_all_keys.py"
        if not find_keys.exists():
            raise DecryptSourceError(f"找不到 find_all_keys.py: {find_keys}")

        # 运行密钥提取（可能需要管理员权限）
        logger.info("运行: python %s", find_keys.name)
        result = subprocess.run(
            [sys.executable, str(find_keys)],
            cwd=str(self.decrypt_path),
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip()
            logger.warning("密钥提取可能有误: %s", err[:200])
        else:
            logger.info("密钥提取完成")

        # 检查生成的密钥文件
        keys_path = self.decrypt_path / "all_keys.json"
        if not keys_path.exists():
            raise DecryptSourceError(
                "密钥文件未生成！常见原因：\n"
                "  1. 需要以管理员身份运行（右键终端→以管理员身份运行）\n"
                "  2. 微信版本不是 4.x\n"
                "  3. 微信未登录"
            )
        logger.info("✅ 密钥文件: %s", keys_path)

    def _run_decrypt_db(self) -> None:
        """运行 wechat-decrypt 的数据库解密（增量模式）"""
        decrypt_script = self.decrypt_path / "decrypt_db.py"
        if not decrypt_script.exists():
            raise DecryptSourceError("找不到 decrypt_db.py")

        logger.info("运行: python decrypt_db.py -i（增量模式，仅解密变更的数据库）")
        result = subprocess.run(
            [sys.executable, str(decrypt_script), "-i"],  # ← 增量模式，省掉未变更 DB 的解密
            cwd=str(self.decrypt_path),
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip()
            raise DecryptSourceError(f"数据库解密失败: {err[:300]}")

        # 检查解密输出
        if not DECRYPT_OUTPUT.exists():
            raise DecryptSourceError("解密目录未生成，可能是密钥不匹配")
        logger.info("✅ 数据库已解密: %s", DECRYPT_OUTPUT)

        # 清理解密过程产生的 -shm/-wal 残留文件
        for f in DECRYPT_OUTPUT.rglob("*-shm"):
            f.unlink(missing_ok=True)
        for f in DECRYPT_OUTPUT.rglob("*-wal"):
            f.unlink(missing_ok=True)

    def _load_keys(self) -> None:
        """加载解密密钥"""
        keys_path = self.decrypt_path / "all_keys.json"
        if not keys_path.exists():
            self._keys = {}
            return
        with open(keys_path, encoding="utf-8") as f:
            raw = json.load(f)
        # 过滤元数据字段（以下划线开头的键）
        self._keys = {k: v for k, v in raw.items() if not k.startswith("_")}
        logger.info("已加载 %d 个数据库密钥", len(self._keys))

    def _load_contacts(self) -> None:
        """从解密后的 contact.db 加载联系人"""
        contact_db = self._find_decrypted_db("contact", "contact.db")
        if not contact_db:
            logger.warning("未找到解密后的 contact.db")
            return

        conn = sqlite3.connect(str(contact_db))
        try:
            rows = conn.execute("SELECT username, nick_name, remark FROM contact").fetchall()
            for username, nick_name, remark in rows:
                display = remark or nick_name or username
                self._contacts[username] = display
                # 同时也建立 备注名→wxid 的反向映射（方便模糊查询）
        except sqlite3.OperationalError as e:
            logger.warning("读取联系人失败: %s", e)
        finally:
            conn.close()

        # 尝试获取自己的 wxid
        self._self_wxid = self._detect_self_wxid()
        logger.info("已加载 %d 个联系人，自己: %s", len(self._contacts), self._self_wxid)

    def _detect_self_wxid(self) -> str:
        """从数据库目录推断自己的 wxid"""
        if not self._keys:
            return ""
        # 尝试从 config.json 获取
        config_path = self.decrypt_path / "config.json"
        if config_path.exists():
            try:
                with open(config_path, encoding="utf-8") as f:
                    cfg = json.load(f)
                db_dir = cfg.get("db_dir", "")
                if db_dir:
                    parent = os.path.dirname(db_dir)
                    wxid = os.path.basename(parent) if parent else ""
                    if wxid and wxid in self._contacts:
                        return wxid
                    # 可能有后缀 _xxxx
                    if "_" in wxid:
                        base = wxid.rsplit("_", 1)[0]
                        if base in self._contacts:
                            return base
            except Exception:  # noqa: BLE001
                pass
        return ""

    def _resolve_target(self, target: str) -> str | None:
        """将目标（wxid/备注名/昵称）解析为 wxid"""
        if not target:
            return None

        # 已经是 wxid 格式
        if target in self._contacts:
            return target
        if target.startswith("wxid_") or "@chatroom" in target:
            return target

        # 模糊匹配备注名或昵称
        target_lower = target.lower()
        for wxid, display in self._contacts.items():
            if target_lower == display.lower():
                return wxid  # type: ignore[no-any-return]
        for wxid, display in self._contacts.items():
            if target_lower in display.lower():
                return wxid  # type: ignore[no-any-return]

        return None

    def _find_decrypted_db(self, *parts: str) -> Path | None:
        """在解密目录中查找指定数据库"""
        # 1. 优先 DECRYPTED_DIR 下的持久解密文件
        path = DECRYPT_OUTPUT.joinpath(*parts)
        if path.exists():
            return path

        # 2. 回退到 temp 缓存（DBCache 目录）
        temp_cache = Path(os.environ.get("TEMP", "/tmp")) / "wechat_mcp_cache"
        rel = "/".join(parts)
        key_hash = hashlib.md5(rel.encode()).hexdigest()[:12]
        cache_path = temp_cache / f"{key_hash}.db"
        if cache_path.exists():
            return cache_path

        return None

    def _find_message_tables(self, target_wxid: str) -> list[dict[str, Any]]:
        """查找目标联系人的消息表
        优先从 decrypted/message_*.db 查找（稳定可靠），
        回退到 mcp_server 的临时缓存路径。"""
        if target_wxid in self._table_cache:
            return self._table_cache[target_wxid]

        # 优先：直接读 decrypted/message/*.db（我们解密后的文件）
        tables = self._find_message_tables_fallback(target_wxid)
        if tables:
            self._table_cache[target_wxid] = tables
            return tables

        # 回退：用 mcp_server（可能指向 temp cache）
        if _wd_mcp is not None:
            try:
                ctx = _wd_mcp._resolve_chat_context(target_wxid)
                if ctx and ctx.get("message_tables"):
                    tables = [
                        {"db_path": t["db_path"], "table_name": t["table_name"]}
                        for t in ctx["message_tables"]
                    ]
                    self._table_cache[target_wxid] = tables
                    return tables
            except Exception as e:  # noqa: BLE001
                logger.debug("mcp_server 查询失败: %s", e)

        return []

    def _find_message_tables_fallback(self, target_wxid: str) -> list[dict[str, Any]]:
        """手动查找消息表（降级方案）"""
        table_hash = hashlib.md5(target_wxid.encode()).hexdigest()
        table_name = f"Msg_{table_hash}"

        message_dir = DECRYPT_OUTPUT / "message"
        if not message_dir.exists():
            return []

        db_files = [p for p in message_dir.glob("message_*.db") if p.is_file()]
        if not db_files:
            return []

        tables = []
        def _check_table(db_path: Path) -> dict | None:
            try:
                conn = sqlite3.connect(str(db_path))
                result = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (table_name,),
                ).fetchone()
                conn.close()
                if result:
                    return {"db_path": str(db_path), "table_name": table_name}
            except sqlite3.Error:
                pass
            return None

        with ThreadPoolExecutor(max_workers=min(8, len(db_files))) as pool:
            futures = {pool.submit(_check_table, p): p for p in db_files}
            for fut in as_completed(futures):
                r = fut.result()
                if r:
                    tables.append(r)

        self._table_cache[target_wxid] = tables
        return tables

    def _query_messages(
        self, db_path: str, table_name: str, target_wxid: str,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 5000,
    ) -> list[dict[str, Any]]:
        """从消息表查询并解码文本消息
        WeChat 4.x 的 message_content 是二进制格式，
        优先用 mcp_server._format_message_text 解码"""
        if _wd_mcp is not None:
            try:
                return self._query_via_mcp(  # type: ignore[no-any-return]
                    db_path, table_name, target_wxid, date_from, date_to, limit
                )
            except Exception as e:  # noqa: BLE001
                logger.debug("mcp_server 解码失败: %s，降级手动读取", e)

        return self._query_messages_fallback(
            db_path, table_name, target_wxid, date_from, date_to, limit
        )

    def _query_via_mcp(
        self, db_path, table_name, target_wxid,
        date_from=None, date_to=None, limit=5000,
    ):
        """用 mcp_server 解码消息（正确处理 WeChat 4.x 二进制格式）"""
        conn = sqlite3.connect(db_path)
        start_ts = self._date_to_ts(date_from) if date_from else None
        end_ts = self._date_to_ts(date_to, end_of_day=True) if date_to else None
        rows = _wd_mcp._query_messages(            conn, table_name,
            start_ts=start_ts, end_ts=end_ts,
            limit=limit, oldest_first=True,
            type_filter=[1],
        )
        names = _wd_mcp.get_contact_names()
        try:
            id_to_username = _wd_mcp._load_name2id_maps(conn)
        except Exception:  # noqa: BLE001
            id_to_username = {}

        messages = []
        for row in rows:
            local_id, local_type, create_time, real_sender_id, raw_content, ct = row

            # 解析发送者
            is_self = False
            if _wd_resolve_sender is not None:
                try:
                    ctx = {
                        "username": target_wxid,
                        "display_name": self._contacts.get(target_wxid, target_wxid),
                        "is_group": "@chatroom" in target_wxid,
                    }
                    label = _wd_resolve_sender(row, ctx, names, id_to_username)
                    is_self = (label == "me")
                except Exception:  # noqa: BLE001
                    pass

            # 解码消息内容（WeChat 4.x 二进制格式）
            decoded = _wd_mcp._decompress_content(raw_content, ct)
            if not decoded:
                if isinstance(raw_content, bytes):
                    decoded = raw_content
                elif isinstance(raw_content, str):
                    decoded = raw_content.encode("utf-8", errors="replace")
                else:
                    decoded = b""

            text_content = ""
            if decoded:
                try:
                    result = _wd_mcp._format_message_text(                        local_id, local_type, decoded,
                        "@chatroom" in target_wxid,
                        target_wxid,
                        self._contacts.get(target_wxid, target_wxid),
                        names,
                    )
                    # _format_message_text 返回 (sender_from_content, text)
                    # 对私聊 sender_from_content 总是空，取 result[1] 才是消息文本
                    if isinstance(result, tuple):
                        text_content = result[1] or ""
                    elif isinstance(result, str):
                        text_content = result
                except Exception:  # noqa: BLE001
                    try:
                        text_content = decoded.decode("utf-8", errors="replace")
                    except Exception:  # noqa: BLE001
                        text_content = ""

            if isinstance(text_content, bytes):
                text_content = text_content.decode("utf-8", errors="replace")
            text_content = str(text_content).strip()

            if not text_content or len(text_content) < 1:
                continue

            messages.append({
                "local_id": local_id,
                "timestamp": create_time or 0,
                "is_self": is_self,
                "content": text_content,
            })

        conn.close()
        return messages

    def _query_messages_fallback(
        self, db_path: str, table_name: str, target_wxid: str,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 5000,
    ) -> list[dict[str, Any]]:
        """手动读取消息（降级方案，用 Name2Id 判断 is_self）"""
        messages = []
        try:
            conn = sqlite3.connect(db_path)

            # 加载 Name2Id → 找出"自己"的 rowid
            self_rowid = None
            try:
                n2i = dict(conn.execute("SELECT rowid, user_name FROM Name2Id").fetchall())
                for rid, uname in n2i.items():
                    if uname == self._self_wxid:
                        self_rowid = rid
                        break
            except Exception:  # noqa: BLE001
                pass

            query = (
                f"SELECT local_id, local_type, create_time, real_sender_id, "
                f"message_content, WCDB_CT_message_content "
                f"FROM [{table_name}] WHERE local_type IN (1, 3)"
            )
            params = []
            if date_from:
                query += " AND create_time >= ?"
                params.append(self._date_to_ts(date_from))
            if date_to:
                query += " AND create_time <= ?"
                params.append(self._date_to_ts(date_to, end_of_day=True))
            query += " ORDER BY create_time ASC"
            if limit:
                query += f" LIMIT {limit}"

            rows = conn.execute(query, params).fetchall()
            conn.close()

            for row in rows:
                _id, _type, ts, sender_id, content, ct = row
                raw = content if isinstance(content, bytes) else b""
                # 尝试各种方式解码
                text = ""
                if content and isinstance(content, str) and content.strip():
                    text = content
                elif raw:
                    try:
                        import zstandard as zstd
                        text = zstd.ZstdDecompressor().decompress(raw).decode("utf-8", errors="replace")
                    except Exception:  # noqa: BLE001
                        pass  # noqa: BLE001
                if not text and raw:
                    try:
                        text = raw.decode("utf-8", errors="replace")
                    except Exception:  # noqa: BLE001
                        text = ""  # noqa: BLE001
                # 跳过二进制乱码(含不可见字符比例过高)
                if isinstance(text, str):
                    visible = sum(1 for c in text if c.isprintable() or c in '\n\r\t')
                    if len(text) > 0 and visible / len(text) < 0.5:
                        continue

                text = text.strip() if isinstance(text, str) else ""
                is_self = (self_rowid is not None and sender_id == self_rowid)
                if text and len(text) >= 1 and len(text) <= 1000:
                    messages.append({
                        "local_id": _id,
                        "timestamp": ts or 0,
                        "is_self": is_self,
                        "content": text,
                    })
        except sqlite3.Error as e:
            logger.debug("手动查询消息表失败: %s", e)

        return messages

    def _build_conversations(self, messages: list[dict]) -> list[dict]:
        """将消息列表转为对话轮次"""
        conversations = []
        pending = None

        for msg in messages:
            # 过滤系统消息、过长消息
            if self._is_system_message(msg["content"]):
                continue
            if len(msg["content"]) > 1000 or len(msg["content"]) < 1:
                continue

            if pending is None:
                pending = msg
            elif pending["is_self"] != msg["is_self"]:
                # 不同人切换 → 形成一轮对话
                if pending["is_self"]:
                    # 自己先发 → pending是user, msg是reply
                    conversations.append({
                        "user": pending["content"],
                        "reply": msg["content"],
                        "timestamp": pending["timestamp"],
                        "is_self": True,
                        "source": "decrypt",
                    })
                else:
                    # 对方先发 → pending是user, msg是reply
                    conversations.append({
                        "user": pending["content"],
                        "reply": msg["content"],
                        "timestamp": pending["timestamp"],
                        "is_self": False,
                        "source": "decrypt",
                    })
                pending = None
            else:
                # 同一人连续发 → 保留最新的
                pending = msg

        return conversations

    def _is_system_message(self, content: str) -> bool:
        """判断是否为系统消息"""
        patterns = [
            r"你已添加了", r"以上是打招呼", r"邀请你加入", r"已退出群聊",
            r"开启了朋友验证", r"消息已发出，但被对方拒收", r"撤回了一条消息",
            r"\[链接\]", r"<msg", r"<sysmsg",
        ]
        return any(re.search(p, content) for p in patterns)

    def _date_to_ts(self, date_str: str, end_of_day: bool = False) -> int:
        """日期字符串 → 时间戳"""
        dt = datetime.strptime(date_str, "%Y-%m-%d")  # noqa: DTZ007
        if end_of_day:
            dt = dt.replace(hour=23, minute=59, second=59)
        return int(dt.timestamp())
