from __future__ import annotations

import logging
from pathlib import Path


def print_banner(version: str = "v1.1") -> None:
    print(f"""
    ╔══════════════════════════════════╗
    ║      💕 小暖 — AI 伴侣女友      ║
    ║        {version} · CowAgent 集成     ║
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
