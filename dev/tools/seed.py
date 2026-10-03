#!/usr/bin/env python3
"""dev DB の nucllei 側 fixture を用意する開発用スクリプト。

やること:
  1. render_probe tool の登録(レンダリング/tool call 表示のデバッグ用)
  2. spawnable な子 preset `sub-worker` の upsert(#107 の subagent 検証用)
  3. preset(既定 llens-thinking)への配線 — meta.toolIds に render_probe と
     builtin_subagent(#140: run_subagent は builtin tool)、meta.subagentPresetIds に
     sub-worker を追記(既存の配線は保持)

**llens の一次コンテンツ(tool / skill / filter / システムプロンプト)は扱わない。**
原本は prism-hu/llens の `nucllei/{tools,skills,functions,prompts}/` で、登録は
あちらの `scripts/nucllei/sync.py` が持つ(dev へ向けるなら NUCLLEI_ORIGIN=
http://localhost:3000)。nucllei 側にコピーを置くと二重管理になるうえ、
`COPY nucllei /app/nucllei` で本番イメージにまで載ってしまう。

使い方は同ディレクトリの README.md を参照。localhost の dev サーバにのみ投げる。
アプリのソースには一切触れず、公開 API 経由で DB を更新するだけ(再実行可)。
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

# spawnable な子 preset(run_subagent の飛び先、#107)。親 preset と同じ base model で
# upsert し、親の meta.subagentPresetIds に配線する。
SUBAGENT_PRESET_ID = "sub-worker"
# run_subagent 本体(#140 で builtin tool 化 — toolIds に入れたときだけ載る)
SUBAGENT_TOOL_ID = "builtin_subagent"

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


def ensure_subagent_preset(base, token, parent_model_id):
    """親 preset と同じ base model の spawnable 子 preset を upsert して id を返す。"""
    status, parent = _req(
        "GET", f"{base}/api/v1/models/model?id={parent_model_id}", token
    )
    if status != 200 or not parent:
        print(
            f"[model] {parent_model_id} 取得失敗 status={status}: {parent}",
            file=sys.stderr,
        )
        sys.exit(1)

    status, existing = _req(
        "GET", f"{base}/api/v1/models/model?id={SUBAGENT_PRESET_ID}", token
    )
    if status == 200 and existing:
        # 既存 meta / params は保持し、base の追従と無効化だけ行う
        meta = existing.get("meta") or {}
        params = existing.get("params") or {}
    else:
        existing = None
        meta = {}
        params = {}
    meta["description"] = "subagent 検証用の spawnable 子 preset(seed.py が管理)"
    form = {
        "id": SUBAGENT_PRESET_ID,
        "base_model_id": parent.get("base_model_id"),
        "name": "Sub Worker",
        "meta": meta,
        "params": params,
        "access_control": None,
        # 裏方 preset の想定運用(#107 仕様変更 2026-08-14): is_active=False で
        # ユーザーのモデルピッカーからは隠しつつ、subagent の spawnable としては
        # 選択・実行できる(既存行があっても False へ更新する)。
        "is_active": False,
    }
    if existing:
        status, payload = _req(
            "POST",
            f"{base}/api/v1/models/model/update?id={SUBAGENT_PRESET_ID}",
            token,
            form,
        )
        verb = "update"
    else:
        status, payload = _req("POST", f"{base}/api/v1/models/create", token, form)
        verb = "create"
    if status != 200:
        print(
            f"[model] {SUBAGENT_PRESET_ID} {verb} 失敗 status={status}: {payload}",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"[model] {verb} OK: {SUBAGENT_PRESET_ID} (base={form['base_model_id']})")
    return SUBAGENT_PRESET_ID


def wire_preset(base, token, model_id, tool_ids, subagent_preset_ids):
    """preset の meta.toolIds / meta.subagentPresetIds に追記する(既存は保持)。

    llens コンテンツ(skillIds や llens tool の toolIds)には触らない — あちらの
    sync.py が登録した後、配線は nucllei 側(UI か本スクリプトの外)の責務。
    """
    status, model = _req("GET", f"{base}/api/v1/models/model?id={model_id}", token)
    if status != 200 or not model:
        print(f"[model] {model_id} 取得失敗 status={status}: {model}", file=sys.stderr)
        sys.exit(1)
    meta = model.get("meta") or {}
    meta["toolIds"] = list(dict.fromkeys((meta.get("toolIds") or []) + tool_ids))
    meta["subagentPresetIds"] = list(
        dict.fromkeys((meta.get("subagentPresetIds") or []) + subagent_preset_ids)
    )
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
    print(f"[model]   subagentPresetIds={new_meta.get('subagentPresetIds')}")
    # MODELS キャッシュ再構築(これをしないと manifest に反映されない)
    _req("GET", f"{base}/api/models", token)


def main():
    ap = argparse.ArgumentParser(description="dev DB の nucllei 側 fixture を用意する")
    ap.add_argument("--base", default="http://localhost:3000", help="ベースURL")
    ap.add_argument("--token", required=True, help="admin の Bearer トークン")
    ap.add_argument("--model", default=MODEL_ID, help="紐付け先 preset モデル id")
    args = ap.parse_args()

    base = args.base.rstrip("/")
    content = SRC.read_text(encoding="utf-8")

    register_tool(base, args.token, content)
    subagent_preset_id = ensure_subagent_preset(base, args.token, args.model)
    wire_preset(
        base,
        args.token,
        args.model,
        [TOOL_ID, SUBAGENT_TOOL_ID],
        [subagent_preset_id],
    )
    print("完了")


if __name__ == "__main__":
    main()
