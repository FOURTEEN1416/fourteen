"""P0 修复批 · 隐私与数据域红测（F1/F2/F3，红测先行）。

- F1 ``wechat_direct/wechat_connector.py``：消息/回复/转写**原文**不得再进日志
  ——静态 AST 守卫（防复活钉）+ send_text 成功/失败两条行为级 caplog 实证；
- F2 ``api/lifecycle.py``：``self_service_delete(background=True)`` 必须真实
  派发 ``delete_account_everywhere``（queued 作业不再无人消费；派发链顶层
  异常落作业账 ``status=failed`` + 原因）；
- F3 ``deploy/backup_manager.py``：备份 run_dir 收紧 0o700、全部产物逐个
  0o600（chmod 经模块级 ``_best_effort_chmod``，Windows 降级为尽力而为）。

全部数据落 tmp_path 沙箱，禁止触碰真实 ``data/``（夹具纪律与
``tests/test_w9_account_lifecycle.py`` 一致：显式传路径 + 重定向默认真源）。
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import importlib.util
import json
import logging
import os
import sqlite3
import types
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.database import Base, User, WechatBinding

REPO_ROOT = Path(__file__).resolve().parent.parent
CONNECTOR_SRC = REPO_ROOT / "wechat_direct" / "wechat_connector.py"
BACKUP_MANAGER_SRC = REPO_ROOT / "deploy" / "backup_manager.py"

# F2 沙箱账号
UID = 21
PEER = "sec@im.wechat"
SKEY = f"{UID}:{PEER}"


async def _drain_background_tasks() -> None:
    """等待当前事件循环上全部后台任务结束（F2 派发验收的驱动器）。"""
    for _ in range(100):
        pending = [
            t for t in asyncio.all_tasks()
            if t is not asyncio.current_task() and not t.done()
        ]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


# ═══════════════════════════════════════════════════════
# F1 · 连接器日志去原文
# ═══════════════════════════════════════════════════════


def test_f1_connector_logs_never_carry_raw_text():
    """静态守卫：wechat_connector.py 任何 logger 调用的实参不得引用原文变量。

    覆盖 text（入站消息/转写结果/追问文案）与 reply（LLM 回复）——旧实现七处
    以 text=%r / reply=%r / %.30s 落原文。本守卫同时是防复活钉。
    ``len(text)`` 长度元数据放行（int 无原文）；裸引用/切片/f-string 内插一律算违规。
    """
    tree = ast.parse(CONNECTOR_SRC.read_text(encoding="utf-8"))
    raw_vars = {"text", "reply", "asr_text"}
    violations: list[tuple[int, str]] = []

    def _scan(node: ast.AST, line: int) -> None:
        # len(...) 子树剪枝：长度表达式是元数据放行位
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "len":
            return
        if isinstance(node, ast.Name) and node.id in raw_vars:
            violations.append((line, node.id))
            return
        for child in ast.iter_child_nodes(node):
            _scan(child, line)

    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "logger"
        ):
            continue
        for arg in [*node.args, *(kw.value for kw in node.keywords)]:
            _scan(arg, node.lineno)
    assert violations == [], f"日志仍携带原文实参（行, 变量）: {violations}"


def _make_connector(tmp_path: Path):
    import wechat_direct.wechat_connector as wc

    conn = wc.WeChatConnector(None, owner_user_id=7, session_dir=tmp_path / "sess")
    conn.token = "tok"
    return conn


# 足够长（>30 字符），旧实现 text[:30] / %.30s 截断后仍完整命中
SECRET_TEXT = "绝密私聊原文绝对不能出现在日志里0123456789"


def test_f1_send_success_log_carries_metadata_only(tmp_path, monkeypatch, caplog):
    """发送成功日志只留元数据（target/长度），不落原文。"""
    import wechat_direct.wechat_connector as wc

    conn = _make_connector(tmp_path)
    try:
        monkeypatch.setattr(wc, "_send_text", lambda **kwargs: {"ret": 0})
        monkeypatch.setattr(wc, "_api_ok", lambda resp: (True, ""))
        with caplog.at_level(logging.INFO, logger="wechat_direct"):
            assert conn._send_text_unlocked(SECRET_TEXT, "peer@im.wechat") is True
        assert "微信主动发送成功" in caplog.text, "成功日志被整行移除（应保留元数据）"
        assert SECRET_TEXT not in caplog.text, "成功日志泄漏消息原文"
    finally:
        conn._msg_executor.shutdown(wait=False)


def test_f1_send_failure_log_carries_metadata_only(tmp_path, monkeypatch, caplog):
    """发送失败日志只留错误码/target/长度，不落原文（旧 %.30s 前缀也不许）。"""
    import wechat_direct.wechat_connector as wc

    conn = _make_connector(tmp_path)
    try:
        monkeypatch.setattr(wc, "_send_text", lambda **kwargs: {"ret": -1})
        monkeypatch.setattr(wc, "_api_ok", lambda resp: (False, "ret=-1 mocked"))
        with caplog.at_level(logging.WARNING, logger="wechat_direct"):
            assert conn._send_text_unlocked(SECRET_TEXT, "peer@im.wechat") is False
        assert "微信主动发送失败" in caplog.text, "失败日志被整行移除（应保留元数据）"
        assert SECRET_TEXT not in caplog.text, "失败日志泄漏消息原文（含截断前缀）"
    finally:
        conn._msg_executor.shutdown(wait=False)


# ═══════════════════════════════════════════════════════
# F2 · 自助注销真实派发
# ═══════════════════════════════════════════════════════


@pytest.fixture()
def sec_env(tmp_path, monkeypatch):
    """注销链沙箱：users.db 独立引擎 + 记忆/坟场/调度/通道全重定向（W9 同纪律）。"""
    import proactive.ase_hub as ase_hub
    import wechat_direct.channel_paths as channel_paths
    from api import lifecycle as lifecycle_mod
    from utils import affinity_state, json_state

    root = tmp_path / "sec_p0"
    (root / "characters").mkdir(parents=True)
    monkeypatch.setattr(
        channel_paths, "sessions_root", lambda: root / "wechat_sessions"
    )
    monkeypatch.setattr(ase_hub, "_STATE_DIR", root / "ase_states")
    monkeypatch.setattr(ase_hub, "_INDEX_PATH", root / "ase_states" / "index.json")
    monkeypatch.setattr(affinity_state, "_PATH", root / "affinity_state.json")
    monkeypatch.setattr(json_state, "DEFAULT_ROOT", root, raising=False)
    monkeypatch.setattr(lifecycle_mod, "job_root", lambda: root / "jobs")
    monkeypatch.setattr(
        "utils.deletion_guard.graveyard_path",
        lambda: root / "jobs" / "graveyard.json",
    )
    monkeypatch.setattr("utils.deletion_guard._local_blocked", set(), raising=False)

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(root / 'users.db').as_posix()}"
    )
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _create_all():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create_all())

    async def _seed():
        async with maker() as db:
            db.add(
                User(
                    id=UID, email="sec@example.com", username="sec",
                    hashed_password="x" * 60, role="viewer", is_active=True,
                )
            )
            db.add(
                WechatBinding(user_id=UID, wxid=PEER, character_card_id="default")
            )
            await db.commit()

    asyncio.run(_seed())

    # 记忆库（默认真源已被 conftest 重定向，这里显式传沙箱路径双保险）
    sm_path = root / "sqlite.db"
    from shisi.memory.legacy.structured_memory import StructuredMemory

    sm = StructuredMemory(str(sm_path))
    with sm.get_connection(write=True) as conn:
        for i in range(3):
            conn.execute(
                "INSERT INTO chat_history(role, content, session_id, user_key) "
                "VALUES ('user', ?, ?, ?)",
                (f"私密消息{i}", SKEY, SKEY),
            )
        conn.commit()

    env = {
        "root": root,
        "maker": maker,
        "paths": dict(
            characters_dir=root / "characters",
            knowledge_dir=root / "knowledge",
            sqlite_db=sm_path,
            agent_db=root / "agent_plane.db",
            chroma_dir=root / "chroma_db",
        ),
    }
    yield env
    asyncio.run(engine.dispose())


def _read_job(env) -> dict:
    path = env["root"] / "jobs" / f"acct-{UID}.json"
    return json.loads(path.read_text(encoding="utf-8"))


async def test_f2_background_true_really_dispatches_purge(sec_env):
    """background=True 受理仍返回 queued，但后台必须真跑完清除（queued 不悬挂）。"""
    from api import lifecycle

    env = sec_env
    receipt = await lifecycle.self_service_delete(
        UID, db_factory=env["maker"], background=True, **env["paths"]
    )
    assert receipt["status"] == "queued", "受理语义改变（响应不得宣称已删除）"

    # 冻结同步即时语义保留（派发前就生效）
    async with env["maker"]() as db:
        user = await db.get(User, UID)
        assert user is not None and user.is_active is False

    await _drain_background_tasks()

    job = _read_job(env)
    assert job["status"] == "completed", f"后台清除未完成: {job}"

    # 清除实效：users.db 主体行 + 记忆库会话归属行全部出库
    async with env["maker"]() as db:
        assert await db.get(User, UID) is None, "users.db 账号行未删除"
    from shisi.memory.legacy.structured_memory import StructuredMemory

    sm = StructuredMemory(str(env["paths"]["sqlite_db"]))
    with sm.get_connection() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM chat_history WHERE session_id = ?", (SKEY,)
        ).fetchone()[0]
    assert n == 0, "记忆库会话行未随注销清除"


async def test_f2_background_failure_lands_failed_with_reason(sec_env, monkeypatch):
    """派发链顶层异常 → 作业账落 failed + 原因（不得滞留 queued）。"""
    from api import lifecycle

    env = sec_env
    dispatched: list[bool] = []

    async def _boom(*args, **kwargs):
        dispatched.append(True)
        raise RuntimeError("注入故障：后台清除崩溃")

    monkeypatch.setattr(lifecycle, "delete_account_everywhere", _boom)
    receipt = await lifecycle.self_service_delete(
        UID, db_factory=env["maker"], background=True, **env["paths"]
    )
    assert receipt["status"] == "queued"
    await _drain_background_tasks()
    assert dispatched, "后台任务未派发：queued 作业仍无人消费"
    job = _read_job(env)
    assert job["status"] == "failed", f"顶层异常未落作业账: {job}"
    assert job.get("error"), "失败原因未落作业账"


# ═══════════════════════════════════════════════════════
# F3 · 备份产物权限收紧
# ═══════════════════════════════════════════════════════


def _load_backup_manager():
    spec = importlib.util.spec_from_file_location(
        "sec_p0_backup_manager", BACKUP_MANAGER_SRC
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _mini_db(path: Path, script: str, rows: list[str]) -> None:
    con = sqlite3.connect(path)
    con.executescript(script)
    for row in rows:
        con.execute(row)
    con.commit()
    con.close()


def _synth_project(tmp_path: Path) -> Path:
    """合成最小可备份项目（W10 synth_project 同构缩减版 + .env 机密）。"""
    root = tmp_path / "proj"
    data = root / "data"
    data.mkdir(parents=True)
    (root / "config" / "characters").mkdir(parents=True)
    (root / "main.py").write_text("# app entry\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    _mini_db(
        data / "users.db",
        "CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT);"
        "CREATE TABLE wechat_channel_sessions ("
        "id INTEGER PRIMARY KEY, owner_user_id INTEGER, peer_wxid TEXT, status TEXT);",
        ["INSERT INTO users VALUES (1, 'a@t.local')",
         "INSERT INTO wechat_channel_sessions VALUES (1, 1, 'wxid_a', 'connected')"],
    )
    _mini_db(
        data / "sqlite.db",
        "CREATE TABLE chat_history (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "session_id TEXT, role TEXT, content TEXT);",
        ["INSERT INTO chat_history (session_id, role, content) "
         "VALUES ('100:wxid_a@im.wechat', 'user', '合成消息')"],
    )
    _mini_db(
        data / "agent_plane.db",
        "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT, "
        "session_key TEXT, payload TEXT);",
        ["INSERT INTO events (type, session_key, payload) "
         "VALUES ('memory_write', '100:wxid_a@im.wechat', '{}')"],
    )
    (data / "chroma_db").mkdir()
    (data / "chroma_db" / "chroma.sqlite3").write_text("chroma-marker", encoding="utf-8")
    (root / "config" / "characters" / "card_a.json").write_text(
        '{"id": "card_a", "name": "甲"}', encoding="utf-8"
    )
    (data / "knowledge").mkdir()
    (data / "knowledge" / "card_a.json").write_text("[]", encoding="utf-8")
    (data / "scheduler_config.json").write_text('{"throttle": {}}', encoding="utf-8")
    (data / "wechat_state.json").write_text('{"ok": true}', encoding="utf-8")
    (root / ".env").write_text("JWT_SECRET=unit-test-only-not-real\n", encoding="utf-8")
    return root


def test_f3_backup_run_dir_0700_and_every_artifact_0600(tmp_path, monkeypatch):
    """备份权限契约：run_dir=0700，且 run_dir 内每个产物文件都被收紧到 0600。

    chmod 断言走模块级 ``_best_effort_chmod`` 记录器（跨平台契约；Windows 下
    真实 chmod 仅只读位语义，模式位本身不可观测）。记录器同时执行真实
    chmod（尽力而为），Linux CI 上落真实权限位。
    """
    mod = _load_backup_manager()
    proj = _synth_project(tmp_path)
    out_dir = tmp_path / "backups"

    calls: list[tuple[Path, int]] = []
    real_chmod = os.chmod

    def _recorder(path, mode):
        calls.append((Path(path), int(mode)))
        with contextlib.suppress(OSError):
            real_chmod(path, mode)

    # 修复前：模块无 _best_effort_chmod → AttributeError 即红
    monkeypatch.setattr(mod, "_best_effort_chmod", _recorder)

    code, manifest = mod.run_backup(proj, out_dir, getenv=lambda _k: None)
    assert code == 0, f"备份未完成: {manifest.get('status')}"

    run_dir = out_dir / f"backup-{manifest['run_id']}"
    artifacts = sorted(p for p in run_dir.iterdir() if p.is_file())
    assert artifacts, "备份目录无产物"

    assert any(
        p == run_dir and mode == 0o700 for p, mode in calls
    ), f"run_dir 未收紧 0700，实际 chmod 记录: {calls}"

    hardened = {p for p, mode in calls if mode == 0o600}
    missing = [a.name for a in artifacts if a not in hardened]
    assert missing == [], f"产物未逐个收紧 0600: {missing}（chmod 记录 {calls}）"


# ═══════════════════════════════════════════════════════
# F2b · 启动 lifespan 接线坟场对账（经主控授权跨域接线 run_api.py）
# ═══════════════════════════════════════════════════════


def _extract_lifespan(init_recorder) -> dict:
    """从 run_api.py 源码摘出 ``_app_lifespan`` 并在受控命名空间重建。

    不 import 整个 run_api 模块（模块级会装配 orchestrator/SessionManager 等，
    副作用过重）；依赖名（cfg/_init_and_preload/logger）以桩注入。
    """
    src = (REPO_ROOT / "api" / "run_api.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(
        n for n in tree.body
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_app_lifespan"
    )
    code = ast.get_source_segment(src, fn)
    assert code, "run_api.py 中未找到 _app_lifespan"
    ns: dict = {
        "asynccontextmanager": asynccontextmanager,
        "AsyncGenerator": AsyncGenerator,
        "cfg": types.SimpleNamespace(
            observability=types.SimpleNamespace(metrics_enabled=False)
        ),
        "_init_and_preload": init_recorder,
        "logger": logging.getLogger("api.run_api"),
    }
    exec(compile(code, "run_api.py[_app_lifespan]", "exec"), ns)  # noqa: S102 — 测试受控源
    # get_source_segment 不含装饰器行：摘出的是裸 async generator 函数，补裹装饰器
    ns["_app_lifespan"] = asynccontextmanager(ns["_app_lifespan"])
    return ns


async def test_f2b_startup_lifespan_wires_graveyard_reconcile(monkeypatch):
    """启动 lifespan 必须接线 reconcile_graveyard（备份恢复兜底的运行时入口）。"""
    from api import lifecycle

    reconcile_calls: list[bool] = []

    async def _fake_reconcile(*args, **kwargs):
        reconcile_calls.append(True)
        return [UID]

    monkeypatch.setattr(lifecycle, "reconcile_graveyard", _fake_reconcile)

    init_calls: list[bool] = []

    async def _fake_init():
        init_calls.append(True)

    ns = _extract_lifespan(_fake_init)
    async with ns["_app_lifespan"](None):
        pass
    assert init_calls, "对账必须在 DB 初始化之后（前置顺序被破坏）"
    assert reconcile_calls, "启动 lifespan 未接线 reconcile_graveyard（兜底缺失）"


async def test_f2b_reconcile_failure_does_not_block_startup(monkeypatch, caplog):
    """对账失败仅告警，不得阻断应用启动（reconcile 是兜底不是启动前提）。"""
    from api import lifecycle

    async def _boom(*args, **kwargs):
        raise RuntimeError("注入故障：对账崩溃")

    monkeypatch.setattr(lifecycle, "reconcile_graveyard", _boom)

    async def _fake_init():
        return None

    ns = _extract_lifespan(_fake_init)
    with caplog.at_level(logging.WARNING, logger="api.run_api"):
        async with ns["_app_lifespan"](None):
            pass  # 走到 yield 即启动未被阻断
    assert "注销坟场对账失败" in caplog.text, "对账失败未留下可观测告警"
