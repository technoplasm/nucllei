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
- **backend**: Python は uv(リポジトリ直下 `.venv`)。`uv sync` 相当で venv を作る。
  テストは `cd backend && ../.venv/bin/pytest open_webui/test -q`(または repo ルートで `uv run pytest`)。
  **CI に backend の black 整形ゲート("Python CI / Format Backend")あり** — 手編集や sed で行を消すと
  black 的に崩れて CI が落ちる。**backend を触ったら push 前に `cd backend && ../.venv/bin/black open_webui/`**
  (Backend Tests(pytest)は別ジョブで pass しても Format で落ちる)。frontend にも同型の
  prettier ゲート("Frontend Build / Format & Build")があり、こちらは `pnpm format`。
- **Svelte 5(互換モード)の 2 大モーダル罠**(#55 で実害・修理済みだが新規コードで再発しやすい):
  (1) `$: if (show) { _user = {...} }` の編集バッファ初期化は **bind:value の書き込みごとに
  再実行され入力が巻き戻る** — `let lastShow` の変化検知ガードで包む(UserModal.svelte が実例)。
  (2) portal(body へ appendChild)する component の無条件 `document.body.removeChild` は
  `{#if}` teardown と衝突して NotFoundError → **effect が中断され画面が中途半端に壊れる**
  (症状: モーダルが空欄)。`parentNode === document.body` を確認してから外す(common/Modal.svelte
  は修理済み — 新しい portal を書くときは同じガードを)。
- **vitest は import 破壊を検知しない**: store やコンポーネントを削除する撤去作業では、
  カバレッジ外のファイルに残った orphan import を vitest がすり抜ける(#67 で
  common/Tags/TagInput の削除済み store import が CI の build 段階まで到達)。
  **大きめの撤去をしたら push 前に `pnpm build` を一度回す**(svelte-check でも可だが build が確実)。
- **pre-push hook(2026-07-10 導入)**: 上記 2 ゲートを push 前に手元で再現する
  `dev/hooks/pre-push`(push 差分のファイルだけチェックするので数秒)。hook 本体は
  リポジトリに入っているが、**有効化は clone ごとに 1 回 `git config core.hooksPath dev/hooks`
  が必要**(git はセキュリティ上 hook を自動有効化しない — 新環境セットアップ時に忘れずに)。
  引っかかると push が中断される。緊急回避は `git push --no-verify`。
- `.git/index.lock` が時々出るのは shell prompt の `gitstatusd` が握るため — stale なら `rm -f .git/index.lock`
  して commit を再試行(走っている git プロセスは無い)。
- **pytest の postgres**: `backend/open_webui/test/conftest.py` が docker で自動起動しセッション共有。
  壊れたら `docker rm -f postgres-test-container-will-get-deleted` で確実に fresh になる。
- **権限のネガティブテストは `mock_webui_user_strict` を使う**(test/util/mock_user.py、#55 で追加):
  従来の `mock_webui_user` は `get_admin_user` ごと差し替えるため **role="user" でも admin endpoint を
  素通りする**(401 を期待するテストが書けない)。strict 版は `get_current_user` だけを差し替えて
  role ゲートを実物で通す。#66(権限3層化)の回帰はこちらで書くこと。
- open_webui は **import 時に DATABASE_URL などの env が確定**する(migration も import 時)。
  import 時確定 env のテストはサブプロセス起動 + `cwd`=backend + `PYTHONPATH`=backend を明示
  (`test_native_tool_calling.py` の `_parse_iterations_env` が実例。ローカル green を過信しない)。
- **リポジトリ直下に symlink を置かない**(Tailwind/vitest が辿って ELOOP/OOM)。
  マシン固有 symlink は `_links/` 配下(gitignore 済み)。現行 llens 構成の参照は
  `_links/llens` → prism-hu/llens(無ければ `~/src/github.com/prism-hu/llens`)。

## dev サーバ

**2026-07-12 改訂: 本番(docker)と同じ「単一オリジン」構成に統一した。** BE は :3000、
フロントは相対 URL で叩き、vite が `/api`・`/openai`・`/ollama`・`/ws`・`/oauth` を BE:3000 へ
proxy する(`vite.config.ts` の `server.proxy`)。旧構成(dev だけ `WEBUI_BASE_URL=http://host:8080`
へクロスオリジン直叩き)は廃止 — 画像等の cookie 認証が dev で不達だった罠(#69 で顕在化)も解消。

```sh
# backend(:3000、hot reload)。uv が .venv と PYTHONPATH(--app-dir backend)を面倒みるので
# 長い PYTHONPATH=backend .venv/bin/uvicorn … は不要になった。
uv run task dev
# frontend(vite :5173、proxy 込み)。これ単体で BE:3000 に繋がる。
pnpm dev        # = uv run task dev-web。:5173 使用中なら 5174 等へ自動退避
```

- **BE origin の上書き**: `BACKEND_ORIGIN=http://別ホスト:3000 pnpm dev` で dev フロントを
  別マシンの BE に向けられる(既定 `http://localhost:3000`。`vite.config.ts`)。
- **tailscale 越し**: `pnpm dev` は `--host` で 0.0.0.0 listen。`{frontend-host}:5173/api` が
  proxy 経由で BE:3000 に流れるので、フロントのホスト名 1 つ叩けば API も socket も通る
  (本番 docker の単一パス集約と同じ形)。
- **:3000 が埋まっていたら**: llens-sandbox コンテナ(`127.0.0.1:3000->8000`、#18-3 の tool 検証用)が
  squat していることがある。`docker stop llens-sandbox` で空ける(可逆)。sandbox と BE を同時に
  使うなら sandbox 側を別ポートへ退避するか BACKEND_ORIGIN で BE を逃がす。upstream stock docker
  (`ghcr.io/open-webui/open-webui:main`)が squat する場合は `docker stop open-webui`。
- **uvicorn の二重起動に注意**(#66 検証時に実害): `ps aux | grep uvicorn` で :3000 に
  2 プロセス(--reload 有無で別個体)ぶら下がっていることがある。ポートを握っているのが
  旧コードのプロセスだと「直したのに挙動が変わらない」罠になる — 検証前に全部 kill して
  1 本で起動し直すのが確実。
- vite は dev 用に **全レスポンス no-store**(`vite.config.ts` の `devNoCache` ミドルウェア, `apply:'serve'`)。
  HMR/リロードで古いモジュールを掴む反映漏れを防ぐ(#47 で導入)。
- `config.py` は起動のたび `build/static/**` を backend static へ無条件上書きコピーする。
  favicon 等を差し替えたら `static/static/` と `build/static/` も同期しないと reload で巻き戻る。
- `backend/data/webui.db` は pytest の残骸で汚染されうる。おかしければ退避して作り直す。

## dev アカウント・モデル接続(新環境での再現手順)

DB(`backend/data/webui.db`)は clone に含まれないので、新環境では作り直す:

1. **admin 作成**: 公開オンボーディング画面は撤去済み(#62)。CLI で作成する:
   ```sh
   PYTHONPATH=backend .venv/bin/open-webui create-admin \
     --email ken@llens.local --name Ken --password llens-dev
   ```
   慣例の dev アカウント: **ken@llens.local / llens-dev**(name: Ken)。
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
   - `probe_result_table(department)`: `Table` 戻り
   - `probe_args` / `echo`: 宣言なしコントロール
   - 旧 `run_python` ダミーは撤去済み(#18-3 で本物と同名衝突 — **同名 tool は先勝ちで
     discard される**ので dev でも名前を被せない)
   - このほか dev DB に `get_weather` tool(fixture の call 先)を手で作って toolIds に足しておくと
     モデルフレーム fixture と同じシナリオを UI で再現できる(spec は
     `test_native_tool_calling.py` の weather_spec 参照)。
   **sandbox tool(#18-3)**: 実行系は `dev/tools/sandbox.py` を tool id `sandbox` で
   DB 登録(API 経由。llens 現行 tool の v0.6.5 適合版)。sandbox コンテナ
   (`llens/sandbox:poc`、従来 `127.0.0.1:3000->8000`)を起こし、valves の `sandbox_url` を
   そのポートにして toolIds へ追加。答え合わせは `.venv/bin/python dev/e2e/check_sandbox_tools.py`(E1〜E6)。
   - **⚠ 2026-07-12 以降 :3000 は BE dev サーバが使う**。sandbox は別ホストポート
     (例 `127.0.0.1:3001->8000` + valves `{"sandbox_url":"http://127.0.0.1:3001"}`)へ退避すること
     (両方 :3000 だと bind 衝突)。または BE を `BACKEND_ORIGIN` で逃がす。
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

- 置き場: `backend/open_webui/test/fixtures/model_frames/`(7 シナリオ + aborted)。
  観察された仕様は `_memos/model-frames.md`。
- Kimi の tool call id は `functions.<name>:<index>` 形式(OpenAI の call_xxx ではない)。
  引数は厳密 JSON(空引数は `"{}"`)。reasoning は常時出る(`reasoning_content`)。
- 再キャプチャ: `uv run python scripts/capture_model_frames.py [--base-url http://prism-llens:13300/v1]`。

## Playwright での UI 確認

- ログインはフォームでなく token 注入が速い: signin API で token を取り
  `localStorage.setItem('token', ...)` → `location.href='/'`。
- 応答完了待ちは「View Result from」等のテキスト待ち、または `GET /api/v1/chats/{id}` ポーリング。
- ストリーミング描画の検証は `pre.hljs code.language-python` の innerText を数百 ms 間隔で
  サンプリングして単調増加を確認する(#20 で実績)。
- alert/confirm を出す操作は避ける(セッションがブロックされる)。
- **claude-in-chrome の描画が編集前に張り付く**ことがある(shell の `curl localhost:5173/src/...` は新コードなのに
  ブラウザだけ古い。no-store でも新規タブ/別オリジンでも直らない場合あり)。原因を「キャッシュ」で流さず、
  **Playwright のクリーンブラウザ**で検証する(MCP が無いセッションでも `.venv` に
  python-playwright + `~/.cache/ms-playwright` の chromium が入っており、
  `.venv/bin/python` から `sync_playwright` で直接叩ける — #66 の UI 検証で実績):
  token 注入 → `page.evaluate` で form class やタブ有無を **live DOM から直接読む**。
  SvelteKit の `goto` は SPA 遷移なので `wait_for_url` が 'load' 待ちで空振りすることがある —
  遷移確認は `wait_for_timeout` 後に `page.url` を直接読むほうが確実。レイアウト不具合は `getBoundingClientRect`・`elementFromPoint`・style 差し替えで
  **原因を数字で特定**してから直す(#47: タブバーが `overflow-x-auto` で高さ0に潰れる罠を Playwright で確定)。

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
- GitHub 操作は `gh` CLI(issue 単位で作業、コミットに `refs #N`)。
- air-gapped 前提: 外部ネットワーク必須の機能・依存を増やさない。
