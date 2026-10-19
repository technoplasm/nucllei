"""組み込み sandbox tool(`builtin_sandbox`)の答え合わせ。実 Kimi × 実 sandbox コンテナ。

起源は #18-3(sandbox 自前ツール搭載)。当時は `dev/tools/sandbox.py` を tool id `sandbox`
で dev DB へ手動登録していたが、**#114(2026-09-03)で tool は nucllei 本体の組み込み
`builtin_sandbox` になった**ので、手動登録の手順は消えている。

前提:
1. sandbox コンテナが `127.0.0.1:13302`(133xx 帯)で稼働している
   (`docker build -t nucllei-sandbox:dev ./sandbox` → 配備層 llens の compose で up。
   詳細は `_memos/dev-environment.md`)
2. backend を **`SANDBOX_URL=http://127.0.0.1:13302` 付きで起動**している。
   env が空だと `builtin_sandbox` はそもそも Tools テーブルに生えない(env ゲート)。
   #132 の `SANDBOX_TOKEN` は opt-in なので省略可 — 設定するならコンテナと同じ値を
3. preset(`MODEL_ID`)に `builtin_sandbox` が接続されている
   — 未接続ならこのスクリプトが接続して MODELS キャッシュを張り直す(冪等)

E1 計算 / E2 stateful(x=42 → 84)/ E3 fetch_from_workspace で渡す(添付 + meta.source)/
E4 timeout(変数保持)/ E5 shell / E6 添付取り込み(send_to_workspace)

E3 は 2026-10-19 まで「exports/ に書くと自動添付」を見ていた。自動添付は廃止され、利用者へ渡すのは
`fetch_from_workspace` 一本になった(`_memos/builtin-tools.md`「exports/ の自動添付の廃止」)。
"""

import io
import json
import re
import time
import urllib.request

import requests

BASE = "http://localhost:3000"
FRONT = "http://localhost:5174"
SECRET = "himitsu-ingredient-7742"
# 組み込み tool の id(`nucllei/builtin_tools/__init__.py` の BUILTIN_TOOLS)。
TOOL_ID = "builtin_sandbox"
# 検証に使う preset。dev の主 preset は 2026-07-22 に llens-assistant → llens-thinking へ改名。
MODEL_ID = "llens-thinking"

req = urllib.request.Request(
    BASE + "/api/v1/auths/signin",
    data=json.dumps({"email": "dev@nucllei.local", "password": "dev"}).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
token = json.loads(urllib.request.urlopen(req).read())["token"]
auth = {"Authorization": f"Bearer {token}"}


def _req(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(
        BASE + url,
        data=data,
        method=method,
        headers={**auth, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(r, timeout=30) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw else None


def preflight():
    """組み込み tool が生えていて preset に接続されていることを確かめる。

    ここで落ちないと、症状が「モデルが run_python を呼ばずに雑談で返す」という
    読み取りにくい FAIL に化ける(#18-3 の偽 PASS と同じ罠)。
    """
    tools = _req("GET", "/api/v1/tools/") or []
    if not any(t["id"] == TOOL_ID for t in tools):
        raise SystemExit(
            f"{TOOL_ID} が Tools に無い。backend を SANDBOX_URL 付きで起動していない"
            "可能性が高い:\n"
            "  tmux send-keys -t nuc:BE 'SANDBOX_URL=http://127.0.0.1:13302 "
            "uv run task dev' Enter\n"
            "(未設定だと __init__.py の env ゲートで登録されず、prune で DB からも消える。"
            "#132 の SANDBOX_TOKEN は opt-in なのでゲートには関係しない)"
        )

    # 向き先は env(SANDBOX_URL)のみ — valve には無い(2026-09-05)。valves は
    # timeout / 出力上限だけなので、動作の解釈に効く範囲で毎回出す。
    valves = _req("GET", f"/api/v1/tools/id/{TOOL_ID}/valves") or {}
    print(
        f"valves: {valves or '(未設定 — コード既定を使用)'} / 向き先は env SANDBOX_URL"
    )

    model = _req("GET", f"/api/v1/models/model?id={MODEL_ID}")
    if model is None:
        raise SystemExit(f"preset {MODEL_ID} が無い。dev fixture を確認すること")
    meta = model.get("meta") or {}
    tool_ids = list(meta.get("toolIds") or [])
    if TOOL_ID not in tool_ids:
        # #54 以降 toolIds はサーバー側解決。接続が無いとモデルに spec が渡らない。
        meta["toolIds"] = tool_ids + [TOOL_ID]
        _req(
            "POST",
            f"/api/v1/models/model/update?id={MODEL_ID}",
            {
                "id": model["id"],
                "base_model_id": model.get("base_model_id"),
                "name": model["name"],
                "meta": meta,
                "params": model.get("params") or {},
                "access_control": model.get("access_control"),
                "is_active": model.get("is_active", True),
            },
        )
        _req("GET", "/api/models")  # app.state.MODELS 再構築(API 更新時の必須手順)
        print(f"{MODEL_ID} の toolIds に {TOOL_ID} を接続した")


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


preflight()

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
    # 利用者へ渡すのは fetch_from_workspace だけ(暗黙の添付は無い)。添付はメッセージの files に入り、
    # Files の meta.source に由来の sandbox パスが残る。
    new_chat()
    chat_id, chat = send_and_wait(
        "run_python で、ヘッダ a,b と行 1,2 だけの CSV を /workspace/result.csv に保存して、"
        "fetch_from_workspace でダウンロードできるようにして",
        1,
    )
    asst = last_assistant(chat)
    calls, reply = describe(asst)
    files = asst.get("files") or []
    csv_files = [
        f
        for f in files
        if (f.get("name") or f.get("file", {}).get("filename", "")).endswith(
            "result.csv"
        )
    ]
    check(
        "E3a fetch_from_workspace で添付",
        "run_python" in calls and "fetch_from_workspace" in calls and bool(csv_files),
        f"calls={calls} files={[f.get('name') for f in files]}",
    )
    source = None
    if csv_files:
        source = (
            (_req("GET", f"/api/v1/files/{csv_files[0]['id']}") or {}).get("meta") or {}
        ).get("source")
    check(
        "E3b meta.source に由来のパス",
        source == "/workspace/result.csv",
        f"source={source!r}",
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
    # 取り込みは send_to_workspace(旧 import_chat_file)。置き場は /workspace/imports/。
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
        f"file_id={file_id} のファイルを send_to_workspace で sandbox に取り込み、"
        "run_python で /workspace/imports/ のそのファイルを読んで中身をそのまま教えて",
        1,
    )
    calls, reply = describe(last_assistant(chat))
    check(
        "E6 添付取り込み", "send_to_workspace" in calls and SECRET in reply, reply[:120]
    )
    page.screenshot(path="/tmp/sandbox_e2e_chat3.png", full_page=True)

    browser.close()

print()
failed = [l for l, ok in results if not ok]
print(f"==== {len(results) - len(failed)}/{len(results)} PASS ====")
if failed:
    print("FAILED:", ", ".join(failed))
    raise SystemExit(1)
