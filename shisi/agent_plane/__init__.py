"""shisi.agent_plane — 智能体状态平面（EventLedger / 投影 / 探针契约）。

P0 骨架：只提供账本与契约，不强制接入生产热路径。
实施批由 orchestrator/profile_sync 按方案 §B5 P1 接线。
"""

from shisi.agent_plane.event_ledger import (
    EventLedger,
    LedgerEvent,
    ReplayBundle,
    default_ledger,
    set_default_ledger,
)
from shisi.agent_plane.profile_projection import (
    project_profile,
    seed_profile_baseline,
    write_profile_event,
)
from shisi.agent_plane.runtime import (
    append_chat_events,
    get_profile_prompt_block,
    project_profile_for,
    write_profile_from_tool,
)

__all__ = [
    "EventLedger",
    "LedgerEvent",
    "ReplayBundle",
    "default_ledger",
    "set_default_ledger",
    "project_profile",
    "seed_profile_baseline",
    "write_profile_event",
    "append_chat_events",
    "get_profile_prompt_block",
    "project_profile_for",
    "write_profile_from_tool",
]
