"""
WeClone 适配器 — 完整的风格克隆管线总控

整合三大能力：
1. 数据提取（WeChat 聊天记录 → 清洗后的对话 JSON）
2. 风格分析（12 维风格特征 → 风格档案 + 提示词）
3. 训练管道（对话 → 训练集 → LoRA 微调 → 部署）

用法:
    from weclone_adapter import WeCloneAdapter

    adapter = WeCloneAdapter(data_dir="./data/clone")
    adapter.clone("目标人的wxid")
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from clone_training import (
    DataExtractor,
    DatasetBuilder,
    LoRATrainer,
    StyleAnalyzer,
)
from safety.pii_anonymizer import PIIAnonymizer

logger = logging.getLogger("weclone.adapter")


class WeCloneAdapter:
    """WeClone 风格克隆总控"""

    def __init__(
        self,
        data_dir: str = "./data/clone",
        output_dir: str = "./data/training",
        base_model: str = "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B",
    ):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.extractor = DataExtractor(str(self.data_dir / "src"))
        self.analyzer = StyleAnalyzer()
        self.builder = DatasetBuilder(str(self.data_dir / "dataset"))
        self.trainer = LoRATrainer(
            base_model=base_model,
            output_dir=str(self.data_dir / "lora_output"),
        )

    def clone(
        self,
        target: str,
        source: str = "wcf",
        name: str = "",
        max_samples: int = 2000,
        do_train: bool = True,
        lora_r: int = 16,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        执行完整克隆流程

        Args:
            target: 目标（wxid / 数据库路径 / 导出文件路径）
            source: 数据来源 ("wcf" | "wechatmsg" | "txt" | "json" | "csv")
            name: 目标人物名字（用于输出文件命名）
            max_samples: 最大样本数
            do_train: 是否执行 LoRA 微调
            lora_r: LoRA rank
            **kwargs: 传递给具体提取器的额外参数

        Returns:
            {
                "style_profile": StyleProfile.to_dict(),
                "dataset": {"train": path, "val": path, ...},
                "training": {"output_dir": path, ...} | None,
                "lora_path": str | None,
            }
        """
        result = {}

        # ── Step 1: 提取数据 ──
        logger.info("━━━ Step 1/4: 提取聊天记录 ━━━")
        conversations = self._extract(target, source, **kwargs)

        if not conversations:
            return {"error": "数据提取失败或无有效对话", "source": source, "target": target}

        result["extracted_turns"] = len(conversations)

        # 保存原始数据
        if not name:
            name = target.replace("/", "_").replace("\\", "_")[:30]
        raw_path = self.data_dir / f"{name}_raw.json"
        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump(conversations[:max_samples], f, ensure_ascii=False, indent=2)
        logger.info("  原始数据: %d 条 -> %s", len(conversations), raw_path)

        # ── Step 2: PII 匿名化 ──
        logger.info("━━━ Step 2/5: PII 匿名化 ━━━")
        pii = PIIAnonymizer(enabled=True)
        anonymized_count = 0
        for conv in conversations[:max_samples]:
            if conv.get("user"):
                cleaned_user, _ = pii.anonymize(conv["user"])
                if cleaned_user != conv["user"]:
                    anonymized_count += 1
                conv["user"] = cleaned_user
            if conv.get("reply"):
                cleaned_reply, _ = pii.anonymize(conv["reply"])
                if cleaned_reply != conv["reply"]:
                    anonymized_count += 1
                conv["reply"] = cleaned_reply
        logger.info("  PII 匿名化处理: %d 处个人信息被脱敏", anonymized_count)

        # ── Step 3: 风格分析 ──
        logger.info("━━━ Step 3/5: 分析说话风格 ━━━")
        profile = self.analyzer.analyze(conversations[:max_samples])
        result["style_profile"] = profile.to_dict()
        result["style_prompt"] = profile.to_style_prompt()
        result["uniqueness"] = profile.uniqueness_score

        # 保存风格报告
        report_path = self.data_dir / f"{name}_style_report.json"
        self.analyzer.save_report(profile, str(report_path))

        # ── Step 4: 构建训练集 ──
        logger.info("━━━ Step 4/5: 构建训练数据集 ━━━")
        dataset_result = self.builder.build(
            conversations[:max_samples],
            name=name,
        )
        result["dataset"] = dataset_result

        # ChatML 多轮对话格式输出
        try:
            chatml_path = self.builder.build_chatml(
                conversations[:max_samples],
                name=name,
                window_size=4,
            )
            result["dataset"]["chatml"] = chatml_path
            logger.info("  ChatML 多轮对话: %s", chatml_path)
        except Exception as e:
            logger.warning("ChatML 构建失败（不影响主流程）: %s", e)
            result["dataset"]["chatml"] = None

        # ── Step 5: LoRA 微调 ──
        if do_train and result["dataset"]:
            logger.info("━━━ Step 5/5: LoRA 微调训练 ━━━")
            train_result = self.trainer.train(
                train_path=result["dataset"]["train"],
                val_path=result["dataset"].get("val"),
                lora_r=lora_r,
                **kwargs,
            )
            result["training"] = train_result

            if train_result.get("status") == "success":
                result["lora_path"] = train_result.get("output_dir")
        else:
            logger.info("━━━ Step 5/5: 跳过训练 (do_train=False 或 数据集为空) ━━━")
            result["training"] = None

        # ── 注入到小暖的语气模仿器 ──
        logger.info("━━━ 注入风格到 ToneMimic ━━━")
        injected = self._inject_to_tone_mimic(conversations[:max_samples])
        result["injected_to_tone_mimic"] = injected

        return result

    def quick_clone(
        self, target: str, name: str = "",
    ) -> Dict[str, Any]:
        """快速克隆（仅提取 + 分析，不训练）"""
        return self.clone(target, name=name, do_train=False)

    def clone_from_file(
        self, file_path: str, name: str = "", do_train: bool = True,
    ) -> Dict[str, Any]:
        """从文件克隆（支持 txt/csv/json 导出格式）"""
        ext = Path(file_path).suffix.lower().lstrip(".")
        source = ext if ext in ("txt", "csv", "json") else "auto"
        return self.clone(
            target=file_path,
            source=source,
            name=name or Path(file_path).stem,
            do_train=do_train,
        )

    # ── 内部方法 ──

    def _extract(
        self, target: str, source: str, **kwargs,
    ) -> List[Dict[str, Any]]:
        """根据来源类型调用对应提取器"""
        if source == "wcf":
            return self.extractor.extract_from_wcf(target, **kwargs)
        elif source == "wechatmsg":
            return self.extractor.extract_from_wechatmsg(target, **kwargs)
        elif source == "decrypt":
            return self.extractor.extract_from_decrypt(target, **kwargs)
        elif source in ("txt", "csv", "json", "auto"):
            return self.extractor.extract_from_export(target, format=source, **kwargs)
        else:
            logger.error("未知来源类型: %s", source)
            return []

    def _inject_to_tone_mimic(
        self, conversations: List[Dict[str, Any]],
    ) -> int:
        """将对话注入到小暖的语气模仿器（ToneMimic）"""
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from my_character.tone_mimic import ToneMimic

            tm = ToneMimic(chroma_path=str(Path(__file__).parent.parent / "data" / "chroma_db"))
            injected = 0

            for conv in conversations:
                if not conv.get("user") or not conv.get("reply"):
                    continue
                try:
                    tm.add_conversation(
                        user_msg=conv["user"],
                        reply=conv["reply"],
                        metadata={"source": "clone", "timestamp": str(conv.get("timestamp", ""))},
                    )
                    injected += 1
                except Exception as e:
                    logger.debug("ToneMimic 注入失败: %s", e)

            logger.info("ToneMimic 注入: %d/%d 条成功", injected, len(conversations))
            return injected

        except ImportError as e:
            logger.warning("ToneMimic 注入跳过（模块不可用）: %s", e)
            return 0

    def extract(self, target: str, source: str = "wcf", **kwargs) -> List[Dict[str, Any]]:
        """Public wrapper around _extract for API use"""
        return self._extract(target, source, **kwargs)

    def train(
        self,
        config_path: str = "",
        progress_callback=None,
        epochs: int = 3,
        lora_rank: int = 16,
        **kwargs,
    ) -> Dict[str, Any]:
        """Run LoRA training on the most recently prepared dataset.

        Args:
            config_path: Ignored (kept for API compatibility).
            progress_callback: Optional callable(step, total, loss).
            epochs: Number of training epochs.
            lora_rank: LoRA rank dimension.
            **kwargs: Additional kwargs forwarded to LoRATrainer.train().

        Returns:
            Dict with status/output_dir/train_loss on success,
            or status "error" on failure.
        """
        # Find most recent dataset files
        dataset_dir = self.data_dir / "dataset"
        if not dataset_dir.exists():
            return {"status": "error", "error": "No dataset directory found"}

        train_path = None
        val_path = None
        jsonl_files = sorted(dataset_dir.glob("*_train.jsonl"))
        if jsonl_files:
            train_path = str(jsonl_files[-1])
            val_name = jsonl_files[-1].name.replace("_train.jsonl", "_val.jsonl")
            val_candidate = dataset_dir / val_name
            if val_candidate.exists():
                val_path = str(val_candidate)

        if not train_path:
            json_files = sorted(dataset_dir.glob("*.json"))
            if json_files:
                train_path = str(json_files[-1])

        if not train_path:
            return {"status": "error", "error": "No training dataset found"}

        logger.info("Training dataset: %s (val: %s)", train_path, val_path)
        return self.trainer.train(
            train_path=train_path,
            val_path=val_path,
            lora_r=lora_rank,
            num_epochs=epochs,
            progress_callback=progress_callback,
            **kwargs,
        )

    def health_check(self) -> dict:
        """健康检查"""
        return {
            "data_dir": str(self.data_dir),
            "trainer_available": self.trainer._peft_available,
            "quant_available": self.trainer._quant_available,
        }
