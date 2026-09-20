"""回复质量根治批次回归测试（2026-09-20）。

覆盖四个修复：
A) 对话上下文真源改 DB chat_history（重启不失忆 + 会话隔离 + 新旧形态合并）
B) get_recent_context 会话隔离
C) fact_extractor 新增 commitment（承诺/约定）类别
D) 微信追问链键错位修复（session_key ↔ 裸 wxid）
E) 沉浸式指令放宽（10~80 字、允许接话题；禁动作/编造保留）
"""
from __future__ import annotations

from pathlib import Path

import pytest

# ══════════════════════════════════════════════════════════
#  A/B. 上下文真源：DB + 会话隔离 + 双形态合并
# ══════════════════════════════════════════════════════════

class TestDBBackedChatContext:
    def _make_pipeline_with_db(self, tmp_path: Path, rows: list[tuple]):
        """构造带独立 DB 的 pipeline；rows = [(session, role, content, created_at)]"""
        from shisi.memory.legacy.memory_pipeline import MemoryPipeline
        from shisi.memory.legacy.structured_memory import StructuredMemory

        sm = StructuredMemory(db_path=str(tmp_path / "t.db"))
        with sm._conn() as conn:
            for sess, role, content, ts in rows:
                conn.execute(
                    "INSERT INTO chat_history (session_id, role, content, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (sess, role, content, ts),
                )
            conn.commit()
        pipe = MemoryPipeline.__new__(MemoryPipeline)  # 绕开重初始化
        pipe.sm = sm
        from shisi.memory.legacy.conversation_summarizer import ConversationSummarizer
        pipe.summarizer = ConversationSummarizer(llm_gateway=None)
        pipe.working = type("W", (), {
            "session_id": "", "get_recent": lambda self, n=10: [],
        })()
        return pipe

    def test_context_survives_empty_working_memory(self, tmp_path):
        """RAM 工作记忆为空（重启后）仍能从 DB 恢复上下文——治失忆。"""
        pipe = self._make_pipeline_with_db(tmp_path, [
            ("s1", "user", "叫我明天七点起床", "2026-09-20 07:00:00"),
            ("s1", "assistant", "好，明早七点我叫你", "2026-09-20 07:00:01"),
        ])
        history, summary = pipe.get_chat_context(session_id="s1", keep_recent=50)
        joined = "".join(m.get("content", "") for m in history)
        assert "叫我明天七点起床" in joined
        assert "明早七点我叫你" in joined

    def test_session_isolation_no_cross_talk(self, tmp_path):
        """两个会话的 DB 消息不得互相混入——治跨用户串扰。"""
        pipe = self._make_pipeline_with_db(tmp_path, [
            ("alice", "user", "我爱吃火锅", "2026-09-20 07:00:00"),
            ("bob", "user", "我下周考试", "2026-09-20 07:00:01"),
        ])
        history, _ = pipe.get_chat_context(session_id="bob", keep_recent=50)
        joined = "".join(m.get("content", "") for m in history)
        assert "我下周考试" in joined
        assert "火锅" not in joined

    def test_legacy_session_form_not_merged_into_owner(self, tmp_path):
        """owner 会话不得并入裸 peer 遗留历史（2026-09-21 隔离硬约束）。"""
        pipe = self._make_pipeline_with_db(tmp_path, [
            ("wxid_abc", "user", "旧的遗留通道消息", "2026-09-19 10:00:00"),
            ("1:wxid_abc", "user", "新的owner通道消息", "2026-09-20 10:00:00"),
        ])
        history, _ = pipe.get_chat_context(session_id="1:wxid_abc", keep_recent=50)
        joined = "".join(m.get("content", "") for m in history)
        assert "新的owner通道消息" in joined
        assert "旧的遗留通道消息" not in joined

    def test_recent_context_session_scoped(self, tmp_path):
        pipe = self._make_pipeline_with_db(tmp_path, [
            ("alice", "user", "alice说的事", "2026-09-20 07:00:00"),
            ("bob", "user", "bob说的事", "2026-09-20 07:00:01"),
            ("bob", "assistant", "bob的回复", "2026-09-20 07:00:02"),
        ])
        text = pipe.get_recent_context(n=3, session_id="bob")
        assert "bob说的事" in text
        assert "alice" not in text


# ══════════════════════════════════════════════════════════
#  C. 承诺类事实提取
# ══════════════════════════════════════════════════════════

class TestCommitmentFactExtraction:
    def test_commitment_category_registered(self):
        from shisi.memory.legacy.fact_extractor import FACT_CATEGORIES
        assert "commitment" in FACT_CATEGORIES

    def test_reminder_request_extracted(self):
        from shisi.memory.legacy.fact_extractor import FactExtractor
        fe = FactExtractor(llm_func=None)  # 规则模式
        facts = fe.extract_facts(["明天早上七点提醒我起床"])
        cats = {f["category"] for f in facts}
        assert "commitment" in cats
        assert any("提醒我" in f["fact"] for f in facts)

    def test_agreement_extracted(self):
        from shisi.memory.legacy.fact_extractor import FactExtractor
        fe = FactExtractor(llm_func=None)
        facts = fe.extract_facts(["咱们说好了周六一起去看电影"])
        assert any(
            f["category"] == "commitment" and "说好" in f["fact"]
            for f in facts
        )

    def test_llm_prompt_includes_commitment(self):
        from shisi.memory.legacy.fact_extractor import FACT_CATEGORIES
        assert "commitment" in FACT_CATEGORIES  # LLM prompt 用 join(FACT_CATEGORIES)


# ══════════════════════════════════════════════════════════
#  D. 微信追问链键错位修复
# ══════════════════════════════════════════════════════════

class TestFollowupKeyFix:
    def test_peer_wxid_from_session(self):
        from wechat_direct.wechat_connector import WeChatConnector
        assert WeChatConnector._peer_wxid_from_session("1:wxid_abc@im.wechat") == "wxid_abc@im.wechat"
        assert WeChatConnector._peer_wxid_from_session("12:peer_x") == "peer_x"
        # 裸形态原样返回（遗留全局通道）
        assert WeChatConnector._peer_wxid_from_session("wxid_abc") == "wxid_abc"
        assert WeChatConnector._peer_wxid_from_session("") == ""

    def test_context_token_lookup_uses_peer_not_session_key(self):
        """追问发送目标必须是裸 wxid（与 _context_tokens 键一致）。"""
        from wechat_direct.wechat_connector import WeChatConnector
        conn = object.__new__(WeChatConnector)
        conn._context_tokens = {"wxid_abc": {"token": "T", "ts": 1e18}}
        # 用 session_key 直接查（旧行为）应查不到；经还原后应查到
        assert conn._context_tokens.get("1:wxid_abc") is None
        peer = WeChatConnector._peer_wxid_from_session("1:wxid_abc")
        assert conn._context_tokens[peer]["token"] == "T"


# ══════════════════════════════════════════════════════════
#  E. 沉浸式指令放宽
# ══════════════════════════════════════════════════════════

class TestImmersiveInstructionRelaxed:
    def test_new_length_and_topic_rules(self):
        from utils.reply_mode import _INSTRUCTION_IMMERSIVE
        assert "10~80 字" in _INSTRUCTION_IMMERSIVE
        assert "长短跟随话题" in _INSTRUCTION_IMMERSIVE
        assert "把话题往前推" in _INSTRUCTION_IMMERSIVE

    def test_old_hard_limits_removed(self):
        from utils.reply_mode import _INSTRUCTION_IMMERSIVE
        assert "3~25 字" not in _INSTRUCTION_IMMERSIVE
        assert "不要连珠炮式追问" not in _INSTRUCTION_IMMERSIVE

    def test_guardrails_preserved(self):
        """防小说感与防编造条款必须保留。"""
        from utils.reply_mode import _INSTRUCTION_IMMERSIVE
        assert "严禁括号动作" in _INSTRUCTION_IMMERSIVE
        assert "严禁编造对方没有说过的处境" in _INSTRUCTION_IMMERSIVE
        assert "你只能说话，不能做动作" in _INSTRUCTION_IMMERSIVE

    def test_novel_mode_untouched(self):
        from utils.reply_mode import _INSTRUCTION_NOVEL
        assert "允许用括号写动作" in _INSTRUCTION_NOVEL


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
