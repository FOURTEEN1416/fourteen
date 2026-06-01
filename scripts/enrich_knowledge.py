"""Knowledge Enrichment & Audit — 统一知识审计 + 覆盖不足角色富文本注入。

运行方式：
  python scripts/enrich_knowledge.py              # 审计 + 自动富化低覆盖
  python scripts/enrich_knowledge.py --audit-only  # 仅审计，不注入

核心逻辑：
  - 从 config/characters/*.json 读取所有角色卡
  - 用 BM25 建索引，报告每个角色的知识块数
  - 对 < 3 chunks 的角色自动注入富文本知识（先检查是否有预设的 enrichment）
  - 兼容 JSON 直接富化（持久方案）和运行时注入（临时方案）
"""

import sys, json, os, argparse
from pathlib import Path

sys.path.insert(0, ".")

import logging
logging.basicConfig(level=logging.WARNING, force=True)
logger = logging.getLogger("enrich")

from shisi.character.character_card_v2 import CharaCardV2Parser
from shisi.knowledge.character_knowledge_service import get_knowledge_service

CHARS_DIR = "config/characters"
MIN_CHUNKS = 3

# ======== 运行时富文本库（对 JSON 本身缺乏内容的角色有用） ========
# 格式: {角色ID: [富文本块列表]}
# 注意：优先直接修改 JSON，此处仅作为后端无法修改 JSON 时的后备
RUNTIME_ENRICHMENTS: dict[str, list[str]] = {
}

# ======== 字符卡字段完整性检查 ========
REQUIRED_TEXT_FIELDS = ["description", "personality", "scenario", "creator_notes"]


def check_card_fields(data: dict) -> dict:
    """检查角色 JSON 各文本字段是否填充。返回 {字段: (有/无, 长度)}。"""
    result = {}
    # 标准 flat 格式
    for field in REQUIRED_TEXT_FIELDS:
        val = data.get(field, "")
        if isinstance(val, dict):
            # 处理孙颖莎式的 personality dict
            val = " ".join(f"{k}={v}" for k, v in val.items())
        result[field] = (bool(val), len(str(val)))
    # shisi 嵌套格式
    if "data" in data and "prompts" in data.get("data", {}):
        prompts = data["data"]["prompts"]
        for pid in prompts:
            pd = prompts[pid].get("data", {})
            for field in REQUIRED_TEXT_FIELDS:
                val = pd.get(field, "")
                result[f"prompts.{field}"] = (bool(val), len(str(val)))
    return result


def audit_character_json(path: Path) -> dict:
    """审计一个角色 JSON 的知识完整性。"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    cid = data.get("id") or path.stem
    name = data.get("name") or cid
    fields = check_card_fields(data)
    filled = sum(1 for v in fields.values() if v[0])
    total = len(fields)
    return {"id": cid, "name": name, "path": str(path), "fields": fields,
            "filled": filled, "total": total, "completeness": filled / max(total, 1)}


def inject_runtime_enrichment(service, char_id: str, enrichments: list[str]) -> int:
    """向 BM25 检索器注入运行时富文本块。"""
    if not enrichments:
        return 0
    from shisi.knowledge.retriever import KnowledgeChunk
    retriever = service._retrievers.get(char_id)
    if not retriever or not hasattr(retriever, "index"):
        return 0
    doc_id = f"enrich_{char_id}"
    chunks = [
        KnowledgeChunk(content=chunk, source=doc_id, source_id=f"{doc_id}_{i}")
        for i, chunk in enumerate(enrichments)
    ]
    retriever.index(chunks)
    if char_id in service._chunk_counts:
        service._chunk_counts[char_id] += len(chunks)
    else:
        service._chunk_counts[char_id] = len(chunks)
    return len(chunks)


def main():
    parser = argparse.ArgumentParser(description="知识库审计与富化工具")
    parser.add_argument("--audit-only", action="store_true", help="仅审计，不注入")
    args = parser.parse_args()

    chars_dir = Path(CHARS_DIR)
    json_files = sorted(chars_dir.glob("*.json"))
    print(f"\n[知识审计报告] {len(json_files)} 个角色\n")

    # ---- Step 1: 审计 JSON 字段完整性 ----
    audit_results = []
    for path in json_files:
        result = audit_character_json(path)
        audit_results.append(result)
        status = "[OK]" if result["completeness"] >= 0.5 else "[WARN]"
        field_summary = ", ".join(
            f"{k}={v[1]}字" for k, v in result["fields"].items()
        )
        print(f"  {status} {result['name']:<20} [{result['id']:<30}] {field_summary}")

    # ---- Step 2: 建 BM25 索引 ----
    print(f"\n[BM25] 正在建立索引...")

    service = get_knowledge_service()
    index_results = []

    for path in json_files:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        try:
            card = CharaCardV2Parser.parse(data)
            cid = data.get("id") or path.stem
            name = card.data.name or path.stem
            service.index_from_card(cid, card)
            stats = service.get_stats(cid)
            chunks = stats.get("total_chunks", 0)
            index_results.append((name, cid, chunks, "OK"))
        except Exception as e:
            cid = data.get("id") or path.stem
            index_results.append((path.stem, cid, 0, f"ERR: {e}"))

    # ---- Step 3: 报告覆盖 ----
    print(f"\n{'角色':<20} {'ID':<30} {'知识块':>6}  状态")
    print("-" * 80)
    total = 0
    low_coverage = []
    for name, cid, chunks, status in index_results:
        total += chunks
        line = f"{name:<20} {cid:<30} {chunks:>6}  {status}"
        if chunks < MIN_CHUNKS and status == "OK":
            low_coverage.append((name, cid, chunks))
            line += " [低覆盖]"
        print(line)

    print(f"\n[总计] {len(index_results)} 角色, {total} 知识块, "
          f"平均 {total/max(len(index_results),1):.1f}/角色")

    # ---- Step 4: 运行时注入 ----
    if not args.audit_only and RUNTIME_ENRICHMENTS:
        injected = 0
        print(f"\n[注入] 运行时富文本...")
        for char_id, chunks in RUNTIME_ENRICHMENTS.items():
            n = inject_runtime_enrichment(service, char_id, chunks)
            if n > 0:
                injected += n
                stats = service.get_stats(char_id)
                print(f"  [OK] {char_id}: +{n} 块，当前 {stats.get('total_chunks', 0)} 块")
        if injected > 0:
            print(f"  共注入 {injected} 个知识块")

    # ---- Step 5: 低覆盖角色提示 ----
    if low_coverage:
        print(f"\n[WARN] 以下 {len(low_coverage)} 个角色知识覆盖不足 (< {MIN_CHUNKS} 块)：")
        for name, cid, chunks in low_coverage:
            print(f"  {name:<20} ({cid}): 仅 {chunks} 块")
        print(f"\n[建议] 直接编辑对应 JSON 文件，补充 creator_notes/personality/scenario 等字段")
    else:
        print(f"\n[OK] 所有角色知识覆盖达标 (>= {MIN_CHUNKS} 块)")

    # ---- Step 6: JSON 字段完整性回顾 ----
    incomplete = [r for r in audit_results if r["completeness"] < 0.5]
    if incomplete:
        print(f"\n[WARN] 以下角色 JSON 字段完整性 < 50%：")
        for r in incomplete:
            missing = [k for k, v in r["fields"].items() if not v[0]]
            print(f"  {r['name']:<20} 缺失字段: {', '.join(missing)}")

    print("\n[DONE]")


if __name__ == "__main__":
    main()
