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
| 席を離れた後の**セッション乗っ取り** | ◯ | `SESSION_IDLE_TIMEOUT`(既存 env、下記の「アイドルタイムアウト」) |
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
| `SESSION_IDLE_TIMEOUT` | `-1`(無期限) | `4h` 等 | RuntimeConfig。`s/m/h/d/w` 表記。**アイドル(無操作)タイムアウト** — 下記 |
| `WEBUI_SESSION_COOKIE_SECURE` / `WEBUI_AUTH_COOKIE_SECURE` | `false` | HTTPS 配備なら `true` | `nucllei/env.py` |
| `WEBUI_SESSION_COOKIE_SAME_SITE` / `WEBUI_AUTH_COOKIE_SAME_SITE` | `lax` | `strict` も可 | 同上 |

- **「ログイン状態を維持する」(remember me)チェックボックスは fork に存在しない。**
  抑止対象そのものが無い。
- **トークンは httpOnly cookie に一本化済み**(#70 B1、`auth/+page.svelte` のコメント)。
  `localStorage` に JWT を残さないので、共有 PC で「ブラウザに残る」のは cookie のみ。
  `SESSION_IDLE_TIMEOUT` を有限にすれば端末に残り続けない。

### アイドルタイムアウト(`SESSION_IDLE_TIMEOUT` の意味)

**配備者向けの説明**:

> `SESSION_IDLE_TIMEOUT` は「無操作でこの時間が経ったらログアウトする」時間。
> **使っている間は切れない** — 最後にキーを叩いた / クリックした時点から数え直す。
> 例: `SESSION_IDLE_TIMEOUT=1d` なら、丸一日使い続けても切れず、席を立って 1 日経つと切れる。
> 既定は `-1`(無期限 = タイムアウトしない)。設定はこれ 1 つで、絶対上限は無い。
> 値は `s` / `m` / `h` / `d` / `w`(例 `30m` `4h` `1d`)。管理画面 > 設定 > 全般 からも変えられる。
> 切れたときは操作した本人がログイン画面に戻され、「セッションの有効期限が切れました」と出る。

実装(2026-09-05、共有 PC 対策):

| 層 | 何をするか | 実体 |
|---|---|---|
| FE 送信側 | 最後の延命以降に**人の操作**(`keydown` / `pointerdown` / `click` / `touchstart` / `wheel`。**`mousemove` は数えない**。
ホイールは「長い回答をスクロールして読んでいる = 在席」なので数える、カーソルが動いただけは数えない。DOM イベントのほかに**マイク入力で発話を検出している間**も数える — 下記)があったリクエストにだけ `X-Nucllei-Activity: 1` を付ける | `src/lib/apis/fetch.ts`(全 API が通る唯一の口) |
| BE | そのヘッダがあり、かつ token の年齢が**延命の刻み**(`min(60 秒, 寿命/10)`。2026-10-20 までは「寿命の半分」)を過ぎていたら cookie を再発行。そのヘッダ付きの応答には、再発行したかどうかに関わらず今の token の期限 `X-Nucllei-Session-Expires: <exp epoch 秒>` を返す(#154)。`-1` なら何もしない。**API キー(`sk-`)経路は延命しない** | `nucllei/utils/auth.py`(`mark_session_renewal` / `apply_session_renewal`)、Set-Cookie と期限ヘッダは `nucllei/main.py` の middleware |
| FE 画面側 | 最後の人の操作から `SESSION_IDLE_TIMEOUT` 経過したら自分で `/auth?redirect=` へ逃がす | `src/lib/apis/fetch.ts`(`startIdleWatch`)、起動は `src/routes/+layout.svelte` |

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
  (= 既存 `SESSION_IDLE_TIMEOUT` を秒で露出しただけ。**設定項目は増やしていない**)。
  判定は `setTimeout` の残り時間ではなく**時刻の差** — スリープ・背面タブでタイマが飛んでも
  復帰時点(`visibilitychange` / `focus`)で評価する。
  失効の後始末(sessionStorage フラグ + ハード遷移 + 到着後トースト)は 401 経路(#70 B2)と共用で、
  二重には走らない。
- **「人の操作」にはマイク入力の発話も含める**(2026-10-03、ken 指示「読み取り中はタイムアウト
  しないように」)。喋っているだけの間はキーもクリックも無いので、`SESSION_IDLE_TIMEOUT` を短くした
  配備では長い口述の途中で FE タイマが追い出し、読み取った文が失われていた(口述中の
  `/transcriptions` にも延命ヘッダは付かなかった)。`fetch.ts` が `noteInteraction()` を公開し、
  `VoiceRecording.svelte` の音検出ループが**音を検出しているフレームで、5 秒に 1 回へ間引いて**
  呼ぶ(Web Speech 経路も同じループを回すので同じ扱い)。DOM イベントと同じ関数なので、FE タイマの
  起点・次のリクエスト(= 次の `/transcriptions`)のヘッダ・操作の瞬間の keepalive がすべて同じに効く。
  **録音バーが開いているだけの無音では呼ばない** — マイクを付けたまま離席した共用 PC は今までどおり
  切れる。「音」の判定は録音の区切りと同じ(`silence_threshold_db` を超える bin があるか)なので、
  しきい値を超える環境音が鳴り続ける部屋では延命されうる。Call(`CallOverlay.svelte`)は未対応。
- **「操作はあるがリクエストが出ない」時間帯を塞ぐ延命の 1 本(`sendKeepalive`)。**
  人の操作は必ずしもリクエストを生まない — 長い回答をホイールで読む、長文を打ち込む。
  ヘッダを運ぶ乗り物が来ないのでサーバ側の期限だけ切れ、**画面の前に人が居るのに**
  次のクリックで 403 → ログイン画面になる(E2E 実測 2026-09-05: wheel だけ 150 秒 →
  次のクリックで追い出し)。そこで `GET /api/v1/auths/` を 1 本だけ出してヘッダを運ぶ。
  条件:
  - **人が操作した瞬間**にだけ出す(`noteInteraction` から呼ぶ)。**無操作なら 1 本も
    出ない**ので「ポーリングでは延命しない」は保たれる。他タブの操作を取り込んだだけでは出さない。
  - **サーバの期限の残りが `TTL − 刻み`(= BE が再発行する境目)以下**のときだけ
    (2026-10-20 までは `TTL / 2` — 下記「延命の刻み」)。残りは BE が操作ヘッダ付きの応答に載せる
    `X-Nucllei-Session-Expires` で測る(同一オリジンの応答の `Date` との差で手元の時計に直す =
    サーバとの時計ずれに影響されない)。操作だけが続く間は刻みに 1 本(`30m` でも `1d` でも
    1 分に 1 本まで)。普通に API を叩いている間はその応答で期限が延びるので追加リクエストは出ない。
  - 期限が分からない(期限ヘッダを返さない古い BE・このタブでまだ 1 本も運べていない)ときは、
    最後にヘッダを運んでから **TTL/4** で出す(保守的なフォールバック)。
  - 連打防止: 直前の keepalive から刻みの間は出さない(2026-10-20 までは TTL/8。時計のずれ・
    TTL 変更直後などで BE が再発行しなかったときに、キーを叩くたびに出続けないように)。
- **半減期の境界の見落とし(#154、2026-10-07)。** 以前の条件は「最後にヘッダを運んでから
  半減期」という FE の自前の時計だった。年齢 TTL/2 の直前(例 `20m` で 9:50)に操作ヘッダ付きの
  API が出ると、BE は残りが半分を超えているので**再発行しない**のに FE の時計だけ 0 に戻る。
  次の keepalive は年齢 19:50 以降になり、延命の窓が 10 秒(最悪 0)しかない。窓を外すと
  20:00 で cookie が切れ、操作中でも次のクリックで `/auths/` 403 → `/auth`(staging で実発生)。
  「ヘッダを運んだ ≠ 延命された」ので、FE は自分の時計ではなく**サーバが返す実際の期限**で判断する。
  期限が分かっていれば窓は常に TTL/2、フォールバックでも TTL/4 ある。
- **アイドル判定はタブ間で共有する(#154)。** cookie(= サーバのセッション)はタブ間で 1 つなのに、
  最終操作時刻がタブごとだと、放置タブ B の FE タイマが TTL で切れて signout(#137)し、
  **作業中のタブ A を道連れにする**。最終操作時刻と最後に知ったサーバの期限を localStorage
  (`nucllei:idle:last-interaction` / `nucllei:idle:session-expires`、epoch ミリ秒)に書き、
  アイドル判定・keepalive の判断の**直前に読み直す**(`storage` イベントを待たない =
  取りこぼしが無い。signout 直前の再確認もこれで兼ねる)。アイドル扱いは**どのタブでも**操作が
  無いときだけ。書き込みは 1 秒に 1 回へ間引く。localStorage が例外を投げる環境では共有せず
  タブ単独で判定する(従来どおり)。全タブ放置なら従来どおり切れ、他タブはサーバ側が切れた後の
  最初のリクエスト(401 → B2)か自分のタイマで追従する。
- **keepalive を FE タイマの tick から出してはいけない**(2026-09-05 に実測で判明し、
  操作イベント側へ移した)。tick で出すと操作から最大半減期ぶん遅れて再発行が走り、
  **サーバの期限が FE のアイドル期限を追い越す**。すると FE が `/auth` へ逃がしても
  `/auth` がまだ生きているセッションを見てアプリへ戻し、**前の人の画面が復活する**
  (実測: 操作 t=1.6s → tick で延命 t=26.2s → FE 追い出し t=71.2s → `/auth` が `/` へ戻した)。
  操作した瞬間に出せば `lastActivitySentAt <= lastInteractionAt` が常に成り立ち、
  サーバのセッションは必ず FE の期限**以前**に死ぬ。
  #154 の後も崩れない: 期限ヘッダが決めるのは「操作の瞬間に**出すかどうか**」だけで、「**いつ**」は
  操作の瞬間のまま。タブ間共有では FE の期限 = 全タブの最終操作 + TTL、keepalive が作るサーバの
  期限 = そのタブの操作時刻 t + TTL で、t は共有の最終操作時刻に入るので t + TTL ≤ FE の期限。
- **`GET /api/v1/auths/` は token を再発行しない**(2026-09-05 に撤去)。呼び出し元は
  アプリ起動時・プロフィール更新後・ログイン直後の 3 箇所 = **ページ読み込み時**だけなので、
  再発行を残すと「タブを開き直せば無期限に延命できる」離席対策の穴になる。
  応答から `token` / `token_type` も落とした(#70 B1 で cookie 一本化済み・FE 未参照)。
  `expires_at` は現セッションの `exp` を返す。
- **絶対上限(何時間使っても必ず切る)は作らない**(設定が増えるため不要と判断 — ken、2026-09-05)。

回帰テスト: `nucllei/test/apps/webui/routers/test_session_idle_timeout.py`(BE。期限ヘッダは
`test_expires_header_*` / `test_no_expires_header_*`、延命の刻みは `test_renews_past_the_step_*` /
`test_no_renewal_while_younger_than_the_step` / `test_step_scales_down_*` / `test_session_renew_step_seconds`)、
`src/lib/apis/fetch.test.ts`(FE。#154 は「延命の境界」「複数タブ」の 2 ブロック — 旧実装では 7 件が
落ちることを確認済み。延命の刻みは「延命の刻みと残り時間」ブロック)。

### アイドル失効時はサーバ側セッションも落とす(#137、2026-10-03)

上の「keepalive を tick から出さない」は**不変条件**(`lastActivitySentAt <= lastInteractionAt`
⇒ サーバ期限 ≤ FE 期限)で安全を保っていた。将来 keepalive の発火点に手が入ってテストを
すり抜けると、「FE が `/auth` へ逃がしたのにサーバ側がまだ生きていて `/auth` がアプリへ戻す =
共有 PC で前の人の画面が残る」が静かに復活する。それを**構成的に**塞いだ:

- **FE**(`fetch.ts` `handleSessionExpired('idle')`): アイドルタイマで切ったときは、`/auth` へ
  ハード遷移する**前に** `GET /api/v1/auths/signout` を叩く(`credentials: 'include'`、
  `keepalive: true` = 遷移でページが消えても完走)。応答を待ってから遷移するが、
  **上限 2 秒**(`IDLE_SIGNOUT_WAIT_MS`)・失敗時も必ず遷移する(ネットワーク断で画面が残る方が悪い)。
  401/403 起因の失効(`'unauthorized'`、既定)ではサーバ側は既に死んでいるので叩かない。
- **BE**(`routers/auths.py` `signout`): 認証依存を持たない(期限切れでも token 無しでも 200)。
  `delete_cookie` の属性を発行箇所(signin / 延命 middleware)と同じ `httponly` / `samesite` /
  `secure` にした — ずれていると secure/samesite の違いで消えない配備がある。
- **設計の前提**: セッションは stateless JWT で、`exp` がそのままアイドル期限(操作で再発行)。
  `exp` を過ぎた token はサーバが元々拒否する(`test_expired_token_is_rejected_not_renewed`)。
  なので「サーバ側セッションを落とす」= ブラウザの httpOnly cookie を消すことで足り、
  失効リスト(DB)は作らない。ブラウザの外へ持ち出した token は `exp` まで生きる —
  これは #137 の対象外(共有 PC の離席 = ブラウザに残る cookie が問題)。
- 回帰: BE `test_signout_needs_no_valid_session` / `test_signout_clears_the_cookie_with_matching_attributes`、
  FE `fetch.test.ts` の「アイドル失効では /auth へ行く前に signout …」以下 4 件。

### 残り時間のリング(右上、2026-10-20)

`SESSION_IDLE_TIMEOUT` が有限のとき、アプリ((app) layout = チャット・ワークスペース)の右上に
アイドル失効までの残り時間をリングと文字(`12:34` / `1:11:55`)で出す(ken 指示)。

- **値は失効の判定と同じ関数**: `fetch.ts` の `getIdleRemaining()` = 全タブ共有の最終操作時刻 + TTL − 今
  (読む直前に localStorage を取り込む)。どのタブでも同じ値になり、どこかで操作すれば全タブで満タンに戻る
  (表示の追従は 1 秒以内。共有の書き込みは従来どおり 1 秒に 1 回へ間引き)。
- **判定を残りちょうどに予約する**: 以前は `setInterval(15 秒)` で、期限を過ぎてから最大 15 秒遅れて
  切れていた。`setTimeout(min(15 秒, 残り))` の連鎖にし、リングが 0 になる瞬間に signout → `/auth` が走る
  (vitest「残りが 0 になる瞬間に…」、Playwright の clock で 1 ms 前は残り表示・0 で遷移を確認)。
  切れるのが早まる方向にしか変わらない(サーバ期限 ≤ FE 期限の不変条件はそのまま)。
- **見え方**(`src/lib/utils/idle-ring.ts`、純関数でテスト): 普段は隠す / カーソルを重ねる(またはキーボードの
  フォーカス)で出す / 残りが TTL の 1/3 を切ったら常に出す / 最後の 1 分(TTL が短ければ 1/3)は赤。
  ホバーの無い端末(タッチ)は 1/3 を切ってからだけ出る(隠れている間は的も置かない)。
- **重ねるだけでは延命しない**: 「操作」は従来どおり keydown / pointerdown / click / touchstart / wheel だけ。
  リングの**クリック**は操作として数える(`noteInteraction` — 他のクリックと同じ経路。サーバ側も既存の
  keepalive の条件に乗るだけで、延命の仕組みは増やしていない)。
- **右上の部品と重ねない**: リングは `fixed`(画面右端から 10px)。占める幅を CSS 変数
  `--idle-ring-reserve`(画面右端からの px)で公開し、右上に接する行が空ける — chat の `Navbar`
  (右端の余白 10px を差し引く)・workspace の上部メニュー・大画面の右ペイン(`ChatFiles` の見出し、
  `reserveTopRight`)。隠れている間に空けるのはリング 1 個分(36px、ホバーの的)、常に出る段階では文字の分も。
  ホバーで出した文字はツールチップと同じく一時的に左の部品へ重ねる(的はリングだけなので、離れれば消える)。
- 導入時の既知の限界(「リングは FE の期限だけを示し、サーバ側は最大 TTL/2 早く切れうる」)は、
  同日の「延命の刻み」(次節)で解消した。リングはサーバの期限との早い方を出す。

### 延命の刻み — サーバの期限を FE の期限に追従させる(ログアウトの暴発、2026-10-20)

**症状**: TTL 20 分、token 発行 0:00 → 9:00 に操作 → 12 分席を立って 21:00 に戻る。リングは残り 8 分
(9:00 + 20 分)なのに、次のクリックで 401 → ログイン画面。BE の再発行と FE の keepalive がどちらも
「残りが半分以下」を条件にしていたため、**残りが半分より多いときの操作はサーバの期限を延ばさない**。
サーバ側は「最後の操作 + TTL/2〜TTL」で切れ、実効のアイドルタイムアウトが TTL/2〜TTL でぶれていた
(「ログアウトが暴発する」の一因。リングで見えるようになった)。

**直し方**(ken 了承):

- **BE**(`utils/auth.py` `mark_session_renewal`): 操作ヘッダ付きのリクエストで、token の年齢が
  **刻み = `min(60 秒, 寿命/10)`**(`session_renew_step_seconds`)以上なら再発行する。
  - 60 秒: サーバの期限が FE の期限より早く切れるずれの上限で、操作が続く間の再発行・keepalive は
    1 分に 1 本まで(多人数配備でも 1 人 1 分 1 本の `GET /auths/` — 操作が無ければ 0 本)。
    実用の TTL(数十分〜1 日)ではずれは数 %。
  - 寿命/10: TTL が 10 分未満(検証用の 1〜数分)でも、ずれを TTL の 1 割に抑える
    (`min(60 秒, TTL/2)` だと TTL 60 秒で 30 秒 = 半分ずれ、リングが操作で半分までしか戻らない)。
- **FE**(`fetch.ts` `renewStepMs` = 同じ式): keepalive は「操作の瞬間、サーバの残りが `TTL − 刻み`
  以下」で出し、連打防止の間隔も刻み。結果、**サーバの期限は FE の期限(最後の操作 + TTL)から
  刻み以内**に収まる。
- **残り時間 = FE の期限とサーバの期限の早い方**(`idleSnapshot` → `getIdleRemaining`)。リングも
  失効の判定も同じ値なので、リングはサーバに無い時間を出さず、0 になる瞬間に signout → `/auth`。
  操作の直後(token の年齢が刻み未満で再発行されない)は最大で刻みぶん満タンより少なく出る
  (20 分なら 19:0x〜20:00)。
- **応答待ち**: keepalive を出してから延びた期限を知るまでは、サーバの期限を使わない(最大 10 秒、
  `EXTENSION_PENDING_MS`。出した時刻は `nucllei:idle:extension-requested` でタブ間共有)。リングは
  操作の瞬間に満タンへ戻り、残り数秒でリングを押したとき応答待ちの間に切ることもない。応答が
  10 秒届かなければサーバの期限で判断する(延命が届かなかった = サーバ側は切れている)。
- **監視開始時に今の期限を知る**(`seedServerExpiry`): `$user.expires_at`(`GET /auths/` か signin の
  応答 = 今の cookie の exp)を、localStorage に残る期限より遅ければ採る。前のセッション(ログアウト →
  再ログイン)の早い期限を引きずってログイン直後に追い出さないため。同じセッションで張り直した
  (管理画面で TTL を保存した等 = `$user` の値の方が古い)ときは延命後の期限が残る。`expires_at` が
  無い(期限なしで発行された token)ときは知っている期限を捨てる。ページ読み込みは FE の期限の起点に
  なるがサーバの期限は延ばさない(`GET /auths/` は再発行しない)ので、読み込んだだけで席を立つと
  リングはサーバの残りを出す。
  既知の残り: 管理画面で TTL を**縮めた**直後に別セッションでログインし直すと、前の(遅い)期限が
  勝ち、次の操作ヘッダ付きの応答までは FE の期限で判断する(従来どおりの挙動に落ちるだけ)。
- **後から運ぶ操作ヘッダを刻みで打ち切る**(`withActivityHeader`): 操作から刻みを過ぎたリクエストには
  ヘッダを付けない。BE は運んだ時刻 + TTL で再発行するので、遅れて運ぶとサーバの期限が FE の期限を
  追い越す(従来は #137 の signout だけが頼り)。打ち切っても失うものは無い — 延命が要る操作なら
  その瞬間に keepalive が出ている。
- **不変条件(サーバ期限 ≤ FE 期限)は不変**: keepalive を出す瞬間は今までどおり操作の瞬間だけで、
  変わったのは「出すかどうか」のしきい値と間隔。残り時間がサーバの期限を見るのも読むだけ。
- 回帰: BE `test_renews_past_the_step_even_with_more_than_half_remaining`(旧規則で落ちることを確認)ほか、
  FE `fetch.test.ts`「延命の刻みと残り時間」(9:00 の操作 → 21:00 に戻る、を旧実装で再現 = 落ちる:
  21:00 にリング残り 8 分・サーバ残り −60 秒)。E2E は共有 dev BE(無期限)の設定を変えずに、ブラウザ側で
  TTL 120 秒を見せ、BE の刻み延命を Playwright の route で模して実バンドルを確認(読み込み時のリングが
  サーバの残り 0:28、クリックで 1:59、刻み未満の操作で keepalive 0 本・刻み超で 1 本、放置でリングの
  0 と同時に signout → `/auth`、サーバ切れのリクエスト 0 本)。

### cookie はセッション専用、API キーはヘッダ専用(#144、2026-10-04)

旧実装(upstream 由来)は `Authorization` が無いとき cookie `token` を拾い、`sk-` 始まりなら
API キーとして認証していた — 人の cookie セッション(httpOnly・アイドル延命の対象)と
API キーが 1 つの cookie 名に同居していた。使う側は無い(FE は JWT を cookie に、
llens の sync は `Authorization` ヘッダ)ので経路を分けた:

- `get_current_user`(`utils/auth.py`): cookie 由来の `sk-` は API キー分岐に入れず JWT 検証へ落とす
  = 不正 token と同じ 401。API キーは `Authorization: Bearer sk-…` のみ。
- `main.py` の middleware: cookie の `sk-` は `request.state.token`(tool server への転送等)に載せない。
  `/api/config` は元々 JWT として decode するだけなので `sk-` は未ログイン扱い(変更なし)。
- アイドル延命(cookie セッション限定)は無変更。
- 回帰: `nucllei/test/apps/webui/routers/test_auth_token_channels.py`。

## 方針(fork.md より)

- 認証(誰がログインできるか)と認可(group 権限)は分離して扱う。
  認可の真実源は `permissions.md`(#66 の 3 層モデル。旧・交差設計 issue #4 は前提誤りで close)。
- OAuth/OIDC/LDAP のコードは **削除しない**(off のまま温存。
  他病院展開で必要になる可能性があるため、投資も削除もしない)。
