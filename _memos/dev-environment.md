# 開発環境セットアップと運用知見(別環境での開発継続用)

2026-07-06 作成。llens をクリーンな環境に clone して開発を続けるために必要な情報の集約。
private repo 前提で dev 用の資格情報も書く(本番の秘密は無い)。
機能要件・方針の一次情報は `_memos/fork.md` / ロードマップは issue #16。

## ツールチェーン

- **node**: `.node-version` = 24.18.0(マネージャは環境依存 — nodebrew や mise。
  mise なら `mise install` が `.node-version` を拾い、`mise exec node@24.18.0 -- <cmd>` で実行)。
  **v24.4.0 は V8 バグで `pnpm install` が OOM クラッシュする(invalid array length)ので使わない**。
- **frontend**: pnpm(`pnpm install` / `pnpm add`)。**npm は使わない**(peer 依存で ERESOLVE 失敗。
  例: @fontsource をバンドルするフォント追加は `pnpm add`)。テストは `npx vitest run`。フォーマットは prettier
  (CI に format チェックあり。`fixture` / `_memos/` / `.claude/` は `.prettierignore` 対象外なので触らない。
  スクリプトで package.json 等を書き換えたら**末尾改行を欠かさない**)。
- **RuntimeConfig の追加は 2 点セット**(2026-07-17 に AttributeError で実害): `config.py` で
  定義するだけでは足りず、`main.py` で import + `app.state.config.X = X` の明示登録が必要
  (未登録だと参照時に `Config key not found`)。優先順位は env > DB(UI 保存)> 既定。
- **backend**: Python は uv(リポジトリ直下 `.venv`)。`uv sync` 相当で venv を作る。
  テストは `.venv/bin/pytest nucllei/test -q`(または repo ルートで `uv run pytest`)。
  **CI に backend の black 整形ゲート("Python CI / Format Backend")あり** — 手編集や sed で行を消すと
  black 的に崩れて CI が落ちる。**backend を触ったら push 前に `.venv/bin/black nucllei/`**
  (Backend Tests(pytest)は別ジョブで pass しても Format で落ちる)。frontend にも同型の
  prettier ゲート("Frontend Build / Format & Build")があり、こちらは `pnpm format`。
- **Svelte 5(互換モード)の 2 大モーダル罠**(#55 で実害・修理済みだが新規コードで再発しやすい):
  (1) `$: if (show) { _user = {...} }` の編集バッファ初期化は **bind:value の書き込みごとに
  再実行され入力が巻き戻る** — `let lastShow` の変化検知ガードで包む(UserModal.svelte が実例)。
  (2) portal(body へ appendChild)する component の無条件 `document.body.removeChild` は
  `{#if}` teardown と衝突して NotFoundError → **effect が中断され画面が中途半端に壊れる**
  (症状: モーダルが空欄)。`parentNode === document.body` を確認してから外す(common/Modal.svelte
  は修理済み — 新しい portal を書くときは同じガードを)。
  (3) **svelte-sonner の Toaster は onMount で reset()(全 toast 消去)する** — ページの
  onMount 中に撃った toast は Toaster マウントの後始末で闇に消える(#70 B2 で実害)。
  (4) **legacy → runes 変換の実行順の罠(2026-07-17、Chat.svelte で実害)**: legacy の `$:` は
  「スクリプト全宣言の評価後・onMount より先」に走る。`$effect` に機械的に置き換えると
  ①発火が onMount 後にずれて init 順序前提(例: loadChat 同期先頭の chatId.set() を
  onMount の !$chatId ガードが見る)が壊れる、②初期実行をスクリプト途中の直接呼び出しに
  すると後方 const の TDZ を踏む。対処 = 初期値はスクリプト**末尾**の直接呼び出しで処理し、
  以後の変化だけ `$effect`(+ 前回値ガードで初回 effect の二重発火防止)。
  (6) **`$state`/`$bindable` 配列に積んだ後の closure(生オブジェクト)に書いても再描画されない**
  (2026-07-18、MessageInput アップロードで実害 — status が 'uploading' に張り付く)。
  `files = [...files, item]` の時点で item は深層プロキシに包まれ、closure の `item.status = ...` は
  シグナルを通らない。legacy の `files = files` 再代入も runes では同一参照 no-op。
  **必ず `files[idx] = {...files[idx], ...}` とプロキシ経由で置き換える**(765d86a2b が実例)。
  (5) **`$: fn()` の依存は文中で「読んだ」変数だけ**(書き込みは依存にならない)。
  `$effect` 化で関数呼び出しの中身まで追跡されて発火過多になるときは、依存を
  `void x;` で明示し本体は先頭 `await tick()` などで追跡外に置く(Selector.resetView が実例)。
  (7) **`$effect` から親コールバック(onChange 等)を呼ぶと親側の読み書きまで依存に入る**
  (2026-07-19、AdvancedParams runes 化で実害 — `effect_update_depth_exceeded`)。
  親の handler が `info.params = {...info.params, ...params}` のように「読んで書く」と、
  effect が自分の依存 state を書く自己ループになる(legacy の `$: dispatch(...)` は
  handler が追跡外で無害だった)。**通知系 effect は依存を `void x;` で明示し、
  コールバック本体は `untrack(() => onChange(x))` に置く**(AdvancedParams / AccessControl が実例)。
  初期ロード直後に出す toast は root layout onMount 末尾 + setTimeout 遅延で発火する
  (失効通知の実例が `src/routes/+layout.svelte`)。
  (8) **依存パッケージを更新しても稼働中 vite は旧版の最適化キャッシュを配り続ける**
  (2026-07-19、bits-ui 0.19→2.x で実害 — 新コードが旧ライブラリで動き Content が無描画。
  console にエラーは出ない)。lockfile 変更の再最適化はサーバ再起動時にしか走らないため、
  `rm -rf node_modules/.vite` + `touch vite.config.ts`(vite の config 変更検知で in-place
  再起動 — ユーザーの tmux ペインの vite プロセスを kill せずに済む)で強制する。
  疑ったら `grep -l melt node_modules/.vite/deps/bits-ui*.js` のように**キャッシュの中身**を見る。
- **Svelte 5(互換モード)テンプレート式は「マークアップに直接書かれた変数」しか追跡しない**
  (#34 AdvancedParams で実害・修理済み)。`{#if isSet(item)}` のように reactive な `params` 読み取りを
  ヘルパ関数に隠すと、コンパイラがその呼び出しを **`$.untrack()` で包み再描画が起きない**(症状:
  スライダー/トグルを操作しても値が反映されない。警告も出ない)。→ テンプレートでは `params[item.key]` を
  **直接参照**し、現在値は引数で渡す(`cycleLabel(item, params[item.key])`)。
  併せて **動的キーの `bind:value={obj[key]}` は片方向にしか展開されない**(書き戻し無し)。書き込みは
  インスタンス関数内の **新 object 再代入**(`params = {...params, [key]: v}`)に統一すると bindable
  プロップの set として確実に伝播する(in-place `obj[key]=v` の mutate は追跡が不安定)。切り分けは
  `svelte/compiler` で client 生成し `untrack(...)` / 書き戻し代入(`params(params()[...]=...)`)の有無を grep。
- **Tailwind v4 の `space-y` は「後続兄弟に margin-top」ではなく `> :not(:last-child) { margin-bottom }`**
  (2026-07-17 実害): `:last-child` は DOM 構造で判定されるため、`display:none` / `absolute` の
  補助要素を可視コンテンツの**後ろの兄弟**に置くと、最後の可視要素が :last-child にならず
  余分な下マージンが残る(FloatingButtons で実害 — ContentRenderer の content div 内へ移動して解消)。
  devtools で出所不明の margin-bottom を見たらまず親の space-y を疑う。
- **vitest は import 破壊を検知しない**: store やコンポーネントを削除する撤去作業では、
  カバレッジ外のファイルに残った orphan import を vitest がすり抜ける(#67 で
  common/Tags/TagInput の削除済み store import が CI の build 段階まで到達)。
  **大きめの撤去をしたら push 前に `pnpm build` を一度回す**(svelte-check でも可だが build が確実)。
- **pre-push hook(2026-07-10 導入、2026-07-16 に ruff 追加)**: フォーマット 2 ゲート
  (black/prettier)+ **ruff(CI: Lint Backend)** を push 前に手元で再現する
  `dev/hooks/pre-push`(push 差分のファイルだけチェックするので数秒)。hook 本体は
  リポジトリに入っているが、**有効化は clone ごとに 1 回 `git config core.hooksPath dev/hooks`
  が必要**(git はセキュリティ上 hook を自動有効化しない — 新環境セットアップ時に忘れずに)。
  引っかかると push が中断される。緊急回避は `git push --no-verify`。
  **整形は必ず `pnpm format`(または `node_modules/.bin/prettier`)で行う** —
  `pnpm exec prettier --write` は同バージョン表示でも plugin 解決が食い違い、
  hook/CI(node_modules 直の prettier)と**別の整形結果**を出すことがある
  (2026-07-16 に svelte の長行 wrap で実際に発生、--write 済みなのにゲートで NG)。
- `.git/index.lock` が時々出るのは shell prompt の `gitstatusd` が握るため — stale なら `rm -f .git/index.lock`
  して commit を再試行(走っている git プロセスは無い)。
- **pytest の postgres**: `nucllei/test/conftest.py` が docker で自動起動しセッション共有。
  壊れたら `docker rm -f postgres-test-container-will-get-deleted` で確実に fresh になる。
  - **podman 環境(2026-07-14 確認)**: usermod や root は不要。rootless の user socket を立てて
    docker-py に `DOCKER_HOST` で教えるだけ:
    ```sh
    systemctl --user enable --now podman.socket   # 一度だけ(enable で永続)
    DOCKER_HOST=unix:///run/user/$UID/podman/podman.sock uv run pytest nucllei/test -q
    ```
    conftest の `docker.from_env()` も pytest_docker の `get_docker_ip()` もこれでそのまま動く(338 passed 実証済み)。
- **権限のネガティブテストは `mock_webui_user_strict` を使う**(test/util/mock_user.py、#55 で追加):
  従来の `mock_webui_user` は `get_admin_user` ごと差し替えるため **role="user" でも admin endpoint を
  素通りする**(401 を期待するテストが書けない)。strict 版は `get_current_user` だけを差し替えて
  role ゲートを実物で通す。#66(権限3層化)の回帰はこちらで書くこと。
- nucllei(旧 open_webui)は **import 時に DATABASE_URL などの env が確定**する(migration も import 時)。
  import 時確定 env のテストはサブプロセス起動 + `cwd`/`PYTHONPATH` にリポジトリルート(`nucllei` の親)を明示
  (`test_native_tool_calling.py` の `_parse_iterations_env` が実例。ローカル green を過信しない)。
- **リポジトリ直下に symlink を置かない**(Tailwind/vitest が辿って ELOOP/OOM)。
  マシン固有 symlink は `_links/` 配下(gitignore 済み)。現行 llens 構成の参照は
  `_links/llens` → prism-hu/llens(無ければ `~/src/github.com/prism-hu/llens`)。

## dev サーバ

**2026-07-12 改訂: 本番(docker)と同じ「単一オリジン」構成に統一した。** BE は :3000、
フロントは相対 URL で叩き、vite が `/api`・`/openai`・`/ollama`・`/ws`・`/oauth`・`/health` を BE:3000 へ
proxy する(`vite.config.ts` の `server.proxy`)。旧構成(dev だけ `WEBUI_BASE_URL=http://host:8080`
へクロスオリジン直叩き)は廃止 — 画像等の cookie 認証が dev で不達だった罠(#69 で顕在化)も解消。

```sh
# backend(:3000、hot reload)。uv が .venv を面倒みるので(nucllei はルート直下で cwd から import 可)
# 長い PYTHONPATH 指定は不要。
uv run task dev
# frontend(vite :5173、proxy 込み)。これ単体で BE:3000 に繋がる。
pnpm dev        # = vite dev --host。:5173 使用中なら 5174 等へ自動退避
```

- **起動場所は tmux(2026-07-16 確定、2026-07-17 再々改訂)**: window 構成 = 0:FE(pnpm dev)/
  **1:BE(uv run task dev)** / 2〜:claude・nvim・zsh。**セッション名は固定と思わない**(2026-07-17 夜に
  実害: 旧記載 `owui` 決め打ちで `can't find session`。現況は無名セッション `0`)。BE ペインは
  `tmux list-panes -a -F '#{session_name}:#{window_index}.#{pane_index} #{pane_tty} #{pane_current_command}'`
  で `uv` を探して特定してから send-keys する。再起動は nohup でなく
  `tmux send-keys -t <pane> 'uv run task dev' Enter`(状態確認は `tmux capture-pane -t <pane> -p`)。
  ログはその window に流れる — 500 の一次調査はまずそこの traceback を読む。
  **再起動時の罠(2026-07-17 実害)**: `C-c` 後に履歴 `Up` で再実行しない — シェル履歴が vite 等を
  拾って BE の代わりに別プロセスを起動しうる。コマンドは必ず明示で送る。
- **`.env`(repo 直下、gitignored)を BE が起動時に読む**(env.py の load_dotenv。
  `--reload` は .env を見ないので変更時は BE 再起動)。dev では配備層ブランドの確認用に
  `WEBUI_NAME` / `WEBUI_VERSION` / `WEBUI_COPYRIGHT` を入れてある。
- **バージョン**(2026-07-16、0d69f2da4): 真実源は git tag(リリース `vYYYYMMDD-NN` /
  プレ `pre-NN` の 2 形式だけを describe --match で拾う)。BE は起動時・FE は vite 起動時に解決
  するので、**tag を切ったら BE 再起動 + vite は自動再起動(反映されなければ `touch vite.config.ts`)**。
  命名は APP_* = 本体 Nucllei / WEBUI_* = 配備層。git hash(BUILD_HASH 系)は全面撤去済み。
- **migration のファイル名は日時プレフィックス**(2026-07-18): `alembic.ini` の `file_template` を
  `YYYYMMDDHHMM_<rev>_<slug>` に設定済み — `alembic revision` で自動付与され、`ls` の並び = 適用順。
  既存分も改名済み(revision id は中身に持つのでファイル名変更は既存 DB に無影響)。
- **migration は出荷前 baseline 1 本(2026-07-17、#80)**: 履歴 replay(peewee 18 + alembic 27)を
  squash 済み。**squash 以前に作られた既存 dev DB は起動時に
  `Can't locate revision identified by '...'` を吐く**(起動は続くが以後 migration が当たらない)。
  復旧(2026-07-28 改訂、#94 で実発症): baseline の上に revision が積まれて以降は
  `stamp --purge head` だと**新 revision を適用せず適用済み扱いにしてしまう**ので、
  ① `cd nucllei && uv run alembic stamp --purge c33997f844fc`(baseline へ張り替え — squash 前 DB は
  schema 同一なのでデータ温存)→ ② `uv run alembic upgrade head`(以降の revision を実適用)の 2 段で行う。
  ※ repo root 以外から叩くときは `DATABASE_URL=sqlite:////…/data/webui.db` を絶対パスで明示すると安全。
  作り直すなら `rm data/webui.db` → 再起動 → `create-admin` + fixture 再投入。
  症状の例(#94): file 一覧が全 500(`deleted_at` 列不在)= 「ファイルが全く見えない」。
  新テーブル追加時は `migrations/env.py` のモデル import 一覧にも 1 行足すこと(autogenerate の対象)。
- **依存ライセンス一覧**: 依存を増減したら `uv run task licenses` で `static/licenses.json` を
  再生成してコミット(License タブが fetch して表示。pnpm store と .venv から生成、ネット不要)。
- **BE origin の上書き**: `BACKEND_ORIGIN=http://別ホスト:3000 pnpm dev` で dev フロントを
  別マシンの BE に向けられる(既定 `http://localhost:3000`。`vite.config.ts`)。
- **tailscale 越し**: `pnpm dev` は `--host` で 0.0.0.0 listen。`{frontend-host}:5173/api` が
  proxy 経由で BE:3000 に流れるので、フロントのホスト名 1 つ叩けば API も socket も通る
  (本番 docker の単一パス集約と同じ形)。
- **llens ポート帯(133xx)**: llens が使うポートは `133xx` 予約帯に統一
  (**正典 = prism-hu/llens `DEPLOYMENT.md` のサービス表**。2026-07-13 に旧 :8000/:3000 帯から移行)。
  ローカル dev のコンテナも同じ採番に揃える:
  SGLang `prism-llens:13300` / OWUI 本番 `:13301` / **sandbox `127.0.0.1:13302`** /
  cage `:13303` / **Docling `127.0.0.1:13304`** / AivisSpeech `:13305`。
  BE dev サーバ(:3000)や他プロセスと衝突しない。
- **:3000 が埋まっていたら**: upstream stock docker(`ghcr.io/open-webui/open-webui:main`)が
  squat する場合は `docker stop open-webui`。(旧 llens-sandbox の :3000 squat は 133xx 移行で解消)
- **uvicorn の二重起動に注意**(#66 検証時に実害): `ps aux | grep uvicorn` で :3000 に
  2 プロセス(--reload 有無で別個体)ぶら下がっていることがある。ポートを握っているのが
  旧コードのプロセスだと「直したのに挙動が変わらない」罠になる — 検証前に全部 kill して
  1 本で起動し直すのが確実。
- **BE auto-reload が固まる罠**(2026-07-15 実害): uvicorn --reload は in-flight の
  リクエストが閉じるまで graceful shutdown を待つ。上流(ollama 等)への接続が
  ハングしたまま backend のコードを編集すると「Reloading... のまま :3000 が無応答」に
  見える — C-c で殺して起動し直すのが早い。53d91647e で completion 経路に
  sock_read 上限(既定 300 秒)が入ったので永久には塞がらないが、最長 5 分は待つ。
  **`_links/` の編集による reload 発火は a457f6bd0(2026-07-18)で根治**(`task dev` に
  `--reload-exclude '_links/*'` — 現行 llens 側ファイルを編集しても BE は reload しない)。
  なお ollama が全モデルでロードハングする時は `nvidia-smi` の
  「Driver/library version mismatch」(ドライバ更新後の再起動待ち)をまず疑う。
- vite は dev 用に **全レスポンス no-store**(`vite.config.ts` の `devNoCache` ミドルウェア, `apply:'serve'`)。
  HMR/リロードで古いモジュールを掴む反映漏れを防ぐ(#47 で導入)。
- `config.py` は起動のたび `build/static/**` を BE static(`nucllei/static/`)へ無条件上書きコピーする。
  favicon 等を差し替えたら `static/static/` と `build/static/` も同期しないと reload で巻き戻る。
- `data/webui.db` は pytest の残骸で汚染されうる。おかしければ退避して作り直す。

## dev アカウント・モデル接続(新環境での再現手順)

DB(`data/webui.db`)は clone に含まれないので、新環境では作り直す:

1. **admin 作成**: 公開オンボーディング画面は撤去済み(#62)。CLI で作成する:
   ```sh
   uv run task create-dev-admin    # 慣例 dev admin(ken@llens.local / llens-dev)を一発で作る
   # 任意アカウントはサブコマンド直: uv run nucllei create-admin --email a@llens.local --name A --password llens-dev
   ```
   慣例の dev アカウント: **ken@llens.local / llens-dev**(name: Ken)。
   ※ CLI は console script `nucllei`(2026-07-24 に package 化 — 旧 `uv run task cli` の
   `python -c` 呪文は撤去)。`uv run nucllei --help` で全サブコマンド確認可。
   ※ `POST /api/v1/auths/signup` の curl も `ENABLE_SIGNUP` が有効なら使えるが、
   フロントの初回 signup 導線は無い(CLI が正)。
   **権限検証用の非 admin アカウント(#66 で作成、パスワードは同じ llens-dev)**:
   `editor@llens.local`(group `skill-editors` 所属 = `workspace.skills` 持ち)と
   `plainuser@llens.local`(無所属)。admin の `POST /api/v1/auths/add` で作る。
   3層権限(admin / skill editor / user)の実機確認はこの 3 アカウントで一巡できる。
2. **トークン取得**:
   ```sh
   TOKEN=$(curl -s -X POST http://localhost:3000/api/v1/auths/signin -H 'Content-Type: application/json' \
     -d '{"email":"ken@llens.local","password":"llens-dev"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
   ```
3. **SGLang 接続**(院内ネットワーク: `http://prism-llens:13300/v1`、モデル `kimi-k2.6`、認証不要):
   ```sh
   curl -s -X POST http://localhost:3000/openai/config/update -H "Authorization: Bearer $TOKEN" \
     -H 'Content-Type: application/json' \
     -d '{"ENABLE_OPENAI_API":true,"OPENAI_API_BASE_URLS":["http://prism-llens:13300/v1"],"OPENAI_API_KEYS":["sk-dummy"],"OPENAI_API_CONFIGS":{}}'
   ```
   ローカル ollama に `gpt-oss:20b` もある(SGLang 不達時の代替)。
4. **preset モデル**: 生モデルはピッカーに出ない仕様(535da68e9)。workspace で
   preset **`llens-assistant`**(base=kimi-k2.6、system prompt 任意)を作る。UI からが早い。
5. **dev ツール登録**: `python3 dev/tools/seed.py --token "$TOKEN"` が `render_probe` tool を
   登録し `llens-assistant` の toolIds に追加する(詳細 `dev/tools/README.md`)。
   - `render_probe(kind)`: table/code/math/long/mixed/edge の markdown サンプル
     ※ **spec ビルダは docstring `:param` の継続行を落とす**(2026-07-18 実害: kind の選択肢
     列挙が 2 行目以降にありモデルに見えなかった)。enum 的な選択肢は**必ず 1 行に列挙**する
   - `probe_result_table(department)`: `Table` 戻り
   - `probe_args` / `echo`: 宣言なしコントロール
   - 旧 `run_python` ダミーは撤去済み(#18-3 で本物と同名衝突 — **同名 tool は先勝ちで
     discard される**ので dev でも名前を被せない)
   - このほか dev DB に `get_weather` tool(fixture の call 先)を手で作って toolIds に足しておくと
     モデルフレーム fixture と同じシナリオを UI で再現できる(spec は
     `test_native_tool_calling.py` の weather_spec 参照)。
   **sandbox tool(#18-3)**: 実行系は `dev/tools/sandbox.py` を tool id `sandbox` で
   DB 登録(API 経由。llens 現行 tool の v0.6.5 適合版)。sandbox コンテナ
   (`llens/sandbox:poc`)を **`127.0.0.1:13302->8000`**(133xx 帯の正典採番)で起こし、
   valves は `{"sandbox_url":"http://127.0.0.1:13302"}`(2026-07-13 設定済み)。
   答え合わせは `.venv/bin/python dev/e2e/check_sandbox_tools.py`(E1〜E6)。
   **docling コンテナ**(`llens-docling` = docling-serve v1.17.0)は
   **`127.0.0.1:13304->5001`**、`docling_extract` の valves は `http://localhost:13304`
   (function content は `_links/llens/owui/attachments/docling_extract.py` から同期)。
6. **skill fixture(#35 検証用)**: skill `kansai`(関西弁指示)/ skill `vault`
   (tool_ids=["secret_password"])/ tool `secret_password`(私有)を作り、preset の
   `meta.skillIds` に紐づける。手順と実機チェックは `dev/e2e/README.md`。
   - **#43(2026-07-07)以降**: skill の可読キーは `id` 一本(旧 command は撤去)。作成時に
     `id=kansai` / `id=vault` を直接指定する(`/kansai` 発火 = id 一致)。表示名は `name`(旧 title)。
     #43 の migration(`c5e7a1b9d2f6`)を跨いだ既存 DB では旧 skill が id=uuid のまま残り発火しないので
     **kansai/vault は作り直す**(content は旧 uuid 行に残っているので流用可)。
     - **#43 Phase 2b(2026-07-07)以降**: 作り直しは編集画面で id を uuid→可読 id に**変更して保存**すれば
       裏で recreate される(content 引き継ぎ)。**kansai は対応済み**(uuid→`id=kansai`)。**vault(金庫番)は
       まだ uuid** = 同じ手順で `id=vault` にリネームすればよい。tool/function を同様にリネームする場合は
       description 必須(空だと保存時 toast で弾かれる) — 例: 検証中に `render_probe` へ description を付与済み。
   **注意**: preset の meta を API で書き換えたら `GET /api/models` を叩いて
   `app.state.MODELS` キャッシュを再構築しないと manifest に反映されない
   (UI 経由なら自然に起きる)。
7. **token filter fixture(#57 検証用、2026-07-10)**: `dev/functions/token_meter_lite.py` を
   function id `token_meter_lite` で登録(POST /api/v1/functions/create — type は content の
   `Filter` クラスから自動判定)し、preset の `meta.filterIds` に追加。
   OWUI が永続化した `Chat.meta["usage"]`(#57)を outlet で読んで status 表示するだけの
   filter — usage 捕捉・stream_options 注入・累積 state・context_size valve は一切不要
   (本体が全部やる)。表示例: `🟢 0.4% | 1.2k/256.0k | IN 1.2k / OUT累計 766`。
   llens 現行 token_meter を簡素化する際の参照実装。

## モデルフレーム fixture(SGLang × Kimi 実キャプチャ)

- 置き場: `nucllei/test/fixtures/model_frames/`(7 シナリオ + aborted)。
  観察された仕様は `_memos/model-frames.md`。
- Kimi の tool call id は `functions.<name>:<index>` 形式(OpenAI の call_xxx ではない)。
  引数は厳密 JSON(空引数は `"{}"`)。reasoning は常時出る(`reasoning_content`)。
- 再キャプチャ: `uv run python scripts/capture_model_frames.py [--base-url http://prism-llens:13300/v1]`。

## STT(内蔵 faster-whisper)の実機スモーク(issue #12)

通常スイートでは skip される opt-in テスト(モデル DL を伴うため):

```bash
uv sync --extra local-ml    # faster-whisper + torch 系(dev グループは維持される)
LLENS_STT_SMOKE=1 uv run pytest \
    nucllei/test/apps/webui/routers/test_audio_stt_builtin.py -v
```

- モデルは既定 `tiny`(初回 ~75MB を `WHISPER_MODEL_DIR` へ自動 DL、以後オフライン可)。
- prod 同等(large-v3-turbo CT2)で試すときは `LLENS_WHISPER_MODEL` にローカル CT2
  ディレクトリを指定。prod のモデル取得手順は prism 側 `stt/fetch-model.sh` / `stt/README.md`。
- prod 構成の真実源は `_memos/prod-config-snapshot.md`(int8 / ja 固定 / VAD)。
  テストは同構成をモデルサイズだけ落として踏襲する。

## Playwright での UI 確認

- ログイン(改訂 2026-07-13): #70 B1(a4b52beed)でセッションが **httpOnly cookie に一本化**され、
  旧「signin API → `localStorage.setItem('token', ...)` 注入」は**効かなくなった**(API が 403 →
  /auth へ飛ばされる)。`/auth` の実フォームに fill → Enter でログインする(cookie がコンテキストに乗る)。
  UI を介さず API を直接叩く検証は従来どおり signin のレスポンス token を `Authorization: Bearer` で使える。
- 応答完了待ちは「View Result from」等のテキスト待ち、または `GET /api/v1/chats/{id}` ポーリング。
- ストリーミング描画の検証は `pre.hljs code.language-python` の innerText を数百 ms 間隔で
  サンプリングして単調増加を確認する(#20 で実績)。
- alert/confirm を出す操作は避ける(セッションがブロックされる)。
- **claude-in-chrome の描画が編集前に張り付く**ことがある(shell の `curl localhost:5173/src/...` は新コードなのに
  ブラウザだけ古い。no-store でも新規タブ/別オリジンでも直らない場合あり)。原因を「キャッシュ」で流さず、
  **Playwright のクリーンブラウザ**で検証する(MCP が無いセッションでも `.venv` に
  python-playwright + `~/.cache/ms-playwright` の chromium が入っており、
  `.venv/bin/python` から `sync_playwright` で直接叩ける — #66 の UI 検証で実績):
  フォームログイン → `page.evaluate` で form class やタブ有無を **live DOM から直接読む**。
  SvelteKit の `goto` は SPA 遷移なので `wait_for_url` が 'load' 待ちで空振りすることがある —
  遷移確認は `wait_for_timeout` 後に `page.url` を直接読むほうが確実。レイアウト不具合は `getBoundingClientRect`・`elementFromPoint`・style 差し替えで
  **原因を数字で特定**してから直す(#47: タブバーが `overflow-x-auto` で高さ0に潰れる罠を Playwright で確定)。
- **Playwright のスクリーンショットに native スクロールバーは写らない**(2026-07-20 実害):
  headless の CDP キャプチャはスクロールバー領域を含まず、素のページの既定バーすら写らない —
  「hover で出ない」等の結論をスクショで出してはいけない。検証は computed style / scrollHeight の
  数値で行う。OverlayScrollbars のバーは実 DOM なので普通に写る(こちらはスクショ検証可)。
- **bits-ui(melt)のメニュートリガーは JS の `element.click()` では開かない**(pointerdown を監視しているため
  合成 click イベントが素通り)。実マウスイベントを使う — hover 表示で不可視のボタンは
  `locator.click(force=True)`、要素特定が難しければ座標 `page.mouse.click(x, y)`(#70 共有コンポーネント検証で実績)。
  同様に `text=...` セレクタは不可視の別インスタンス(隠れた UserMenu 等)に先に当たることがある —
  `locator('visible=true')` で絞る。

## 進め方の知見(セッション運用で確立したもの)

- **バックエンド先行**。フロントはバックエンドの構造(スキーマ・wire)確定後にまとめて。
- **意味のない仕様を作らない**: 各要素に「誰がいつ消費するか」を問い、答えが無ければ入れない。
  折衷パス・二重化を嫌い、一本化を強く好む。
- マイナーな UX ノブは**設定 UI でなくコード定数**で持つ(例: `AUTO_EXPAND_EXECUTING_TOOL_CALLS`)。
- モデル出力に関わる変更は実キャプチャ fixture でテストファースト(推測で実装しない)。
  実 SGLang が生きていれば端到端 + Playwright で実測してから出荷。

## その他の運用

- ブランチ・コミット規約・upstream 制約は `CLAUDE.md` と `_memos/upstream-policy.md`(v0.6.6+ のソースを読まない)。
- リリースタグ(2026-07-06 決定、refs #25): **docker image(ghcr)出荷を始めるまでタグは切らない**
  (消費者のいないバージョンは無価値。それまで master は常に開発版)。
  出荷開始後は `prodYYYYMMDD_NN` 形式(technoplasm/app と同形式。prefix は tag 判定の安定化のため。
  app の `a` サフィックスは CI がマイグレーション有無で貼るもので、llens ではマイグレーションは
  実装側の仕事なので採らない)。git tag = ghcr image tag を 1:1 対応、その瞬間の head に打つだけ。
  連番はリポジトリごとに独立(app とのロックステップはしない。組み合わせの同一性は deploy 側が持つ)。
  ※ 旧 `v0.6.5-llens.N` 方式は廃止、`v0.6.5-llens.1` はローカル・origin とも削除済み。
  ※ 2026-07-16 改訂(0d69f2da4): tag 形式はリリース `vYYYYMMDD-NN` / プレ `pre-NN` に変更
  (`prodYYYYMMDD_NN` 案は採らない。現行の真実源は上記「dev サーバ」節の**バージョン**項。
  プレ tag `pre-00` は打鍵済み — 「出荷までタグを切らない」はリリース tag についての方針)。
- GitHub 操作は `gh` CLI(issue 単位で作業、コミットに `refs #N`)。
- 外部ネットワーク必須の機能・依存を増やさない(air-gapped 配備もあるため)。閉域を理由にオプショナルな外部連携は切らない。

## wire v2 以後の dev 知見(2026-07-17)

- **チャット経路の真実源は `_memos/streaming-wire-v2.md`**(socket.io 撤去・SSE 一本化)。
  vite の `/ws` プロキシは撤去済み。
- **dev DB の tool content はリポジトリのリネームに追従しない**: #34 の `open_webui`→`nucllei`
  リネーム後、DB 登録済み `sandbox` tool のモジュールが `No module named 'open_webui'` で
  ロード失敗し、チャット全体が 400 になっていた(2026-07-17 に `dev/tools/sandbox.py` の
  現内容で DB を更新して解消)。**パッケージ構造を動かしたら DB 登録 tool の再同期を忘れない**
  (`POST /api/v1/tools/id/{id}/update` に repo ファイルの content を流す)。
- **実機ストリーミング e2e**: `.venv/bin/python dev/e2e/check_streaming_wire_v2.py`
  (実 SGLang 前提。単調成長・ストリーム中リロード再アタッチ・完走を検証)。
- **dev アカウント(ken@llens.local)は `ctrlEnterToSend: true`**: 素の Enter では送信されない。
  Playwright からの送信は `#send-message-button` クリックが設定非依存で確実。
- リッチテキスト入力は全廃(textarea 一本、`2894654da`)。`#chat-input` は常に textarea。

## i18n の流儀と罠(2026-07-17、1531e574b で実害を踏んで確立)

- **locale JSON(`src/lib/i18n/locales/*/translation.json`)は手で編集して並べ替えない**。
  キーの増減は必ず `pnpm i18n:parse`(i18next-parser + prettier)に出させる。手動 JSON 再ダンプは
  parser と collation が違い、diff が数百行の並び替えノイズになる(実害)。訳の投入だけは
  parse 後のファイルに該当行の値編集で OK。
- **動的キーは `src/lib/i18n/dynamic-keys.js` に登録**(コメント内 t() ヒント集、2026-07-17 全面棚卸し)。
  parser は `keepRemoved: false` なので、`t(variable)` 経由のキーは登録しないと**生きた翻訳ごと prune**
  される(実害: ShortcutsModal 宣言配列化のとき未登録でショートカット 12 キーが消えかけた)。
  **機能撤去・label の動的化・t() の生表示化をしたら dynamic-keys.js を必ず追従**させ、
  `pnpm i18n:parse` して diff の追加/削除キーを全数目視する。
- **parser の input は `src/**/*.{js,svelte}` — `.ts` を含まない**。.ts 内の静的 t('...')
  (例: tts.svelte.ts)も dynamic-keys.js に載せないと prune される。

## セッション追補(2026-07-22、prism sync / preset / sandbox 再ビルド)

dev 実機(:3000)に prism ツールを載せて検証したときの罠と手順。真実源はコード + issue #88/#89/#42。

- **preset は `llens-thinking` に改名済み**(旧 `llens-assistant`)。prism `nuc-sync` の prompt 同期は
  「ファイル名 stem = model id」で当たるため、`nucllei/prompts/llens-thinking.md` は model
  `llens-thinking` にだけ届く。改名前は harness 管理外で system prompt がドリフトした
  (`exports` に直したのに preset は旧 `outputs` のまま → モデルが `/workspace/outputs` に保存する実害)。
  **preset は必ず prompt ファイル stem に一致させる**。
- **preset の toolIds は生きた id のみ**。dead id を残すと "tool not available" になる
  (実害: `feed_image` 未登録 + `view_image` dead で feed_image が呼べず、モデルが画像内容を捏造)。
  現行の正: `view_image`→`feed_image`、`doc`→`doc_tool` に差し替え済み。
- **改名・撤去された custom tool は手削除**。`nuc-sync` は custom を prune しないので、旧
  `view_image` / `doc` の DB 行が残る。builtin の prune(#42)は `builtin_` prefix 専用で custom は対象外。
  admin API で `DELETE /api/v1/tools/id/{id}/delete`(builtin でなければ通る)。
- **prism `nuc-sync` の向き先 = `NUCLLEI_ORIGIN`**(llens `.env`、`005df9e`)。dev は
  `NUCLLEI_ORIGIN=http://localhost:3000`。admin API Key は llens `.env` の `NUCLLEI_API_KEY` に置く
  (この開発機だけの単一 .env 管理。`.env` は gitignore 済み)。dev key は
  `POST /api/v1/auths/signin`(ken@llens.local / llens-dev)→ `GET /api/v1/auths/api_key` で取得。
- **sandbox は imports/exports 命名で再ビルド要**(旧 inputs/outputs コンテナだと tool と食い違う)。
  compose 補間が `nucllei` サービスの `NUCLLEI_DB_PASSWORD` を要求するので、sandbox 単体は
  `docker build -t llens/sandbox:poc ./sandbox` → `NUCLLEI_DB_PASSWORD=x docker compose up -d --no-deps --force-recreate sandbox`。
  `/exec` 応答キーが `outputs`→`exports` に変わったのを確認(スモーク: exports/ に書いて応答 keys を見る)。
