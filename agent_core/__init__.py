"""agent_core —— 会话级 Agent 影子账本（B-1 写 / B-2 读侧消费）。

**职责边界**：把「用户说了什么 / 我说了什么 / 调了什么工具 / 我答应过什么」按
会话级归属追加到 ``data/sqlite.db`` 的三张 ``agent_*`` 表；并提供只读消费面：
含只读回放 ``agent_core.replay``（``replay_turn`` / ``replay_session``，离线诊断，
生产零调用方）与承诺账本读侧（``list_open_commitments`` / ``close_commitment``）。

**纪律**：

- 本文件只做 re-export + ``__all__``：**无 IO、无 DB 连接、无 logger handler、
  无 atexit**（import 成本必须为零，否则会在 connectors/scheduler 的 import 图里
  埋副作用）。
- 依赖方向单向：``agent_core`` **不 import** ``orchestrator`` / ``wechat_direct``
  / ``proactive``。
- 开关 ``AGENT_CORE_EVENTS`` 运行期缺省**关闭**：关闭时所有写接口首行短路，
  不建表、不打开文件、不执行 SQL；读接口（``list_open_commitments``）同样零 IO。
  B-2 的启动期一致性视图（``utils/speaker_switches``）把「未设置」按产品意图
  呈现为 ``'1'``，**不改变**本模块的零 IO 缺省；两者口径见该模块 docstring。
  **部署必须显式写 ``AGENT_CORE_EVENTS=1``**（``.env.example`` 已写）。
"""

from __future__ import annotations

from agent_core import commitments as commitments

# 顺序有意：先把 events 载入，threads / commitments / replay 才能在包初始化
# 期间 `from agent_core import events` 拿到已就绪的模块对象。
from agent_core import events as events
from agent_core import replay as replay
from agent_core import threads as threads
from agent_core.commitments import (
    add_commitment,
    close_commitment,
    expire_commitments,
    fulfil_commitment,
    list_open_commitments,
)
from agent_core.events import connect, enabled, ensure_schema, record_event, turn_key
from agent_core.threads import close_thread, open_thread

__all__ = [
    "add_commitment",
    "close_commitment",
    "close_thread",
    "commitments",
    "connect",
    "enabled",
    "ensure_schema",
    "events",
    "expire_commitments",
    "fulfil_commitment",
    "list_open_commitments",
    "open_thread",
    "record_event",
    "replay",
    "threads",
    "turn_key",
]
