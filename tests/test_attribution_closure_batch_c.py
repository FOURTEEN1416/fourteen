"""块C（P0 消息归属闭环）回归 —— 2026-09-22 六域二次根治。

## 根因（一句话）

**出站消息（主动消息 / 到期提醒 / 微信追问）从不写角色归属**，而读取端的
``OR character_id = ''`` 兼容条款在"存量数据全空"时恒真 ⇒ 角色过滤**整体
失效**：切到角色 B 仍读到角色 A 说过的台词。

## 三处缺陷与修法

1. `record_outbound_message` 只写 `role + session_id`（无 character_id）
   → 补写五归属（character_id / user_key / turn_id / importance / channel），
   与入站 `write_chat_history_sync` 同构。
2. 读取端 `character_id = ? OR character_id = ''` 在存量全空时恒真
   → 改两段式：命中归属行 + 无归属行**懒回填**后判定，非本角色行**排除**。
3. 装配层三处调用 (`scheduler` / `reminder_delivery` / `wechat_connector`)
   不知道当前会话绑哪个角色 → 新增 `utils.character_resolver` 唯一 owner。

## 测试策略

- **属性级/DB 行级断言**，不用 count（会被巧合命中）；
- **钉调用来源**（`resolve_character_id` 被以什么参数调用），不靠真实墙钟；
- 每个契约都有**突变验红**对应的失败路径（构造会红的输入）。
"""

from __future__ import annotations

import pytest


def _sm(tmp_path):
    from shisi.memory.legacy.structured_memory import StructuredMemory

    return StructuredMemory(str(tmp_path / "closure.db"))


# ══════════════════════════════════════════════════════════
#  1. character_resolver —— 会话键 → 角色 id / 展示名
# ══════════════════════════════════════════════════════════

def test_resolve_returns_nonempty_even_without_manager():
    """返回值**恒非空** —— 调用方无需判空（旧实现返回 '' 引发误判）。"""
    from utils import character_resolver as cr

    assert cr.resolve_character_id("") == cr.BUILTIN_CHARACTER_ID
    assert cr.resolve_character_id("2:wx@im.wechat") == cr.BUILTIN_CHARACTER_ID


def test_resolve_prefers_manager_binding():
    from utils import character_resolver as cr

    class _Mgr:
        def get_user_character(self, key):
            return "椎名真昼" if key == "2:wx@im.wechat" else ""

    assert cr.resolve_character_id("2:wx@im.wechat", _Mgr()) == "椎名真昼"
    # 未绑定的会话回落内置 id，而非空串
    assert cr.resolve_character_id("9:other", _Mgr()) == cr.BUILTIN_CHARACTER_ID


def test_resolve_survives_manager_exception():
    """绑定表查询抛异常不得冒出（旧实现 try/except 后回落全局单值）。"""
    from utils import character_resolver as cr

    class _Boom:
        def get_user_character(self, key):
            raise RuntimeError("db locked")

    assert cr.resolve_character_id("2:wx@im.wechat", _Boom()) == cr.BUILTIN_CHARACTER_ID


def test_default_is_legal_id_not_failure():
    """🔴 核心契约：'default' 是**合法角色 id**，不是"没解析到"。

    旧 `api/run_api._character_resolver` 用 `if not char_id` 判断"未绑定"，
    而 `default` 是非空真值 ⇒ 该分支永不触发 ⇒ 走 `get_card('default')`
    取不到文件卡 ⇒ 静默回落全局角色名（A 的提醒用 B 的口吻）。
    """
    from utils import character_resolver as cr

    assert cr.BUILTIN_CHARACTER_ID == "default"
    assert cr.is_builtin("default") is True
    assert cr.is_builtin("") is True
    assert cr.is_builtin(None) is True
    assert cr.is_builtin("椎名真昼") is False


def test_display_name_maps_builtin_and_file_card(tmp_path, monkeypatch):
    """内置 → persona.yaml 名；文件卡 → 卡内 name；缺卡 → 回落 id 本身。"""
    from utils import character_resolver as cr

    assert cr.display_name("default") == cr._persona_yaml_name()
    assert cr.display_name("default") != ""  # persona.yaml 名非空
    assert cr.display_name("不存在的角色卡xyz") == "不存在的角色卡xyz"

    monkeypatch.setattr(cr, "_CHARACTERS_DIR", tmp_path)
    (tmp_path / "card-x.json").write_text('{"name": "测试角色"}', encoding="utf-8")
    cr.clear_cache()
    assert cr.display_name("card-x") == "测试角色"


def test_run_api_character_resolver_does_not_fall_back_to_global():
    """🔴 块C 直击：装配层解析器**不得**回落全局 `current_character_name`。

    以源码级静态断言锁定：旧写法的 `if not char_id:` + "回落全局"组合一旦
    回归，本用例立即红。
    """
    from pathlib import Path

    src = Path("api/run_api.py").read_text(encoding="utf-8")
    seg_start = src.index("def _character_id_resolver(")
    seg_end = src.index("\n        _scheduler.register_reminder_task", seg_start)
    body = src[seg_start:seg_end]
    assert "current_character_name" not in body
    assert "_cr.resolve_character_id(session_key, user_mgr)" in body
    assert "character_id_resolver=_character_id_resolver" in body


# ══════════════════════════════════════════════════════════
#  2. record_outbound_message 写归属（五列）
# ══════════════════════════════════════════════════════════

def _pipeline_with_db(tmp_path):
    from shisi.memory.legacy._legacy_working_memory import WorkingMemory
    from shisi.memory.legacy.memory_pipeline import MemoryPipeline

    sm = _sm(tmp_path)
    pipe = MemoryPipeline.__new__(MemoryPipeline)
    pipe.sm = sm
    pipe._session_id = ""
    pipe.working = WorkingMemory(limit=20)
    return pipe, sm


def test_outbound_message_persists_character_attribution(tmp_path):
    """出站 assistant 行**必须**带 character_id —— 断言行本身，不断 count。"""
    pipe, sm = _pipeline_with_db(tmp_path)
    ok = pipe.record_outbound_message(
        "在想你呀", session_id="2:wx@im.wechat",
        character_id="charA", channel="proactive", importance=0.3,
    )
    assert ok is True
    rows = sm.get_chats_by_session_limit("2:wx@im.wechat", 10)
    assert len(rows) == 1
    row = rows[0]
    assert row["role"] == "assistant"
    assert row["content"] == "在想你呀"
    assert row["character_id"] == "charA", "出站行必须带归属（旧实现只写 role+session）"
    assert row["channel"] == "proactive"
    assert float(row["importance"]) == pytest.approx(0.3)
    assert row["user_key"] == "2:wx@im.wechat", "user_key 必须落库（跨会话检索靠它）"
    sm.close()


def test_outbound_message_without_character_writes_empty(tmp_path):
    """显式不传归属时不得伪造 —— 由调用方负责解析，本层不猜。"""
    pipe, sm = _pipeline_with_db(tmp_path)
    ok = pipe.record_outbound_message("裸调用", session_id="s-bare")
    assert ok is True
    rows = sm.get_chats_by_session_limit("s-bare", 10)
    assert rows[0]["character_id"] == ""
    sm.close()


def test_outbound_rejects_empty_text_and_system_error(tmp_path):
    """空文本与系统错误占位**不得**入库（否则出站路径把兜底句写进角色历史）。"""
    pipe, sm = _pipeline_with_db(tmp_path)
    assert pipe.record_outbound_message("", session_id="s1") is False
    assert pipe.record_outbound_message("   ", session_id="s1") is False
    assert pipe.record_outbound_message(
        "抱歉，处理超时，请稍后重试", session_id="s1"
    ) is False, "系统错误占位必须被拦（_SYSTEM_ERROR_REPLIES 精确匹配）"
    assert pipe.record_outbound_message(
        "（处理消息时出现异常, 请稍后重试）", session_id="s1"
    ) is False, "异常占位必须被拦"
    assert sm.get_chats_by_session_limit("s1", 10) == []
    # 正常内容仍写入（证明拦截不是"一律拒绝"）
    assert pipe.record_outbound_message("正常的一句", session_id="s1") is True
    assert len(sm.get_chats_by_session_limit("s1", 10)) == 1
    sm.close()


# ══════════════════════════════════════════════════════════
#  3. 读取端：未知归属不认领；读取只读，归属过滤先于 LIMIT
# ══════════════════════════════════════════════════════════

def test_unknown_history_remains_unknown_after_read(tmp_path):
    """当前绑定不能证明历史说话人；任何角色读取都不能认领旧行。"""
    sm = _sm(tmp_path)
    sm.add_chat("user", "迁移前的老话", session_id="s1")
    sm.add_chat("assistant", "迁移前的回复", session_id="s1")
    for cid in ("charA", "charB", "charA"):
        assert sm.get_chats_by_session_limit("s1", 10, character_id=cid) == []
    stored = sm.get_chats_by_session("s1")
    assert [r["content"] for r in stored] == ["迁移前的老话", "迁移前的回复"]
    assert all(r["character_id"] == "" for r in stored)
    sm.close()


def test_character_filter_precedes_limit(tmp_path):
    sm = _sm(tmp_path)
    sm.add_chat("assistant", "A的已知发言", session_id="s1", character_id="charA")
    for i in range(20):
        sm.add_chat("assistant", f"未知发言{i}", session_id="s1")
    rows = sm.get_chats_by_session_limit("s1", 1, character_id="charA")
    assert [r["content"] for r in rows] == ["A的已知发言"]
    assert sm.get_chats_by_session_limit("s1", 1, character_id="charB") == []
    sm.close()


def test_unknown_history_is_invisible_even_to_first_reader(tmp_path):
    sm = _sm(tmp_path)
    sm.add_chat("user", "谁都没认领过", session_id="s2")
    assert sm.get_chats_by_session_limit("s2", 10, character_id="charB") == []
    assert sm.get_chats_by_session_limit("s2", 10, character_id="charC") == []
    assert sm.get_chats_by_session("s2")[0]["character_id"] == ""
    sm.close()


def test_no_character_filter_returns_everything(tmp_path):
    """不传 character_id = 不做角色过滤（管理/统计路径语义不变）。"""
    sm = _sm(tmp_path)
    sm.add_chat("user", "A的话", session_id="s3", character_id="charA")
    sm.add_chat("user", "B的话", session_id="s3", character_id="charB")
    rows = sm.get_chats_by_session_limit("s3", 10)
    assert len(rows) == 2
    sm.close()


def test_get_session_rows_uses_same_strict_attribution_policy(tmp_path):
    """多会话行与主历史共用严格过滤，不能绕过角色边界。"""
    sm = _sm(tmp_path)
    sm.add_chat("user", "残留老话", session_id="k1")
    sm.add_chat("assistant", "A的新话", session_id="k1", character_id="charA")

    rows_a = sm.get_session_rows(["k1"], limit=10, character_id="charA")
    contents_a = sorted(r["content"] for r in rows_a)
    assert contents_a == ["A的新话"], "空归属不伪装成当前角色"

    rows_b = sm.get_session_rows(["k1"], limit=10, character_id="charB")
    assert rows_b == [], "其他角色和未知归属均不可见"
    sm.close()


def test_repeated_reads_execute_no_history_updates(tmp_path):
    sm = _sm(tmp_path)
    sm.add_chat("user", "老话", session_id="s4")
    statements = []
    sm._connection.set_trace_callback(statements.append)
    sm.get_chats_by_session_limit("s4", 10, character_id="charA")
    sm.get_session_rows(["s4"], character_id="charB")
    assert not any(s.lstrip().upper().startswith("UPDATE") for s in statements)
    sm.close()


def test_history_read_preserves_all_unknown_attribution_fields(tmp_path):
    sm = _sm(tmp_path)
    sm.add_chat("user", "老话", session_id="5:wx@im.wechat")
    assert sm.get_chats_by_session_limit("5:wx@im.wechat", 10, character_id="charA") == []
    raw = sm.get_chats_by_session("5:wx@im.wechat")[0]
    assert raw["character_id"] == ""
    assert raw["user_key"] == ""
    sm.close()


def test_unknown_attribution_stays_unknown_after_reopen(tmp_path):
    """关闭重开数据库，证明读取没有永久伪造角色归属。"""
    sm = _sm(tmp_path)
    sm.add_chat("assistant", "她说过的话", session_id="6:wx@im.wechat")
    sm.get_chats_by_session_limit("6:wx@im.wechat", 10, character_id="charZ")

    from shisi.memory.legacy.structured_memory import StructuredMemory

    fresh = StructuredMemory(str(tmp_path / "closure.db"))
    rows = fresh.get_chats_by_session_limit("6:wx@im.wechat", 10, character_id="charZ")
    assert rows == []
    assert fresh.get_chats_by_session("6:wx@im.wechat")[0]["character_id"] == ""
    fresh.close()
    sm.close()


# ══════════════════════════════════════════════════════════
#  4. 三处调用点：解析出的归属真的传下去了
# ══════════════════════════════════════════════════════════

def test_scheduler_record_outbound_passes_character_id(tmp_path, monkeypatch):
    """主动消息回写必须带解析出的 character_id（钉参数，不看结果）。"""
    from proactive import scheduler as sched_mod

    captured: dict = {}

    class _Mem:
        def record_outbound_message(self, **kw):
            captured.update(kw)
            return True

    sched = sched_mod.ProactiveScheduler.__new__(sched_mod.ProactiveScheduler)
    sched._resolve_memory = lambda: _Mem()  # type: ignore[method-assign]
    monkeypatch.setattr(
        sched_mod.character_resolver, "resolve_character_id",
        lambda key, mgr=None: "charA",
    )
    monkeypatch.setattr(
        sched_mod.character_resolver, "display_name", lambda cid: "角色A",
    )
    sched._record_outbound("想你了", "2:wx@im.wechat")
    assert captured["character_id"] == "charA", "装配层必须传解析结果"
    assert captured["channel"] == "proactive"
    assert captured["session_id"] == "2:wx@im.wechat"


def test_scheduler_record_outbound_skips_without_session():
    """无会话键 = 无归属目标，必须**不写**（不是写一行空归属）。"""
    from proactive import scheduler as sched_mod

    called: list = []

    class _Mem:
        def record_outbound_message(self, **kw):
            called.append(kw)
            return True

    sched = sched_mod.ProactiveScheduler.__new__(sched_mod.ProactiveScheduler)
    sched._resolve_memory = lambda: _Mem()  # type: ignore[method-assign]
    sched._record_outbound("无处可去", None)
    assert called == []


def test_reminder_delivery_records_channel_and_character():
    """到期提醒回写：channel='reminder' + 角色归属齐备（钉 `_deliver` 实参）。

    `_deliver` 内部同时调发送与回写，这里以 fake 通道使其成功、捕获 recorder 实参。
    """
    import asyncio

    from proactive import reminder_delivery as rd_mod

    captured: dict = {}

    class _Mem:
        def record_outbound_message(self, **kw):
            captured.update(kw)
            return True

    class _SM:
        def mark_reminder_result(self, rid, ok):
            return None

    task = rd_mod.ReminderDeliveryTask.__new__(rd_mod.ReminderDeliveryTask)
    task._memory = _Mem()
    task._sm = _SM()
    task._character_id_resolver = lambda key: "default"

    async def _noop_compose(reminder, *, character_id):
        return "该吃药了"

    async def _ok_send(session_key, text):
        return True

    task._compose_text = _noop_compose  # type: ignore[method-assign]
    task._send_to_session = _ok_send  # type: ignore[method-assign]

    asyncio.run(task._deliver({"id": 7, "session_key": "3:web:abcd", "content": "吃药"}))

    assert captured.get("channel") == "reminder"
    assert captured.get("session_id") == "3:web:abcd"
    # 角色 id 由 resolver 解析（本机无绑定表 → 内置 default）
    assert captured.get("character_id") == rd_mod.character_resolver.BUILTIN_CHARACTER_ID


def test_reminder_delivery_resolver_prefers_injected():
    """身份只解析一次；未知结果不得借用全局角色。"""
    from proactive import reminder_delivery as rd_mod

    task = rd_mod.ReminderDeliveryTask(None, character_id_resolver=lambda key: "charA")
    assert task._resolve_character_id("any:key") == "charA"
    task._character_id_resolver = lambda key: ""
    assert task._resolve_character_id("any:key") == ""
