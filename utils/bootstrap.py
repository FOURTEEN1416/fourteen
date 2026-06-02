from __future__ import annotations

import logging
from pathlib import Path


def print_banner(version: str = "v1.1") -> None:
    print(f"""
    ╔══════════════════════════════════╗
    ║      💕 唯一的你 — AI 虚拟伴侣      ║
    ║        {version} · 微信直连          ║
    ╚══════════════════════════════════╝
    """)


def setup_logging(project_root: Path) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(
                str(project_root / "data" / "app.log"),
                encoding="utf-8",
            ),
        ],
    )
