"""身份组运维操作审计留痕（2026-10-12 记忆域批 item2）红测。

缺陷：link_identities / unlink_identity / purge_owner_data 全程零 logger 调用
（生产 app.log 全窗口 igrp_/unlink/purge_owner = 0 命中），身份组运维零留痕，
追溯只能靠人工记 LOG.md——v1.46 unlink 拆除能闭环纯因主控手写 LOG+手工备份。

隐私红线：日志只落键指纹 `len:*尾4位`（完整 user_key 内含 wxid，不得入日志）。
本项不改任何返回值与写语义（D15 测试面零扰动）。

红测先行：link/unlink/purge 三组审计断言应先红后绿；未关联键不产生审计
记录（防日志噪声）与组 id 对账断言为一般性审计价值护栏。
"""

from __future__ import annotations

import logging
import re

import pytest

from shisi.memory.legacy.structured_memory import StructuredMemory

KEY_A = "2:wxid_aaa@im.wechat"
KEY_B = "4:wxid_bbb@im.wechat"
KEY_OTHER = "9:wxid_ccc@im.wechat"  # 无关联的第三人
KEY_C = "5:wxid_ddd@im.wechat"  # 用于构造两组合并场景
IGRP_RE = re.compile(r"igrp_[0-9a-f]{16}")  # 与 structured_memory 生成格式一致


def _fingerprint(key: str) -> str:
    """与实现同构的键指纹：只落长度+尾4位。"""
    return f"{len(key)}:*{key[-4:]}"


@pytest.fixture()
def sm(tmp_path):
    memory = StructuredMemory(str(tmp_path / "audit.db"))
    yield memory
    memory.close()


def _link_records(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if "identity_link" in r.getMessage()]


def _unlink_records(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if "identity_unlink" in r.getMessage()]


# ── 1. link 审计：组 id + 成员键指纹 + 隐私红线 ───────────────────


def test_link_identities_audits_group_and_fingerprints(sm, caplog):
    """现行零日志 = 红；审计必须含 igrp_ 组 id 与两键指纹，且不含完整 user_key。"""
    with caplog.at_level(logging.INFO, logger="structured_memory"):
        keys = sm.link_identities(KEY_A, KEY_B)
    assert sorted(keys) == sorted([KEY_A, KEY_B])
    recs = _link_records(caplog)
    assert recs, "link 身份组必须落审计日志（现行零留痕 = 红测根因）"
    msg = recs[0]
    gid_match = IGRP_RE.search(msg)
    assert gid_match, "审计必须含 igrp_ 组 id（供与库内行/备份对账）"
    assert _fingerprint(KEY_A) in msg and _fingerprint(KEY_B) in msg, "审计必须含两键指纹"
    # 隐私红线：完整 user_key（内含 wxid）不得入日志
    assert KEY_A not in msg and KEY_B not in msg
    # 对账：日志组 id 与库内行 identity_id 一致
    with sm.get_connection() as conn:
        row = conn.execute(
            "SELECT identity_id FROM user_identity_map WHERE user_key = ?", (KEY_A,)
        ).fetchone()
    assert row is not None
    assert gid_match.group(0) == row["identity_id"], "日志组 id 必须与库内 identity_id 逐字对账"


def test_link_identities_labels_new_group_and_merge(sm, caplog):
    """建新组与并组分别标注（审计可区分动作）。"""
    with caplog.at_level(logging.INFO, logger="structured_memory"):
        sm.link_identities(KEY_A, KEY_B)
    assert any("new_group" in m for m in _link_records(caplog)), "建新组须标注 new_group"
    caplog.clear()
    # 真「并组」：两组各存（A-B、C-OTHER）后链中间键 → 两组合一
    sm.link_identities(KEY_C, KEY_OTHER)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="structured_memory"):
        sm.link_identities(KEY_A, KEY_C)
    assert any("merge_groups" in m for m in _link_records(caplog)), "两组合一须标注 merge_groups"


# ── 2. unlink 审计：解绑键指纹 + 原组 id + 剩余组员数 ─────────────


def test_unlink_identity_audits_key_and_remaining_size(sm, caplog):
    sm.link_identities(KEY_A, KEY_B)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="structured_memory"):
        assert sm.unlink_identity(KEY_B) is True
    recs = _unlink_records(caplog)
    assert recs, "unlink 必须落审计日志"
    msg = recs[0]
    assert _fingerprint(KEY_B) in msg, "审计必须含解绑键指纹"
    assert IGRP_RE.search(msg), "审计必须含原组 id"
    # 精确断言（验收门②修正）：B 解绑后 A 独留=牵连影响面 remaining=1、
    # 触发清组 cleared=1——子串 "1" in msg 会与 igrp_<16hex> 随机字符巧合（假绿）。
    assert "remaining=1" in msg, "审计必须记录清组前的牵连成员数（1 人被牵连解除）"
    assert "cleared=1" in msg, "剩余 ≤1 必须标注清组动作"
    assert KEY_B not in msg, "隐私红线：完整 user_key 不得入日志"


def test_unlink_from_three_member_group_keeps_pair(sm, caplog):
    """三人组解绑一人：余 2 人不触发清组（cleared=0），审计可区分两种结局。"""
    sm.link_identities(KEY_A, KEY_B)
    sm.link_identities(KEY_B, KEY_C)  # C 并入 → 三人组
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="structured_memory"):
        assert sm.unlink_identity(KEY_A) is True
    msg = _unlink_records(caplog)[0]
    assert "remaining=2" in msg, "三人组解绑一人后余 2 人"
    assert "cleared=0" in msg, "余 ≥2 不清组"
    assert sorted(sm.identity_keys(KEY_B)) == sorted([KEY_B, KEY_C]), "其余成员保持关联"


def test_unlink_unlinked_key_is_silent_false(sm, caplog):
    """未关联键返回 False 且不产生审计记录（防日志噪声）。"""
    with caplog.at_level(logging.INFO, logger="structured_memory"):
        assert sm.unlink_identity(KEY_OTHER) is False
    assert _unlink_records(caplog) == []


# ── 3. purge 审计：owner 前缀 + 影响计数（WARNING 级）──────────────


def test_purge_owner_data_audits_owner_and_counts(sm, caplog):
    sm.add_fact("账号2的私有事实", category="general", confidence=0.9, user_key=KEY_A)
    with caplog.at_level(logging.WARNING, logger="structured_memory"):
        counts = sm.purge_owner_data(2, session_keys=[KEY_A])
    assert counts.get("user_facts") == 1
    purge_recs = [
        r for r in caplog.records
        if "identity_purge" in r.getMessage() and r.levelno >= logging.WARNING
    ]
    assert purge_recs, "purge 必须落 WARNING 审计日志"
    msg = purge_recs[0].getMessage()
    assert "2:" in msg, "审计必须含 owner 前缀"
    assert "user_facts" in msg, "审计必须含影响计数表名"


# ── 4. D15 语义零扰动：审计不改变返回值 ──────────────────────────


def test_audit_does_not_change_semantics(sm):
    """审计只加日志：link 幂等、组解析、unlink 返回值与既有契约逐字一致。"""
    assert sm.link_identities(KEY_A, KEY_B) == sorted(sm.identity_keys(KEY_A))
    assert sm.link_identities(KEY_A, KEY_B) == sorted([KEY_A, KEY_B])  # 幂等
    assert sm.unlink_identity(KEY_B) is True
    assert sm.identity_keys(KEY_A) == [KEY_A]
