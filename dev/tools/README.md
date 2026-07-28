# dev/tools

レンダリング/tool call 表示デバッグ用の開発ツール（`render_probe`）とその登録スクリプト。
アプリ本体には手を入れず、公開 API 経由で dev DB に登録するだけ。localhost のみ。

## 登録手順

```sh
# 1) admin トークン取得
TOKEN=$(curl -s -X POST http://localhost:3000/api/v1/auths/signin \
  -H 'Content-Type: application/json' \
  -d '{"email":"ken@llens.local","password":"llens-dev"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

# 2) render_probe を登録し、llens-thinking の toolIds に追加
python3 dev/tools/seed.py --token "$TOKEN"
```

## 使える probe

- **markdown 本文**: `render_probe(kind)` — `kind` は `table` / `code` / `math` / `long` / `mixed` / `edge`
- **引数表示**: `echo(text)` / `probe_args(code, items, count)`(宣言なしコントロール)/
  `probe_args_code(sql, note)`(引数 Code 宣言 — sandbox なしで確認)
- **結果 kind**(#20 型駆動レンダリング。kind ごとに別 tool なので、モデルに
  「text 戻りの probe を呼んで」と名指しで頼める):
  - `probe_result_text()` — `Text` 宣言 → pre-wrap 素テキスト
  - `probe_result_code()` — `Code("python")` 宣言 → ハイライト
  - `probe_result_json()` — dict 戻り(宣言なし)→ JSON 整形
  - `probe_result_plain(variant)` — 素の str・宣言なし(既定フォールバック)。
    `plain` / `scalar` / `json_string` / `empty`
  - `probe_result_image(label)` — data URL 抽出(files チャンネル)→ 画像表示
  - `probe_result_error(message)` — 必ず例外 → str(e) の表示確認

旧 `run_python` ダミーは本物の sandbox tool(`dev/tools/sandbox.py`、#18-3)搭載に伴い
撤去 — 同名衝突で本物が discard されるため。旧 `probe_result_table` は table 描画の
廃止(#60)に伴い撤去。
