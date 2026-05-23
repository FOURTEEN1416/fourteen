"""
数据集构建器 — 将聊天对话转为 LoRA 微调训练集

输出格式：
1. HuggingFace Dataset (jsonl) — 标准对话格式
2. Alpaca 格式 — instruction/input/output
3. ChatML 格式 — 多轮对话

训练数据结构:
    {
        "messages": [
            {"role": "system", "content": "你是{名字}，..."},
            {"role": "user", "content": "用户消息"},
            {"role": "assistant", "content": "被克隆人的回复"}
        ],
        "metadata": {
            "style_tags": ["短句", "表情多", "语气软"],
            "emotion": "正面",
        }
    }
"""

import json
import logging
import random
from pathlib import Path
from typing import Any, Dict, List

from .style_analyzer import StyleAnalyzer, StyleProfile

logger = logging.getLogger("clone.builder")


class DatasetBuilder:
    """构建训练数据集"""

    SYSTEM_PROMPT_TEMPLATE = """你是{name}，以下是你需要严格模仿的说话风格：

{style_description}

请按照以上风格回复用户的消息。语气、句式、标点、表情使用都要与风格描述一致。
你的回复应该像是{name}本人发出来的，而不是一个AI助手。"""

    def __init__(self, output_dir: str = "./data/training"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.analyzer = StyleAnalyzer()

    def build(
        self,
        conversations: List[Dict[str, Any]],
        name: str = "目标人物",
        split_ratio: float = 0.15,
        max_samples: int = 2000,
    ) -> Dict[str, Any]:
        """
        构建完整训练数据集

        Args:
            conversations: 对话列表
            name: 被克隆人的名字
            split_ratio: 验证集比例
            max_samples: 最大样本数

        Returns:
            {"train": train_path, "val": val_path, "style_report": report_path}
        """
        logger.info("正在构建训练数据集...")

        # 1. 风格分析
        profile = self.analyzer.analyze(conversations)
        style_prompt = profile.to_style_prompt()
        report_path = str(self.output_dir / f"{name}_style_report.json")
        self.analyzer.save_report(profile, report_path)

        # 2. 构建训练样本
        system_prompt = self.SYSTEM_PROMPT_TEMPLATE.format(
            name=name,
            style_description=style_prompt,
        )

        samples = []
        for conv in conversations[:max_samples]:
            if not conv.get("user") or not conv.get("reply"):
                continue

            sample = {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": conv["user"].strip()},
                    {"role": "assistant", "content": conv["reply"].strip()},
                ],
                "metadata": {
                    "style_tags": self._extract_style_tags(profile),
                    "emotion": self._classify_emotion(conv.get("reply", "")),
                },
            }
            samples.append(sample)

        if len(samples) < 5:
            logger.error("样本数不足 (%d), 至少需要 5 条", len(samples))
            return {}

        # 3. 打乱并分割
        random.shuffle(samples)
        split_idx = int(len(samples) * (1 - split_ratio))
        train_samples = samples[:split_idx]
        val_samples = samples[split_idx:]

        # 4. 保存
        train_path = str(self.output_dir / f"{name}_train.jsonl")
        val_path = str(self.output_dir / f"{name}_val.jsonl")

        self._save_jsonl(train_samples, train_path)
        self._save_jsonl(val_samples, val_path)

        # 5. 同时输出 Alpaca 格式
        alpaca_path = str(self.output_dir / f"{name}_alpaca.json")
        self._save_alpaca(train_samples, alpaca_path, system_prompt)

        logger.info(
            "数据集构建完成: train=%d, val=%d, report=%s",
            len(train_samples), len(val_samples), report_path,
        )

        return {
            "train": train_path,
            "val": val_path,
            "report": report_path,
            "alpaca": alpaca_path,
            "n_train": len(train_samples),
            "n_val": len(val_samples),
        }

    def build_chatml(
        self,
        conversations: List[Dict[str, Any]],
        name: str = "目标人物",
        window_size: int = 4,
    ) -> str:
        """
        构建 ChatML 格式多轮对话训练集

        Args:
            conversations: 对话列表
            name: 被克隆人名字
            window_size: 每段对话的轮次数

        Returns:
            输出文件路径
        """
        style_prompt = self.analyzer.analyze(conversations).to_style_prompt()
        system_msg = self.SYSTEM_PROMPT_TEMPLATE.format(
            name=name, style_description=style_prompt
        )

        all_chunks = []
        for i in range(0, len(conversations) - window_size + 1, window_size):
            chunk = conversations[i:i+window_size]
            messages = [{"role": "system", "content": system_msg}]
            for conv in chunk:
                if conv.get("user") and conv.get("reply"):
                    messages.append({"role": "user", "content": conv["user"].strip()})
                    messages.append({"role": "assistant", "content": conv["reply"].strip()})
            all_chunks.append({"messages": messages})

        path = str(self.output_dir / f"{name}_chatml.jsonl")
        self._save_jsonl(all_chunks, path)
        logger.info("ChatML 数据集: %d 段对话 → %s", len(all_chunks), path)
        return path

    def _save_jsonl(self, data: List[dict], path: str) -> None:
        """保存为 JSONL 格式"""
        with open(path, "w", encoding="utf-8") as f:
            for item in data:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

    def _save_alpaca(
        self, samples: List[dict], path: str, system_prompt: str,
    ) -> None:
        """保存为 Alpaca 格式"""
        alpaca_data = []
        for s in samples:
            user_msg = ""
            asst_msg = ""
            for msg in s["messages"]:
                if msg["role"] == "user":
                    user_msg = msg["content"]
                elif msg["role"] == "assistant":
                    asst_msg = msg["content"]

            alpaca_data.append({
                "instruction": system_prompt,
                "input": user_msg,
                "output": asst_msg,
            })

        with open(path, "w", encoding="utf-8") as f:
            json.dump(alpaca_data, f, ensure_ascii=False, indent=2)

        logger.info("Alpaca 格式: %d 条 → %s", len(alpaca_data), path)

    def _extract_style_tags(self, profile: StyleProfile) -> List[str]:
        """从风格档案中提取简短标签"""
        tags = []

        # 句长
        dominant = max(  # type: ignore
            profile.sentence_length_dist,
            key=profile.sentence_length_dist.get,  # type: ignore
            default="中句",
        )
        if "短句" in dominant:
            tags.append("短句")
        elif "长句" in dominant:
            tags.append("长句")

        # 表情
        if profile.emoji_freq > 0.2:
            tags.append("表情多")
        if profile.kaomoji_freq > 0.05:
            tags.append("颜文字")

        # 情绪
        if profile.emotion_dist:
            top_emo = max(profile.emotion_dist, key=profile.emotion_dist.get)  # type: ignore
            if top_emo == "正面":
                tags.append("语气活泼")
            elif top_emo == "负面":
                tags.append("语气低沉")

        # 网络用语
        if profile.slang_freq > 0.05:
            tags.append("网络用语")

        return tags

    def _classify_emotion(self, text: str) -> str:
        """简单情绪分类"""
        pos = sum(1 for kw in self.analyzer.POSITIVE_EMO if kw in text)
        neg = sum(1 for kw in self.analyzer.NEGATIVE_EMO if kw in text)
        if pos > neg:
            return "正面"
        elif neg > pos:
            return "负面"
        return "中性"

    def get_dataset_stats(self, conversations: List[Dict[str, Any]]) -> Dict[str, Any]:
        """获取数据集统计信息（预训练前检查）"""
        if not conversations:
            return {"error": "无数据"}

        lengths = [len(c["reply"]) for c in conversations if c.get("reply")]
        if not lengths:
            return {"error": "无有效回复"}

        return {
            "total_turns": len(conversations),
            "avg_reply_length": sum(lengths) / len(lengths),
            "min_length": min(lengths),
            "max_length": max(lengths),
            "length_std": (
                sum((ln - sum(lengths)/len(lengths))**2 for ln in lengths) / len(lengths)
            ) ** 0.5,
            "estimated_tokens": sum(lengths) * 1.5,
            "diversity": len(set(c["reply"][:10] for c in conversations if c.get("reply"))) / len(conversations),
        }
