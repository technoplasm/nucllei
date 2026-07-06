"""#18-3(sandbox 自前ツール搭載)の答え合わせ。実 Kimi × 実 sandbox(:3000)。

前提: dev/tools/sandbox.py を tool id `sandbox` で登録し、valves の sandbox_url を
http://127.0.0.1:3000 に設定、llens-assistant の toolIds に追加済み
(_memos/builtin-tools.md §0 と #35 コメント参照)。

E1 計算 / E2 stateful(x=42 → 84)/ E3 outputs 自動添付 / E4 timeout(変数保持)/
E5 shell / E6 添付取り込み(import_chat_file)
"""

import io
import json
import re
import time
import urllib.request

import requests

BASE = "http://localhost:8080"
FRONT = "http://localhost:5174"
SECRET = "himitsu-ingredient-7742"

req = urllib.request.Request(
    BASE + "/api/v1/auths/signin",
    data=json.dumps({"email": "ken@llens.local", "password": "llens-dev"}).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
token = json.loads(urllib.request.urlopen(req).read())["token"]
auth = {"Authorization": f"Bearer {token}"}


def get_chat(chat_id):
    r = urllib.request.Request(f"{BASE}/api/v1/chats/{chat_id}", headers=auth)
    return json.loads(urllib.request.urlopen(r).read().decode(), strict=False)["chat"]


def wait_done(chat_id, n_assistant, timeout=300):
    """assistant メッセージが n 件そろい全部 done になるまで待つ(マルチターン対応)。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        chat = get_chat(chat_id)
        assistant = [
            m
            for m in chat["history"]["messages"].values()
            if m.get("role") == "assistant"
        ]
        if len(assistant) >= n_assistant and all(m.get("done") for m in assistant):
            return chat
        time.sleep(1.5)
    raise TimeoutError(f"assistant {n_assistant} 件の完了を待てなかった")


def last_assistant(chat):
    msgs = [m for m in chat["history"]["messages"].values() if m["role"] == "assistant"]
    return sorted(msgs, key=lambda m: m.get("timestamp", 0))[-1]


def describe(asst):
    calls = []
    for b in asst.get("content_blocks") or []:
        if b.get("type") == "tool_calls":
            for c in b.get("calls", []):
                calls.append(c["function"]["name"])
    return calls, (asst.get("content") or "")


results = []


def check(label, cond, detail=""):
    results.append((label, bool(cond)))
    print(
        f"{label}: {'PASS' if cond else 'FAIL'}" + (f"  ({detail})" if detail else "")
    )


from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.goto(FRONT)
    page.evaluate(f"localStorage.setItem('token', '{token}')")

    def new_chat():
        page.goto(FRONT)
        page.wait_for_selector("#chat-input", timeout=30000)
        time.sleep(1.5)

    def send(text):
        page.click("#chat-input")
        page.keyboard.type(text)
        page.keyboard.press("Enter")

    def send_and_wait(text, n_assistant, chat_id=None):
        send(text)
        if chat_id is None:
            page.wait_for_url(re.compile(r"/c/"), timeout=30000)
            chat_id = page.url.split("/c/")[1].split("?")[0]
        chat = wait_done(chat_id, n_assistant)
        return chat_id, chat

    # ---- chat 1: E1 / E2 / E4 --------------------------------------------
    new_chat()
    chat_id, chat = send_and_wait("run_python で 2**10 を計算して結果を教えて", 1)
    calls, reply = describe(last_assistant(chat))
    check("E1 計算(2**10)", "run_python" in calls and "1024" in reply, reply[:80])

    _, chat = send_and_wait("run_python で x = 42 を定義しておいて", 2, chat_id)
    calls, reply = describe(last_assistant(chat))
    check("E2a 変数定義(x=42)", "run_python" in calls, reply[:80])

    _, chat = send_and_wait("run_python で x * 2 の値を確かめて教えて", 3, chat_id)
    calls, reply = describe(last_assistant(chat))
    check("E2b stateful(x*2=84)", "run_python" in calls and "84" in reply, reply[:80])

    _, chat = send_and_wait(
        "run_python で import time; time.sleep(10) を timeout_s=2 で実行してみて",
        4,
        chat_id,
    )
    calls, reply = describe(last_assistant(chat))
    check(
        "E4a timeout 中断",
        "run_python" in calls
        and ("タイムアウト" in reply or "中断" in reply or "超え" in reply),
        reply[:80],
    )

    _, chat = send_and_wait("x はまだ定義されてる?run_python で確かめて", 5, chat_id)
    calls, reply = describe(last_assistant(chat))
    check(
        "E4b timeout 後も変数保持(x=42)",
        "run_python" in calls and "42" in reply,
        reply[:80],
    )
    page.screenshot(path="/tmp/sandbox_e2e_chat1.png", full_page=True)

    # ---- chat 2: E3 / E5 --------------------------------------------------
    new_chat()
    chat_id, chat = send_and_wait(
        "run_python で、ヘッダ a,b と行 1,2 だけの CSV を outputs/result.csv に保存して",
        1,
    )
    asst = last_assistant(chat)
    calls, reply = describe(asst)
    files = asst.get("files") or []
    check(
        "E3 outputs 自動添付",
        "run_python" in calls
        and any(
            (f.get("name") or f.get("file", {}).get("filename", "")).endswith(
                "result.csv"
            )
            for f in files
        ),
        f"files={[f.get('name') for f in files]}",
    )

    _, chat = send_and_wait(
        "run_shell で echo sandbox-shell-18-3 を実行して出力をそのまま教えて",
        2,
        chat_id,
    )
    calls, reply = describe(last_assistant(chat))
    check(
        "E5 shell", "run_shell" in calls and "sandbox-shell-18-3" in reply, reply[:80]
    )
    page.screenshot(path="/tmp/sandbox_e2e_chat2.png", full_page=True)

    # ---- chat 3: E6 添付取り込み ------------------------------------------
    up = requests.post(
        f"{BASE}/api/v1/files/",
        headers=auth,
        files={
            "file": (
                "secret_note.txt",
                io.BytesIO(f"合言葉は {SECRET} です。\n".encode()),
                "text/plain",
            )
        },
    )
    file_id = up.json()["id"]
    print(f"uploaded file_id={file_id}")

    new_chat()
    chat_id, chat = send_and_wait(
        f"file_id={file_id} のファイルを import_chat_file で sandbox に取り込み、"
        "run_python で /workspace/inputs/ のそのファイルを読んで中身をそのまま教えて",
        1,
    )
    calls, reply = describe(last_assistant(chat))
    check(
        "E6 添付取り込み", "import_chat_file" in calls and SECRET in reply, reply[:120]
    )
    page.screenshot(path="/tmp/sandbox_e2e_chat3.png", full_page=True)

    browser.close()

print()
failed = [l for l, ok in results if not ok]
print(f"==== {len(results) - len(failed)}/{len(results)} PASS ====")
if failed:
    print("FAILED:", ", ".join(failed))
    raise SystemExit(1)
