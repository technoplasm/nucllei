# llens の設定モデル(config layer)— #30

設定の優先順位は **明示 env > DB(UI 保存)> 既定** の3段階。真実源は本メモ。
既定「値」は `llens-defaults.md`、本メモは「どう管理するか」。

## モデル(明示 env > DB > 既定)
> **env が非空(明示設定)なら env が正 — 起動時にその値を DB へ書き込む(config-as-code の
> 権威シード。env を変えれば必ず効く)。env が空なら DB の保存値(UI 変更)を採用し、再起動後も
> 残る。DB も無ければ既定値。**

- **起動時**(`RuntimeConfig.__init__`)の実効値:
  1. `env_name` が **非空** → env 値を採用し、DB(config テーブル)へ書き込む(env が DB を上書き)。
  2. env が空 → **DB の保存値**(あれば)を採用(= UI 変更が永続)。
  3. DB も無ければ **hardcoded 既定**。
- **実行中**: admin UI からの変更は `app.state.config` に反映 + DB へ永続(`RuntimeConfig.save()`)。
  ※ 改訂(2026-07-17、#80): Redis 経由の複数ワーカー共有は**撤去** — wire v2 のタスクレジストリが
  in-process のため multi-worker 自体が非サポート(単一プロセス前提)。
- **再起動**: env が設定されている項目はその env 値で再確定(+DB 同期)。env 未設定の項目は
  DB の保存値がそのまま残る。
- 帰結:「env を変えたのに反映されない」は起きない(env が常に勝つ)。かつ **env を置いていない
  項目は UI 設定が永続する**(例: 特定接続を UI で足す運用)。恒久固定したい値は env/compose に置く。
- **裏面の帰結(2026-07-17 に dev で実地確認)**: 一度でも env 付きで起動すると値が DB へシード
  されるため、**env を消しただけでは元に戻らない**(空 env は「明示なし」扱い → DB 保存値が生きる)。
  完全に戻すには DB の `config` テーブル(最新行の JSON)から該当キーを削除して再起動する
  (実例: dev で `GOOGLE_CLIENT_ID` 等のダミー OAuth を試した後、`oauth.google`/`oauth.microsoft`
  サブツリーを SQL で除去した)。un-seed の仕組みは意図的に無い — 「明示 env > DB」の設計対価。

## 背景(#30 の経緯)
- OWUI 元実装は「DB に値があれば DB が env に勝つ」→「env を変えても反映されない」温床。
- `798a7aa4c`(2026-07-08)でこれを「env が常に勝つ・DB は見ない」に反転したが、**env 未設定項目の
  UI 保存値が再起動で必ず既定に戻る**副作用が発生(config の DB 永続経路を叩くテストが無く、すり抜け)。
- 2026-07-09、上記3段階に是正(明示 env > DB > 既定)。TDD(`test_runtime_config.py`)で固定。

## 実装
- **3段階是正(2026-07-09)**: `RuntimeConfig.__init__` を「明示 env > DB > 既定」に。env 非空時は
  DB へ書き込む(シード)。TDD `test_runtime_config.py`(env明示→DB反映 / env空→DB採用 / env空+DB無→既定)。
  レジストリ名を `PERSISTENT_CONFIG_REGISTRY`→`RUNTIME_CONFIG_REGISTRY` に掃除。CORE 回帰 91 passed。
- 設定機構 `798a7aa4c`(2026-07-08, 上記で是正済み): `PersistentConfig`→`RuntimeConfig` 改名。
  `ENABLE_PERSISTENT_CONFIG` 撤去。`save()`/`update()` と DB 永続機構(`config` テーブル / import・export /
  Redis 共有)は温存。→ Redis 共有は 2026-07-17(#80、`e4b023390`)に撤去(依存 redis ごと。
  Config モデルは `models/configs.py` へ移設)。
- Settings 掃除 `e9f15cf10`: admin General・About から update check / What's New / Help・外部リンク /
  License 表示を撤去。version は残す(BSD 著作権・Twemoji は #34 まで温存)。
- version 焼き込み `c92c0784f`: deploy tag(`prodYYYYMMDD_NN`、#25)を build 時 `APP_VERSION` で
  焼き込み可能に(vite.config.ts / env.py / Dockerfile)。未設定なら package.json の 0.6.5。
  → **2026-07-16 改訂**(f37361283 / 0d69f2da4): バージョンの真実源は git tag
  (リリース `vYYYYMMDD-NN` / プレ `pre-NN` の 2 形式のみ describe --match で拾う)。
  優先順 = `APP_VERSION` env(docker build 焼き込み)> git describe(dev)> package.json の 0.6.5。
  命名は APP_* = 本体 Nucllei / WEBUI_* = 配備層(`WEBUI_VERSION` は配備 tag 用に新設)。
  `prodYYYYMMDD_NN` 形式は廃止。git hash(BUILD_HASH 系)は全面撤去。

## 残(任意・後回し)
- 運用固定の項目(接続 URL・認証方式・エンジン種別など、実行時に触らせたくないもの)を素の
  `os.environ.get(...)` に落として設定オブジェクトから外す棚卸し。env 勝ちが効いているので急ぎではない。
