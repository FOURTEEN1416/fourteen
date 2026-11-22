"""称呼语义矫正 + 数据生命周期回归（2026-11-21）。

本文件先落红测，覆盖两组审计结论：

称呼链
1. `user_profile.nickname` 历史上被 profile 工具写成了「用户对 AI 的称呼」
   （生产 25/28/27/20 与 chat 5851 实证），本次必须做兼容迁移。
   2026-11-21 用户裁决「逐行进行修正」：自述名（我叫X/叫我X 等）保留在
   nickname；对 AI 的称呼（显式「叫你X/喊你X」语境）迁入 user_calls_ai
   并清空；无法判别保守保留并记待复核日志；迁移前全表备份、幂等、可
   dry-run。2026-11-22 M13/L4 修正：裸亲属/爱称（老公/姐姐/宝贝等）与
   动作整句（叫你吃饭等）方向不可判，一律 ambiguous 保守保留 + 待复核。
2. `address_terms` 的 legal 集合不得再把该字段直接当作用户称呼。
3. 角色卡 `mes_example` / `creator_notes` 里的「叫你 XX」是角色自称，
   不得直接成为对用户的称呼（拦截 + 白名单校验）。
4. 自称代词约束必须进 prompt（用「我」，不得用「你」指自己）。
5. 主链/L1 工具派发的 `_meta` 必须带 character_id，否则称呼闸在
   update_user_profile 路径永不触发（2026-11-21 验收缺陷）。

生命周期
5. `memory_recycle_bin` 到 TTL（restore_before + 宽限期）后物理删除，先备份。
6. `fact_deletion_watermarks` 早于最小相关 chat id 的已确认水位归档。
7. `user_sessions` 过期行在登录时/每日清理。
8. `user_facts` 精确同文写入必去重；语义相似只做 90 天窗候选，不自动合并。
9. 删号步骤必须覆盖 `user_character_settings` 与 `runtime_plane`。

⚠️ 本文件不运行 pytest 全量（任务纪律：统一串行测试在后续阶段）。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from shisi.agent_plane import runtime as apruntime
from shisi.agent_plane.event_ledger import EventLedger
from shisi.memory.legacy.structured_memory import StructuredMemory
from shisi.memory.legacy.user_profile import UserProfileStore

# ═══════════════════════════════════════════════════════════
# 称呼语义：字段迁移
# ═══════════════════════════════════════════════════════════


def _create_legacy_profile_db(path: Path, nickname: str) -> None:
    """建旧版 user_profile 表（无 user_calls_ai / address_semantics）。"""
    with sqlite3.connect(str(path)) as conn:
        conn.execute(
            """
            CREATE TABLE user_profile (
                user_key TEXT PRIMARY KEY,
                nickname TEXT DEFAULT '',
                birthday TEXT DEFAULT '',
                occupation TEXT DEFAULT '',
                location TEXT DEFAULT '',
                preferences TEXT DEFAULT '[]',
                commitments TEXT DEFAULT '[]',
                notes TEXT DEFAULT '',
                updated_at TEXT DEFAULT ''
            )
            """
        )
        conn.execute(
            "INSERT INTO user_profile (user_key, nickname, updated_at) VALUES (?, ?, ?)",
            ("1001:wx-legacy", nickname, "2026-11-01 00:00:00"),
        )
        conn.commit()


def test_legacy_nickname_migrates_to_user_calls_ai(tmp_path):
    """显式「叫你X」语境的称呼（叫你老公）迁到 user_calls_ai（取干净名「老公」），
    nickname 清空且幂等。2026-11-22 M13：裸词不再迁移，迁移只认显式语境。"""
    db = tmp_path / "legacy_profile.db"
    _create_legacy_profile_db(db, "叫你老公")

    store = UserProfileStore(db)
    row = store.get("1001:wx-legacy")
    assert row["user_calls_ai"] == "老公", "对 AI 的称呼必须迁入 user_calls_ai"
    assert row["nickname"] == "", "迁移后 nickname 必须清空，避免继续当作用户称呼"
    assert int(row["address_semantics"]) == 2

    # 二次构造不得重复搬运（迁移幂等）
    store2 = UserProfileStore(db)
    row2 = store2.get("1001:wx-legacy")
    assert row2["user_calls_ai"] == "老公"
    assert row2["nickname"] == ""

    block = store2.to_prompt_block("1001:wx-legacy")
    assert "用户对 AI 的称呼：老公" in block
    assert "- 称呼：老公" not in block


def test_self_reported_nickname_is_preserved(tmp_path):
    """裁决·自述名保留：叫我X/名字叫X 是用户自述名，不清空、不搬移。"""
    db = tmp_path / "legacy_self.db"
    _create_legacy_profile_db(db, "叫我小芳")
    store = UserProfileStore(db)
    row = store.get("1001:wx-legacy")
    assert row["nickname"] == "叫我小芳", "自述名必须保留在 nickname"
    assert row["user_calls_ai"] == "", "自述名不得搬进 user_calls_ai"
    assert int(row["address_semantics"]) == 2, "打已处理标记（幂等），但内容保留"

    block = store.to_prompt_block("1001:wx-legacy")
    assert "用户希望被称呼：叫我小芳" in block


def test_ambiguous_nickname_is_preserved_and_logged(tmp_path, caplog):
    """裁决·无法判别（裸名阿茹）：保守保留原状 + 记入待复核日志。"""
    import logging

    db = tmp_path / "legacy_ambiguous.db"
    _create_legacy_profile_db(db, "阿茹")
    with caplog.at_level(logging.WARNING, logger="user_profile"):
        store = UserProfileStore(db)
    row = store.get("1001:wx-legacy")
    assert row["nickname"] == "阿茹", "无法判别的行保守保留原状"
    assert row["user_calls_ai"] == ""
    assert int(row["address_semantics"]) == 2
    assert any("待复核" in r.getMessage() and "阿茹" in r.getMessage() for r in caplog.records), (
        "无法判别行必须记入待复核日志"
    )


def test_migration_backs_up_table_and_is_idempotent(tmp_path):
    """迁移前全表备份 JSONL + 每次一行审计；二次构造不重复迁移。"""
    db = tmp_path / "legacy_backup.db"
    _create_legacy_profile_db(db, "叫你老公")
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "INSERT INTO user_profile (user_key, nickname, updated_at) VALUES (?, ?, ?)",
            ("1001:wx-second", "阿茹", "2026-11-02 00:00:00"),
        )
        conn.commit()

    backup_dir = tmp_path / "backups" / "user_profile_nickname_migration"
    store = UserProfileStore(db, migration_backup_dir=backup_dir)

    files = sorted(backup_dir.glob("*.jsonl"))
    assert len(files) == 1, "首次迁移必须生成一份全表备份"
    content = files[0].read_text(encoding="utf-8")
    assert "1001:wx-legacy" in content and "老公" in content
    assert "阿茹" in content, "备份必须覆盖全表（含未迁移行），可事后追溯"
    audit = (backup_dir / "migration_audit.log").read_text(encoding="utf-8")
    assert "migrated=1" in audit and "review=1" in audit

    assert store.get("1001:wx-legacy")["user_calls_ai"] == "老公"
    assert store.get("1001:wx-second")["nickname"] == "阿茹"

    # 二次构造：address_semantics 已标记 → 不再产生新备份/审计（幂等）
    UserProfileStore(db, migration_backup_dir=backup_dir)
    assert len(sorted(backup_dir.glob("*.jsonl"))) == 1
    audit2 = (backup_dir / "migration_audit.log").read_text(encoding="utf-8")
    assert audit2.count("\n") == audit.count("\n")


def test_migration_dry_run_makes_no_changes(tmp_path):
    """dry-run：只判别与写审计，不改库、不打版本标记（后续真实迁移仍可处理）。"""
    db = tmp_path / "legacy_dry.db"
    _create_legacy_profile_db(db, "叫你老公")
    backup_dir = tmp_path / "backups"
    store = UserProfileStore(
        db, nickname_migration_dry_run=True, migration_backup_dir=backup_dir
    )
    row = store.get("1001:wx-legacy")
    assert row["nickname"] == "叫你老公", "dry-run 不得清空 nickname"
    assert row["user_calls_ai"] == "", "dry-run 不得写入 user_calls_ai"
    assert int(row["address_semantics"]) == 0, "dry-run 不得打已处理标记"
    assert (backup_dir / "migration_audit.log").exists(), "dry-run 仍需留审计"


# ═══════════════════════════════════════════════════════════
# 2026-11-22 对抗验收修复：M13 裸词方向 / L4 写侧过滤对齐 / L5 渲染器否认筛查
# ═══════════════════════════════════════════════════════════


def test_classify_bare_kinship_terms_are_ambiguous_20261122():
    """M13：裸亲属/爱称方向不可判（旧「叫我X」写入路径同样产生），改判 ambiguous。"""
    from shisi.memory.legacy.user_profile import classify_legacy_nickname as classify

    for term in (
        "老公", "老婆", "姐姐", "哥哥", "弟弟", "妹妹",
        "宝贝", "亲爱的", "老板", "小姐姐", "欧巴",
    ):
        assert classify(term) == "ambiguous", f"裸词「{term}」不得单方向判 ai_address"
    # 显式「叫你X/喊你X」语境仍可判 ai_address
    assert classify("叫你姐姐") == "ai_address"
    assert classify("喊你宝贝") == "ai_address"
    assert classify("以后叫你老公") == "ai_address"
    # 自述名判别不受影响
    assert classify("叫我小芳") == "self"
    assert classify("我的名字是默默") == "self"
    assert classify("我是十七") == "self"
    # 普通裸名仍为 ambiguous（保守保留）
    assert classify("阿茹") == "ambiguous"


def test_classify_action_sentences_are_ambiguous_20261122():
    """L4：判别前套用写侧 _NAME_STOP/动作补语过滤，动作整句改判 ambiguous。"""
    from shisi.memory.legacy.user_profile import classify_legacy_nickname as classify

    for s in ("叫你吃饭", "叫你起床", "喊你睡觉", "叫你写作业", "叫你过来一下"):
        assert classify(s) == "ambiguous", f"动作句「{s}」不得判 ai_address"
    # 「我叫你宝贝」同时命中自述与称呼两条句式 → 方向冲突 → ambiguous（不再判 self）
    assert classify("我叫你宝贝") == "ambiguous"
    # 对照：干净称呼名仍可判
    assert classify("叫你宝贝") == "ai_address"
    assert classify("叫我小芳") == "self"


def test_bare_kinship_rows_stay_pending_review_20261122(tmp_path, caplog):
    """M13 落库行为：裸词行保守保留在 nickname，不迁 user_calls_ai，记待复核。"""
    import logging

    db = tmp_path / "legacy_bare_terms.db"
    _create_legacy_profile_db(db, "姐姐")
    with sqlite3.connect(str(db)) as conn:
        conn.executemany(
            "INSERT INTO user_profile (user_key, nickname, updated_at) VALUES (?, ?, ?)",
            [
                ("1001:wx-bare2", "老公", "2026-11-01 00:00:00"),
                ("1001:wx-bare3", "宝贝", "2026-11-01 00:00:00"),
            ],
        )
        conn.commit()

    with caplog.at_level(logging.WARNING, logger="user_profile"):
        store = UserProfileStore(db)

    for uk, term in (
        ("1001:wx-legacy", "姐姐"),
        ("1001:wx-bare2", "老公"),
        ("1001:wx-bare3", "宝贝"),
    ):
        row = store.get(uk)
        assert row["nickname"] == term, "裸词方向不可判，必须保守保留原状"
        assert row["user_calls_ai"] == "", "裸词不得迁入 user_calls_ai"
        assert int(row["address_semantics"]) == 2
    assert any(
        "待复核" in r.getMessage() and "老公" in r.getMessage() for r in caplog.records
    ), "裸词行必须记入待复核日志"


def test_action_sentence_rows_stay_pending_review_20261122(tmp_path, caplog):
    """L4 落库行为：动作整句保留原状 + 待复核，不迁不判方向。"""
    import logging

    db = tmp_path / "legacy_action_sentence.db"
    _create_legacy_profile_db(db, "叫你吃饭")
    with caplog.at_level(logging.WARNING, logger="user_profile"):
        store = UserProfileStore(db)

    row = store.get("1001:wx-legacy")
    assert row["nickname"] == "叫你吃饭", "动作整句必须保守保留原状"
    assert row["user_calls_ai"] == "", "动作整句不得迁入 user_calls_ai"
    assert any(
        "待复核" in r.getMessage() and "叫你吃饭" in r.getMessage()
        for r in caplog.records
    ), "动作整句行必须记入待复核日志"


def test_store_renderer_screens_denied_names_like_runtime_20261122(tmp_path):
    """L5：同数据下 to_prompt_block 与 format_profile_prompt_block 行为一致——
    被用户否认过的称呼不得经 store 渲染路径重新进入提示词。"""
    from shisi.agent_plane.runtime import format_profile_prompt_block

    db = tmp_path / "legacy_denied_screen.db"
    _create_legacy_profile_db(db, "小鹿")
    store = UserProfileStore(db)
    uk = "1001:wx-legacy"
    store.upsert(uk, birthday="腊月初一")
    row = store.get(uk)

    # 未否认：两个渲染器都展示
    assert "用户希望被称呼：小鹿" in store.to_prompt_block(uk)
    assert "用户希望被称呼：小鹿" in format_profile_prompt_block(row)

    # 否认「小鹿」后：两个渲染器都隐藏，其余字段照常
    hidden_store = store.to_prompt_block(uk, denied_names=["小鹿"])
    hidden_runtime = format_profile_prompt_block(row, denied_names=["小鹿"])
    assert "小鹿" not in hidden_store and "用户希望被称呼" not in hidden_store
    assert "生日：腊月初一" in hidden_store
    assert "小鹿" not in hidden_runtime and "用户希望被称呼" not in hidden_runtime
    assert "生日：腊月初一" in hidden_runtime

    # 显式空集 = 不筛查（向后兼容旧调用）
    assert "用户希望被称呼：小鹿" in store.to_prompt_block(uk, denied_names=[])


def test_store_renderer_semantics_screening_matches_runtime_20261122(tmp_path):
    """L5：semantics<2 且已有 user_calls_ai 时 nickname 视为历史污染值，
    双渲染器一致隐藏；语义升级后一致恢复展示。"""
    from shisi.agent_plane.runtime import format_profile_prompt_block

    db = tmp_path / "legacy_semantics_screen.db"
    _create_legacy_profile_db(db, "小鹿")
    store = UserProfileStore(db)
    uk = "1001:wx-legacy"
    store.upsert(uk, nickname="小鹿", user_calls_ai="老公", address_semantics=0)
    row = store.get(uk)

    assert "用户希望被称呼" not in store.to_prompt_block(uk)
    assert "用户希望被称呼" not in format_profile_prompt_block(row)
    assert "用户对 AI 的称呼：老公" in store.to_prompt_block(uk)

    store.upsert(uk, address_semantics=2)
    assert "用户希望被称呼：小鹿" in store.to_prompt_block(uk)
    assert "用户希望被称呼：小鹿" in format_profile_prompt_block(store.get(uk))


def test_store_renderer_default_call_screens_persisted_denial_20261122(
    tmp_path, monkeypatch
):
    """L5 接线（复核返工项）：生产 store 调用点（ase_engine._user_profile_snippet
    / persona_service legacy 回退）都是**无参** ``to_prompt_block(user_key)``——
    缺省必须自动取双源否认名筛查，被否认称呼不得经 store 路径重回生成提示词。

    修复前：缺省 None 不做筛查，ledger 里已持久否认的「小鹿」照样渲染（红）；
    修复后：默认调用即隐藏（绿），其余画像字段不受影响。
    """
    from shisi.agent_plane.profile_projection import write_profile_event

    db = tmp_path / "legacy_denied_default.db"
    _create_legacy_profile_db(db, "小鹿")
    store = UserProfileStore(db)
    uk = "1001:wx-legacy"
    store.upsert(uk, birthday="腊月初一")

    led = EventLedger(tmp_path / "agent_plane.db")
    monkeypatch.setattr(apruntime, "get_ledger", lambda: led)
    # 用户明确否认「小鹿」→ 持久否决事件（源②，窗口滑出后仍生效）
    write_profile_event(
        led,
        session_key=uk,
        payload={"denied_name": "小鹿", "reason": "test: 用户明确否认该称呼"},
        correct=True,
        actor="denial_gate",
    )
    assert "小鹿" in apruntime.denied_names_for(uk), "前提：否认已入双源合并入口"

    # 与生产调用点同形：无参调用（denied_names 缺省 None）
    block = store.to_prompt_block(uk)
    assert "小鹿" not in block, "被否认称呼不得经 store 渲染路径进入提示词"
    assert "用户希望被称呼" not in block
    assert "生日：腊月初一" in block, "否认只隐藏称呼，其余画像字段照常"


def test_store_renderer_explicit_empty_list_skips_screening_20261122(tmp_path):
    """显式传 [] = 调用方确认无否认、跳过筛查（只读/工具路径的豁免口仍在）；
    与缺省 None（自动取否认名）语义分开。"""
    db = tmp_path / "legacy_denied_explicit_empty.db"
    _create_legacy_profile_db(db, "小鹿")
    store = UserProfileStore(db)
    uk = "1001:wx-legacy"
    assert "用户希望被称呼：小鹿" in store.to_prompt_block(uk, denied_names=[])


def test_store_renderer_denied_resolution_failure_degrades_20261122(
    tmp_path, monkeypatch
):
    """否认名取数失败（账本故障等）必须降级为空集照常渲染，不得阻断画像注入。"""
    db = tmp_path / "legacy_denied_degrade.db"
    _create_legacy_profile_db(db, "小鹿")
    store = UserProfileStore(db)
    uk = "1001:wx-legacy"

    def _boom(_sk):
        raise RuntimeError("ledger unavailable")

    monkeypatch.setattr(apruntime, "denied_names_for", _boom)
    block = store.to_prompt_block(uk)
    assert "用户希望被称呼：小鹿" in block, "取数失败按无否认降级，渲染不被阻断"


def test_self_intro_name_is_valid_fallback():
    """nickname 不可信时，用户自述「我叫默默」可成为合法称呼；职业句不误抽。"""
    import utils.address_terms as at

    assert at.self_intro_name("我叫默默，做安全的") == "默默"
    assert at.self_intro_name("我是做后端的，喜欢猫") == ""
    assert at.resolve_user_address_from_profile(
        {"self_intro": "我叫默默，做安全的", "user_calls_ai": "阿茹"}
    ) == "默默"


def test_address_terms_never_uses_user_calls_ai_as_user_address(monkeypatch):
    """user_calls_ai 是「用户怎么叫 AI」，绝不能成为 AI 对用户的称呼。"""
    import utils.address_terms as at
    import utils.character_resolver as cr

    monkeypatch.setattr(at, "_profile_nickname", lambda sk: "")
    monkeypatch.setattr(at, "_profile_user_calls_ai", lambda sk: "阿茹")
    monkeypatch.setattr(cr, "display_name", lambda cid: "十四")

    legal, _forbidden = at.resolve_address("1001:wx-call", "default")
    assert legal == "你", "用户没有自述名字时只能称「你」"

    block = at.address_prompt_block("1001:wx-call", "default")
    assert "用户平时叫你「阿茹」" in block
    assert "不是用户的名字" in block


def test_card_declared_vocative_is_not_user_address(tmp_path, monkeypatch):
    """角色卡「叫你 XX」是角色自称，必须从用户称呼白名单中拦掉。"""
    import utils.address_terms as at
    import utils.character_resolver as cr

    card_dir = tmp_path / "characters"
    card_dir.mkdir()
    (card_dir / "fc3130cd.json").write_text(
        json.dumps(
            {
                "id": "fc3130cd",
                "name": "林某",
                "creator_notes": "你性格安静。以后叫你阿茹，别叫错了。",
                "mes_example": "<START>\n阿茹：今天也一起走吧。",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cr, "_CHARACTERS_DIR", card_dir)

    assert at.extract_self_vocatives("以后叫你阿茹，别叫错了") == {"阿茹"}
    assert "阿茹" in at.character_card_self_names("fc3130cd")
    guard = at.card_vocative_guard("以后叫你阿茹")
    assert "不是用户" in guard

    monkeypatch.setattr(at, "_profile_nickname", lambda sk: "阿茹")
    monkeypatch.setattr(at, "_denied_names", lambda sk: set())
    legal, forbidden = at.resolve_address("1001:wx-card", "fc3130cd")
    assert legal == "你", "卡文「叫你阿茹」不得直接成为用户称呼"
    assert "阿茹" in forbidden, "卡文自称必须进非法称呼集，出站可确定性剥除"

    block = at.address_prompt_block("1001:wx-card", "fc3130cd")
    assert "卡文" in block and "阿茹" in block
    assert "绝不能用来称呼用户" in block


def test_address_prompt_block_has_self_reference_rule(monkeypatch):
    """自称代词约束：只能用「我」，不得用「你」指自己。"""
    import utils.address_terms as at
    import utils.character_resolver as cr

    monkeypatch.setattr(at, "_profile_nickname", lambda sk: "阿树")
    monkeypatch.setattr(at, "_profile_user_calls_ai", lambda sk: "小十")
    monkeypatch.setattr(at, "_denied_names", lambda sk: set())
    monkeypatch.setattr(cr, "display_name", lambda cid: "十四")

    block = at.address_prompt_block("1001:wx-self", "default")
    assert "自称" in block
    assert "「我」" in block
    assert "不得用「你」指代自己" in block
    assert "用户平时叫你「小十」" in block


def test_profile_tool_routes_card_self_name_to_user_calls_ai(tmp_path, monkeypatch):
    """写侧拦截：LLM 把角色卡自称写进 nickname 时改存 user_calls_ai。"""
    from tools.builtin.profile_agent_tools import UpdateUserProfileTool

    card_dir = tmp_path / "characters"
    card_dir.mkdir()
    (card_dir / "fc3130cd.json").write_text(
        json.dumps(
            {
                "id": "fc3130cd",
                "name": "林某",
                "creator_notes": "以后叫你阿茹。",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    import utils.character_resolver as cr

    monkeypatch.setattr(cr, "_CHARACTERS_DIR", card_dir)

    led = EventLedger(tmp_path / "agent_plane.db")
    monkeypatch.setattr(apruntime, "get_ledger", lambda: led)
    store_db = tmp_path / "profile_cache.db"
    UserProfileStore(store_db)
    monkeypatch.setattr(apruntime, "_store", lambda: UserProfileStore(store_db))

    res = UpdateUserProfileTool().execute(
        _meta={"session_key": "1001:wx-tool", "character_id": "fc3130cd"},
        nickname="阿茹",
        reason="用户说以后叫你阿茹",
    )
    assert res.success
    profile = res.data["profile"]
    assert profile["nickname"] == "", "卡文自称不得占用用户称呼字段"
    assert profile["user_calls_ai"] == "阿茹", "应改存为用户对 AI 的称呼"


def test_main_chain_tool_meta_carries_character_id(tmp_path, monkeypatch):
    """主链/L1 工具派发的 _meta 必须带 character_id（称呼闸的触发前提）。"""
    import agent_core.events as acevents
    from orchestrator.optimized_orchestrator import OptimizedOrchestrator
    from tools.base_tool import ToolResult

    # 影子账本重定向到沙箱（agent_core.events._DB_PATH 文档化注入点）
    monkeypatch.setattr(acevents, "_DB_PATH", tmp_path / "shadow.db", raising=False)
    led = EventLedger(tmp_path / "agent_plane.db")
    monkeypatch.setattr(apruntime, "get_ledger", lambda: led)

    captured: dict = {}

    class _Registry:
        def get(self, name):
            return SimpleNamespace(wants_call_context=True)

        def get_tools_by_permission(self, level):
            return []

    class _Tools:
        registry = _Registry()

        def dispatch(self, name, args, **kwargs):
            captured["name"] = name
            captured["args"] = args
            return ToolResult(True, data={"ok": True})

    orch = OptimizedOrchestrator()
    tc = {
        "id": "call-meta-1",
        "function": {
            "name": "update_user_profile",
            "arguments": json.dumps({"nickname": "阿茹"}, ensure_ascii=False),
        },
    }
    out = asyncio.run(
        orch._dispatch_tool_call(
            tc,
            tools=_Tools(),
            session_key="1001:wx-meta",
            user_id=7,
            turn_id="turn-meta-1",
            character_id="fc3130cd",
        )
    )
    assert out["success"]
    assert captured["name"] == "update_user_profile"
    meta = captured["args"]["_meta"]
    assert meta["character_id"] == "fc3130cd", "_meta 必须携带 character_id"
    assert meta["session_key"] == "1001:wx-meta"
    assert meta["call_id"] == "call-meta-1"


def test_main_chain_update_user_profile_routes_card_self_name(tmp_path, monkeypatch):
    """端到端：主链 update_user_profile（经 _dispatch_tool_call 注入 _meta）
    触发角色卡自称改存 user_calls_ai——修复前 _meta 缺 character_id 闸门不触发。"""
    import agent_core.events as acevents
    from orchestrator.optimized_orchestrator import OptimizedOrchestrator
    from tools.builtin.profile_agent_tools import UpdateUserProfileTool

    card_dir = tmp_path / "characters"
    card_dir.mkdir()
    (card_dir / "fc3130cd.json").write_text(
        json.dumps(
            {"id": "fc3130cd", "name": "林某", "creator_notes": "以后叫你阿茹。"},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    import utils.character_resolver as cr

    monkeypatch.setattr(cr, "_CHARACTERS_DIR", card_dir)

    led = EventLedger(tmp_path / "agent_plane.db")
    monkeypatch.setattr(apruntime, "get_ledger", lambda: led)
    store_db = tmp_path / "profile_cache.db"
    UserProfileStore(store_db)
    monkeypatch.setattr(apruntime, "_store", lambda: UserProfileStore(store_db))
    monkeypatch.setattr(acevents, "_DB_PATH", tmp_path / "shadow.db", raising=False)

    tool = UpdateUserProfileTool()

    class _Tools:
        registry = SimpleNamespace(
            get=lambda name: tool, get_tools_by_permission=lambda level: []
        )

        def dispatch(self, name, args, **kwargs):
            meta = args.pop("_meta", {})
            return tool.execute(_meta=meta, **args)

    orch = OptimizedOrchestrator()
    tc = {
        "id": "call-main-1",
        "function": {
            "name": "update_user_profile",
            "arguments": json.dumps(
                {"nickname": "阿茹", "reason": "用户说以后叫你阿茹"},
                ensure_ascii=False,
            ),
        },
    }
    out = asyncio.run(
        orch._dispatch_tool_call(
            tc,
            tools=_Tools(),
            session_key="1001:wx-main",
            user_id=8,
            turn_id="turn-main-1",
            character_id="fc3130cd",
        )
    )
    assert out["success"], out
    profile = out["result"]["data"]["profile"]
    assert profile["nickname"] == "", "主链路径同样不得把角色自称写进 nickname"
    assert profile["user_calls_ai"] == "阿茹", "主链路径应触发称呼闸改存 user_calls_ai"


# ═══════════════════════════════════════════════════════════
# 生命周期：回收站 TTL / 水位裁剪 / 会话清理 / 事实去重
# ═══════════════════════════════════════════════════════════


def test_memory_recycle_bin_ttl_purges_expired_with_backup(tmp_path):
    """restore_before + 宽限期已过 → 物理删；删除前必须留备份。"""
    sm = StructuredMemory(str(tmp_path / "memory.db"))
    now = datetime(2026, 11, 21, 12, 0, 0)
    with sm.get_connection(write=True) as conn:
        # 与生产 delete_fact 同口径落库（ensure_ascii=False），否则中文会被
        # 转义成字面量 \uXXXX，备份读回时无法按原文核对。
        rows = [
            ("user_fact:1001:wx", "1", json.dumps({"fact": "旧事实一"}, ensure_ascii=False), "2026-11-01 00:00:00", "2026-11-11 00:00:00"),
            ("user_fact:1001:wx", "2", json.dumps({"fact": "刚删除"}, ensure_ascii=False), "2026-11-20 00:00:00", "2026-11-30 00:00:00"),
            ("user_fact:1001:wx", "3", json.dumps({"fact": "未来到期"}, ensure_ascii=False), "2026-12-01 00:00:00", "2026-12-31 00:00:00"),
        ]
        conn.executemany(
            "INSERT INTO memory_recycle_bin "
            "(character_id, memory_id, memory_content, deleted_at, restore_before, restored) "
            "VALUES (?, ?, ?, ?, ?, 0)",
            rows,
        )
        conn.commit()

    backup_dir = tmp_path / "backups"
    receipt = sm.purge_expired_recycle_bin(grace_days=7, now=now, backup_dir=backup_dir)
    assert receipt["deleted"] == 1
    assert receipt["expired"] == 1
    assert receipt["backup"], "有删除时必须生成备份路径"
    backup = Path(receipt["backup"])
    assert backup.exists()
    content = backup.read_text(encoding="utf-8")
    assert "旧事实一" in content
    assert "旧事实二" not in content

    with sm.get_connection() as conn:
        remain = [r["memory_id"] for r in conn.execute("SELECT memory_id FROM memory_recycle_bin ORDER BY id")]
    assert remain == ["2", "3"]


def test_fact_deletion_watermarks_prune_before_min_chat_id(tmp_path):
    """早于最小相关 chat id 的水位已失效，归档删除；仍能阻挡旧来源的水位保留。"""
    sm = StructuredMemory(str(tmp_path / "memory.db"))
    with sm.get_connection(write=True) as conn:
        # 显式 id=10/11，模拟该会话现存的聊天窗口
        conn.execute(
            "INSERT INTO chat_history (id, role, content, session_id) VALUES (10, 'user', '旧消息', '1001:wx1')"
        )
        conn.execute(
            "INSERT INTO chat_history (id, role, content, session_id) VALUES (11, 'assistant', '回复', '1001:wx1')"
        )
        conn.executemany(
            "INSERT INTO fact_deletion_watermarks (user_key, fact, through_chat_id) VALUES (?, ?, ?)",
            [
                ("1001:wx1", "水位早于窗口", 5),   # 失效：所有保留聊天都 >5
                ("1001:wx1", "仍挡旧来源", 15),    # 有效：10..15 仍在窗口内
                ("1001:wx2", "无聊天记录", 99),    # 保留：防迟到抽取复活
            ],
        )
        conn.commit()

    backup_dir = tmp_path / "backups"
    receipt = sm.prune_deletion_watermarks(backup_dir=backup_dir)
    assert receipt["pruned"] == 1
    assert Path(receipt["backup"]).exists()

    with sm.get_connection() as conn:
        rows = conn.execute(
            "SELECT user_key, fact, through_chat_id FROM fact_deletion_watermarks "
            "ORDER BY user_key, fact"
        ).fetchall()
    assert [dict(r) for r in rows] == [
        {"user_key": "1001:wx1", "fact": "仍挡旧来源", "through_chat_id": 15},
        {"user_key": "1001:wx2", "fact": "无聊天记录", "through_chat_id": 99},
    ]


def test_user_sessions_cleanup_expired(tmp_path):
    """过期 refresh 会话必须可被全局/单用户清理，未过期行保留。"""
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from api.database import Base, User, UserSession

    async def _run() -> tuple[int, int]:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'users.db'}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        now = datetime.now(timezone.utc)
        async with session_factory() as db:
            user = User(
                email="lifecycle-test@example.com",
                username="lifecycle-test",
                hashed_password="x",
                role="viewer",
                is_active=True,
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)
            db.add_all([
                UserSession(
                    user_id=user.id,
                    refresh_token_hash="expired",
                    expires_at=now - timedelta(days=1),
                ),
                UserSession(
                    user_id=user.id,
                    refresh_token_hash="alive",
                    expires_at=now + timedelta(days=1),
                ),
            ])
            await db.commit()

            from api.lifecycle import cleanup_expired_user_sessions

            removed = await cleanup_expired_user_sessions(db, user_id=int(user.id))
            remain = len((
                await db.execute(
                    UserSession.__table__.select().where(UserSession.user_id == int(user.id))
                )
            ).all())
        await engine.dispose()
        return removed, remain

    removed, remain = asyncio.run(_run())
    assert removed == 1
    assert remain == 1


def test_user_sessions_sync_cleanup_for_daily_job(tmp_path):
    """每日维护走同步清理（不依赖请求事件循环）。"""
    from api.lifecycle import purge_expired_user_sessions_sync

    db = tmp_path / "users.db"
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            """
            CREATE TABLE user_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                refresh_token_hash TEXT NOT NULL,
                expires_at DATETIME NOT NULL
            )
            """
        )
        conn.executemany(
            "INSERT INTO user_sessions (user_id, refresh_token_hash, expires_at) VALUES (?, ?, ?)",
            [
                (1, "expired", "2026-11-01 00:00:00"),
                (1, "alive", "2026-12-01 00:00:00"),
            ],
        )
        conn.commit()

    removed = purge_expired_user_sessions_sync(
        db_path=db, now=datetime(2026, 11, 21, 12, 0, 0, tzinfo=timezone.utc)
    )
    assert removed == 1
    with sqlite3.connect(str(db)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM user_sessions").fetchone()[0] == 1


def test_fact_exact_duplicate_is_reinforced_not_inserted(tmp_path):
    """精确同文（归一化后）写入必须强化原行，不得双插。"""
    sm = StructuredMemory(str(tmp_path / "memory.db"))
    first = sm.add_fact_receipt("用户喜欢吃火锅", user_key="1001:wx1")
    second = sm.add_fact_receipt("用户喜欢吃火锅。", user_key="1001:wx1")
    assert first["action"] == "inserted"
    assert second["action"] == "reinforced"
    assert second["fact_id"] == first["fact_id"]

    with sm.get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM user_facts").fetchone()[0]
    assert count == 1


def test_restore_recycle_exact_duplicate_returns_existing_id(tmp_path):
    """恢复入口：同归属已有精确同文时只标记 restored，不双插。"""
    sm = StructuredMemory(str(tmp_path / "memory.db"))
    first = sm.add_fact_receipt("用户喜欢猫", user_key="1001:wx1")["fact_id"]
    assert sm.delete_fact(first, recycle=True, user_key="1001:wx1") is True
    with sm.get_connection() as conn:
        recycle_id = int(conn.execute(
            "SELECT id FROM memory_recycle_bin WHERE restored = 0 ORDER BY id DESC LIMIT 1"
        ).fetchone()[0])
    again = sm.add_fact_receipt("用户喜欢猫", user_key="1001:wx1")["fact_id"]
    assert sm.restore_fact_from_recycle(recycle_id) == again
    with sm.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM user_facts").fetchone()[0] == 1
        assert conn.execute(
            "SELECT restored FROM memory_recycle_bin WHERE id = ?", (recycle_id,)
        ).fetchone()[0] == 1


def test_fact_exact_dedupe_maintenance_merges_legacy_duplicates(tmp_path):
    """存量精确同文重复行由维护任务合并（保留高置信度行 + 备份）。"""
    sm = StructuredMemory(str(tmp_path / "memory.db"))
    with sm.get_connection(write=True) as conn:
        conn.executemany(
            "INSERT INTO user_facts (fact, category, confidence, user_key, access_count, status) "
            "VALUES (?, 'general', ?, '1001:wx1', ?, 'active')",
            [
                ("用户生日是腊月初一", 0.5, 1),
                ("用户生日是腊月初一。", 0.9, 2),
                ("用户喜欢吃火锅", 0.7, 1),
            ],
        )
        conn.commit()

    receipt = sm.dedupe_exact_facts(user_key="1001:wx1", backup_dir=tmp_path / "backups")
    assert receipt["deleted"] == 1
    assert Path(receipt["backup"]).exists()

    with sm.get_connection() as conn:
        rows = conn.execute("SELECT fact, confidence, access_count FROM user_facts ORDER BY id").fetchall()
    assert len(rows) == 2
    birthday = [dict(r) for r in rows if "生日" in r["fact"]][0]
    assert birthday["confidence"] == 0.9
    assert birthday["access_count"] == 3


def test_fact_semantic_duplicate_candidates_use_90_day_window(tmp_path):
    """语义相似只做 90 天窗候选（不自动合并），窗外旧事实不参与。"""
    sm = StructuredMemory(str(tmp_path / "memory.db"))
    now = datetime(2026, 11, 21, 12, 0, 0)
    ids = [
        sm.add_fact_receipt("用户喜欢吃火锅", user_key="1001:wx1")["fact_id"],
        sm.add_fact_receipt("用户喜欢吃火锅和烧烤", user_key="1001:wx1")["fact_id"],
    ]
    with sm.get_connection(write=True) as conn:
        conn.execute("UPDATE user_facts SET updated_at = ? WHERE id = ?", ("2026-11-10 00:00:00", ids[0]))
        conn.execute("UPDATE user_facts SET updated_at = ? WHERE id = ?", ("2026-11-01 00:00:00", ids[1]))
        # 90 天窗外的同文旧事实：直接落库绕过写入去重，模拟历史存量。
        cur = conn.execute(
            "INSERT INTO user_facts (fact, category, confidence, user_key, access_count, status, updated_at) "
            "VALUES ('用户喜欢吃火锅', 'general', 0.8, '1001:wx1', 0, 'active', '2026-06-01 00:00:00')"
        )
        old_id = int(cur.lastrowid)
        conn.commit()

    candidates = sm.find_semantic_duplicate_candidates(
        "用户喜欢吃火锅", user_key="1001:wx1", window_days=90, now=now
    )
    candidate_ids = {int(c["id"]) for c in candidates}
    assert ids[0] in candidate_ids
    assert old_id not in candidate_ids, "90 天窗外的旧事实不参与语义查重"


def test_lifecycle_steps_cover_settings_and_runtime_plane():
    """删号编排必须覆盖 user_character_settings（同库状态表）与 runtime_plane。"""
    from api.lifecycle import _OWNER_STEPS, _STEP_ORDER

    assert "user_state_tables" in _STEP_ORDER
    assert "runtime_plane" in _STEP_ORDER
    assert "user_state_tables" in _OWNER_STEPS
    assert "runtime_plane" in _OWNER_STEPS
