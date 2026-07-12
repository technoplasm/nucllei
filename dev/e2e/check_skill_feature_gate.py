"""#42 E7: モデルの機能「スキル」トグルの実機確認(実 Kimi)。

- OFF(meta.features.skills=false): `/kansai` が発火しない(合成 load_skill 注入なし)。
  素の入力がそのままモデルへ渡り、関西弁化しない。
- ON(既定): `/kansai` が発火して load_skill が履歴に入る(既存挙動)。

前提: llens-assistant に skill kansai が紐づいている(dev fixture)。
"""

import json
import re
import time
import urllib.request

BASE = "http://localhost:3000"
FRONT = "http://localhost:5174"
MODEL_ID = "llens-assistant"


def _req(method, url, token, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(
        BASE + url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(r, timeout=30) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw else None


req = urllib.request.Request(
    BASE + "/api/v1/auths/signin",
    data=json.dumps({"email": "ken@llens.local", "password": "llens-dev"}).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
token = json.loads(urllib.request.urlopen(req).read())["token"]


def set_feature_skills(enabled):
    """meta.features.skills を設定し MODELS キャッシュを更新。"""
    model = _req("GET", f"/api/v1/models/model?id={MODEL_ID}", token)
    meta = model.get("meta") or {}
    features = dict(meta.get("features") or {})
    if enabled:
        features.pop("skills", None)
    else:
        features["skills"] = False
    meta["features"] = features
    form = {
        "id": model["id"],
        "base_model_id": model.get("base_model_id"),
        "name": model["name"],
        "meta": meta,
        "params": model.get("params") or {},
        "access_control": model.get("access_control"),
        "is_active": model.get("is_active", True),
    }
    _req("POST", f"/api/v1/models/model/update?id={MODEL_ID}", token, form)
    _req("GET", "/api/models", token)  # app.state.MODELS 再構築


def get_chat(chat_id):
    return _req("GET", f"/api/v1/chats/{chat_id}", token)["chat"]


def wait_done(chat_id, timeout=240):
    deadline = time.time() + timeout
    while time.time() < deadline:
        chat = get_chat(chat_id)
        assistant = [
            m
            for m in chat["history"]["messages"].values()
            if m.get("role") == "assistant"
        ]
        if assistant and all(m.get("done") for m in assistant):
            return chat
        time.sleep(1.5)
    raise TimeoutError


def load_skill_calls(asst):
    names = []
    for b in asst.get("content_blocks") or []:
        if b.get("type") == "tool_calls":
            for c in b.get("calls", []):
                names.append(c["function"]["name"])
    return names


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

    def send_kansai():
        page.goto(FRONT)
        page.wait_for_selector("#chat-input", timeout=30000)
        time.sleep(1.5)
        page.click("#chat-input")
        page.keyboard.type("/kansai 天気について一言")
        page.keyboard.press("Enter")
        page.wait_for_url(re.compile(r"/c/"), timeout=30000)
        chat_id = page.url.split("/c/")[1].split("?")[0]
        chat = wait_done(chat_id)
        return [
            m for m in chat["history"]["messages"].values() if m["role"] == "assistant"
        ][-1]

    try:
        # ---- OFF: 発火しない ----
        set_feature_skills(False)
        time.sleep(1)
        asst = send_kansai()
        calls = load_skill_calls(asst)
        check("E7a OFF: load_skill 非発火", "load_skill" not in calls, f"calls={calls}")

        # ---- ON: 発火する(既定へ戻す) ----
        set_feature_skills(True)
        time.sleep(1)
        asst = send_kansai()
        calls = load_skill_calls(asst)
        check("E7b ON: load_skill 発火", "load_skill" in calls, f"calls={calls}")
    finally:
        set_feature_skills(True)  # 後始末: 必ず既定へ
        browser.close()

print()
failed = [l for l, ok in results if not ok]
print(f"==== {len(results) - len(failed)}/{len(results)} PASS ====")
if failed:
    print("FAILED:", ", ".join(failed))
    raise SystemExit(1)
