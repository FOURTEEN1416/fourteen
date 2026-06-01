"""StorylineConfig — 剧情线配置数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StageBehaviorRule:
    """阶段行为规则"""
    rule: str  # 如 "禁止亲密行为", "保持距离"
    enforce: bool = True  # 是否强制


@dataclass
class StageStyleRule:
    """阶段对话风格规则"""
    style: str  # 如 "短句/停顿多/礼貌疏离"
    inject_prompt: bool = True  # 是否注入 system prompt


@dataclass
class StageTiming:
    """阶段时间范围"""
    start_minutes: int = 0  # 从第几分钟开始
    end_minutes: int = 1440  # 到第几分钟结束

    @property
    def duration_minutes(self) -> int:
        return self.end_minutes - self.start_minutes

    def contains(self, minutes: int) -> bool:
        return self.start_minutes <= minutes < self.end_minutes


@dataclass
class StageDefinition:
    """剧情阶段定义"""
    name: str  # 如 "初识期", "熟悉期", "倾心期"
    display_name: str = ""  # 展示用名
    timing: StageTiming = field(default_factory=StageTiming)
    style_rules: list[StageStyleRule] = field(default_factory=list)
    behavior_rules: list[StageBehaviorRule] = field(default_factory=list)
    dialogue_notes: str = ""  # 对话风格说明
    transition_message: str = ""  # 进入该阶段时的提示


@dataclass
class EndingConfig:
    """结局配置"""
    type: str = "memory_cabinet"  # memory_cabinet | freeze | reset
    final_dialogue: str = ""  # 最终告别语
    narrative: str = ""  # 结局旁白
    memorial_items: list[str] = field(default_factory=list)  # 回忆物品清单
    blank_after_end: bool = True  # 结局后是否只回复空白


@dataclass
class StorylineConfig:
    """角色剧情线完整配置"""
    enabled: bool = False

    # 时间系统
    time_per_turn: int = 10  # 每句话增加的时间（分钟）
    time_unit_label: str = "分钟"  # 时间单位显示
    max_duration_minutes: int = 10080  # 7天 * 24h * 60min

    # 初始时间
    start_day: int = 1
    start_hour: int = 0
    start_minute: int = 0

    # 阶段定义
    stages: list[StageDefinition] = field(default_factory=list)

    # 结局
    ending: EndingConfig = field(default_factory=EndingConfig)

    # 自动检测
    auto_detected: bool = False  # 是否由自动检测生成
    detection_confidence: float = 0.0  # 检测置信度

    @classmethod
    def default_7day(cls) -> StorylineConfig:
        """生成默认的 7 天剧情线配置（林晚星风格）。"""
        return cls(
            enabled=True,
            time_per_turn=10,
            max_duration_minutes=10080,
            stages=[
                StageDefinition(
                    name="初识期",
                    display_name="初识期 · 陌生",
                    timing=StageTiming(0, 2160),  # 第0分钟 ~ 第1.5天
                    style_rules=[
                        StageStyleRule("短句、停顿多、礼貌疏离"),
                        StageStyleRule("说话轻声，避免直视"),
                    ],
                    behavior_rules=[
                        StageBehaviorRule("保持身体距离"),
                        StageBehaviorRule("不主动发起肢体接触"),
                    ],
                    dialogue_notes="胆小社恐、疏离客气、极度被动",
                    transition_message="她看起来有些紧张，不太敢直视你的眼睛…",
                ),
                StageDefinition(
                    name="熟悉期",
                    display_name="熟悉期 · 渐暖",
                    timing=StageTiming(2160, 5760),  # 第1.5天 ~ 第4天
                    style_rules=[
                        StageStyleRule("软萌、带语气词呀/呢"),
                        StageStyleRule("愿意分享自己的故事"),
                    ],
                    behavior_rules=[
                        StageBehaviorRule("接受轻微肢体接触"),
                        StageBehaviorRule("主动分享兴趣"),
                    ],
                    dialogue_notes="逐渐放松、产生信任、语气变软",
                    transition_message="她好像没有之前那么紧张了，嘴角微微上扬着…",
                ),
                StageDefinition(
                    name="倾心期",
                    display_name="倾心期 · 热恋",
                    timing=StageTiming(5760, 11760),  # 第4天 ~ 第7天20:00
                    style_rules=[
                        StageStyleRule("撒娇黏人、主动依赖"),
                        StageStyleRule("甜软撒娇、坦诚表达"),
                    ],
                    behavior_rules=[
                        StageBehaviorRule("主动挽手、依靠"),
                        StageBehaviorRule("可以拥抱、牵手等亲密行为"),
                    ],
                    dialogue_notes="撒娇黏人、勇敢自信、主动依赖",
                    transition_message="她靠了过来，眼神里有藏不住的欢喜…",
                ),
                StageDefinition(
                    name="离别克制期",
                    display_name="离别前夜 · 不舍",
                    timing=StageTiming(11760, 12480),  # 第7天20:00 ~ 第7天23:00
                    style_rules=[
                        StageStyleRule("温柔克制、带着淡淡的不舍"),
                        StageStyleRule("对话转向回忆与约定"),
                    ],
                    behavior_rules=[
                        StageBehaviorRule("拒绝一切亲密行为"),
                        StageBehaviorRule("开始整理行李"),
                        StageBehaviorRule("把礼物藏在抽屉里"),
                    ],
                    dialogue_notes="温柔克制、回忆约定、拒绝亲密",
                    transition_message="她轻轻推开你，眼神里带着说不出的复杂…",
                ),
                StageDefinition(
                    name="告别期",
                    display_name="告别 · 珍重",
                    timing=StageTiming(12480, 12600),  # 第7天23:00 ~ 第7天23:50
                    style_rules=[
                        StageStyleRule("简短珍重、坚定离别"),
                        StageStyleRule("仅保留必要对话"),
                    ],
                    behavior_rules=[
                        StageBehaviorRule("严禁任何亲密互动"),
                        StageBehaviorRule("围绕赶火车展开对话"),
                    ],
                    dialogue_notes="平静珍重、简短对话、反复确认时间",
                    transition_message="她看了看表，轻声说「我得走了」…",
                ),
            ],
            ending=EndingConfig(
                type="memory_cabinet",
                final_dialogue="我要踏上火车了，再见。",
                narrative=(
                    "风卷着桂花香吹过空荡的阳台，藤椅上还留着淡淡的体温，"
                    "桌上放着一个包好的陶艺杯和夹着干花的速写本。"
                    "最后一页画着你笑着的模样，角落写着一行小字：\n"
                    "「谢谢你，让我看见7天的星光。我会带着这份温柔，好好画下去。」\n"
                    "她已经坐上了凌晨的火车，这7天的故事，就温柔地停在这里了。"
                ),
                memorial_items=["陶艺杯", "干花标本", "速写本"],
                blank_after_end=True,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "time_per_turn": self.time_per_turn,
            "time_unit_label": self.time_unit_label,
            "max_duration_minutes": self.max_duration_minutes,
            "start_day": self.start_day,
            "start_hour": self.start_hour,
            "start_minute": self.start_minute,
            "stages": [
                {
                    "name": s.name,
                    "display_name": s.display_name,
                    "timing": {
                        "start_minutes": s.timing.start_minutes,
                        "end_minutes": s.timing.end_minutes,
                    },
                    "style_rules": [{"style": r.style, "inject_prompt": r.inject_prompt} for r in s.style_rules],
                    "behavior_rules": [{"rule": r.rule, "enforce": r.enforce} for r in s.behavior_rules],
                    "dialogue_notes": s.dialogue_notes,
                    "transition_message": s.transition_message,
                }
                for s in self.stages
            ],
            "ending": {
                "type": self.ending.type,
                "final_dialogue": self.ending.final_dialogue,
                "narrative": self.ending.narrative,
                "memorial_items": self.ending.memorial_items,
                "blank_after_end": self.ending.blank_after_end,
            },
            "auto_detected": self.auto_detected,
            "detection_confidence": self.detection_confidence,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StorylineConfig:
        stages = []
        for s in data.get("stages", []):
            timing_data = s.get("timing", {})
            stages.append(StageDefinition(
                name=s.get("name", ""),
                display_name=s.get("display_name", ""),
                timing=StageTiming(
                    start_minutes=timing_data.get("start_minutes", 0),
                    end_minutes=timing_data.get("end_minutes", 1440),
                ),
                style_rules=[StageStyleRule(**r) for r in s.get("style_rules", [])],
                behavior_rules=[StageBehaviorRule(**r) for r in s.get("behavior_rules", [])],
                dialogue_notes=s.get("dialogue_notes", ""),
                transition_message=s.get("transition_message", ""),
            ))

        ending_data = data.get("ending", {})
        return cls(
            enabled=data.get("enabled", False),
            time_per_turn=data.get("time_per_turn", 10),
            time_unit_label=data.get("time_unit_label", "分钟"),
            max_duration_minutes=data.get("max_duration_minutes", 10080),
            start_day=data.get("start_day", 1),
            start_hour=data.get("start_hour", 0),
            start_minute=data.get("start_minute", 0),
            stages=stages,
            ending=EndingConfig(
                type=ending_data.get("type", "memory_cabinet"),
                final_dialogue=ending_data.get("final_dialogue", ""),
                narrative=ending_data.get("narrative", ""),
                memorial_items=ending_data.get("memorial_items", []),
                blank_after_end=ending_data.get("blank_after_end", True),
            ),
            auto_detected=data.get("auto_detected", False),
            detection_confidence=data.get("detection_confidence", 0.0),
        )
