"""
WeClone 适配器 — 风格克隆管线总控（无 LoRA 版）

整合两大能力：
1. 数据提取（WeChat 聊天记录 → 清洗后的对话 JSON）
2. 风格分析（12 维风格特征 → 风格档案 + 提示词）+ ToneMimic 注入

注意：LoRA 微调训练已移除（项目使用外接 API + RAG + 提示词注入）。
风格克隆通过 ToneMimic 提示词注入实现，不再依赖本地模型训练。

用法:
    from weclone_adapter import WeCloneAdapter

    adapter = WeCloneAdapter(data_dir="./data/clone")
    adapter.clone("目标人的wxid")
"""

import json
import logging
from pathlib import Path
from typing import Any

from clone_training import DataExtractor, StyleAnalyzer
from security.pii_anonymizer import PIIAnonymizer

logger = logging.getLogger("weclone.adapter")


class WeCloneAdapter:
    """WeClone 风格克隆总控（仅提取 + 分析 + ToneMimic 注入）"""

    def __init__(
        self,
        data_dir: str = "./data/clone",
        output_dir: str | None = None,  # 保留参数以兼容旧调用，内部已不使用
        base_model: str | None = None,  # 保留参数以兼容旧调用，内部已不使用
    ):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.extractor = DataExtractor(str(self.data_dir / "src"))
        self.analyzer = StyleAnalyzer()

    def clone(
        self,
        target: str,
        source: str = "wcf",
        name: str = "",
        max_samples: int = 2000,
        do_train: bool = False,  # 保留参数以兼容旧调用，永远视为 False
        lora_r: int = 0,         # 保留参数以兼容旧调用，已忽略
        **kwargs,
    ) -> dict[str, Any]:
        """
        执行风格克隆流程（提取 → PII → 风格分析 → ToneMimic 注入）

        Args:
            target: 目标（wxid / 数据库路径 / 导出文件路径）
            source: 数据来源 ("wcf" | "wechatmsg" | "decrypt" | "txt" | "json" | "csv" | "auto")
            name: 目标人物名字（用于输出文件命名）
            max_samples: 最大样本数
            do_train: 已废弃，永远视为 False（项目使用外接 API，不再训练本地模型）
            lora_r: 已废弃，已忽略
            **kwargs: 传递给具体提取器的额外参数

        Returns:
            {
                "style_profile": dict,
                "style_prompt": str,
                "uniqueness": float,
                "injected_to_tone_mimic": int,
            }
        """
        result: dict[str, Any] = {}

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
        logger.info("━━━ Step 2/4: PII 匿名化 ━━━")
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
        logger.info("━━━ Step 3/4: 分析说话风格 ━━━")
        profile = self.analyzer.analyze(conversations[:max_samples])
        result["style_profile"] = profile.to_dict()  # type: ignore[assignment]
        result["style_prompt"] = profile.to_style_prompt()  # type: ignore[assignment]
        result["uniqueness"] = profile.uniqueness_score  # type: ignore[assignment]

        # 保存风格报告
        report_path = self.data_dir / f"{name}_style_report.json"
        self.analyzer.save_report(profile, str(report_path))

        # ── Step 4: 注入到十四的语气模仿器（ToneMimic）──
        logger.info("━━━ Step 4/4: 注入风格到 ToneMimic ━━━")
        injected = self._inject_to_tone_mimic(conversations[:max_samples])
        result["injected_to_tone_mimic"] = injected

        return result

    def quick_clone(
        self, target: str, name: str = "",
    ) -> dict[str, Any]:
        """快速克隆（仅提取 + 分析 + 注入）"""
        return self.clone(target, name=name)

    def clone_from_file(
        self, file_path: str, name: str = "",
    ) -> dict[str, Any]:
        """从文件克隆（支持 txt/csv/json 导出格式）"""
        ext = Path(file_path).suffix.lower().lstrip(".")
        source = ext if ext in ("txt", "csv", "json") else "auto"
        return self.clone(
            target=file_path,
            source=source,
            name=name or Path(file_path).stem,
        )

    # ── 内部方法 ──

    def _extract(
        self, target: str, source: str, **kwargs,
    ) -> list[dict[str, Any]]:
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
        self, conversations: list[dict[str, Any]],
    ) -> int:
        """将对话注入到十四的语气模仿器（ToneMimic）"""
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
                except Exception as e:  # noqa: BLE001
                    logger.debug("ToneMimic 注入失败: %s", e)

            logger.info("ToneMimic 注入: %d/%d 条成功", injected, len(conversations))
            return injected

        except ImportError as e:
            logger.warning("ToneMimic 注入跳过（模块不可用）: %s", e)
            return 0

    def extract(self, target: str, source: str = "wcf", **kwargs) -> list[dict[str, Any]]:
        """Public wrapper around _extract for API use"""
        return self._extract(target, source, **kwargs)

    def health_check(self) -> dict:
        """健康检查（不再返回训练器状态）"""
        return {
            "data_dir": str(self.data_dir),
            "mode": "prompt_injection",
            "lora_training_removed": True,
        }
