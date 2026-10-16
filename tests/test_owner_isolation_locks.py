"""用户隔离语义专项测试：不同 owner 互不阻塞（抢救优化·隔离证据）。

背景：D16「一人一 bot 一条通道」+ AGENTS 硬约束「多用户隔离：不同微信用户的
数据（角色、记忆、剧情）必须严格隔离，不得串扰」。`_peer_lock` 的锁键由
`_session_key` 生成，隔离的正确性取决于「不同 owner_user_id 的同 peer 落
到不同锁」。

本文件补 2026-10-16 对标分析发现的缺口：既有锁测试
（test_proactive_segmented_delivery.py::test_real_connector_holds_peer_lock_during_segments）
只验证了「同 owner 同 peer 持锁期间他人拿不到」，没有验证**「不同 owner 互不阻塞」**
——后者才是隔离的直接证据。

风险锚点（三处，全部实测；按**符号**引用而非行号——行号随并发窗口漂移）：
- `WeChatConnector._session_key`：`owner_user_id is None` 时
  退化为裸 peer_wxid（全局共享锁）；非 None 时 `f"{int(owner)}:{peer}"`（隔离锁）。
- `WeChatConnector._peer_lock`：按 session_key 建独立 RLock。
- 若某处把 owner_user_id 误传 None，两个用户的同名 peer 会共享一把锁（隔离失效）。

本文件只做「读取锁对象 / 并发占用检测」，不改生产代码。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from wechat_direct import wechat_connector as wc


def _mk_connector(owner_user_id):
    conn = wc.WeChatConnector(SimpleNamespace(), owner_user_id=owner_user_id)
    conn.token = "test-token"
    return conn


def test_session_key_isolation_by_owner():
    """同一 peer、不同 owner ⇒ session_key 不同 ⇒ 落到不同的锁。"""
    a = _mk_connector(1)
    b = _mk_connector(2)
    peer = "wxid_same"
    ka = a._session_key(peer)
    kb = b._session_key(peer)
    assert ka != kb, f"不同 owner 的 session_key 不应相同: {ka!r} vs {kb!r}"
    assert ka == "1:wxid_same"
    assert kb == "2:wxid_same"

    la = a._peer_lock(ka)
    lb = b._peer_lock(kb)
    assert la is not lb, "不同 owner 的同 peer 不应共享同一把 RLock"


def test_legacy_global_channel_rejected_at_construction():
    """遗留全局通道（owner_user_id=None）在构造期即被拒绝（D16 硬约束）。

    `WeChatConnector.__init__`（`wechat_direct/wechat_connector.py` 内）强制
    owner_user_id 非空 ——
    「一人一 bot 一条通道」已从构造入口根除共享锁风险。本测试锁定该护栏，
    防止回退到「None 退化为裸 peer_wxid」的旧形态。
    """
    with pytest.raises(ValueError, match="必须绑定 owner_user_id"):
        _mk_connector(None)


def test_owner_user_id_zero_is_isolated_and_allowed():
    """owner_user_id=0 是合法 owner（非 None），构造允许且隔离。"""
    a = _mk_connector(0)
    b = _mk_connector(1)
    peer = "wxid_same"
    ka = a._session_key(peer)
    kb = b._session_key(peer)
    assert ka != kb, "owner=0 应与 owner=1 隔离"
    assert ka == "0:wxid_same"
    assert kb == "1:wxid_same"
    """抢救核心：owner A 持锁发送期间，owner B 的同 peer 发送**能立即拿到锁**。

    验证方式：owner A 的连接器在主线程持锁；另一线程用 owner B 的连接器对
    同一 peer 尝试 acquire(blocking=False)。若隔离正确，B 应拿到锁（True）；
    若隔离失效（退化共享锁），B 会被阻塞拿不到（False）。
    """
    conn_a = _mk_connector(1)
    conn_b = _mk_connector(2)
    peer = "wxid_same"

    key_a = conn_a._session_key(peer)
    key_b = conn_b._session_key(peer)
    assert key_a != key_b, "前置：隔离键必须不同"

    lock_a = conn_a._peer_lock(key_a)
    # 主线程持有 owner A 的锁
    lock_a.acquire()
    try:
        # 另一线程用 owner B 的锁尝试获取同 peer 的锁
        def _try_b() -> bool:
            lock_b = conn_b._peer_lock(conn_b._session_key(peer))
            got = lock_b.acquire(blocking=False)
            if got:
                lock_b.release()
            return got

        with ThreadPoolExecutor(max_workers=1) as pool:
            b_got = pool.submit(_try_b).result(timeout=3)

        assert b_got is True, (
            "owner A 持锁期间，owner B 对同 peer 应能立即拿到自己的锁。"
            "若为 False，说明两用户被同一把锁阻塞 —— 用户隔离失效。"
        )
    finally:
        lock_a.release()


def test_owner_user_id_type_normalization():
    """owner_user_id 的 int/str 混用应归一化到同一锁（防 42 与 '42' 分家）。"""
    conn_int = _mk_connector(42)
    conn_str = _mk_connector("42")
    peer = "wxid_same"
    k_int = conn_int._session_key(peer)
    k_str = conn_str._session_key(peer)
    assert k_int == k_str == "42:wxid_same", (
        f"int/str owner 应归一化到同一 session_key: {k_int!r} vs {k_str!r}"
    )
    # 建锁动作照旧执行（副作用：各连接器 _peer_locks 落键），返回对象**不**参与
    # 断言——两连接器各自维护 _peer_locks 字典，锁对象不同是预期（锁的是「通道
    # 实例内的串行」，跨实例本来就该独立）；这里只验证**键**一致，不验证对象同一。
    conn_int._peer_lock(k_int)
    conn_str._peer_lock(k_str)
    assert conn_int._session_key(peer) == conn_str._session_key(peer)
