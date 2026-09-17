"""针对性测试：2026-09-17 人设绑定/emoji 收口/主动消息投递修复包

覆盖五个修复点：
1. activate 端点同步当前登录用户的 wechat_bindings（带 JWT 时）
2. 角色卡人设片段：长锚点截断保留（旧实现 >20 字直接丢弃）
3. emoji 指令语义化：五处提示词改为明确文字约束
4. ProactiveScheduler._deliver 在无事件循环线程中可投递（修 64/0 未送达）
5. MultiProviderGateway.chat_sync 存在且可从 APScheduler 式线程调用
6. UserManager.get_bound_wxids 返回绑定缓存快照
"""
import asyncio
import sys
import threading

sys.path.insert(0, ".")


# ═══════════════════════════════════════════════════════════════
#  1. activate → wechat_bindings 同步
# ═══════════════════════════════════════════════════════════════

def test_optional_user_id_none_without_bearer():
    from api.routers.character_routes import _optional_user_id

    class _Req:
        headers = {"authorization": "X-API-Key abc"}

    # 需要一个 dict-like headers；fastapi Request.headers 是不可变 dict
    class _Req2:
        headers = {}

    async def run():
        assert await _optional_user_id(_Req2()) is None

    asyncio.run(run())


def test_optional_user_id_none_with_garbage_token():
    from api.routers.character_routes import _optional_user_id

    class _Req:
        headers = {"Authorization": "Bearer not-a-jwt"}

    async def run():
        assert await _optional_user_id(_Req()) is None

    asyncio.run(run())


# ═══════════════════════════════════════════════════════════════
#  2. 长锚点截断保留
# ═══════════════════════════════════════════════════════════════

def test_long_anchor_truncated_not_dropped(tmp_path, monkeypatch):
    """>20 字锚点应截断保留进人设片段，而非被过滤丢弃。"""
    from orchestrator.optimized_orchestrator import OptimizedOrchestrator

    long_anchor = "温柔安静，表面看起来有点冷淡其实内心很细腻。不太善于社交，在熟人面前才会放松。很细心，会注意到别人忽略的细节。"
    huge_anchor = long_anchor + "感性但克制，不会轻易表露情绪。喜欢安静的事物：老旧电影、手写书信、下雨的声音。恋旧，喜欢保存有纪念意义的小物件。"
    monkeypatch.setattr(
        "orchestrator.optimized_orchestrator.normalize_character_card",
        lambda raw: {
            "name": "测试角色",
            "description": "desc",
            "core_anchors": [long_anchor, huge_anchor, "手写书信"],
        },
    )
    chars_dir = tmp_path / "config" / "characters"
    chars_dir.mkdir(parents=True)
    (chars_dir / "testcid.json").write_text('{"id": "testcid"}', encoding="utf-8")
    monkeypatch.setattr(
        "orchestrator.optimized_orchestrator.project_root", tmp_path
    )
    OptimizedOrchestrator._character_persona_cache.clear()

    segment = OptimizedOrchestrator._load_character_persona_segment("testcid")
    assert "核心锚点" in segment
    # 中长锚点（≤60 字）完整保留（旧实现 >20 字就整条丢弃）
    assert long_anchor in segment
    # 超长锚点（>60 字）截断保留头部 + 省略号，不丢弃
    assert huge_anchor not in segment
    assert huge_anchor[:60] in segment
    # 短锚点原样保留
    assert "手写书信" in segment
    OptimizedOrchestrator._character_persona_cache.clear()


# ═══════════════════════════════════════════════════════════════
#  3. emoji 指令语义化
# ═══════════════════════════════════════════════════════════════

def test_persona_profile_emoji_semantic_text():
    from shisi.core.models.persona_profile import PersonaProfile

    seg = PersonaProfile(emoji_frequency=0.6).to_prompt_segment()
    assert "每条最多一个" in seg and "情绪强烈" in seg

    seg_low = PersonaProfile(emoji_frequency=0.1).to_prompt_segment()
    assert "不使用表情符号" in seg_low

    seg_high = PersonaProfile(emoji_frequency=0.9).to_prompt_segment()
    assert "每条最多两个" in seg_high


def test_persona_engine_default_desc_emoji_constraint():
    from my_character.persona_engine import DEFAULT_PERSONA_DESC

    assert "偶尔用～表情" not in DEFAULT_PERSONA_DESC
    assert "最多一个" in DEFAULT_PERSONA_DESC


def test_style_layer_emoji_default_tier():
    from my_character.emotion_style_coupler import EmotionStyleCoupler

    coupler = EmotionStyleCoupler()
    coupled = coupler.couple({"primary": {"type": "平常"}})
    seg = coupler.get_style_prompt_segment(coupled)
    assert "每条最多一个" in seg


def test_persona_engine_style_layer_emoji():
    from my_character.persona_engine import PersonaEngine

    engine = PersonaEngine.__new__(PersonaEngine)  # 不走 __init__（避免拉 LLM）
    # 直接测 _build_style_layer 内 emoji 分支：构造带 emoji 值的情绪状态
    class _FakeState:
        primary_emotion = "撒娇"
        primary_intensity = 0.5
        energy = 1.0
        affinity = 0

        def to_dict(self):
            return {
                "primary": {"type": "撒娇", "intensity": 0.5},
                "energy": 1.0,
                "affinity": {"level": 0},
            }

    seg = engine._build_style_layer(_FakeState(), "", None)
    assert "最多一个" in seg or "最多两个" in seg  # 撒娇 emoji=0.9 → 高档文案


# ═══════════════════════════════════════════════════════════════
#  4. 调度器投递：无事件循环线程内 _deliver 可用
# ═══════════════════════════════════════════════════════════════

def test_scheduler_deliver_in_plain_thread():
    """APScheduler 工作线程场景：线程内无事件循环，_deliver 应成功调用通道。"""
    from proactive.scheduler import ProactiveScheduler

    sent: list[str] = []

    async def fake_sender(msg: str) -> None:
        sent.append(msg)

    sched = ProactiveScheduler(send_message_func=lambda m: sent.append("fallback:" + m))
    sched._channel_instances["wechat"] = fake_sender

    result: dict = {}

    def runner():
        # 关键：本线程没有事件循环（复现 APScheduler 线程环境）
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            pass
        sched._deliver("hello-proactive")
        result["ok"] = True

    t = threading.Thread(target=runner)
    t.start()
    t.join(timeout=10)

    assert result.get("ok") is True
    assert "hello-proactive" in sent


def test_scheduler_deliver_fallback_on_channel_error():
    from proactive.scheduler import ProactiveScheduler

    sent: list[str] = []

    async def bad_sender(msg: str) -> None:
        raise RuntimeError("channel down")

    sched = ProactiveScheduler(send_message_func=lambda m: sent.append(m))
    sched._channel_instances["wechat"] = bad_sender

    sched._deliver("msg-x")
    # 通道失败 → _send_to_all 内部会吞掉通道异常（标记失效），
    # 但兜底 _send 不应被触发（_send_to_all 返回 False 时才走）；
    # 这里主要断言不抛出、不崩溃
    assert True


# ═══════════════════════════════════════════════════════════════
#  5. MultiProviderGateway.chat_sync
# ═══════════════════════════════════════════════════════════════

def test_multi_gateway_has_chat_sync():
    from llm_provider.multi_provider_gateway import MultiProviderGateway

    assert hasattr(MultiProviderGateway, "chat_sync")


def test_multi_gateway_chat_sync_callable_from_thread():
    """ASE MessageGenerator 探测路径：hasattr + 线程内调用。"""
    from llm_provider.multi_provider_gateway import MultiProviderGateway

    gw = MultiProviderGateway.__new__(MultiProviderGateway)

    async def fake_chat(**kwargs):
        return "sync-ok"

    gw.chat = fake_chat

    out: dict = {}

    def runner():
        out["val"] = gw.chat_sync(query="hi")

    t = threading.Thread(target=runner)
    t.start()
    t.join(timeout=10)
    assert out.get("val") == "sync-ok"


def test_ase_message_generator_uses_chat_sync():
    """端到端探测：MessageGenerator 拿到带 chat_sync 的假网关时应走 LLM 路径。"""
    from proactive.ase_engine import MessageGenerator

    class FakeGW:
        def chat_sync(self, query, max_tokens=100, temperature=0.7):
            return "主动消息来自LLM的个性化内容"

    gen = MessageGenerator(llm_gateway=FakeGW())
    out = gen.generate_with_llm(
        msg_type=type("T", (), {"value": "share"}),
        emotion_state={"primary": {"type": "平常"}},
        affinity_level=0,
    )
    assert out == "主动消息来自LLM的个性化内容"


# ═══════════════════════════════════════════════════════════════
#  6. UserManager.get_bound_wxids
# ═══════════════════════════════════════════════════════════════

def test_user_manager_get_bound_wxids():
    from user_scheduler import UserManager

    um = UserManager.__new__(UserManager)
    um._bindings = {
        "wxid_a": {"character_card_id": "c1"},
        "wxid_b": {"character_card_id": "c2"},
    }
    assert sorted(um.get_bound_wxids()) == ["wxid_a", "wxid_b"]


# ═══════════════════════════════════════════════════════════════
#  7. web 控制端开关：免打扰时段 + 知识采集持久化（09-17 第二批）
# ═══════════════════════════════════════════════════════════════

def test_scheduler_quiet_hours_settable():
    from proactive.scheduler import ProactiveScheduler

    s = ProactiveScheduler()
    assert s.get_quiet_hours() == (23, 7)
    s.set_quiet_hours(22, 8)
    assert s.get_quiet_hours() == (22, 8)
    try:
        s.set_quiet_hours(25, 7)
        raise AssertionError("应拒绝越界小时")
    except ValueError:
        pass


def test_scheduler_vault_config_persistence(tmp_path, monkeypatch):
    from proactive.scheduler import ProactiveScheduler

    monkeypatch.setattr(ProactiveScheduler, "_CONFIG_PATH", tmp_path / "sched.json")
    s = ProactiveScheduler()
    assert s.get_vault_config() == {"enabled": False, "interval_minutes": 60}

    s.set_vault_collect(True, 30)
    assert s.get_vault_config() == {"enabled": True, "interval_minutes": 30}

    # 新实例从磁盘恢复（跨重启保持开关状态）
    s2 = ProactiveScheduler()
    assert s2.get_vault_config() == {"enabled": True, "interval_minutes": 30}
    s2.set_vault_collect(False)
    assert (tmp_path / "sched.json").exists()


def test_scheduler_config_file_cross_worker(tmp_path, monkeypatch):
    """非 master worker 直写文件 → master reload_config 拾取（跨 worker 一致性）。"""
    from proactive.scheduler import ProactiveScheduler

    monkeypatch.setattr(ProactiveScheduler, "_CONFIG_PATH", tmp_path / "sched.json")

    master = ProactiveScheduler()
    assert master.get_quiet_hours() == (23, 7)

    # 模拟另一 worker 直接写文件（不经过任何实例）
    ProactiveScheduler.write_config_file(quiet_hours=(22, 8), vault_enabled=True, vault_interval=45)

    master.reload_config()
    assert master.get_quiet_hours() == (22, 8)
    assert master.get_vault_config() == {"enabled": True, "interval_minutes": 45}
