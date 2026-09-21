"""人格应用服务 — 统一接入 shisi 角色聚合根与 prompt_builder。

底层不再直接复用 PersonaEngine 的完整 system prompt，而是：
1. 用 shisi CharacterAggregate + prompt_builder 生成基础角色/状态/记忆 prompt；
2. 把 PersonaEngine 的 5 层人格对齐规则（情感层、风格层、约束层、身份自指、锚点保护）
   作为"注入层"附加到结果中。

对外接口保持不变，PersonaEngine 仍被保留以提供 engine 属性、一致性检查等能力。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from my_character.persona_engine import (
    PersonaEngine,
    is_external_character_id,
)
from shisi.core.models.affinity_level import AffinityLevel
from shisi.core.models.character_aggregate import CharacterAggregate
from shisi.core.models.emotion_type import EmotionType
from shisi.core.models.emotional_state import EmotionalState
from shisi.core.models.persona_profile import PersonaProfile as ShisiPersonaProfile
from shisi.core.services import prompt_builder

logger = logging.getLogger("shisi.application.persona_service")


class PersonaService:
    """复用 shisi 角色模型与 prompt_builder，并注入 PersonaEngine 人格对齐规则。"""

    _EMOTION_MAP: dict[str, EmotionType] = {
        "开心": EmotionType.HAPPY,
        "伤心": EmotionType.SAD,
        "生气": EmotionType.ANGRY,
        "撒娇": EmotionType.LOVELY,
        "吃醋": EmotionType.JEALOUS,
        "傲娇": EmotionType.SULLEN,
        "温柔": EmotionType.CARING,
        "调皮": EmotionType.PLAYFUL,
        "疲惫": EmotionType.TIRED,
        "平常": EmotionType.NEUTRAL,
    }

    def __init__(
        self,
        config_loader: Any,
        llm_gateway: Any,
        emotion_engine: Any | None = None,
        **engine_kwargs: Any,
    ) -> None:
        self._engine = PersonaEngine(
            config_loader=config_loader,
            llm_gateway=llm_gateway,
            emotion_engine=emotion_engine,
            **engine_kwargs,
        )
        # 角色卡缓存：character_id -> (mtime, 展平后的卡片 dict)
        # 命中路径上 build_system_prompt 与一致性检查**每条消息各调一次**
        # _load_character_card，旧实现每次都要做磁盘 I/O + JSON 解析，未命中
        # 文件名时还要 glob 整个 config/characters 目录逐个 json.load
        # （2026-09-17 修复：热路径上最贵的同步 I/O 之一）。
        self._card_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}
        self._card_cache_max = 64

    # 6b 项9④：/api/persona/profile 与 /api/persona/evolution-log 端点直接读
    # orch._persona（即本服务）的 .profile / .get_evolution_log，此前缺委托
    # → AttributeError → 500（前端 useQueries 消费这两个端点）。

    @property
    def profile(self) -> Any:
        return self._engine.profile

    @property
    def style_coupler(self) -> Any:
        return getattr(self._engine, "_emotion_style_coupler", None)

    def get_evolution_log(self, limit: int = 50) -> list[dict]:
        return self._engine.get_evolution_log(limit)

    def check_consistency(self, response: str, emotion_state: Any = None,
                          chat_round: int = 0) -> Any:
        # _stream_mixin 的兜底分支 hasattr(persona_service, "check_consistency")
        # 过去恒为 False → 默认角色（非外部卡）的后台一致性检测整段落空。补委托。
        return self._engine.check_consistency(response, emotion_state, chat_round)

    def build_system_prompt(
        self,
        emotion_state: Any = None,
        memory_context: str = "",
        rag_context: str = "",
        chat_summary: str = "",
        world_info: str = "",
        character_id: str | None = None,
        character_overrides: dict[str, Any] | None = None,
        user_message: str = "",
        tool_context: str = "",
    ) -> str:
        """构建系统提示词。

        流程：
        1. 将 emotion_state 映射为 shisi EmotionalState；
        2. 用当前角色卡（如可用）或 PersonaEngine 的配置构造 CharacterAggregate；
        3. 通过 prompt_builder 生成基础 prompt（角色设定 + 人设 + RAG 知识 + 状态 + 对话历史 + 工具位）；
        4. 注入 PersonaEngine 的人格对齐规则：世界信息、RAG、情感层、风格层、约束层。
        """
        effective_emotion = (
            emotion_state if emotion_state is not None else self._engine.emotion.state
        )

        character = self._build_character(effective_emotion, character_id=character_id)
        # 2026-09-21：记忆层**不得**再走 prompt_builder 的 chat_history 槽
        # （该槽会被 CharacterAggregate 标成「# 对话历史」，模型会把记忆当成聊天记录，
        #  分不清哪句是用户/哪句是自己）。记忆改为独立标注段，真对话只走 messages。
        from utils.prompt_sanitize import (
            ROLE_CLARITY_RULE,
            sanitize_episodic,
            sanitize_fact_list,
            sanitize_reflections,
        )

        mem_ctx = memory_context if isinstance(memory_context, dict) else {}
        mem_parts: list[str] = ["# 记忆上下文（供参考，**不是**本轮对话记录）"]

        # 用户画像槽（A3 + AX P1）：读 EventLedger 投影；禁止编造画像外信息
        # P0-1：画像键只取结构化 user_key/_user_key——旧实现把整段 memory 字符串
        # 当键必然 miss，属"repr 兜底"谎言。
        profile_key = str(mem_ctx.get("user_key") or mem_ctx.get("_user_key") or "")
        try:
            from shisi.agent_plane.runtime import get_profile_prompt_block

            if profile_key:
                block = get_profile_prompt_block(profile_key)
                if block:
                    mem_parts.append("\n" + block)
        except Exception:  # noqa: BLE001
            try:
                from shisi.memory.legacy.user_profile import default_store

                store = default_store()
                if profile_key:
                    block = store.to_prompt_block(profile_key)
                    if block:
                        mem_parts.append("\n" + block)
            except Exception as e:  # noqa: BLE001
                logger.warning("画像槽注入失败（agent-plane 与 legacy 双路均断）: %s", e)

        if chat_summary:
            mem_parts.append("\n## 早期对话摘要（历史压缩，非用户新消息）")
            mem_parts.append(str(chat_summary)[:800])
        reflections = sanitize_reflections(mem_ctx.get("reflections"))
        if reflections:
            mem_parts.append("\n## 我对你的观察（记忆，非用户发言）")
            for insight in reflections:
                mem_parts.append(f"- {insight}")
        facts = sanitize_fact_list(mem_ctx.get("facts") or mem_ctx.get("user_facts"))
        if facts:
            mem_parts.append("\n## 我记得的（关于你的记忆，非对话原文）")
            for fact in facts:
                mem_parts.append(f"- {fact}")
        episodic = sanitize_episodic(mem_ctx.get("episodic"))
        if episodic:
            mem_parts.append("\n## 相关回忆（摘要，非对话原文）")
            for ep in episodic:
                mem_parts.append(f"- {ep}")
        # B-d 跨会话尾巴：orchestrator 已包 untrusted 信封，原样透传
        tail = str(mem_ctx.get("session_tail") or "").strip()
        if tail:
            mem_parts.append("\n" + tail)
        memory_block = "\n".join(mem_parts) if len(mem_parts) > 1 else ""
        if chat_summary and not memory_block:
            memory_block = (
                "# 记忆上下文（供参考，**不是**本轮对话记录）\n\n"
                f"## 早期对话摘要（历史压缩，非用户新消息）\n{chat_summary[:800]}"
            )

        base_prompt = prompt_builder.build(
            character,
            user_message="",  # 当前用户消息只经 messages/query 传入，避免 system 重复
            chat_history="",
            use_knowledge=True,
            use_storyline=False,
            tool_context=tool_context or "",
            # 批6b 项11：检索查询与 system 回显解耦——旧实现 user_message="" 使
            # prompt_builder 知识槽的检索门槛（query 非空）恒不满足，每轮 RAG 根本
            # 不发生，角色知识只剩 orchestrator rag_context 兜底段（rag 任务失败即
            # 当轮整体消失）。本轮原话只作检索命中，不回显进 system。
            knowledge_query=user_message,
        )

        # 身份唯一 Owner（2026-09-21 收口）：身份只来自 `_build_character` 解析到的
        # 那张卡（内置卡 or 文件卡），prompt 里不存在"默认人格 + 再剥离"的第二条路径，
        # 因此这里不需要 external 分支、不需要 strip_default_identity、不需要引擎名擦除。
        # PersonaEngine 只提供数值/风格/约束等**身份中性**的注入层。
        injection_parts: list[str] = []

        if memory_block:
            injection_parts.append(memory_block)
        # 角色归属硬约束：真对话只在 messages；system 记忆不是用户发言
        injection_parts.append(ROLE_CLARITY_RULE.strip())

        if world_info:
            injection_parts.append(f"# 世界与时间\n{world_info}")

        if rag_context and "# 角色知识库" not in base_prompt:
            # 本轮检索结果由 rag_context 承载注入（orchestrator 的 RAG 任务产出）。
            # `prompt_builder._get_knowledge_context` 在本路由**不生效**——它要求
            # user_message 非空，而这里传 ""（当前消息只走 messages，避免 system 重复），
            # 故 base_prompt 恒不含知识块，此分支即唯一注入处；守卫保留给
            # 直接带 user_message 的调用方，防止同一知识出现两份。
            injection_parts.append(f"# 角色知识库\n{rag_context}")

        emotion_layer = self._safe_engine_layer(
            "emotion", self._engine.build_emotion_layer, effective_emotion
        )
        if emotion_layer:
            injection_parts.append(emotion_layer)

        emotion_style = self._safe_engine_layer(
            "emotion_style", self._engine.build_emotion_style_segment, effective_emotion
        )
        if emotion_style:
            injection_parts.append(emotion_style)

        style_layer = self._safe_engine_layer(
            "style", self._engine.build_style_layer, effective_emotion, "", None
        )
        if style_layer:
            injection_parts.append(style_layer)

        constraint_layer = self._safe_engine_layer(
            "constraint", self._engine.build_constraint_layer
        )
        if constraint_layer:
            injection_parts.append(constraint_layer)

        if not injection_parts:
            return base_prompt

        return f"{base_prompt}\n\n" + "\n\n".join(p for p in injection_parts if p)

    @property
    def engine(self) -> PersonaEngine:
        """暴露底层引擎，供 consistency_checker 等仍依赖 PersonaEngine 的组件使用。"""
        return self._engine

    # ── 内部构建 ──────────────────────────────────────────────

    def _build_character(self, emotion_state: Any, character_id: str | None = None) -> CharacterAggregate:
        """构造 shisi CharacterAggregate —— **所有角色共用同一条构建路径**。

        2026-09-21 唯一身份路径（用户批评「先引入十四、又给十四加限制」）：
        内置「十四」不再走 PersonaEngine 基线、外部角色不再走「注入后剥离」的
        双分支。两者都先解析成**同构的角色卡字典**（`_resolve_character_card`），
        再由 `_character_from_card` 一次性构建 —— 身份只来自当前解析到的那张卡，
        因此不需要 strip_default_identity / 引擎名擦除 / 两套约束层。
        """
        emotional_state = self._map_emotional_state(emotion_state)
        card = self._resolve_character_card(character_id)
        if card:
            character = self._character_from_card(
                card, str(character_id or card.get("name") or ""), emotional_state
            )
            if character is not None:
                return character

        # 外部 ID 但角色卡缺失：**不得**回落默认「十四」身份
        placeholder = CharacterAggregate(
            id=str(character_id or "external"),
            name=str(character_id or "角色"),
            description="",
            persona=self._map_persona_from_traits({}),
        )
        placeholder.emotional_state = emotional_state
        return placeholder

    def _resolve_character_card(self, character_id: str | None) -> dict[str, Any] | None:
        """把任意 character_id 解析成**同构**的角色卡字典（唯一解析入口）。

        - 外部 id：读 `config/characters/*.json`（展平后）；缺失返回 None
        - 空 / default / demo：由 PersonaEngine 的 `config/persona.yaml` 合成一张
          内置卡（name / description / core_anchors / 数值），与文件卡同构
        """
        cid = str(character_id or "")
        if is_external_character_id(cid):
            return self._load_character_card(cid)
        return self._builtin_character_card()

    def _builtin_character_card(self) -> dict[str, Any]:
        """内置十四的角色卡形状视图（值全部来自 persona.yaml，不另写一份散文）。"""
        traits = self._engine.get_personality_traits() or {}
        profile = self._engine.profile
        return {
            "name": self._engine.get_name(),
            "description": self._engine.get_description(),
            "personality_text": "",
            # 2026-09-22：内置卡携带 persona.yaml 的扮演规则（含身份拷问应答
            # 剧本），经 _character_from_card 与文件卡同路进 PHI 位。
            "creator_notes": self._engine.get_creator_notes(),
            "scenario": "",
            "first_mes": "",
            "catchphrases": [],
            "core_anchors": list(self._engine.get_core_anchors() or []),
            "personality": {
                "warmth": profile.core_character.get("warmth", 0.7),
                "playfulness": profile.core_character.get("playfulness", 0.5),
                "independence": profile.core_character.get("independence", 0.6),
                "jealousy": profile.core_character.get("jealousy", 0.4),
                "stubbornness": profile.core_character.get("stubbornness", 0.5),
                **{k: v for k, v in traits.items() if k in {
                    "warmth", "playfulness", "independence", "jealousy", "stubbornness",
                }},
            },
            "speaking_style": dict(profile.speaking_style or {}),
        }

    def _character_from_card(
        self, card: dict[str, Any], character_id: str, emotional_state: EmotionalState
    ) -> CharacterAggregate | None:
        """由同构卡字典构建 CharacterAggregate（内置卡与文件卡共用）。"""
        if not card:
            return None

        name = card.get("name", "未命名角色")
        description = card.get("description", "")
        anchors = card.get("core_anchors", []) or []

        # 将 personality dict（如 warmth/playfulness）映射到 shisi PersonaProfile
        traits = card.get("personality", {}) if isinstance(card.get("personality"), dict) else {}
        style = card.get("speaking_style", {}) if isinstance(card.get("speaking_style"), dict) else {}

        persona = self._map_persona_from_traits(
            traits, style=style, anchors=anchors
        )

        character = CharacterAggregate(
            id=character_id,
            name=name,
            description=description,
            persona=persona,
            source_format="shisi_app_card",
            source_data=card,
            # ── 人设贴合关键字段（2026-09-18 系统性升级）──
            # 这三个字段在角色卡里覆盖率高（personality_text 23/25、scenario 24/25、
            # creator_notes 24/25），但此前**从未进入 prompt**，导致角色只有
            # 「名字 + 描述 + 一组默认数值」可用 → 所有角色普遍不贴合。
            personality_text=card.get("personality_text", "") or "",
            scenario=card.get("scenario", "") or "",
            creator_notes=card.get("creator_notes", "") or "",
            catchphrases=[str(c) for c in (card.get("catchphrases") or []) if str(c).strip()][:8],
            first_mes=str(card.get("first_mes", "") or ""),
        )
        character.emotional_state = emotional_state
        return character

    def _build_character_from_card(
        self, character_id: str, emotional_state: EmotionalState
    ) -> CharacterAggregate | None:
        """从 app 角色卡格式构建 CharacterAggregate。"""
        return self._character_from_card(
            self._load_character_card(character_id), character_id, emotional_state
        )

    def invalidate_character_cache(self, character_id: str | None = None) -> None:
        """清除角色卡缓存。

        character_id 为 None 时清空全部；否则只清指定角色。
        角色卡被更新/删除后调用，保证下一条消息读到新卡。
        """
        if character_id is None:
            self._card_cache.clear()
        else:
            self._card_cache.pop(character_id, None)

    def _load_character_card(self, character_id: str) -> dict[str, Any] | None:
        """从 config/characters 加载角色卡数据，并展平为统一格式。

        带 mtime 感知缓存：命中且文件未变更时直接返回内存副本，
        避免每条消息的磁盘 I/O + JSON 解析（含未命中文件名时的整目录 glob）。
        文件被外部改写（mtime 变化）时自动失效，无需显式清缓存。
        """
        from pathlib import Path

        from utils.character_helpers import normalize_character_card

        # 基于项目根目录构建绝对路径，避免依赖工作目录
        chars_dir = Path(__file__).resolve().parent.parent.parent / "config" / "characters"
        if not chars_dir.exists():
            return None

        direct_path = chars_dir / f"{character_id}.json"
        mtime = 0.0
        if direct_path.exists():
            try:
                mtime = direct_path.stat().st_mtime
            except OSError:
                mtime = 0.0

        cached = self._card_cache.get(character_id)
        if cached is not None and cached[0] == mtime and mtime > 0.0:
            return cached[1]

        raw_card: dict[str, Any] | None = None
        matched_path: Path | None = None

        # 1. 按文件名查
        if direct_path.exists():
            try:
                with open(direct_path, encoding="utf-8") as fh:
                    raw_card = json.load(fh)
                matched_path = direct_path
            except (OSError, json.JSONDecodeError):
                pass

        # 2. 遍历匹配内部 id 字段
        if raw_card is None:
            try:
                for f in chars_dir.glob("*.json"):
                    try:
                        with open(f, encoding="utf-8") as fh:
                            data = json.load(fh)
                        if data.get("id") == character_id:
                            raw_card = data
                            matched_path = f
                            break
                    except (OSError, json.JSONDecodeError):
                        continue
            except OSError:
                pass

        # 记录命中文件的 mtime（文件名直查与 id 遍历两条路径都要记），
        # 否则 id 遍历命中的卡片 mtime 恒为 0 → 缓存永不生效，每轮都重扫目录。
        if matched_path is not None:
            try:
                mtime = matched_path.stat().st_mtime
            except OSError:
                mtime = 0.0

        # 展平 SillyTavern 等嵌套格式，确保 name/description/personality 等字段可用
        card = normalize_character_card(raw_card) if raw_card else None
        self._cache_card(character_id, mtime, card)
        return card

    def _cache_card(
        self, character_id: str, mtime: float, card: dict[str, Any] | None
    ) -> None:
        """写入角色卡缓存（带容量上限，避免无界增长）。"""
        if len(self._card_cache) >= self._card_cache_max and character_id not in self._card_cache:
            # 简单 FIFO 淘汰：角色卡数量级远小于上限，无需 LRU 复杂度
            self._card_cache.pop(next(iter(self._card_cache)), None)
        self._card_cache[character_id] = (mtime, card)

    def _map_persona_from_traits(
        self,
        traits: dict[str, Any],
        style: dict[str, Any] | None = None,
        anchors: list[Any] | None = None,
    ) -> ShisiPersonaProfile:
        """人格数值 → shisi PersonaProfile —— 内置卡与文件卡共用的**唯一映射**。

        键名差异在收敛处一次处理：文件卡用 `expressiveness`/`emoji_freq`，
        PersonaProfile 用 `emotional_expression`。
        """
        style = style if isinstance(style, dict) else {}

        def _num(source: dict[str, Any], keys: tuple[str, ...], fallback: float) -> float:
            for key in keys:
                if key in source:
                    try:
                        return float(source[key] or fallback)
                    except (TypeError, ValueError):
                        return fallback
            return fallback

        return ShisiPersonaProfile(
            warmth=_num(traits, ("warmth",), 0.7),
            playfulness=_num(traits, ("playfulness",), 0.5),
            independence=_num(traits, ("independence",), 0.6),
            jealousy=_num(traits, ("jealousy",), 0.4),
            stubbornness=_num(traits, ("stubbornness",), 0.5),
            formality=_num(style, ("formality",), 0.3),
            emoji_frequency=_num(style, ("emoji_freq", "emoji_frequency"), 0.6),
            sentence_length=_num(style, ("sentence_length",), 0.5),
            emotional_expression=_num(style, ("expressiveness", "emotional_expression"), 0.7),
            humor=_num(style, ("humor",), 0.5),
            core_anchors=[str(a) for a in (anchors or [])],
        )

    def _map_emotional_state(self, emotion_state: Any) -> EmotionalState:
        """将 PersonaEngine/外部 emotion_state 映射为 shisi EmotionalState。"""
        if emotion_state is None:
            return EmotionalState()

        if isinstance(emotion_state, dict):
            primary = emotion_state.get("primary_emotion")
            if primary is None:
                primary_obj = emotion_state.get("primary", {})
                primary = primary_obj.get("type", "平常") if isinstance(primary_obj, dict) else primary_obj
            intensity = float(
                emotion_state.get("intensity")
                or emotion_state.get("primary", {}).get("intensity", 0.5)
            )
            energy = float(emotion_state.get("energy", 1.0))
            affinity = emotion_state.get("affinity", 0)
            level = affinity.get("level", 0) if isinstance(affinity, dict) else affinity
            affection_points = float(emotion_state.get("affection_points", 0.0))
        else:
            primary = getattr(emotion_state, "primary_emotion", None)
            if primary is not None and hasattr(primary, "value"):
                primary = primary.value
            elif primary is None:
                primary = "平常"

            intensity = float(
                getattr(emotion_state, "primary_intensity", getattr(emotion_state, "intensity", 0.5))
            )
            energy = float(getattr(emotion_state, "energy", 1.0))
            affinity = getattr(emotion_state, "affinity", 0)
            level = affinity.get("level", 0) if isinstance(affinity, dict) else affinity
            affection_points = float(getattr(emotion_state, "affection_points", 0.0))

        emotion_type = self._EMOTION_MAP.get(str(primary), EmotionType.NEUTRAL)
        affinity_level = AffinityLevel(min(8, max(0, int(level or 0))))

        return EmotionalState(
            primary_emotion=emotion_type,
            intensity=intensity,
            energy=energy,
            affinity_level=affinity_level,
            affection_points=affection_points,
        )

    def _safe_engine_layer(self, name: str, builder: Any, *args: Any, **kwargs: Any) -> str:
        """安全调用 PersonaEngine 的私有构建方法，失败时返回空字符串并记录日志。

        审计 item46：旧实现只打 debug——生产 INFO 级下注入层任一抛错即从 system
        **无声缺件**，角色行为突变无从排查。失败属功能降级，必须 WARNING 可见。
        """
        try:
            return builder(*args, **kwargs)
        except Exception:  # noqa: BLE001
            logger.warning("注入层 %s 构建失败，本轮 prompt 缺失该层", name, exc_info=True)
            return ""
