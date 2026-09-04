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
| 席を離れた後の**セッション乗っ取り** | ◯ | `JWT_EXPIRES_IN`(既存 env、下記) |

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
| `JWT_EXPIRES_IN` | `-1`(無期限) | `4h` 等 | RuntimeConfig。`s/m/h/d/w` 表記。cookie の `expires` と JWT の exp の両方に効く(`nucllei/routers/auths.py`) |
| `WEBUI_SESSION_COOKIE_SECURE` / `WEBUI_AUTH_COOKIE_SECURE` | `false` | HTTPS 配備なら `true` | `nucllei/env.py` |
| `WEBUI_SESSION_COOKIE_SAME_SITE` / `WEBUI_AUTH_COOKIE_SAME_SITE` | `lax` | `strict` も可 | 同上 |

- **「ログイン状態を維持する」(remember me)チェックボックスは fork に存在しない。**
  抑止対象そのものが無い。
- **トークンは httpOnly cookie に一本化済み**(#70 B1、`auth/+page.svelte` のコメント)。
  `localStorage` に JWT を残さないので、共有 PC で「ブラウザに残る」のは cookie のみ。
  `JWT_EXPIRES_IN` を有限にすれば端末に残り続けない。
- **アイドル(無操作)タイムアウトの実装は無い。** `JWT_EXPIRES_IN` は発行時刻からの絶対期限
  であって無操作検出ではない。加えて `GET /api/v1/auths/` が呼ばれるたびに token を再発行して
  cookie を更新する(`auths.py:get_session_user`)ため、実質は「最後のセッション取得からの期限」に近い。
  厳密なアイドルタイムアウトが要るなら別途実装が必要 — **未実装**。

## 方針(fork.md より)

- 認証(誰がログインできるか)と認可(group 権限)は分離して扱う。
  認可の真実源は `permissions.md`(#66 の 3 層モデル。旧・交差設計 issue #4 は前提誤りで close)。
- OAuth/OIDC/LDAP のコードは **削除しない**(off のまま温存。
  他病院展開で必要になる可能性があるため、投資も削除もしない)。
