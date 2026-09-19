"""回复模式（沉浸式真人 / 小说式）回归 —— 2026-09-19 用户要求。

「在 web 端手动控制，分成两个模式：一个是沉浸式聊天（像真人真正在聊天），
一个是像小说一样（带上动作、神态这些东西）。」

生产实证的背景：同一角色会在两种风格间**随机跳**（括号动作描写与纯口语混发），
因为 system prompt 里同时存在互相冲突的格式要求、没有明确二选一。
"""

from __future__ import annotations

import pytest


def _use_tmp(monkeypatch, tmp_path):
    from utils import reply_mode as rm

    monkeypatch.setattr(rm, "_CONFIG_PATH", tmp_path / "scheduler_config.json")
    return rm


# ── 读写与默认值 ──────────────────────────────────────────

def test_default_is_immersive(monkeypatch, tmp_path):
    rm = _use_tmp(monkeypatch, tmp_path)
    assert rm.read_reply_mode() == rm.REPLY_MODE_IMMERSIVE


def test_roundtrip(monkeypatch, tmp_path):
    rm = _use_tmp(monkeypatch, tmp_path)
    rm.write_reply_mode(rm.REPLY_MODE_NOVEL)
    assert rm.read_reply_mode() == rm.REPLY_MODE_NOVEL
    rm.write_reply_mode(rm.REPLY_MODE_IMMERSIVE)
    assert rm.read_reply_mode() == rm.REPLY_MODE_IMMERSIVE


def test_write_preserves_other_keys(monkeypatch, tmp_path):
    """reply_mode 与 quiet_hours / follow_up 共用一份跨 worker 配置文件。"""
    import json

    rm = _use_tmp(monkeypatch, tmp_path)
    (tmp_path / "scheduler_config.json").write_text(
        json.dumps({"quiet_hours": {"start": 23, "end": 7}}), encoding="utf-8",
    )
    rm.write_reply_mode(rm.REPLY_MODE_NOVEL)

    data = json.loads((tmp_path / "scheduler_config.json").read_text(encoding="utf-8"))
    assert data["quiet_hours"] == {"start": 23, "end": 7}
    assert data["reply_mode"] == "novel"


def test_invalid_value_rejected(monkeypatch, tmp_path):
    rm = _use_tmp(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        rm.write_reply_mode("poetry")


def test_invalid_stored_value_falls_back(monkeypatch, tmp_path):
    rm = _use_tmp(monkeypatch, tmp_path)
    (tmp_path / "scheduler_config.json").write_text('{"reply_mode": "poetry"}', encoding="utf-8")
    assert rm.read_reply_mode() == rm.REPLY_MODE_IMMERSIVE


def test_broken_file_falls_back(monkeypatch, tmp_path):
    rm = _use_tmp(monkeypatch, tmp_path)
    (tmp_path / "scheduler_config.json").write_text("{ not json", encoding="utf-8")
    assert rm.read_reply_mode() == rm.REPLY_MODE_IMMERSIVE


# ── 两种模式的指令必须真正互斥 ────────────────────────────

def test_immersive_forbids_action_narration(monkeypatch, tmp_path):
    rm = _use_tmp(monkeypatch, tmp_path)
    text = rm.reply_mode_instruction("immersive")
    assert "严禁括号动作" in text or "严禁" in text and "括号" in text
    assert "沉浸式" in text
    # 关键约束：不得编造对方处境（生产里「路上堵不堵/热不热」的根因）
    assert "编造" in text
    assert "否认" in text          # 对方否认过的情境必须放弃


def test_novel_allows_action_narration(monkeypatch, tmp_path):
    rm = _use_tmp(monkeypatch, tmp_path)
    text = rm.reply_mode_instruction("novel")
    assert "动作" in text and "神态" in text
    assert "允许" in text
    # 小说式同样不得凭空设定对方处境
    assert "否认" in text


def test_two_modes_are_different(monkeypatch, tmp_path):
    rm = _use_tmp(monkeypatch, tmp_path)
    assert rm.reply_mode_instruction("immersive") != rm.reply_mode_instruction("novel")


def test_instruction_follows_stored_mode(monkeypatch, tmp_path):
    rm = _use_tmp(monkeypatch, tmp_path)
    rm.write_reply_mode("novel")
    assert rm.reply_mode_instruction() == rm.reply_mode_instruction("novel")
    rm.write_reply_mode("immersive")
    assert rm.reply_mode_instruction() == rm.reply_mode_instruction("immersive")


def test_labels_exist_for_both_modes(monkeypatch, tmp_path):
    rm = _use_tmp(monkeypatch, tmp_path)
    for mode in rm.REPLY_MODES:
        assert rm.reply_mode_label(mode)
