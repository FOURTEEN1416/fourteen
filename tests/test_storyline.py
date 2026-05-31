"""剧情线引擎测试"""
from shisi.storyline.config import StorylineConfig
from shisi.storyline.engine import get_storyline_engine
from shisi.storyline.detector import StorylineDetector
from shisi.core.models.character_aggregate import CharacterAggregate
from shisi.core.services.prompt_builder import build


def test_detector():
    text = """
- 第1天 00:00 初识期：胆小社恐
- 第5天 00:00 倾心期：撒娇黏人
- 时间系统：每句话+10分钟
- 第7天 20:00 后禁止亲密行为
- 第7天 23:50 最终告别，只回复空白
"""
    result = StorylineDetector.detect_from_text(text)
    assert result.has_storyline, "应该检测到剧情线"
    assert result.confidence >= 0.3, "置信度应超过阈值"
    assert len(result.matched_patterns) > 0, "应匹配到模式"
    print(f"检测通过: has_storyline={result.has_storyline}, confidence={result.confidence}")
    print(f"  匹配模式: {result.matched_patterns}")


def test_engine():
    engine = get_storyline_engine()
    config = StorylineConfig.default_7day()
    config.enabled = True
    engine.set_config("test_char", config)
    engine.get_or_init_state("test_char")

    # 前3轮
    for i in range(3):
        state = engine.tick("test_char")
        stage = engine.get_current_stage("test_char")
        print(f"轮 {i+1}: 时间={state.display_time}, 阶段={stage.name if stage else '?'}")

    # 验证
    state = engine.get_state("test_char")
    assert state is not None
    assert state.turn_count == 3
    assert state.story_time_minutes == 30  # 3 * 10
    print("引擎测试通过")


def test_engine_stage_transition():
    """测试阶段自动演进"""
    engine = get_storyline_engine()
    config = StorylineConfig.default_7day()
    config.enabled = True
    engine.set_config("test_char2", config)
    engine.get_or_init_state("test_char2")

    # 推进到初识期后段（2000分钟）
    engine.get_state("test_char2").story_time_minutes = 2000
    stage = engine.get_current_stage("test_char2")
    assert stage and stage.name == "初识期", "应在初识期"
    print(f"2000分钟时: 阶段={stage.name}")

    # 再推进到熟悉期（2200分钟）
    engine.get_state("test_char2").story_time_minutes = 2200
    state = engine.tick("test_char2")
    stage = engine.get_current_stage("test_char2")
    assert stage and stage.name == "熟悉期", "应进入熟悉期"
    print(f"2200分钟时: 阶段={stage.name}, 时间={state.display_time}")

    print("阶段演进测试通过")


def test_prompt_injection():
    """测试剧情线上下文注入 prompt"""
    char = CharacterAggregate(id="test_char3", name="测试角色", description="一个测试角色")
    char.persona.core_anchors = ["温柔、慢热"]
    char.storyline_config = StorylineConfig.default_7day().to_dict()
    char.source_data = {
        "personality": "她性格温柔，喜欢安静。",
        "scenario": "两人在民宿看夕阳。",
    }

    prompt = build(char, user_message="你好", use_knowledge=False)
    assert "剧情时间" in prompt, "应包含剧情时间"
    assert "测试角色" in prompt, "应包含角色名"
    print(f"Prompt注入成功（长度: {len(prompt)}）")
    print(prompt[:300])
    print("...")


def test_storyline_context():
    """测试剧情上下文提取"""
    engine = get_storyline_engine()
    config = StorylineConfig.default_7day()
    config.enabled = True
    engine.set_config("test_char4", config)

    # 在初识期
    ctx = engine.get_storyline_context("test_char4")
    assert "当前时间" in ctx
    assert "对话基调" in ctx or "当前阶段" in ctx
    print(f"上下文: {ctx[:200]}")

    print("上下文提取测试通过")


if __name__ == "__main__":
    test_detector()
    print("---")
    test_engine()
    print("---")
    test_engine_stage_transition()
    print("---")
    test_storyline_context()
    print("---")
    test_prompt_injection()
    print("\n全部通过!")
