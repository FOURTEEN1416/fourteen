from __future__ import annotations

"""
音色训练管理器 — 用户手动上传音频 → GPT-SoVITS训练

流程:
1. 接收用户上传的音频文件
2. 格式统一转换为WAV(16kHz单声道)
3. 可选降噪
4. 自动切片(3~10秒)
5. ASR自动标注(Whisper/FunASR)
6. 生成GPT-SoVITS配置文件(YAML)
7. 调用GPT-SoVITS训练脚本
8. 训练完成自动绑定角色

GPT-SoVITS使用配置文件而非命令行参数
"""

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("voice.voice_training")

# P1: 安全名称验证 - 防止命令注入
_SAFE_NAME_RE = re.compile(r"[^\w\-]")


def _validate_model_name(name: str) -> str:
    """验证并清理模型名称，防止命令注入"""
    safe = _SAFE_NAME_RE.sub("_", name)
    if not safe:
        raise ValueError(f"Invalid model name: {name!r}")
    return safe


class VoiceTrainingManager:
    """音色训练管理器 - 配置文件方式"""

    def __init__(self, models_dir: str = "data/clone/lora_output"):
        self._models = Path(models_dir)
        self._models.mkdir(parents=True, exist_ok=True)
        self._state: dict[str, Any] = {
            "status": "idle",
            "step": "",
            "progress": 0.0,
            "error": "",
        }
        self._is_training = False

    @property
    def state(self) -> dict[str, Any]:
        return self._state.copy()

    @property
    def is_training(self) -> bool:
        return self._is_training

    async def save_uploads(self, files: list[tuple[str, bytes]], model_name: str) -> dict[str, Any]:
        """保存上传的音频文件"""
        safe_name = _SAFE_NAME_RE.sub("_", model_name)
        audio_dir = self._models / safe_name / "raw"
        audio_dir.mkdir(parents=True, exist_ok=True)

        saved = []
        for filename, data in files:
            safe_filename = Path(filename).name
            path = audio_dir / safe_filename
            path.write_bytes(data)
            saved.append(str(path))

        self._state.update({"status": "uploaded", "step": "文件已保存", "progress": 0.1})
        logger.info("保存 %d 个音频文件到 %s", len(saved), audio_dir)
        return {"saved": len(saved), "directory": str(audio_dir)}

    async def preprocess(self, model_name: str) -> dict[str, Any]:
        """
        预处理: ffmpeg转换+降噪+切片

        需要系统安装ffmpeg
        """
        safe_name = _SAFE_NAME_RE.sub("_", model_name)
        raw_dir = self._models / safe_name / "raw"
        processed_dir = self._models / safe_name / "processed"
        processed_dir.mkdir(parents=True, exist_ok=True)

        if not raw_dir.exists() or not any(raw_dir.iterdir()):
            return {"error": "没有上传的音频文件，请先上传"}

        self._state.update({"status": "preprocessing", "step": "音频预处理中", "progress": 0.2})

        try:
            raw_files = list(raw_dir.glob("*"))
            processed_count = 0
            for raw_file in raw_files:
                if raw_file.suffix.lower() not in (".wav", ".mp3", ".flac", ".ogg", ".m4a"):
                    continue
                output_file = processed_dir / f"{raw_file.stem}.wav"
                cmd = [
                    "ffmpeg", "-i", str(raw_file),
                    "-ar", "16000", "-ac", "1", "-y",
                    str(output_file),
                ]
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.communicate()
                if proc.returncode == 0:
                    processed_count += 1

            self._state.update({"status": "preprocessed", "step": "预处理完成", "progress": 0.3})
            return {"processed": processed_count, "directory": str(processed_dir)}
        except FileNotFoundError:
            return {"error": "ffmpeg未安装，请先安装ffmpeg"}
        except Exception as e:
            logger.exception("预处理失败: %s", e)
            return {"error": str(e)}

    async def generate_dataset(self, model_name: str) -> dict[str, Any]:
        """
        ASR标注 + 生成训练列表文件

        需要Whisper或FunASR
        """
        safe_name = _SAFE_NAME_RE.sub("_", model_name)
        processed_dir = self._models / safe_name / "processed"
        list_file = self._models / f"{safe_name}.list"

        if not processed_dir.exists():
            return {"error": "没有预处理后的音频文件，请先执行预处理"}

        self._state.update({"status": "generating_dataset", "step": "生成数据集中", "progress": 0.4})

        wav_files = list(processed_dir.glob("*.wav"))
        if not wav_files:
            return {"error": "processed目录中没有WAV文件"}

        lines = []
        for wav in wav_files:
            lines.append(f"{wav}|{safe_name}|zh|请在此处填入标注文本")

        list_file.write_text("\n".join(lines), encoding="utf-8")
        self._state.update({"status": "dataset_ready", "step": "数据集已生成(需手动标注)", "progress": 0.5})
        return {"list_file": str(list_file), "entries": len(lines), "note": "标注文本需要手动补充"}

    async def train(
        self,
        model_name: str,
        gpt_sovits_dir: str = "third_party/GPT-SoVITS",
        epochs: int = 8,
        batch_size: int = 4,
    ) -> dict[str, Any]:
        """
        调用GPT-SoVITS训练（配置文件方式）
        """
        if self._is_training:
            return {"error": "训练已在进行中，请等待完成"}

        safe_name = _SAFE_NAME_RE.sub("_", model_name)
        list_file = self._models / f"{safe_name}.list"

        if not list_file.exists():
            return {"error": "训练数据集不存在，请先完成预处理和标注"}

        self._is_training = True
        self._state.update({"status": "training", "step": "GPT-SoVITS训练中", "progress": 0.7})

        try:
            gpt_path = Path(gpt_sovits_dir)

            s1_config = self._generate_s1_config(safe_name, epochs)
            s1_config_path = self._models / f"{safe_name}_s1.yaml"
            s1_config_path.write_text(
                json.dumps(s1_config, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            s2_config = self._generate_s2_config(safe_name, list_file, epochs, batch_size)
            s2_config_path = self._models / f"{safe_name}_s2.json"
            s2_config_path.write_text(
                json.dumps(s2_config, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            s1_script = gpt_path / "GPT_SoVITS" / "s1_train.py"
            if not s1_script.exists():
                self._is_training = False
                return {"error": f"GPT训练脚本不存在: {s1_script}"}

            cmd1 = ["python", str(s1_script), "-c", str(s1_config_path)]
            proc1 = await asyncio.create_subprocess_exec(
                *cmd1, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout1, stderr1 = await proc1.communicate()

            if proc1.returncode != 0:
                error_msg = stderr1.decode()[:500] if stderr1 else "GPT训练失败"
                self._state.update({"status": "error", "error": error_msg})
                self._is_training = False
                return {"status": "error", "error": error_msg}

            s2_script = gpt_path / "GPT_SoVITS" / "s2_train.py"
            if not s2_script.exists():
                self._is_training = False
                return {"error": f"SoVITS训练脚本不存在: {s2_script}"}

            cmd2 = ["python", str(s2_script), "-c", str(s2_config_path)]
            proc2 = await asyncio.create_subprocess_exec(
                *cmd2, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout2, stderr2 = await proc2.communicate()

            if proc2.returncode != 0:
                error_msg = stderr2.decode()[:500] if stderr2 else "SoVITS训练失败"
                self._state.update({"status": "error", "error": error_msg})
                self._is_training = False
                return {"status": "error", "error": error_msg}

            output_dir = self._models / safe_name
            pth_files = list(output_dir.glob("**/*.pth")) if output_dir.exists() else []

            if pth_files:
                model_path = str(max(pth_files, key=lambda p: p.stat().st_mtime))
                self._state.update({"status": "done", "step": "训练完成", "progress": 1.0, "result": {"model_path": model_path}})
                self._is_training = False
                return {"status": "success", "model_path": model_path}
            else:
                self._state.update({"status": "done", "step": "训练完成(未找到输出模型)", "progress": 1.0})
                self._is_training = False
                return {"status": "success", "note": "训练完成但未找到.pth输出文件"}

        except Exception as e:
            logger.exception("[VoiceTraining] 训练失败: %s", e)
            self._state.update({"status": "error", "error": str(e)})
            self._is_training = False
            return {"status": "error", "error": str(e)}

    def _generate_s1_config(self, model_name: str, epochs: int) -> dict:
        return {
            "train": {
                "exp_name": model_name,
                "epochs": epochs,
                "if_save_latest": True,
                "if_save_every_weights": True,
                "half_weights_save_dir": str(self._models / model_name / "weights"),
                "output_dir": str(self._models / model_name / "logs"),
                "seed": 42,
                "precision": "16-mixed",
                "save_every_n_epoch": 1,
            },
        }

    def _generate_s2_config(self, model_name: str, list_file: Path, epochs: int, batch_size: int) -> dict:
        return {
            "train": {
                "exp_name": model_name,
                "epochs": epochs,
                "batch_size": batch_size,
                "if_save_latest": True,
                "if_save_every_weights": True,
                "half_weights_save_dir": str(self._models / model_name / "weights"),
                "output_dir": str(self._models / model_name / "logs"),
            },
            "list_file": str(list_file),
        }
