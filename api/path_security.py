"""路径安全工具 — 防止路径遍历攻击"""
import re
from pathlib import Path

# 仅允许字母、数字、下划线、连字符
_SAFE_ID_PATTERN = re.compile(r'^[A-Za-z0-9_\-]+$')

def sanitize_id(value: str, max_length: int = 128) -> str:
    """净化用户传入的 ID 参数，防止路径遍历。
    仅允许 [A-Za-z0-9_-]，长度不超过 max_length。
    如果包含非法字符，返回净化后的值（去除非法字符）。
    如果为空，返回空字符串。
    """
    if not value:
        return ""
    # 去除路径分隔符和点号（防止路径遍历）
    cleaned = re.sub(r'[^\w\-]', '', value)[:max_length]
    return cleaned

def safe_join_path(base_dir: Path, filename: str, suffix: str = ".json") -> Path:
    """安全拼接文件路径，确保结果在 base_dir 内。
    1. 净化 filename
    2. 拼接 base_dir / f"{filename}{suffix}"
    3. resolve 后校验是否在 base_dir.resolve() 内
    """
    safe_name = sanitize_id(filename)
    if not safe_name:
        raise ValueError("Invalid filename after sanitization")
    target = (base_dir / f"{safe_name}{suffix}").resolve()
    base_resolved = base_dir.resolve()
    if not str(target).startswith(str(base_resolved)):
        raise ValueError(f"Path traversal detected: {filename}")
    return target
