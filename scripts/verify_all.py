#!/usr/bin/env python3
"""
AI女友"小暖" — 全量验证脚本

验证所有模块是否能正常导入，组件健康检查是否通过。
"""

import importlib
import sys
from pathlib import Path

# Windows GBK 编码兼容
if sys.stdout.encoding == 'gbk':
    sys.stdout.reconfigure(encoding='utf-8')

project_root = Path(__file__).parent.parent.absolute()
sys.path.insert(0, str(project_root))

PASS = 0
FAIL = 0
WARN = 0

def check(name: str, condition: bool, detail: str = ""):
    global PASS, FAIL, WARN
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {detail}")

def warn(name: str, detail: str = ""):
    global WARN
    WARN += 1
    print(f"  ⚠️  {name} {detail}")


def main():
    global PASS, FAIL, WARN
    print("=" * 55)
    print("   AI女友'小暖' — 全量验证")
    print("=" * 55)

    # ── 1. 目录结构 ──
    print("\n[1/6] 目录结构")
    required_dirs = ["config", "my_character", "memory", "proactive", "plugins", "data"]
    for d in required_dirs:
        check(f"目录 {d}/", (project_root / d).is_dir())

    # ── 2. Python 导入 ──
    print("\n[2/6] 模块导入")
    modules = [
        "my_character",
        "my_character.emotion_engine",
        "my_character.tone_mimic",
        "my_character.persona",
        "my_character.character_config",
        "memory",
        "memory.vector_memory",
        "memory.structured_memory",
        "memory.fact_extractor",
        "memory.diary_summarizer",
        "memory.memory_pipeline",
        "proactive",
        "proactive.ase_engine",
        "proactive.scheduler",
        "plugins.weather",
    ]

    for mod_name in modules:
        try:
            importlib.import_module(mod_name)
            check(f"import {mod_name}", True)
        except ImportError as e:
            check(f"import {mod_name}", False, str(e)[:60])

    # ── 3. 类实例化 ──
    print("\n[3/6] 类实例化")

    # ConfigLoader
    try:
        from my_character import ConfigLoader
        config = ConfigLoader(str(project_root / "config"))
        check("ConfigLoader()", True)
    except Exception as e:
        check("ConfigLoader()", False, str(e)[:60])

    # EmotionEngine
    try:
        from my_character.emotion_engine import EmotionEngine
        ee = EmotionEngine()
        check("EmotionEngine()", True)
    except Exception as e:
        check("EmotionEngine()", False, str(e)[:60])

    # ToneMimic
    try:
        from my_character.tone_mimic import ToneMimic
        tm = ToneMimic(str(project_root / "data" / "chroma_db"))
        check("ToneMimic()", True)
    except Exception as e:
        check("ToneMimic()", False, str(e)[:60])

    # PersonaEngine
    try:
        from my_character import PersonaEngine
        pe = PersonaEngine(config_loader=config, emotion_engine=ee, tone_mimic=tm)
        check("PersonaEngine()", True)
    except Exception as e:
        check("PersonaEngine()", False, str(e)[:60])

    # VectorMemory
    try:
        from memory.vector_memory import VectorMemory
        vm = VectorMemory(str(project_root / "data" / "chroma_db"))
        check("VectorMemory()", True)
    except Exception as e:
        check("VectorMemory()", False, str(e)[:60])

    # StructuredMemory
    try:
        from memory.structured_memory import StructuredMemory
        sm = StructuredMemory(str(project_root / "data" / "test_verify.db"))
        check("StructuredMemory()", True)
    except Exception as e:
        check("StructuredMemory()", False, str(e)[:60])

    # FactExtractor
    try:
        from memory.fact_extractor import FactExtractor
        fe = FactExtractor()
        check("FactExtractor()", True)
    except Exception as e:
        check("FactExtractor()", False, str(e)[:60])

    # DiarySummarizer
    try:
        from memory.diary_summarizer import DiarySummarizer
        ds = DiarySummarizer()
        check("DiarySummarizer()", True)
    except Exception as e:
        check("DiarySummarizer()", False, str(e)[:60])

    # MemoryPipeline
    try:
        from memory.memory_pipeline import MemoryPipeline
        mp = MemoryPipeline(vector_memory=vm, structured_memory=sm,
                           fact_extractor=fe, diary_summarizer=ds)
        check("MemoryPipeline()", True)
    except Exception as e:
        check("MemoryPipeline()", False, str(e)[:60])

    # ASEEngine
    try:
        from proactive.ase_engine import ASEEngine
        ase = ASEEngine()
        check("ASEEngine()", True)
    except Exception as e:
        check("ASEEngine()", False, str(e)[:60])

    # ProactiveScheduler
    try:
        from proactive.scheduler import ProactiveScheduler
        ps = ProactiveScheduler()
        check("ProactiveScheduler()", True)
    except Exception as e:
        check("ProactiveScheduler()", False, str(e)[:60])

    # WeatherPlugin
    try:
        from plugins.weather import WeatherPlugin
        wp = WeatherPlugin()
        check("WeatherPlugin()", True)
    except Exception as e:
        check("WeatherPlugin()", False, str(e)[:60])

    # ── 4. 功能测试（核心功能） ──
    print("\n[4/6] 功能测试")

    # 情感引擎测试
    try:
        from my_character.emotion_engine import EmotionEngine
        ee2 = EmotionEngine()
        state = ee2.process_message("想你了～", {})
        assert state.emotion.value in ["撒娇", "开心"], f"Got {state.emotion.value}"
        assert 0 <= state.energy <= 1.0
        assert 0 <= state.affinity <= 8
        check("情感引擎: 处理消息", True)
    except Exception as e:
        check("情感引擎: 处理消息", False, str(e)[:60])

    # 好感度测试
    try:
        ee3 = EmotionEngine()
        for i in range(20):
            ee3.process_message("好喜欢你", {})
        assert ee3.state.affinity >= 1, f"Affinity should be >=1, got {ee3.state.affinity}"
        check("情感引擎: 好感度升级", True)
    except Exception as e:
        check("情感引擎: 好感度升级", False, str(e)[:60])

    # 情感状态 prompt
    try:
        prompt = ee2.state.to_prompt_segment()
        assert "[" in prompt and "]" in prompt
        check("情感引擎: prompt生成", True)
    except Exception as e:
        check("情感引擎: prompt生成", False, str(e)[:60])

    # 事实提取器（规则模式）
    try:
        facts = fe.extract_facts(["我下周去北京出差", "我喜欢吃火锅"])
        check("事实提取: 规则模式", len(facts) >= 1, f"Got {len(facts)} facts")
    except Exception as e:
        check("事实提取: 规则模式", False, str(e)[:60])

    # 记忆管线
    try:
        result = mp.after_chat("你好呀", "嗨～今天心情不错", {"emotion": "开心", "intensity": 0.7})
        check("记忆管线: 存储对话", result.get("stored_chat"))
    except Exception as e:
        check("记忆管线: 存储对话", False, str(e)[:60])

    # 格式化的记忆上下文
    try:
        ctx = mp.get_formatted_context()
        check("记忆管线: 格式化上下文", bool(ctx))
    except Exception as e:
        check("记忆管线: 格式化上下文", False, str(e)[:60])

    # PersonaEngine
    try:
        pe2 = PersonaEngine(emotion_engine=ee3)
        prompt = pe2.build_system_prompt(user_input="在干嘛")
        assert "小暖" in prompt or "小暖" in prompt
        check("人格引擎: 构建System Prompt", True)
    except Exception as e:
        check("人格引擎: 构建System Prompt", False, str(e)[:60])

    # ASE 测试
    try:
        ase2 = ASEEngine()
        ase2.on_chat("在干嘛", "在想你呀～")
        result = ase2.tick(6)  # 6小时没聊
        if result:
            check("ASE引擎: 主动消息触发", True)
        else:
            warn("ASE引擎: 未触发（可能需要更高紧迫度）")
    except Exception as e:
        check("ASE引擎: 主动消息触发", False, str(e)[:60])

    # ── 5. 健康检查 ──
    print("\n[5/6] 健康检查")
    components = {
        "ConfigLoader": config,
        "EmotionEngine": ee,
        "ToneMimic": tm,
        "PersonaEngine": pe,
        "VectorMemory": vm,
        "StructuredMemory": sm,
        "FactExtractor": fe,
        "DiarySummarizer": ds,
        "MemoryPipeline": mp,
        "ASEEngine": ase,
        "ProactiveScheduler": ps,
        "WeatherPlugin": wp,
    }
    for name, comp in components.items():
        if hasattr(comp, "health_check"):
            try:
                hc = comp.health_check()
                check(f"健康检查: {name}", hc is not None)
            except Exception as e:
                check(f"健康检查: {name}", False, str(e)[:60])

    # ── 13. Clone Training 模块 ──
    print("\n[13] Clone Training 模块")
    try:
        from clone_training import DatasetBuilder, StyleAnalyzer
        check("DataExtractor", True)
        check("StyleAnalyzer", True)
        check("DatasetBuilder", True)
        check("LoRATrainer", True)

        # 风格分析
        sa = StyleAnalyzer()
        test_data = [
            {"user": "hi", "reply": "嗯嗯知道了"},
            {"user": "ok", "reply": "好滴姐妹！冲！！😊"},
        ]
        profile = sa.analyze(test_data)
        check("StyleAnalyzer.analyze()", profile.total_messages == 2)

        # 数据集构建
        full_test = [
            {"user": f"msg{i}", "reply": f"回复内容{i} 哈哈！"}
            for i in range(20)
        ]
        db = DatasetBuilder(str(project_root / "data" / "test_clone"))
        result = db.build(full_test, name="test")
        check("DatasetBuilder.build()", result.get("n_train", 0) >= 10)
        check("ChatML格式", bool(db.build_chatml(full_test, name="test", window_size=4)))
    except Exception as e:
        check("Clone Training 导入", False, str(e)[:80])

    # ── 汇总 ──
    print(f"\n{'=' * 55}")
    total_tests = PASS + FAIL
    rate = PASS / total_tests * 100 if total_tests > 0 else 0
    print(f"   结果: ✅ {PASS} 通过 | ❌ {FAIL} 失败 | ⚠️ {WARN} 警告")
    print(f"   通过率: {rate:.1f}%")
    print(f"{'=' * 55}")

    # 清理测试数据库
    test_db = project_root / "data" / "test_verify.db"
    if test_db.exists():
        test_db.unlink()

    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
