from __future__ import annotations

import json
import threading
import time
import urllib.request

BASE = "http://127.0.0.1:8765"
result: dict = {}


def client() -> None:
    req = urllib.request.Request(
        f"{BASE}/v1/chat/completions",
        data=json.dumps(
            {
                "model": "human",
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "用一句话打招呼"},
                ],
            }
        ).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        result["status"] = resp.status
        result["body"] = json.load(resp)


t = threading.Thread(target=client, daemon=True)
t.start()

job = None
for _ in range(50):
    jobs = json.load(urllib.request.urlopen(f"{BASE}/operator/jobs"))["jobs"]
    pending = [j for j in jobs if j["status"] == "pending"]
    if pending:
        job = pending[0]
        break
    time.sleep(0.1)

if job is None:
    raise SystemExit("no pending job appeared")

reply_req = urllib.request.Request(
    f"{BASE}/operator/jobs/{job['id']}/reply",
    data=json.dumps({"content": "你好，这里是人工模型。"}).encode(),
    headers={"content-type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(reply_req) as resp:
    replied = json.load(resp)

t.join(timeout=10)
if result.get("status") != 200:
    raise SystemExit(result)

body = result["body"]
assert body["choices"][0]["message"]["content"] == "你好，这里是人工模型。"
assert body["object"] == "chat.completion"
assert replied["status"] == "completed"
print(json.dumps({
    "job_id": job["id"],
    "model": body["model"],
    "content": body["choices"][0]["message"]["content"],
    "usage": body["usage"],
}, ensure_ascii=False, indent=2))
