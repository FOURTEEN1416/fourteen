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
    import inspect

    import api.run_api as run_api

    src = inspect.getsource(run_api)
    seg_start = src.find("def _character_resolver(")
    assert seg_start != -1, "装配层解析器必须存在"
    seg_end = src.find("\n        persona = orchestrator.components.get", seg_start)
    assert seg_end != -1
    body = src[seg_start:seg_end]
    assert "if not char_id:" not in body, (
        "禁止用 `if not char_id` 判『未绑定』—— default 是非空真值，该分支永不触发"
    )
    assert "_cr.resolve_character_id" in body and "_cr.display_name" in body, (
        "必须走 utils.character_resolver 唯一 owner"
    )


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
#  3. 读取端：懒回填 + 保守过滤（旧 OR '' 反噬的根治）
# ══════════════════════════════════════════════════════════

def test_legacy_empty_rows_visible_to_bound_character_and_backfilled(tmp_path):
    """存量无归属行 → 对**当前绑定角色**可见，且回填为实体归属（幂等）。"""
    sm = _sm(tmp_path)
    sm.add_chat("user", "迁移前的老话", session_id="s1")  # character_id 空
    sm.add_chat("assistant", "迁移前的回复", session_id="s1")

    rows = sm.get_chats_by_session_limit("s1", 10, character_id="charA")
    assert [r["content"] for r in rows] == ["迁移前的老话", "迁移前的回复"]
    assert all(r["character_id"] == "charA" for r in rows)

    # 二次读取：已回填，走的是"命中归属行"路径（结果一致 = 幂等）
    with sm._conn() as conn:
        stored = conn.execute(
            "SELECT character_id FROM chat_history WHERE session_id='s1'"
        ).fetchall()
    assert all(dict(r)["character_id"] == "charA" for r in stored), "回填必须真落库"
    rows2 = sm.get_chats_by_session_limit("s1", 10, character_id="charA")
    assert [r["content"] for r in rows2] == ["迁移前的老话", "迁移前的回复"]
    sm.close()


def test_legacy_empty_rows_hidden_from_other_character(tmp_path):
    """🔴 核心：另一角色读**不得**看到尚未归位的存量行（宁缺毋串）。

    旧实现 `OR character_id = ''` 恒真 ⇒ charB 也能读到 —— 这就是
    「切角色继承他人台词」的复发通道。
    """
    sm = _sm(tmp_path)
    sm.add_chat("user", "老数据", session_id="s1")
    # 先由 charA 读走（触发回填）
    assert len(sm.get_chats_by_session_limit("s1", 10, character_id="charA")) == 1
    # charB 再读：那行已属 charA，必须看不到
    rows_b = sm.get_chats_by_session_limit("s1", 10, character_id="charB")
    assert rows_b == [], "回填后行归属明确，其他角色不得可见"
    sm.close()


def test_unbound_legacy_rows_excluded_for_second_character(tmp_path):
    """未回填的存量行对**非首个**读取角色保守排除（不猜测归属）。"""
    sm = _sm(tmp_path)
    sm.add_chat("user", "谁都没认领过", session_id="s2")
    rows_b = sm.get_chats_by_session_limit("s2", 10, character_id="charB")
    assert [r["content"] for r in rows_b] == ["谁都没认领过"], (
        "首次读取（无既有归属）时按当前绑定角色认领 = 等价于迁移默认归位"
    )
    # 认领后 charC 不可见
    rows_c = sm.get_chats_by_session_limit("s2", 10, character_id="charC")
    assert rows_c == []
    sm.close()


def test_no_character_filter_returns_everything(tmp_path):
    """不传 character_id = 不做角色过滤（管理/统计路径语义不变）。"""
    sm = _sm(tmp_path)
    sm.add_chat("user", "A的话", session_id="s3", character_id="charA")
    sm.add_chat("user", "B的话", session_id="s3", character_id="charB")
    rows = sm.get_chats_by_session_limit("s3", 10)
    assert len(rows) == 2
    sm.close()


def test_get_session_rows_uses_same_two_stage_policy(tmp_path):
    """`get_session_rows` 必须与 `get_chats_by_session_limit` 同口径。

    旧实现两处各写一份 `OR ''`，只修一处会造成"半隔离"（比不修更难查）。
    """
    sm = _sm(tmp_path)
    sm.add_chat("user", "残留老话", session_id="k1")
    sm.add_chat("assistant", "A的新话", session_id="k1", character_id="charA")

    rows_a = sm.get_session_rows(["k1"], limit=10, character_id="charA")
    contents_a = sorted(r["content"] for r in rows_a)
    assert contents_a == ["A的新话", "残留老话"], "空归属行对当前绑定角色可见并回填"

    rows_b = sm.get_session_rows(["k1"], limit=10, character_id="charB")
    assert rows_b == [], "回填后其他角色不可见"
    sm.close()


def test_backfill_is_idempotent_across_repeated_reads(tmp_path):
    """重复读取不得反复触发 UPDATE（幂等：第二次起 legacy 集为空）。"""
    sm = _sm(tmp_path)
    sm.add_chat("user", "老话", session_id="s4")
    calls: list[int] = []
    orig = sm._backfill_legacy_attribution

    def _spy(rows, character_id):
        calls.append(sum(1 for r in rows if not str(r.get("character_id") or "")))
        return orig(rows, character_id)

    sm._backfill_legacy_attribution = _spy  # type: ignore[method-assign]
    sm.get_chats_by_session_limit("s4", 10, character_id="charA")
    sm.get_chats_by_session_limit("s4", 10, character_id="charA")
    assert calls == [1, 0], f"第二次读取不应再有 legacy 行，实测 {calls}"
    sm.close()


def test_backfill_also_repairs_user_key(tmp_path):
    """🔴 存量行的 `user_key` 也须回填（跨会话检索归属键）。

    生产实测 382 行中 324 行 `user_key` 为空 —— 这些行在"跨会话尾巴"注入
    （`get_cross_session_tail` 按 user_key 取数）中**整体缺席**。
    """
    sm = _sm(tmp_path)
    sm.add_chat("user", "老话", session_id="5:wx@im.wechat")  # character_id + user_key 均空
    rows = sm.get_chats_by_session_limit("5:wx@im.wechat", 10, character_id="charA")
    assert len(rows) == 1
    assert rows[0]["character_id"] == "charA"
    assert rows[0]["user_key"] == "5:wx@im.wechat", "user_key 必须一并回填"

    with sm._conn() as conn:
        raw = dict(
            conn.execute(
                "SELECT character_id, user_key FROM chat_history WHERE session_id='5:wx@im.wechat'"
            ).fetchone()
        )
    assert raw["character_id"] == "charA", "回填必须真落库（不只改内存对象）"
    assert raw["user_key"] == "5:wx@im.wechat", "user_key 回填必须真落库"
    sm.close()


def test_backfill_backed_by_real_sql_not_memory_only(tmp_path):
    """回填必须**落库**：换一个新连接读同一 DB 仍带归属。"""
    sm = _sm(tmp_path)
    sm.add_chat("assistant", "她说过的话", session_id="6:wx@im.wechat")
    sm.get_chats_by_session_limit("6:wx@im.wechat", 10, character_id="charZ")

    from shisi.memory.legacy.structured_memory import StructuredMemory

    fresh = StructuredMemory(str(tmp_path / "closure.db"))
    rows = fresh.get_chats_by_session_limit("6:wx@im.wechat", 10, character_id="charZ")
    assert len(rows) == 1 and rows[0]["character_id"] == "charZ"
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
    task._character_name = "兜底名"
    task._character_resolver = lambda key: "会话角色"

    async def _noop_compose(reminder):
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
    """注入了 resolver 就必须用它；解析结果为空才用装配兜底名。"""
    from proactive import reminder_delivery as rd_mod

    task = rd_mod.ReminderDeliveryTask.__new__(rd_mod.ReminderDeliveryTask)
    task._character_resolver = lambda key: "会话角色"
    task._character_name = "装配兜底名"
    assert task._resolve_character_name("any:key") == "会话角色"

    task._character_resolver = lambda key: ""
    assert task._resolve_character_name("any:key") == "", (
        "注入了解析器但解析为空 → 宁缺毋串，留空（不得回落全局单值）"
    )

    task._character_resolver = None
    assert task._resolve_character_name("any:key") == "装配兜底名", (
        "无解析器（单角色部署/夹具）才允许用装配名"
    )
