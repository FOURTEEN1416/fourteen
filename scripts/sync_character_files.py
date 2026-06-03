"""Sync character files from config/characters to data/characters, keyed by ID."""
import sys, json, os, shutil
sys.path.insert(0, ".")

src_dir = "config/characters"
dst_dir = "data/characters"
os.makedirs(dst_dir, exist_ok=True)

ok = 0
errors: list[str] = []

for fname in sorted(os.listdir(src_dir)):
    if not fname.endswith(".json"):
        continue
    src_path = os.path.join(src_dir, fname)
    with open(src_path, encoding="utf-8") as f:
        data = json.load(f)
    
    cid = data.get("id")
    if not cid:
        # Generate an ID from filename if missing
        cid = fname.replace(".json", "").replace(" ", "_").replace("(", "").replace(")", "")
        data["id"] = cid
        print(f"  {fname}: no id field, generated '{cid}'")
    
    # Also ensure name is set
    if not data.get("name"):
        data["name"] = fname.replace(".json", "")
    
    dst_path = os.path.join(dst_dir, f"{cid}.json")
    
    # Write to destination (also save updated file back to source if we added id)
    with open(dst_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    with open(src_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    ok += 1
    print(f"  OK: {cid} -> {dst_path}")

print(f"\nTotal: {ok} files synced")

# Now verify with the API
print("\n=== Verify API knowledge stats ===")
import urllib.request
API_BASE = "http://localhost:8000"
API_KEY = "CHANGE_ME_TO_STRONG_RANDOM_KEY_32_CHARS_MIN"
HEADERS = {"X-API-Key": API_KEY}

test_ids = ["sys_001", "阿哈(1)", "孙颖莎"]  # try different patterns
for cid in test_ids:
    try:
        url = f"{API_BASE}/api/characters/{urllib.parse.quote(cid, safe='')}/knowledge/stats"
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
            print(f"  {cid}: {result}")
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"  {cid}: HTTP {e.code} - {body[:100]}")
    except Exception as e:
        print(f"  {cid}: ERROR {e}")

print("\nDONE")
