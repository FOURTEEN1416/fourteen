"""知识源存储（SourceStore）— 不可丢源材料的唯一持久化 owner。

背景（W5 缺陷 A/C 根治）：BM25 索引曾被当作「可重建缓存」，但上传文档与
抓取/增强内容只存在于索引文件里 —— 任何整库重建/失效都会把外部知识销毁。
本模块把**源材料**与**派生索引**分离：

  - 源材料：角色卡（card 来源）、上传文档（doc:*）、Vault 采集（vault）、
    爬虫/网络增强（external / web_enrich:*）—— 存 ``data/knowledge/sources/{cid}.json``
  - 派生索引：``data/knowledge/{cid}.json``（BM25），由来源集合整体派生，
    任何时刻可从源存储重建，不含独立信息。

并发口径（任务 5：读写并发用版本或锁，不靠 mtime 加无锁覆盖）：
  - 每角色一把锁：进程内 ``threading.Lock`` + POSIX ``flock`` 跨进程互斥
    （Windows 开发机退化为进程内锁，与 ``utils/json_state`` 既有约定一致）；
  - 文件 ``version`` 单调递增：任何来源变更 bump 一次；BM25 索引落盘时打上
    ``sources_version`` 戳，加载方据此判定索引新鲜度（冷 worker 读最新版本）；
  - 所有索引写路径必须持同一把锁（见 CharacterKnowledgeService 的重建流程），
    mtime 只作为「绕过服务层的外部写入」缓存失效提示，不承担写互斥。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from utils.project_paths import project_path

from .retriever import KnowledgeChunk

logger = logging.getLogger("shisi.knowledge.source_store")

_DEFAULT_ROOT = project_path("data", "knowledge", "sources")

_process_locks: dict[str, threading.Lock] = {}
_process_locks_guard = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    key = str(path)
    with _process_locks_guard:
        lock = _process_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _process_locks[key] = lock
        return lock


@contextmanager
def _file_lock(target: Path) -> Iterator[None]:
    """跨进程互斥（POSIX ``flock``）；无 flock 平台退化为进程内锁。"""
    lock_path = target.with_name(target.name + ".lock")
    fd: int | None = None
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    except OSError as e:  # noqa: BLE001
        logger.warning("打开知识源锁失败 %s: %s", lock_path, e)
        fd = None
    if fd is None:
        yield
        return
    try:
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
        except ImportError:
            pass  # Windows 开发机：单进程场景，进程内锁已足够
        yield
    finally:
        with contextlib.suppress(ImportError, OSError):
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
        with contextlib.suppress(OSError):
            os.close(fd)


def chunk_fingerprint(chunks: list[KnowledgeChunk]) -> str:
    """来源内容指纹：对块集合做稳定哈希，用作来源 version（内容不变则不变）。"""
    canonical = json.dumps(
        [(c.source, c.source_id, c.content) for c in chunks],
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:16]


def chunks_to_dicts(chunks: list[KnowledgeChunk]) -> list[dict[str, str]]:
    return [{"content": c.content, "source": c.source, "source_id": c.source_id}
            for c in chunks]


def dicts_to_chunks(items: list[dict[str, Any]]) -> list[KnowledgeChunk]:
    return [KnowledgeChunk(
        content=str(d.get("content", "")),
        source=str(d.get("source", "")),
        source_id=str(d.get("source_id", "")),
    ) for d in items if isinstance(d, dict)]


def safe_cid(character_id: str) -> str:
    """文件名安全化：拒绝路径分隔符与相对路径穿越；保留中文等合法字符。

    与索引文件同名规则（``{character_id}.json``，见服务层 _index_path）保持一致，
    因此不能使用 ``api.path_security.sanitize_id``（它会剥掉中文角色 id）。
    """
    cid = (character_id or "").strip()
    if not cid or cid in (".", "..") or "/" in cid or "\\" in cid:
        raise ValueError(f"非法角色 id: {character_id!r}")
    return cid


class SourceStore:
    """按角色的源材料存储：``{root}/{cid}.json``。

    文件结构::

        {"version": <int 单调递增>,
         "sources": {"<key>": {"kind": str, "version": str,
                               "chunks": [{"content","source","source_id"}]}}}
    """

    def __init__(self, root: str | Path | None = None):
        self._root = Path(root) if root else _DEFAULT_ROOT
        # version 读缓存：(mtime_ns, version)。version() 在每轮 RAG ensure 上都会被
        # 调用，缓存避免热路径整文件解析；mtime 变化即失效。
        self._version_cache: dict[str, tuple[int, int]] = {}
        self._cache_guard = threading.Lock()

    # ── 路径 ──

    @property
    def root(self) -> Path:
        return self._root

    def _path(self, character_id: str) -> Path:
        return self._root / f"{safe_cid(character_id)}.json"

    # ── 读 ──

    def exists(self, character_id: str) -> bool:
        try:
            return self._path(character_id).exists()
        except ValueError:
            return False

    def read(self, character_id: str) -> dict[str, Any]:
        """容错读。缺失/损坏返回空结构（version=0），不抛异常。"""
        empty: dict[str, Any] = {"version": 0, "sources": {}}
        try:
            p = self._path(character_id)
        except ValueError:
            return empty
        try:
            if not p.exists():
                return empty
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("sources"), dict):
                logger.warning("知识源文件损坏，按空处理: %s", p)
                return empty
            data.setdefault("version", 0)
            return data
        except Exception as e:  # noqa: BLE001
            logger.warning("读取知识源失败 %s: %s", p, e)
            return empty

    def version(self, character_id: str) -> int:
        """当前版本号（带 mtime 读缓存）。文件不存在返回 0。"""
        try:
            p = self._path(character_id)
        except ValueError:
            return 0
        try:
            mtime = p.stat().st_mtime_ns
        except OSError:
            with self._cache_guard:
                self._version_cache.pop(character_id, None)
            return 0
        with self._cache_guard:
            cached = self._version_cache.get(character_id)
        if cached and cached[0] == mtime:
            return cached[1]
        data = self.read(character_id)
        version = int(data.get("version", 0))
        with self._cache_guard:
            self._version_cache[character_id] = (mtime, version)
        return version

    def all_chunks(self, character_id: str, *, exclude_keys: set[str] | None = None) -> list[KnowledgeChunk]:
        """全部来源块拼接（card 来源在前，其余按 key 稳定排序）。"""
        data = self.read(character_id)
        sources: dict[str, Any] = data.get("sources", {})
        chunks: list[KnowledgeChunk] = []
        ordered = sorted(sources.keys(), key=lambda k: (k != "card", k))
        for key in ordered:
            if exclude_keys and key in exclude_keys:
                continue
            chunks.extend(dicts_to_chunks(sources[key].get("chunks", [])))
        return chunks

    def sources_meta(self, character_id: str) -> dict[str, dict[str, Any]]:
        """来源元信息（kind/version/块数），不含块内容。"""
        data = self.read(character_id)
        return {
            key: {"kind": src.get("kind", ""), "version": src.get("version", ""),
                  "chunk_count": len(src.get("chunks", []))}
            for key, src in data.get("sources", {}).items()
        }

    # ── 写（全部持锁 + 原子替换 + 版本自增）──

    @contextmanager
    def transaction(self, character_id: str, *, create: bool = False) -> Iterator[dict[str, Any]]:
        """锁内「读 → 变更 → 原子写」。

        版本协议：**变更点显式 bump**（``data["version"] += 1``，重建派生索引
        需要在块内读到新版本）；退出时若内容有变化而未 bump，则兜底 +1，
        保证「version 严格随内容变化递增」的不变量永不被绕过。

        ``create=False`` 且文件不存在时抛 ``FileNotFoundError``
        （读路径不得凭空创建源文件）。同一把锁同时串行化派生索引的写盘
        （服务层在 transaction 块内重建并保存索引）。
        """
        try:
            p = self._path(character_id)
        except ValueError as e:
            raise ValueError(str(e)) from e
        if not create and not p.exists():
            raise FileNotFoundError(f"知识源不存在: {character_id}")
        with _lock_for(p), _file_lock(p):
            data = self.read(character_id)
            before = json.dumps(data, ensure_ascii=False, sort_keys=True)
            before_version = int(data.get("version", 0))
            yield data
            after = json.dumps(data, ensure_ascii=False, sort_keys=True)
            if after != before:
                if int(data.get("version", 0)) == before_version:
                    data["version"] = before_version + 1
                self._atomic_write(p, data)
            try:
                mtime = p.stat().st_mtime_ns
            except OSError:
                mtime = 0
            with self._cache_guard:
                self._version_cache[character_id] = (mtime, int(data.get("version", 0)))

    def upsert_source(self, character_id: str, key: str, kind: str, version: str,
                      chunks: list[KnowledgeChunk]) -> tuple[int, bool]:
        """整源替换式写入（幂等）。返回 (该源真实块数, 是否发生变更)。"""
        items = chunks_to_dicts(chunks)
        with self.transaction(character_id, create=True) as data:
            sources: dict[str, Any] = data.setdefault("sources", {})
            cur = sources.get(key)
            if cur and cur.get("version") == version and cur.get("chunks") == items:
                return len(items), False
            sources[key] = {"kind": kind, "version": version, "chunks": items}
            return len(items), True

    def remove_source(self, character_id: str, key: str) -> int:
        """按来源键删除。返回移除块数；来源不存在返回 0。"""
        with self.transaction(character_id) as data:
            sources: dict[str, Any] = data.get("sources", {})
            src = sources.pop(key, None)
            return len(src.get("chunks", [])) if src else 0

    def delete(self, character_id: str) -> None:
        """角色删除：连源文件与锁文件一并清理。"""
        with contextlib.suppress(ValueError):
            p = self._path(character_id)
            for f in (p, p.with_name(p.name + ".lock"), p.with_name(p.name + ".tmp")):
                with contextlib.suppress(OSError):
                    f.unlink(missing_ok=True)
        with self._cache_guard:
            self._version_cache.pop(character_id, None)

    # ── 内部 ──

    @staticmethod
    def _atomic_write(p: Path, data: dict[str, Any]) -> None:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)


_default_store: SourceStore | None = None


def default_source_store() -> SourceStore:
    global _default_store
    if _default_store is None:
        _default_store = SourceStore()
    return _default_store


def mark_dirty_callable(store: SourceStore, character_id: str) -> Callable[[], None]:
    """占位：保留给未来「外部直接写文件后通知」场景。"""
    def _mark() -> None:
        with store._cache_guard:
            store._version_cache.pop(character_id, None)
    return _mark
