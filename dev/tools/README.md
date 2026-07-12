# dev/tools

レンダリング/tool call 表示デバッグ用の開発ツール（`render_probe`）とその登録スクリプト。
アプリ本体には手を入れず、公開 API 経由で dev DB に登録するだけ。air-gapped 前提で localhost のみ。

## 登録手順

```sh
# 1) admin トークン取得
TOKEN=$(curl -s -X POST http://localhost:3000/api/v1/auths/signin \
  -H 'Content-Type: application/json' \
  -d '{"email":"ken@llens.local","password":"llens-dev"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

# 2) render_probe を登録し、llens-assistant の toolIds に追加
python3 dev/tools/seed.py --token "$TOKEN"
```

`render_probe(kind)` / `echo(text)` / `probe_args(code, items, count)` /
`probe_result_table(department)` が使える。
`kind` は `table` / `code` / `math` / `long` / `mixed` / `edge`。
`probe_result_table` は `Table` 戻りで型駆動レンダリング(#20)の表示確認用
(probe_args は宣言なしのコントロール)。
旧 `run_python` ダミーは本物の sandbox tool(`dev/tools/sandbox.py`、#18-3)搭載に伴い
撤去 — 同名衝突で本物が discard されるため。
