"""#35 step2b(tool 遅延取り込み)の答え合わせ。

A(入口 1): /vault 手動発火 → secret_password が tools に載る → モデルが呼ぶ → パスワード
B(入口 2): 自然文 → モデルが load_skill(vault) → tools 注入 → secret_password → パスワード
"""

import json
import re
import time
import urllib.request

BASE = "http://localhost:8080"
FRONT = "http://localhost:5174"
PASSWORD = "kotori-sazanka-9412"

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


def describe(asst):
    calls = []
    for b in asst.get("content_blocks") or []:
        if b.get("type") == "tool_calls":
            for c in b.get("calls", []):
                calls.append((c["id"], c["function"]["name"]))
    return calls, (asst.get("content") or "")


from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.goto(FRONT)
    page.evaluate(f"localStorage.setItem('token', '{token}')")
    page.goto(FRONT)
    page.wait_for_selector("#chat-input", timeout=30000)
    time.sleep(2)

    # ---- A: 手動 /vault(入口 1)----
    page.click("#chat-input")
    page.keyboard.type("/vault パスワードを教えて")
    page.keyboard.press("Enter")
    page.wait_for_url(re.compile(r"/c/"), timeout=30000)
    chat_id = page.url.split("/c/")[1].split("?")[0]
    chat = wait_done(chat_id)
    asst = [
        m for m in chat["history"]["messages"].values() if m["role"] == "assistant"
    ][-1]
    calls, reply = describe(asst)
    print("A calls:", calls)
    print("A reply:", reply[:150])
    print("A PASS:", PASSWORD in reply)
    page.screenshot(path="/tmp/vault_a.png")

    # ---- B: 自然文(入口 2)----
    page.goto(FRONT)
    page.wait_for_selector("#chat-input", timeout=30000)
    time.sleep(1)
    page.click("#chat-input")
    page.keyboard.type("金庫の秘密パスワードが必要になった。取得して教えて")
    page.keyboard.press("Enter")
    page.wait_for_url(re.compile(r"/c/"), timeout=30000)
    chat_id_b = page.url.split("/c/")[1].split("?")[0]
    chat_b = wait_done(chat_id_b)
    asst_b = [
        m for m in chat_b["history"]["messages"].values() if m["role"] == "assistant"
    ][-1]
    calls_b, reply_b = describe(asst_b)
    print("B calls:", calls_b)
    print("B reply:", reply_b[:150])
    print("B PASS:", PASSWORD in reply_b)
    page.screenshot(path="/tmp/vault_b.png")

    browser.close()
