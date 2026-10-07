# dev/e2e — Playwright 実機チェック(skill 機能)

dev サーバ(backend :3000 + vite。ポートはスクリプト冒頭の `FRONT` を環境に合わせる)と
dev アカウント(`dev@nucllei.local` / `dev`、`_memos/dev-environment.md`)前提の
半自動チェック。pytest ではなく実 SGLang × Kimi を叩くので CI には載せない。

```sh
.venv/bin/python dev/e2e/check_skill_command.py       # /command 発火・会話持続・View Result 表示
.venv/bin/python dev/e2e/check_lazy_tools.py          # tool 遅延取り込みの答え合わせ(両入口)
.venv/bin/python dev/e2e/check_skill_result_render.py # 構造化 result の md ハイライト + Tools loaded
.venv/bin/python dev/e2e/check_sandbox_tools.py       # 組み込み sandbox tool(#18-3 起源、E1〜E6)
.venv/bin/python dev/e2e/check_skill_feature_gate.py  # 機能「スキル」トグル(#42 E7、後始末で ON に戻す)
.venv/bin/python dev/e2e/check_result_table.py [dir]  # tool 結果の表(kind=table)の目視 — モデル不要、チャットを API で作って消す
.venv/bin/python dev/e2e/mock_reasoning_model.py      # モックモデルサーバ(:13399) — think 綴じ込み再現(冒頭 docstring 参照)
```

`check_sandbox_tools.py` は sandbox コンテナ(この repo の `sandbox/` = `ghcr.io/technoplasm/nucllei-sandbox`、
`127.0.0.1:13302`)と、**`SANDBOX_URL=http://127.0.0.1:13302` 付きで起動した backend** を要求する
(#132 の `SANDBOX_TOKEN` は opt-in なので dev では省略可。設定するなら**両側同じ値**で)。
tool は組み込みの `builtin_sandbox`(#114、2026-09-03)なので DB への手動登録は要らない —
env が空だと env ゲートでそもそも生えない。preset への接続(toolIds)はスクリプトが冪等に張る。
`check_skill_feature_gate.py` は llens-assistant の `meta.features.skills` を一時的に false にして
`/kansai` の非発火を確認し、終了時に必ず既定(ON)へ戻す。

## 前提 fixture(dev DB に無ければ作る)

- skill `kansai`(content: 関西弁指示 + {{USER_NAME}}、description あり)
- skill `vault`(tool_ids=["secret_password"]、description あり)
- tool `secret_password`(`kotori-sazanka-9412` を返すだけ。access_control={} = 私有
  — skill 経由でだけ届くことの検証を兼ねる)
- preset `llens-assistant` の `meta.skillIds` に両 skill を紐づけ、`GET /api/models` で
  MODELS キャッシュを更新(API で preset を書き換えた場合の必須手順)

作成コマンド例は issue #35 のコメント(2026-07-06)か、このリポジトリの
`_memos/skill-design.md` step2a/2b の記述を参照。
