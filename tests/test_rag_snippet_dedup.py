"""RAG 知识槽渲染去重（2026-10-21 FIX-4）。

缺陷（生产 `event_ledger.prompt_slots` 实测）：`knowledge_snippets` 只有 1~2 个
唯一字符串却被塞进 5 条 —— event 15696（17:53:40）n=5 uniq=2，400 字符里唯一
内容 160 字符；event 15687 n=5 uniq=1，400 字符里唯一内容 80 字符。**60%~80%
是纯重复**，近 40 条 prompt_slots 里 4 条命中此形态。重复内容挤占注入窗口、
拉长 payload，对模型是纯噪声（上游检索把同一来源的多个 chunk 都返回了）。

修法：**只在渲染侧**去重（`rag_payload_to_text` / 知识槽渲染），不动知识库内容、
不改检索结果本身 —— 保持可追溯性与原始数据不变。

安全边界：去重必须「宁可漏合、不可误删」。真正不同的知识块（尤其长卡文被
切成的相邻片段）必须全部保留；只有归一化后**完全相同**、或**互为前缀/包含**
（短的全部信息已在长的里面）、或**近似度极高**（≥ 0.9）的才合并，且合并时保留
信息量更大的那一条。
"""
from __future__ import annotations

import json

import pytest

from orchestrator.context_budget import rag_payload_to_text

_DUP = "核心锚点：她喜欢猫，家里养了一只三花猫叫年糕。"


def _payload(contents: list[str]) -> dict:
    return {"results": [{"content": c} for c in contents]}


# ── 1. 生产形态复现 ─────────────────────────────────────────


def test_production_shape_collapses_to_unique():
    """复现生产形态：同一内容塞 5 条 → 渲染后只剩 1 条（uniq=1 场景）。"""
    out = rag_payload_to_text(_payload([_DUP] * 5))
    assert out.count("核心锚点") == 1, f"同一内容重复注入 5 次未被合并：{out!r}"
    assert len(out) <= len(_DUP) + 2


def test_two_unique_among_five():
    """uniq=2 / n=5 场景：渲染后只剩 2 条，且两条都在。"""
    other = "核心锚点：她讨厌香菜，闻到就想吐。"
    out = rag_payload_to_text(_payload([_DUP, _DUP, other, _DUP, other]))
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 2, f"应合并为 2 条，实得 {len(lines)}：{lines!r}"
    assert any("年糕" in ln for ln in lines)
    assert any("香菜" in ln for ln in lines)


def test_exact_duplicate_with_whitespace_variance():
    """仅空白/标点差异的重复同样合并（按归一化比较，非字面比较）。"""
    a = "核心锚点：她喜欢猫。"
    b = "  核心锚点：她喜欢猫  "
    out = rag_payload_to_text(_payload([a, b]))
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 1, f"归一化后相同却未合并：{lines!r}"


# ── 2. 安全边界：真的不同的块必须全部保留 ───────────────────


def test_distinct_chunks_all_preserved():
    """内容互不相同的块一条都不能少（防过度去重吃掉知识）。"""
    distinct = [
        "核心锚点：她喜欢猫。",
        "核心锚点：她在医院当护士。",
        "核心锚点：她怕黑，睡觉要留一盏小灯。",
        "角色设定：她是被收养长大的。",
    ]
    out = rag_payload_to_text(_payload(distinct))
    for d in distinct:
        assert d in out, f"不同内容被误删：{d!r}"


def test_adjacent_slices_of_long_card_are_kept():
    """长卡文被切成相邻片段（无重叠）→ 全部保留（不按「同源」粗暴合并）。"""
    base = "".join(f"第{i}段真正的剧情细节" for i in range(1, 12))
    chunks = [base[i : i + 60] for i in range(0, len(base), 60)]
    out = rag_payload_to_text(_payload(chunks))
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == len(chunks), f"相邻片段被误合并：{len(lines)} != {len(chunks)}"


def test_prefix_variant_keeps_the_longer_one():
    """一长一短且短的是长的前缀 → 只留长的那条（不丢尾部的补充信息）。"""
    short = "核心锚点：她喜欢猫。"
    long_ = "核心锚点：她喜欢猫。她还养了两只鹦鹉，分别叫豆豆和球球。"
    out = rag_payload_to_text(_payload([short, long_]))
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 1, f"前缀重复未合并：{lines!r}"
    assert "鹦鹉" in out, "合并时必须保留信息更全的那条"


def test_order_and_content_of_kept_lines_unchanged():
    """保留下来的行必须是原文（逐字），且首现顺序不变（可追溯性）。"""
    a = "核心锚点：她喜欢猫。"
    b = "核心锚点：她在医院当护士。"
    out = rag_payload_to_text(_payload([a, a, b]))
    assert out == f"{a}\n{b}", f"保留下来的行必须原样且保序：{out!r}"


# ── 3. 只改渲染，不动原始数据 ───────────────────────────────


def test_payload_not_mutated():
    """渲染侧去重不得改动传入的检索结果（原始数据/可追溯性）。"""
    payload = _payload([_DUP, _DUP])
    snapshot = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    rag_payload_to_text(payload)
    assert json.dumps(payload, ensure_ascii=False, sort_keys=True) == snapshot, (
        "去重篡改了原始 payload"
    )


def test_str_and_empty_payload_paths_still_work():
    """既有契约不回归：字符串原样、None/空 → 空串。"""
    assert rag_payload_to_text("已经是文本") == "已经是文本"
    assert rag_payload_to_text(None) == ""
    assert rag_payload_to_text({}) == ""


# ── 4. 知识槽渲染处（persona_service）同样不注入重复 ─────────


def _build_prompt_with_rag(monkeypatch, rag_context: str) -> str:
    from pathlib import Path

    from shisi.application.persona_service import PersonaService
    from shisi.core.services import prompt_builder as pb
    from utils.character_helpers import normalize_character_card

    class _NoIndexSvc:
        def has_index(self, cid):  # noqa: ANN001, ANN201
            return False

        def index_character(self, cid, character):  # noqa: ANN001, ANN201
            pass

        def get_knowledge_context(self, cid, query, top_k=8, exclude_sources=None):  # noqa: ANN001, ANN201
            return ""

    monkeypatch.setattr(pb, "get_knowledge_service", lambda: _NoIndexSvc())

    root = Path(__file__).resolve().parent.parent / "config" / "characters"
    for f in sorted(root.glob("*.json")):
        try:
            card = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if card.get("name") == "林挽夏":
            cid = normalize_character_card(card)["id"]
            break
    else:
        pytest.skip("未找到林挽夏角色卡（config/characters 受 gitignore，环境相关）")

    ps = PersonaService(config_loader=None, llm_gateway=None)
    return ps.build_system_prompt(
        character_id=cid, user_message="她喜欢什么", rag_context=rag_context,
    )


def test_persona_service_rag_block_has_no_duplicate_lines(monkeypatch):
    """注入 system 的 `# 角色知识库` 段不得含重复行。"""
    dup_block = "\n".join([_DUP, _DUP, _DUP])
    prompt = _build_prompt_with_rag(monkeypatch, dup_block)
    assert "# 角色知识库" in prompt, "知识段未注入（前提不成立）"
    assert prompt.count("年糕") == 1, f"知识槽渲染处未去重：count={prompt.count('年糕')}"
