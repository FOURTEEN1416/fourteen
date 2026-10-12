"""跨通道事实共享（2026-10-12 用户裁决「需要解决，注意用户隔离和真正的库共享」）。

背景（生产取证，LOG 2026-10-12 逻辑谬误根治批遗留登记）：同一真人两条微信通道
在库里是两个互不相交的 user_key（owner 前缀不同 + peer wxid 不同），事实库
零交集——A 通道昨晚说过「没有空调没有暖气」，B 通道次日仍问「空调开没开」。

设计（真共享、零误伤）：
- **显式身份关联** `user_identity_map(user_key, identity_id)`：同 identity_id =
  同一真人。库里不存在任何可推导的「同一真人」信号（owner 前缀/peer wxid 都
  不能猜——隔离域 (owner, peer) 正是防好友代聊串台的硬约束），必须显式声明。
- **读侧合并**：get_facts / search_facts（FTS+LIKE）/ find_active_fact_id /
  语义向量检索的 Python 侧复核，全部按身份组扩展（user_key IN 组键）。
- **删除组同步**：任一通道否认 → 组内所有键的等价事实删除 + 全键写水位
  （防另一通道重抽复活）。
- **读侧去重**：组内两键各存等价事实（归一化同文）时收敛为一条。
- **隔离红线**：无关联键行为必须 100% 不变（identity_keys 无映射返回自身）。

红测先行：本文件先写于实现之前，全部用例应先红后绿。
"""

from __future__ import annotations

import pytest

from shisi.memory.legacy.structured_memory import StructuredMemory

KEY_A = "2:wxid_aaa@im.wechat"
KEY_B = "4:wxid_bbb@im.wechat"
KEY_OTHER = "7:wxid_ccc@im.wechat"  # 无关联的第三人（隔离红线）


@pytest.fixture()
def sm(tmp_path):
    memory = StructuredMemory(str(tmp_path / "identity.db"))
    yield memory
    memory.close()


# ── 1. 关联建立与组解析 ──────────────────────────────────────────


def test_identity_keys_without_mapping_returns_self(sm):
    """隔离红线：无映射键的组 = 自身（行为不变的地基）。"""
    assert sm.identity_keys(KEY_A) == [KEY_A]


def test_link_identities_makes_keys_mutual(sm):
    """link 后组解析互含双方；第三方键不受影响。"""
    sm.link_identities(KEY_A, KEY_B)
    assert sorted(sm.identity_keys(KEY_A)) == sorted([KEY_A, KEY_B])
    assert sorted(sm.identity_keys(KEY_B)) == sorted([KEY_A, KEY_B])
    assert sm.identity_keys(KEY_OTHER) == [KEY_OTHER]


def test_link_identities_idempotent_and_group_grows(sm):
    """重复 link 幂等；第三人并入同一组后三键互见。"""
    sm.link_identities(KEY_A, KEY_B)
    sm.link_identities(KEY_A, KEY_B)  # 幂等
    sm.link_identities(KEY_B, KEY_OTHER)
    group = sorted(sm.identity_keys(KEY_A))
    assert group == sorted([KEY_A, KEY_B, KEY_OTHER])
    assert sorted(sm.identity_keys(KEY_OTHER)) == group


def test_unlink_identity_removes_membership(sm):
    sm.link_identities(KEY_A, KEY_B)
    assert sm.unlink_identity(KEY_B)
    assert sm.identity_keys(KEY_A) == [KEY_A]
    assert sm.identity_keys(KEY_B) == [KEY_B]


# ── 2. 读侧共享（get_facts / search / 复核）──────────────────────


def _seed(sm):
    sm.add_fact("宿舍没有空调也没有暖气", category="general",
                confidence=0.9, user_key=KEY_A)
    sm.add_fact("早上六点出早操", category="commitment",
                confidence=0.8, user_key=KEY_B)
    sm.add_fact("第三方自己的秘密", category="general",
                confidence=0.9, user_key=KEY_OTHER)


def test_get_facts_merges_group_after_link(sm):
    """关联后 A 能看到 B 的事实、反之亦然（真共享）。"""
    _seed(sm)
    # 关联前互不可见
    assert all(r["user_key"] == KEY_A for r in sm.get_facts(user_key=KEY_A))
    sm.link_identities(KEY_A, KEY_B)
    facts_a = sm.get_facts(user_key=KEY_A, min_confidence=0.0)
    facts_b = sm.get_facts(user_key=KEY_B, min_confidence=0.0)
    texts_a = {r["fact"] for r in facts_a}
    texts_b = {r["fact"] for r in facts_b}
    assert "早上六点出早操" in texts_a  # A 见 B
    assert "宿舍没有空调也没有暖气" in texts_b  # B 见 A
    # 第三人隔离红线：OTHER 看不到组内事实，组内也看不到 OTHER
    texts_o = {r["fact"] for r in sm.get_facts(user_key=KEY_OTHER, min_confidence=0.0)}
    assert texts_o == {"第三方自己的秘密"}
    assert "第三方自己的秘密" not in texts_a


def test_search_facts_merges_group(sm):
    _seed(sm)
    sm.link_identities(KEY_A, KEY_B)
    hits = sm.search_facts("暖气", user_key=KEY_B)
    assert any("空调" in str(r.get("fact") or "") for r in hits)


def test_find_active_fact_id_across_group(sm):
    _seed(sm)
    sm.link_identities(KEY_A, KEY_B)
    assert sm.find_active_fact_id("宿舍没有空调也没有暖气", KEY_B) is not None
    assert sm.has_active_fact("早上六点出早操", KEY_A)
    # 隔离红线
    assert sm.find_active_fact_id("宿舍没有空调也没有暖气", KEY_OTHER) is None


def test_get_facts_group_dedup_equivalent_facts(sm):
    """两键各存等价事实（归一化同文）→ 读侧收敛为一条（不双注入）。"""
    sm.add_fact("我喜欢吃火锅。", user_key=KEY_A)
    sm.add_fact("我喜欢吃火锅", user_key=KEY_B)  # 等价（去尾句号/空白）
    sm.link_identities(KEY_A, KEY_B)
    rows = sm.get_facts(user_key=KEY_A, min_confidence=0.0)
    deduped = [r for r in rows if "火锅" in r["fact"]]
    assert len(deduped) == 1


# ── 3. 删除组同步（否认必须全线失效）────────────────────────────


def test_forget_via_one_channel_hides_from_group(sm):
    """A 通道否认 → B 通道注入面同样不可见（派生召回失效宪法）。"""
    _seed(sm)
    sm.link_identities(KEY_A, KEY_B)
    fid = sm.find_active_fact_id("宿舍没有空调也没有暖气", KEY_A)
    assert fid is not None
    assert sm.delete_fact(fid, user_key=KEY_A)
    # 组内两键都不可见、复核为 False
    assert sm.find_active_fact_id("宿舍没有空调也没有暖气", KEY_A) is None
    assert sm.find_active_fact_id("宿舍没有空调也没有暖气", KEY_B) is None
    assert not sm.has_active_fact("宿舍没有空调也没有暖气", KEY_B)


def test_forget_writes_watermarks_for_all_group_keys(sm):
    """水位必须组内全键落账（各键按各自会话进度）：任一通道后续重抽同文都会被幂等跳过。"""
    _seed(sm)
    # 各键落一条会话历史，使删除时各键的 through_chat_id ≥ 本用例的 source id
    sm.add_chat("user", "早", session_id=KEY_A)
    sm.add_chat("user", "早", session_id=KEY_B)
    sm.link_identities(KEY_A, KEY_B)
    fid = sm.find_active_fact_id("宿舍没有空调也没有暖气", KEY_A)
    sm.delete_fact(fid, user_key=KEY_A)
    # 两键用各自水位内的小 source id 重抽（模拟 extraction 回看旧会话）→ 跳过
    assert sm.is_deleted_source("宿舍没有空调也没有暖气", KEY_A, 1)
    assert sm.is_deleted_source("宿舍没有空调也没有暖气", KEY_B, 1)
    receipt = sm.add_fact_receipt(
        "宿舍没有空调也没有暖气", user_key=KEY_B, source_last_id=1,
    )
    assert receipt["action"] == "skipped_deleted_source"
    receipt_a = sm.add_fact_receipt(
        "宿舍没有空调也没有暖气", user_key=KEY_A, source_last_id=1,
    )
    assert receipt_a["action"] == "skipped_deleted_source"


def test_forget_watermark_survives_unlink(sm):
    """组内删除的水位在解除关联后仍各自生效（各键自带否认记录，非组读兜底）。"""
    _seed(sm)
    sm.add_chat("user", "早", session_id=KEY_A)
    sm.add_chat("user", "早", session_id=KEY_B)
    sm.link_identities(KEY_A, KEY_B)
    fid = sm.find_active_fact_id("宿舍没有空调也没有暖气", KEY_A)
    sm.delete_fact(fid, user_key=KEY_A)
    sm.unlink_identity(KEY_B)  # 解除后各自回单键语义
    assert sm.is_deleted_source("宿舍没有空调也没有暖气", KEY_B, 1)
    assert sm.is_deleted_source("宿舍没有空调也没有暖气", KEY_A, 1)


def test_add_fact_receipt_reinforces_across_group(sm):
    """写侧 near-dup 组化：B 通道抽取到与 A 键等价的事实 → reinforce 旧行，不双插。"""
    _seed(sm)
    sm.link_identities(KEY_A, KEY_B)
    receipt = sm.add_fact_receipt(
        "宿舍没有空调也没有暖气", user_key=KEY_B, confidence=0.95,
    )
    assert receipt["action"] == "reinforced"
    rows = [r for r in sm.get_facts(user_key=KEY_A, min_confidence=0.0)
            if "空调" in r["fact"]]
    assert len(rows) == 1
    assert float(rows[0]["confidence"]) == 0.95  # 强化取高


def test_forget_deletes_equivalent_copies_in_group(sm):
    """两键各存等价事实，A 删除后 B 侧等价副本一并失效（不留影子）。"""
    sm.add_fact("我喜欢吃火锅。", user_key=KEY_A)
    sm.add_fact("我喜欢吃火锅", user_key=KEY_B)
    sm.link_identities(KEY_A, KEY_B)
    rows = [r for r in sm.get_facts(user_key=KEY_A, min_confidence=0.0)
            if "火锅" in r["fact"]]
    assert len(rows) == 1  # 去重后只见一条（等价收敛）
    fid = rows[0]["id"]
    assert sm.delete_fact(fid, user_key=KEY_A)
    assert sm.find_active_fact_id("我喜欢吃火锅", KEY_B) is None


def test_delete_fact_still_rejects_foreign_key(sm):
    """越权红线保留：无关联的调用键删他人事实仍被拒。"""
    _seed(sm)
    fid = sm.find_active_fact_id("第三方自己的秘密", KEY_OTHER)
    assert sm.delete_fact(fid, user_key=KEY_A) is False  # 不同组 → 拒删
    assert sm.find_active_fact_id("第三方自己的秘密", KEY_OTHER) is not None


# ── 4. 兼容与边界 ────────────────────────────────────────────────


def test_get_facts_unfiltered_mode_unchanged(sm):
    """user_key=None（管理面全库读）不受身份组影响。"""
    _seed(sm)
    sm.link_identities(KEY_A, KEY_B)
    rows = sm.get_facts(min_confidence=0.0, limit=-1)
    assert len(rows) == 3


def test_get_facts_limit_applies_after_merge(sm):
    """limit 在组合并后生效（按 updated_at 排序截断，不超发）。"""
    for i in range(6):
        sm.add_fact(f"事实编号{i}", user_key=KEY_A if i % 2 == 0 else KEY_B)
    sm.link_identities(KEY_A, KEY_B)
    rows = sm.get_facts(user_key=KEY_A, min_confidence=0.0, limit=4)
    assert len(rows) == 4


def test_owner_prefix_filter_still_isolates_unlinked(sm):
    """owner_prefix 维度（管理/统计面）不受身份组扩展影响。"""
    _seed(sm)
    sm.link_identities(KEY_A, KEY_B)
    rows = sm.get_facts(min_confidence=0.0, owner_prefix="2:")
    assert all(str(r["user_key"]).startswith("2:") for r in rows)
    assert len(rows) == 1
