"""描述不足角色的描述扩写 —— 严格基于已有设定素材，不编造新事实。

背景：实测 25 张卡中有 12 张 description < 300 字，但其中 11 张的
personality_text / scenario 素材充足（如「镜心」素材合计 3658 字，
description 只用了 152 字）—— 素材没被用上是**整理问题**，不是内容缺失。

做法：把 description + personality_text + scenario 交给 LLM 融合扩写。
⚠️ 严格约束：
  - 只用已提供的信息，**严禁编造**新设定/经历/关系
  - **不使用 creator_notes**（那是扮演规则，已单独注入 prompt，混入描述会污染）
  - 备份原卡，可回滚

用法：
  python scripts/expand_short_descriptions.py --dry-run      # 只列出待处理项
  python scripts/expand_short_descriptions.py --limit 2      # 先试 2 个
  python scripts/expand_short_descriptions.py                # 全部处理
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, ".")

CHARS = pathlib.Path("config/characters")
BACKUP = pathlib.Path("data/archive/desc-expand-backup")
MIN_DESC = 300          # 低于此长度视为"描述不足"
MIN_MATERIAL = 250      # 素材总和低于此值则跳过（无料可扩）
# 注：阈值 250 是为纳入「林挽夏」（活跃角色，素材 275 字）而设；
# 素材确实极少的角色（如伊蕾娜·艾斯特莱雅 245 字）仍会被跳过。

API_BASE = os.environ.get("AGNES_API_BASE", "https://apihub.agnes-ai.com/v1")
API_KEY = os.environ.get("AGNES_API_KEY", "")
MODEL = os.environ.get("AGNES_MODEL", "agnes-3.0-flash")

PROMPT = """你是一个角色设定整理助手。下面给出某个角色的**现有设定素材**。

【任务】把这些素材整合、扩写为一段更完整的角色描述（description）。

【严格约束 —— 必须遵守】
1. **只能使用已提供的信息**。严禁编造原文没有的设定、经历、人际关系、能力或背景；
2. 允许：合并同类信息、补充表达的因果与细节、调整叙述顺序、润色语言；
3. 不允许：新增任何原文未提及的事实；
4. 若某项信息原文没有，就**不要提**，宁可写短也不要编；
5. 输出第二人称、连贯中文段落，与原文语气风格一致；
6. 目标长度 300~600 字（若素材本身不足则按实际能写出的长度输出）。

【现有描述】
{description}

【性格素材】
{personality}

【场景素材】
{scenario}

请直接输出整合后的角色描述正文，不要任何标题、解释或前后缀。"""


def call_llm(prompt: str, timeout: int = 120) -> str:
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 1600,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{API_BASE.rstrip('/')}/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode("utf-8"))
    return (d.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    if not API_KEY and not args.dry_run:
        print("❌ 未设置 AGNES_API_KEY")
        return

    targets = []
    for f in sorted(CHARS.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        dl = len(str(d.get("description") or ""))
        mat = sum(len(str(d.get(k) or "")) for k in ("personality_text", "scenario"))
        if dl < MIN_DESC and mat >= MIN_MATERIAL:
            targets.append((f, d, dl, mat))

    print(f"=== 待扩写 {len(targets)} 个角色 ===")
    for f, d, dl, mat in targets:
        print(f"  {str(d.get('name'))[:10]:<12} desc={dl:<5} 素材={mat:<5} ({f.stem})")
    if args.dry_run:
        return

    BACKUP.mkdir(parents=True, exist_ok=True)
    if args.limit:
        targets = targets[: args.limit]

    ok = 0
    for f, d, dl, _mat in targets:
        name = str(d.get("name"))
        print(f"\n── {name} (原 desc {dl} 字) ──")
        try:
            new = call_llm(PROMPT.format(
                description=d.get("description") or "（无）",
                personality=d.get("personality_text") or "（无）",
                scenario=d.get("scenario") or "（无）",
            ))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            print(f"   ❌ 调用失败: {e}")
            continue
        if not new or len(new) < dl:
            print(f"   ⚠️ 结果过短（{len(new)} 字），跳过")
            continue
        shutil.copy(f, BACKUP / f.name)
        d["description"] = new
        f.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"   ✅ {dl} → {len(new)} 字")
        print(f"      预览: {new[:110]}...")
        ok += 1
        time.sleep(1)

    print(f"\n共扩写 {ok} 个（备份: {BACKUP}）")


if __name__ == "__main__":
    main()
