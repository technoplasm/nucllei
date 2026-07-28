#!/usr/bin/env python3
"""dev DB へ render_probe tool を登録し、preset モデル llens-thinking に紐付ける開発用スクリプト。

使い方は同ディレクトリの README.md を参照。localhost の dev サーバにのみ投げる。
アプリのソースには一切触れず、公開 API 経由で DB を更新するだけ。
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

TOOL_ID = "render_probe"
TOOL_NAME = "Render Probe"
MODEL_ID = "llens-thinking"  # --model で上書き可(dev DB の preset id は変遷している)

SRC = Path(__file__).with_name("render_probe.py")


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


def register_tool(base, token, content):
    """render_probe を create、既存なら update する。"""
    form = {
        "id": TOOL_ID,
        "name": TOOL_NAME,
        "content": content,
        "meta": {"description": "レンダリング/tool call 表示デバッグ用の開発ツール"},
        "access_control": None,
    }
    status, payload = _req("GET", f"{base}/api/v1/tools/id/{TOOL_ID}", token)
    if status == 200 and payload:
        print(f"[tool] 既存 {TOOL_ID} を update します")
        status, payload = _req(
            "POST", f"{base}/api/v1/tools/id/{TOOL_ID}/update", token, form
        )
    else:
        print(f"[tool] {TOOL_ID} を create します")
        status, payload = _req("POST", f"{base}/api/v1/tools/create", token, form)

    if status != 200:
        print(f"[tool] 失敗 status={status}: {payload}", file=sys.stderr)
        sys.exit(1)
    print(f"[tool] OK id={payload.get('id')} name={payload.get('name')}")


def attach_to_model(base, token, model_id):
    """llens-thinking の meta.toolIds に render_probe を追加(既存 toolIds は保持)。"""
    status, model = _req("GET", f"{base}/api/v1/models/model?id={model_id}", token)
    if status != 200 or not model:
        print(f"[model] {model_id} 取得失敗 status={status}: {model}", file=sys.stderr)
        sys.exit(1)

    meta = model.get("meta") or {}
    tool_ids = list(meta.get("toolIds") or [])
    if TOOL_ID not in tool_ids:
        tool_ids.append(TOOL_ID)
    meta["toolIds"] = tool_ids

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
    print(f"[model] OK {model_id} toolIds={payload.get('meta', {}).get('toolIds')}")


def main():
    ap = argparse.ArgumentParser(description="render_probe を dev DB に登録する")
    ap.add_argument("--base", default="http://localhost:3000", help="ベースURL")
    ap.add_argument("--token", required=True, help="admin の Bearer トークン")
    ap.add_argument("--model", default=MODEL_ID, help="紐付け先 preset モデル id")
    args = ap.parse_args()

    base = args.base.rstrip("/")
    content = SRC.read_text(encoding="utf-8")

    register_tool(base, args.token, content)
    attach_to_model(base, args.token, args.model)
    print("完了")


if __name__ == "__main__":
    main()
