"""
Character migration script: convert all old-format (shisi_prompts) character files
in config/characters/ to the new unified format (UnifiedCharacter),
with user_id="default" so all users can use them.

Usage: python scripts/migrate_characters.py
"""

import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone


CHARACTERS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "characters")
BACKUP_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "characters_backup")

TRAIT_KEYWORDS = {
    "warmth": ["温暖", "温柔", "热心", "体贴", "亲和", "友善", "温和", "暖心", "暖", "温婉", "柔", "柔情"],
    "playfulness": ["调皮", "爱玩", "风趣", "活泼", "幽默", "顽皮", "俏皮", "皮的", "跳脱", "好动", "爱闹", "淘气"],
    "independence": ["独立", "自主", "自立", "坚强", "坚韧", "强硬", "倔强", "孤高", "冷漠"],
    "jealousy": ["嫉妒", "吃醋", "占有欲", "霸道", "独占", "爱吃醋", "醋坛子"],
    "stubbornness": ["固执", "倔", "执拗", "钻牛角尖", "不撞南墙", "顽固", "死脑筋", "执着", "死不认输", "犟"],
    "intelligence": ["聪明", "智慧", "机智", "睿智", "精明", "学识", "博学", "理智", "深谋远虑", "敏锐"],
    "sweetness": ["可爱", "甜美", "萌", "软萌", "甜", "撒娇", "黏人", "亲昵", "依赖", "软糯", "奶", "甜妹"],
    "elegance": ["优雅", "高贵", "端庄", "大气", "涵养", "淑女", "典雅", "知性", "从容", "雅致"],
    "mystery": ["神秘", "冷漠", "孤僻", "寡言", "沉默", "孤傲", "清冷", "冷淡", "冷淡疏离", "难以捉摸"],
    "loyalty": ["忠诚", "守护", "专一", "深情", "重情", "责任", "担当", "誓死", "守护"],
}

STYLE_KEYWORDS = {
    "directness": ["直接", "干脆", "爽快", "直率", "直来直去", "坦诚", "直白", "痛快", "凌厉"],
    "formality": ["礼貌", "尊敬", "敬语", "郑重", "正式", "得体", "客气", "谦逊", "谦和"],
    "expressiveness": ["多话", "健谈", "话痨", "滔滔不绝", "表达力", "生动", "话多", "能说会道", "爱说"],
    "poeticness": ["诗意", "文艺", "文采", "优美", "修辞", "比喻", "意象", "唯美", "含蓄", "典雅"],
    "casualness": ["随性", "随意", "轻松", "懒散", "不正经", "吊儿郎当", "吐槽", "自然", "日常"],
    "warmth_of_language": ["温暖", "关心", "贴心", "柔软", "和煦", "柔和", "软", "暖"],
}


def extract_old_data(filepath):
    """Extract character data from old format (shisi_prompts) files."""
    with open(filepath, "r", encoding="utf-8") as f:
        raw = json.load(f)

    # Format: data.prompts.{key}.data
    prompts = raw.get("data", {}).get("prompts", {})
    if not prompts:
        # Try direct chara_card_v2 format
        if raw.get("spec") == "chara_card_v2":
            d = raw.get("data", {})
            return {
                "name": d.get("name", ""),
                "description": d.get("description", ""),
                "personality": d.get("personality", ""),
                "scenario": d.get("scenario", ""),
                "creator_notes": d.get("creator_notes", ""),
                "tags": d.get("tags", []),
            }
        raise ValueError(f"Unknown format: {list(raw.keys())[:3]}")

    first_key = next(iter(prompts))
    prompt = prompts[first_key]

    if isinstance(prompt, dict) and "data" in prompt:
        d = prompt["data"]
        if isinstance(d, dict):
            if "name" not in d:
                for v in d.values():
                    if isinstance(v, dict) and "name" in v:
                        d = v
                        break
            return {
                "name": d.get("name", ""),
                "description": d.get("description", ""),
                "personality": d.get("personality", ""),
                "scenario": d.get("scenario", ""),
                "creator_notes": d.get("creator_notes", ""),
                "tags": d.get("tags", []),
            }

    return {
        "name": prompt.get("name", first_key),
        "description": prompt.get("description", ""),
        "personality": prompt.get("personality", ""),
        "scenario": prompt.get("scenario", ""),
        "creator_notes": prompt.get("creator_notes", ""),
        "tags": [],
    }


def analyze_text_to_scores(text: str, keywords_map: dict) -> dict:
    """Analyze text to produce trait/personality scores (0.0 - 1.0)."""
    if not text:
        return {k: 0.5 for k in keywords_map}

    text_lower = text.lower()
    scores = {}

    for key, keywords in keywords_map.items():
        score = 0.5
        for kw in keywords:
            if kw.lower() in text_lower:
                score += 0.08
        scores[key] = round(min(max(score, 0.1), 1.0), 2)

    return scores


def extract_core_anchors(d: dict) -> list:
    """Extract core anchors/keywords from character data."""
    anchors = []

    tags = d.get("tags", [])
    if isinstance(tags, list):
        for tag in tags:
            if isinstance(tag, str) and len(tag) <= 20:
                anchors.append(tag.strip())

    notes = d.get("creator_notes", "")
    brackets = re.findall(r'\[([^\]]+)\]', d.get("description", "") + notes)
    for b in brackets:
        parts = b.split(":")
        if len(parts) >= 1:
            key = parts[0].strip()
            if key not in anchors and len(key) <= 20:
                anchors.append(key)

    return anchors[:8]


def convert_file(filepath: str):
    """Convert a single character file to unified format."""
    try:
        data = extract_old_data(filepath)
    except Exception as e:
        print(f"  SKIP {os.path.basename(filepath)}: {e}")
        return None

    name = data.get("name", "").strip()
    if not name:
        print(f"  SKIP {os.path.basename(filepath)}: no name")
        return None

    description_text = data.get("description", "")
    personality_text = data.get("personality", "")
    scenario_text = data.get("scenario", "")
    creator_notes = data.get("creator_notes", "")

    all_text = f"{description_text} {personality_text} {scenario_text} {creator_notes}"

    personality_scores = analyze_text_to_scores(all_text, TRAIT_KEYWORDS)
    speaking_scores = analyze_text_to_scores(all_text, STYLE_KEYWORDS)
    core_anchors = extract_core_anchors(data)

    if name not in core_anchors:
        core_anchors.insert(0, name)

    char_id = str(uuid.uuid4())[:8]
    now = datetime.now(timezone.utc).isoformat()

    desc = description_text[:500] + ("..." if len(description_text) > 500 else "")

    unified = {
        "id": char_id,
        "name": name,
        "description": desc,
        "personality": personality_scores,
        "speaking_style": speaking_scores,
        "core_anchors": core_anchors,
        "user_id": "default",
        "is_active": False,
        "created_at": now,
        "updated_at": now,
        "version": 1,
        "voice_config": None,
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(unified, f, ensure_ascii=False, indent=2)

    return name


def main():
    os.makedirs(BACKUP_DIR, exist_ok=True)

    files = sorted([
        f for f in os.listdir(CHARACTERS_DIR)
        if f.endswith(".json")
    ])

    print(f"[+] Found {len(files)} character files")
    print()

    converted = 0
    skipped = 0

    for fname in files:
        fpath = os.path.join(CHARACTERS_DIR, fname)

        # Skip if already in unified format
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                existing = json.load(f)
            if "id" in existing and "personality" in existing and isinstance(existing.get("personality"), dict):
                print(f"  [SKIP] {fname} already in unified format")
                skipped += 1
                continue
        except Exception:
            pass

        print(f"  [CONV] {fname} ... ", end="", flush=True)

        backup_path = os.path.join(BACKUP_DIR, fname)
        shutil.copy2(fpath, backup_path)

        result = convert_file(fpath)
        if result:
            print(f"done -> {result}")
            converted += 1
        else:
            skipped += 1

    print(f"\n[+] Done! Converted: {converted}, Skipped: {skipped}")
    print(f"[+] Backup at: {BACKUP_DIR}")


if __name__ == "__main__":
    main()
