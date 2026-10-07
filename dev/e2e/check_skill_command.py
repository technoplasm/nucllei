"""`/command` 発火 = 合成 load_skill exchange の実機確認。

- 応答先頭に tool_calls ブロック(load_skill、id が skill: 始まり)
- UI に「View Result from load_skill」が出る(モデル自律起動と同一の見た目)
- ターン 2(command なし): 合成ブロックなし・モデルの再ロードなし・スタイル持続
"""

import json
import re
import time
import urllib.request

BASE = "http://localhost:3000"
FRONT = "http://localhost:5174"

req = urllib.request.Request(
    BASE + "/api/v1/auths/signin",
    data=json.dumps({"email": "dev@nucllei.local", "password": "dev"}).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
token = json.loads(urllib.request.urlopen(req).read())["token"]


def get_chat(chat_id):
    r = urllib.request.Request(
        f"{BASE}/api/v1/chats/{chat_id}", headers={"Authorization": f"Bearer {token}"}
    )
    return json.loads(urllib.request.urlopen(r).read().decode(), strict=False)["chat"]


def wait_done(chat_id, timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        chat = get_chat(chat_id)
        msgs = list(chat["history"]["messages"].values())
        assistant = [m for m in msgs if m.get("role") == "assistant"]
        if assistant and all(m.get("done") for m in assistant):
            return chat
        time.sleep(1.5)
    raise TimeoutError


from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.goto(FRONT)
    page.evaluate(f"localStorage.setItem('token', '{token}')")
    page.goto(FRONT)
    page.wait_for_selector("#chat-input", timeout=30000)
    time.sleep(2)

    page.click("#chat-input")
    page.keyboard.type("/kansai 一言だけ自己紹介して")
    page.keyboard.press("Enter")
    page.wait_for_url(re.compile(r"/c/"), timeout=30000)
    chat_id = page.url.split("/c/")[1].split("?")[0]
    chat = wait_done(chat_id)

    asst = [
        m for m in chat["history"]["messages"].values() if m["role"] == "assistant"
    ][-1]
    blocks = asst.get("content_blocks") or []
    print("block types:", [b.get("type") for b in blocks])
    tc = [b for b in blocks if b.get("type") == "tool_calls"]
    if tc:
        call = tc[0]["calls"][0]
        print("first call id:", call["id"], "| name:", call["function"]["name"])
        print("result:", tc[0]["results"][0]["content"][:80])
    print("reply:", (asst.get("content") or "")[:100])

    time.sleep(1)
    body = page.inner_text("body")
    print("UI shows View Result from load_skill:", "load_skill" in body)
    page.screenshot(path="/tmp/synth_1.png")

    # ターン 2: command なし
    page.click("#chat-input")
    page.keyboard.type("ほな一言で続けて")
    page.keyboard.press("Enter")
    time.sleep(2)
    chat = wait_done(chat_id)
    asst2 = [
        m for m in chat["history"]["messages"].values() if m["role"] == "assistant"
    ][-1]
    blocks2 = asst2.get("content_blocks") or []
    print("turn2 block types:", [b.get("type") for b in blocks2])
    print("turn2 reply:", (asst2.get("content") or "")[:100])
    page.screenshot(path="/tmp/synth_2.png")

    browser.close()
