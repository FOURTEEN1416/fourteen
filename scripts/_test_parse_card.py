"""Test if CharaCardV2 can parse the character files in data/characters/."""
import sys, json, os
sys.path.insert(0, ".")
from shisi.character.character_card_v2 import CharaCardV2Parser

dc = "data/characters"

# Test sys_001 (孙颖莎)
fpath = os.path.join(dc, "sys_001.json")
print(f"Testing: {fpath}")
with open(fpath, encoding="utf-8") as f:
    data = json.load(f)

print(f"  id: {data.get('id')}, name: {data.get('name')}")
print(f"  data fields: {list(data.keys())[:10]}")
print(f"  data type: {type(data)}")

try:
    card = CharaCardV2Parser.parse(data)
    print(f"  ✅ Parsed OK: {card.data.name}")
except Exception as e:
    print(f"  ❌ Parse FAILED: {e}")
    import traceback
    traceback.print_exc()

# Now test persona_林挽夏_1774701604527
fpath2 = os.path.join(dc, "persona_林挽夏_1774701604527.json")
print(f"\nTesting: {fpath2}")
with open(fpath2, encoding="utf-8") as f:
    data2 = json.load(f)

print(f"  id: {data2.get('id')}, name: {data2.get('name')}")

try:
    card2 = CharaCardV2Parser.parse(data2)
    print(f"  ✅ Parsed OK: {card2.data.name}")
except Exception as e:
    print(f"  ❌ Parse FAILED: {e}")
    import traceback
    traceback.print_exc()

print("\nDONE")
