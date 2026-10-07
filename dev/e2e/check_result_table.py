"""tool 結果の表(kind="table" — Rendered(csv, "table", meta={...}))の UI 確認。

モデルを介さず、表の結果を持つ tool_calls ブロックを載せたチャットを API で作って開く
(results[].render は backend が確定した形そのもの)。1 本目は列の多い 1200 行(行上限の
注記・meta のラベル・横 / 縦スクロール・sticky 見出し・長いセルの省略)、2 本目は meta なしの小さな表と
引用符(カンマ・改行・"")入りのセル。1280px / 390px × ライト / ダークでスクショを撮り、
見出しの sticky と省略の title を live DOM から読む。作ったチャットは最後に消す。

    .venv/bin/python dev/e2e/check_result_table.py [出力ディレクトリ]
"""

import csv
import io
import json
import os
import sys
import time
import urllib.request
import uuid

BASE = "http://localhost:3000"
FRONT = os.environ.get("FRONT", "http://localhost:5173")
EMAIL, PASSWORD = "dev@nucllei.local", "dev"
OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/check_result_table"
os.makedirs(OUT, exist_ok=True)


def api(method, path, token, body=None):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method=method,
    )
    return json.loads(urllib.request.urlopen(req).read().decode() or "null")


def to_csv(rows):
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    return buf.getvalue()


token = api("POST", "/api/v1/auths/signin", "", {"email": EMAIL, "password": PASSWORD})[
    "token"
]

columns = ["patient_id", "visit_date", "department", "diagnosis", "note"] + [
    f"lab_{k:02d}" for k in range(1, 11)
]
big_rows = [columns] + [
    [
        f"P{n:06d}",
        f"2026-09-{n % 28 + 1:02d}",
        ["内科", "外科", "小児科", "病理"][n % 4],
        "慢性腎臓病 ステージ 3a(eGFR 低下、経過観察中)" if n % 7 == 0 else "高血圧症",
        "長いメモ: " + "検査値の推移を確認。" * 6 if n == 0 else "",
    ]
    + [f"{(n * 13 + k * 7) % 200 / 10:.1f}" for k in range(1, 11)]
    for n in range(1200)
]
# 本文は BE の Rendered.content() と同じ形(meta 行 + CSV)
big_text = "# rows=1200 truncated=false duration=0.42s\n" + to_csv(big_rows)
small_text = to_csv(
    [
        ["name", "comment", "count"],
        ["Doe, John", 'said "hello"', "3"],
        ["山田", "1 行目\n2 行目", ""],
        ["", "empty name", "0"],
    ]
)

calls = [
    {
        "id": "call_table_big",
        "type": "function",
        "function": {
            "name": "query_emr",
            "arguments": json.dumps({"sql": "SELECT * FROM visits"}),
        },
        "render": {"args": {"sql": {"kind": "code", "language": "sql"}}},
    },
    {
        "id": "call_table_small",
        "type": "function",
        "function": {"name": "query_emr", "arguments": json.dumps({"sql": "SELECT 1"})},
        "render": {"args": {"sql": {"kind": "code", "language": "sql"}}},
    },
]
results = [
    {
        "tool_call_id": "call_table_big",
        "content": big_text,
        "render": {
            "kind": "table",
            "meta": {"rows": 1200, "truncated": False, "duration": "0.42s"},
        },
        "duration": 0.42,
    },
    {
        "tool_call_id": "call_table_small",
        "content": small_text,
        "render": {"kind": "table"},
        "duration": 0.05,
    },
]

user_id, asst_id = str(uuid.uuid4()), str(uuid.uuid4())
now = int(time.time())
chat = {
    "title": "check_result_table",
    "models": ["llens"],
    "params": {},
    "messages": [],
    "files": [],
    "history": {
        "currentId": asst_id,
        "messages": {
            user_id: {
                "id": user_id,
                "parentId": None,
                "childrenIds": [asst_id],
                "role": "user",
                "content": "表を出して",
                "timestamp": now,
            },
            asst_id: {
                "id": asst_id,
                "parentId": user_id,
                "childrenIds": [],
                "role": "assistant",
                "model": "llens",
                "modelName": "LLENS",
                "content": "結果です。",
                "content_blocks": [
                    {"type": "tool_calls", "calls": calls, "results": results},
                    {"type": "text", "content": "結果です。"},
                ],
                "done": True,
                "timestamp": now,
            },
        },
    },
}
chat_id = api("POST", "/api/v1/chats/new", token, {"chat": chat})["id"]

from playwright.sync_api import sync_playwright  # noqa: E402

os.environ.pop("DISPLAY", None)
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--disable-gpu"])
        for theme in ("light", "dark"):
            for width in (1280, 390):
                ctx = browser.new_context(viewport={"width": width, "height": 900})
                page = ctx.new_page()
                page.on("pageerror", lambda e: print("pageerror:", str(e)[:400]))
                page.on(
                    "response",
                    lambda r: (
                        print("http", r.status, r.url) if r.status >= 400 else None
                    ),
                )
                page.goto(f"{FRONT}/auth")
                page.fill('input[type="email"]', EMAIL)
                page.fill('input[type="password"]', PASSWORD)
                page.keyboard.press("Enter")
                page.wait_for_timeout(2500)
                page.evaluate(f"localStorage.theme = '{theme}'")
                page.goto(f"{FRONT}/c/{chat_id}")
                try:
                    page.wait_for_selector("text=query_emr", timeout=30000)
                except Exception:
                    page.screenshot(path=f"{OUT}/failed.png")
                    print("url:", page.url)
                    raise
                page.wait_for_timeout(1500)
                # 両方のブロックを開く(ヘッダの pill)
                for header in page.locator("text=query_emr").all():
                    header.click()
                    page.wait_for_timeout(400)
                page.wait_for_timeout(800)
                tables = page.locator("table.border-separate")
                info = page.evaluate("""() => {
                        const ts = [...document.querySelectorAll('table.border-separate')];
                        return ts.map((t) => {
                            const box = t.parentElement;
                            const th = t.querySelector('th');
                            return {
                                rows: t.querySelectorAll('tbody tr').length,
                                cols: t.querySelectorAll('thead th').length,
                                boxH: box.clientHeight, scrollH: box.scrollHeight,
                                boxW: box.clientWidth, scrollW: box.scrollWidth,
                                thPos: getComputedStyle(th).position,
                                longTitle: [...t.querySelectorAll('td div')]
                                    .map((d) => d.title).find((s) => s.length > 40) ?? null,
                            };
                        });
                    }""")
                print(theme, width, json.dumps(info, ensure_ascii=False))
                print(
                    "  footer:",
                    page.locator(
                        "text=/Showing first|先頭 .* 行を表示/"
                    ).all_inner_texts(),
                )
                first = tables.first
                first.scroll_into_view_if_needed()
                page.wait_for_timeout(300)
                page.screenshot(
                    path=f"{OUT}/table-{width}-{theme}.png", full_page=False
                )
                # 縦スクロール後も見出しが残ることの絵
                first.evaluate("(t) => { t.parentElement.scrollTop = 600; }")
                page.wait_for_timeout(300)
                page.screenshot(
                    path=f"{OUT}/table-{width}-{theme}-scrolled.png", full_page=False
                )
                # 2 本目(小さな表)
                tables.nth(1).scroll_into_view_if_needed()
                page.wait_for_timeout(300)
                page.screenshot(
                    path=f"{OUT}/table-{width}-{theme}-small.png", full_page=False
                )
                ctx.close()
        browser.close()
finally:
    api("DELETE", f"/api/v1/chats/{chat_id}", token)
print("screenshots:", OUT)
