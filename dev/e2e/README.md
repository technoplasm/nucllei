# dev/e2e — Playwright 実機チェック(skill 機能)

dev サーバ(backend :8080 + vite。ポートはスクリプト冒頭の `FRONT` を環境に合わせる)と
dev アカウント(`ken@llens.local` / `llens-dev`、`_memos/dev-environment.md`)前提の
半自動チェック。pytest ではなく実 SGLang × Kimi を叩くので CI には載せない。

```sh
.venv/bin/python dev/e2e/check_skill_command.py       # /command 発火・会話持続・View Result 表示
.venv/bin/python dev/e2e/check_lazy_tools.py          # tool 遅延取り込みの答え合わせ(両入口)
.venv/bin/python dev/e2e/check_skill_result_render.py # 構造化 result の md ハイライト + Tools loaded
```

## 前提 fixture(dev DB に無ければ作る)

- skill `kansai`(content: 関西弁指示 + {{USER_NAME}}、description あり)
- skill `vault`(tool_ids=["secret_password"]、description あり)
- tool `secret_password`(`kotori-sazanka-9412` を返すだけ。access_control={} = 私有
  — skill 経由でだけ届くことの検証を兼ねる)
- preset `llens-assistant` の `meta.skillIds` に両 skill を紐づけ、`GET /api/models` で
  MODELS キャッシュを更新(API で preset を書き換えた場合の必須手順)

作成コマンド例は issue #35 のコメント(2026-07-06)か、このリポジトリの
`_memos/skill-design.md` step2a/2b の記述を参照。
