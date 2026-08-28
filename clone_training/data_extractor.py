"""
数据提取器 — 从已导出的聊天记录文件中提取对话

支持 1 种数据来源（已剥离微信本地解密）:
1. 手动导出的 txt/csv/json 聊天记录（云端可用）

输出格式：统一 JSON 对话列表
    [{"user": "xxx", "reply": "yyy", "timestamp": 123456789, "is_self": true/false}, ...]

剥离历史（2026-08-27）:
- 删除 WeChatFerry (RPC) 提取 — 需本机微信进程，云端不可能
- 删除 WeChatMsg SQLite 提取 — 需本机已解密数据库
- 删除 wechat-decrypt 提取 — 需本机微信内存密钥
- 全部云端可用的提取路径：仅保留文件导入
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
    """从已导出的聊天记录文件提取对话（云端可用版本）"""

    def __init__(self, data_dir: str = "./data/clone_src", single_combine_time_window: int = 2):
        """
        Args:
            data_dir: 数据目录
            single_combine_time_window: 同一人连续多条时，在此分钟数内合并（默认 2 分钟）
        """
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.single_combine_time_window = single_combine_time_window

    # ── 来源: 手动导出文件（txt/csv/json） ──────────────

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
                ts = int(datetime.strptime(  # noqa: DTZ007
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
        dt = datetime.strptime(date_str, "%Y-%m-%d")  # noqa: DTZ007
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
