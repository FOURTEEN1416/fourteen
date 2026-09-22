"""热点知识链 —— 采集 → 全局热点池 → 经主动消息知识供出口供出。

链路（W3 热点知识链窗，2026-09-22，设计见
``docs/design/2026-09-22_热点入知识库主动消息链设计.md``）：

1. **采集**：复用 ``tools.builtin.search_tool.SearchTool``（Bing 主 + DDG 副熔断
   为现成能力），查询表来自 ``config/hot_topics.yaml``，全部为公共热点关键词，
   **零用户隐私入参**。
2. **入库**：池落 ``data/hot_topics.json``（``utils/json_state`` 为状态文件唯一
   owner，flock 跨 worker）。条目用 **epoch 绝对时刻** 存算（fetched_at /
   expires_at），规避本项目墙钟/UTC 双口径陷阱；去重键 = 归一化标题或 href；
   TTL 到期读侧过滤、写侧剪枝。
3. **供出**：``proactive/ase_engine._try_knowledge_share`` 调
   ``get_hot_context(character_id)`` 把新鲜热点前置拼入 excerpt——收口在既有
   知识供出口，不另造通道、不写角色 BM25 索引文件（41 卡索引结构零变更）。
4. **触发**：``collect_if_due()`` 同步幂等自限速，任意轮询频率下重复调用安全；
   scheduler 侧注册归主控（接线契约见 ``docs/board/W3_HANDOFF_WIRING.md``，
   手动一轮走 ``scripts/run_hot_topics_collect.py``）。

失败降级：搜索全挂 → 只记 warning 返回 ``{ok: False}``，池保留旧未过期条目；
供出侧任何异常 → 空串 → 回退既有知识索引/模板路径，链路永不因热点炸掉主动消息。
"""
from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from shisi.knowledge.retriever import KeywordRetriever
from utils import json_state
from utils.project_paths import project_path

logger = logging.getLogger("shisi.knowledge.hot_topics")

# 模块级路径（测试可 monkeypatch 重定向，与 scheduler._CONFIG_PATH 同构）
_STATE_PATH: Path = project_path("data", "hot_topics.json")
_CONFIG_PATH: Path = project_path("config", "hot_topics.yaml")

# 公共热点查询默认表——只搜公开资讯，禁用户隐私入参
DEFAULT_QUERIES: tuple[str, ...] = (
    "今日热搜 热点事件",
    "本周新闻热点 社会",
    "近期电影 电视剧 热议",
    "体育赛事 最新赛果",
    "科技 互联网 热门话题",
)

_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "queries": list(DEFAULT_QUERIES),
    "interval_minutes": 60,
    "ttl_hours": 48.0,
    "max_pool": 50,
    "max_results_per_query": 3,
    "max_items_inject": 2,
}


def _clamp_int(val: Any, lo: int, hi: int, default: int) -> int:
    try:
        f = float(val)
    except (TypeError, ValueError):
        return default
    if f != f or f in (float("inf"), float("-inf")):
        return default
    n = int(f)
    return max(lo, min(hi, n))


def _clamp_float(val: Any, lo: float, hi: float, default: float) -> float:
    try:
        f = float(val)
    except (TypeError, ValueError):
        return default
    if f != f or f in (float("inf"), float("-inf")):
        return default
    return max(lo, min(hi, f))


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """读 config/hot_topics.yaml，逐键默认值 + 畸形值钳制（缺文件/坏 yaml 全默认）。"""
    cfg = dict(_DEFAULTS)
    p = Path(path) if path is not None else _CONFIG_PATH
    raw: Any = None
    try:
        with open(p, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except FileNotFoundError:
        return cfg
    except Exception as e:  # noqa: BLE001 - 坏 yaml 视同缺省
        logger.warning("hot_topics.yaml 解析失败，全用默认值: %s", e)
        return cfg
    if not isinstance(raw, dict):
        return cfg

    cfg["enabled"] = bool(raw.get("enabled", cfg["enabled"]))
    queries = raw.get("queries")
    if isinstance(queries, list):
        cleaned = [str(q).strip() for q in queries if str(q).strip()]
        if cleaned:
            cfg["queries"] = cleaned
    cfg["interval_minutes"] = _clamp_int(raw.get("interval_minutes"), 5, 1440, cfg["interval_minutes"])
    cfg["ttl_hours"] = _clamp_float(raw.get("ttl_hours"), 1.0, 168.0, cfg["ttl_hours"])
    cfg["max_pool"] = _clamp_int(raw.get("max_pool"), 5, 200, cfg["max_pool"])
    cfg["max_results_per_query"] = _clamp_int(raw.get("max_results_per_query"), 1, 10, cfg["max_results_per_query"])
    cfg["max_items_inject"] = _clamp_int(raw.get("max_items_inject"), 1, 5, cfg["max_items_inject"])
    return cfg


def _norm_key(title: str, href: str) -> str:
    t = re.sub(r"\s+", "", title or "").lower()
    return t or str(href or "").strip().lower()


def read_pool(now: float | None = None) -> list[dict[str, Any]]:
    """读池并过滤过期项（读侧 TTL）。返回按 fetched_at 降序的存活条目。"""
    ts = time.time() if now is None else now
    data = json_state.read_json(_STATE_PATH, default={})
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    alive = [i for i in items if isinstance(i, dict) and float(i.get("expires_at", 0)) > ts]
    alive.sort(key=lambda i: (float(i.get("fetched_at", 0)), str(i.get("title", ""))), reverse=True)
    return alive


def _default_search(query: str, max_results: int) -> tuple[list[dict[str, str]], str]:
    """真搜索：SearchTool（Bing 主/DDG 副，熔断内置）。返回 (结果, 错误串)。"""
    try:
        from tools.builtin.search_tool import SearchTool

        result = SearchTool().execute(query=query, max_results=max_results)
    except Exception as e:  # noqa: BLE001 - 网络/依赖异常族全吞为降级
        logger.warning("热点搜索异常（降级）: %s: %s", type(e).__name__, str(e)[:120])
        return [], type(e).__name__
    if not getattr(result, "success", False):
        return [], str(getattr(result, "error", "") or "搜索失败")
    data = result.data if isinstance(result.data, list) else []
    return data, ""


def collect_once(
    search_fn: Callable[[str, int], tuple[list[dict[str, str]], str]] | None = None,
    now: float | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """执行一轮采集：逐查询搜索 → 归一化去重 → 锁内剪枝过期 + 追加 + 池上限截断。

    永不抛出。返回 {ok, added, duplicates, failed_queries, pool_size}。
    """
    ts = time.time() if now is None else now
    cfg = config or load_config()
    search = search_fn or _default_search
    ttl_s = float(cfg["ttl_hours"]) * 3600.0

    candidates: list[dict[str, Any]] = []
    failed: list[str] = []
    for query in cfg["queries"]:
        try:
            results, err = search(str(query), int(cfg["max_results_per_query"]))
        except Exception as e:  # noqa: BLE001 - 单查询炸不影响其余
            logger.warning("热点查询失败 %r: %s", query, e)
            results, err = [], str(e)
        if err:
            failed.append(f"{query}({err})")
        for r in results or []:
            if not isinstance(r, dict):
                continue
            title = str(r.get("title", "")).strip()
            body = str(r.get("body", "")).strip()
            href = str(r.get("href", "")).strip()
            if not (title or body):
                continue
            candidates.append({
                "title": title[:120],
                "body": body[:300],
                "href": href[:300],
                "fetched_at": ts,
                "expires_at": ts + ttl_s,
            })

    stats = {"ok": not failed or bool(candidates), "added": 0, "duplicates": 0,
             "failed_queries": failed, "pool_size": 0}

    def _mutate(data: dict[str, Any]) -> dict[str, Any]:
        items = data.get("items")
        if not isinstance(items, list):
            items = []
        alive = [i for i in items if isinstance(i, dict) and float(i.get("expires_at", 0)) > ts]
        keys = {_norm_key(str(i.get("title", "")), str(i.get("href", ""))) for i in alive}
        for c in candidates:
            k = _norm_key(c["title"], c["href"])
            if not k or k in keys:
                stats["duplicates"] += 1
                continue
            keys.add(k)
            alive.append(c)
            stats["added"] += 1
        alive.sort(key=lambda i: (float(i.get("fetched_at", 0)), str(i.get("title", ""))), reverse=True)
        alive = alive[: int(cfg["max_pool"])]
        data["items"] = alive
        data["last_collect_at"] = ts
        stats["pool_size"] = len(alive)
        return data

    try:
        json_state.update_json(_STATE_PATH, _mutate, default={"items": [], "last_collect_at": 0})
    except Exception as e:  # noqa: BLE001 - 落盘炸只记日志
        logger.warning("热点池落盘失败（降级）: %s", e)
        stats["ok"] = False
        stats["error"] = str(e)[:200]
    return stats


def collect_if_due(now: float | None = None) -> dict[str, Any]:
    """自限速采集入口（交 scheduler 轮询注册；任意频率重复调用安全）。永不抛出。"""
    try:
        cfg = load_config()
        if not cfg["enabled"]:
            return {"skipped": "disabled"}
        ts = time.time() if now is None else now
        data = json_state.read_json(_STATE_PATH, default={})
        last = data.get("last_collect_at") if isinstance(data, dict) else None
        try:
            last_ts = float(last)
        except (TypeError, ValueError):
            last_ts = 0.0
        if ts - last_ts < float(cfg["interval_minutes"]) * 60.0:
            return {"skipped": "interval"}
        return collect_once(now=ts, config=cfg)
    except Exception as e:  # noqa: BLE001 - 供出/调度链上的兜底闸
        logger.warning("热点采集入口异常（降级）: %s", e)
        return {"ok": False, "error": str(e)[:200]}


def _persona_tokens(character_id: str) -> set[str]:
    """角色人设文本 → token 集合（相关度排序用）。取不到卡返回空集。"""
    if not character_id:
        return set()
    try:
        from api.deps import deps as _deps

        cm = getattr(getattr(_deps, "shisi_reg", None), "character_manager", None)
        card = cm.load_character(character_id) if cm else None
        data = getattr(card, "data", None)
        text = " ".join(
            str(getattr(data, f, "") or "")
            for f in ("name", "description", "personality")
        ) if data else ""
        return set(KeywordRetriever._tokenize(text))
    except Exception as e:  # noqa: BLE001 - 无卡可查退化为纯新近排序
        logger.debug("热点人设相关度取卡失败（退化新近序）: %s", e)
        return set()


def get_hot_context(
    character_id: str,
    now: float | None = None,
    max_items: int | None = None,
) -> str:
    """取角色可用的热点上下文（供 _try_knowledge_share 前置拼入 excerpt）。

    排序 = （人设 token 重叠相关度, 新鲜度）降序；无卡可读时人设集为空、
    自然退化为纯新近。池空/全过期/异常 → 空串（静默降级，调用方回退既有路径）。
    """
    try:
        cfg = load_config()
        if not cfg["enabled"]:
            return ""
        limit = int(max_items) if max_items else int(cfg["max_items_inject"])
        items = read_pool(now=now)
        if not items:
            return ""
        persona = _persona_tokens(character_id)
        if persona:
            def _score(i: dict[str, Any]) -> int:
                toks = set(KeywordRetriever._tokenize(
                    f"{i.get('title', '')} {i.get('body', '')}"))
                return len(toks & persona)

            items = sorted(items, key=lambda i: (_score(i), float(i.get("fetched_at", 0))), reverse=True)
        lines = [
            f"- {i.get('title', '')}：{i.get('body', '')}"
            for i in items[:limit]
        ]
        return "热点（近日公开资讯）：\n" + "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        logger.warning("热点上下文读取失败（降级空）: %s", e)
        return ""
