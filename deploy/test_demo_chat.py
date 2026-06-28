"""Test demo chat stream endpoint."""
import json
import requests

url = "http://127.0.0.1:8000/api/demo/chat/stream"
payload = {"message": "你好", "session_id": "test-server-001"}
headers = {"Content-Type": "application/json"}

print(f"POST {url}")
print(f"Payload: {json.dumps(payload, ensure_ascii=False)}")
print("--- RESPONSE ---")

try:
    resp = requests.post(url, json=payload, headers=headers, stream=True, timeout=30)
    print(f"Status: {resp.status_code}")
    print(f"Content-Type: {resp.headers.get('content-type', 'unknown')}")
    chars = 0
    for line in resp.iter_lines(decode_unicode=True):
        if line:
            print(line)
            chars += len(line)
            if chars > 1500:
                print("... (truncated)")
                break
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {e}")
