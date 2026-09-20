"""包 Q · A4 上下文预算与去重测试。"""

from __future__ import annotations

from orchestrator.context_budget import (
    DEFAULT_BUDGET,
    ContextBudget,
    apply_budget,
    clip_text,
    dedup_memory_against_knowledge,
    memory_overlaps_knowledge,
    rag_payload_to_text,
)


class TestRagPayloadToText:
    def test_dict_results_extract_content_only(self):
        payload = {
            "results": [
                {"content": "米彩是一名画师", "source": "description", "score": 0.9},
                {"content": "她喜欢安静", "source": "personality", "score": 0.8},
            ],
            "style_examples": [{"x": 1}],
            "total_vector": 0,
        }
        text = rag_payload_to_text(payload)
        assert "米彩是一名画师" in text
        assert "她喜欢安静" in text
        assert "style_examples" not in text
        assert '"results"' not in text
        assert "{" not in text  # 不得整包 JSON

    def test_string_passthrough(self):
        assert rag_payload_to_text("已经是文本") == "已经是文本"

    def test_empty(self):
        assert rag_payload_to_text(None) == ""
        assert rag_payload_to_text({}) == ""


class TestBudgetAndDedup:
    def test_clip_text_respects_budget(self):
        long = "行一\n" * 200
        out = clip_text(long, 50)
        assert len(out) <= 55
        assert out.endswith("…")

    def test_memory_dedup_when_knowledge_covers(self):
        know = "米彩是一名画师，性格沉静。她喜欢安静的夜晚。"
        mem = "- 米彩是一名画师，性格沉静。\n- 用户喜欢吃辣"
        out = dedup_memory_against_knowledge(mem, know)
        assert "画师" not in out
        assert "吃辣" in out

    def test_memory_keeps_unrelated(self):
        know = "完全无关的知识块"
        mem = "- 用户下周要去上海出差"
        assert "上海" in dedup_memory_against_knowledge(mem, know)

    def test_history_list_tail_clip(self):
        budget = ContextBudget(history_msgs_max=3)
        history = [f"m{i}" for i in range(10)]
        out = apply_budget(chat_history=history, budget=budget)
        assert out["chat_history"] == ["m7", "m8", "m9"]
        assert out["lengths"]["history"] == 3

    def test_tool_chars_budget(self):
        budget = ContextBudget(tool_chars_max=20)
        out = apply_budget(tool_results="x" * 100, budget=budget)
        assert len(out["tool_results"]) <= 25
        assert out["lengths"]["tool"] <= 25

    def test_overlap_helper(self):
        assert memory_overlaps_knowledge("她喜欢安静", "她喜欢安静的夜晚")
        assert not memory_overlaps_knowledge("用户生日是三月", "天气晴朗")


class TestNoJsonDumpInPrepare:
    def test_prepare_context_source_forbids_rag_json_dumps(self):
        import inspect

        from orchestrator.optimized_orchestrator import OptimizedOrchestrator

        src = inspect.getsource(OptimizedOrchestrator._prepare_context)
        assert "json.dumps(task_result" not in src
        assert "rag_payload_to_text" in src
        assert "apply_budget" in src
        assert DEFAULT_BUDGET.knowledge_chars_max > 0
