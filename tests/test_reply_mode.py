"""回复模式（沉浸式真人 / 小说式）回归 —— 2026-09-19 用户要求。

「在 web 端手动控制，分成两个模式：一个是沉浸式聊天（像真人真正在聊天），
一个是像小说一样（带上动作、神态这些东西）。」

生产实证的背景：同一角色会在两种风格间**随机跳**（括号动作描写与纯口语混发），
因为 system prompt 里同时存在互相冲突的格式要求、没有明确二选一。

2026-11-08 D-C 变更：`user_reply_mode` 按人覆盖层整体退役，模式改由
「我 × 这个角色」快照承接（`utils.character_settings.reply_mode`）。解析链：
快照 → 全局默认（`data/scheduler_config.json`）→ 沉浸式兜底。本文件只保留
**全局真源 + 指令内容 + 快照解析** 三部分；快照的 API 往返与旧端点退役断言
见 `tests/test_my_settings_reply_mode_20261108.py`。
"""

from __future__ import annotations

import pytest


def _use_tmp(monkeypatch, tmp_path):
    from utils import reply_mode as rm

    monkeypatch.setattr(rm, "_CONFIG_PATH", tmp_path / "scheduler_config.json")
    return rm


# ── 全局真源：读写与默认值 ────────────────────────────────

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


def test_write_reply_mode_failure_raises(monkeypatch, tmp_path):
    """写盘失败必须**上抛**：旧实现只 WARNING 后 return mode，调用方据此记
    「回复模式已切换」= 假成功（复核 2026-10-21 指出的既有缺陷）。"""
    rm = _use_tmp(monkeypatch, tmp_path)

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(rm.json_state, "update_json", _boom)
    with pytest.raises(OSError):
        rm.write_reply_mode("novel")


def test_config_path_is_inside_data_dir():
    """全局真源必须是跨 worker 的那份运行时配置（不新开配置文件）。"""
    import inspect

    from utils import reply_mode as rm

    src = inspect.getsource(rm)
    assert '_CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "scheduler_config.json"' in src


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


# ── 快照解析：没有会话/角色上下文时回落全局 ────────────────

def test_read_without_user_key_is_global_default(monkeypatch, tmp_path):
    """`None`/空串保持旧行为（后台线/提醒线无会话上下文的调用点不变）。"""
    rm = _use_tmp(monkeypatch, tmp_path)
    rm.write_reply_mode("novel")
    assert rm.read_reply_mode() == "novel"
    assert rm.read_reply_mode(None) == "novel"
    assert rm.read_reply_mode("") == "novel"


def test_snapshot_isolated_between_users_and_characters(monkeypatch, tmp_path):
    """多用户 × 多角色隔离：A 的某个角色改成小说式，不泄漏给任何其他组合。"""
    from utils import character_settings as cs

    rm = _use_tmp(monkeypatch, tmp_path)
    rm.write_reply_mode("immersive")
    cs.write("2:alice@im.wechat", "ch_x", {"reply_mode": "novel"})

    assert rm.read_reply_mode("2:alice@im.wechat", "ch_x") == "novel"
    assert rm.read_reply_mode("2:alice@im.wechat", "ch_y") == "immersive"
    assert rm.read_reply_mode("3:bob@im.wechat", "ch_x") == "immersive"
    assert rm.read_reply_mode() == "immersive"


def test_reply_mode_instruction_accepts_snapshot_keys(monkeypatch, tmp_path):
    from utils import character_settings as cs

    rm = _use_tmp(monkeypatch, tmp_path)
    rm.write_reply_mode("immersive")
    cs.write("2:alice@im.wechat", "ch_x", {"reply_mode": "novel"})
    assert (
        rm.reply_mode_instruction(user_key="2:alice@im.wechat", character_id="ch_x")
        == rm._INSTRUCTION_NOVEL
    )
    assert rm.reply_mode_instruction() == rm._INSTRUCTION_IMMERSIVE


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


def test_orchestrator_resolver_carries_character_id():
    """2026-11-08：一轮单次解析必须带上角色 id（快照主键的另一半）。"""
    import inspect

    from orchestrator.optimized_orchestrator import OptimizedOrchestrator

    params = inspect.signature(OptimizedOrchestrator._resolve_turn_reply_mode).parameters
    assert "character_id" in params
    src = inspect.getsource(OptimizedOrchestrator._prepare_context)
    assert "_resolve_turn_reply_mode(session_id, character_id)" in src


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


# ── admin 写面：只剩全局默认 ──────────────────────────────


def test_proactive_request_has_reply_mode_without_user_scope():
    """2026-11-08：admin 端点的 `reply_mode_scope=user` 写法退役。

    `ProactiveConfigRequest` 仍接受 `reply_mode`（写**全局默认**）；
    `ProactiveConfigRequestScoped`（R7 引入的作用域扩展）不再存在。
    """
    from api.main_routes import ProactiveConfigRequest
    from api.routers import training_routes

    assert "reply_mode" in ProactiveConfigRequest.model_fields
    assert not hasattr(training_routes, "ProactiveConfigRequestScoped")
    assert not hasattr(training_routes, "UserReplyModeRequest")


def test_retired_self_routes_absent_from_router():
    """自助端点退役：路由表里不得再有 `/api/user/reply-mode` / `/api/user/humanize`。"""
    from api.routers.training_routes import router

    paths = {getattr(route, "path", "") for route in router.routes}
    assert "/api/user/reply-mode" not in paths
    assert "/api/user/humanize" not in paths
    assert "/api/user/proactive-config" not in paths
    assert "/api/user/proactive/pause" not in paths
    # 保留的只读面（状态页「最近主动消息」卡在用）
    assert "/api/user/proactive/history" in paths


def test_explicit_mode_beats_hot_read(monkeypatch, tmp_path):
    """一轮内单次解析：显式 mode 优先于任何热读（防半轮混排）。"""
    rm = _use_tmp(monkeypatch, tmp_path)
    rm.write_reply_mode("immersive")
    assert rm.reply_mode_instruction("novel") == rm._INSTRUCTION_NOVEL
    assert rm.reply_mode_instruction("novel") != rm.reply_mode_instruction()
