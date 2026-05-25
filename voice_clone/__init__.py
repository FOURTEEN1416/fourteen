"""
声音克隆模块 — 让AI能用"那个人"的声音说话

功能：
- 声音样本上传与管理
- GPT-SoVITS训练调度
- 模型管理与切换
- 微信语音发送/接收
"""

from .model_manager import ModelManager
from .trainer import VoiceTrainer
from .upload_handler import UploadHandler
from .wechat_voice import WeChatVoiceHandler

__all__ = ["UploadHandler", "VoiceTrainer", "ModelManager", "WeChatVoiceHandler"]
