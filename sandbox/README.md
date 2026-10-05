# sandbox — サーバーサイドコード実行基盤

Pyodide (ブラウザ WASM) のコード実行を置き換える、nsjail ベースのサーバーサイド実行環境。
このファイルが設計・API の単一情報源。

**source は nucllei 製品の部品としてこの repo が持ち**、CI がリリースタグ push で
`ghcr.io/technoplasm/nucllei-sandbox:<tag>` を publish する
(`.github/workflows/docker-build.yaml` — 本体 image と同じ workflow・同じ CI gate)。
**配備 (compose のサービス定義・env の実値・ホスト公開ポート・閉域搬入・workspace の
GC 運用) は配備層の `prism-hu/llens` が持つ** —
この repo は「image とその中身の設計」だけを持ち、何番で上げるか・quota を何 GB にするかは
配備側の決定。以下で llens の文書 (DEPLOYMENT.md / RELEASE.md / docs/airgap.md) を指すのは
そのため。

## この配下 (`sandbox/`)

| パス | 中身 |
|---|---|
| `sandbox/Dockerfile` | イメージ (nsjail ソースビルド + 2 つの venv + シェルツール + Office 変換 + レガシーデータ + OCR・画像・研究データ + 日本語フォント) |
| `sandbox/soffice-wrapper.sh` | `/usr/local/bin/soffice` に置く LibreOffice の wrapper (プロファイルを `/tmp` へ。下記「Office 文書」) |
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
  - /ext/<名前>: 配備側が置いた参照用データを read-only bind (既定は空。下記。2026-10-03 までは /mnt)
  - network namespace: 空 (loopback 含め通信不可)
```

- 各ユーザーの `/workspace` は `/workspaces/.images/<nucllei_user_id>.img` という
  **sparse な loopback ext4** をマウントしたもの。見かけ上の容量は quota GB だが、
  ホストの実消費は書き込んだ分だけ (下記 disk quota)。

- 実行環境 (Python venv / シェルツール / フォント) はすべてイメージに焼き込む。
  閉域後の追加インストールは無い前提で、閉域前にリストを確定する。
- UI とのファイル入出力は tool (UI プロセス側) が bytes を持ち運ぶ。
  sandbox サービスは UI に一切アクセスしない (信頼方向は UI → sandbox の一方通行)。

### 参照用データを jail に見せる (`/ext`)

「大きな読み取り専用データを丸ごと走査したい」用途 (コーパス横断の集計など) のために、
**コンテナの `/ext` 直下に在るものが、同じパスのまま jail へ read-only で bind される**
(#138)。**サービス側の設定は無い** — 配備側は compose に volume を足すだけでよい。

置き場の名前が `/ext` なのは (2026-10-03、ken 決定。それまでは `/mnt`)、nucllei が同じ物を
URL `/ext/<name>/…` で配信するため (2026-10-05 から nucllei 側は env `EXT_MOUNTS` の対応表 —
`name=http(s)://…` は proxy、`name=/abs/dir` は静的) — **jail の
パスと URL が同じ文字列** になる (`/ext/cage/static/X` は jail から読んでも、ブラウザで開いても、
nucllei の tool の `url=` / `path=` に渡しても同じ物)。モデルに読み替えをさせない。
`/mnt` を捨てた理由: docker 固有の慣習で、nucllei 側は host 直起動もあり、host の `/mnt`
(NFS 等) を晒してしまう (nucllei の `README_API.md` §3.8)。

```yaml
services:
  nucllei:
    environment:
      # /ext/cage/** は cage の HTTP へ proxy、/ext/shared/** は静的配信
      EXT_MOUNTS: cage=http://cage:13303,shared=/ext/shared
    volumes:
      - /srv/shared:/ext/shared:ro                    # URL /ext/shared/…
  sandbox:
    volumes:
      - /srv/cage/data/doc:/ext/doc:ro                      # jail からも /ext/doc で見える
      - /srv/cage/data/static:/ext/cage/static:ro           # nucllei と同じ相対パスで
```

- **guest 側 = コンテナ側と同じパス**。`host:guest` の対応表を持たないので、複数置いても
  名前順に並ぶだけで衝突しない (同じ名前は同じ 1 つの mount point にしかならない)。
- **直下 1 階層だけ**を見る (`/ext/doc` は bind するが `/ext/doc/guideline` を個別には
  足さない)。「volume 1 本 = `/ext` の 1 エントリ」の対応を保つため。深い位置に bind したい
  (上の `/ext/cage/static`) なら、`/ext/cage` が 1 エントリ = その下は docker が作る中間
  ディレクトリごと bind される。
- **何も置かなければ何も足さない** = 従来どおりの jail (これが既定)。
- **read-only**。compose 側で `:ro` を書き忘れてもコンテナ内が rw になるだけで、jail へは
  `--bindmount_ro` でしか渡らない (jail 内は EROFS)。
- **全ユーザーに同じものが見える** — 置いてよいのは「利用者全員が読んでよいデータ」だけで、
  その判断は配備側が持つ (workspace のユーザー間隔離は従来どおり)。
- 実体を辿れないもの (壊れた symlink) は**飛ばして警告**する。そのまま渡すと jail の起動ごと
  失敗し全ユーザーの全実行が落ちるため。dot 始まりは無視。
- 起動ログに bind 一覧が 1 行出る (`[sandbox] 追加 read-only bind: /ext/doc`)。

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
  配備側が `/ext` に足した参照用データも read-only (bind した各エントリが EROFS。
  それを載せている `/` 自体は 16MB の使い捨て tmpfs なので `/ext` 直下は書ける)。
  rlimit (wall/CPU/メモリ/ファイルサイズ/プロセス数) で資源を制限。
  stdout/stderr はパイプなので rlimit_fsize が効かない → サービス側で
  上限バイトまで読んで以降は捨てる (メモリ膨張防止)。
- **監査ログ**: sandbox 側では取らない。tool の入出力 (= 実行コードと結果) は
  chat 履歴に完全に残り、そちらを保全する運用のため二重化しない。
- **API 認証 = 共有トークン、opt-in** (#132。2026-09-04 に導入、2026-09-05 に
  fail-closed から opt-in へ変更):
  - **`SANDBOX_TOKEN` を設定すれば認証が有効**になり、`/healthz` `/metrics` 以外の全
    endpoint が `Authorization: Bearer <SANDBOX_TOKEN>` を要求する。
  - **未設定なら認証は掛からず、従来どおり無認証で動く**。「キーが無ければ元のまま」
    (ken 判断) — トークンを渡していない既存の配備・手元の `docker run` を壊さないことを
    優先した。起動を拒否したり 401 を返したりはしない。
  - **未設定時に何が起きるか** (トレードオフの事実。#132 以前と同じ状態):
    この API は「到達した者が body の `user_id` をそのまま名乗る」設計なので、
    **到達 = 任意ユーザーとしてのコード実行・任意ユーザー workspace の読み書き・
    `DELETE /workspace/{user_id}` による他ユーザー workspace の破棄**と同義になる。
    compose 内 (`sandbox:8000`) に加え host `127.0.0.1:13302` へ公開する
    (フォークした UI をホスト側で動かして叩くため) ので、ホスト上のローカルプロセスは
    無認証で叩ける。**実配備では設定すること。**
  - **黙って無防備にはしない**: 未設定で起動したときは起動ログに warning を 1 行出す
    (`[sandbox] WARNING: API 認証は無効です (SANDBOX_TOKEN 未設定)…`)。呼び出し元の
    nucllei 側も、`SANDBOX_URL` はあるのに `SANDBOX_TOKEN` が無い配備で同種の warning を
    出す (`_memos/builtin-tools.md`)。ログには状態だけを書き、上の「何が起きるか」は
    この節が単一情報源。
  - **env は両側で同名 `SANDBOX_TOKEN`** (sandbox サービスと呼び出し元の nucllei)。
    compose では 1 つの値を 2 サービスに配れば済み、「どちらの名前だったか」を
    考えなくてよい。値の作り方は任意の高エントロピー文字列 (例: `openssl rand -hex 32`)。
    **値は ASCII 限定** — HTTP ヘッダは latin-1 で解釈されるため非 ASCII を含むトークンは
    どう送っても一致せず全要求が 401 になる (起動ログで warning を出す。推奨の
    `openssl rand -hex 32` は常に ASCII)。
    **片側だけ設定すると壊れる**: sandbox 側だけ設定 → nucllei からの呼び出しが全て
    401、nucllei 側だけ設定 → 送ったヘッダは無視されて素通し (認証されていない)。
  - **有効なときは既定 deny**: 認証は middleware で全パスに掛かり、免除は `PUBLIC_PATHS`
    に書いた `/healthz` と `/metrics` だけ (endpoint を足したときに開く側へ倒れない)。
    この 2 つを免除するのは、配備側の healthcheck / Prometheus が叩く口で、返すのが
    真偽値と label なし集約値だけ = user データを含まないため (docs/health.md ②)。
  - 「トークンが空かどうか」の判定は `main.py` の `AUTH_ENABLED` 1 箇所だけが持つ。
    middleware の素通し判断も `_token_ok` の防御も同じフラグを見る (認証が有効なとき、
    空トークン `Bearer ` が正解になる経路は無い)。
  - **有効なときに保証するのは「呼び出し元が nucllei であること」だけ**。`user_id` の
    正しさは依然として呼び出し元任せ = **nucllei を信頼境界とする**のが本 fork の設計判断。
    署名付き user_id 等の強い紐付けは採らない (nucllei と sandbox は同じ tag で出る
    一体の出荷物であり、2 段目の検証は運用コストに見合わない)。
  - トークン値はログにもレスポンスにも出さない。比較は `hmac.compare_digest` (定数時間)。
  - 配備側の外部 GC スクリプト (`DELETE /workspace/{user_id}` を呼ぶ、docs/health.md ④)
    も、トークンを設定した配備では同じヘッダが要る。
  - ホスト公開ポート採番は配備層 (llens の DEPLOYMENT.md のサービス表) が単一情報源で、
    この repo は番号を決めない。

### 外から「認証が効いているか」を確かめる (配備の監査用の契約)

opt-in なので、**トークンを入れ忘れても止まらない**。`.env` に値があるかを見るだけでは
不十分で (image が古くて読まない場合がある)、**実際に叩いて確かめる**必要がある。
配備側の監査スクリプトはこの 2 本を使ってよい:

| 叩き方 | 認証あり | 認証なし (未設定) | 何が分かるか |
|---|---|---|---|
| `GET /healthz` (トークン無し) | `200` | `200` | 到達できる |
| `GET /_auth_probe` など**存在しないパス**にトークン**無し** | **`401`** | `404` | 認証が要求されている |
| 同じパスにトークン**付き** | **`404`** | `404` | **トークンが受理された** |

3 本目が **positive control**。トークン無しの `401` だけでは「サービスが全部落ちている」と
区別できない (どちらも失敗する) ため、**同じパスにトークンを付けて `404` が返ること**で
「認証を通過して routing に落ちた」= トークンが正しいことまで示せる。
認証を通った未知パスが `404` になることも**契約として維持する**
(`test_unknown_path_with_token_is_404` が固定)。

**これは契約として維持する。** 免除は `/healthz` と `/metrics` の**完全一致 2 つだけ**で、
それ以外は未知パスも含めて routing の前段で 401 になる (`test_unknown_path_is_401_not_404`
`test_public_path_lookalikes_are_401` が固定)。副作用のないパスで判定できるよう、
**存在しないパスを使うこと** — `/exec` 等の実処理パスを probe に使わない。

2 本目が `404` を返したら、その配備は**無認証で動いている**。2 本目が `401` で
3 本目も `401` なら、トークンが**両側で一致していない**(片側だけ設定した等)。

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
  - `/workspace/memory/` — 配備層の `memory` skill (llens の `nucllei/skills/memory.md`)
    が会話を跨ぐ記憶を置く (`MEMORY.md` 索引 + トピック別 md / csv)。sandbox 側に特別扱いは
    無く、単なる規約 — ユーザーごとに永続する workspace をそのまま使っているだけ
- 直列化は 2 系統で独立: `/exec_cell` は (user, chat) の session lock、`/exec` は user lock。
  run_python と run_shell、別 chat の run_python 同士は並列に走る。全体の同時実行数は
  semaphore (`SANDBOX_MAX_CONCURRENCY`) で制限。lock 待ちは tool 側 HTTP timeout
  (timeout_s+30) を超えると client が切れて ERROR になる。サーバ側は lock 取得後に
  `request.is_disconnected()` を見て、切れていた要求は実行せず捨てる (499) — 放置すると
  結果の返らない実行が走り、モデルの再試行と合わせて同じコードが 2 回動く。

## API (sandbox サービス、port 8000)

**`SANDBOX_TOKEN` を設定した配備では、`/healthz` と `/metrics` 以外の全 endpoint に
`Authorization: Bearer <SANDBOX_TOKEN>` が要る** (無い/違う → 401)。未設定の配備では
認証は掛からない (opt-in。上の「セキュリティモデル」参照)。

- `POST /exec` — stateless。`{user_id, lang: "python"|"shell", code, timeout_s?, imports?: [{name, b64}]}`
  → `{exit_code, timed_out, duration_s, stdout, stderr, stdout_truncated, stderr_truncated,
     exports: [{name, size, b64?, truncated}]}`
- `POST /exec_cell` — stateful。`{user_id, chat_id, code, timeout_s?}`
  → `{stdout, error, timed_out, session_reset, duration_s, stdout_truncated,
     exports: [...]}`
  - `imports` は実行前に `/workspace/imports/` へ書かれる (basename のみ有効)
  - `exports` は `/workspace/exports/` の実行前後差分 (新規 + 更新)
- `POST /files/list` — `{user_id, path?}` → `{entries, exists, is_dir}` workspace のファイル一覧
  (`exists` / `is_dir` は「無い」「空」「ディレクトリでなくファイル」を言い分けるため — #145)
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
- **保持期限** (`SANDBOX_WORKSPACE_RETENTION_DAYS`、既定 0 = 消さない): >0 にすると、
  最終利用から N 日経った **未 mount** の `.img` を reaper が unlink する (起動直後に 1 回、
  以降 1 時間ごと。`docs/health.md` の ⑤)。最終利用時刻は `.images/<user_id>.last_used`
  (image の隣 = 未 mount でも読める sidecar) を利用のたびに touch して再起動を跨いで持つ。
  marker が無い古い image は `.img` の mtime で一回限り判定する。本番は 40TB NAS なので
  既定は「消さない」 — 半年〜1 年の不在で消してよい配備だけ 180〜365 を設定する
  (ken 決定 2026-10-03)。

## リソース制限 (変更場所: 配備側 compose の environment)

既定値はこの表 (= `app/main.py`) が持ち、環境ごとの実値は配備層 (llens の
`docker-compose.yml`) が上書きする。両方に literal を書くとどちらが効いているか
読めなくなるので、**この repo は既定だけを持つ**。

| env | 既定 | 意味 |
|---|---|---|
| `SANDBOX_TOKEN` | なし (**未設定 = 認証なし**) | API の共有トークン。設定すると全 endpoint (`/healthz` `/metrics` を除く) が Bearer 認証必須になる。未設定なら従来どおり無認証で動く (起動時に warning)。**値は ASCII 限定** (非 ASCII は全要求 401 になる。起動時に warning)。**配備では設定すること** — 呼び出し元の nucllei にも同じ値を同じ名前で渡す |
| `SANDBOX_TIMEOUT_DEFAULT_S` | 120 | wall clock 既定 (tool から実行ごとに指定可) |
| `SANDBOX_TIMEOUT_MAX_S` | 600 | wall clock 上限 (指定値はこれで clamp) |
| `SANDBOX_MEM_MB` | 4096 | rlimit_as |
| `SANDBOX_FSIZE_MB` | 8192 | rlimit_fsize (1 ファイルの書込上限。disk 総量は quota 側が本来の上限) |
| `SANDBOX_QUOTA_GB` | 10 | ユーザー単位の workspace 容量上限 (loopback ext4) |
| `SANDBOX_QUOTA_OVERRIDES` | {} | ユーザー別 quota 上書き `{"<user_id>": GB}` (新規作成時のみ) |
| `SANDBOX_WORKSPACE_RETENTION_DAYS` | 0 (**= 消さない**) | 最終利用から N 日経った未 mount の workspace `.img` を削除 (上記「disk quota」の保持期限)。mount 中・実行中・session 生存中は飛ばす |
| `SANDBOX_LOOP_NODES` | 256 | 先に mknod する loop ノード数 = 同時 quota ユーザー上限 |
| `SANDBOX_MAX_CONCURRENCY` | 4 | 全体の同時実行数 (SGLang と CPU を食い合わないように) |
| `SANDBOX_STREAM_LIMIT_BYTES` | 524288 | stdout/stderr それぞれの保持上限 |
| `SANDBOX_FILE_LIMIT_BYTES` | 26214400 | tool の成果物回収 (fetch_from_workspace / exports 自動添付) の 1 ファイル上限。UI のユーザーアップロード制限とは独立。compose では 100MiB に引き上げ済。read は base64 で JSON に載る (RAM ~2.3x) ため真の無制限にはしない (GB 級は raw ストリーム化が要る) |
| `SANDBOX_SESSION_TTL_S` | 43200 (12h) | stateful session の生存上限 (kernel の nsjail time_limit。idle 回収も同値) |
| `SANDBOX_SESSION_MAX` | 64 | 常駐 session 数の上限 (超過で最も idle を回収) |
| `SANDBOX_SESSION_GRACE_S` | 5 | 中断不能セルを kill するまでの猶予 (kernel の SIGALRM 後) |

参照用データの read-only bind には **env が無い** — `/ext` 直下に置いた volume がそのまま
jail に出る (上記「参照用データを jail に見せる」)。

## UI tool (nucllei/builtin_tools/sandbox.py)

| メソッド | 役割 |
|---|---|
| `run_python(code, timeout_s?)` | **stateful**。会話ごとの常駐 kernel で永続 namespace 実行 (`/exec_cell`)。exports/ 自動添付 |
| `run_python_once(code, timeout_s?)` | stateless 使い捨て Python (`/exec`)。状態非依存の単発用 |
| `run_shell(command, timeout_s?)` | stateless bash 実行 (coreutils, jq, ripgrep, pandoc, weasyprint, unzip/zip, nkf, file, poppler-utils, ffmpeg, soffice (LibreOffice headless), antiword, catdoc/xls2csv/catppt, 下記「レガシーデータ」「OCR・画像・研究データ」の道具)。exports/ 自動添付 |
| `send_to_workspace(filename?, file_id?, url?)` | チャット添付 (`file_id`) / URL (`url`: 絶対 URL、または同一サーバの `/ext/…`) を `/workspace/imports/` に取り込み、絶対パスを返す。URL は nucllei の共有 resolver で解決 (下記) |
| `write_file(path, content)` | モデルが書いたテキストを workspace に直接書く (`/files/write`)。`path` は `/workspace/` 配下の絶対パス。テキスト専用・上書き・既定 1MB 上限 (`SANDBOX_WRITE_FILE_LIMIT_BYTES`) |
| `list_workspace(path?)` | workspace のファイル一覧。`path` は `/workspace` 配下の絶対パス (省略時 `/workspace`)、各行も絶対パスで返す |
| `fetch_from_workspace(path)` | sandbox のファイル (`/workspace/…` か `/ext/…` の絶対パス) を利用者に見せられる形にする: Files に登録してチャットに添付し file_id を返す。nucllei 側にも在る (= 既に URL `/ext/…` で配信されている) `/ext/…` のファイルは添付せず、その URL (同じ文字列) を返す (下記) |

- **`send_to_workspace(url=)` の URL は nucllei の共有 resolver (`nucllei/utils/image_refs.py`) で解決する**
  (2026-10-03): `/ext/{rest}` (同一サーバの相対、または絶対 `https://<host>/ext/{rest}` = host 不問) は
  nucllei の `EXT_MOUNTS` の対応表で読む (2026-10-05。静的の name はファイル、proxy の name は上流へ GET)、それ以外のスキーム付き http(s) URL は**どこでも**直接取りに行く
  (断るのはこのサーバ自身だけ。cookie は転送しない)。builtin の `fetch_url` / `feed_image` と同じ URL が
  同じように通り、同じ理由で落ちる。上限は valve `url_max_mb` (既定 50 MB)。
  - **`/ext` 配下にある物はコピーしない**: URL が `/ext/<name>/<sub>` で name が静的の mount かつ
    ファイルが実在すれば、取り込まずに「`/ext/<name>/<sub>` として既に sandbox から見えている」とパスを返す
    (URL と同じ文字列。上限も掛からない)。proxy の name は上流から取り込む (cage が描画した HTML 等が
    jail の同じパスに在るとは限らない)。sandbox の `/ext/<name>` と nucllei の `EXT_MOUNTS` の `<name>` に同じ物を置く
    規約 (上の「参照用データを jail に見せる (`/ext`)」) に依存する — **nucllei は sandbox 側の bind の
    有無を確かめない**ので、nucllei にだけ bind した配備ではモデルに無いパスを案内することになる。
    必ず両方に bind する。
  - **引数は名前で解釈が決まる。値の中身を見て別の引数に読み替えない**: `url` は URL としてだけ
    解決し (UUID や `/workspace/…` は「URL として解決できない」+ 固定の案内文)、`file_id` に
    URL が来ても url としては処理しない。`url` の `/api/v1/files/{id}/content` (このサーバのファイル
    配信口。相対・絶対) は URL のルーティングとして file に当たり、file_id と同じ所有者チェックで取り込む。
    規則の定義は nucllei の `README_API.md` §3.8。
- **`fetch_from_workspace(path)` は `send_to_workspace(url=)` の逆向きの橋渡し** (2026-10-03)。利用者に
  見せられるのは URL だけ (`/ext/…` か `/api/v1/files/{id}/content`) で、sandbox のパスはブラウザでは
  開けない。モデルにパスを URL へ書き換えさせず、tool が解決結果で決める:

  | tool | 向き | 同じ実体が両方にあるとき | そうでないとき |
  |---|---|---|---|
  | `send_to_workspace(url)` | URL → sandbox で開けるパス | コピーせず `/ext/…` (URL と同じ文字列) を返す | `/workspace/imports/` にコピー |
  | `fetch_from_workspace(path)` | sandbox のパス → 利用者に見せられる URL / file_id | 添付せず `/ext/{name}/…` を返す | Files に登録して添付 (file_id) |

  - `/ext/…` を nucllei の `/ext` ゲートが同じ URL で配信していれば (静的は実在、proxy は上流が HEAD に 2xx)、
    その配信 URL (相対 `/ext/…`、パス部は percent-encode 済み — ASCII 名なら path と同じ文字列) を返す。
    Files には何も作らない。**nucllei が配信している物は全部 URL になる** — 「利用者に配信する物」と
    「sandbox で読むだけの物」を分けたい配備は、後者を `EXT_MOUNTS` に書かず sandbox にだけ bind する
    (2026-10-03 夕に `EXT_FILE_SCOPES` と「利用者に配信しない範囲にある」の断りは撤去。
    nucllei の `README_API.md` §3.8)。
  - nucllei 側に無い `/ext/…` は添付の経路に落ちるが、nucllei 自身の置き場から読む (sandbox の
    `/files/read` は workspace しか扱わない) ので **「読めない」になる** — そのときは `run_shell` で
    `/workspace/` にコピーしてから渡すよう tool が案内する。上限は nucllei の `FETCH_MAX_BYTES`
    (既定 25 MB。`/workspace` 側の `SANDBOX_FILE_LIMIT_BYTES` とは別。旧 `EXT_INLINE_MAX_BYTES`)。
  - 不在・ディレクトリ・置き場の外を指す symlink は同じ「読めない」(fail closed)、上限超過は
    実サイズと上限を言う。`path` に URL が来ても URL としては扱わない。
- **tool の `path` は jail から見た絶対パス (`/workspace/...`) で揃える**。`send_to_workspace` の
  戻り値・`run_python` / `run_shell` の中のパス・`list_workspace` の戻り値が全部同じ形なので、
  ある tool の結果を別の tool の `path` にそのまま渡せる。**相対パスは受けない** (2026-10-03。`path` の形は
  絶対パス 1 つ — 相対を「`/workspace` からの相対」と解くと `path="https://x/y.html"` が
  `/workspace/https:/x/y.html` になる。`list_workspace` の無指定の既定は `/workspace`)。
  相対・空・URL・`/workspace` の外 (`/etc/...`、`..` での脱出) は tool 側で、全 tool 共通の固定文
  (`README_API.md` §3.8) で断り、`/ext/...` (read-only の参照
  データ) は `write_file` / `list_workspace` が「`run_shell` の `ls` で見る。利用者に見せるなら
  `fetch_from_workspace`」と案内して断る — サービスの `/files/*` は workspace しか
  扱わないため (`fetch_from_workspace` だけは `/ext/...` を受ける。上の項)。**サービスの wire (`/files/list|read|write` の `path`) は workspace 相対のまま**で、
  絶対 ↔ 相対の変換は tool 側だけが持つ (sandbox image と nucllei の版がずれても動く)。境界の検査は
  tool 側 (文字列の正規化) とサービス側 (`_safe_path`、symlink 越しの脱出も実 fs で弾く) の二重。
  失敗時の文面は原因で言い分ける: 400 = path の問題、404 = ファイルが無い、413 = 大きすぎる、
  HTTP 応答が無い = 接続できない。
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
- **Office 文書 (レガシー 97-2003 形式を含む)**: 院内の添付は今も `.doc` / `.xls` / `.ppt` が多いので、
  開く道具を一通り焼き込む (Office 系 apt は重いので別層)。image 増分は約 +360MB (非圧縮。
  圧縮 = pull 量で約 +140MB)、ほぼ LibreOffice。

  | 道具 | 入っているもの | 用途 |
  |---|---|---|
  | `soffice --headless --convert-to <fmt>` | apt `libreoffice-{writer,calc,impress}-nogui` (GUI・Java 無し、7.4) | 本命。.doc/.xls/.ppt ⇄ docx/xlsx/pptx、→ pdf / csv / png。PDF の日本語は埋め込まれる (明朝・ゴシック指定は IPA、指定の無い和文は Noto CJK。下記「レガシーデータ」のフォント節) |
  | `antiword -m UTF-8.txt` | apt `antiword` | .doc → テキスト (表は `\|` 区切り)。soffice を起動しない軽量経路 |
  | `catdoc -d utf-8` / `xls2csv -d utf-8` / `catppt` | apt `catdoc` | .doc → テキスト / .xls → CSV (全シート連結)。**`catppt` は LibreOffice が書いた .ppt から何も取れなかった** (本物の PowerPoint 産は未確認) — .ppt は soffice 経由が確実 |
  | `olefile` / `xlrd` / `python-docx` / `openpyxl` / `python-pptx` | venv | OLE2 の低レベル読み出し / .xls 読み / OOXML 読み書き |
  | `msoffcrypto-tool` | venv (CLI も同名) | パスワード付き Office の復号 (`msoffcrypto-tool -p <pw> in out`) |

  **jail で soffice を動かす工夫 (運用者向け)**: LibreOffice は起動のたびにユーザープロファイルを書き、
  既定の置き場は `$HOME` (jail では永続 workspace = quota 対象)。rootfs は ro なので、
  `/usr/local/bin/soffice` (PATH で `/usr/bin` より前) の wrapper (`sandbox/soffice-wrapper.sh`) が
  `-env:UserInstallation=file:///tmp/lo-profile` を足し、`HOME` も `/tmp/lo-home` に差し替えて
  (プロファイルとは別に `$HOME/.config` を作るため) 実体 `/usr/lib/libreoffice/program/soffice` を呼ぶ。
  `/tmp` は実行ごとに消えるので毎回まっさらなプロファイルで起動し、それでも 1 回 1 秒未満。
  呼び出し側が `-env:UserInstallation=` を渡せばそちらが優先される。stderr に出る
  `Warning: failed to launch javaldx` は Java を入れていないための無害な警告。
  出力ファイル名は入力の basename + 新拡張子なので、同名の .doc と .xls を同じ `--outdir` に
  変換すると上書きされる。回帰は `test_isolation.py` の `test_soffice_*` / `test_legacy_office_*`。
- **レガシーデータ (2026-10-02)**: 院内に残る古い形式を開く軽い道具を一通り。Office 層の後ろに
  apt の別層 + venv の「レガシーデータ」節。image 増分は非圧縮で約 +100MB (apt 層 +58MB・venv +43MB)、
  圧縮 (pull 量) で約 +48MB。**重いもの (ghostscript / imagemagick / tesseract) は入れていない** (別判断)。

  | 道具 | 入っているもの | 用途 |
  |---|---|---|
  | `xxd` / `hexdump -C` | apt `xxd` / `bsdextrautils` | 正体不明のバイナリの先頭を覗く (マジックナンバー確認) |
  | `mdb-tables` / `mdb-export` / `mdb-schema` / `mdb-ver` | apt `mdbtools` (1.0) | Access .mdb / .accdb の表一覧・CSV 書き出し・DDL。日付は既定が `MM/DD/YY` なので `mdb-export -T '%Y-%m-%d %H:%M:%S'` を付ける |
  | `unrtf --html` | apt `unrtf` | RTF → HTML。**`--text` は和文を落とす** — 平文が欲しければ `unrtf --html x.rtf \| pandoc -f html -t plain` か soffice (`--convert-to txt:Text`)。文字コードは fonttbl の `\fcharset128` で解く (`\ansicpg932` だけでは化ける) |
  | `7z` | apt `p7zip-full` | 7z・zip (パスワード付きを含む) の作成と展開 |
  | `unar` / `lsar` | apt `unar` | 汎用展開 (rar・lzh・cab・sit 等)。**Windows で作った zip / lzh の Shift_JIS ファイル名を推定して UTF-8 に直す** — `unzip` / `lha` / `7z` は生バイトのまま出して化けるので、日本語名の書庫はこちら (`lsar` で中身一覧、外したら `-e cp932`) |
  | `lha` | apt `lhasa` | LZH の一覧・展開 (展開専用。作成はできない)。ファイル名は化けるので上の unar が先 |
  | `cabextract` | apt `cabextract` | Windows .cab の展開 |
  | `sqlite3` | apt `sqlite3` | SQLite DB を開く / CSV を SQL で集計する |
  | `xmllint` | apt `libxml2-utils` | XML の整形・XPath 抽出・スキーマ検証 (MML・CDA 等) |
  | `dos2unix` / `unix2dos` | apt `dos2unix` | CRLF ⇄ LF |
  | `qpdf` | apt `qpdf` | PDF の復号 (パスワード既知)・ページ抜き出し/結合・壊れた PDF の修復 |
  | `dbfread` | venv | dBASE / FoxPro .dbf の読み出し (`encoding='cp932'` を明示) |
  | `xlwt` | venv | 97-2003 .xls の書き出し (openpyxl は .xls を書けない。読みは xlrd) |
  | `extract_msg` | venv `extract-msg` (CLI は `extract_msg`) | Outlook .msg の本文・添付の取り出し。依存で oletools (`olevba` 等) も入る |
  | `hl7` | venv | HL7 v2 メッセージ (SS-MIX2 標準化ストレージの中身) のパース |
  | `pyreadstat` | venv | SPSS .sav / SAS .sas7bdat / Stata .dta の読み書き (変数ラベル付き) |
  | `pdfplumber` | venv | PDF の表抽出 (`pdftotext` で列が崩れる帳票向け)。依存で pypdfium2 も入る |
  | `mojimoji` | venv | 全角 ⇄ 半角 (カナ・英数・記号を個別に指定) |
  | `jpholiday` | venv | 日本の祝日 (診療日・営業日計算) |

  **フォント**: `fonts-ipafont` を足し、fontconfig の alias (`/etc/fonts/conf.d/64-nucllei-fonts.conf`、
  Dockerfile 内で生成) で「ＭＳ 明朝 / ＭＳ ゴシック / ＭＳ Ｐ明朝 / ＭＳ Ｐゴシック」(全角・半角・
  英字表記、`MS UI Gothic` も) を IPA 明朝 / ゴシック / P 系へ寄せる。古い Word を soffice で
  PDF 化したとき字幅が原本に近くなり、改行位置のずれが減る。alias が無いと LibreOffice は
  DejaVu と IPA を混ぜて P (プロポーショナル) も区別せず、IPA 自体が無かった前の image では
  明朝指定でも Noto Sans CJK (ゴシック体) に落ちていた。**和文の既定は Noto CJK のまま**: IPA を
  入れると Debian の `65-nonlatin.conf` が `sans-serif` / `serif` の和文 (lang=ja) を IPA に倒すので、
  同じ conf (番号 64 = 65 より先) で Noto Sans / Serif / Sans Mono CJK JP を前に戻している
  (dot・weasyprint・soffice の指定無し和文が IPA に変わらない。欧文は DejaVu のまま)。

  確認済み (jail 経由): Access は mdbtools の公開テストデータ (JET4 の .mdb / ACE12 の .accdb、
  英語) で表一覧・export、.msg は extract-msg の公開サンプル (英語) で本文取り出し。
  **未検証**: 日本語を含む実物の .mdb / .msg / .cab、圧縮 (-lh5-) の LZH (テストは無圧縮 -lh0- を手で組む)。
  回帰は `test_isolation.py` の `test_legacy_data_*` / `test_soffice_maps_ms_fonts_to_ipa_in_jail`。
- **OCR・画像・研究データ (2026-10-02)**: 軽くてよく使うもの (ken 判断)。レガシー層の後ろに apt の
  別層 + venv の「OCR・画像」「研究データ・統計」「日本語テキスト・データ整形」節。
  **OCR はモデルの目 (画像入力 / feed_image) と役割が違う**: モデルが 1 枚を見て判断するのではなく、
  何十ページのスキャン束を機械的に一括でテキスト化 / 検索可能 PDF 化する用途。
  image 増分はレガシー層の後から非圧縮で約 +530MB (apt 層 +231MB・venv +330MB)、圧縮 (pull 量) で
  約 +194MB。venv の大物は opencv (153MB)。janome (辞書同梱で 202MB) と pyarrow (156MB) は
  サイズ対効果で外した (ken 判断 2026-10-02。表は pandas / numpy で足りる)。
  ocrmypdf は既定で全コアにページを振る (300dpi で密な 10 ページが wall 5.4 秒・CPU 42 秒)。
  `rlimit_cpu` はプロセス単位なので落ちないが、SGLang と CPU を食い合わないよう `--jobs 4` を付ける。

  | 道具 | 入っているもの | 用途 |
  |---|---|---|
  | `ocrmypdf -l jpn --force-ocr in.pdf out.pdf` | venv `ocrmypdf` (+ apt `tesseract-ocr`・`ghostscript`) | スキャン PDF の束を検索可能 PDF に (後は `pdftotext`)。縦書きは `-l jpn_vert`。テキスト層が既にあるページは `--skip-text` |
  | `tesseract img - -l jpn` | apt `tesseract-ocr` + `-jpn` / `-jpn-vert` / `-eng` (5.3) | 画像 1 枚 → テキスト。Python からは `pytesseract` (位置付きは `image_to_data`) |
  | `gs -sDEVICE=pdfwrite -dPDFSETTINGS=/ebook` | apt `ghostscript` | スキャン PDF の圧縮 (`-dNOPAUSE -dBATCH -sOutputFile=out.pdf in.pdf`)。ocrmypdf の前提 |
  | `img2pdf` / `pdf2image` | venv | 画像 → PDF (再圧縮しない) / PDF → PIL 画像 (poppler) |
  | `tiffsplit` / `tiffinfo` | apt `libtiff-tools` | マルチページ TIFF (FAX・スキャナ出力) の分割・情報 |
  | `zbarimg` / `pyzbar` | apt `zbar-tools` / venv | QR・バーコードの読み取り (作るのは既存の `segno`) |
  | `exiftool` | apt `libimage-exiftool-perl` (+ `libarchive-zip-perl`) | 画像・PDF・Office のメタデータ (作成者・撮影日時・GPS) の確認と除去 |
  | `dot` / `graphviz` | apt `graphviz` / venv | フロー図・関係図 (`dot -Tpng`/`-Tsvg`/`-Tpdf`)。和文ラベルは既定で Noto CJK |
  | `opencv-python-headless` (`cv2`) | venv | 画像処理 (傾き補正・二値化・輪郭・切り出し)。GUI 無し版 |
  | `git` | apt `git` | workspace 内の版管理、`git diff --no-index --word-diff a b` で文書の語単位差分 |
  | `wdiff` | apt `wdiff` | 2 ファイルの語単位差分 (git 無しで) |
  | `tree` / `mediainfo` | apt | ディレクトリ構造の一覧 / 音声・動画のメタ (コーデック・長さ) |
  | `csvlook` / `csvcut` / `csvstat` / `csvsql` / `in2csv` | venv `csvkit` | CSV をシェルで覗く・切る・集計する・SQL で引く |
  | `docxtpl` | venv | Word テンプレート (jinja2 記法) への差し込みで帳票を作る |
  | `tableone` | venv | 論文の Table 1 (群別の患者背景・p 値) |
  | `pingouin` / `scikit-posthocs` | venv | 検定の簡便 API (効果量・検出力込み。**結果の列名は `p_val`**) / 多重比較の事後検定 |
  | `h5py` / `nibabel` | venv | HDF5 / NIfTI 等の医用画像ボリューム |
  | `neologdn` / `pykakasi` | venv | 日本語テキストの正規化 / 漢字 → かな・ローマ字 |
  | `pyyaml` / `xmltodict` | venv | YAML / XML ⇄ dict |

  **jail で動かす工夫**: git は jail のユーザー (nobody) に名前もメールも無く commit が落ちるので、
  `/etc/gitconfig` (system) に既定の identity (`sandbox <sandbox@localhost>`)・`init.defaultBranch=main`・
  `core.quotePath=false` (日本語ファイル名を `\343...` にしない) を焼いた。HOME (= 永続 workspace) に
  `.gitconfig` は作られない (利用者が `--global` で書けばそちらが勝つ)。ocrmypdf / tesseract は
  `TMPDIR=/tmp` (JAIL_ENV) の揮発 tmpfs だけを使い、workspace を汚さない。**/tmp は 256MB** なので、
  巨大なスキャン束は分割して流す。シェルのプロセス置換 `<(...)` は jail に `/dev/fd` が無いので
  使えない (一時ファイルを経由する)。
  回帰は `test_isolation.py` の `test_ocr_*` / `test_image_and_doc_shell_tools_in_jail` /
  `test_research_python_libs_in_jail`。
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
- **同梱ツールが jail の制約下で動くこと**: LibreOffice headless で .doc/.xls を作って PDF に変換でき
  (日本語フォント埋め込み)、workspace にプロファイル等を残さない / antiword・xls2csv・xlrd・olefile
  で読める / ＭＳ 明朝・ゴシック指定の docx が IPA で PDF 化される / レガシーデータ層の道具
  (xxd・Shift_JIS 名の LZH / zip を unar で展開・unrtf・sqlite3・xmllint・qpdf・7z、venv の
  dbfread・hl7・pyreadstat・pdfplumber・xlwt 等) が動く / tesseract (jpn) と ocrmypdf で画像だけの
  PDF が検索可能 PDF になる (workspace を汚さない) / zbarimg・exiftool・gs・dot (和文は Noto)・git
  (commit と word-diff。HOME に .gitconfig を作らない)・cv2・tableone・pingouin・h5py・nibabel 等が動く。
- **参照用データ (`/ext`)**: volume を入れた配備でのみ確認する — 見えること + 書けないこと
  (EROFS)。何も入れていない配備 (既定) は確認対象が無いので何もしない。

実行: `uv run task sandbox-test` / `python3 sandbox/tests/test_isolation.py` (pytest 不要の
末尾ランナー付き)。**本体の `uv run pytest` には含めない** — `[tool.pytest.ini_options]` の
`testpaths` は `nucllei/test` だけなので、`sandbox/tests/` は明示指定でしか走らない
(コンテナ依存テストで CI のバックエンドゲートを汚さないため)。

コンテナ不要のユニットテストは `uv run task sandbox-unit` (CI でも走る)。どちらも
`main.py` を直接 import する:

- `sandbox/tests/test_session_manager.py` — kernel 起動を偽物にして、SessionManager の
  リセット通知 (TTL 満了 / 追い出し / drop の非二重) と lock 待ち中の client 切断検知。
- `sandbox/tests/test_auth.py` — 共有トークン認証 (#132)。トークンを設定したとき
  ヘッダ無し / 誤トークン / 別スキーム / 未知パスが 401、正しい Bearer が通る、
  `/healthz` `/metrics` は公開、そして**トークン未設定なら認証が掛からず素通しする**
  (opt-in) ことを固定する。加えて**免除は完全一致だけ** (`/healthz/` `//healthz`
  `/HEALTHZ` `/healthz/../files/list` 等はすべて 401 — 免除判定を prefix 一致や自前の
  正規化に変えたら落ちる) と、**非 ASCII トークンは起動ログで名指しする**ことも見る。
  形状のテストは httpx がクライアント側で URL を畳んでしまうため ASGI を直接叩く。
- `sandbox/tests/test_ro_mounts.py` — 参照用データの read-only bind (#138)。置き場が
  空/不在なら nsjail の引数が増えない (挙動不変)、直下のものが同じパスで名前順に並ぶ、
  **rw では絶対に足さない**、1 階層だけ見る、dot 始まり・壊れた symlink は飛ばす
  (後者は警告)、を固定する。

なお `test_isolation.py` の HTTP クライアントは、トークンを**コンテナ自身の env**
(`SANDBOX_TOKEN`) から取る — 配備側がサービスに渡した値が常に正解なので、テストに
値を持たせない (未設定のコンテナでは中身が空になるが、その場合は service 側も認証を
掛けないので通る)。

## イメージと配布

- **image 名**: `ghcr.io/technoplasm/nucllei-sandbox`。本体 image
  (`ghcr.io/technoplasm/nucllei`) と別 package だが、同じ repo・同じ git タグで焼かれるので
  「どの版の nucllei と組で作られたか」がタグから読める。
- **ビルド契機**: リリースタグ (`vYYYYMMDD-NN`) の push、または workflow_dispatch
  (image tag は既定 `dev`)。master push ごとには焼かない
  — 空撃ちになる上、「いつ時点の image か」がタグから読めなくなる。
- **本体と同じ workflow で焼く**: `.github/workflows/docker-build.yaml` の
  `build-and-push-sandbox` job。本体 job と**同じ CI gate (lint/test) にぶら下がり**、
  版 (`APP_VERSION`) と image タグは同 workflow の `version` job が 1 回だけ解決した値を
  両 job が参照する。**nucllei と sandbox は常に同じ版で出る**のがこの構成の目的
  (別 workflow に分けると、テストが落ちたタグで sandbox だけ出て版が割れる)。
  片方の push だけが infra 都合で失敗したら、失敗した job だけ re-run する
  (タグを切り直すと版がずれる)。
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
