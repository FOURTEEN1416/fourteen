"""剧情线用户维度隔离测试（2026-10-12 隔离缺陷修复批）。

缺陷：``StorylineEngine._states`` 仅按 character_id 键 + 模块级单例 ——
两用户绑同一角色卡时共享剧情进度（跨用户串扰）。

修复契约（不改任何门禁/行为开关，CHAT_AFFECTING / use_storyline 不动）：
1. 状态键 = ``f"{user_key}::{character_id}"``；user_key 空时回落裸
   character_id（旧行为兜底，拿不到用户维度的调用方与旧持久化数据不迁移）；
2. 读写 _states 的方法签名统一加 ``user_key: str = ""``（向后兼容）；
3. 持久化回调第一参为完整状态键（落盘侧据此建键）；
4. 配置（_configs）保持角色级共享 —— 同卡多用户共享配置、各自拥有进度。
"""

from __future__ import annotations

from shisi.storyline.config import (
    EndingConfig,
    StageDefinition,
    StageTiming,
    StorylineConfig,
)
from shisi.storyline.engine import StorylineEngine


def _two_stage_config() -> StorylineConfig:
    """两阶段配置：每 tick +50 分钟；0-100 阶段一，100-200 阶段二。

    dialogue_notes 必填——get_storyline_context 只在非空时输出阶段段，
    空值会让上下文只剩剧情时间行（隔离断言无处落脚）。
    """
    return StorylineConfig(
        enabled=True,
        time_per_turn=50,
        max_duration_minutes=10_080,
        stages=[
            StageDefinition(name="阶段一", dialogue_notes="基调一", timing=StageTiming(0, 100)),
            StageDefinition(name="阶段二", dialogue_notes="基调二", timing=StageTiming(100, 200)),
        ],
        ending=EndingConfig(type="freeze", blank_after_end=False),
    )


# ── ① 两用户同 character_id 各自推进剧情互不可见 ──


def test_two_users_same_character_progress_independently():
    """user1 与 user2 绑同一张卡：各自的 tick 只推进各自的状态。"""
    engine = StorylineEngine()
    engine.set_config("charX", _two_stage_config())

    s1 = engine.tick("charX", user_key="user1")
    s2 = engine.tick("charX", user_key="user2")

    # 各自恰好推进 1 轮 / 50 分钟 —— 共享状态下 user2 会读到 turn_count=2
    assert s1.turn_count == 1
    assert s2.turn_count == 1
    assert engine.get_state("charX", user_key="user1").turn_count == 1
    assert engine.get_state("charX", user_key="user2").turn_count == 1
    assert engine.get_state("charX", user_key="user1").story_time_minutes == 50
    assert engine.get_state("charX", user_key="user2").story_time_minutes == 50


def test_stage_visibility_isolated_per_user():
    """user1 推进到阶段二后，user2 的阶段视图不受影响。"""
    engine = StorylineEngine()
    engine.set_config("charX", _two_stage_config())

    for _ in range(3):  # user1: 150 分钟 → 阶段二
        engine.tick("charX", user_key="user1")
    engine.tick("charX", user_key="user2")  # user2: 50 分钟 → 阶段一

    assert engine.get_current_stage("charX", user_key="user1").name == "阶段二"
    assert engine.get_current_stage("charX", user_key="user2").name == "阶段一"
    # 上下文注入同样按用户隔离（阶段段以 dialogue_notes 落地）
    ctx1 = engine.get_storyline_context("charX", user_key="user1")
    ctx2 = engine.get_storyline_context("charX", user_key="user2")
    assert "基调二" in ctx1
    assert "基调二" not in ctx2


def test_get_progress_user_scoped():
    """get_progress 按用户维度返回各自进度。"""
    engine = StorylineEngine()
    engine.set_config("charP", _two_stage_config())
    engine.tick("charP", user_key="user1")
    engine.tick("charP", user_key="user1")
    engine.tick("charP", user_key="user2")

    p1 = engine.get_progress("charP", user_key="user1")
    p2 = engine.get_progress("charP", user_key="user2")
    assert p1["state"]["turn_count"] == 2
    assert p2["state"]["turn_count"] == 1


def test_reset_state_is_per_user():
    """重置只作用于目标用户，其他用户进度保持。"""
    engine = StorylineEngine()
    engine.set_config("charR", _two_stage_config())
    engine.tick("charR", user_key="user1")
    engine.tick("charR", user_key="user1")
    engine.tick("charR", user_key="user2")

    engine.reset_state("charR", user_key="user1")

    assert engine.get_state("charR", user_key="user1").turn_count == 0
    assert engine.get_state("charR", user_key="user2").turn_count == 1


# ── ② user_key 缺省/空时行为同旧（兜底） ──


def test_empty_user_key_falls_back_to_legacy_behavior():
    """不带 user_key 与显式空串等价：共享同一裸 character_id 键（旧行为）。"""
    engine = StorylineEngine()
    engine.set_config("charY", _two_stage_config())

    engine.tick("charY")  # 旧调用形态（无 user_key 实参）
    engine.tick("charY", user_key="")  # 显式空串

    state = engine.get_state("charY")
    assert state is not None
    assert state.turn_count == 2
    assert state.story_time_minutes == 100
    assert engine.get_state("charY", user_key="") is state


def test_user_key_and_legacy_key_do_not_cross():
    """用户键与裸键是两个独立状态：带 user_key 的推进不影响裸键兜底状态。"""
    engine = StorylineEngine()
    engine.set_config("charY2", _two_stage_config())

    engine.tick("charY2")  # 裸键（旧行为路径，如 storyline_routes 配置恢复）
    engine.tick("charY2", user_key="user1")

    assert engine.get_state("charY2").turn_count == 1
    assert engine.get_state("charY2", user_key="user1").turn_count == 1


# ── ③ 同用户同角色进度保持 ──


def test_same_user_same_character_progress_persists():
    """同用户反复 tick 进度单调累积，且不受其他用户推进干扰。"""
    engine = StorylineEngine()
    engine.set_config("charC", _two_stage_config())

    engine.tick("charC", user_key="user1")
    engine.tick("charC", user_key="user1")
    assert engine.get_state("charC", user_key="user1").turn_count == 2

    engine.tick("charC", user_key="user2")  # 他人推进
    assert engine.get_state("charC", user_key="user1").turn_count == 2

    engine.tick("charC", user_key="user1")
    assert engine.get_state("charC", user_key="user1").turn_count == 3


# ── 持久化：键同步带用户维度 + 旧格式兼容 ──


def test_persist_hook_receives_state_key():
    """持久化回调第一参为完整状态键：用户键带 user 前缀，兜底为裸键。"""
    engine = StorylineEngine()
    received: list[tuple[str, dict]] = []
    engine.set_persist_hook(lambda key, data: received.append((key, data)))
    engine.set_config("charW", _two_stage_config())

    engine.tick("charW", user_key="u1")
    engine.tick("charW")  # 旧行为路径：兜底裸键

    keys = [k for k, _ in received]
    assert "u1::charW" in keys, f"持久化键缺用户维度: {keys}"
    assert "charW" in keys, f"兜底路径持久化键应保留裸键（旧格式兼容）: {keys}"


def test_set_state_from_dict_legacy_and_user_keys():
    """旧格式（裸 character_id 键）按旧数据读入不迁移；用户键独立恢复。"""
    engine = StorylineEngine()
    engine.set_config("charZ", _two_stage_config())

    engine.set_state_from_dict("charZ", {"turn_count": 7})  # 旧格式：无 user_key
    engine.set_state_from_dict("charZ", {"turn_count": 9}, user_key="user1")

    assert engine.get_state("charZ").turn_count == 7
    assert engine.get_state("charZ", user_key="user1").turn_count == 9


def test_remove_config_user_scoped_and_legacy_full_clear():
    """带 user_key 只删该用户；不带做角色级清理（裸键 + 所有所属用户键）。"""
    engine = StorylineEngine()
    engine.set_config("charV", _two_stage_config())
    engine.tick("charV", user_key="user1")
    engine.tick("charV", user_key="user2")
    engine.tick("charV")  # 裸键

    engine.remove_config("charV", user_key="user1")
    assert engine.get_state("charV", user_key="user1") is None
    assert engine.get_state("charV", user_key="user2") is not None
    assert engine.get_state("charV") is not None

    engine.remove_config("charV")  # 角色级删除：全部清理
    assert engine.get_state("charV") is None
    assert engine.get_state("charV", user_key="user2") is None


# ── build 层透传（生产 prompt 路径形态） ──


def test_prompt_builder_passes_user_key_to_engine():
    """prompt_builder.build(user_key=…) 透传引擎：同卡两用户各自推进互不可见。"""
    from shisi.core.models.character_aggregate import CharacterAggregate
    from shisi.core.services.prompt_builder import build
    from shisi.storyline.engine import get_storyline_engine

    cid = "iso_build_char"
    agg = CharacterAggregate(id=cid, name="隔离测试角色", description="测试")
    agg.storyline_config = _two_stage_config().to_dict()

    engine = get_storyline_engine()
    try:
        build(agg, user_message="你好", use_knowledge=False, user_key="user1")
        build(agg, user_message="你好", use_knowledge=False, user_key="user2")

        s1 = engine.get_state(cid, user_key="user1")
        s2 = engine.get_state(cid, user_key="user2")
        assert s1 is not None and s2 is not None
        assert s1.turn_count == 1 and s2.turn_count == 1
    finally:
        engine.remove_config(cid)  # 角色级清理，防污染全局单例
