# sandbox — サーバーサイドコード実行基盤

Pyodide (ブラウザ WASM) のコード実行を置き換える、nsjail ベースのサーバーサイド実行環境。
このファイルが設計・API の単一情報源。

**source は nucllei 製品の部品としてこの repo が持ち**、CI がリリースタグ push で
`ghcr.io/technoplasm/nucllei-sandbox:<tag>` を publish する
(`.github/workflows/sandbox-image.yml`)。**配備 (compose のサービス定義・env の実値・
ホスト公開ポート・閉域搬入・workspace の GC 運用) は配備層の `prism-hu/llens` が持つ** —
この repo は「image とその中身の設計」だけを持ち、何番で上げるか・quota を何 GB にするかは
配備側の決定。以下で llens の文書 (DEPLOYMENT.md / RELEASE.md / docs/airgap.md) を指すのは
そのため。

## この配下 (`sandbox/`)

| パス | 中身 |
|---|---|
| `sandbox/Dockerfile` | イメージ (nsjail ソースビルド + 2 つの venv + シェルツール + 日本語フォント) |
| `sandbox/app/main.py` | service (FastAPI)。実行ごとに nsjail を起動、workspace / quota / session を管理 |
| `sandbox/app/kernel.py` | stateful `run_python` の常駐 kernel (永続 namespace, SIGALRM でセル中断) |
| `sandbox/requirements.txt` | jail 内ユーザーコード用 venv のパッケージ (単一情報源。**未 pin**) |
| `sandbox/matplotlibrc` | jail 内 matplotlib の既定 (日本語フォント) |
| `sandbox/tests/` | 隔離の回帰テスト (`uv run task sandbox-test`。下記「隔離テスト」) |
| `sandbox/README.md` | これ (設計・API・セキュリティの単一情報源) |
| `sandbox/STATUS.md` | 実装状況・残課題 (作業管理の台帳) |

compose のサービス定義 (`sandbox`) と env の実値は配備層 (`prism-hu/llens` の
`docker-compose.yml`) にある。UI 側の tool は組み込みツール `nucllei/builtin_tools/sandbox.py`
(搭載形態の経緯は `_memos/builtin-tools.md`)。

## 背景 / 目的

- Pyodide はブラウザ実行のため遅く、複雑・長尺の処理でタイムアウトやセッション破損
  (リロードでも復帰しない) が起きる。
- micropip の deps=False 儀式・harvest の JS FFI・セッション保護指示など、
  Pyodide 時代の system prompt の大半は Pyodide の制約への workaround であり、
  サーバー実行に移すと丸ごと不要になる (プロンプト = context の大幅削減)。
- サーバー実行にすると「Python で作ってシェルで加工」のような複合操作も可能になる。

## 構成

```
nucllei (nucllei/builtin_tools/sandbox.py — 組み込み FC tool)
  │  HTTP。compose 内は sandbox:8000。加えて host 127.0.0.1:13302 に公開 (dev 用、外部不可。
  │  ホスト側の採番は配備層の決定 — llens の DEPLOYMENT.md)
  ▼
sandbox サービス (この repo の sandbox/ = ghcr.io/technoplasm/nucllei-sandbox, privileged)
  │  実行 1 回ごとに nsjail プロセスを起動
  ▼
nsjail jail
  - rootfs: コンテナ内の /usr /bin /etc /opt/venv 等を read-only bind
  - /: 16MB tmpfs (マウント点を載せる土台。書込には使わない。既定だと df に
    RAM の半分が出て誤解を招くので明示的に小さく固定)
  - /dev: null/zero/full/random/urandom のみ bind
  - /workspace: ユーザー単位の loopback ext4 を rw bind (下記 quota 参照)
  - /tmp: 256MB tmpfs (RAM-backed・実行ごとに消える。実行中の一時ファイル用。
    跨いで残すなら /workspace へ)
  - network namespace: 空 (loopback 含め通信不可)
```

- 各ユーザーの `/workspace` は `/workspaces/.images/<nucllei_user_id>.img` という
  **sparse な loopback ext4** をマウントしたもの。見かけ上の容量は quota GB だが、
  ホストの実消費は書き込んだ分だけ (下記 disk quota)。

- 実行環境 (Python venv / シェルツール / フォント) はすべてイメージに焼き込む。
  閉域後の追加インストールは無い前提で、閉域前にリストを確定する。
- UI とのファイル入出力は tool (UI プロセス側) が bytes を持ち運ぶ。
  sandbox サービスは UI に一切アクセスしない (信頼方向は UI → sandbox の一方通行)。

## セキュリティモデル

病院イントラ設置なので、守るべきは (1) ユーザーコードからイントラ他ホストへの到達
(SSRF)、(2) 権限昇格、(3) 他ユーザー領域・ホストファイルの読み出し。

- **ネットワーク遮断が本丸**: Pyodide はブラウザの same-origin で通信先が事実上 UI に
  限定されていた。サーバー実行での最悪シナリオは「ユーザーコードが電子カルテ系ホストに
  到達する」こと。jail は network namespace を空にして構造的に潰す
  (nsjail デフォルトの clone_newnet。loopback すら無し)。
- **薄い壁と本壁**: sandbox コンテナ自体は `privileged`。理由は (1) nsjail が
  namespace/mount を作る (2) quota 用の loopback ext4 をマウントする、の 2 つ。
  つまり「コンテナ境界は薄い壁、nsjail が本壁」という整理。ユーザーコードは
  コンテナ名前空間では一切動かず (必ず jail 内)、privileged が意味を持つのは
  nsjail 脱出に成功した後だけ。sandbox コンテナ内で jail 外実行は絶対にしない。
- **ユーザー間の隔離は mount ns**: 実行時に当該ユーザーの workspace だけを bind する
  ため、他ユーザーの領域は jail 内に存在しない (見えない・触れない)。
  jail 内 uid は全ユーザー共通の 65534。
  - 残懸念 (受容済み): nsjail 脱出ができた場合、同一 uid のため他ユーザーの workspace
    が DAC では守られない。対策としては per-user uid オフセット割当があるが、
    脱出 + α の多段前提であり、利用者が院内医師に限られるため PoC では見送り。
- **rootfs read-only**: 書けるのは /workspace と /tmp (tmpfs) のみ。
  rlimit (wall/CPU/メモリ/ファイルサイズ/プロセス数) で資源を制限。
  stdout/stderr はパイプなので rlimit_fsize が効かない → サービス側で
  上限バイトまで読んで以降は捨てる (メモリ膨張防止)。
- **監査ログ**: sandbox 側では取らない。tool の入出力 (= 実行コードと結果) は
  chat 履歴に完全に残り、そちらを保全する運用のため二重化しない。
- **API 認証**: PoC では無し。compose 内 (`sandbox:8000`) に加え、dev 用に host
  `127.0.0.1:13302` へ公開する (フォークした UI をホスト側で動かして叩くため)。
  127.0.0.1 bind なので HINES/外部からは不可・UFW 許可も無しだが、**ホスト上の
  ローカルプロセスからは無認証で叩ける**点は認識しておく。本番前に shared token を
  検討 (残課題)。ホスト公開ポート採番は配備層 (llens の DEPLOYMENT.md のサービス表) が
  単一情報源で、この repo は番号を決めない。

## 実行モデル

2 系統ある。どちらも `/workspace` (per-user 永続ディスク) を共有する。

- **stateful (`run_python` = `/exec_cell`)**: (user_id, chat_id) ごとに常駐 kernel
  プロセス (`kernel.py`) を 1 本持ち、永続 namespace でセルを実行する。変数・import・
  関数定義が会話内で保持される (notebook のセル相当)。Pyodide の持続性を、あちらの
  「セッションが静かに壊れる」脆さ抜きで再現する。
  - **session キー = UI の `__chat_id__`**。新しい session 概念や KVS は不要
    (namespace はプロセスメモリに載る。in-memory registry = SessionManager)。
  - **per-cell timeout は kernel 内の SIGALRM で「セルだけ中断」**。外部シグナルは
    nsjail に吸われ jail ごと落ちるため使わない。中断してもプロセスは生存し namespace
    は保たれる。中断不能な C 演算だけサービスが grace 後に kill (session リセット)。
  - **最後の式は repr 表示** (ipython 不要。AST で最終式を eval)。
  - **回収**: kernel は nsjail `--time_limit=SESSION_TTL_S` (既定 12h) で起動するので
    **セッション開始から** 12h で活動中でも落ちる (idle 回収も同じ TTL で reaper が
    行うが last_used ≥ 生成時刻なので実効は絶対上限側)。以降の実行は fresh namespace。**同時 session 数は `SESSION_MAX` (既定 64) で上限**、
    超過時は最も idle な session を 1 本落とす。per-session の `rlimit_as` が単発の
    メモリ暴走を止める (3 層で RAM を抑える)。
  - crash / kill / TTL 満了 / SESSION_MAX 追い出しで作り直したときは戻り値に
    session_reset を返す (reaper が消した (user, chat) は tombstone に残し、次の
    `get` で「前世代あり」と判定する。`sandbox/tests/test_session_manager.py`)。
- **stateless (`run_python_once` / `run_shell` = `/exec`)**: 1 実行 = 1 プロセス。
  変数は残らない。会話状態から独立した単発処理・シェル用。
- **workspace が永続状態**: `/workspace` はユーザー単位で永続 (stateful/stateless 共有)。
  - `/workspace/imports/` — チャット添付ファイルの取り込み先 (tool の import 操作)
  - `/workspace/exports/` — 書かれた新規/更新ファイルは実行後に tool が自動回収し、
    Files に登録してチャットに添付する
- 直列化は 2 系統で独立: `/exec_cell` は (user, chat) の session lock、`/exec` は user lock。
  run_python と run_shell、別 chat の run_python 同士は並列に走る。全体の同時実行数は
  semaphore (`SANDBOX_MAX_CONCURRENCY`) で制限。lock 待ちは tool 側 HTTP timeout
  (timeout_s+30) を超えると client が切れて ERROR になる。サーバ側は lock 取得後に
  `request.is_disconnected()` を見て、切れていた要求は実行せず捨てる (499) — 放置すると
  結果の返らない実行が走り、モデルの再試行と合わせて同じコードが 2 回動く。

## API (sandbox サービス、port 8000)

- `POST /exec` — stateless。`{user_id, lang: "python"|"shell", code, timeout_s?, imports?: [{name, b64}]}`
  → `{exit_code, timed_out, duration_s, stdout, stderr, stdout_truncated, stderr_truncated,
     exports: [{name, size, b64?, truncated}]}`
- `POST /exec_cell` — stateful。`{user_id, chat_id, code, timeout_s?}`
  → `{stdout, error, timed_out, session_reset, duration_s, stdout_truncated,
     exports: [...]}`
  - `imports` は実行前に `/workspace/imports/` へ書かれる (basename のみ有効)
  - `exports` は `/workspace/exports/` の実行前後差分 (新規 + 更新)
- `POST /files/list` — `{user_id, path?}` → workspace のファイル一覧
- `POST /files/read` — `{user_id, path}` → `{name, size, b64}`
- `POST /files/write` — `{user_id, path, b64}` → 書き込み (workspace 外へのパスは拒否)
- `GET /healthz` — `{ok, nsjail, loop}`。`loop` は loop ドライバの実効チェック
  (`/dev/loop-control` を open して確かめる)。**false なら 503** を返し、compose の
  healthcheck 経由でコンテナが unhealthy になる(`docker ps` で見える)。loop 不能 =
  全 workspace mount が失敗する hard fault のため(典型例と切り分けは
  [docs/health.md](docs/health.md) ⑤)。

## disk quota (ユーザー単位)

各 `/workspace` は sparse loopback ext4 なので、次の 3 つが同時に成立する:

- **見せ方**: jail 内の `df` は quota GB (既定 10GB) を返す。1.8TB は見えない
  → 「なんでもできる」誤認を防ぐ。
- **thin provisioning**: 見かけ 10GB でもホスト実消費は書き込んだ分だけ
  (mkfs 直後は約 4MB)。10GB を予約消費しない。
- **hard enforcement**: 書き込みが quota を超えると fs 層で `No space left on
  device`。ホストの実消費もイメージサイズ (quota) で頭打ちになり、1 ユーザーが
  ホスト disk を食い潰せない。`mount -o discard` によりファイル削除でホスト側の
  実消費も解放される。

- サイズ変更: `SANDBOX_QUOTA_GB` (全体既定) と `SANDBOX_QUOTA_OVERRIDES`
  (`{"<nucllei_user_id>": 20}` 形式で個別上書き)。**上書きはイメージ新規作成時のみ
  効く** (既存イメージのリサイズは resize2fs が要る。PoC では未対応)。
- loopback の実装制約: コンテナには udev が無いため、起動時に `/dev/loop0..N` を
  先に mknod する (`SANDBOX_LOOP_NODES`, 既定 256 = 同時に quota を持てるユーザー数の
  上限)。loop デバイスはカーネル全体で共有 (ホストの snapd 等と番号を共有する)。
- イメージは volume に永続。コンテナ再起動時は e2fsck して再マウントする
  (journal 無し fs のため)。

## リソース制限 (変更場所: 配備側 compose の environment)

既定値はこの表 (= `app/main.py`) が持ち、環境ごとの実値は配備層 (llens の
`docker-compose.yml`) が上書きする。両方に literal を書くとどちらが効いているか
読めなくなるので、**この repo は既定だけを持つ**。

| env | 既定 | 意味 |
|---|---|---|
| `SANDBOX_TIMEOUT_DEFAULT_S` | 120 | wall clock 既定 (tool から実行ごとに指定可) |
| `SANDBOX_TIMEOUT_MAX_S` | 600 | wall clock 上限 (指定値はこれで clamp) |
| `SANDBOX_MEM_MB` | 4096 | rlimit_as |
| `SANDBOX_FSIZE_MB` | 8192 | rlimit_fsize (1 ファイルの書込上限。disk 総量は quota 側が本来の上限) |
| `SANDBOX_QUOTA_GB` | 10 | ユーザー単位の workspace 容量上限 (loopback ext4) |
| `SANDBOX_QUOTA_OVERRIDES` | {} | ユーザー別 quota 上書き `{"<user_id>": GB}` (新規作成時のみ) |
| `SANDBOX_LOOP_NODES` | 256 | 先に mknod する loop ノード数 = 同時 quota ユーザー上限 |
| `SANDBOX_MAX_CONCURRENCY` | 4 | 全体の同時実行数 (SGLang と CPU を食い合わないように) |
| `SANDBOX_STREAM_LIMIT_BYTES` | 524288 | stdout/stderr それぞれの保持上限 |
| `SANDBOX_FILE_LIMIT_BYTES` | 26214400 | tool の成果物回収 (fetch_from_workspace / exports 自動添付) の 1 ファイル上限。UI のユーザーアップロード制限とは独立。compose では 100MiB に引き上げ済。read は base64 で JSON に載る (RAM ~2.3x) ため真の無制限にはしない (GB 級は raw ストリーム化が要る) |
| `SANDBOX_SESSION_TTL_S` | 43200 (12h) | stateful session の生存上限 (kernel の nsjail time_limit。idle 回収も同値) |
| `SANDBOX_SESSION_MAX` | 64 | 常駐 session 数の上限 (超過で最も idle を回収) |
| `SANDBOX_SESSION_GRACE_S` | 5 | 中断不能セルを kill するまでの猶予 (kernel の SIGALRM 後) |

## UI tool (nucllei/builtin_tools/sandbox.py)

| メソッド | 役割 |
|---|---|
| `run_python(code, timeout_s?)` | **stateful**。会話ごとの常駐 kernel で永続 namespace 実行 (`/exec_cell`)。exports/ 自動添付 |
| `run_python_once(code, timeout_s?)` | stateless 使い捨て Python (`/exec`)。状態非依存の単発用 |
| `run_shell(command, timeout_s?)` | stateless bash 実行 (coreutils, jq, ripgrep, pandoc, weasyprint, unzip/zip, nkf, file, poppler-utils, ffmpeg)。exports/ 自動添付 |
| `send_to_workspace(filename?, file_id?)` | チャット添付を `/workspace/imports/` に取り込む |
| `write_file(path, content)` | モデルが書いたテキストを workspace に直接書く (`/files/write`)。テキスト専用・上書き・既定 1MB 上限 (`SANDBOX_WRITE_FILE_LIMIT_BYTES`) |
| `list_workspace(path?)` | workspace のファイル一覧 |
| `fetch_from_workspace(path)` | workspace の任意ファイルを Files に登録しチャットに添付 |

- `write_file` は本文を function calling の引数として受け取るので、`run_python` の
  文字列や `run_shell` のヒアドキュメントを経由しない。裸の HTML/CSV/テンプレートを
  書かせるときのクォート事故 (本文中の `"""` や `EOF` 行で壊れる) を構造的に潰すのが目的で、
  利便性より failure mode の削減が主眼。**`exports/` に書いても自動添付はされない**
  (添付は実行の後始末で回収する仕組みのため) — tool の戻り値でその旨を返し
  `fetch_from_workspace` に誘導する。
- 実行中の経過秒数は nucllei が tool のブロックに自動表示する (llens #100)。tool 側から
  秒を流さない — 起点も粒度も違う時計が 2 つ並ぶ。段階のある処理だけ
  `__progress__("何をしているか")` で中身を伝える (LLM には見えない、UI のみ)。
- 画像・成果物の基本経路は `/workspace/exports/` 保存 → tool が file_id 発行して
  自動添付。画像・音声・動画は display チャネル (#88) で tool 枠下に自動表示され、
  本文への貼り直しは prompt で禁止 (例外: background 完了通知で届いた成果物は自動表示に
  乗らないので本文にリンク/`![]()` を書く)。

## Python 環境 / シェルツール

- venv (`/opt/venv`): 現行 Pyodide 環境と同等のセット + 解析系 (statsmodels 等)。
  一覧は `sandbox/requirements.txt` が単一情報源。**未 pin (搬入前に固定)**。
- BLAS/OMP スレッドは `SANDBOX_BLAS_THREADS` (既定 4) で固定。64 コア機で既定の
  ままだと OpenBLAS が rlimit_as を超えて失敗し、かつ SGLang と CPU を食い合うため。
  joblib は multiprocessing 無効 (serial)。
- シェル: bash + coreutils + jq + ripgrep + pandoc + ffmpeg/ffprobe (音声・動画処理)。
  音声は venv 側の pydub / soundfile (libsndfile) と併用。
- **HTML/CSS → PDF は weasyprint** (venv、apt 側に `libpango-1.0-0` / `libpangoft2-1.0-0`)。
  `pandoc --pdf-engine=weasyprint` で md → PDF にもなるので、LaTeX を入れずに済む。
  Chromium を入れる案もあったが見送った: jail に `/dev/shm` が無く (`DEV_BINDS` 参照)
  `--disable-dev-shm-usage` で 256MB の `/tmp` に逃がす必要があり、`rlimit_as` 4096MB を
  ユーザーコードと食い合い、Chromium 自身の sandbox が user ns を要求するため
  `--no-sandbox` 必須になる。数百 MB のイメージ増と引き換えに得られるのは JS 実行だけで、
  静的な帳票には weasyprint で足りる。**mermaid や plotly を静止画にする要求が出たら再考する**。
  weasyprint はフォントをサブセット埋め込みするので、出力を後から画像化・印刷しても
  字が落ちない (非埋め込み PDF でよくある欠落を作らない)。
- 日本語フォント: `fonts-noto-cjk` を同梱。matplotlib は `sandbox/matplotlibrc`
  (JAIL_ENV の `MATPLOTLIBRC` で注入) が sans-serif / monospace / serif 各ファミリの
  先頭を Noto CJK JP 系にするため、rcParams 設定なしで日本語が出る。prompt 指示
  (rcParams を書け) 頼みだとモデルが忘れて DejaVu 系の glyph missing 警告が出ていた
  ための焼き込み (Pyodide 時代のフォント fetch helper も不要)。sans-serif しか
  向けていなかった頃は monospace 指定の作図で DejaVu Sans Mono に落ちて同警告が出た。
- nsjail は apt パッケージが無いため multi-stage build でソースビルド
  (commit/tag pin)。コンパイルは builder レイヤにキャッシュされるので
  イメージ再ビルドのたびに走ることはない。

## Pyodide からの移行

移行完了 (2026-07)。本 tool がコード実行の本線で、ブラウザ実行の Code Interpreter (Pyodide) は
使わない。nucllei 側では Pyodide 実行系をコード・依存・ビルドステップごと削除済み (#11)、
code_interpreter も機能ごと撤去済み (#23) で、**tool 経路以外のコード実行 (タグ検出等の裏道) は
作らない**のが CLAUDE.md の絶対制約。

利用指針は system prompt (llens の `nucllei/prompts/llens-thinking.md`「サンドボックス」節 —
prompt は配備側が持つ) に集約し、tool 側は Tools クラス docstring と各メソッドの schema 説明を持つ。

残: requirements.txt / apt ツールの version pin (閉域前)。

## 隔離テスト

`sandbox/tests/test_isolation.py` (`uv run task sandbox-test`)。稼働中の privileged コンテナに対し、
jail の *中から* 上記セキュリティモデルを実際に突く統合テスト。ユニットではないので稼働中の
コンテナが要る (無ければ全 skip)。到達はコンテナ内 Python で `localhost:8000/exec` を叩く
(`docker exec` 越し、追加依存なし)。コンテナ名は `SANDBOX_CONTAINER` で指定する。
**既定は `llens-sandbox`** — コンテナ名を付けるのは配備側の compose で、現行の唯一の配備
(llens) がこの名前を使っているため。別名で上げた環境ではこの env を渡す。

- **権限昇格**: uid/gid=65534(nobody) / `setuid(0)` 拒否 / rootfs (/usr /etc /opt/venv) read-only /
  ホスト `/workspaces` (他ユーザー領域) 不可視。
- **デバイス・ハードウェア・システムの見え方**: /dev は 5 ノード (null/zero/full/random/urandom)
  のみ / ホストの block・GPU デバイス (sda/loop/nvme/mem/kmsg/nvidia/dm) 不可視 / 無ネットワーク
  (外部接続不可) / `df` は quota (既定 10GB) を返しホスト実容量は見えない / PID namespace 独立
  (jail 内から見える pid は自プロセスのみ)。

実行: `uv run task sandbox-test` / `python3 sandbox/tests/test_isolation.py` (pytest 不要の
末尾ランナー付き)。**本体の `uv run pytest` には含めない** — `[tool.pytest.ini_options]` の
`testpaths` は `nucllei/test` だけなので、`sandbox/tests/` は明示指定でしか走らない
(コンテナ依存テストで CI のバックエンドゲートを汚さないため)。

コンテナ不要のユニットテストは `sandbox/tests/test_session_manager.py` (`uv run task sandbox-unit`) —
`main.py` を直接 import し kernel 起動を偽物にして、SessionManager のリセット通知
(TTL 満了 / 追い出し / drop の非二重) と lock 待ち中の client 切断検知を確認する。

## イメージと配布

- **image 名**: `ghcr.io/technoplasm/nucllei-sandbox`。本体 image
  (`ghcr.io/technoplasm/nucllei`) と別 package だが、同じ repo・同じ git タグで焼かれるので
  「どの版の nucllei と組で作られたか」がタグから読める。
- **ビルド契機**: リリースタグ (`vYYYYMMDD-NN`) / プレタグ (`pre-NN`) の push、または
  workflow_dispatch (`.github/workflows/sandbox-image.yml`)。master push ごとには焼かない
  — 空撃ちになる上、「いつ時点の image か」がタグから読めなくなる。
- **版の焼き込み**: CI が `--build-arg APP_VERSION=<タグ名>` を渡し、image は OCI ラベルと
  **ENV `APP_VERSION`** の両方で版を持つ。ENV が要るのは、閉域で版を表示する llens の
  `scripts/depot/restore.sh` が「自 repo 産以外 (ext) の image は ENV しか見ない」ため
  (ラベルには他人の版体系が入りうる)。値は同じタグの本体 image と一致する。
- **手元ビルド**: `docker build -t nucllei-sandbox:dev ./sandbox`。nsjail のソースビルドは
  builder ステージにキャッシュされるので 2 回目以降は速い (APP_VERSION は空になるが動作に影響なし)。
- **配備側での取り回し** (pull → ローカル名に retag → compose が固定名を参照、閉域への
  搬入は registry 間 copy) は配備層の責務。手順は llens の `docs/airgap.md` /
  `RELEASE.md` が単一情報源で、ここには書かない (二重管理にすると必ずずれる)。

## 実装状況 / 残課題

作業管理の台帳は [STATUS.md](STATUS.md) (実装済みチェックリストと残課題)。
