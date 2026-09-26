"""CharacterKnowledgeService — 从角色卡提取知识并检索增强。

职责：
1. 从 CharaCardV2 / CharacterAggregate 提取所有可索引的知识
2. 建索引并支持检索
3. 索引持久化（缓存到磁盘，避免重复建索引）
4. 注入 LLM 上下文
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import Any

from shisi.character.models import CharaCardV2
from shisi.core.models.character_aggregate import CharacterAggregate
from utils.project_paths import project_path

from .retriever import (
    BM25Retriever,
    KeywordRetriever,
    KnowledgeChunk,
    RetrievalResult,
)
from .source_store import (
    SourceStore,
    chunk_fingerprint,
    chunks_to_dicts,
    dicts_to_chunks,
)

logger = logging.getLogger("shisi.knowledge.character_knowledge_service")

# 默认 BM25 索引缓存目录（锚定项目根，避免依赖进程 CWD —— 2026-09 全仓扫描）
_DEFAULT_INDEX_DIR = project_path("data", "knowledge")


# ── 查询扩展表 ───────────────────────────────────────────────
# 口语化提问 → 领域关键词。依据：BM25 是 2-gram 关键词匹配，用户口语提问与
# 知识库原文措辞常无交集（实测见 search() 的 docstring 注释）。
# 只在命中信号词时触发，零额外依赖、零 API 调用、零延迟增加。
_QUERY_EXPANSIONS: tuple[tuple[tuple[str, ...], str], ...] = (
    # ⚠️ 扩展词必须是**领域实词**：泛词（"日常"/"平时"）会引入大量噪声
    #    （实测：把"日常 平时"加进爱好扩展后，top-1 由正确块变成无关的"互动场景"）。
    (("爱好", "喜欢做", "平时做", "兴趣", "消遣"), "爱好 兴趣 习惯 消遣"),
    (("家人", "家庭", "父母", "亲人", "出身", "亲戚", "家里", "家有"), "家庭 家人 父母 亲属 出身 家世"),
    (("性格", "什么样的人", "脾气", "脾气秉性"), "性格 特质 脾气 内里 底色 为人"),
    (("是谁", "叫什么", "名字", "称呼"), "名字 称呼 身份"),
    (("朋友", "同伴", "同学"), "朋友 同学 同伴"),
    (("经历", "过去", "以前", "背景", "故事"), "经历 过去 背景 往事 早年"),
    (("能力", "擅长", "会什么", "技能"), "能力 擅长 技能 天赋 特长"),
    (("外貌", "长相", "样子", "身高", "穿着"), "外貌 长相 身高 穿着 模样"),
    (("学校", "班级", "工作", "职业"), "学校 班级 职业 工作 单位"),
    (("讨厌", "不喜欢", "害怕", "弱点"), "讨厌 害怕 弱点 禁忌"),
)


def _expand_query(query: str) -> str:
    """命中领域信号词时，返回**纯领域关键词串**（不含原查询）。

    ⚠️ 为什么返回纯扩展词而非"原查询 + 扩展词"：后者会让扩展路**仍带着原查询的
    噪声词**（如「你家里有什么人」中的"有"/"人"会命中无关的"女生小团体"块），
    实测导致正确块被压到第 3 位。双路检索时两路必须**真正互补**：
    原路保精度、扩展路提召回（扩展词用知识库惯用措辞）。

    无命中信号词时返回空串，调用方据此跳过双路检索。
    """
    if not query:
        return ""
    extras: list[str] = []
    for signals, expansion in _QUERY_EXPANSIONS:
        if any(s in query for s in signals):
            extras.append(expansion)
    if not extras:
        return ""
    return " ".join(dict.fromkeys(" ".join(extras).split()))


def build_character_aggregate(raw: dict[str, Any]) -> CharacterAggregate:
    """从角色卡权威真源（卡 dict）构建 CharacterAggregate。

    路由（knowledge_routes）/失效点（character_routes）/重建脚本共用，
    保证同一张卡在任何入口抽取出的知识块集合一致（含 core_anchors 与
    source_data 透传的 mes_example 等）。
    """
    from shisi.core.models.persona_profile import PersonaProfile

    return CharacterAggregate(
        id=str(raw.get("id") or "").strip() or "default",
        name=(raw.get("name") or "未命名").strip() or "未命名",
        description=raw.get("description", "") or "",
        personality_text=raw.get("personality_text", "") or "",
        scenario=raw.get("scenario", "") or "",
        creator_notes=raw.get("creator_notes", "") or "",
        persona=PersonaProfile(core_anchors=raw.get("core_anchors", []) or []),
        source_data=raw,
    )


class CharacterKnowledgeService:
    """角色知识服务 — 知识提取 + 检索 + 上下文注入。

    2026-09-27（W5）源材料与派生索引分离：
    - 上传文档 / 抓取与增强内容 / Vault 采集是**不可丢源材料**，唯一存储在
      SourceStore（``data/knowledge/sources/{cid}.json``，锁 + 单调版本）；
    - BM25 索引是**纯派生物**，由来源集合整体重建，落盘打 ``sources_version``
      戳；冷 worker 按版本判定新鲜度，过期即从源存储派生重建（无需卡在手）。
    """

    def __init__(self, use_bm25: bool = True, index_dir: str | Path | None = None,
                 source_store: SourceStore | None = None):
        self._use_bm25 = use_bm25
        self._retrievers: dict[str, KeywordRetriever | BM25Retriever] = {}
        self._chunk_counts: dict[str, int] = {}
        # 内存缓存条目对应的磁盘索引 mtime_ns（P0-7：改卡/重建脚本后必须核对，
        # 否则进程内命中缓存即永久用旧索引）
        self._index_mtimes: dict[str, int] = {}
        # 内存索引派生自的源存储版本（W5：版本判定，非 mtime 无锁覆盖）
        self._index_versions: dict[str, int] = {}
        self._index_dir = Path(index_dir) if index_dir else _DEFAULT_INDEX_DIR
        self._source_store = source_store or SourceStore(self._index_dir / "sources")

    # ── 索引持久化 ──

    def _index_path(self, character_id: str) -> Path:
        return self._index_dir / f"{character_id}.json"

    def _disk_mtime(self, character_id: str) -> int:
        """磁盘索引文件的 mtime_ns；文件不存在或不可读返回 0（视为无需核对）。"""
        try:
            return self._index_path(character_id).stat().st_mtime_ns
        except OSError:
            return 0

    def save_index(self, character_id: str) -> None:
        """将角色 BM25 索引保存到磁盘（打上所派生自的源存储版本戳）。"""
        retriever = self._retrievers.get(character_id)
        if not retriever or not isinstance(retriever, BM25Retriever):
            return
        path = self._index_path(character_id)
        retriever.save(path, sources_version=self._index_versions.get(character_id, 0))
        try:
            self._index_mtimes[character_id] = path.stat().st_mtime_ns
        except OSError:
            self._index_mtimes.pop(character_id, None)
        logger.info("BM25 索引已保存: %s (%d 块, sources_version=%s)",
                    path, self._chunk_counts.get(character_id, 0),
                    self._index_versions.get(character_id, 0))

    def load_index(self, character_id: str) -> bool:
        """从磁盘加载角色 BM25 索引。成功返回 True。"""
        path = self._index_path(character_id)
        if not path.exists():
            return False
        try:
            retriever = BM25Retriever.from_file(path)
            self._retrievers[character_id] = retriever
            self._chunk_counts[character_id] = len(retriever._chunks)  # type: ignore[attr-defined]
            self._index_versions[character_id] = int(getattr(retriever, "sources_version", 0))
            self._index_mtimes[character_id] = path.stat().st_mtime_ns
            logger.info("BM25 索引已加载: %s (%d 块)", path, self._chunk_counts[character_id])
            return True
        except Exception as e:
            logger.warning("BM25 索引加载失败，将重新构建: %s — %s", path, e)
            return False

    def _try_load_fresh(self, character_id: str, sources_version: int) -> bool:
        """磁盘索引存在且版本戳与源存储一致时加载并安装；否则不改动状态。"""
        path = self._index_path(character_id)
        if not path.exists():
            return False
        try:
            retriever = BM25Retriever.from_file(path)
        except Exception as e:  # noqa: BLE001
            logger.warning("BM25 索引加载失败，将派生重建: %s — %s", path, e)
            return False
        if int(getattr(retriever, "sources_version", 0)) != int(sources_version):
            return False
        self._retrievers[character_id] = retriever
        self._chunk_counts[character_id] = len(retriever._chunks)  # type: ignore[attr-defined]
        self._index_versions[character_id] = int(sources_version)
        with contextlib.suppress(OSError):
            self._index_mtimes[character_id] = path.stat().st_mtime_ns
        return True

    def _drop_memory(self, character_id: str) -> None:
        self._retrievers.pop(character_id, None)
        self._chunk_counts.pop(character_id, None)
        self._index_mtimes.pop(character_id, None)
        self._index_versions.pop(character_id, None)

    @staticmethod
    def _bump(store_data: dict[str, Any]) -> None:
        """来源发生变更后在重建前显式递增版本（transaction 兜底防遗漏）。"""
        store_data["version"] = int(store_data.get("version", 0)) + 1

    def _rebuild_from_store_locked(self, character_id: str, store_data: dict[str, Any]) -> None:
        """从源存储整体派生重建索引。必须在 SourceStore.transaction（锁内）调用。"""
        sources: dict[str, Any] = store_data.get("sources", {})
        chunks: list[KnowledgeChunk] = []
        card = sources.get("card")
        if card:
            chunks.extend(dicts_to_chunks(card.get("chunks", [])))
        for key in sorted(k for k in sources if k != "card"):
            chunks.extend(dicts_to_chunks(sources[key].get("chunks", [])))
        retriever = BM25Retriever() if self._use_bm25 else KeywordRetriever()
        retriever.index(chunks)
        self._retrievers[character_id] = retriever
        self._chunk_counts[character_id] = len(chunks)
        self._index_versions[character_id] = int(store_data.get("version", 0))
        self.save_index(character_id)
        logger.info("知识索引已从源存储派生重建: %s → %d 块 (sources_version=%s)",
                    character_id, len(chunks), store_data.get("version", 0))

    def _ensure_fresh_locked(self, character_id: str, store_data: dict[str, Any]) -> None:
        """锁内确保内存索引与 store_data 版本一致（不一致则加载或重建）。"""
        version = int(store_data.get("version", 0))
        if character_id in self._retrievers and self._index_versions.get(character_id) == version:
            return
        if self._try_load_fresh(character_id, version):
            return
        self._rebuild_from_store_locked(character_id, store_data)

    def ensure_index(self, character_id: str, card: CharaCardV2 | None = None,
                     character: CharacterAggregate | None = None) -> bool:
        """确保角色索引就绪（W5 版本化闭环）。

        流程：
        1. 内存索引且版本与源存储一致（且磁盘索引未被服务层之外的写入改动）→ 直接用；
        2. 否则尝试磁盘加载（版本戳一致才算新鲜）；
        3. 仍不可用 → 锁内从源存储派生重建；卡在手时先刷新 card 来源。

        store 与卡都不存在时返回 False（不凭空创建）。
        """
        if not character_id:
            return False
        store = self._source_store
        store_exists = store.exists(character_id)
        sv = store.version(character_id) if store_exists else 0

        if character_id in self._retrievers:
            disk_mtime = self._disk_mtime(character_id)
            version_fresh = self._index_versions.get(character_id, 0) == sv
            mtime_fresh = not (disk_mtime and disk_mtime != self._index_mtimes.get(character_id))
            if version_fresh and mtime_fresh:
                return True
            logger.info("索引落后于源存储或磁盘被外部更新，丢弃内存缓存: %s", character_id)
            self._drop_memory(character_id)

        if self._try_load_fresh(character_id, sv):
            return True

        if not store_exists and card is None and character is None:
            return False

        with store.transaction(character_id, create=True) as data:
            if self._try_load_fresh(character_id, int(data.get("version", 0))):
                return True
            if character is not None or card is not None:
                chunks = (
                    self._extract_from_character(character) if character is not None
                    else self._extract_from_card(card)
                )
                fp = chunk_fingerprint(chunks)
                sources: dict[str, Any] = data.setdefault("sources", {})
                cur = sources.get("card")
                if cur is None or cur.get("version") != fp:
                    sources["card"] = {"kind": "card", "version": fp, "chunks": chunks_to_dicts(chunks)}
                    self._bump(data)
            self._rebuild_from_store_locked(character_id, data)
        return True

    # ── 索引构建 ──

    def index_character(self, character_id: str, character: CharacterAggregate) -> None:
        """以角色聚合根刷新 card 来源并派生重建索引（外部来源保留）。

        W5 语义变更：旧实现创建全新检索器**替换整库**（上传/抓取知识即丢）；
        现在只替换 ``card`` 来源，其余来源（doc:/vault/external/web_enrich:）
        原样保留并参与派生。
        """
        chunks = self._extract_from_character(character)
        with self._source_store.transaction(character_id, create=True) as data:
            sources: dict[str, Any] = data.setdefault("sources", {})
            sources["card"] = {
                "kind": "card",
                "version": chunk_fingerprint(chunks),
                "chunks": chunks_to_dicts(chunks),
            }
            self._bump(data)
            self._rebuild_from_store_locked(character_id, data)
        logger.info("角色知识索引完成: %s → %d 知识块", character_id,
                    self._chunk_counts.get(character_id, 0))

    def index_from_card(self, character_id: str, card: CharaCardV2) -> None:
        """从 CharaCardV2 刷新 card 来源并派生重建索引（外部来源保留）。"""
        chunks = self._extract_from_card(card)
        with self._source_store.transaction(character_id, create=True) as data:
            sources: dict[str, Any] = data.setdefault("sources", {})
            sources["card"] = {
                "kind": "card",
                "version": chunk_fingerprint(chunks),
                "chunks": chunks_to_dicts(chunks),
            }
            self._bump(data)
            self._rebuild_from_store_locked(character_id, data)
        logger.info("角色知识索引完成(卡): %s → %d 知识块", character_id,
                    self._chunk_counts.get(character_id, 0))

    def refresh_card_source(self, character_id: str, raw: dict[str, Any] | None = None,
                            character: CharacterAggregate | None = None) -> tuple[int, bool]:
        """角色卡变更（改名/激活/编辑）后的失效入口：只替换 card 来源。

        返回 (card 来源块数, 是否发生变更)。外部来源不受影响；索引在锁内
        派生重建并落盘，其他 worker 下次 ensure 按版本自动跟进。
        """
        if character is None:
            if raw is None:
                raise ValueError("refresh_card_source 需要 raw 或 character 之一")
            character = build_character_aggregate(raw)
        chunks = self._extract_from_character(character)
        items = chunks_to_dicts(chunks)
        fp = chunk_fingerprint(chunks)
        with self._source_store.transaction(character_id, create=True) as data:
            sources: dict[str, Any] = data.setdefault("sources", {})
            cur = sources.get("card")
            if cur and cur.get("version") == fp and cur.get("chunks") == items:
                self._ensure_fresh_locked(character_id, data)
                return len(items), False
            sources["card"] = {"kind": "card", "version": fp, "chunks": items}
            self._bump(data)
            self._rebuild_from_store_locked(character_id, data)
            return len(items), True

    def upsert_source(self, character_id: str, key: str, kind: str, version: str,
                      chunks: list[KnowledgeChunk]) -> tuple[int, bool]:
        """整源替换式写入一个外部来源并派生重建（幂等，锁内）。

        返回 (该源真实块数, 是否发生变更)。同 key 同 version 同内容时
        不重建索引，只确保索引就绪 —— Vault 定期采集据此幂等。
        """
        items = chunks_to_dicts(chunks)
        with self._source_store.transaction(character_id, create=True) as data:
            sources: dict[str, Any] = data.setdefault("sources", {})
            cur = sources.get(key)
            if cur and cur.get("version") == version and cur.get("chunks") == items:
                self._ensure_fresh_locked(character_id, data)
                return len(items), False
            sources[key] = {"kind": kind, "version": version, "chunks": items}
            self._bump(data)
            self._rebuild_from_store_locked(character_id, data)
            return len(items), True

    def remove_source(self, character_id: str, key: str) -> int:
        """按来源粒度删除（如 ``doc:{doc_id}``）。返回移除块数。"""
        if not self._source_store.exists(character_id):
            return 0
        with self._source_store.transaction(character_id) as data:
            sources: dict[str, Any] = data.get("sources", {})
            src = sources.pop(key, None)
            if not src:
                return 0
            self._bump(data)
            self._rebuild_from_store_locked(character_id, data)
            return len(src.get("chunks", []))

    def forget(self, character_id: str) -> None:
        """角色删除：清内存索引、删磁盘索引与源存储（不可逆，仅删除角色时用）。"""
        self._drop_memory(character_id)
        try:
            self._index_path(character_id).unlink(missing_ok=True)
        except OSError as e:  # noqa: BLE001
            logger.warning("删除索引文件失败 %s: %s", character_id, e)
        self._source_store.delete(character_id)
        logger.info("角色知识索引与源存储已清除: %s", character_id)

    def search(self, character_id: str, query: str, top_k: int = 3) -> RetrievalResult:
        """检索角色知识（带查询扩展）。

        2026-09-18 新增查询扩展：BM25 是 2-gram 关键词匹配，对**口语化提问**召回很差。
        实测（阿哈 166 块）：「你家里有什么人」top-1 命中 4.18 分的**无关内容**
        （"女生小团体楠楠"，只因同含"人"字）；而扩展为
        「家庭 家人 父母 亲属 出身 早年」后，命中正确块（"早年家庭经历"）且分数升至 13.46。
        → 做法：命中领域信号词时，用「原查询 ∪ 扩展查询」双路检索并**交错合并**，
          首位给扩展路（其措辞更接近知识库原文），两路头部块交替进入注入窗口。
        """
        retriever = self._retrievers.get(character_id)
        if not retriever:
            return RetrievalResult()

        expanded = _expand_query(query)
        if not expanded:
            return retriever.search(query, top_k=top_k)

        # 双路互补检索后**交错合并**（扩展路占奇数位、原路占偶数位）。
        # 2026-09-20 修复：此前 ext+base 顺序拼接再截断，扩展路命中多时（如「X是谁」
        # 一路命中 8 个"身份锚点"块）会把原路的高 idf 块整体挤出注入窗口——实测
        # 米彩卡「昭阳是谁」top-8 完全丢掉含"昭阳"的原作知识块。交错保证两路
        # 各自的头部块都进入窗口，首位仍是扩展路（其措辞更贴近知识库原文）。
        ext = list(retriever.search(expanded, top_k=top_k).chunks)
        base = list(retriever.search(query, top_k=top_k).chunks)

        # 按 content 去重
        seen: set[str] = set()
        merged: list[Any] = []
        for i in range(max(len(ext), len(base))):
            for chunk in (ext[i] if i < len(ext) else None,
                          base[i] if i < len(base) else None):
                if chunk is None:
                    continue
                key = (chunk.content or "").strip()
                if key and key not in seen:
                    seen.add(key)
                    merged.append(chunk)
        return RetrievalResult(chunks=merged[:top_k], ranked=True)

    def get_knowledge_context(
        self,
        character_id: str,
        query: str,
        top_k: int = 3,
        exclude_sources: set[str] | None = None,
    ) -> str:
        """获取格式化的知识上下文，直接用于 prompt 注入。

        ``exclude_sources``：按块的来源字段排除（如卡片身份字段
        ``character_name`` / ``personality.core_anchors`` / ``description``——
        这些内容恒由角色设定/人设段以唯一 owner 注入，再以「知识」名义
        回声即成同文本双份；批6b 项11 激活每轮 RAG 后由 prompt_builder 传入）。

        W5：先 ensure_index 自愈（版本化冷加载/派生重建），任何只读路径
        （prompt 知识槽、ASE 知识分享）都不再依赖「同进程先有人建过索引」。
        """
        try:
            self.ensure_index(character_id)
        except Exception as e:  # noqa: BLE001
            logger.debug("ensure_index 失败（非阻塞）: %s — %s", character_id, e)
        result = self.search(character_id, query, top_k=top_k)
        if exclude_sources:
            result.chunks = [c for c in result.chunks if c.source not in exclude_sources]
        context = result.to_prompt_context(k=top_k)
        return context

    def has_index(self, character_id: str) -> bool:
        return character_id in self._retrievers

    def get_stats(self, character_id: str) -> dict[str, Any]:
        return {
            "indexed": character_id in self._retrievers,
            "total_chunks": self._chunk_counts.get(character_id, 0),
            "retriever_type": "bm25" if self._use_bm25 else "keyword",
        }

    def clear(self, character_id: str | None = None) -> None:
        if character_id:
            self._drop_memory(character_id)
        else:
            self._retrievers.clear()
            self._chunk_counts.clear()
            self._index_mtimes.clear()
            self._index_versions.clear()

    def add_knowledge_chunks(self, character_id: str, chunks: list[KnowledgeChunk]) -> int:
        """向指定角色追加外部知识块（幂等：按 source_id 去重，缺省补内容哈希）。

        W5 语义变更：块先落入源存储（``external`` 来源），再锁内派生重建索引。
        旧实现冷 service 上凭空新建空检索器再整体覆盖落盘 —— 磁盘既有知识
        （卡片/既有上传）即丢；现在先确保索引就绪再追加。
        返回实际新增块数（重复 source_id 不重复计入）。
        """
        if not chunks:
            return 0
        import hashlib

        prepared: list[KnowledgeChunk] = []
        for c in chunks:
            sid = c.source_id or f"ext_{hashlib.sha1(c.content.encode('utf-8')).hexdigest()[:12]}"
            prepared.append(KnowledgeChunk(content=c.content, source=c.source, source_id=sid))
        with self._source_store.transaction(character_id, create=True) as data:
            sources: dict[str, Any] = data.setdefault("sources", {})
            src = sources.setdefault("external", {"kind": "external", "version": "1", "chunks": []})
            existing = {item.get("source_id") for item in src.get("chunks", []) if isinstance(item, dict)}
            new_items = [chunks_to_dicts([c])[0] for c in prepared if c.source_id not in existing]
            if not new_items:
                self._ensure_fresh_locked(character_id, data)
                return 0
            src["chunks"] = list(src.get("chunks", [])) + new_items
            self._bump(data)
            self._rebuild_from_store_locked(character_id, data)
            return len(new_items)

    # ── 知识提取 ──

    def _extract_from_character(self, character: CharacterAggregate) -> list[KnowledgeChunk]:
        """从 CharacterAggregate 提取知识块。"""
        chunks: list[KnowledgeChunk] = []
        source_data = character.source_data or {}

        # 0. 角色名作为可检索知识块，确保"她叫什么名字"类查询能命中
        if character.name:
            chunks.append(KnowledgeChunk(
                content=f"她的名字是{character.name}，你可以称呼她{character.name}。",
                source="character_name",
            ))

        # 1. personality — 分段提取
        if character.persona.core_anchors:
            for i, anchor in enumerate(character.persona.core_anchors):
                if anchor.strip():
                    chunks.append(KnowledgeChunk(
                        content=anchor.strip(),
                        source="personality.core_anchors",
                        source_id=f"anchor_{i}",
                    ))

        # 2. description
        if character.description:
            for i, paragraph in enumerate(self._split_paragraphs(character.description)):
                chunks.append(KnowledgeChunk(
                    content=paragraph,
                    source="description",
                    source_id=f"desc_{i}",
                ))

        # 3. personality 长文本
        #    优先取聚合根字段（2026-09-18 新增 personality_text），回退 source_data
        #    以兼容按卡直建、未传 source_data 的调用方
        #    （此前仅读 source_data → 重建索引时该段恒为空，148 块掉到 136 块）。
        personality_text = (
            getattr(character, "personality_text", "")
            or source_data.get("personality", "")
            or source_data.get("data", {}).get("personality", "")
        )
        if not isinstance(personality_text, str):
            # 归一化卡的 `personality` 是**数值字典**（warmth/playfulness…），
            # 散文在 `personality_text`。数值喂给分段器会 AttributeError
            # （2026-09-21 由被静默跳过的用例暴露：卡无 personality_text 时索引即崩）。
            personality_text = ""
        if personality_text:
            for i, paragraph in enumerate(self._split_paragraphs(personality_text)):
                if paragraph.strip() and len(paragraph) > 10:
                    chunks.append(KnowledgeChunk(
                        content=paragraph.strip(),
                        source="personality",
                        source_id=f"personality_{i}",
                    ))

        # 4. scenario（同上：优先聚合根字段）
        scenario = (
            getattr(character, "scenario", "")
            or source_data.get("scenario", "")
            or source_data.get("data", {}).get("scenario", "")
        )
        if scenario:
            chunks.append(KnowledgeChunk(
                content=scenario,
                source="scenario",
            ))

        # 5. creator_notes（同上：优先聚合根字段）
        creator_notes = (
            getattr(character, "creator_notes", "")
            or source_data.get("creator_notes", "")
            or source_data.get("data", {}).get("creator_notes", "")
        )
        if creator_notes:
            for i, section in enumerate(self._split_sections(creator_notes)):
                if section.strip() and len(section) > 20:
                    chunks.append(KnowledgeChunk(
                        content=section.strip(),
                        source="creator_notes",
                        source_id=f"note_{i}",
                    ))

        # 6. example dialogues (mes_example)
        mes_example = source_data.get("mes_example", "") or source_data.get("data", {}).get("mes_example", "")
        if mes_example:
            chunks.append(KnowledgeChunk(
                content=mes_example,
                source="mes_example",
            ))

        return chunks

    def _extract_from_card(self, card: CharaCardV2) -> list[KnowledgeChunk]:
        """从 CharaCardV2 提取知识块。"""
        chunks: list[KnowledgeChunk] = []
        data = card.data

        # 0. 角色名作为可检索知识块，确保"她叫什么名字"类查询能命中
        if data.name:
            chunks.append(KnowledgeChunk(
                content=f"她的名字是{data.name}，你可以称呼她{data.name}。",
                source="card_name",
            ))

        # 1. personality 分段
        if data.personality:
            for i, paragraph in enumerate(self._split_paragraphs(data.personality)):
                if paragraph.strip() and len(paragraph) > 10:
                    chunks.append(KnowledgeChunk(
                        content=paragraph.strip(),
                        source="personality",
                        source_id=f"personality_{i}",
                    ))

        # 2. scenario
        if data.scenario:
            chunks.append(KnowledgeChunk(
                content=data.scenario,
                source="scenario",
            ))

        # 3. creator_notes 分段
        if data.creator_notes:
            for i, section in enumerate(self._split_sections(data.creator_notes)):
                if section.strip() and len(section) > 20:
                    chunks.append(KnowledgeChunk(
                        content=section.strip(),
                        source="creator_notes",
                        source_id=f"note_{i}",
                    ))

        # 4. WorldInfoBook entries
        if data.character_book:
            for entry in data.character_book.entries:
                if entry.enabled and entry.content.strip():
                    chunks.append(KnowledgeChunk(
                        content=entry.content.strip(),
                        source="world_info",
                        source_id=f"world_{entry.id}",
                    ))

        # 5. mes_example
        if data.mes_example:
            chunks.append(KnowledgeChunk(
                content=data.mes_example,
                source="mes_example",
            ))

        # 6. description
        if data.description:
            for i, paragraph in enumerate(self._split_paragraphs(data.description)):
                chunks.append(KnowledgeChunk(
                    content=paragraph,
                    source="description",
                    source_id=f"desc_{i}",
                ))

        return chunks

    @staticmethod
    def _split_paragraphs(text: str, max_len: int = 500) -> list[str]:
        """按段落分割，长段进一步切分。"""
        paragraphs = []
        for para in text.split("\n"):
            para = para.strip()
            if not para:
                continue
            if len(para) <= max_len:
                paragraphs.append(para)
            else:
                # 长段按句号切分
                import re
                sentences = re.split(r'(?<=[。！？!?])', para)
                current = ""
                for sent in sentences:
                    if not sent.strip():
                        continue
                    if len(current) + len(sent) < max_len:
                        current += sent
                    else:
                        if current:
                            paragraphs.append(current.strip())
                        current = sent
                if current:
                    paragraphs.append(current.strip())
        return paragraphs

    @staticmethod
    def _split_sections(text: str, min_len: int = 30) -> list[str]:
        """按编号标题分段（如 1. xxx / 2. xxx）。"""
        import re
        # "数字. " 或 "数字、"
        parts = re.split(r'\n(?:[\d]+[.、．]\s*)', text)
        result = []
        for part in parts:
            part = part.strip()
            if part and len(part) >= min_len:
                result.append(part)
        if not result:
            # fallback: 按段落
            result = [p.strip() for p in text.split("\n") if p.strip() and len(p.strip()) >= min_len]
        return result


# ── 单例 ──

_knowledge_service: CharacterKnowledgeService | None = None


def get_knowledge_service() -> CharacterKnowledgeService:
    global _knowledge_service
    if _knowledge_service is None:
        _knowledge_service = CharacterKnowledgeService(use_bm25=True)
    return _knowledge_service
