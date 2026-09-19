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


# ── 沉浸式的「非共处」约束（2026-09-19 第二轮修复）─────────

def test_immersive_forbids_physical_co_presence(monkeypatch, tmp_path):
    """用户复报「还是展现出小说的感觉，一个人怎么会面对面发消息」。

    根因：角色卡把关系设定成**物理共处**（当时绑定角色 62105bca 的 scenario 写
    「你刚从公交车上下来…她站在巷口等你…转身走在前面带路」，description 写
    「从小一起长大的青梅竹马…她家就在巷子尽头那栋楼」），模型据此把「关系设定」
    演成「此时此地的舞台」，写出「我尝一口」「那我走」「那喝口茶消消食」。
    故沉浸式指令必须显式声明非共处。
    """
    rm = _use_tmp(monkeypatch, tmp_path)
    text = rm.reply_mode_instruction("immersive")
    assert "不在同一个地方" in text          # 非共处
    assert "不能做动作" in text              # 只能说话
    # 实际踩到的句子应作为反例被写进指令
    assert "我尝一口" in text
    assert "那我走" in text
    # 场景设定必须被降级为「背景」，而不是此刻正在发生
    assert "背景" in text


def test_novel_is_not_hit_by_co_presence_ban(monkeypatch, tmp_path):
    """小说式本就允许画面感，不应被非共处的措辞误伤。"""
    rm = _use_tmp(monkeypatch, tmp_path)
    assert "不在同一个地方" not in rm.reply_mode_instruction("novel")
