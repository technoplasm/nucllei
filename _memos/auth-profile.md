# llens 標準認証プロファイル(issue #7)

fork.md §3.1(自己ホスト・院内完結認証)の既定値を固定する。
v0.6.5 の素の既定値がほぼそのまま院内完結プロファイルに合致しており、
**環境変数の追加設定なしで以下の運用になる**ことを回帰テスト
(`nucllei/test/apps/webui/routers/test_auth_profile.py`)で保護する。

## 既定値(= llens 標準)

| 設定 | 既定値 | 意味 |
|---|---|---|
| `WEBUI_AUTH` | `True` | 認証必須 |
| `ENABLE_LOGIN_FORM` | `True` | ローカル ID/パスワードのログインフォーム |
| `ENABLE_SIGNUP` | `True` → 初回登録後に自動で `False` | 下記ブートストラップ参照 |
| `DEFAULT_USER_ROLE` | `pending` | 万一 signup を再開放しても即利用は不可(承認待ち) |
| `ENABLE_LDAP` | `False` | 外部 IdP に依存しない(コードは温存) |
| `ENABLE_OAUTH_SIGNUP` | `False` | 同上 |
| `ENABLE_LOGIN_AUTOCOMPLETE` | `True` | ログインフォームの autocomplete。共有 PC 配備で `False`(下記) |

## ブートストラップと運用

1. 初回セットアップ: 最初に signup したユーザーが **admin** になる。
   その瞬間に `ENABLE_SIGNUP` が自動で `False` に永続化される(auths.py)。
2. 以後のユーザー発行は **管理者(事務)が管理画面から作成**
   (`POST /api/v1/auths/add`、GUI: 管理画面 > ユーザー)。
3. signup を再開放する運用も可能だが、その場合も新規ユーザーは
   `pending` で承認待ちになる。

## 共有 PC 配備(北大病院・使い回し端末)

配備先の端末が複数人で使い回す共有 PC になるため、
**ブラウザのパスワードマネージャに認証情報を残したくない**という要件。

### 結論から: Web 側だけでは防ぎきれない

「保存プロンプトを出させない」は **Web アプリからは実現できない**。
Chrome / Edge は password 欄の `autocomplete="off"` を **仕様として意図的に無視する**
(ユーザーのパスワード管理を守る方が重要、という Chromium セキュリティチームの判断)。
Firefox / Safari も程度の差はあれ同様に、保存の可否は最終的にユーザーとブラウザ側が決める。

- 根拠: MDN `autocomplete`
  「In most modern browsers, setting `autocomplete` to `"off"` will not prevent a password
  manager from asking the user if they would like to save username and password information,
  or from automatically filling in those values in a site's login form.」
- 根拠: Chromium `chromium-dev` の PSA「Ignoring autocomplete='off' by default for password fields」

**したがって「ENV を入れれば記憶されなくなる」とは言えない。** 下表の切り分けで運用する。

| 目的 | Web 側(nucllei)で可能か | 手段 |
|---|---|---|
| 保存済み資格情報の**オートフィル**を抑える | ◯(概ね効く) | `ENABLE_LOGIN_AUTOCOMPLETE=false`(下記) |
| 「パスワードを保存しますか」**プロンプトを出させない** | ✕ | 端末のブラウザポリシー。Chrome / Edge の `PasswordManagerEnabled=false`(GPO / Intune / macOS 構成プロファイル。Windows GUI は「パスワード マネージャーへのパスワードの保存を有効にする」を無効) |
| **既に保存済み**の資格情報を消す | ✕ | 端末側での削除。ポリシーを後から入れても既存の保存分は残る |
| 席を離れた後の**セッション乗っ取り** | ◯ | `JWT_EXPIRES_IN`(既存 env、下記の「アイドルタイムアウト」) |
| 離席中に**前の人の画面が出たまま**になる | ◯ | 同上(FE 側タイマが `/auth` へ逃がす) |

> ブラウザポリシーの適用は病院の端末管理(情シス)側の作業。
> nucllei 側でやれるのは「オートフィルを促さない」ところまで、と説明すること。

### `ENABLE_LOGIN_AUTOCOMPLETE`(RuntimeConfig、既定 `True`)

`False` にするとログイン画面(`src/routes/auth/+page.svelte`)の autocomplete を抑止方向に倒す:

- `<form autocomplete="off">`
- パスワード欄 `autocomplete="current-password"` → **`new-password`**
  (`off` は無視されるが `new-password` は「既存パスワードを入れる欄ではない」と解釈され、
  Chrome / Edge も保存済み資格情報のオートフィルをしない。実効性が一番高い組み合わせ)
- 氏名 / ユーザー名 / メール欄 → `off`(非 password 欄の `off` は素直に効く)

配線: `nucllei/config.py`(定義)→ `nucllei/main.py`(`app.state.config` バインド +
`/api/config` の `features.enable_login_autocomplete`)→ `src/lib/stores/index.ts`(型)→
ログイン画面。**`features` ブロックは未認証でも返る**ので、ログイン前の画面から読める
(回帰テスト: `test_login_autocomplete_flag_is_public_and_defaults_on`)。

既定を `True`(= 現状維持)にしてあるので、共有 PC の配備側だけが `.env` / compose で落とす。

### 併せて効く既存の設定(新規実装は不要)

| env | 既定 | 共有 PC での推奨 | 備考 |
|---|---|---|---|
| `JWT_EXPIRES_IN` | `-1`(無期限) | `4h` 等 | RuntimeConfig。`s/m/h/d/w` 表記。**アイドル(無操作)タイムアウト** — 下記 |
| `WEBUI_SESSION_COOKIE_SECURE` / `WEBUI_AUTH_COOKIE_SECURE` | `false` | HTTPS 配備なら `true` | `nucllei/env.py` |
| `WEBUI_SESSION_COOKIE_SAME_SITE` / `WEBUI_AUTH_COOKIE_SAME_SITE` | `lax` | `strict` も可 | 同上 |

- **「ログイン状態を維持する」(remember me)チェックボックスは fork に存在しない。**
  抑止対象そのものが無い。
- **トークンは httpOnly cookie に一本化済み**(#70 B1、`auth/+page.svelte` のコメント)。
  `localStorage` に JWT を残さないので、共有 PC で「ブラウザに残る」のは cookie のみ。
  `JWT_EXPIRES_IN` を有限にすれば端末に残り続けない。

### アイドルタイムアウト(`JWT_EXPIRES_IN` の意味)

**配備者向けの説明**:

> `JWT_EXPIRES_IN` は「無操作でこの時間が経ったらログアウトする」時間。
> **使っている間は切れない** — 最後にキーを叩いた / クリックした時点から数え直す。
> 例: `JWT_EXPIRES_IN=1d` なら、丸一日使い続けても切れず、席を立って 1 日経つと切れる。
> 既定は `-1`(無期限 = タイムアウトしない)。設定はこれ 1 つで、絶対上限は無い。
> 値は `s` / `m` / `h` / `d` / `w`(例 `30m` `4h` `1d`)。管理画面 > 設定 > 全般 からも変えられる。
> 切れたときは操作した本人がログイン画面に戻され、「セッションの有効期限が切れました」と出る。

実装(2026-09-05、共有 PC 対策):

| 層 | 何をするか | 実体 |
|---|---|---|
| FE 送信側 | 最後の延命以降に**人の操作**(`keydown` / `pointerdown` / `click` / `touchstart` / `wheel`。**`mousemove` は数えない**。
ホイールは「長い回答をスクロールして読んでいる = 在席」なので数える、カーソルが動いただけは数えない。DOM イベントのほかに**マイク入力で発話を検出している間**も数える — 下記)があったリクエストにだけ `X-Nucllei-Activity: 1` を付ける | `src/lib/apis/fetch.ts`(全 API が通る唯一の口) |
| BE | そのヘッダがあり、かつ token が**寿命の半分**を過ぎていたら cookie を再発行。`-1` なら何もしない。**API キー(`sk-`)経路は延命しない** | `nucllei/utils/auth.py`(`mark_session_renewal`)、Set-Cookie は `nucllei/main.py` の middleware |
| FE 画面側 | 最後の人の操作から `JWT_EXPIRES_IN` 経過したら自分で `/auth?redirect=` へ逃がす | `src/lib/apis/fetch.ts`(`startIdleWatch`)、起動は `src/routes/+layout.svelte` |

設計上の要点(触るときはここを壊さないこと):

- **延命の起点は「認証付きリクエストが来たこと」ではなく「人が操作したこと」。**
  チャット画面は開いている間 5 秒ごとに認証付き API を叩く
  (`Chat.svelte` の `pollExecutions` / agent session ポーリング)。リクエスト基準にすると
  **タブを開いたまま離席した人が永久にログインしたまま**になり、離席対策として無意味になる。
  ヘッダ方式なので**endpoint の除外リストを持たない** — 将来ポーリングが増えても自動的に安全側。
- **`Set-Cookie` は依存関数ではなく HTTP middleware で書く。** endpoint が `Response` を直接返す
  場合(chat completion の `StreamingResponse` 等)、FastAPI は依存関数の sub-response の
  header を捨てる(`fastapi/routing.py`)。依存関数だけに置くと「メッセージ送信」という
  最も人間的な操作で延命が落ちる。
- **FE 側タイマも要る。** アイドル中のチャットは**リクエストを 1 本も出さない**
  (`pollExecutions` は `visibilityState === 'hidden'` と `shouldPollExecutions` で二重に止まる)。
  サーバのセッションが死んでも、次にリクエストが飛ぶまで画面は前の人のチャット一覧と本文のまま。
  共有 PC で一番効かせたいのがここ。TTL は `/api/config` の `session_idle_timeout`
  (= 既存 `JWT_EXPIRES_IN` を秒で露出しただけ。**設定項目は増やしていない**)。
  判定は `setTimeout` の残り時間ではなく**時刻の差** — スリープ・背面タブでタイマが飛んでも
  復帰時点(`visibilitychange` / `focus`)で評価する。
  失効の後始末(sessionStorage フラグ + ハード遷移 + 到着後トースト)は 401 経路(#70 B2)と共用で、
  二重には走らない。
- **「人の操作」にはマイク入力の発話も含める**(2026-10-03、ken 指示「読み取り中はタイムアウト
  しないように」)。喋っているだけの間はキーもクリックも無いので、`JWT_EXPIRES_IN` を短くした
  配備では長い口述の途中で FE タイマが追い出し、読み取った文が失われていた(口述中の
  `/transcriptions` にも延命ヘッダは付かなかった)。`fetch.ts` が `noteInteraction()` を公開し、
  `VoiceRecording.svelte` の音検出ループが**音を検出しているフレームで、5 秒に 1 回へ間引いて**
  呼ぶ(Web Speech 経路も同じループを回すので同じ扱い)。DOM イベントと同じ関数なので、FE タイマの
  起点・次のリクエスト(= 次の `/transcriptions`)のヘッダ・半減期の keepalive がすべて同じに効く。
  **録音バーが開いているだけの無音では呼ばない** — マイクを付けたまま離席した共用 PC は今までどおり
  切れる。「音」の判定は録音の区切りと同じ(`silence_threshold_db` を超える bin があるか)なので、
  しきい値を超える環境音が鳴り続ける部屋では延命されうる。Call(`CallOverlay.svelte`)は未対応。
- **「操作はあるがリクエストが出ない」時間帯を塞ぐ延命の 1 本(`sendKeepalive`)。**
  人の操作は必ずしもリクエストを生まない — 長い回答をホイールで読む、長文を打ち込む。
  ヘッダを運ぶ乗り物が来ないのでサーバ側の期限だけ切れ、**画面の前に人が居るのに**
  次のクリックで 403 → ログイン画面になる(E2E 実測 2026-09-05: wheel だけ 150 秒 →
  次のクリックで追い出し)。そこで `GET /api/v1/auths/` を 1 本だけ出してヘッダを運ぶ。
  条件は 2 つ:
  - **人が操作した瞬間**にだけ出す(`noteInteraction` から呼ぶ)。**無操作なら 1 本も
    出ない**ので「ポーリングでは延命しない」は保たれる。
  - 最後にヘッダを運べた時刻から**半減期**(`JWT_EXPIRES_IN / 2` = BE が再発行を始める
    境目)以上経っているときだけ。`1d` なら 12 時間に 1 本、`30m` なら 15 分に 1 本。
    普通に API を叩いている間は条件が成立しないので追加リクエストは出ない
    (E2E 実測: 20 秒ごとにクリックして 150 秒 → keepalive 0 本)。
- **keepalive を FE タイマの tick から出してはいけない**(2026-09-05 に実測で判明し、
  操作イベント側へ移した)。tick で出すと操作から最大半減期ぶん遅れて再発行が走り、
  **サーバの期限が FE のアイドル期限を追い越す**。すると FE が `/auth` へ逃がしても
  `/auth` がまだ生きているセッションを見てアプリへ戻し、**前の人の画面が復活する**
  (実測: 操作 t=1.6s → tick で延命 t=26.2s → FE 追い出し t=71.2s → `/auth` が `/` へ戻した)。
  操作した瞬間に出せば `lastActivitySentAt <= lastInteractionAt` が常に成り立ち、
  サーバのセッションは必ず FE の期限**以前**に死ぬ。
- **`GET /api/v1/auths/` は token を再発行しない**(2026-09-05 に撤去)。呼び出し元は
  アプリ起動時・プロフィール更新後・ログイン直後の 3 箇所 = **ページ読み込み時**だけなので、
  再発行を残すと「タブを開き直せば無期限に延命できる」離席対策の穴になる。
  応答から `token` / `token_type` も落とした(#70 B1 で cookie 一本化済み・FE 未参照)。
  `expires_at` は現セッションの `exp` を返す。
- **絶対上限(何時間使っても必ず切る)は作らない**(設定が増えるため不要と判断 — ken、2026-09-05)。

回帰テスト: `nucllei/test/apps/webui/routers/test_session_idle_timeout.py`(BE)、
`src/lib/apis/fetch.test.ts`(FE)。

## 方針(fork.md より)

- 認証(誰がログインできるか)と認可(group 権限)は分離して扱う。
  認可の真実源は `permissions.md`(#66 の 3 層モデル。旧・交差設計 issue #4 は前提誤りで close)。
- OAuth/OIDC/LDAP のコードは **削除しない**(off のまま温存。
  他病院展開で必要になる可能性があるため、投資も削除もしない)。
