"""W8 缺陷 D（剧情线假接线）——只诚实化，不自决接线。

现状（三处只读实证）：

1. 唯一生产 prompt 路径 `shisi/application/persona_service.py` 调
   `prompt_builder.build(..., use_storyline=False)` →
   `prompt_builder._get_storyline_context(…, enabled=False)` 恒返回 ``""``；
   `CharacterAggregate.build_system_prompt` 只在 storyline_context 非空时才落
   ``# 剧情线`` 段。结论：卡里 `storyline_config.enabled=true` **对回复零影响**。
2. `shisi/storyline/engine.py` 是进程内单例，生产 4 worker = 4 份互盲时间线。
3. `storyline_state` 全仓只有读（GET/PUT 恢复）与 pop（关闭/删除时清除），
   **没有任何写入者** → 进度从不落盘，`engine.tick()` 在停用的 prompt 路径外
   无人调用。

而 UI 侧三处在承诺「这会改变她说的话」：徽章「已启用」、开关副标
「（默认关闭，角色级可选）」、保存后「✓ 已保存」。

裁决边界：接线 vs 撤除入口 = 产品决策（D 类未裁决），本批**不改任何行为**，
只让 payload 自我声明真实语义（`affects_chat`），前端据此显示"仅存档"。
本套测试钉的是「声明与事实同源」：若将来有人把 `use_storyline` 改 True，
静态钉即红，强制同批把声明一并改掉——不允许出现"偷偷生效"。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from api.routers import storyline_routes as routes
from shisi.core.models.character_aggregate import CharacterAggregate
from shisi.core.services import prompt_builder

REPO_ROOT = Path(__file__).resolve().parents[1]

STORYLINE_CONFIG = {
    "enabled": True,
    "time_per_turn": 30,
    "max_duration_minutes": 10080,
    "stages": [
        {
            "name": "初识期",
            "display_name": "初识期",
            "timing": {"start_minutes": 0, "end_minutes": 2160},
            "style_rules": [{"style": "短句克制", "inject_prompt": True}],
            "behavior_rules": [{"rule": "保持距离", "enforce": True}],
            "dialogue_notes": "礼貌疏离",
            "transition_message": "",
        },
    ],
    "ending": {"type": "separation", "narrative": "列车远去", "blank_after_end": True},
}


# ═══════════════════════════════════════════════════════════
# 1. 事实：剧情线在停用的 prompt 闸下零影响
# ═══════════════════════════════════════════════════════════


def test_enabled_config_still_yields_no_context_when_gate_closed():
    """卡里 enabled=true 也没用——闸门关闭时上下文恒空（当前生产行为）。"""
    agg = CharacterAggregate(name="合成角色", storyline_config=dict(STORYLINE_CONFIG))
    assert agg.get_storyline_config() is not None
    assert agg.get_storyline_config().enabled is True
    assert prompt_builder._get_storyline_context(agg, enabled=False) == ""
    prompt = agg.build_system_prompt(storyline_context="")
    assert "# 剧情线" not in prompt


def test_production_prompt_path_keeps_storyline_gate_closed():
    """静态钉：唯一生产调用点仍是 use_storyline=False。

    改 True 即红 —— 那不是"回退"，而是"开始生效"，必须同批把 affects_chat
    声明、前端横幅与本文件语义一并改写，禁止悄悄生效。
    """
    src = (REPO_ROOT / "shisi" / "application" / "persona_service.py").read_text(
        encoding="utf-8"
    )
    assert "use_storyline=False" in src, (
        "persona_service 的 storyline 闸状态变了：本文件的诚实声明判据需同步改写"
    )


# ═══════════════════════════════════════════════════════════
# 2. payload 自我声明：affects_chat 与事实同源
# ═══════════════════════════════════════════════════════════


@pytest.fixture()
def synth_card(tmp_path, monkeypatch):
    """合成角色卡 + 隔离目录（不碰 config/characters 的 41 张真卡）。"""
    monkeypatch.setattr(routes, "CHARACTERS_DIR", tmp_path)
    cid = "w8synthetic"
    (tmp_path / f"{cid}.json").write_text(
        json.dumps({"name": "合成角色", "description": ""}, ensure_ascii=False),
        encoding="utf-8",
    )
    return cid


def test_affects_chat_constant_is_false():
    assert routes.CHAT_AFFECTING is False


def test_get_unconfigured_declares_affects_chat(synth_card):
    payload = asyncio.run(routes.get_storyline_config(synth_card))
    assert payload["configured"] is False
    assert payload["affects_chat"] is routes.CHAT_AFFECTING


def test_get_configured_declares_affects_chat(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "CHARACTERS_DIR", tmp_path)
    cid = "w8synthetic2"
    (tmp_path / f"{cid}.json").write_text(
        json.dumps(
            {"name": "合成角色", "storyline_config": dict(STORYLINE_CONFIG)},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    payload = asyncio.run(routes.get_storyline_config(cid))
    assert payload["configured"] is True
    assert payload["enabled"] is True
    # 已保存 + 已"启用"，但仍不影响对话——三态同时可见，UI 无歧义空间
    assert payload["affects_chat"] is False


def test_put_declares_affects_chat(synth_card):
    req = routes.StorylineConfigRequest(**{**STORYLINE_CONFIG, "stages": []})
    payload = asyncio.run(routes.update_storyline_config(synth_card, req))
    assert payload["status"] == "updated"
    assert payload["affects_chat"] is False
    # 写盘行为不变（不自决撤除入口）
    saved = json.loads((Path(routes.CHARACTERS_DIR) / f"{synth_card}.json").read_text(encoding="utf-8"))
    assert saved["storyline_config"]["enabled"] is True


def test_delete_payload_stays_consistent(synth_card):
    """DELETE 不新增谎报：要么不带该字段，要么与常量一致。"""
    payload = asyncio.run(routes.delete_storyline_config(synth_card))
    assert payload.get("affects_chat", routes.CHAT_AFFECTING) is routes.CHAT_AFFECTING


# ═══════════════════════════════════════════════════════════
# 3. 前端契约：从声明字段渲染，不再承诺"保存即生效"
# ═══════════════════════════════════════════════════════════


def test_frontend_type_declares_affects_chat():
    ts = (REPO_ROOT / "frontend" / "src" / "types" / "api.ts").read_text(encoding="utf-8")
    block = ts.split("export interface StorylineConfigResponse", 1)[1]
    block = block.split("}", 1)[0]
    assert "affects_chat?" in block, "响应类型缺 affects_chat，前端只能靠猜"


def test_frontend_renders_storage_only_banner():
    src = (
        REPO_ROOT / "frontend" / "src" / "components" / "storyline" / "StorylineEditor.tsx"
    ).read_text(encoding="utf-8")
    assert "affects_chat" in src, "编辑器未读取声明字段"
    assert "仅存档" in src, "缺少「仅存档、不影响对话」横幅"
    # 承诺性副标必须撤下（改中性表述）
    assert "（默认关闭，角色级可选）" not in src
    assert "默认关闭，角色级可选" not in src


def test_frontend_badge_is_gate_on_affects_chat():
    """「已启用」徽章必须受声明字段约束，不得无条件承诺生效。"""
    src = (
        REPO_ROOT / "frontend" / "src" / "components" / "storyline" / "StorylineEditor.tsx"
    ).read_text(encoding="utf-8")
    assert 'enabled && <Badge variant="info">已启用</Badge>' not in src
    line = next((ln for ln in src.splitlines() if "已启用" in ln), "")
    assert line, "徽章文案丢失且未改为诚实表述"
    assert "affectsChat" in line, f"徽章仍无条件承诺生效：{line.strip()}"
