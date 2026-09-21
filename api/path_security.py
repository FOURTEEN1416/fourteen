"""路径安全工具 — 防止路径遍历攻击"""
import re


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
