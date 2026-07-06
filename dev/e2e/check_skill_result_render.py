"""構造化 load_skill 結果の UI 確認: markdown ハイライト + Tools loaded 一覧。"""

import json
import re
import time
import urllib.request

BASE = "http://localhost:8080"
FRONT = "http://localhost:5174"

req = urllib.request.Request(
    BASE + "/api/v1/auths/signin",
    data=json.dumps({"email": "ken@llens.local", "password": "llens-dev"}).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
token = json.loads(urllib.request.urlopen(req).read())["token"]


def get_chat(chat_id):
    r = urllib.request.Request(
        f"{BASE}/api/v1/chats/{chat_id}", headers={"Authorization": f"Bearer {token}"}
    )
    return json.loads(urllib.request.urlopen(r).read().decode(), strict=False)["chat"]


def wait_done(chat_id, timeout=240):
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
    page = browser.new_page(viewport={"width": 1400, "height": 1000})
    page.goto(FRONT)
    page.evaluate(f"localStorage.setItem('token', '{token}')")
    page.goto(FRONT)
    page.wait_for_selector("#chat-input", timeout=30000)
    time.sleep(2)

    page.click("#chat-input")
    page.keyboard.type("/vault パスワードを教えて")
    page.keyboard.press("Enter")
    page.wait_for_url(re.compile(r"/c/"), timeout=30000)
    chat_id = page.url.split("/c/")[1].split("?")[0]
    chat = wait_done(chat_id)

    asst = [
        m for m in chat["history"]["messages"].values() if m["role"] == "assistant"
    ][-1]
    for b in asst.get("content_blocks") or []:
        if b.get("type") == "tool_calls":
            for r_ in b.get("results", []):
                print("result raw:", r_["content"][:160])
            for c in b.get("calls", []):
                print("call:", c["id"], "render:", c.get("render"))
    print("reply:", (asst.get("content") or "")[:120])

    # load_skill の折りたたみを展開してスクリーンショット
    time.sleep(1)
    try:
        page.click("text=View Result from load_skill")
        time.sleep(0.8)
    except Exception as e:
        print("expand failed:", e)
    body = page.inner_text("body")
    print("UI has Tools loaded:", "Tools loaded" in body)
    print("UI has secret_password chip:", "secret_password" in body)
    page.screenshot(path="/tmp/skill_result.png", full_page=True)

    browser.close()
