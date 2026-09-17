"""项目路径解析 — 消除对进程当前工作目录（CWD）的隐式依赖。

背景（2026-09-17 全仓扫描）：
仓库内多处使用 ``Path("data/...")`` / ``Path("config/...")`` 这类**相对路径**
作为模块级常量或函数默认值。相对路径按**进程 CWD** 解析，因此：

- 生产环境 systemd 单元恰好设了 ``WorkingDirectory=/opt/ai-girlfriend``，
  看起来"一直是对的"——直到换入口（``python /path/to/main.py``）、
  换 CWD、或测试里直接构造组件，就会静默读写到**另一个目录**；
- 症状极具迷惑性：保存返回成功、日志正常，但重启后配置丢失、
  角色卡"读不到"、开关"不生效"；
- 已实证案例：``proactive/scheduler.py`` 的跨 worker 真源
  ``data/scheduler_config.json`` 读到脏值，直接导致
  ``test_scheduler_quiet_hours_settable`` 失败（见 docs/verification/）。

统一约定：**仓库内资源路径一律以项目根为基准**，新增代码禁止直接依赖 CWD。
"""
from __future__ import annotations

from pathlib import Path

#: 项目根目录（本文件位于 <root>/utils/ 下）
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent


def project_path(*parts: str) -> Path:
    """返回项目根下的绝对路径。

    例：``project_path("data", "sqlite.db")`` → ``<root>/data/sqlite.db``
    """
    return PROJECT_ROOT.joinpath(*parts)


def resolve_project_path(path: str | Path) -> Path:
    """把相对路径锚定到项目根；绝对路径原样返回。

    用于"调用方可能传相对路径也可能传绝对路径"的场景，
    避免调用方从不同 CWD 启动时读到不同文件。
    """
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p
