"""
LoRA 微调训练器 — 使用 HuggingFace PEFT 进行轻量级微调

核心功能：
1. 加载基座模型（DeepSeek / Qwen / 任意 HuggingFace 模型）
2. 注入 LoRA adapter
3. 训练（支持 4-bit / 8-bit 量化）
4. 保存 adapter 权重
5. 合并 adapter → 完整模型（可选）
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("clone.trainer")


class LoRATrainer:
    """HuggingFace PEFT LoRA 微调器"""

    def __init__(
        self,
        base_model: str = "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B",
        output_dir: str = "./data/lora_output",
        device: str = "auto",
    ):
        self.base_model = base_model
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.device = device

        self._peft_available = False
        self._check_dependencies()

    def _check_dependencies(self) -> None:
        deps = {
            "torch": "torch",
            "transformers": "transformers",
            "peft": "peft",
            "datasets": "datasets",
            "accelerate": "accelerate",
        }
        missing = []
        for module_name, pip_name in deps.items():
            try:
                __import__(module_name)
            except ImportError:
                missing.append(pip_name)

        if missing:
            logger.warning(
                "LoRA 训练依赖缺失: %s\n  安装: pip install %s",
                ", ".join(missing), " ".join(missing),
            )
        else:
            self._peft_available = True

        try:
            __import__("bitsandbytes")
            self._quant_available = True
        except ImportError:
            self._quant_available = False

    def train(
        self,
        train_path: str,
        val_path: str | None = None,
        lora_r: int = 16,
        lora_alpha: int = 32,
        learning_rate: float = 1e-4,
        num_epochs: int = 2,
        batch_size: int = 4,
        gradient_accumulation_steps: int = 4,
        max_seq_length: int = 512,
        use_4bit: bool = False,
        use_8bit: bool = False,
        warmup_ratio: float = 0.03,
        save_steps: int = 200,
        logging_steps: int = 10,
    ) -> dict[str, Any]:
        if not self._peft_available:
            return self._training_unavailable_result()

        import torch
        from datasets import load_dataset
        from peft import (
            LoraConfig,
            TaskType,
            get_peft_model,
            prepare_model_for_kbit_training,
        )
        from transformers import (
            AutoModelForCausalLM,
            AutoTokenizer,
            DataCollatorForLanguageModeling,
            Trainer,
            TrainingArguments,
        )

        logger.info("=" * 60)
        logger.info("LoRA 微调训练开始")
        logger.info("  基座模型: %s", self.base_model)
        logger.info("  LoRA rank: %d, alpha: %d", lora_r, lora_alpha)
        logger.info("  学习率: %.0e, Epochs: %d", learning_rate, num_epochs)
        logger.info("=" * 60)

        logger.info("[1/6] 加载数据集...")
        dataset = load_dataset("json", data_files={"train": train_path})
        if val_path and os.path.exists(val_path):
            dataset["validation"] = load_dataset(
                "json", data_files={"val": val_path}
            )["val"]

        logger.info("[2/6] 加载 Tokenizer...")
        # 安全提示：trust_remote_code=True 允许执行模型仓库中的代码
        # 仅在确信模型来源可信时使用。如需禁用，设置环境变量 DISABLE_TRUST_REMOTE_CODE=1
        _trust_remote = os.environ.get("DISABLE_TRUST_REMOTE_CODE", "").lower() not in ("1", "true", "yes")
        if _trust_remote:
            logger.warning("trust_remote_code=True 已启用，确保模型来源可信")
        tokenizer = AutoTokenizer.from_pretrained(
            self.base_model, trust_remote_code=_trust_remote,
        )
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token

        logger.info("[3/6] 数据预处理...")

        def format_chat(example: dict) -> str:
            msgs = example.get("messages", [])
            if not msgs and example.get("instruction"):
                msgs = [
                    {"role": "system", "content": example["instruction"]},
                    {"role": "user", "content": example.get("input", "")},
                    {"role": "assistant", "content": example.get("output", "")},
                ]
            formatted = ""
            for msg in msgs:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                formatted += f"<|{role}|>\n{content}\n"
            formatted += "<|end|>\n"
            return formatted

        def tokenize_fn(examples: dict) -> dict:
            texts = [format_chat({"messages": msgs}) for msgs in examples["messages"]]
            return tokenizer(
                texts, truncation=True, max_length=max_seq_length,
                padding="max_length", return_tensors="pt",
            )

        tokenized_dataset = dataset.map(
            tokenize_fn, batched=True,
            remove_columns=dataset["train"].column_names,
        )

        logger.info("[4/6] 加载基座模型...")
        model_kwargs = {
            "trust_remote_code": True,
            "torch_dtype": torch.float16 if torch.cuda.is_available() else torch.float32,
        }

        if use_4bit and self._quant_available:
            from transformers import BitsAndBytesConfig
            model_kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_compute_dtype=torch.float16,
                bnb_4bit_use_double_quant=True,
            )
            logger.info("  使用 4-bit 量化")
        elif use_8bit and self._quant_available:
            model_kwargs["load_in_8bit"] = True
            logger.info("  使用 8-bit 量化")

        model = AutoModelForCausalLM.from_pretrained(
            self.base_model, **model_kwargs,
        )

        if use_4bit or use_8bit:
            model = prepare_model_for_kbit_training(model)

        logger.info("[5/6] 配置 LoRA adapter...")
        lora_config = LoraConfig(
            task_type=TaskType.CAUSAL_LM,
            r=lora_r,
            lora_alpha=lora_alpha,
            lora_dropout=0.05,
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
            bias="none",
        )

        model = get_peft_model(model, lora_config)
        model.print_trainable_parameters()

        logger.info("[6/6] 开始训练...")
        output_path = self.output_dir / f"lora_r{lora_r}_ep{num_epochs}"
        output_path.mkdir(parents=True, exist_ok=True)

        training_args = TrainingArguments(
            output_dir=str(output_path),
            num_train_epochs=num_epochs,
            per_device_train_batch_size=batch_size,
            per_device_eval_batch_size=batch_size,
            gradient_accumulation_steps=gradient_accumulation_steps,
            learning_rate=learning_rate,
            warmup_ratio=warmup_ratio,
            logging_steps=logging_steps,
            save_steps=save_steps,
            eval_strategy="steps" if "validation" in dataset else "no",
            eval_steps=save_steps,
            save_total_limit=3,
            load_best_model_at_end="validation" in dataset,
            fp16=torch.cuda.is_available(),
            report_to="none",
            remove_unused_columns=False,
        )

        data_collator = DataCollatorForLanguageModeling(
            tokenizer=tokenizer, mlm=False,
        )

        trainer = Trainer(
            model=model,
            args=training_args,
            train_dataset=tokenized_dataset["train"],
            eval_dataset=(
                tokenized_dataset.get("validation", None)
            ),
            data_collator=data_collator,
        )

        try:
            train_result = trainer.train()
        except Exception as e:
            logger.exception("训练过程出错: %s", e)
            return {"error": "training_failed", "output_dir": str(output_path)}

        final_path = output_path / "final"
        model.save_pretrained(str(final_path))
        tokenizer.save_pretrained(str(final_path))

        config = {
            "base_model": self.base_model,
            "lora_r": lora_r,
            "lora_alpha": lora_alpha,
            "learning_rate": learning_rate,
            "num_epochs": num_epochs,
            "train_samples": len(tokenized_dataset["train"]),
            "train_loss": train_result.training_loss,
        }
        with open(output_path / "train_config.json", "w") as f:
            json.dump(config, f, indent=2)

        logger.info("=" * 60)
        logger.info("LoRA 训练完成! 输出: %s  Loss: %.4f", final_path, train_result.training_loss)
        logger.info("=" * 60)

        return {
            "output_dir": str(final_path),
            "trained_epochs": num_epochs,
            "train_loss": train_result.training_loss,
            "config": config,
            "status": "success",
        }

    def merge_and_export(
        self, lora_path: str, export_format: str = "gguf",
    ) -> str:
        if not self._peft_available:
            return ""
        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer

        merge_path = self.output_dir / "merged_model"
        merge_path.mkdir(parents=True, exist_ok=True)

        base_model = AutoModelForCausalLM.from_pretrained(
            self.base_model, trust_remote_code=True, torch_dtype=torch.float16,
        )
        tokenizer = AutoTokenizer.from_pretrained(
            self.base_model, trust_remote_code=True,
        )

        model = PeftModel.from_pretrained(base_model, lora_path)
        merged = model.merge_and_unload()  # type: ignore

        merged.save_pretrained(str(merge_path))
        tokenizer.save_pretrained(str(merge_path))

        logger.info("合并完成 → %s", merge_path)
        return str(merge_path)

    def _training_unavailable_result(self) -> dict[str, Any]:
        return {
            "status": "unavailable",
            "error": (
                "训练依赖未安装: pip install torch transformers peft datasets accelerate"
            ),
            "output_dir": str(self.output_dir),
        }

    def health_check(self) -> dict:
        return {
            "peft_available": self._peft_available,
            "quant_available": self._quant_available,
            "base_model": self.base_model,
            "output_dir": str(self.output_dir),
        }
