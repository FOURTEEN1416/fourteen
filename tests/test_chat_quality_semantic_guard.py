"""2026-10-20 八问题批 P0：语义终审 + 模式边界回归。

生产原句锚（会话 2862~2875）：
1. 「是的，我想确认一下你是不是真的记得，有时候我会忘记自己说过的话
    你今天有没有什么特别的事情想要和我分享的呢？」——元记忆解释 + 零信息问卷；
2. 「你呢，有没有什么特别想吃的？」——用户没提问，模型反向虚构提问；
3. 逐字复述用户上一句；4. 沉浸式回复混入括号动作（小说模式格式越界）。
"""

from __future__ import annotations

from utils.prompt_sanitize import terminal_reply_guard

PROD_META_MEMORY = (
    "是的，我想确认一下你是不是真的记得，有时候我会忘记自己说过的话 "
    "你今天有没有什么特别的事情想要和我分享的呢？"
)


class TestVerbatimEcho:
    def test_production_echo_first_line_dropped(self):
        user = "我今天去了一趟医院，医生说没什么大事"
        out = terminal_reply_guard(user + "\n那就好，别自己吓自己。", user_msg=user)
        assert out == "那就好，别自己吓自己。"

    def test_punctuation_insensitive_echo(self):
        user = "今天下班好晚啊"
        out = terminal_reply_guard("今天下班好晚啊！\n辛苦啦。", user_msg=user)
        assert out == "辛苦啦。"

    def test_short_normal_reply_not_stripped(self):
        # 短回应不判复述：「早」对「早」是正常寒暄
        assert terminal_reply_guard("早", user_msg="早") == "早"

    def test_echo_only_reply_returns_empty(self):
        user = "我明天要早起赶高铁"
        assert terminal_reply_guard(user, user_msg=user) == ""

    def test_paraphrase_not_stripped(self):
        raw = "今天确实挺晚的，辛苦啦。"
        assert terminal_reply_guard(raw, user_msg="今天下班好晚啊") == raw


class TestMetaMemoryTalk:
    def test_production_sentence_removed_entirely(self):
        assert terminal_reply_guard(PROD_META_MEMORY, user_msg="你还记得我上次说的事吗") == ""

    def test_meta_sentence_removed_but_content_kept(self):
        raw = "记得啊，你说的是下周三。\n我想确认一下你是不是真的记得，有时候我会忘记自己说过的话"
        out = terminal_reply_guard(raw, user_msg="你还记得吗")
        assert "记得啊，你说的是下周三。" in out
        assert "确认一下" not in out

    def test_natural_care_not_stripped(self):
        raw = "确认一下你到家了没，到了跟我说一声。"
        assert terminal_reply_guard(raw, user_msg="我下班了") == raw


class TestUngroundedReciprocal:
    def test_production_reciprocal_removed(self):
        raw = "我煮了碗面。你呢，有没有什么特别想吃的？"
        out = terminal_reply_guard(raw, user_msg="我晚上加班到八点")
        assert out == "我煮了碗面。"

    def test_grounded_reciprocal_kept(self):
        raw = "我煮了碗面。你呢，吃了吗？"
        assert terminal_reply_guard(raw, user_msg="你晚上吃什么了？") == raw

    def test_non_question_ni_ne_kept(self):
        raw = "我懂。你呢，别太累了。"
        assert terminal_reply_guard(raw, user_msg="今天好累") == raw

    def test_proactive_empty_user_kept(self):
        """主动链 user_msg 为空：没有「回应哪句」语境，「你呢」是合法主动开场。"""
        raw = "我这边下雨了，你呢，带伞没？"
        assert terminal_reply_guard(raw, user_msg="") == raw


class TestImmersiveStageDirection:
    def test_immersive_strips_stage_direction(self):
        raw = "（她停下脚步，回头看你）就这点出息。"
        assert terminal_reply_guard(raw, user_msg="嗯", reply_mode="immersive") == "就这点出息。"

    def test_novel_keeps_stage_direction(self):
        raw = "（她停下脚步，回头看你）就这点出息。"
        assert terminal_reply_guard(raw, user_msg="嗯", reply_mode="novel") == raw

    def test_star_direction_stripped_in_immersive(self):
        raw = "*叹了口气* 好吧，早点睡。"
        assert terminal_reply_guard(raw, user_msg="晚安", reply_mode="immersive") == "好吧，早点睡。"

    def test_mode_absent_no_strip(self):
        raw = "（笑了笑）好啊。"
        assert terminal_reply_guard(raw, user_msg="周末去爬山吗", reply_mode="") == raw


class TestPromptModeBoundary:
    @staticmethod
    def _aggregate():
        from shisi.core.models.character_aggregate import CharacterAggregate

        return CharacterAggregate(
            id="mode01",
            name="测试角色",
            description="她是测试角色。",
            first_mes="（抬头看你）你来啦。",
            source_data={
                "mes_example": "<START>\n用户：你好\n测试角色：（笑了笑）嗯，坐吧。",
            },
        )

    def test_immersive_prompt_excludes_stage_directions(self, monkeypatch):
        from utils import reply_mode

        monkeypatch.setattr(
            reply_mode, "read_reply_mode", lambda: reply_mode.REPLY_MODE_IMMERSIVE
        )
        prompt = self._aggregate().build_system_prompt(user_message="在吗")
        assert "（抬头看你）" not in prompt
        assert "（笑了笑）" not in prompt
        assert "你来啦" in prompt
        assert "嗯，坐吧" in prompt

    def test_novel_prompt_keeps_stage_directions(self, monkeypatch):
        from utils import reply_mode

        monkeypatch.setattr(
            reply_mode, "read_reply_mode", lambda: reply_mode.REPLY_MODE_NOVEL
        )
        prompt = self._aggregate().build_system_prompt(user_message="在吗")
        assert "（抬头看你）" in prompt
        assert "（笑了笑）" in prompt

    def test_strip_stage_directions_pure(self):
        from utils.reply_mode import strip_stage_directions

        assert strip_stage_directions("（想了想）你先去忙吧。") == "你先去忙吧。"
        assert strip_stage_directions("（想了想）") == ""
        assert strip_stage_directions("今天好热（擦了擦汗）。") == "今天好热。"

    def test_immersive_creator_notes_format_conflict_overridden(self, monkeypatch):
        """12/42 张卡 creator_notes 强制括号格式：沉浸式下必须声明格式优先级。"""
        from shisi.core.models.character_aggregate import CharacterAggregate
        from utils import reply_mode

        monkeypatch.setattr(
            reply_mode, "read_reply_mode", lambda: reply_mode.REPLY_MODE_IMMERSIVE
        )
        agg = CharacterAggregate(
            id="mode02",
            name="测试角色",
            description="她是测试角色。",
            creator_notes="所有身体状态和内心独白必须自然写在(括号)里。",
        )
        prompt = agg.build_system_prompt(user_message="在吗")
        assert "冲突的**输出格式要求**" in prompt
        assert "所有身体状态和内心独白必须自然写在(括号)里。" in prompt

    def test_novel_creator_notes_no_conflict_override(self, monkeypatch):
        from shisi.core.models.character_aggregate import CharacterAggregate
        from utils import reply_mode

        monkeypatch.setattr(
            reply_mode, "read_reply_mode", lambda: reply_mode.REPLY_MODE_NOVEL
        )
        agg = CharacterAggregate(
            id="mode03",
            name="测试角色",
            description="她是测试角色。",
            creator_notes="所有身体状态和内心独白必须自然写在(括号)里。",
        )
        prompt = agg.build_system_prompt(user_message="在吗")
        assert "冲突的**输出格式要求**" not in prompt


class TestInstructionRootCause:
    def test_immersive_instruction_removes_question_incentive(self):
        from utils.reply_mode import _INSTRUCTION_IMMERSIVE

        assert "需要时反问一句" not in _INSTRUCTION_IMMERSIVE
        assert "自然追问一句" not in _INSTRUCTION_IMMERSIVE
        assert "没有可指认的由头就不提问" in _INSTRUCTION_IMMERSIVE
        assert "禁止逐字复述对方上一句" in _INSTRUCTION_IMMERSIVE

    def test_topic_rule_removes_interrogation_incentive(self):
        from utils.prompt_sanitize import TOPIC_CONTINUITY_RULE

        assert "连续追问" not in TOPIC_CONTINUITY_RULE
        assert "只有对方话里有可指认的" in TOPIC_CONTINUITY_RULE


class TestTopicBagNoise:
    def test_production_junk_topics_filtered(self):
        """生产话题包 ['的话','哈喽'] 这类纯功能词不得进「当前话题」。"""
        from utils.prompt_sanitize import extract_current_topics

        topics = extract_current_topics([], current_user_message="哈喽，有什么想说的吗")
        assert "的话" not in topics
        assert "哈喽" not in topics
        assert "说的" not in topics

    def test_real_topic_survives(self):
        from utils.prompt_sanitize import extract_current_topics

        topics = extract_current_topics([], current_user_message="周末去图书馆")
        assert "周末去图书馆" in topics

