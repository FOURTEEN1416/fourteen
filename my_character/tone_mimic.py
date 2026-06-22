"""
语气模仿器 — 从历史聊天记录学习说话风格

核心流程：
1. 将历史对话向量化存入 ChromaDB
2. 收到新消息时检索最相似的对话作为 few-shot 示例
3. 结合情感状态和风格画像生成语气 prompt

依赖: chromadb (pip install chromadb)
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("tone_mimic")


@contextlib.contextmanager
def _silence_stdout():
    """屏蔽 onnxruntime C++ 扩展 import 时的 EP Error 噪声（缺 TensorRT 库）。
    onnxruntime C++ 通过 std::cerr (fd 2) 打印 EP Error，必须同时重定向 fd 1+2。
    """
    devnull_path = "nul" if os.name == "nt" else os.devnull
    import sys
    sys.stdout.flush()
    sys.stderr.flush()
    saved_fd1 = os.dup(1)
    saved_fd2 = os.dup(2)
    d1 = os.open(devnull_path, os.O_WRONLY)
    d2 = os.open(devnull_path, os.O_WRONLY)
    os.dup2(d1, 1)
    os.dup2(d2, 2)
    os.close(d1)
    os.close(d2)
    saved_stdout_file = sys.stdout
    saved_stderr_file = sys.stderr
    try:
        with contextlib.ExitStack() as stack:
            devnull_out = stack.enter_context(open(devnull_path, "w"))
            devnull_err = stack.enter_context(open(devnull_path, "w"))
            sys.stdout = devnull_out
            sys.stderr = devnull_err
            yield
    finally:
        sys.stdout = saved_stdout_file
        sys.stderr = saved_stderr_file
        if saved_fd1 is not None:
            os.dup2(saved_fd1, 1)
            os.close(saved_fd1)
        if saved_fd2 is not None:
            os.dup2(saved_fd2, 2)
            os.close(saved_fd2)

# ChromaDB 延迟导入，避免无环境时崩溃
try:
    import chromadb
    from chromadb.api.models.Collection import Collection
    from chromadb.utils import embedding_functions
    HAS_CHROMADB = True
except ImportError:
    HAS_CHROMADB = False
    Collection = Any  # type: ignore


@dataclass
class StyleProfile:
    """语气风格画像"""
    formality: float = 0.3          # 0~1, 0=随意 1=正式
    emotion_expr: float = 0.8       # 0~1, 情绪表达程度
    emoji_freq: float = 0.6         # 0~1, 表情使用频率
    sentence_len: str = "short"     # short/medium/long
    pet_name: str = ""              # 对用户的昵称
    self_name: str = ""             # 自称
    catch_phrases: list[str] = field(default_factory=list)
    topics_like: list[str] = field(default_factory=list)
    topics_dislike: list[str] = field(default_factory=list)
    punctuation_style: str = "normal"  # normal/exclamation/dot/ellipsis
    first_person: str = "我"        # 第一人称


class ToneMimic:
    """
    语气模仿系统

    Architecture:
        Character Prompt (基底) + Style Profile (风格画像)
        + RAG (上下文检索) + Few-shot (示例选择)

    完整 prompt 组装由 PersonaEngine 完成。
    """

    def __init__(
        self,
        chroma_path: str = "./data/chroma_db",
        collection_name: str = "chat_style",
    ):
        self.chroma_path = os.path.abspath(chroma_path)
        self.collection_name = collection_name
        self._collection: Collection | None = None
        self._client: Any = None

        # 风格画像
        self.style_profile = StyleProfile()

        if HAS_CHROMADB:
            self._init_chromadb()
        else:
            logger.warning("chromadb not installed, ToneMimic runs in fallback mode")

    def _init_chromadb(self) -> None:
        """初始化 ChromaDB 连接"""
        try:
            os.makedirs(self.chroma_path, exist_ok=True)
            self._client = chromadb.PersistentClient(path=self.chroma_path)
            # onnxruntime 首次加载会 printf "EP Error nvinfer_10.dll missing" 到 stdout
            with _silence_stdout():
                ef = embedding_functions.DefaultEmbeddingFunction()
            self._collection = self._client.get_or_create_collection(
                name=self.collection_name,
                embedding_function=ef,
            )
        except BaseException as e:  # noqa: BLE001
            # ChromaDB Rust 绑定偶发 panic（如 nvinfer 缺失 / sqlite 越界），
            # pyo3 PanicException 继承自 BaseException 而非 Exception，必须兜底。
            # 降级为无库模式：功能受限但 PersonaEngine 仍可构建。
            logger.warning("ChromaDB init failed, ToneMimic running in fallback mode: %s", e)
            self._client = None
            self._collection = None
            return
        logger.info("ChromaDB initialized at %s", self.chroma_path)

    # ── 核心接口 ──────────────────────────────────────────────

    def add_conversation(self, user_msg: str, reply: str, metadata: dict | None = None) -> None:
        """
        添加一轮对话到风格库

        Args:
            user_msg: 用户消息
            reply: 回复内容
            metadata: 额外元数据（情感标签、时间戳等）
        """
        if self._collection is None:
            return

        doc = f"User: {user_msg}\nYou: {reply}"
        meta = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "type": "chat",
            "user_msg_len": len(user_msg),
            "reply_len": len(reply),
        }
        if metadata:
            meta.update(metadata)

        doc_id = f"chat_{hashlib.md5(doc.encode()).hexdigest()[:12]}"

        try:
            self._collection.add(
                documents=[doc],
                metadatas=[meta],  # type: ignore[list-item]
                ids=[doc_id],
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to add conversation: %s", e)

    def retrieve_style_examples(self, query: str, top_k: int = 3) -> list[str]:
        """
        检索与当前消息最相似的历史回复

        Args:
            query: 用户输入消息
            top_k: 返回示例数量

        Returns:
            历史对话文本列表（格式: "User: ...\nYou: ..."）
        """
        if self._collection is None:
            return []

        try:
            results = self._collection.query(
                query_texts=[query],
                n_results=top_k,
            )
            if results and results.get("documents"):
                return results["documents"][0]  # type: ignore[index]
        except Exception as e:  # noqa: BLE001
            logger.warning("Style retrieval failed: %s", e)

        return []

    def get_style_prompt(self) -> str:
        """
        生成语气风格描述段（用于注入 system prompt）

        Returns:
            风格描述文本
        """
        p = self.style_profile
        parts = ["[说话风格参考]"]

        # 正式程度
        if p.formality < 0.3:
            parts.append("- 语气：非常随意自然，像日常聊天")
        elif p.formality < 0.6:
            parts.append("- 语气：适度放松，偶尔带点俏皮")
        else:
            parts.append("- 语气：偏向正式，但有温度")

        # 情感表达
        if p.emotion_expr > 0.6:
            parts.append("- 情感：丰富外露，不掩饰情绪")
        elif p.emotion_expr > 0.3:
            parts.append("- 情感：适度表达，看场合")
        else:
            parts.append("- 情感：内敛含蓄")

        # 表情使用
        if p.emoji_freq > 0.7:
            parts.append("- 表情：经常使用~和ww，表达语气")
        elif p.emoji_freq > 0.3:
            parts.append("- 表情：偶尔带个～或w")

        # 称呼
        if p.pet_name:
            parts.append(f"- 称呼对方：{p.pet_name}")
        if p.self_name:
            parts.append(f"- 自称：{p.self_name}")

        # 口头禅
        if p.catch_phrases:
            phrases = "、".join(p.catch_phrases[:3])
            parts.append(f"- 口头禅：{phrases}")

        # 句子长度
        if p.sentence_len == "short":
            parts.append("- 句子：偏短，干脆利落")
        elif p.sentence_len == "long":
            parts.append("- 句子：偏长，娓娓道来")

        return "\n".join(parts)

    def analyze_style(self, chat_history: list[dict[str, str]]) -> StyleProfile:
        """
        用规则分析聊天记录的语气特征
        实际使用时可替换为 LLM 分析

        Args:
            chat_history: [{"role": "user"/"assistant", "content": str}, ...]

        Returns:
            分析后的 StyleProfile
        """
        if not chat_history:
            return self.style_profile

        replies = [m["content"] for m in chat_history if m.get("role") == "assistant"]

        if not replies:
            return self.style_profile

        # 统计指标
        total_replies = len(replies)
        total_chars = sum(len(r) for r in replies)
        emoji_count = sum(1 for r in replies for c in r if c in "～~❤️😊😝🥰✨😘😳🤔💕🥺😏😭😤💪😌")
        exclaim_count = sum(1 for r in replies if r.endswith("！") or r.endswith("!"))
        question_count = sum(1 for r in replies if r.endswith("？") or r.endswith("?"))

        # 句子长度
        avg_len = total_chars / total_replies if total_replies else 0
        if avg_len < 20:
            self.style_profile.sentence_len = "short"
        elif avg_len < 50:
            self.style_profile.sentence_len = "medium"
        else:
            self.style_profile.sentence_len = "long"

        # 表情频率
        self.style_profile.emoji_freq = min(1.0, emoji_count / max(1, total_replies) / 3)

        # 标点风格
        if exclaim_count > total_replies * 0.5:
            self.style_profile.punctuation_style = "exclamation"
        elif question_count > total_replies * 0.3:
            self.style_profile.punctuation_style = "question"

        # 提取常用词作为口头禅（简单版）
        all_text = " ".join(replies)
        words = re.findall(r'[\u4e00-\u9fff]{2,4}', all_text)
        if words:
            word_freq = Counter(words)
            common = [w for w, c in word_freq.most_common(5) if c >= 2]
            self.style_profile.catch_phrases = common[:3]

        logger.info("Style analysis complete: %d replies, avg_len=%.1f", total_replies, avg_len)
        return self.style_profile

    def update_style_profile(self, updates: dict) -> None:
        """手动更新风格画像"""
        for key, value in updates.items():
            if hasattr(self.style_profile, key):
                setattr(self.style_profile, key, value)

    def to_dict(self) -> dict:
        """导出风格画像为字典"""
        return asdict(self.style_profile)

    def from_dict(self, data: dict) -> None:
        """从字典加载风格画像"""
        for key, value in data.items():
            if hasattr(self.style_profile, key):
                setattr(self.style_profile, key, value)

    def health_check(self) -> dict:
        """健康检查"""
        return {
            "chromadb_available": HAS_CHROMADB,
            "chromadb_connected": self._collection is not None,
            "collection": self.collection_name,
            "style_profile": self.to_dict(),
        }
