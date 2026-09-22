"""热点知识链端到端测试 — 采集→入库→供出收口（mock 搜索，禁真实网络）。

覆盖：去重/TTL/自限速/搜索全挂静默降级/配置逐键钳制/
供出口 _try_knowledge_share 热点前置与空池回退/人设相关度排序。
"""
from __future__ import annotations

import time

import pytest

from proactive.ase_engine import ASEEngine
from shisi.knowledge import hot_topics


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """状态池与配置全部重定向 tmp —— 绝不写开发机 data/hot_topics.json。"""
    monkeypatch.setattr(hot_topics, "_STATE_PATH", tmp_path / "hot_topics.json")
    monkeypatch.setattr(hot_topics, "_CONFIG_PATH", tmp_path / "hot_topics.yaml")


def _fake_search(items_by_query: dict[str, list[dict[str, str]]], err: str = ""):
    def fn(query: str, max_results: int):
        if err:
            return [], err
        return list(items_by_query.get(query, []))[:max_results], ""

    return fn


def _result(title: str, body: str = "正文内容", href: str = ""):
    return {"title": title, "body": body, "href": href or f"https://example.com/{title}"}


class _FakeLLM:
    def __init__(self, reply: str = "诶你看这个热搜好好玩"):
        self.reply = reply
        self.queries: list[str] = []

    def chat_sync(self, query: str = "", **kwargs):
        self.queries.append(query)
        return self.reply


# ── 采集与入库 ──


def test_collect_adds_and_dedups():
    items = {"q": [_result("热搜A"), _result("热搜B")]}
    stats1 = hot_topics.collect_once(search_fn=_fake_search(items), now=1000.0,
                                     config=hot_topics.load_config() | {"queries": ["q"], "max_results_per_query": 5})
    assert stats1["added"] == 2 and stats1["pool_size"] == 2
    stats2 = hot_topics.collect_once(search_fn=_fake_search(items), now=1100.0,
                                     config=hot_topics.load_config() | {"queries": ["q"], "max_results_per_query": 5})
    assert stats2["added"] == 0 and stats2["duplicates"] == 2 and stats2["pool_size"] == 2


def test_ttl_expiry_read_and_write_side():
    cfg = hot_topics.load_config() | {"queries": ["q"], "ttl_hours": 1.0}
    now0 = time.time()
    hot_topics.collect_once(search_fn=_fake_search({"q": [_result("过期项")]}), now=now0, config=cfg)
    assert len(hot_topics.read_pool(now=now0 + 3600 - 1)) == 1
    assert hot_topics.read_pool(now=now0 + 3600 + 1) == []
    assert hot_topics.get_hot_context("", now=now0 + 3600 + 1) == ""
    # 写侧剪枝：新采集一轮后，已过期旧项不再占池
    hot_topics.collect_once(search_fn=_fake_search({"q": [_result("新项")]}), now=now0 + 7200, config=cfg)
    pool = hot_topics.read_pool(now=now0 + 7200)
    assert {i["title"] for i in pool} == {"新项"}


def test_collect_if_due_self_throttles(monkeypatch):
    calls: list[str] = []

    def fake_default_search(query: str, max_results: int):
        calls.append(query)
        return [_result(f"热点-{query}")], ""

    monkeypatch.setattr(hot_topics, "_default_search", fake_default_search)
    t0 = time.time()
    r1 = hot_topics.collect_if_due(now=t0)
    n_calls_1 = len(calls)
    assert r1.get("added", 0) > 0 and n_calls_1 > 0
    r2 = hot_topics.collect_if_due(now=t0 + 59 * 60)
    assert r2 == {"skipped": "interval"}
    assert len(calls) == n_calls_1  # 限速窗口内一次搜索都不发
    r3 = hot_topics.collect_if_due(now=t0 + 61 * 60)
    assert len(calls) > n_calls_1          # 超窗后真发起了采集
    assert r3.get("added") == 0 and r3.get("duplicates", 0) > 0  # 同题全去重


def test_collect_if_due_disabled(monkeypatch, tmp_path):
    (tmp_path / "hot_topics.yaml").write_text("enabled: false\n", encoding="utf-8")
    monkeypatch.setattr(hot_topics, "_default_search",
                        lambda q, n: pytest.fail("disabled 时不得发起搜索") or ([], ""))
    assert hot_topics.collect_if_due() == {"skipped": "disabled"}


def test_search_total_failure_degrades_quietly():
    cfg = hot_topics.load_config() | {"queries": ["q1", "q2"]}
    stats = hot_topics.collect_once(search_fn=_fake_search({}, err="全部超时"), now=1000.0, config=cfg)
    assert stats["ok"] is False and len(stats["failed_queries"]) == 2
    assert hot_topics.read_pool(now=1000.0) == []  # 空池不炸、无半写状态


# ── 配置接线（无死键） ──


def test_config_defaults_when_missing_file():
    cfg = hot_topics.load_config()
    assert cfg["enabled"] is True and cfg["interval_minutes"] == 60
    assert cfg["queries"] and all(isinstance(q, str) for q in cfg["queries"])


def test_config_malformed_values_clamped(tmp_path):
    p = tmp_path / "hot.yaml"
    p.write_text(
        "enabled: true\nqueries: ['', '  ', '有效查询']\ninterval_minutes: 1\n"
        "ttl_hours: -5\nmax_pool: 99999\nmax_results_per_query: 'abc'\n",
        encoding="utf-8",
    )
    cfg = hot_topics.load_config(p)
    assert cfg["queries"] == ["有效查询"]
    assert cfg["interval_minutes"] == 5      # 钳下限
    assert cfg["ttl_hours"] == 1.0           # 钳下限
    assert cfg["max_pool"] == 200            # 钳上限
    assert cfg["max_results_per_query"] == 3  # 坏值回默认


# ── 供出收口（_try_knowledge_share） ──


def _seed_pool(title: str = "某电影定档", body: str = "口碑爆了"):
    cfg = hot_topics.load_config() | {"queries": ["q"]}
    hot_topics.collect_once(search_fn=_fake_search({"q": [_result(title, body)]}),
                            now=time.time(), config=cfg)


def test_share_prefers_hot_when_no_knowledge_func():
    """知识索引为空的会话也能经热点供出——收口在既有 share 机制。"""
    _seed_pool()
    llm = _FakeLLM()
    eng = ASEEngine(llm_gateway=llm)
    eng._knowledge_share_func = None
    eng._knowledge_character_id = "micai"
    out = eng._try_knowledge_share()
    assert out is not None and out["type"] == "share" and out["generated_by"] == "knowledge"
    assert "某电影定档" in llm.queries[0]  # 热点内容确实喂给了 LLM


def test_share_empty_pool_and_no_func_returns_none():
    eng = ASEEngine(llm_gateway=_FakeLLM())
    eng._knowledge_share_func = None
    eng._knowledge_character_id = "micai"
    assert eng._try_knowledge_share() is None


def test_share_hot_prepended_before_knowledge_context():
    _seed_pool(title="热搜第一")
    llm = _FakeLLM()
    eng = ASEEngine(llm_gateway=llm)
    eng._knowledge_share_func = lambda cid: "【知识 1】\n" + "旧知识内容" * 10
    eng._knowledge_character_id = "micai"
    out = eng._try_knowledge_share()
    assert out is not None
    prompt = llm.queries[0]
    assert prompt.index("热搜第一") < prompt.index("旧知识内容")  # 热点前置


def test_share_hot_failure_degrades_to_knowledge_only(monkeypatch):
    def boom(character_id, **kw):
        raise RuntimeError("池读取炸了")

    monkeypatch.setattr(hot_topics, "get_hot_context", boom)
    llm = _FakeLLM()
    eng = ASEEngine(llm_gateway=llm)
    eng._knowledge_share_func = lambda cid: "知识上下文足够长的一一段内容用于通过长度门槛"
    eng._knowledge_character_id = "micai"
    out = eng._try_knowledge_share()
    assert out is not None and "知识上下文" in llm.queries[0]  # 静默降级不走热点


def test_hot_context_persona_relevance_ranking(monkeypatch):
    cfg = hot_topics.load_config() | {"queries": ["q"]}
    hot_topics.collect_once(
        search_fn=_fake_search({"q": [_result("球赛结果公布"), _result("新片首映礼")]}),
        now=time.time() - 1, config=cfg,
    )
    hot_topics.collect_once(
        search_fn=_fake_search({"q": [_result("综艺录制花絮")]}),
        now=time.time(), config=cfg,
    )
    # 人设只命中"球赛"相关 token → 虽最旧仍排首位
    monkeypatch.setattr(hot_topics, "_persona_tokens", lambda cid: {"球赛"})
    ctx = hot_topics.get_hot_context("micai", max_items=1)
    assert "球赛结果公布" in ctx and "综艺" not in ctx
    # 无人设可取 → 纯新近（最新采集的综艺居首）
    monkeypatch.setattr(hot_topics, "_persona_tokens", lambda cid: set())
    ctx2 = hot_topics.get_hot_context("", max_items=1)
    assert "综艺录制花絮" in ctx2
