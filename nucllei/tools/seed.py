#!/usr/bin/env python3
"""llens 一次コンテンツ(nucllei/tools/*.py + nucllei/skills/*.md)を DB へ登録する。

アプリのソースには触れず、公開 API 経由で create/update するだけ(再実行可)。
valves は環境ごとの接続先(cage / SGLang)を --cage / --sglang で調整する。

使い方(dev):
    TOKEN=$(curl -s -X POST http://localhost:3000/api/v1/auths/signin \
      -H 'Content-Type: application/json' \
      -d '{"email":"ken@llens.local","password":"llens-dev"}' \
      | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
    python3 nucllei/tools/seed.py --token "$TOKEN"
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

TOOLS_DIR = Path(__file__).parent
SKILLS_DIR = TOOLS_DIR.parent / "skills"

# tool id → 定義ファイルと表示情報。preset=True のものを preset の toolIds に常載する
# (medical_calculator は skill `medical-calc` の tool_ids 経由で遅延ロード)。
TOOLS = {
    "doc": {
        "file": "doc_tool.py",
        "name": "Doc",
        "description": "cage 文書ツリーの目次・本文・全文検索",
        "preset": True,
    },
    "subagent": {
        "file": "subagent.py",
        "name": "Subagent",
        "description": "資料を別 context のサブエージェントに読ませて結果だけ返す",
        "preset": True,
    },
    "password_generator": {
        "file": "password_generator.py",
        "name": "Password Generator",
        "description": "アカウント発行用のパスワード生成",
        "preset": True,
    },
    "medical_calculator": {
        "file": "medical_calculator.py",
        "name": "Medical Calculator",
        "description": "医学計算・重症度スコア",
        "preset": False,
    },
}


def _req(method, url, token, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw


def upsert_tool(base, token, tool_id, entry, valves):
    form = {
        "id": tool_id,
        "name": entry["name"],
        "content": (TOOLS_DIR / entry["file"]).read_text(encoding="utf-8"),
        "meta": {"description": entry["description"]},
        "access_control": None,
    }
    status, existing = _req("GET", f"{base}/api/v1/tools/id/{tool_id}", token)
    if status == 200 and existing:
        status, payload = _req(
            "POST", f"{base}/api/v1/tools/id/{tool_id}/update", token, form
        )
        verb = "update"
    else:
        status, payload = _req("POST", f"{base}/api/v1/tools/create", token, form)
        verb = "create"
    if status != 200:
        print(
            f"[tool] {tool_id} {verb} 失敗 status={status}: {payload}", file=sys.stderr
        )
        sys.exit(1)
    print(f"[tool] {verb} OK: {tool_id}")

    if valves:
        status, payload = _req(
            "POST", f"{base}/api/v1/tools/id/{tool_id}/valves/update", token, valves
        )
        if status != 200:
            print(
                f"[tool] {tool_id} valves 失敗 status={status}: {payload}",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"[tool]   valves: {valves}")


def parse_skill(path):
    """front matter (name / description / tool_ids) と本文を返す。"""
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    if not m:
        print(f"[skill] {path.name}: front matter が無い", file=sys.stderr)
        sys.exit(1)
    fm, body = m.group(1), m.group(2).strip()
    meta = {}
    for line in fm.splitlines():
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    name = meta.get("name", path.stem)
    description = meta.get("description", "").strip('"')
    tool_ids = json.loads(meta.get("tool_ids") or "[]")
    return name, description, tool_ids, body


def upsert_skill(base, token, skill_id, path):
    name, description, tool_ids, body = parse_skill(path)
    form = {
        "id": skill_id,
        "name": name,
        "description": description,
        "content": body,
        "tool_ids": tool_ids,
        "access_control": None,
    }
    status, existing = _req("GET", f"{base}/api/v1/skills/id/{skill_id}", token)
    if status == 200 and existing:
        status, payload = _req(
            "POST", f"{base}/api/v1/skills/id/{skill_id}/update", token, form
        )
        verb = "update"
    else:
        status, payload = _req("POST", f"{base}/api/v1/skills/create", token, form)
        verb = "create"
    if status != 200:
        print(
            f"[skill] {skill_id} {verb} 失敗 status={status}: {payload}",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"[skill] {verb} OK: {skill_id} (tools={tool_ids})")


def wire_preset(base, token, model_id, tool_ids, skill_ids):
    """preset の meta.toolIds / meta.skillIds に追記する(既存は保持)。"""
    status, model = _req("GET", f"{base}/api/v1/models/model?id={model_id}", token)
    if status != 200 or not model:
        print(f"[model] {model_id} 取得失敗 status={status}: {model}", file=sys.stderr)
        sys.exit(1)
    meta = model.get("meta") or {}
    meta["toolIds"] = list(dict.fromkeys((meta.get("toolIds") or []) + tool_ids))
    meta["skillIds"] = list(dict.fromkeys((meta.get("skillIds") or []) + skill_ids))
    form = {
        "id": model["id"],
        "base_model_id": model.get("base_model_id"),
        "name": model["name"],
        "meta": meta,
        "params": model.get("params") or {},
        "access_control": model.get("access_control"),
        "is_active": model.get("is_active", True),
    }
    status, payload = _req(
        "POST", f"{base}/api/v1/models/model/update?id={model_id}", token, form
    )
    if status != 200:
        print(f"[model] update 失敗 status={status}: {payload}", file=sys.stderr)
        sys.exit(1)
    new_meta = payload.get("meta", {})
    print(f"[model] OK {model_id}")
    print(f"[model]   toolIds={new_meta.get('toolIds')}")
    print(f"[model]   skillIds={new_meta.get('skillIds')}")
    # MODELS キャッシュ再構築(これをしないと manifest に反映されない)
    _req("GET", f"{base}/api/models", token)


def main():
    ap = argparse.ArgumentParser(description="llens tools/skills を DB に登録する")
    ap.add_argument("--base", default="http://localhost:3000", help="ベースURL")
    ap.add_argument("--token", required=True, help="admin の Bearer トークン")
    ap.add_argument(
        "--model", default="llens-assistant", help="紐付け先 preset モデル id"
    )
    ap.add_argument(
        "--cage", default="http://localhost:13303", help="cage API の base URL"
    )
    ap.add_argument(
        "--sglang",
        default="http://prism-llens:13300/v1",
        help="SGLang OpenAI 互換 endpoint(subagent 用)",
    )
    args = ap.parse_args()
    base = args.base.rstrip("/")

    valves = {
        "doc": {"api_base": args.cage},
        "subagent": {"model_base_url": args.sglang, "cage_api_base": args.cage},
    }
    for tool_id, entry in TOOLS.items():
        upsert_tool(base, args.token, tool_id, entry, valves.get(tool_id))

    skill_ids = []
    for path in sorted(SKILLS_DIR.glob("*.md")):
        skill_id = path.stem
        upsert_skill(base, args.token, skill_id, path)
        skill_ids.append(skill_id)

    preset_tools = [tid for tid, e in TOOLS.items() if e["preset"]]
    # view_image は builtin から外し、prism 側 tool `feed_image` に一本化(#89 改訂)。
    # dev preset への露出は prism tool を seed する側で足す。
    wire_preset(base, args.token, args.model, preset_tools, skill_ids)
    print("完了")


if __name__ == "__main__":
    main()
