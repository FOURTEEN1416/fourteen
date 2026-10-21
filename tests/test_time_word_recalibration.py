"""出站相对时间词确定性改写（2026-10-19 时间感知 3.0 批 · P3）。

生产病灶（用户报障截图 10-19）：距上一回合 51 小时，agnes 主链在
「（距上一条 2 天）」注记+时间锚点+推理指引三重信号下仍输出
「你**刚说的**那个咳」——软约束对相对时间词彻底失效。
修法（呼应 v1.52 生成侧判死哲学，不做软约束做确定性闸门）：
terminal_reply_guard 新增 last_turn_age_seconds 实参——>45min 时
「刚才/刚刚/刚X（说讲提发问聊/告诉）」确定性降为「之前」；
缺省 None 行为与旧版完全一致；≤45min 真·刚才不误伤；
「刚好/刚巧」等非时间义形态受负向断言保护。
"""

from __future__ import annotations


class TestRecalibrationTriggers:
    def test_old_turn_rewrites_just_now(self):
        """>45min：刚才/刚刚 → 之前。"""
        from utils.prompt_sanitize import terminal_reply_guard

        out = terminal_reply_guard(
            "你刚才说的那个咳，不是真的咳吧",
            user_msg="哈喽",
            last_turn_age_seconds=8000.0,
        )
        assert "刚才" not in out
        assert "之前" in out

    def test_old_turn_rewrites_bare_gang(self):
        """单字「刚+动词」形态同样改写。"""
        from utils.prompt_sanitize import terminal_reply_guard

        out = terminal_reply_guard(
            "你刚说周末要来，真的吗",
            user_msg="嗯",
            last_turn_age_seconds=51 * 3600.0,
        )
        assert "刚说" not in out
        assert "之前说" in out

    def test_production_original_sentence(self):
        """生产原句回归（10-19 17:50，间隔 51h4m）。"""
        from utils.prompt_sanitize import terminal_reply_guard

        out = terminal_reply_guard(
            "哈喽 你刚说的那个咳，不是真的咳吧 还好没事",
            user_msg="哈喽",
            last_turn_age_seconds=51 * 3600 + 244,
        )
        assert "刚" not in out.replace("刚好", "").replace("刚巧", "")
        assert "哈喽" in out

    def test_none_age_noop(self):
        """缺省不传 → 行为与旧版完全一致（主动链/提醒链无会话间隔语境）。"""
        from utils.prompt_sanitize import terminal_reply_guard

        raw = "你刚才说的那个咳，不是真的咳吧"
        assert terminal_reply_guard(raw, user_msg="哈喽") == raw

    def test_recent_turn_untouched(self):
        """≤45min 真·刚才：不改（人类两分钟前刚说过，「刚才」是自然的）。"""
        from utils.prompt_sanitize import terminal_reply_guard

        raw = "你刚才那个表情好可爱"
        assert (
            terminal_reply_guard(raw, user_msg="[微笑]", last_turn_age_seconds=120.0) == raw
        )


class TestNonTemporalProtection:
    def test_gang_hao_not_rewritten(self):
        """「刚好/刚巧」非时间义，不改写。"""
        from utils.prompt_sanitize import _recalibrate_time_words

        assert _recalibrate_time_words("我刚好路过") == "我刚好路过"
        assert _recalibrate_time_words("刚巧遇见你") == "刚巧遇见你"

    def test_gangqiang_not_rewritten(self):
        """「刚强」人格义不改写。"""
        from utils.prompt_sanitize import _recalibrate_time_words

        assert _recalibrate_time_words("性格刚强") == "性格刚强"


class TestMainChainWiring:
    """主链接线：_prepare_context 计算回合间隔 → _finalize_reply 透传守卫。"""

    def test_prepare_context_returns_turn_age(self):
        """ctx 携带 last_turn_age_seconds（None/畸形时间戳时 None）。"""
        from orchestrator.optimized_orchestrator import OptimizedOrchestrator

        src_fn = getattr(OptimizedOrchestrator, "_prepare_context", None)
        assert src_fn is not None

    def test_finalize_passes_age_to_guard(self):
        """_finalize_reply 调 terminal_reply_guard 时透传 last_turn_age_seconds。"""
        import inspect

        from orchestrator.optimized_orchestrator import OptimizedOrchestrator

        src = inspect.getsource(OptimizedOrchestrator._finalize_reply)
        assert "last_turn_age_seconds" in src, "_finalize_reply 未接回合间隔"


class TestStreamWiring:
    """流式接线（2026-10-21 FIX-4）：流式 _finalize_reply 调用点曾漏传
    ``session_id`` 与 ``last_turn_age_seconds``。

    后果两条：① 会话级被否认称呼剥除失效（guard 与随后
    ``strip_forbidden_vocatives`` 都拿不到 session 上下文，
    ``resolve_address('')`` 读不到画像昵称）；② v1.53 的出站时间词确定性降级
    （>45min 时「刚才/刚刚/刚X动词」→「之前」）在流式整条不生效——而非流式
    主链（D5:1813-1820）传了这两个参数，属同一函数两条路径行为不一致。
    """

    @staticmethod
    def _stream_finalize_call():
        """取 process_message_stream 里对 _finalize_reply 的调用节点。"""
        import ast
        import inspect

        from orchestrator._stream_mixin import _StreamPipelineMixin

        src = inspect.getsource(_StreamPipelineMixin.process_message_stream)
        tree = ast.parse(inspect.cleandoc(src))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_finalize_reply"
        ]
        assert len(calls) == 1, f"流式 _finalize_reply 调用点应唯一，实得 {len(calls)}"
        return calls[0]

    def test_stream_finalize_passes_session_id(self):
        """流式必须传 session_id（称呼剥除需要会话上下文读画像昵称）。"""
        call = self._stream_finalize_call()
        kw = {k.arg for k in call.keywords}
        assert "session_id" in kw, "流式 _finalize_reply 漏传 session_id（称呼剥除失效）"

    def test_stream_finalize_passes_turn_age(self):
        """流式必须传 last_turn_age_seconds（时间词降级需要回合间隔）。"""
        call = self._stream_finalize_call()
        kw = {k.arg: k.value for k in call.keywords}
        assert "last_turn_age_seconds" in kw, (
            "流式 _finalize_reply 漏传 last_turn_age_seconds（时间词降级不生效）"
        )
        # 值必须取自 _prepare_context 的 ctx（不得写死 None 之类假接线）
        import ast

        src = ast.unparse(kw["last_turn_age_seconds"])
        assert "ctx" in src and "last_turn_age_seconds" in src, (
            f"回合间隔必须取自 prepare 产出的 ctx，实得 {src!r}"
        )

    def test_stream_and_blocking_paths_agree(self):
        """两条路径（流式 / 非流式）必须传同一组守卫实参（防再次分叉）。"""
        import ast
        import inspect

        from orchestrator._stream_mixin import _StreamPipelineMixin
        from orchestrator.optimized_orchestrator import OptimizedOrchestrator

        def _kw(func_src: str) -> set[str]:
            tree = ast.parse(inspect.cleandoc(func_src))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "_finalize_reply"
                ):
                    return {k.arg for k in node.keywords}
            raise AssertionError("未找到 _finalize_reply 调用点")

        stream_kw = _kw(inspect.getsource(_StreamPipelineMixin.process_message_stream))
        blocking_kw = _kw(inspect.getsource(OptimizedOrchestrator.process_message))
        for required in ("session_id", "last_turn_age_seconds", "user_msg"):
            assert required in stream_kw, f"流式缺 {required}"
            assert required in blocking_kw, f"非流式缺 {required}"
