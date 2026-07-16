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

## ブートストラップと運用

1. 初回セットアップ: 最初に signup したユーザーが **admin** になる。
   その瞬間に `ENABLE_SIGNUP` が自動で `False` に永続化される(auths.py)。
2. 以後のユーザー発行は **管理者(事務)が管理画面から作成**
   (`POST /api/v1/auths/add`、GUI: 管理画面 > ユーザー)。
3. signup を再開放する運用も可能だが、その場合も新規ユーザーは
   `pending` で承認待ちになる。

## 方針(fork.md より)

- 認証(誰がログインできるか)と認可(group 権限)は分離して扱う。
  認可は交差設計 issue #4 側。
- OAuth/OIDC/LDAP のコードは **削除しない**(off のまま温存。
  他病院展開で必要になる可能性があるため、投資も削除もしない)。
