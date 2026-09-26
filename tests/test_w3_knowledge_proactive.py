"""W3 缺陷 H 回归 —— 知识/热点经**统一读接口**进入当前 LLM 主动决策链。

红先行钉的四件事（旧现状四处都缺）：

1. 唯一 door `proactive.llm_proactive.load_proactive_knowledge`：热点池 + 角色知识
   索引一次读出、热点前置，任一侧缺失/异常静默降级；
2. **当前生产链**（`scheduler._llm_proactive_one_user` → 决策 prompt）必须读到它，
   且角色 id 用**当轮解析出的**绑定（旧实现知识只留在被旁路的 ASE 生成线，
   决策链从不读知识 → 知识库/热点在真实链路上永不落地）；
3. 旧 ASE 分享线不再自己拼两份源，改调同一 door（否则两条线各自的源选择逻辑
   会漂移，又变成一个概念两个 owner）；
4. 装配层（`orchestrator/_init_mixin`）注入的角色 resolver 必须**带用户管理器、
   且每次读取重新解析**——不传 manager 时恒解析成内置 `default`（知识永远来自
   "没绑定角色"的兜底），回落「全局活跃角色」则是跨角色串内容。

数据全部合成（tmp_path 状态目录 + 假检索），不读真实知识库。
"""

from __future__ import annotations

import pytest

import proactive.llm_proactive as lp

# ── 1. 统一读接口本体 ──────────────────────────────────────

def _patch_sources(monkeypatch, *, hot="热点：某电影定档", knowledge="知识：米彩的咖啡馆在大理"):
    """把两个知识源换成可记录读数的假源。"""
    import shisi.knowledge.character_knowledge_service as cks
    import shisi.knowledge.hot_topics as ht

    seen: dict = {}

    def _hot(character_id, **kw):
        seen["hot_cid"] = character_id
        if isinstance(hot, Exception):
            raise hot
        return hot

    class _Svc:
        def get_knowledge_context(self, cid, query, top_k=3, **kw):
            seen["cid"] = cid
            seen["query"] = query
            seen["top_k"] = top_k
            seen["exclude_sources"] = kw.get("exclude_sources")
            if isinstance(knowledge, Exception):
                raise knowledge
            return knowledge

    monkeypatch.setattr(ht, "get_hot_context", _hot)
    monkeypatch.setattr(cks, "get_knowledge_service", lambda: _Svc())
    return seen


def test_door_requires_character_id(monkeypatch):
    """无角色 id 一律空串——绝不能退化成"读某个全局活跃角色"（跨角色串内容）。"""
    seen = _patch_sources(monkeypatch)
    assert lp.load_proactive_knowledge("") == ""
    assert lp.load_proactive_knowledge("   ") == ""
    assert seen == {}, "缺角色 id 时不应发生任何读取"


def test_door_prepends_hot_before_character_knowledge(monkeypatch):
    seen = _patch_sources(monkeypatch)
    out = lp.load_proactive_knowledge("micai")
    assert "某电影定档" in out and "米彩的咖啡馆" in out
    assert out.index("某电影定档") < out.index("米彩的咖啡馆"), "热点前置（更新鲜）"
    assert seen["cid"] == "micai" and seen["hot_cid"] == "micai"
    assert seen["query"] and seen["top_k"] >= 1
    # 卡片身份字段不得以「可分享的真实内容」回声（她会把自己的名字当新闻说）
    from shisi.core.services.prompt_builder import IDENTITY_KNOWLEDGE_SOURCES

    assert seen["exclude_sources"] == IDENTITY_KNOWLEDGE_SOURCES


def test_door_degrades_when_either_source_fails(monkeypatch):
    """一侧炸掉不得连带另一侧（旧实现整段包在一个 try 里，热点异常连带弃知识）。"""
    seen = _patch_sources(monkeypatch, hot=RuntimeError("池读取炸了"))
    out = lp.load_proactive_knowledge("micai")
    assert "米彩的咖啡馆" in out and seen["cid"] == "micai"

    seen = _patch_sources(monkeypatch, knowledge=RuntimeError("索引读取炸了"))
    out = lp.load_proactive_knowledge("micai")
    assert "某电影定档" in out


def test_door_prefers_injected_reader_over_direct_service(monkeypatch):
    """装配层已注入 `character_id→检索` 闭包时必须复用它（不得把它变成死属性）。"""
    seen = _patch_sources(monkeypatch, knowledge="不该走直连服务")
    out = lp.load_proactive_knowledge(
        "micai", knowledge_reader=lambda cid: f"由注入闭包读出的知识（cid={cid}）",
    )
    assert "由注入闭包读出的知识（cid=micai）" in out
    assert "不该走直连服务" not in out
    assert "cid" not in seen, "有注入 reader 时不应再直连知识服务"


# ── 2. 决策链真的吃到知识（当前生产链） ────────────────────

class _FakeEngine:
    def __init__(self, user_key: str = "", state_path: str = ""):
        self.user_key = user_key
        self.state_path = state_path
        self._knowledge_character_id = ""
        self._last_user_message = ""
        self.response_rate = None

    def save_state(self) -> None:
        pass

    def close(self) -> None:
        pass


@pytest.fixture()
def hub_tmp(tmp_path, monkeypatch):
    import proactive.ase_hub as hub_mod
    from proactive.ase_hub import ASEHub

    monkeypatch.setattr(hub_mod, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(hub_mod, "_INDEX_PATH", tmp_path / "index.json")
    hub = ASEHub(_FakeEngine)
    hub._state_dir = tmp_path
    return hub


@pytest.fixture()
def sched():
    from proactive.ase_engine import _local_now
    from proactive.scheduler import ProactiveScheduler

    s = ProactiveScheduler()
    h = _local_now().hour
    s._quiet_hours = ((h + 3) % 24, (h + 5) % 24)  # 当前小时落在静默窗外
    return s


def _stub_decision_deps(monkeypatch, events: list):
    import shisi.agent_plane.runtime as rt

    monkeypatch.setattr(lp, "read_web_proactive_config", lambda: {"enabled": True})
    monkeypatch.setattr(lp, "load_persona_hint", lambda cid="": "P:" + str(cid))
    monkeypatch.setattr(rt, "project_profile_for", lambda uk: {})
    monkeypatch.setattr(rt, "get_profile_prompt_block", lambda uk: "")
    monkeypatch.setattr(rt, "append_proactive_event", lambda **kw: events.append(kw))


def test_decision_chain_injects_knowledge_of_current_character(sched, hub_tmp, monkeypatch):
    """🔴 缺陷 H 主断言：决策 prompt 必须带上当轮角色的知识/热点。"""
    _stub_decision_deps(monkeypatch, [])
    sched._resolve_proactive_llm = lambda eng=None: object()

    reads: list = []

    def _door(character_id, **kw):
        reads.append(str(character_id))
        return "【知识】刘十三在小卖部门口等客"

    monkeypatch.setattr(lp, "load_proactive_knowledge", _door)
    ctxs: list = []
    monkeypatch.setattr(
        lp, "decide_proactive",
        lambda llm, ctx: ctxs.append(ctx) or {
            "should_contact": False, "message": "", "reason": "test", "wait_minutes": None,
        },
    )

    eng = hub_tmp.get("4:peer@im.wechat")
    eng._knowledge_character_id = "stale-character"
    monkeypatch.setattr(sched, "_resolve_character_id", lambda sk: "liu-shisan")
    sched._llm_proactive_one_user(hub_tmp, "4:peer@im.wechat")

    assert reads == ["liu-shisan"], (
        f"决策链未用当轮解析出的角色读知识（实际读={reads}）"
    )
    assert ctxs and "刘十三在小卖部门口等客" in ctxs[0], (
        "知识/热点从未进入当前 LLM 决策上下文（缺陷 H：只活在已旁路的 ASE 线）"
    )


def test_decision_chain_omits_section_when_no_knowledge(sched, hub_tmp, monkeypatch):
    """无知识 → 整段不出现（宁缺毋串，不注入占位标题）。"""
    _stub_decision_deps(monkeypatch, [])
    sched._resolve_proactive_llm = lambda eng=None: object()
    monkeypatch.setattr(lp, "load_proactive_knowledge", lambda character_id, **kw: "")
    ctxs: list = []
    monkeypatch.setattr(
        lp, "decide_proactive",
        lambda llm, ctx: ctxs.append(ctx) or {
            "should_contact": False, "message": "", "reason": "test", "wait_minutes": None,
        },
    )
    hub_tmp.get("4:peer@im.wechat")
    monkeypatch.setattr(sched, "_resolve_character_id", lambda sk: "micai")
    sched._llm_proactive_one_user(hub_tmp, "4:peer@im.wechat")
    assert ctxs
    assert "知识库" not in ctxs[0], "空知识时不得留下孤零零的段落标题"


def test_build_context_renders_knowledge_hint():
    ctx = lp.build_proactive_context(
        local_time="2026-09-27 10:00 Saturday",
        persona_hint="角色：米彩",
        knowledge_hint="热点：某电影定档",
    )
    assert "某电影定档" in ctx
    assert "不得声称你亲历现场" in ctx, "转述材料必须带防编造约束（她的经历不是新闻现场）"


# ── 3. 旧 ASE 分享线收口到同一 door ────────────────────────

def test_legacy_share_line_goes_through_the_same_door(monkeypatch):
    """ASE 线不得自己再拼一份「热点 + 知识」源选择逻辑（两处漂移=两个 owner）。"""
    from proactive.ase_engine import ASEEngine

    calls: list = []
    monkeypatch.setattr(
        lp, "load_proactive_knowledge",
        lambda character_id, **kw: calls.append((character_id, kw))
        or "合并后的真实内容——足够长以通过长度门槛的分享材料",
    )

    class _LLM:
        queries: list = []

        def chat_sync(self, query="", **kw):
            self.queries.append(str(query))
            return "我刚看到个片子定档了"

    llm = _LLM()
    eng = ASEEngine(llm_gateway=llm)
    func = lambda cid: "注入闭包的知识"  # noqa: E731
    eng._knowledge_share_func = func
    eng._knowledge_character_id = "micai"

    out = eng._try_knowledge_share()
    assert out is not None and out["type"] == "share"
    assert calls and calls[0][0] == "micai"
    assert calls[0][1].get("knowledge_reader") is func, (
        "装配层注入的检索闭包必须经 door 复用，而不是被绕过"
    )
    assert "我刚看到个片子定档了" in str(out["message"])
    assert llm.queries and "合并后的真实内容" in llm.queries[0]


# ── 4. 装配层注入的角色 resolver 必须带 user_manager ────────

def _init_ase_hub(tmp_path, monkeypatch):
    """跑真实装配入口 `_init_ase_and_scheduler`，返回 ASEHub（引擎逐实例注入知识）。"""
    from types import SimpleNamespace

    from orchestrator._init_mixin import _InitPhasesMixin as Mixin
    from proactive import ase_hub

    hub_dir = tmp_path / "ase_states"
    monkeypatch.setattr(ase_hub, "_STATE_DIR", hub_dir)
    monkeypatch.setattr(ase_hub, "_INDEX_PATH", hub_dir / "index.json")
    self_ = SimpleNamespace(components={"llm": None})
    cfg = SimpleNamespace(proactive=SimpleNamespace(
        max_daily_messages=8,
        min_interval_minutes=30,
        cooldown_after_reply_minutes=5,
        urgency_threshold=5.0,
    ))
    Mixin._init_ase_and_scheduler(self_, cfg, {})
    return self_.components["ase"]


def test_engine_factory_passes_user_manager_to_resolver(tmp_path, monkeypatch):
    """🔴 不传 user_manager 时 resolver 恒回落内置 `default` —— 生产实况：每人引擎的
    `_knowledge_character_id` 都是 default，知识只可能来自"没有角色"的兜底。
    """
    import api.deps as deps_mod
    import utils.character_resolver as resolver_mod

    class _Mgr:
        def get_user_character(self, key: str) -> str:
            return "micai"

    mgr = _Mgr()
    monkeypatch.setattr(deps_mod.deps, "gf", mgr, raising=False)

    seen: dict = {}

    def _resolve(key, user_manager=None):
        seen["key"] = key
        seen["user_manager"] = user_manager
        return user_manager.get_user_character(key) if user_manager is not None else "default"

    monkeypatch.setattr(resolver_mod, "resolve_character_id", _resolve)

    eng = _init_ase_hub(tmp_path, monkeypatch).get("4:wxid_x@im.wechat")
    assert seen["user_manager"] is mgr, (
        "角色 resolver 未收到用户管理器（拿不到绑定表 → 恒解析成 default）"
    )
    assert eng._knowledge_character_id == "micai"


def test_share_reader_rebinds_to_current_character(tmp_path, monkeypatch):
    """切角色后旧引擎不得继续供旧角色的内容，也绝不回落「全局活跃角色」。"""
    import api.deps as deps_mod
    import shisi.knowledge.character_knowledge_service as cks
    from shisi.core.services.prompt_builder import IDENTITY_KNOWLEDGE_SOURCES

    class _Mgr:
        cid = "micai"

        def get_user_character(self, key: str) -> str:
            return self.cid

    class _Active:
        def get_active_id(self) -> str:
            return "someone-elses-card"

    class _Reg:
        character_manager = _Active()

    mgr = _Mgr()
    monkeypatch.setattr(deps_mod.deps, "gf", mgr, raising=False)
    monkeypatch.setattr(deps_mod.deps, "shisi_reg", _Reg(), raising=False)

    calls: list = []

    class _Svc:
        def get_knowledge_context(self, cid, query, top_k=3, **kw):
            calls.append({"cid": cid, "exclude_sources": kw.get("exclude_sources")})
            return f"知识：{cid}" * 12

    monkeypatch.setattr(cks, "get_knowledge_service", lambda: _Svc())

    eng = _init_ase_hub(tmp_path, monkeypatch).get("4:wxid_x@im.wechat")
    eng._knowledge_share_func("")
    mgr.cid = "liu-shisan"
    eng._knowledge_share_func("")

    cids = [c["cid"] for c in calls]
    assert cids == ["micai", "liu-shisan"], (
        f"分享线用的是引擎创建时缓存的角色（旧角色内容当谈资）：{cids}"
    )
    assert "someone-elses-card" not in cids, "不得回落全局活跃角色（跨角色串内容）"
    assert all(c["exclude_sources"] == IDENTITY_KNOWLEDGE_SOURCES for c in calls), (
        "装配注入的检索闭包同样不得回声卡片身份字段"
    )


def test_share_reader_never_falls_back_to_global_character(tmp_path, monkeypatch):
    """解析不到角色（无绑定 / 解析异常）时宁可不读。

    旧实现在此回落 `CharacterManager.get_active_id()`（全局活跃角色）——
    把**别人角色**的知识当成本会话的谈资，多用户下必然串。
    """
    import api.deps as deps_mod
    import shisi.knowledge.character_knowledge_service as cks
    import utils.character_resolver as resolver_mod

    class _Active:
        def get_active_id(self) -> str:
            return "someone-elses-card"

    class _Reg:
        character_manager = _Active()

    monkeypatch.setattr(deps_mod.deps, "shisi_reg", _Reg(), raising=False)

    calls: list = []

    class _Svc:
        def get_knowledge_context(self, cid, query, top_k=3, **kw):
            calls.append(cid)
            return f"知识：{cid}" * 12

    monkeypatch.setattr(cks, "get_knowledge_service", lambda: _Svc())

    def _boom(key, user_manager=None):
        raise RuntimeError("绑定表不可用")

    monkeypatch.setattr(resolver_mod, "resolve_character_id", _boom)

    eng = _init_ase_hub(tmp_path, monkeypatch).get("4:wxid_x@im.wechat")
    assert eng._knowledge_share_func("") == ""
    assert calls == [], f"解析不到角色却去读了全局活跃角色：{calls}"

