# llens の設定モデル(config layer)— #30

OWUI の設定は **env を単一の正の源(single source of truth)**とする。真実源は本メモ。
既定「値」は `llens-defaults.md`、本メモは「どう管理するか」。

## モデル(env 正・起動時に再強制。DB は稼働中の可変ストア)
> **env が毎回の起動で必ず勝つ(正の源)。実行中の UI 変更は DB に載って稼働中は有効
> (複数ワーカーで共有)だが、次の起動で env が再び上書きする。恒久変更の唯一の経路は env/compose。**

- **起動時**: 各 config の実効値 = env(あれば)/ 無ければ hardcoded 既定。**env が DB 値より優先**。
- **実行中**: admin UI からの変更は `app.state.config` に反映 + **DB へ永続**(`RuntimeConfig.save()`)。
  稼働中は有効で、Redis 経由で複数ワーカーに共有される。
- **再起動**: env が実効値を上書きし直す(DB の実行時値は起動時には勝てない)。
- 帰結: 「env を変えたのに反映されない」が起きない。durable にしたい値は env/compose に置く運用
  (横展開の環境構築も env/compose ファイルが正)。

## 背景(#30 で直したバグ)
OWUI の元実装は「`config` テーブルに値があれば DB が env に勝つ」で、初回起動や UI 保存で
DB に値が入ると以後 env が無視された(「env を変えたのに反映されない」温床)。
起動時の優先順位を env 勝ちに反転してこれを解消した。

## 実装(完了、2026-07-08)
- 設定機構 `798a7aa4c`: `RuntimeConfig.__init__` を env 勝ちに(起動時は env が DB 値より優先)。
  `PersistentConfig`→`RuntimeConfig` 改名。`ENABLE_PERSISTENT_CONFIG` 撤去。
  `save()`/`update()` の DB 永続は維持。DB 永続機構(`config` テーブル / import・export / Redis 共有)は温存。
  CORE(認証/Filter/authz/defaults)回帰 46 passed。
- Settings 掃除 `e9f15cf10`: admin General・About から update check / What's New / Help・外部リンク /
  License 表示を撤去。version は残す(BSD 著作権・Twemoji は #34 まで温存)。
- version 焼き込み `c92c0784f`: deploy tag(`prodYYYYMMDD_NN`、#25)を build 時 `APP_VERSION` で
  焼き込み可能に(vite.config.ts / env.py / Dockerfile)。未設定なら package.json の 0.6.5。

## 残(任意・後回し)
- 運用固定の項目(接続 URL・認証方式・エンジン種別など、実行時に触らせたくないもの)を素の
  `os.environ.get(...)` に落として設定オブジェクトから外す棚卸し。env 勝ちが効いているので急ぎではない。
