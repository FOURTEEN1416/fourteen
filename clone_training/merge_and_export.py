"""
合并与导出 — 将 LoRA 权重合并到基础模型并导出为可部署格式

支持导出格式:
1. HuggingFace Transformers (完整模型)
2. GGUF (llama.cpp / Ollama)
3. ONNX (跨平台推理)
4. vLLM 兼容格式
5. API 服务部署配置
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger("clone.merge_export")


class ModelExporter:
    """模型合并与多格式导出"""

    def __init__(self, output_base: str = "./data/models"):
        self.output_base = Path(output_base)
        self.output_base.mkdir(parents=True, exist_ok=True)

    def merge(
        self,
        base_model: str,
        lora_weights: str,
        output_name: str = "girlfriend-clone",
    ) -> str:
        """
        合并 LoRA 权重到基础模型

        Returns:
            合并后模型路径
        """
        output_path = self.output_base / output_name / "merged"
        logger.info("合并 LoRA 权重: %s + %s → %s", base_model, lora_weights, output_path)

        # 使用 LoRATrainer.merge_lora_weights 静态方法
        from .lora_trainer import LoRATrainer
        try:
            result = LoRATrainer.merge_lora_weights(base_model, lora_weights, str(output_path))  # type: ignore
            return result
        except Exception as e:
            logger.error("合并失败: %s", e)

            # 降级：直接复制 LoRA 权重
            output_path.mkdir(parents=True, exist_ok=True)
            logger.info("降级: 复制 adapter 文件到 %s", output_path)

            # 复制 adapter_config.json 和 adapter_model.bin
            src = Path(lora_weights)
            for f in src.glob("*"):
                if f.is_file():
                    shutil.copy2(str(f), str(output_path / f.name))

            # 创建说明文件
            with open(output_path / "README.txt", "w") as f:
                f.write(f"LoRA Adapter for {base_model}\n")
                f.write(f"Original weights: {lora_weights}\n")
                f.write("\n使用方法:\n")
                f.write("  from peft import PeftModel\n")
                f.write(f"  model = PeftModel.from_pretrained(base_model, '{output_path}')\n")

            return str(output_path)

    def export_vllm(
        self,
        merged_path: str,
        output_name: str = "girlfriend-clone",
    ) -> str:
        """导出为 vLLM 可部署格式"""
        output_path = self.output_base / output_name / "vllm"

        # vLLM 需要 tokenizer 和模型权重在同一目录
        shutil.copytree(merged_path, output_path, dirs_exist_ok=True)

        # 创建 vllm 配置
        vllm_config = {
            "model": str(output_path),
            "max_model_len": 4096,
            "gpu_memory_utilization": 0.85,
            "tensor_parallel_size": 1,
        }
        with open(output_path / "vllm_config.json", "w") as f:
            json.dump(vllm_config, f, indent=2)

        logger.info("vLLM 导出完成: %s", output_path)
        return str(output_path)

    def export_onnx(
        self,
        merged_path: str,
        output_name: str = "girlfriend-clone",
        opset: int = 17,
    ) -> str:
        """导出为 ONNX 格式"""
        output_path = self.output_base / output_name / "onnx"
        output_path.mkdir(parents=True, exist_ok=True)

        logger.info("ONNX 导出需要安装 optimum 和 onnxruntime")
        logger.info("请手动执行以下命令:")
        logger.info("  pip install optimum onnx onnxruntime")
        logger.info("  optimum-cli export onnx \\")
        logger.info(f"    --model {merged_path} \\")
        logger.info(f"    --opset {opset} \\")
        logger.info(f"    {output_path}")

        return str(output_path)

    def export_gguf(
        self,
        merged_path: str,
        output_name: str = "girlfriend-clone",
        quantize: str = "q4_k_m",
    ) -> str:
        """导出为 GGUF 格式"""
        output_path = self.output_base / output_name / "gguf"
        output_path.mkdir(parents=True, exist_ok=True)

        output_file = output_path / f"{output_name}.{quantize}.gguf"

        logger.info("GGUF 导出需要 llama.cpp 工具")
        logger.info("请手动执行以下命令:")
        logger.info("  git clone https://github.com/ggerganov/llama.cpp")
        logger.info("  cd llama.cpp && make")
        logger.info(f"  python convert_hf_to_gguf.py {merged_path} \\")
        logger.info(f"    --outfile {output_file}")
        logger.info(f"  ./llama-quantize {output_file} {quantize}")

        return str(output_file)

    def create_ollama_modelfile(
        self,
        gguf_path: str,
        model_name: str = "girlfriend-clone",
        system_prompt: str = "",
        temperature: float = 0.85,
    ) -> str:
        """创建 Ollama Modelfile"""
        content = f"""# Ollama Modelfile for {model_name}
# 自动生成于 AI 女友项目

FROM {gguf_path}

PARAMETER temperature {temperature}
PARAMETER top_p 0.9
PARAMETER top_k 40
PARAMETER num_predict 512
PARAMETER repeat_penalty 1.1
PARAMETER stop "</s>"
PARAMETER stop "用户:"
PARAMETER stop "对方:"

SYSTEM \"\"\"
{system_prompt if system_prompt else '你是一个可爱傲娇的女朋友，名字叫小暖。'}
\"\"\"
"""
        modelfile_path = self.output_base / model_name / "Modelfile"
        modelfile_path.parent.mkdir(parents=True, exist_ok=True)
        with open(modelfile_path, "w", encoding="utf-8") as f:
            f.write(content)

        logger.info("Ollama Modelfile 已创建: %s", modelfile_path)
        logger.info("部署命令:")
        logger.info(f"  ollama create {model_name} -f {modelfile_path}")
        logger.info(f"  ollama run {model_name}")

        return str(modelfile_path)

    def create_deploy_config(
        self,
        model_path: str,
        model_name: str = "girlfriend-clone",
        deploy_type: str = "api",
    ) -> str:
        """创建部署配置文件"""
        config = {}

        if deploy_type == "api":
            config = {
                "service": "girlfriend-api",
                "model": model_path,
                "engine": "vllm",  # or "transformers"
                "host": "0.0.0.0",
                "port": 8080,
                "max_tokens": 512,
                "temperature": 0.85,
                "top_p": 0.9,
                "system_prompt": (
                    "你是一个可爱傲娇的女朋友，名字叫小暖。"
                    "你的回复要自然、口语化、有情感。"
                ),
            }
        elif deploy_type == "cowagent":
            config = {
                "bot_type": "girlfriend",
                "model": model_path,
                "api_base": "http://localhost:8080/v1",
                "temperature": 0.85,
                "max_history_len": 20,
            }

        config_path = self.output_base / model_name / "deploy_config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

        logger.info("部署配置已创建: %s", config_path)
        return str(config_path)

    def export_full(
        self,
        base_model: str,
        lora_weights: str,
        output_name: str = "girlfriend-clone",
        formats: List[str] = None,  # type: ignore
        ollama_system_prompt: str = "",
    ) -> Dict[str, str]:
        """
        一键全格式导出

        Returns:
            {format: output_path, ...}
        """
        if formats is None:
            formats = ["merged", "vllm", "gguf"]

        results = {}

        # Step 1: 合并
        merged_path = self.merge(base_model, lora_weights, output_name)
        results["merged"] = merged_path

        # Step 2: 各格式导出
        if "vllm" in formats:
            results["vllm"] = self.export_vllm(merged_path, output_name)

        if "onnx" in formats:
            results["onnx"] = self.export_onnx(merged_path, output_name)

        if "gguf" in formats:
            results["gguf"] = self.export_gguf(merged_path, output_name)
            if ollama_system_prompt:
                results["ollama_modelfile"] = self.create_ollama_modelfile(
                    results["gguf"], output_name, ollama_system_prompt
                )

        # Step 3: 部署配置
        results["deploy_config"] = self.create_deploy_config(merged_path, output_name)

        logger.info("全格式导出完成: %s", results)
        return results


# ── 便捷函数 ─────────────────────────────────────────────

def merge_and_export(
    base_model: str,
    lora_weights: str,
    output_name: str = "girlfriend-clone",
    output_base: str = "./data/models",
) -> Dict[str, str]:
    """快捷合并导出入口"""
    exporter = ModelExporter(output_base=output_base)
    return exporter.export_full(base_model, lora_weights, output_name)
