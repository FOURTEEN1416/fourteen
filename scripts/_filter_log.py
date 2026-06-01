"""Filter out known third-party noise (onnxruntime EP Error, etc.) from a stream.

Usage:
    python scripts/_filter_log.py < input.log > output.log
    python -u -m uvicorn ... 2>&1 | python scripts/_filter_log.py > server.log

Filters (case-sensitive substrings dropped entirely):
    - onnxruntime EP Error block (TensorRT plugin missing)
    - nvinfer DLL missing messages
    - The asterisk decorations around the EP Error

Lines that contain filter patterns are completely dropped.
All other lines pass through unchanged.
"""
from __future__ import annotations

import re
import sys

# onnxruntime 输出的 EP Error 块用连续的 `***` 装饰行包围
# 用正则匹配整段并丢弃
_EP_ERROR_BLOCK = re.compile(
    r"^\*+\s*EP Error\s*\*+\s*\n"
    r".*?RegisterTensorRTPluginsAsCustomOps.*?\n"
    r"(?:.*?\n)*?"  # 中间任意行
    r"^\*+\s*$",  # 结尾的 *** 行
    re.MULTILINE,
)

# 单行噪声模式
_LINE_NOISE = re.compile(
    r"onnxruntime::python::RegisterTensorRTPluginsAsCustomOps"
    r"|nvinfer_1?0?\.dll.*missing"
    r"|Please install TensorRT libraries",
    re.IGNORECASE,
)


def filter_stream(in_stream, out_stream) -> None:
    buffer = ""
    for line in in_stream:
        buffer += line
        # 检查是否包含 EP Error 块（buffer 累积到 `***` 结束行）
        if "EP Error" in buffer or _LINE_NOISE.search(buffer):
            # 等待装饰 `***` 结束行
            if buffer.rstrip().endswith("***") or buffer.rstrip().endswith("**"):
                buffer = ""
        else:
            # 没有 EP Error 模式 → flush buffer
            out_stream.write(buffer)
            out_stream.flush()
            buffer = ""
    # 收尾
    if buffer and not _EP_ERROR_BLOCK.search(buffer) and not _LINE_NOISE.search(buffer):
        out_stream.write(buffer)
        out_stream.flush()


if __name__ == "__main__":
    filter_stream(sys.stdin, sys.stdout)
