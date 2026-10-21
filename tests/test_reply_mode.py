"""回复模式（沉浸式真人 / 小说式）回归 —— 2026-09-19 用户要求。

「在 web 端手动控制，分成两个模式：一个是沉浸式聊天（像真人真正在聊天），
一个是像小说一样（带上动作、神态这些东西）。」

生产实证的背景：同一角色会在两种风格间**随机跳**（括号动作描写与纯口语混发），
因为 system prompt 里同时存在互相冲突的格式要求、没有明确二选一。
"""

from __future__ import annotations

import pytest


def _use_tmp(monkeypatch, tmp_path):
    from utils import reply_mode as rm

    monkeypatch.setattr(rm, "_CONFIG_PATH", tmp_path / "scheduler_config.json")
    return rm


# ── 读写与默认值 ──────────────────────────────────────────

def test_default_is_immersive(monkeypatch, tmp_path):
    rm = _use_tmp(monkeypatch, tmp_path)
    assert rm.read_reply_mode() == rm.REPLY_MODE_IMMERSIVE


def test_roundtrip(monkeypatch, tmp_path):
    rm = _use_tmp(monkeypatch, tmp_path)
    rm.write_reply_mode(rm.REPLY_MODE_NOVEL)
    assert rm.read_reply_mode() == rm.REPLY_MODE_NOVEL
    rm.write_reply_mode(rm.REPLY_MODE_IMMERSIVE)
    assert rm.read_reply_mode() == rm.REPLY_MODE_IMMERSIVE


def test_write_preserves_other_keys(monkeypatch, tmp_path):
    """reply_mode 与 quiet_hours 共用一份跨 worker 配置文件。"""
    import json

    rm = _use_tmp(monkeypatch, tmp_path)
    (tmp_path / "scheduler_config.json").write_text(
        json.dumps({"quiet_hours": {"start": 23, "end": 7}}), encoding="utf-8",
    )
    rm.write_reply_mode(rm.REPLY_MODE_NOVEL)

    data = json.loads((tmp_path / "scheduler_config.json").read_text(encoding="utf-8"))
    assert data["quiet_hours"] == {"start": 23, "end": 7}
    assert data["reply_mode"] == "novel"


def test_invalid_value_rejected(monkeypatch, tmp_path):
    rm = _use_tmp(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        rm.write_reply_mode("poetry")


def test_invalid_stored_value_falls_back(monkeypatch, tmp_path):
    rm = _use_tmp(monkeypatch, tmp_path)
    (tmp_path / "scheduler_config.json").write_text('{"reply_mode": "poetry"}', encoding="utf-8")
    assert rm.read_reply_mode() == rm.REPLY_MODE_IMMERSIVE


def test_broken_file_falls_back(monkeypatch, tmp_path):
    rm = _use_tmp(monkeypatch, tmp_path)
    (tmp_path / "scheduler_config.json").write_text("{ not json", encoding="utf-8")
    assert rm.read_reply_mode() == rm.REPLY_MODE_IMMERSIVE


# ── 两种模式的指令必须真正互斥 ────────────────────────────

def test_immersive_forbids_action_narration(monkeypatch, tmp_path):
    rm = _use_tmp(monkeypatch, tmp_path)
    text = rm.reply_mode_instruction("immersive")
    assert "严禁括号动作" in text or "严禁" in text and "括号" in text
    assert "沉浸式" in text
    # 关键约束：不得编造对方处境（生产里「路上堵不堵/热不热」的根因）
    assert "编造" in text
    assert "否认" in text          # 对方否认过的情境必须放弃


def test_novel_allows_action_narration(monkeypatch, tmp_path):
    rm = _use_tmp(monkeypatch, tmp_path)
    text = rm.reply_mode_instruction("novel")
    assert "动作" in text and "神态" in text
    assert "允许" in text
    # 小说式同样不得凭空设定对方处境
    assert "否认" in text


def test_two_modes_are_different(monkeypatch, tmp_path):
    rm = _use_tmp(monkeypatch, tmp_path)
    assert rm.reply_mode_instruction("immersive") != rm.reply_mode_instruction("novel")


def test_instruction_follows_stored_mode(monkeypatch, tmp_path):
    rm = _use_tmp(monkeypatch, tmp_path)
    rm.write_reply_mode("novel")
    assert rm.reply_mode_instruction() == rm.reply_mode_instruction("novel")
    rm.write_reply_mode("immersive")
    assert rm.reply_mode_instruction() == rm.reply_mode_instruction("immersive")


def test_labels_exist_for_both_modes(monkeypatch, tmp_path):
    rm = _use_tmp(monkeypatch, tmp_path)
    for mode in rm.REPLY_MODES:
        assert rm.reply_mode_label(mode)


# ── 沉浸式的「非共处」约束（2026-09-19 第二轮修复）─────────

def test_immersive_forbids_physical_co_presence(monkeypatch, tmp_path):
    """用户复报「还是展现出小说的感觉，一个人怎么会面对面发消息」。

    根因：角色卡把关系设定成**物理共处**（当时绑定角色 62105bca 的 scenario 写
    「你刚从公交车上下来…她站在巷口等你…转身走在前面带路」，description 写
    「从小一起长大的青梅竹马…她家就在巷子尽头那栋楼」），模型据此把「关系设定」
    演成「此时此地的舞台」，写出「我尝一口」「那我走」「那喝口茶消消食」。
    故沉浸式指令必须显式声明非共处。
    """
    rm = _use_tmp(monkeypatch, tmp_path)
    text = rm.reply_mode_instruction("immersive")
    assert "不在同一个地方" in text          # 非共处
    assert "不能做动作" in text              # 只能说话
    # 实际踩到的句子应作为反例被写进指令
    assert "我尝一口" in text
    assert "那我走" in text
    # 场景设定必须被降级为「背景」，而不是此刻正在发生
    assert "背景" in text


def test_novel_is_not_hit_by_co_presence_ban(monkeypatch, tmp_path):
    """小说式本就允许画面感，不应被非共处的措辞误伤。"""
    rm = _use_tmp(monkeypatch, tmp_path)
    assert "不在同一个地方" not in rm.reply_mode_instruction("novel")


# ══════════════════════════════════════════════════════════════
#  按用户分层覆盖（R7 2026-10-21）
#
#  裁决：模式必须能**按用户**显式切换，而不是一个全站单例。真源仍是
#  `data/scheduler_config.json` 的 `reply_mode`（降级为**全局默认**），
#  新增 per-user 覆盖表 `user_reply_mode(user_key PRIMARY KEY, mode, updated_at)`，
#  落在 `shisi` 侧同一 sqlite.db（与 user_profile / user_facts 同库同隔离域）。
#  user_key = v1.28 完整会话键 `N:wxid`（`StructuredMemory.user_key_from_session`）。
# ══════════════════════════════════════════════════════════════


def _use_tmp_with_db(monkeypatch, tmp_path):
    """同时隔离配置文件与覆盖库（覆盖库路径也走模块常量，防写真实 data/sqlite.db）。"""
    from utils import reply_mode as rm

    monkeypatch.setattr(rm, "_CONFIG_PATH", tmp_path / "scheduler_config.json")
    monkeypatch.setattr(rm, "_OVERRIDE_DB_PATH", tmp_path / "reply_mode_override.db")
    return rm


def test_read_without_user_key_is_global_default(monkeypatch, tmp_path):
    """`None`/空串保持旧行为（后台线/提醒线无会话上下文的调用点不变）。"""
    rm = _use_tmp_with_db(monkeypatch, tmp_path)
    rm.write_reply_mode("novel")
    assert rm.read_reply_mode() == "novel"
    assert rm.read_reply_mode(None) == "novel"
    assert rm.read_reply_mode("") == "novel"


def test_per_user_override_wins_over_global(monkeypatch, tmp_path):
    rm = _use_tmp_with_db(monkeypatch, tmp_path)
    rm.write_reply_mode("immersive")
    rm.write_reply_mode("novel", user_key="2:alice@im.wechat")
    assert rm.read_reply_mode("2:alice@im.wechat") == "novel"
    # 全局默认不受影响 → 另一个用户仍是沉浸式
    assert rm.read_reply_mode("3:bob@im.wechat") == "immersive"
    assert rm.read_reply_mode() == "immersive"


def test_per_user_override_isolated_between_users(monkeypatch, tmp_path):
    """多用户隔离硬约束：A 的覆盖绝不泄漏给 B。"""
    rm = _use_tmp_with_db(monkeypatch, tmp_path)
    rm.write_reply_mode("novel", user_key="2:alice@im.wechat")
    rm.write_reply_mode("immersive", user_key="4:carol@im.wechat")
    assert rm.read_reply_mode("2:alice@im.wechat") == "novel"
    assert rm.read_reply_mode("4:carol@im.wechat") == "immersive"
    assert rm.read_reply_mode("9:stranger@im.wechat") == rm.DEFAULT_REPLY_MODE


def test_per_user_override_persists_across_reads(monkeypatch, tmp_path):
    """覆盖落库（不是进程内缓存）——换进程/换 worker 仍生效。"""
    rm = _use_tmp_with_db(monkeypatch, tmp_path)
    rm.write_reply_mode("novel", user_key="2:alice@im.wechat")
    # 模拟另一个 worker：清掉模块级缓存（若有）后重读
    import importlib

    mod = importlib.reload(importlib.import_module("utils.reply_mode"))
    monkeypatch.setattr(mod, "_CONFIG_PATH", tmp_path / "scheduler_config.json")
    monkeypatch.setattr(mod, "_OVERRIDE_DB_PATH", tmp_path / "reply_mode_override.db")
    assert mod.read_reply_mode("2:alice@im.wechat") == "novel"


def test_clear_per_user_override_falls_back_to_global(monkeypatch, tmp_path):
    rm = _use_tmp_with_db(monkeypatch, tmp_path)
    rm.write_reply_mode("novel", user_key="2:alice@im.wechat")
    rm.clear_reply_mode_override("2:alice@im.wechat")
    assert rm.read_reply_mode("2:alice@im.wechat") == rm.DEFAULT_REPLY_MODE


def test_per_user_invalid_mode_rejected(monkeypatch, tmp_path):
    rm = _use_tmp_with_db(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        rm.write_reply_mode("poetry", user_key="2:alice@im.wechat")


def test_per_user_stored_invalid_value_falls_back(monkeypatch, tmp_path):
    """库里被手工写坏 → 回落全局默认，不得抛异常炸主链。"""
    rm = _use_tmp_with_db(monkeypatch, tmp_path)
    rm.write_reply_mode("novel", user_key="2:alice@im.wechat")
    import sqlite3

    conn = sqlite3.connect(str(tmp_path / "reply_mode_override.db"))
    conn.execute("UPDATE user_reply_mode SET mode='poetry' WHERE user_key=?", ("2:alice@im.wechat",))
    conn.commit()
    conn.close()
    assert rm.read_reply_mode("2:alice@im.wechat") == rm.DEFAULT_REPLY_MODE


def test_override_db_failure_falls_back_to_global(monkeypatch, tmp_path):
    """覆盖库不可用（路径指向目录）→ 回落全局，绝不炸主链。"""
    rm = _use_tmp_with_db(monkeypatch, tmp_path)
    monkeypatch.setattr(rm, "_OVERRIDE_DB_PATH", tmp_path)  # 目录，无法当 sqlite 打开
    rm.write_reply_mode("novel")
    assert rm.read_reply_mode("2:alice@im.wechat") == "novel"


def test_write_reply_mode_failure_raises(monkeypatch, tmp_path):
    """写盘失败必须**上抛**：旧实现只 WARNING 后 return mode，调用方据此记
    「回复模式已切换」= 假成功（复核 2026-10-21 指出的既有缺陷）。"""
    rm = _use_tmp_with_db(monkeypatch, tmp_path)

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(rm.json_state, "update_json", _boom)
    with pytest.raises(OSError):
        rm.write_reply_mode("novel")


def test_reply_mode_instruction_accepts_user_key(monkeypatch, tmp_path):
    rm = _use_tmp_with_db(monkeypatch, tmp_path)
    rm.write_reply_mode("immersive")
    rm.write_reply_mode("novel", user_key="2:alice@im.wechat")
    assert rm.reply_mode_instruction(user_key="2:alice@im.wechat") == rm._INSTRUCTION_NOVEL
    assert rm.reply_mode_instruction() == rm._INSTRUCTION_IMMERSIVE


def test_override_db_path_is_inside_data_dir():
    """默认覆盖库与 user_profile/user_facts 同库（sqlite.db），不新开一个真源。

    ⚠️ conftest 的 `isolate_runtime_state_files` 会把该常量重定向到沙箱
    （防测试写真实库）——故本用例断言**源码常量**，不读运行期被替换的值。
    """
    import inspect

    from utils import reply_mode as rm

    src = inspect.getsource(rm)
    assert '_OVERRIDE_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "sqlite.db"' in src, (
        "覆盖库必须与 user_profile/user_facts 同库（data/sqlite.db）"
    )


def test_override_isolated_in_tests():
    """反向保护：测试环境里该常量必须已被 conftest 重定向（否则会污染真实库）。"""
    from utils import reply_mode as rm

    assert "sqlite.db" not in str(rm._OVERRIDE_DB_PATH), (
        "conftest 未隔离 _OVERRIDE_DB_PATH —— 测试会写开发机真实 data/sqlite.db"
    )


# ── 一轮内单次解析：编排器把 mode 存进 ctx 并全程复用 ─────────


def test_orchestrator_resolves_mode_once_per_turn():
    """源码级防漂移：`_prepare_context` 必须把模式解析结果写进 ctx，
    下游（reply_mode 指令/守卫/兜底/流式）**不得**再各自热读。"""
    import inspect

    from orchestrator._stream_mixin import _StreamPipelineMixin
    from orchestrator.optimized_orchestrator import OptimizedOrchestrator

    ctx_src = inspect.getsource(OptimizedOrchestrator._prepare_context)
    assert 'ctx_mode' in ctx_src or '"reply_mode"' in ctx_src, "prepare 未把模式写进 ctx"

    finalize_src = inspect.getsource(OptimizedOrchestrator._finalize_reply)
    assert "reply_mode" in finalize_src

    stream_src = inspect.getsource(_StreamPipelineMixin.process_message_stream)
    # 流式终裁必须把 ctx 里的模式透传给 _finalize_reply（不得内部再热读）
    assert "reply_mode=" in stream_src


def test_finalize_reply_accepts_explicit_reply_mode():
    """`_finalize_reply(reply_mode=...)` 显式入参；缺省才回落热读。"""
    import inspect

    from orchestrator.optimized_orchestrator import OptimizedOrchestrator

    params = inspect.signature(OptimizedOrchestrator._finalize_reply).parameters
    assert "reply_mode" in params
    assert params["reply_mode"].default is None


def test_character_aggregate_accepts_reply_mode():
    """注入侧同样按一轮一个模式（mes_example/creator_notes 的剥除开关）。"""
    import inspect

    from shisi.core.models.character_aggregate import CharacterAggregate

    params = inspect.signature(CharacterAggregate.build_system_prompt).parameters
    assert "reply_mode" in params


def test_persona_service_accepts_reply_mode():
    import inspect

    from shisi.application.persona_service import PersonaService

    params = inspect.signature(PersonaService.build_system_prompt).parameters
    assert "reply_mode" in params


# ── API：admin 作用域（global/user）+ 自助端点（R7 2026-10-21）────


def test_proactive_request_accepts_scope_fields():
    """admin 端点必须接受 reply_mode_scope / user_key（扩展模型）。"""
    from api.routers.training_routes import ProactiveConfigRequestScoped

    fields = ProactiveConfigRequestScoped.model_fields
    assert "reply_mode_scope" in fields
    assert "user_key" in fields
    # 旧字段一个不丢（前端既有调用不受影响）
    for legacy in ("reply_mode", "threshold", "max_daily", "llm_proactive_enabled"):
        assert legacy in fields


def test_self_route_registered_without_admin_gate():
    """普通用户入口：独立端点存在，且**不**依赖 admin 角色。"""
    import inspect

    from api.routers import training_routes as tr

    get_src = inspect.getsource(tr.get_my_reply_mode)
    post_src = inspect.getsource(tr.set_my_reply_mode)
    for src in (get_src, post_src):
        assert "get_current_user_id" in src, "自助端点必须从 JWT 派生 user_id"
        assert "require_role" not in src, "自助端点不得挂 admin 角色门"
        assert "_ase" not in src, "自助端点不得耦合主动引擎 503 门"
    # 服务端派生 user_key：不得接受客户端传 user_key
    assert "user_key" not in tr.UserReplyModeRequest.model_fields, (
        "自助端点不得接受客户端 user_key（等于允许改别人）"
    )


def test_self_routes_present_in_router():
    from api.routers.training_routes import router

    paths = {getattr(r, "path", "") for r in router.routes}
    assert "/api/user/reply-mode" in paths


def test_admin_scope_user_without_key_is_400(monkeypatch):
    """scope=user 缺 user_key → 400（不猜键、不静默写全局）。"""
    import asyncio

    from api.routers import training_routes as tr

    class _Ase:
        def apply_runtime_config(self, **kw):
            pass

        def get_runtime_config(self):
            return {"max_daily_messages": 8}

        _urgency_threshold = 2.0

    monkeypatch.setattr(tr.deps, "orch", type("O", (), {"_ase": _Ase()})())
    req = tr.ProactiveConfigRequestScoped(reply_mode="novel", reply_mode_scope="user")
    with pytest.raises(Exception) as exc:
        asyncio.run(tr.update_proactive_config(req, _auth=True, _admin=(1, None)))
    assert "400" in str(exc.value) or "user_key" in str(exc.value)


def test_write_failure_surfaces_as_http_error(monkeypatch, tmp_path):
    """写盘失败必须变成可见错误（旧实现静默吞掉 → 前端假成功）。"""
    import asyncio

    from api.routers import training_routes as tr
    from utils import reply_mode as rm

    monkeypatch.setattr(rm, "_CONFIG_PATH", tmp_path / "scheduler_config.json")

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(rm.json_state, "update_json", _boom)

    class _Ase:
        def apply_runtime_config(self, **kw):
            pass

        def get_runtime_config(self):
            return {"max_daily_messages": 8}

        _urgency_threshold = 2.0

    monkeypatch.setattr(tr.deps, "orch", type("O", (), {"_ase": _Ase()})())
    req = tr.ProactiveConfigRequestScoped(reply_mode="novel", reply_mode_scope="global")
    with pytest.raises(Exception) as exc:
        asyncio.run(tr.update_proactive_config(req, _auth=True, _admin=(1, None)))
    assert "500" in str(exc.value) or "写入失败" in str(exc.value)


# ── 一轮内单次解析：行为级 ─────────────────────────────


def test_user_override_changes_immersion_per_user(monkeypatch, tmp_path):
    """A 覆盖为 novel 时，A 的 prompt 保留括号；B 仍沉浸式剥除。"""
    rm = _use_tmp_with_db(monkeypatch, tmp_path)
    rm.write_reply_mode("immersive")  # 全局
    rm.write_reply_mode("novel", user_key="2:alice@im.wechat")

    from shisi.core.models.character_aggregate import CharacterAggregate

    agg = CharacterAggregate(
        id="rm01", name="测试角色", description="她。",
        # `_format_dialogue_examples` 只读 `source_data["mes_example"]`（与生产卡同构）；
        # 顶层 `mes_example` 字段在本渲染路径不被消费。
        source_data={"mes_example": "用户：早\n测试角色：（笑了笑）嗯。"},
    )
    a_prompt = agg.build_system_prompt(reply_mode=rm.read_reply_mode("2:alice@im.wechat"))
    b_prompt = agg.build_system_prompt(reply_mode=rm.read_reply_mode("3:bob@im.wechat"))
    assert "（笑了笑）" in a_prompt, "A（novel）的示例动作被误剥"
    assert "（笑了笑）" not in b_prompt, "B（immersive）的示例动作漏剥"


def test_explicit_mode_beat_hot_read(monkeypatch, tmp_path):
    """显式入参优先于热读：一轮内单次解析的语义基础。"""
    rm = _use_tmp_with_db(monkeypatch, tmp_path)
    rm.write_reply_mode("immersive")

    from utils.reply_mode import REPLY_MODE_NOVEL

    assert rm.reply_mode_instruction("novel") == rm.reply_mode_instruction(REPLY_MODE_NOVEL)
    assert rm.reply_mode_instruction("novel") != rm.reply_mode_instruction()


# ── admin「指定用户」的**写入键形态**（P0 复核 2026-10-22）──────────
#
# 独立复核端到端实测：前端下拉把 `/api/admin/users` 的**数字 id** 当 user_key
# 发来（`RoleSettingsTabs.tsx` 的 `String(replyModeTargetUser)`），后端原样当键
# 写库（`rows: [('3','novel')]`）；而运行时 `_resolve_turn_reply_mode` 按
# **完整会话键** `N:wxid@im.wechat` 读（`user_key_from_session` 明确「禁止剥掉
# owner」）→ `runtime read '3:o9cq80@im.wechat' -> immersive`。
# 即 admin 该项功能完全无效，却回执 `status=ok` = **假成功**。
#
# 修复口径：admin 侧把「数字 user_id」经 `wechat_bindings` 确定性还原成完整会话键；
# 已含 `:` 的完整会话键原样透传（脚本直呼契约不变）；两者都不是 → 400，不猜键。


class _FakeScalars:
    def __init__(self, val):
        self._val = val

    def first(self):
        return self._val


class _FakeResult:
    def __init__(self, val):
        self._result = _FakeScalars(val)

    def scalars(self):
        return self._result


class _FakeDB:
    """最小 db 替身：`_user_session_key_for` 只消费 `execute(...).scalars().first()`。"""

    def __init__(self, wxid):
        self._wxid = wxid
        self.queries = 0

    async def execute(self, *a, **k):
        self.queries += 1
        return _FakeResult(self._wxid)


def _stub_admin_engine(monkeypatch, tr):
    """admin 端点前置：mock `deps.orch._ase`（403/503 门）与回执构造函数。"""

    class _Ase:
        def apply_runtime_config(self, **kw):
            pass

        def get_runtime_config(self):
            return {"max_daily_messages": 8}

        _urgency_threshold = 2.0

    monkeypatch.setattr(tr.deps, "orch", type("O", (), {"_ase": _Ase()})())

    async def _fake_cfg(_auth=True, _admin=None):
        return {"reply_mode": "immersive"}

    monkeypatch.setattr(tr, "get_proactive_config", _fake_cfg)


def test_admin_user_scope_numeric_id_resolves_to_session_key(monkeypatch, tmp_path):
    """P0 复核：admin 传**数字 user_id** → 必须还原成 `N:wxid` 再写覆盖。

    否则运行时（按会话键读）永远读不到，而回执 `status=ok` 骗前端。
    """
    import asyncio

    from api.routers import training_routes as tr

    rm = _use_tmp_with_db(monkeypatch, tmp_path)
    rm.write_reply_mode("immersive")  # 全局默认
    _stub_admin_engine(monkeypatch, tr)

    req = tr.ProactiveConfigRequestScoped(
        reply_mode="novel", reply_mode_scope="user", user_key="3",
    )
    res = asyncio.run(
        tr.update_proactive_config(
            req, _auth=True, _admin=(1, None), db=_FakeDB("o9cq80@im.wechat"),
        )
    )

    assert res["status"] == "ok"
    # 运行时视角：按完整会话键读 → 覆盖生效
    assert rm.read_reply_mode("3:o9cq80@im.wechat") == "novel", (
        "admin 写入未还原成完整会话键——运行时按 N:wxid 读，永远读不到"
    )
    # 裸数字键不得成为真源（写进去也读不到 = 假成功）
    assert rm.read_reply_mode("3") == "immersive", "裸数字 id 不得被当作覆盖键写入"
    # 回执必须报告**真实生效**的键形态，而不是原样回显数字
    assert res["user_key"] == "3:o9cq80@im.wechat", (
        f"回执 user_key 未反映真实写入键：{res['user_key']!r}"
    )


def test_admin_user_scope_full_session_key_passthrough(monkeypatch, tmp_path):
    """已含 `:` 的完整会话键原样写入（脚本/API 直呼路径契约不变，不反弹）。"""
    import asyncio

    from api.routers import training_routes as tr

    rm = _use_tmp_with_db(monkeypatch, tmp_path)
    rm.write_reply_mode("immersive")
    _stub_admin_engine(monkeypatch, tr)

    req = tr.ProactiveConfigRequestScoped(
        reply_mode="novel", reply_mode_scope="user", user_key="7:peer@im.wechat",
    )
    db = _FakeDB("o9cq80@im.wechat")  # 不应被查（已是完整会话键）
    asyncio.run(
        tr.update_proactive_config(req, _auth=True, _admin=(1, None), db=db)
    )

    assert rm.read_reply_mode("7:peer@im.wechat") == "novel"
    assert rm.read_reply_mode("7") == "immersive"
    assert db.queries == 0, "完整会话键不应再查绑定表（无谓 DB 往返）"


def test_admin_user_scope_numeric_id_without_binding_is_400(monkeypatch, tmp_path):
    """数字 user_id 查不到绑定 → 400（不猜键、不静默写全局）。"""
    import asyncio

    from api.routers import training_routes as tr

    rm = _use_tmp_with_db(monkeypatch, tmp_path)
    rm.write_reply_mode("immersive")
    _stub_admin_engine(monkeypatch, tr)

    req = tr.ProactiveConfigRequestScoped(
        reply_mode="novel", reply_mode_scope="user", user_key="3",
    )
    with pytest.raises(Exception) as exc:
        asyncio.run(
            tr.update_proactive_config(
                req, _auth=True, _admin=(1, None), db=_FakeDB(""),
            )
        )
    assert "400" in str(exc.value) or "绑定" in str(exc.value)
    # 未绑定不得写入任何覆盖行
    assert rm.read_reply_mode("3") == "immersive"


def test_admin_user_scope_invalid_key_shape_is_400(monkeypatch, tmp_path):
    """既非数字 id 也非完整会话键 → 400（不把垃圾串当真源键）。"""
    import asyncio

    from api.routers import training_routes as tr

    rm = _use_tmp_with_db(monkeypatch, tmp_path)
    rm.write_reply_mode("immersive")
    _stub_admin_engine(monkeypatch, tr)

    req = tr.ProactiveConfigRequestScoped(
        reply_mode="novel", reply_mode_scope="user", user_key="alice",
    )
    with pytest.raises(Exception) as exc:
        asyncio.run(
            tr.update_proactive_config(
                req, _auth=True, _admin=(1, None), db=_FakeDB("o9cq80@im.wechat"),
            )
        )
    assert "400" in str(exc.value)
    assert rm.read_reply_mode("alice") == "immersive"
