"""
数据提取器 — 从 WeChat 数据库 / 导出的聊天记录中提取对话

支持 4 种数据来源：
1. WeChatFerry（通过 RPC 实时查询微信进程内数据库）
2. WeChatMsg (LC044) 导出格式（本地 SQLite 数据库 + 解密）
3. 手动导出的 txt/csv/json 聊天记录
4. wechat-decrypt 微信 4.x 数据库解密提取（新增）

输出格式：统一 JSON 对话列表
    [{"user": "xxx", "reply": "yyy", "timestamp": 123456789, "is_self": true/false}, ...]
"""

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("clone.extractor")


class DataExtractor:
    """从多种来源提取 WeChat 聊天对话"""

    def __init__(self, data_dir: str = "./data/clone_src", single_combine_time_window: int = 2):
        """
        Args:
            data_dir: 数据目录
            single_combine_time_window: 同一人连续多条时，在此分钟数内合并（默认 2 分钟）
        """
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.single_combine_time_window = single_combine_time_window

    # ── 来源1: WeChatFerry (RPC) ─────────────────────────

    def extract_from_wcf(
        self,
        target_wxid: str,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """
        通过 WeChatFerry RPC 查询微信进程内数据库

        Args:
            target_wxid: 目标联系人 wxid
            limit: 最多拉取消息数
            offset: 起始偏移

        Returns:
            清洗后的对话列表
        """
        try:
            import wcf  # type: ignore
        except ImportError:
            logger.error("WeChatFerry 未安装: pip install wcf")
            return self._empty_result("wcf_not_installed")

        try:
            client = wcf.WeChatFerry()
            raw_msgs = client.query_msg(target_wxid, limit=limit, offset=offset)
            if not raw_msgs:
                logger.warning("WCF 未返回消息（微信进程可能未运行或目标不存在）")
                return self._empty_result("no_messages")

            conversations = self._process_wcf_messages(raw_msgs)
            logger.info("WCF 提取: %d 条消息 → %d 轮对话",
                        len(raw_msgs), len(conversations))
            return conversations

        except Exception as e:
            logger.exception("WCF 提取失败: %s", e)
            return self._empty_result(str(e))

    def _process_wcf_messages(
        self, raw_msgs: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """处理 WCF 原始消息 → 轮次对话"""
        text_msgs = [
            m for m in raw_msgs
            if m.get("type") == 1  # 文本消息
            and m.get("content")
            and len(m["content"].strip()) > 0
        ]

        conversations = []
        for i in range(0, len(text_msgs) - 1, 2):
            user_msg = text_msgs[i]
            reply = text_msgs[i + 1]

            conversations.append({
                "user": user_msg["content"].strip(),
                "reply": reply["content"].strip(),
                "timestamp": user_msg.get("timestamp", 0),
                "is_self": user_msg.get("is_self", False),
                "source": "wcf",
            })

        return conversations

    # ── 来源2: WeChatMsg (LC044) 导出格式 ──────────────────

    def extract_from_wechatmsg(
        self,
        db_path: str,
        target_name: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        从 WeChatMsg 导出/解密的 SQLite 数据库提取聊天记录

        Args:
            db_path: 已解密的 MSG.db 路径
            target_name: 目标联系人备注名（None = 取所有）
            date_from: 起始日期 "2024-01-01"
            date_to: 截止日期 "2024-12-31"

        Returns:
            清洗后的对话列表
        """
        import sqlite3

        if not os.path.exists(db_path):
            logger.error("数据库不存在: %s", db_path)
            return self._empty_result("db_not_found")

        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row

            # 构建 SQL 查询
            query = "SELECT * FROM MSG WHERE Type = 1 AND IsSender IS NOT NULL"
            params = []

            if target_name:
                # 根据备注名查 Talker
                query += " AND StrTalker IN (SELECT UsrName FROM Contact WHERE Remark=?)"
                params.append(target_name)

            if date_from:
                query += " AND CreateTime >= ?"
                params.append(self._date_to_timestamp(date_from))

            if date_to:
                query += " AND CreateTime <= ?"
                params.append(self._date_to_timestamp(date_to, end_of_day=True))

            query += " ORDER BY CreateTime ASC LIMIT 5000"

            cursor = conn.execute(query, params)
            rows = cursor.fetchall()
            conn.close()

            if not rows:
                logger.warning("WeChatMsg 数据库无匹配消息")
                return self._empty_result("no_messages")

            conversations = self._build_conversations_from_rows(rows)
            logger.info("WeChatMsg 提取: %d 行 → %d 轮对话",
                        len(rows), len(conversations))
            return conversations

        except Exception as e:
            logger.exception("WeChatMsg 提取失败: %s", e)
            return self._empty_result(str(e))

    def _build_conversations_from_rows(
        self, rows: list[Any]
    ) -> list[dict[str, Any]]:
        """从 SQLite 查询结果构建对话列表"""
        conversations = []
        pending_msg = None

        for row in rows:
            msg_data = {
                "content": row.get("StrContent", ""),
                "timestamp": row.get("CreateTime", 0),
                "is_self": bool(row["IsSender"]) if "IsSender" in row else False,
                "talker": row.get("StrTalker", ""),
            }

            if not msg_data["content"]:
                continue

            content = msg_data["content"].strip()

            # 过滤系统消息、过长消息
            if self._is_system_message(content):
                continue
            if len(content) > 500 or len(content) < 1:
                continue

            if pending_msg is None:
                pending_msg = msg_data
            elif pending_msg["is_self"] != msg_data["is_self"]:
                # 不同人之间的对话轮次
                if pending_msg["is_self"]:
                    conversations.append({
                        "user": pending_msg["content"],
                        "reply": msg_data["content"],
                        "timestamp": pending_msg["timestamp"],
                        "is_self": pending_msg["is_self"],
                        "source": "wechatmsg",
                    })
                else:
                    conversations.append({
                        "user": msg_data["content"],
                        "reply": pending_msg["content"],
                        "timestamp": msg_data["timestamp"],
                        "is_self": msg_data["is_self"],
                        "source": "wechatmsg",
                    })
                pending_msg = None
            else:
                # 同一人连续发消息 — 按时间窗口决定合并或替换
                time_diff = abs(msg_data["timestamp"] - pending_msg["timestamp"])
                window_seconds = self.single_combine_time_window * 60

                if time_diff <= window_seconds:
                    # 时间窗口内 → 合并消息，保留说话者身份
                    pending_msg["content"] = pending_msg["content"] + "，" + msg_data["content"]
                    pending_msg["timestamp"] = msg_data["timestamp"]
                    logger.debug("合并同人连续消息 (时间差 %.0f 秒)", time_diff)
                else:
                    # 超过时间窗口 → 保留最新一条（视为新会话开始）
                    pending_msg = msg_data
                    logger.debug("同人消息超时窗口 (%.0f > %d 秒), 保留最新", time_diff, window_seconds)

        return conversations

    # ── 来源3: 手动导出文件（txt/csv/json） ──────────────

    def extract_from_export(
        self, file_path: str, format: str = "auto",
    ) -> list[dict[str, Any]]:
        """
        从手动导出的聊天记录文件提取

        Args:
            file_path: 文件路径
            format: "auto" | "txt" | "csv" | "json"

        Returns:
            清洗后的对话列表
        """
        if not os.path.exists(file_path):
            return self._empty_result("file_not_found")

        file_format = format
        if format == "auto":
            ext = Path(file_path).suffix.lower()
            file_format = {"txt": "txt", "csv": "csv", "json": "json"}.get(ext, "txt")

        content = Path(file_path).read_text(encoding="utf-8")

        if file_format == "json":
            return self._extract_from_json(content)
        elif file_format == "csv":
            return self._extract_from_csv(content)
        else:
            return self._extract_from_txt(content)

    def _extract_from_json(self, content: str) -> list[dict[str, Any]]:
        """从 JSON 文件提取（ChatGPT/微信导出格式）"""
        try:
            data = json.loads(content)
            if isinstance(data, list):
                return data
            elif isinstance(data, dict):
                # 微信导出格式: {"messages": [...]}
                msgs = data.get("messages", [])
                return self._process_raw_messages(msgs)
        except json.JSONDecodeError:
            pass
        return []

    def _extract_from_csv(self, content: str) -> list[dict[str, Any]]:
        """从 CSV 提取"""
        import csv
        import io
        reader = csv.DictReader(io.StringIO(content))
        msgs = [dict(row) for row in reader]
        return self._process_raw_messages(msgs)

    def _extract_from_txt(self, content: str) -> list[dict[str, Any]]:
        """从 TXT 文件提取（微信聊天记录导出格式）

        支持格式：
        格式A: "2026-05-16 21:30 名字: 消息内容" （单行）
        格式B: "2026-05-16 21:30 名字\n消息内容" （双行）
        格式C: "名字 2026-05-16 21:30\n消息内容" （变体）
        """
        lines = [line.strip() for line in content.split("\n") if line.strip()]
        conversations = []

        # 模式: 日期 时间 说话人: 消息
        pattern_a = re.compile(
            r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})\s+(\d{1,2}:\d{2}(?::\d{2})?)\s+"
            r"([^\n:：]{1,30})[：:]\s*(.+)"
        )

        # 模式B: 日期 时间 说话人\n消息
        pattern_b = re.compile(
            r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})\s+(\d{1,2}:\d{2}(?::\d{2})?)\s+([^\n]+)\n([^\n]+)",
            re.MULTILINE,
        )

        # 先尝试格式 A（单行，大多数导出工具使用此格式）
        parsed = []
        for line in lines:
            m = pattern_a.match(line)
            if m:
                date_str, time_str, speaker, message = m.groups()
                parsed.append({
                    "date": date_str,
                    "time": time_str,
                    "speaker": speaker.strip(),
                    "message": message.strip(),
                })

        # 如果 A 没匹配到，尝试 B
        if not parsed:
            for m in pattern_b.finditer(content):
                date_str, time_str, speaker, message = m.groups()
                parsed.append({
                    "date": date_str,
                    "time": time_str,
                    "speaker": speaker.strip(),
                    "message": message.strip(),
                })

        if not parsed:
            logger.warning("TXT 格式不匹配，尝试按时间戳行+\n消息行解析")
            # 兜底：按行解析，含时间戳的行 = 说话人+消息
            ts_pattern = re.compile(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})\s+(\d{1,2}:\d{2}(?::\d{2})?)")
            i = 0
            while i < len(lines):
                line = lines[i]
                m = ts_pattern.match(line)
                if m:
                    # 提取说话人和消息
                    rest = line[m.end():].strip()
                    if ":" in rest or "：" in rest:
                        sep = "：" if "：" in rest else ":"
                        speaker, _, message = rest.partition(sep)
                        parsed.append({
                            "date": m.group(1),
                            "time": m.group(2),
                            "speaker": speaker.strip(),
                            "message": message.strip(),
                        })
                    else:
                        # 说话人可能在下一行
                        if i + 1 < len(lines) and not ts_pattern.match(lines[i + 1]):
                            parsed.append({
                                "date": m.group(1),
                                "time": m.group(2),
                                "speaker": rest,
                                "message": lines[i + 1],
                            })
                            i += 1
                i += 1

        # 转为对话对（相邻两个消息 = 一轮对话）
        for i in range(0, len(parsed) - 1, 2):
            if i + 1 >= len(parsed):
                break
            user_entry = parsed[i]
            reply_entry = parsed[i + 1]

            is_self = any(
                kw in user_entry["speaker"]
                for kw in ["我", "自己", "Self", "self", "本人"]
            )

            try:
                ts_str = f"{user_entry['date']} {user_entry['time']}"
                ts = int(datetime.strptime(
                    ts_str.replace("/", "-"),
                    "%Y-%m-%d %H:%M:%S" if ":" in user_entry["time"].split(":")[-1] and len(user_entry["time"].split(":")) > 2
                    else "%Y-%m-%d %H:%M"
                ).timestamp())
            except ValueError:
                ts = 0

            conversations.append({
                "user": user_entry["message"],
                "reply": reply_entry["message"],
                "timestamp": ts,
                "is_self": is_self,
                "source": "txt_export",
            })

        logger.info("TXT 提取: %d 行 → %d 条消息 → %d 轮对话",
                    len(lines), len(parsed), len(conversations))
        return conversations

    # ── 来源4: wechat-decrypt ─────────────────────────────

    def extract_from_decrypt(
        self,
        target: str,
        max_messages: int = 5000,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        通过 wechat-decrypt（微信 4.x 数据库解密）提取聊天记录

        自动完成：密钥提取 → 数据库解密 → 消息读取 → 对话构建

        Args:
            target: 目标联系人 wxid / 备注名 / 昵称
            max_messages: 最大消息数
            date_from: 起始日期 "2024-01-01"
            date_to: 截止日期 "2024-12-31"

        Returns:
            清洗后的对话列表
        """
        try:
            from clone_training.decrypt_source import DecryptSource
        except ImportError:
            logger.error("decrypt_source 模块不可用，请确保 third_party/wechat-decrypt 已安装")
            return self._empty_result("decrypt_source_not_found")

        try:
            ds = DecryptSource()
            conversations = ds.extract(
                target=target,
                max_messages=max_messages,
                date_from=date_from,
                date_to=date_to,
            )
            logger.info("wechat-decrypt 提取: %d 轮对话 (目标: %s)",
                        len(conversations), target)
            return conversations

        except Exception as e:
            logger.exception("wechat-decrypt 提取失败: %s", e)
            return self._empty_result(str(e))

    # ── 工具方法 ────────────────────────────────────────

    def _process_raw_messages(
        self, msgs: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """将原始消息列表转为轮次对话"""
        text_msgs = [
            m for m in msgs
            if m.get("content") or m.get("text") or m.get("body")
        ]
        conversations = []

        for i in range(0, len(text_msgs) - 1, 2):
            user_msg = text_msgs[i]
            reply = text_msgs[i + 1]
            conversations.append({
                "user": (
                    user_msg.get("content") or
                    user_msg.get("text") or
                    user_msg.get("body", "")
                ).strip(),
                "reply": (
                    reply.get("content") or
                    reply.get("text") or
                    reply.get("body", "")
                ).strip(),
                "timestamp": user_msg.get("timestamp", 0),
                "is_self": user_msg.get("is_self", False),
                "source": user_msg.get("source", "json_export"),
            })

        return conversations

    def _is_system_message(self, content: str) -> bool:
        """判断是否为系统消息"""
        sys_patterns = [
            r"你已添加了", r"以上是打招呼", r"邀请你加入", r"已退出群聊",
            r"开启了朋友验证", r"消息已发出，但被对方拒收", r"撤回了一条消息",
            r"\[链接\]", r"<msg>", r"<sysmsg",
        ]
        return any(re.search(p, content) for p in sys_patterns)

    def _date_to_timestamp(self, date_str: str, end_of_day: bool = False) -> int:
        """日期字符串 → 时间戳"""
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        if end_of_day:
            dt = dt.replace(hour=23, minute=59, second=59)
        return int(dt.timestamp())

    def _empty_result(self, reason: str) -> list[dict[str, Any]]:
        logger.warning("提取失败或无数据: %s", reason)
        return []

    def save_to_json(
        self, conversations: list[dict[str, Any]], filename: str
    ) -> str:
        """保存为 JSON 文件"""
        path = self.data_dir / f"{filename}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(conversations, f, ensure_ascii=False, indent=2)
        logger.info("已保存 %d 条对话到 %s", len(conversations), path)
        return str(path)
