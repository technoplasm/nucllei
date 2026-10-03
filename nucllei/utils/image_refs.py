"""「参照 → バイト」の共有 resolver(#139)。

モデルに見せる画像参照は 2 つの形しか無い: `file_id`(ユーザー添付 = Storage)と、
tool の戻り値に literal で出る **参照文字列**(URL または sandbox の fs パス)。
後者の解決規則をここ 1 箇所に置く — feed 側の tool(配備層の feed_image / fetch_url、
nucllei プロセス内で動く)と、送信直前に image_url を data URL 化する
`utils/file_images.py`、URL を sandbox に取り込む builtin の `send_to_workspace(url=)`、
sandbox のファイルを利用者に見せられる形にする `fetch_from_workspace(path)` が
これを呼ぶ。規則を 2 箇所に持つと、tool が「見えた」と
判断したものが送信時に落ちる(またはその逆)ずれが生まれる。

## 参照の規則

定義はここ。英語の正文は README_API.md §3.8「Reference rules」(同じ表を持つ)。
`_memos/file-handling.md` は要約とここへのポインタだけを持つ。

**tool の引数は名前で解釈が決まる。値の中身を見て別の引数の意味に読み替えない。**
モデルは受け取った形のまま渡す(読み替えない・origin を補わない):

- **`url` = ブラウザが開ける URL**。スキーム付きの絶対 URL(`http(s)://…`)、または
  **同一サーバの相対 URL**(「今のサーバ(origin)の URL」として、サーバが実際に持つ経路で
  解決する — `/ext/{rest}` → `resolve_url`、`/api/v1/files/{id}/content` →
  `file_id_of_url` で id を取り、呼び手が自分の権限検査で読む)
- **`path` = sandbox の fs の絶対パス**(`/workspace/…` / `/ext/…`)→ `resolve_path`。
  **相対パスは受けない**(2026-10-03 — `path` の形は 1 つ。「相対は /workspace から」と
  解くと `path="https://x/y.html"` が `/workspace/https:/x/y.html` になる)
- **`file_id` = 添付・生成物の id** — ここでは扱わない(Files / Storage と権限検査は呼び手)

`url` に sandbox のパスが来ても、`path` に URL が来ても、その引数の意味のまま解決して
失敗する(`unsupported_form`)。救済はしない — 取り違えを吸収すると、名前が意味を
持たなくなる。

**エラーに添える固定文**: 形の誤りには、値を調べて文面を出し分けず、その引数が受ける形を
固定の文で添える(下の定数 `URL_ARG_FORM` / `PATH_ARG_FORM` / `WORKSPACE_PATH_ARG_FORM` /
`FILE_ID_ARG_FORM`。builtin と配備層の tool が同じ文を import して使う)。行き先は
tool ごとに書かず、**どの tool でも同じ 1 文** `REF_ARGS_RULE` として持つ。組み立ては
全 tool 共通:

- url が URL として解決できない:
  `ERROR: <値> は URL として解決できない。` + `URL_ARG_FORM` + ` ` + `REF_ARGS_RULE`
- path が sandbox のパスの形でない:
  `ERROR: <値> は sandbox のパスの形でない。` + `PATH_ARG_FORM`(workspace だけを扱う tool は
  `WORKSPACE_PATH_ARG_FORM`)+ ` ` + `REF_ARGS_RULE`
- file_id が引けない:
  `ERROR: file_id <id> のレコードが無い、またはアクセス権が無い。` + `FILE_ID_ARG_FORM`
  (「無い」と「権限が無い」を言い分けない — 存在の有無を漏らさない)

解決結果(`RefError.code` / `reason`)に基づく言い分け(存在しない / サイズ超過 / redirect /
上流 status / 届かない / 許可先の外 / 配信範囲外)は別で、各 code の文面で言う(値は見ない)。

**保存済みの参照文字列**(`context_images` のエントリ・image_url の url — 引数名を持たない)
だけは、先頭で機械的に振り分ける。振り分けは `stored_ref_kind` 1 箇所(モデルが引数を
選ぶ場面ではない)。`resolve_ref` はその上の薄い dispatcher で、履歴の組み立て
(`content_blocks.py`)・完了通知の添付(`execution_resume._notice_files`)も同じ関数で
分ける — 独自の先頭判定を持たない:

| 先頭                         | kind      | 意味                               | 解決                           |
| ---------------------------- | --------- | ---------------------------------- | ------------------------------ |
| `/api/v1/files/{id}/content` | `file_url`| file(添付・生成物)の配信 URL(相対、または絶対 = host 不問)| 呼び手(`file_id_of_url`、下記)|
| `http://` `https://`         | `url`     | 絶対 URL                           | `resolve_url`                  |
| `/ext/`                      | `url`     | 同一サーバの URL(= sandbox の RO マウントと同じ文字列。保存済みは URL として読む)| `resolve_url`   |
| `/workspace/`                | `path`    | sandbox の fs(ユーザーの workspace)| `resolve_path`               |
| `/` 始まりでない裸の文字列   | `file_id` | file の id(`__feed_image__(file_id)` が積む形)| 呼び手(`/api/v1/files/{id}/content` に組む)|
| それ以外(他の `/…`・空)    | —         | —                                  | `unsupported_form`             |

FE の `chat-messages.ts`(`contextImageUrl`)は「`/` 始まりと `http(s)://` はそのまま、
それ以外は file id」で、この表と同じ結果になる(`/` 始まりの中の区分は送信直前の
BE が行う)。

**URL の解決**(絶対・相対とも `_route_url` 1 箇所):

- path が `/ext/{rest}` なら **`EXT_DIR`の `{rest}` をローカル読み**する
  (`/ext/cage/static/X` ⇔ `ext/cage/static/X`。設定は無い — 配備が `EXT_DIR` に置いた
  (Docker なら `/ext/<name>` に volume した)ものがそのまま配信される。ken 決定
  2026-10-03。同日の朝はコンテナの `/mnt` 固定だったが、nucllei は docker 専用ではなく、
  host 直起動で host の `/mnt` を配信してしまうので `DATA_DIR` の中に改めた)。絶対 URL は
  **host を問わない** — 与えられた host には接続しない。相対 `/ext/{rest}` は「今のサーバの
  URL」= 同じ置き場。閉じ込めは sandbox パス `/ext/{rest}` の読み替えと同じ
  (`_read_confined`: resolve 済みの実体が `EXT_DIR` の中の通常ファイル。外へ出る
  symlink・ディレクトリ・不在は `not_found`)。
- `/ext/` と `/ext/{dir}/`(rest が空・ディレクトリ)も形としては正しく、`not_found`
  (一覧は出さない — ゲートも 404)。
- `/ext/` 形でない絶対 URL は **一般の URL** として、そのホストに直接取りに行く
  (下記「一般の URL」)。行き先で絞らない — 唯一取りに行かないのは **このサーバ自身
  (と同居するサービス)** だけ(`unknown_upstream` reason `server_self`)。
- 2026-10-03 までは EXT_UPSTREAMS(name ごとの http 中継 / `file://` root)・EXT_FILE_SCOPES
  (root からの相対の配信範囲)・EXT_PUBLIC_BASES(院内 web の原本 URL のルート → その name の
  backend)の 3 変数があった。`url=` の一般 URL がどこでも取れるようになり、http 中継と原本
  ルートの対応は要らなくなったので、同日に設定ごと無くした(ken 決定)。URL の形は変わらない
  (`/ext/cage/static/X`)。

**一般の URL**(2026-10-03、ken 決定)。`url=` はスキーム付きの `http(s)://` URL なら
**どこでも**取りに行く — 院内でもインターネットでも。「閉域だから外には届かない」は
prompt / skill に書く文であって、tool のゲートではない(届くかどうかは配備のネットワークが
決める)。同日に一度入れた院内の範囲(env EXT_INTRANET_CIDRS。宣言した CIDR の中だけ取る)は
**同日に撤回**した — 行き先で機能を絞っていたし、固定 CIDR の拒否(docker 既定帯 172.17/16)は
本番の bridge が 10.106.x なので効かず、守れないのに正当な院内を巻き込みかねなかった。

| 名前空間                                   | 誰が決める       | 解決                                          | redirect                 |
| ------------------------------------------ | ---------------- | --------------------------------------------- | ------------------------ |
| `/ext/{rest}`(相対・絶対)                | —(`EXT_DIR` の中身)| `EXT_DIR/{rest}` をローカル読み(与えられた host は見ない)| —(ローカル)      |
| 一般の URL — `/ext/` 形でない絶対 URL      | —(どこでも)    | ホストのアドレス(IP リテラルはそのまま、名前は getaddrinfo)が自分自身でなければ **そのアドレスに固定して** 直接取得 | 3 回まで辿る(各ホップで同じ判定)|

一般の URL は一般の Web と同じように辿れる(ページのリンクはそのページ自身の URL を基準に
解決し、redirect は追う)。

- **ホストは IP リテラルが主経路**(閉域の本番 MiNet には DNS が無く、院内の URL は
  `http://10.x.x.x/…` の形が普通)。リテラル(v4 / v6、`[::1]` の括弧付き)は名前解決せずに
  そのまま判定して接続する。ホスト名のときは `getaddrinfo`(= `/etc/hosts` も効く)で
  引き、引けなければ `upstream_unreachable`(reason `unresolvable` — tool は「名前を解決
  できない。DNS が無い環境では IP で指定」と案内する)。
- **接続先は解決したアドレスに固定する**(`ext_upstreams.pin_address`)。名前を引いて得た
  アドレスが自分自身でなければその IP に接続し、Host ヘッダ / SNI には元のホスト名を使う —
  httpx に名前で繋がせると 2 度目の解決(DNS rebinding)で自分自身へ向けられる。外のアドレスと
  loopback を同時に返す名前は怪しいので取らない。
- **自分自身の集合は実行時に求める**(`ext_upstreams.self_addresses`): 固定で分かる
  「正当な行き先になり得ないアドレス」(loopback・unspecified・リンクローカル = クラウドの
  メタデータ・multicast / 予約)+ この環境の自分のインターフェースのアドレス・既定
  ゲートウェイ(docker bridge のホスト側)・`host.docker.internal` の解決結果。private 帯
  (10/8 等)は本物の院内なので見ない。限界は `discover_self_addresses` の docstring。
- **redirect**: 3xx + Location は最大 3 回辿る(`MAX_REDIRECTS`)。転送先は URL として
  同じ規則で解決し直す(IP リテラルはそのまま、名前は引いてから判定)— 一般の URL なら辿る
  (またアドレスに固定)、`/ext/{rest}` 形なら `EXT_DIR` をローカル読みする(そこから先の
  redirect は無い)。転送先が自分自身なら `upstream_status`(転送を返した 3xx の
  status、reason `redirect_self`)で止める。転送先が URL として組めない(http(s) でない・
  形が不正・Location に制御文字)なら `upstream_status`(3xx の
  status、reason 無し = 辿らなかった redirect)。4 回目の redirect は `upstream_status`
  (reason `too_many_redirects`)。
- 上限(`max_bytes`)・content-type(上流ヘッダ)・404 → `not_found`・2xx 以外 →
  `upstream_status`・接続失敗 → `upstream_unreachable`。cookie / 認証ヘッダは送らない
  (共有クライアント)。
- `route_of_url` は一般の URL を `UrlRoute(kind="general", name=None, origin=…, rel=…)`
  で返す。純粋な関数なので **DNS は引かない** — 「`/ext/` 形でない絶対 URL」= 一般の
  規則で解決される候補、という意味。実際に自分自身でないかは `resolve_url` が決める
  (`unknown_upstream` reason `server_self`)。配備層が `resolve_url` の成功後にリンクの
  ルートを組む用途では、一般の URL のリンクはそのページ自身の URL(`{origin}/{rel}{query}`)
  を基準に解決する(通常の Web と同じ)。
- `mounted_path_for_url` / `url_for_mounted_path` / `/ext` ゲートは無関係(`EXT_DIR` の話)。
- **`/api/v1/files/{id}/content`**(このサーバのファイル配信口。相対、または絶対 — host は
  `/ext/` と同じく問わない: 与えられた host には接続せず Storage から読むので)は、URL の
  ルーティングとしては `file_id_of_url` が id を取り出すところまで。バイトの読み出しは
  per-user の権限検査が要る(`resolve_url` は user を取らない — `/ext/` の公開範囲は
  admin 管理で per-user ではない)ので、呼び手が自分の検査で読む: 送信直前 inline
  (`file_images.py`、読み取り権限)と `send_to_workspace(url=)`(所有者)。`resolve_url` に
  そのまま渡せば `unsupported_form`。**fragment は末尾の `#page=N` だけが意味を持つ**
  (`split_page`)。それ以外の fragment は無視して同じ file に当てる(fragment は
  サーバに送られない、ブラウザ側だけの情報 — どの tool でも同じ)。
- **URL がどこに属するか**だけが要る呼び手(配備層の fetch_url がリンク一覧のルートを
  組む)は `route_of_url` を使う — 同じ `_route_url` の結果を返すだけで、読まない。

**sandbox の fs パス**:

- `/ext/...` — sandbox(jail)の read-only の参照用データ(#138。2026-10-03 までは `/mnt`)。
  これは **sandbox 側のパスの形** で、nucllei は同じ物を `EXT_DIR/...`に
  持つ規約なので、そこを読み替えて読む(`local_path_for_sandbox_path`)。resolve() して
  `EXT_DIR` 内でなければ読まない。nucllei 自身の `/ext` や `/mnt` は見ない(host 直起動で
  host の fs を晒さないため)。URL の `/ext/{rest}` と同じ文字列だが、`path=` に来たものは
  fs のパスとして(percent-decode せずに)読む — 引数名が読み方を決める。
- `/workspace/...` — sandbox の `/files/read`(呼び出しユーザーの workspace に閉じる)。
  jail 内で作ったものをモデルが見るだけなら、Files に登録する必要は無い。

**URL と sandbox のパスの橋渡し**(同じ実体が両方の名前を持つとき)。モデルは参照を
読み替えない — 読み替えが要る場面は tool が **解決結果** に基づいて橋渡しする(値の形では
なく、解決した先の実ファイルがどこにあるかで決まる):

| 向き                          | 関数                    | 使う tool                    |
| ----------------------------- | ----------------------- | ---------------------------- |
| URL → sandbox で開けるパス    | `mounted_path_for_url`  | `send_to_workspace(url=)`    |
| sandbox のパス → 見せられる URL | `url_for_mounted_path` | `fetch_from_workspace(path)` |

どちらも **`EXT_DIR` 配下の実ファイルのときだけ** 値を返す — `/ext/{rest}` ↔
`/ext/{rest}` は **同じ文字列** で、`EXT_DIR/{rest}` の実在の確認だけ(rest は URL 側が
percent-encoded、fs 側が decode 済み — なので `url_for_mounted_path` が返す URL は、文書の
本文にある生の日本語 URL とは文字列が一致しないことがある。同じファイルの別表記)。sandbox の
`/ext/X` が nucllei の `EXT_DIR/X` に無ければ「配信されていない」だけ(`fetch_from_workspace`
は読めれば添付、読めなければ `not_found`)— 配備は配信したくない物を nucllei 側の `EXT_DIR` に
置かないことで渡さない(2026-10-03 までの「配信範囲の外は添付もしない」
(`mounted_path_withheld`)は設定ごと無くした)。
利用者に見せられるのは URL だけ(sandbox のパスはブラウザでは開けない)。

**相対 `/ext/…` の経緯**: #139(2026-09-07)は「"/" 始まり = sandbox の fs だけ、`/ext/…` は
受けない(失敗 `relative_ext`)、URL は必ず絶対」と決めていた。理由は取り違え — URL と
fs パスが同じ "/" 始まりで同居すると、モデルが `/ext/` を fs と誤認したり fs パスを URL に
書き換えたりする。2026-10-03 に覆した(ken 決定): 文書の図は相対 `/ext/cage/static/X` の
ままモデルに渡す。生成物の本文リンク `/api/v1/files/{id}/content` が相対で運用できているのと
同じで、相対なら origin や http/https が変わっても表示が壊れず、origin を補う仕組み
(リクエストヘッダから組む・組めないときの fallback・サブエージェント経由で別ホストになる
問題)が要らなくなる。`/` 始まりが URL か sandbox のパスかは **引数名**(`url` / `path`)と
文脈(markdown のリンク・画像か、コードの中か)で決まる。既存の絶対 URL
(`https://<任意の host>/ext/{rest}`)は引き続き受ける。

**File を発行しない**: PDF は送信直前にページを画像化して data URL にする
(`rasterize_pdf_page`)。「見るために置く」ものが Storage / Files に永久に残る
経路(旧 feed_image の `_register_png`)を無くすのが目的の 1 つ。ページ指定は参照の
`#page=N`(1-origin)に載せる(`split_page` / `with_page`)。URL でも同じ — 末尾の
`#page=N` だけを見るので、URL 自身の fragment とは衝突しない(`#page=N` は常に
一番後ろに付く。`https://h/x.pdf#page=3` → base `https://h/x.pdf`)。

**fail closed**: 境界(root の外・サイズ上限・不達・不明な種別)はすべて失敗 +
log.warning。`resolve_ref` は失敗理由を `RefError`(code + 人向け detail)で返し、
`load_ref` はそれを捨てて None にする薄い wrapper — 呼び手が「リンク切れ」と
「サイズ超過」を言い分けられるように(実走で 32.6 MB の PDF が上限 25 MB に当たり、
モデルが「リンク切れ」と誤報した)。tool 側に on/off の valve は置かない — 境界を持つのは解決層だけで、
tool のスイッチはモデルが届く範囲を変えない(意味の無い設定になる)。
"""

import asyncio
import base64
import io
import logging
import mimetypes
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import quote, unquote, urljoin, urlsplit

import httpx

from nucllei.env import EXT_DIR as _ENV_EXT_DIR
from nucllei.env import FETCH_MAX_BYTES, SELF_ADDRESSES, SRC_LOG_LEVELS
from nucllei.utils.ext_upstreams import (
    MAX_REDIRECTS,
    get_ext_client,
    has_path_traversal,
    pin_address,
    self_addresses,
)

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])

# 参照用データ(read-only。#138 / #139)の **root は 2 つ** あって別物(2026-10-03、ken 決定。
# 同日の朝は nucllei 側も sandbox 側もコンテナの `/mnt` 固定だった):
#
# - `EXT_DIR`(`nucllei.env`。DATA_DIR と同じ作法で、既定はインストールルート直下の `ext/`、
#   Docker image は Dockerfile が `/ext` にする)— **nucllei 自身がローカルに読む・配信する root**
#   (nucllei の fs のパス)。ここの `X` がそのまま URL `/ext/X` で配信される。Docker なら volume を
#   `/ext/<name>` に bind(sandbox と同じ行)、host 直起動なら `ext/` にファイルか symlink を置く
#   (root 不要)。固定の `/mnt` を捨てた理由は nucllei が docker 専用でないこと — host 直起動では
#   `/mnt` に置くのに root が要るし、何より host の `/mnt`(NFS 等)がログインユーザー全員に黙って
#   配信されてしまう。`EXT_DIR` も一度置いたが、書き換わる状態の置き場と読み取り専用の配信物を
#   同じ木に混ぜない・コンテナ内のパスを sandbox と揃える、で `EXT_DIR` に(ken 2026-10-03 夜)。
# - `SANDBOX_MOUNT_ROOT` = `/ext` — **sandbox(jail)側の read-only mount の置き場**
#   (`sandbox/app/main.py` の RO_MOUNT_ROOT)。モデルが `path=` に書く形(`/ext/…`)は
#   こちらで、nucllei の fs のパスではない(nucllei 自身の `/ext` は見ない)。URL の prefix と
#   同じ `/ext` にしてあるので、**sandbox のパスと URL は同じ文字列** — `/ext/cage/static/X` は
#   jail で開いても、ブラウザで開いても、tool の `url=` / `path=` に渡しても同じ物。これが
#   唯一の意図した重なりで、引数名は読み方(URL は percent-decode、path はそのまま)だけを決める。
#
# 橋渡しは「sandbox の `/ext/X` = URL `/ext/X` ⇔ nucllei の `EXT_DIR/X`」— 配備は同じ物を
# sandbox の `/ext/<name>` と nucllei の `EXT_DIR/<name>` の両方に置く規約(sandbox 側の
# bind の有無は nucllei からは確かめられない。実在を見るのは常に `EXT_DIR/X`)。
# `EXT_DIR` は呼び出し時に読む(import 時に固定しない)ので、テストは module 属性を差し替える。
EXT_DIR = _ENV_EXT_DIR
SANDBOX_MOUNT_ROOT = "/ext"
_SANDBOX_MOUNT_PREFIX = SANDBOX_MOUNT_ROOT + "/"

# sandbox の workspace 参照。prefix を落とした残りが `/files/read` の相対 path。
WORKSPACE_PREFIX = "/workspace/"

# PDF ページ画像化の解像度。valve にしない — モデルが読める解像度は配備で変わらない。
PDF_RASTER_DPI = 150

# URL の **path 部** "/ext[/{rest}]"(query / fragment は urlsplit で分離済み)。
# 絶対 URL なら host は問わない(module docstring)。rest は `EXT_DIR` からの相対パス
# (percent-encoded のまま)。空でもよい(= `EXT_DIR` 自体 = ディレクトリ → not_found)。
EXT_URL_PATH_PATTERN = re.compile(r"^/ext(?:/(.*))?$")

# tool の引数が受ける形の固定文(module docstring「エラーに添える固定文」)。公開名 —
# builtin(sandbox / attachment_text)と配備層の tool が import して、形の誤りのエラーに
# 同じ文を使う(llens の tool と文面を合意済み、2026-10-03。変えるときは両側を揃える)。
URL_ARG_FORM = (
    "url が受けるのは、スキーム付きの絶対 URL (`http(s)://…`) と、同一サーバの "
    "`/ext/…`・`/api/v1/files/{id}/content` だけ。"
)
PATH_ARG_FORM = "path が受けるのは、sandbox の絶対パス (`/workspace/…` `/ext/…`) だけ。"
# workspace に書く / 一覧する tool(read-only の `/ext` を扱えない)用
WORKSPACE_PATH_ARG_FORM = (
    "path が受けるのは、sandbox の絶対パス (`/workspace/…`) だけ。"
)
FILE_ID_ARG_FORM = "file_id は添付・生成物の id。"
# 行き先の規則(全 tool 共通の 1 文。その tool が持たない引数にも触れるが、規則そのものの
# 記述。tool 固有の行き先は書かない)
REF_ARGS_RULE = (
    "URL は url、sandbox のパスは path、添付・生成物は file_id に、受け取った形のまま渡す "
    "(値を書き換えて別の引数に入れ直さない)。"
)

# 同一サーバの相対 URL のうち、この resolver が解決する経路の先頭。
_EXT_URL_PREFIX = "/ext/"

# このサーバのファイル配信口 "/api/v1/files/{id}/content"。相対と絶対(スキーム+ホスト付き、
# host は問わない)の両方に一致。{id} は uuid 想定だがフォーマットは強制しない(パス区切り・
# クエリ開始文字以外)。fragment は付いていても同じ file に当たる(意味を持つのは末尾の
# `#page=N` だけで、それは呼び手が `split_page` で読む — module docstring)。
INTERNAL_FILE_URL_PATTERN = re.compile(
    r"^(?:https?://[^/]+)?/api/v1/files/([^/?#]+)/content(?:\?[^#]*)?(?:#.*)?$"
)

_ABSOLUTE_URL_PATTERN = re.compile(r"^https?://", re.IGNORECASE)

# 末尾の "#page=N" だけを見る(URL 自身の fragment とは衝突しない — module docstring)。
_PAGE_SUFFIX_PATTERN = re.compile(r"^(.*)#page=(\d+)$")

# 種別判定の magic。content-type と拡張子は自己申告(上流ヘッダ / ファイル名)なので
# 実バイトを優先する。
_MAGIC_TYPES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
    (b"%PDF", "application/pdf"),
)


@dataclass
class LoadedRef:
    data: bytes
    content_type: str
    name: str


@dataclass(frozen=True)
class RefError:
    """`resolve_ref` の失敗理由。`code` はこの一覧のどれか(増やさない — 呼び手の tool が
    code からモデル向け文面を組むので、ここと README §3.8 と tool 側で同じ集合を持つ):

    - `unsupported_form` — その入口が受ける形でない。`resolve_url` なら URL(スキーム付き /
      同一サーバの `/ext/…`)でないもの、`resolve_path` なら `/workspace/` `/ext/` で
      ないもの、`resolve_ref` ならそのどちらでもないもの(相対パス・空・`ftp:` 等)
    - `traversal` — 制御文字・`..`・バックスラッシュ・NUL
    - `unknown_upstream` — 一般の URL の行き先がこのサーバ自身(内訳は `reason`)
    - `not_found` — `/ext/{rest}`(URL / sandbox パスとも)でファイルが無い・ディレクトリ
      (`/ext/` 自体を含む)・`EXT_DIR` の外へ出る symlink、一般の URL の 404、workspace の 404
    - `too_large` — サイズ上限超過(file・http・workspace の 3 経路とも)。
      `size` は実サイズ(http stream 打ち切りなら Content-Length か打ち切り時点の
      受信量)、`limit` は上限。`size_exact` は `size` が確定値か下限かの区別 —
      True: stat(`EXT_DIR` / sandbox 413)・全量読了(workspace)・
      Content-Length(http)。False: Content-Length の無い http stream を打ち切った
      時点の受信量(実サイズは不明で、`size` は limit を少し超えた値になる)。
      too_large 以外の code では常に False(意味を持たない)
    - `upstream_status` — 一般の URL の 2xx 以外(404 は not_found。3xx = 辿れなかった
      redirect — 内訳は `reason`)。`status` に HTTP status
    - `upstream_unreachable` — 一般の URL への接続失敗・タイムアウト・InvalidURL・
      引けない名前(内訳は `reason`)
    - `workspace_unavailable` — SANDBOX_URL 無し / sandbox API 失敗(404 は not_found、
      413 は too_large に寄せる)

    `reason` は code の内訳(code の集合は変えずに、同じ code の中を言い分けるための
    安定したフィールド)。値はこの閉じた集合で、`unknown_upstream`・`upstream_status`・
    `upstream_unreachable` に付く(他の code では None):

    `unknown_upstream`(2026-10-03 までは `unknown_name` / `not_ext_form` もあった —
    `/ext/{name}/` の name の概念ごと無くした。`/ext/…` の失敗は `not_found` だけ):

    - `server_self` — `/ext/` 形でない絶対 URL(一般の URL)で、ホストのアドレス
      (IP リテラル、または DNS 解決結果の 1 つ)が **このサーバ自身(と同居するサービス)**
      を指す(`ext_upstreams.is_self_address`)。一般の URL で取りに行かない唯一の相手 —
      外部サイト・インターネットの URL はここに **来ない**(届くかどうかは配備のネットワーク
      次第で、届かなければ `upstream_unreachable`)

    `upstream_status`(転送先が URL として組めない — http(s) でない・形が不正・Location に
    制御文字 — ときは None = 辿らなかった redirect):

    - `redirect_self` — 転送先がこのサーバ自身(と同居するサービス)で辿らなかった。
      `status` は転送を返した応答の 3xx
    - `too_many_redirects` — redirect が `MAX_REDIRECTS`(3)を超えた

    `upstream_unreachable`:

    - `unresolvable` — URL のホスト名が `getaddrinfo` で引けない(閉域の本番には DNS が
      無い — tool は「IP で指定」と案内できる。`/etc/hosts` にあれば引ける)

    `detail` は人(ログ)向けの 1 行で、**文面は固定ではない**(変わりうる — 分岐に
    使わない)。モデル向け文面は tool 側が `code`(と `reason`)から組む。値を自前で
    パースし直して内訳を当てない。
    """

    code: str
    detail: str
    size: int | None = None
    limit: int | None = None
    status: int | None = None
    size_exact: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class UrlRoute:
    """URL が **どこの何か**(`route_of_url` / `_route_url` の結果)。

    - `kind` — `"ext"`(`/ext/{rest}` = `EXT_DIR` の `{rest}`)、`"general"`
      (`/ext/` 形でない **一般の URL**。module docstring「一般の URL」)、`"file"`
      (このサーバのファイル配信口 `/api/v1/files/{id}/content` — `route_of_url` だけが返す。
      `resolve_url` は読まない: 権限検査が要るので呼び手が `file_id_of_url` で読む)
    - `rel` — `"ext"`: `EXT_DIR` からの相対パス(URL に書かれたまま = percent-encoded。先頭 `/`・
      query・fragment を含まない。`/ext/` 自体は `""`)。`"general"`: URL の path から先頭 `/` を
      落とした物(ページのルート `http://h/` は `""`)。`"file"`: file の id
    - `query` — `"?a=b"` か `""`
    - `origin` — 入力が絶対 URL なら `"scheme://host[:port]"`(与えられたまま。接続は
      しない)、同一サーバの相対 URL なら None。一般の URL は常に絶対なので必ず有る

    ページ内リンクの基準は **そのページ自身の URL**(`page_url`): 一般の URL は
    `{origin}/{rel}{query}`、`/ext/{rest}` は相対入力なら `/ext/{rel}{query}`、絶対入力なら
    `{origin}/ext/{rel}{query}`(通常の Web と同じ urljoin。配信元のルートという概念は無い)。

    2026-10-03 までは `name`(EXT_UPSTREAMS の name)・`public_base`・`ext_form` を持ち、
    `kind` は `"upstream"` だった — 設定の撤去で落とした。
    """

    rel: str
    query: str
    origin: str | None
    kind: str = "ext"

    @property
    def relative(self) -> bool:
        """入力が同一サーバの相対 URL(スキーム無し)だったか。"""
        return self.origin is None

    @property
    def general(self) -> bool:
        """一般の URL(`/ext/` 形でない)か。"""
        return self.kind == "general"

    @property
    def page_url(self) -> str | None:
        """ページ自身の URL(fragment 抜き)— ページ内リンクを解決する基準。`"file"` は None
        (添付の HTML の href はどこにも属さない)。"""
        if self.kind == "general":
            return f"{self.origin}/{self.rel}{self.query}"
        if self.kind == "ext":
            return f"{self.origin or ''}/ext/{self.rel}{self.query}"
        return None


# 内部の各経路が返す形。成功 (loaded, None) / 失敗 (None, RefError) のどちらか一方。
_Result = tuple[LoadedRef | None, RefError | None]


def _fail(code: str, detail: str, **fields) -> _Result:
    return None, RefError(code, detail, **fields)


def file_id_of_url(url: str) -> str | None:
    """URL がこのサーバのファイル配信口(`/api/v1/files/{id}/content`)なら id を返す。

    URL のルーティング(どの経路の何か)だけ — 読み出しと権限検査は呼び手
    (module docstring「URL の解決」)。値の形から引数の型を当て推量しているのではない:
    `url` に渡された値を URL として、サーバが実際に持つ経路に当てている。

    query と fragment は付いていてもよい(同じ file に当たる)。fragment のうち意味を
    持つのは末尾の `#page=N`(PDF のページ指定)だけで、ページが要る呼び手は別に
    `split_page(url)` で読む。それ以外の fragment は無視する — どの tool でも同じ規則。
    """
    match = INTERNAL_FILE_URL_PATTERN.match(url) if isinstance(url, str) else None
    return match.group(1) if match else None


def split_page(ref: str) -> tuple[str, int | None]:
    """ "/a.pdf#page=3" → ("/a.pdf", 3)。ページ指定が無ければ (ref, None)。
    URL でも同じ("https://h/a.pdf#page=3" → ("https://h/a.pdf", 3))。"""
    match = _PAGE_SUFFIX_PATTERN.match(ref)
    if not match:
        return ref, None
    return match.group(1), int(match.group(2))


def with_page(ref: str, page: int) -> str:
    """ref にページ指定(1-origin)を付ける。既に付いていれば付け替える。"""
    base, _ = split_page(ref)
    return f"{base}#page={page}"


def _sniff(data: bytes) -> str | None:
    for magic, content_type in _MAGIC_TYPES:
        if data.startswith(magic):
            return content_type
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def detect_kind(loaded: LoadedRef) -> str | None:
    """ "image" / "pdf" / None。magic bytes 優先、次に content-type、次に拡張子。"""
    for content_type in (
        _sniff(loaded.data),
        loaded.content_type,
        mimetypes.guess_type(loaded.name)[0],
    ):
        base = (content_type or "").split(";")[0].strip().lower()
        if base.startswith("image/"):
            return "image"
        if base == "application/pdf":
            return "pdf"
    return None


def pdf_page_count(data: bytes) -> int:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)
    try:
        return len(pdf)
    finally:
        pdf.close()


def rasterize_pdf_page(data: bytes, page: int) -> bytes:
    """PDF の page(1-origin)を PNG バイト列にする。範囲外は IndexError。"""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)
    try:
        if page < 1 or page > len(pdf):
            raise IndexError(f"page {page} out of range (1..{len(pdf)})")
        pdf_page = pdf[page - 1]
        try:
            image = pdf_page.render(scale=PDF_RASTER_DPI / 72).to_pil()
        finally:
            pdf_page.close()
    finally:
        pdf.close()
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _content_type_for(data: bytes, declared: str | None, name: str) -> str:
    """data URL に載せる型。magic → 申告(上流ヘッダ)→ 拡張子 → octet-stream。"""
    declared = (declared or "").split(";")[0].strip().lower()
    return (
        _sniff(data)
        or declared
        or mimetypes.guess_type(name)[0]
        or "application/octet-stream"
    )


def _read_confined(
    inside: Callable[[Path], bool], target: Path, label: str, limit: int
) -> _Result:
    """resolve した実体が `inside` を満たす通常ファイルだけを読む。blocking — thread で
    呼ぶ。`inside` は閉じ込め: URL `/ext/{rest}` も sandbox パス `/ext/{rest}` の読み替え先も
    `EXT_DIR` の mount の中(`served_mount`。/ext ゲート `routers/ext.py` も同じ判定)。"""
    try:
        resolved = target.resolve()
    except (OSError, RuntimeError, ValueError):
        log.warning(f"image_refs: cannot resolve path ({label})")
        return _fail("not_found", f"cannot resolve path ({label})")
    if not inside(resolved):
        log.warning(f"image_refs: path outside served root ({label})")
        return _fail("not_found", f"path escapes root ({label})")
    if not resolved.is_file():
        log.warning(f"image_refs: not a file ({label})")
        return _fail("not_found", f"not a file ({label})")
    try:
        size = resolved.stat().st_size
        if size > limit:
            log.warning(
                f"image_refs: file exceeds size limit ({label}, size={size}, "
                f"limit={limit})"
            )
            return _fail(
                "too_large",
                f"file exceeds size limit ({label})",
                size=size,
                limit=limit,
                size_exact=True,
            )
        data = resolved.read_bytes()
    except OSError as e:
        log.exception(f"image_refs: read failed ({label})")
        return _fail("not_found", f"read failed ({label}): {e}")
    return (
        LoadedRef(
            data=data,
            content_type=_content_type_for(data, None, resolved.name),
            name=resolved.name,
        ),
        None,
    )


def _is_under(root: Path) -> Callable[[Path], bool]:
    return lambda resolved: resolved.is_relative_to(root)


def served_mount(target: Path) -> tuple[Path, Path] | None:
    """`target`(`EXT_DIR` 起点、未 resolve)を読むときの **mount** — `(mount_rel, root)`:
    `mount_rel` は `EXT_DIR` から mount point までの相対(無ければ `Path()`)、`root` は
    その resolve 済み(閉じ込めの基準)。`target` が `EXT_DIR` 起点でなければ None。

    `EXT_DIR` の木は nucllei の持ち物で、配備だけが書く。そこに置かれた **ディレクトリの
    symlink** は配備の意思(Docker の volume と同じ役)なので、辿った先を mount の root として
    閉じ込める — host 直起動で `ext/cage/static → ~/…/cage/data/static` のように
    EXT_DIR の外へ張った symlink を配信するため(2026-10-03)。`EXT_DIR` の実ディレクトリを
    上から辿り、最初に当たったディレクトリ symlink が mount point。それより先(mount の中身 =
    文書側の木)の symlink は mount の外へ出られない(resolve 済みの実体が root の中、という
    従来の判定)。ファイルの symlink は mount にならない(中身の木にある
    `escape.txt → /etc/passwd` と区別がつかないので、常に閉じ込めの対象)— 1 ファイルだけ
    置きたい配備はディレクトリごと張るかコピーする。symlink が無ければ `EXT_DIR` 自身
    (resolve 済み — Docker では `/app/data` の volume、host では `ext/` 自体が symlink で
    ありうる)が root。
    """
    base = Path(EXT_DIR)
    try:
        root = base.resolve()
        rel = target.relative_to(base)
    except (OSError, RuntimeError, ValueError):
        return None
    cur = base
    for i, part in enumerate(rel.parts):
        cur = cur / part
        try:
            if cur.is_symlink():
                if cur.is_dir():
                    return Path(*rel.parts[: i + 1]), cur.resolve()
                return Path(), root
            if not cur.is_dir():
                return Path(), root
        except (OSError, RuntimeError, ValueError):
            return None
    return Path(), root


def _inside_served(target: Path) -> Callable[[Path], bool]:
    """`_read_confined` の `inside`: `target` の mount(`served_mount`)の中か。"""
    mount = served_mount(target)
    if mount is None:
        return lambda _resolved: False
    return _is_under(mount[1])


def _served_file(target: Path) -> tuple[Path, str] | None:
    """`target`(`EXT_DIR` 起点、未 resolve)が配信される通常ファイルなら
    `(resolve 済みの実体, 正規形の rest)` — rest は `EXT_DIR` からの相対で、mount point まで
    は配備が置いた名前、その先は mount の root からの実体(mount の中の symlink は実体に
    畳む)。それ以外(不在・ディレクトリ・mount の外へ出る symlink)は None。
    blocking — thread で呼ぶ。`_mounted_file` / `_ext_url_for_file` の共通部。"""
    mount = served_mount(target)
    if mount is None:
        return None
    mount_rel, root = mount
    try:
        resolved = target.resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if not resolved.is_relative_to(root) or not resolved.is_file():
        return None
    return resolved, (mount_rel / resolved.relative_to(root)).as_posix()


def local_path_for_sandbox_path(path: str) -> Path | None:
    """sandbox のパス `/ext/{rest}` → nucllei 側の同じ物 `EXT_DIR/{rest}`(未 resolve)。
    `/ext/` 配下でなければ None(`/ext` 自体も None — ファイルではない)。percent-decode は
    しない(fs のパスとして渡された文字列そのまま。URL 側の decode は `ext_file_target`)。
    橋渡しの「sandbox → ローカル」の向き 1 箇所。実在・閉じ込めは呼び手が resolve して見る。"""
    if not isinstance(path, str) or not path.startswith(_SANDBOX_MOUNT_PREFIX):
        return None
    return Path(EXT_DIR) / path[len(_SANDBOX_MOUNT_PREFIX) :]


def ext_file_target(path: str) -> Path | None:
    """URL の `/ext/` 以降の path(percent-encoded のまま)から、読む先のパス(未 resolve、
    `EXT_DIR` 起点)を作る。decode 後に traversal を含むなら None。

    文書に焼かれた URL は日本語ディレクトリを %E7%A5%9E… で持つ。ブラウザ側 routers/ext.py は
    Starlette が decode 済みの path を受けるので、同じ URL が同じファイルに当たるようここでも
    decode してから結合する。traversal 判定は decode 後で見る(念のため再判定)。
    先頭 "/" は Path 結合で mount を捨てさせるので落とす(routers/ext.py と同じ扱い)。
    """
    decoded = unquote(path)
    if has_path_traversal(decoded):
        return None
    return Path(EXT_DIR) / decoded.lstrip("/")


async def _load_ext(rel: str, limit: int) -> _Result:
    if has_path_traversal(rel):
        log.warning("image_refs: rejecting traversal ext path")
        return _fail("traversal", "traversal in ext path")
    candidate = ext_file_target(rel)
    if candidate is None:
        log.warning("image_refs: rejecting traversal ext path")
        return _fail("traversal", "traversal in ext path")
    return await asyncio.to_thread(
        _read_confined, _inside_served(candidate), candidate, f"ext:{rel}", limit
    )


async def _read_response(
    response: httpx.Response, label: str, filename: str, limit: int
) -> _Result:
    """開いたストリーム応答を LoadedRef にする(一般の URL)。
    404 → not_found、それ以外の 2xx 以外(3xx 含む)→ upstream_status、上限超過 →
    too_large。httpx の例外は呼び手が受ける(upstream_unreachable)。"""
    if not response.is_success:
        status = response.status_code
        log.warning(f"image_refs: ext fetch failed ({label}, status={status})")
        if status == 404:
            return _fail("not_found", f"upstream 404 ({label})", status=404)
        return _fail(
            "upstream_status", f"upstream returned {status} ({label})", status=status
        )
    chunks = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > limit:
            # 全量バッファせず、超過した時点でストリームを打ち切る。size は
            # 打ち切り時点の受信量(実サイズの下限)— Content-Length が
            # 有ればそちらを採る
            declared_len = response.headers.get("content-length")
            exact = (declared_len or "").isdigit()
            size = int(declared_len) if exact else total
            log.warning(
                f"image_refs: ext content exceeds size limit "
                f"({label}, size{'=' if exact else '>='}{size}, limit={limit})"
            )
            return _fail(
                "too_large",
                f"upstream content exceeds size limit ({label})",
                size=size,
                limit=limit,
                size_exact=exact,
            )
        chunks.append(chunk)
    data = b"".join(chunks)
    return (
        LoadedRef(
            data=data,
            content_type=_content_type_for(
                data, response.headers.get("content-type"), filename
            ),
            name=filename,
        ),
        None,
    )


_REDIRECT_STATUSES = frozenset((301, 302, 303, 307, 308))


def _pinned_request(parts, address: str) -> httpx.Request:
    """一般の URL への要求を、接続先を `address` に固定して組む。URL の host を IP に
    差し替え、Host ヘッダ(と https の SNI)には元のホスト名を使う。userinfo は落とす
    (認証情報はどこにも送らない)。path が空(`http://h`)は `/`(ルート)。"""
    host = parts.hostname or ""
    netloc_host = f"[{address}]" if ":" in address else address
    port = f":{parts.port}" if parts.port is not None else ""
    pinned = parts._replace(
        netloc=f"{netloc_host}{port}", path=parts.path or "/", fragment=""
    ).geturl()
    # Host ヘッダは元の host[:port](IPv6 リテラルは [] 付き)
    host_header = f"[{host}]" if ":" in host else host
    return get_ext_client().build_request(
        "GET",
        pinned,
        headers={"Host": f"{host_header}{port}"},
        extensions={"sni_hostname": host} if parts.scheme == "https" else {},
    )


async def _load_general(
    url: str, limit: int, hops: int, redirect_status: int | None
) -> _Result:
    """一般の URL(`/ext/` 形でない絶対 URL。`_route_url` が `kind="general"` と
    判定済み)を直接取得する(module docstring「一般の URL」)。

    - ホストのアドレスを決めて接続を固定(`pin_address`): IP リテラルはそのまま判定(閉域の
      本番は DNS が無く、院内の URL は IP リテラルが普通)、名前は `getaddrinfo`(`/etc/hosts`
      も効く)。自分自身 → `unknown_upstream`(reason `server_self`)、名前が引けない →
      `upstream_unreachable`(reason `unresolvable`)。それ以外は行き先で絞らない
    - 3xx + Location は `_load_url` に転送先を渡して辿る(hops を数える)。転送先は URL と
      して同じ規則で解決される — 一般の URL ならまた固定して取得、`/ext/{rest}` なら
      `EXT_DIR` をローカル読み。転送先が URL として組めなければ `upstream_status`(reason
      無し)、上限を超えたら `upstream_status`(reason `too_many_redirects`)
    - `redirect_status` は、この URL が redirect の転送先として来たときの元の 3xx。転送先が
      自分自身なら `server_self` ではなく、元の応答の `upstream_status` + `redirect_self` で
      返す(モデルには「転送先がこのサーバ自身」と言える)
    - 本文の読みは `_read_response`
    """
    parts = urlsplit(url)
    host = parts.hostname or ""
    label = f"general host={host}"
    address, reason = await pin_address(host)
    if address is None:
        if reason == "unresolvable":
            log.warning(f"image_refs: host does not resolve ({label})")
            return _fail(
                "upstream_unreachable",
                f"host name does not resolve ({label})",
                reason="unresolvable",
            )
        if redirect_status is not None:
            log.warning(
                f"image_refs: redirect target is this server itself ({label}, "
                f"status={redirect_status})"
            )
            return _fail(
                "upstream_status",
                f"redirect {redirect_status} to this server itself ({label})",
                status=redirect_status,
                reason="redirect_self",
            )
        log.warning(f"image_refs: host is this server itself ({label})")
        return _fail(
            "unknown_upstream",
            f"host resolves to this server itself or a co-located service ({label})",
            reason="server_self",
        )
    try:
        request = _pinned_request(parts, address)
        response = await get_ext_client().send(request, stream=True)
        try:
            status = response.status_code
            location = response.headers.get("location")
            if status in _REDIRECT_STATUSES and location:
                if hops >= MAX_REDIRECTS:
                    log.warning(f"image_refs: too many redirects ({label})")
                    return _fail(
                        "upstream_status",
                        f"more than {MAX_REDIRECTS} redirects ({label})",
                        status=status,
                        reason="too_many_redirects",
                    )
                # Location は上流が返した生の値 — 制御文字・バックスラッシュは urljoin が
                # 黙って落とす前に見る(has_path_traversal)。結合後も同じ入口の検査に通す
                target = urljoin(url, location)
                if has_path_traversal(location) or _check_url(target) is not None:
                    route = None
                else:
                    route, _ = _route_url(target)
                if route is None:
                    log.warning(
                        f"image_refs: redirect target is not a URL this server "
                        f"resolves ({label}, status={status})"
                    )
                    return _fail(
                        "upstream_status",
                        f"redirect {status} to a URL this server cannot resolve "
                        f"({label})",
                        status=status,
                    )
                log.info(f"image_refs: following redirect {status} ({label})")
            else:
                return await _read_response(
                    response, label, os.path.basename(parts.path), limit
                )
        finally:
            await response.aclose()
    except (httpx.HTTPError, httpx.InvalidURL) as e:
        log.exception(f"image_refs: general fetch error ({label})")
        return _fail(
            "upstream_unreachable",
            f"general fetch error ({label}): {type(e).__name__}: {e}",
        )
    return await _load_url(target, limit, hops + 1, status)


# sandbox_post が投げる RuntimeError の文面(`sandbox HTTP {status}: {body}`)から
# status と、413 の body(`file too large: N bytes (limit L)`)からサイズを拾う。
_SANDBOX_HTTP_PATTERN = re.compile(r"^sandbox HTTP (\d{3})\b")
_SANDBOX_TOO_LARGE_PATTERN = re.compile(r"(\d+) bytes \(limit (\d+)\)")


def _workspace_error(exc: Exception, rel_path: str) -> RefError:
    """sandbox `/files/read` の失敗を code に振り分ける。404 → not_found、
    413 → too_large、それ以外(不達・401・5xx)→ workspace_unavailable。"""
    message = str(exc)
    match = _SANDBOX_HTTP_PATTERN.match(message)
    status = int(match.group(1)) if match else None
    if status == 404:
        return RefError("not_found", f"workspace file not found (path={rel_path})")
    if status == 413:
        size_match = _SANDBOX_TOO_LARGE_PATTERN.search(message)
        return RefError(
            "too_large",
            f"workspace file exceeds sandbox size limit (path={rel_path})",
            size=int(size_match.group(1)) if size_match else None,
            limit=int(size_match.group(2)) if size_match else None,
            # sandbox は stat で見て 413 を返す — body から拾えた size は確定値
            size_exact=size_match is not None,
        )
    return RefError(
        "workspace_unavailable",
        f"sandbox read failed (path={rel_path}): {type(exc).__name__}: {message[:200]}",
        status=status,
    )


async def _load_workspace(rel_path: str, user_id: str, limit: int) -> _Result:
    # builtin_tools パッケージは feed_image / fetch_url が本 module を import する
    # (2026-10-03、llens からの移設)— top-level で `builtin_tools` を引くと、どちらを先に
    # import しても循環になるので、sandbox を使うここでだけ引く
    from nucllei.builtin_tools import sandbox

    if not sandbox.SANDBOX_URL:
        log.warning("image_refs: /workspace reference without SANDBOX_URL")
        return _fail("workspace_unavailable", "SANDBOX_URL is not configured")
    if not rel_path:
        log.warning("image_refs: empty workspace path")
        return _fail("not_found", "empty workspace path")
    if has_path_traversal(rel_path):
        log.warning("image_refs: rejecting workspace path")
        return _fail("traversal", "traversal in workspace path")
    try:
        res = await sandbox.sandbox_post(
            "/files/read", {"user_id": user_id, "path": rel_path}, 60
        )
        data = base64.b64decode(res["b64"])
    except Exception as e:
        # 不在(404)・上限(413)・不達は全部ここ — sandbox 側のメッセージは log に残す
        log.exception(f"image_refs: workspace read failed (path={rel_path})")
        return None, _workspace_error(e, rel_path)
    if len(data) > limit:
        log.warning(
            f"image_refs: workspace file exceeds size limit (path={rel_path}, "
            f"size={len(data)}, limit={limit})"
        )
        return _fail(
            "too_large",
            f"workspace file exceeds size limit (path={rel_path})",
            size=len(data),
            limit=limit,
            size_exact=True,
        )
    filename = res.get("name") or os.path.basename(rel_path)
    return (
        LoadedRef(
            data=data,
            content_type=_content_type_for(data, None, filename),
            name=filename,
        ),
        None,
    )


def _is_url(ref: str) -> bool:
    """`resolve_url` が受ける形か — スキーム付き、または同一サーバの相対 `/ext/…`。"""
    return bool(_ABSOLUTE_URL_PATTERN.match(ref)) or ref.startswith(_EXT_URL_PREFIX)


def _is_sandbox_path(ref: str) -> bool:
    """`resolve_path` が受ける形か — `/workspace/…` か sandbox の `/ext/…`(sandbox 側の
    形。nucllei の fs ではない — 読むときは `EXT_DIR` へ読み替える)。"""
    return ref.startswith((WORKSPACE_PREFIX, _SANDBOX_MOUNT_PREFIX))


def stored_ref_kind(ref) -> str | None:
    """**保存済みの参照文字列**(`context_images` のエントリ・image_url の url)が何かを、
    先頭で機械的に振り分ける(module docstring の表。規則はここ 1 箇所)。`#page=N` は
    付いていてもよい。

    - `"file_url"` — `/api/v1/files/{id}/content`(相対、または絶対 = host 不問)
    - `"url"` — `http(s)://…` / 同一サーバの `/ext/…`(`resolve_url` が受ける形。`/ext/…` は
      sandbox の RO マウントと同じ文字列だが、保存済みは URL として読む — 同じ物に当たる)
    - `"path"` — `/workspace/…`(`resolve_path` が受ける形のうち `/ext/…` でないもの)
    - `"file_id"` — `/` 始まりでない裸の文字列(file の id)
    - `None` — それ以外(他の `/…`・空・文字列でない)

    **tool の引数には使わない**(引数は名前で解釈が決まる)。
    """
    if not isinstance(ref, str) or not ref:
        return None
    if file_id_of_url(ref):
        return "file_url"
    base, _ = split_page(ref)
    if _is_url(base):
        return "url"
    if _is_sandbox_path(base):
        return "path"
    if not base.startswith("/"):
        return "file_id"
    return None


def _route_url(url: str) -> tuple[UrlRoute | None, RefError | None]:
    """URL(スキーム付き / 同一サーバの相対 `/ext/…`)が **どこの何か** を決める
    (読みはしない)。URL の名前空間規則(module docstring)はここ 1 箇所 — `_load_url` と
    `mounted_path_for_url`、公開の `route_of_url` が共有し、逆向きの `url_for_mounted_path`
    も組んだ URL をここに通して確かめる。ログは出さない(呼び手が出す)。"""
    try:
        parts = urlsplit(url)
    except ValueError as e:
        return None, RefError("unsupported_form", f"malformed URL: {e}")
    query = f"?{parts.query}" if parts.query else ""
    origin = f"{parts.scheme}://{parts.netloc}" if parts.scheme else None

    match = EXT_URL_PATH_PATTERN.match(parts.path)
    if match is None:
        if origin is None:
            # 相対で来るのは `/ext/` 始まりだけ(`_is_url`)— ここには来ない(念のため)
            return None, RefError(
                "unsupported_form", "same-server URL is not under /ext/"
            )
        return _route_general(parts, origin, query)
    # 自オリジンの表示 URL(相対ならそのまま今のサーバ)。host は見ない(module docstring)。
    # rest が無い / 空 = `EXT_DIR` 自体(rel "")。他の rel と同じに扱う(読めば not_found)
    return UrlRoute(rel=match.group(1) or "", query=query, origin=origin), None


def _route_general(
    parts, origin: str, query: str
) -> tuple[UrlRoute | None, RefError | None]:
    """`/ext/` 形でない絶対 URL を、一般の URL(`kind="general"`)として組む。
    DNS は引かない(`route_of_url` の純粋さを保つ — 自分自身でないかは `_load_general` が
    決める)。ホスト名が取れない・port が不正な URL は `unsupported_form`。"""
    try:
        host, port = parts.hostname, parts.port
    except ValueError as e:
        return None, RefError("unsupported_form", f"malformed URL: {e}")
    if not host:
        return None, RefError("unsupported_form", "URL has no host")
    del port  # 検証のためだけに評価した
    return (
        UrlRoute(
            rel=parts.path.lstrip("/"), query=query, origin=origin, kind="general"
        ),
        None,
    )


def _check_url(url) -> RefError | None:
    """`url` の入口の前段(`resolve_url` / `route_of_url` 共通): 文字列でない・traversal・
    URL の形でないものを RefError にする。"""
    err = _reject(url)
    if err is not None:
        return err
    if not _is_url(url):
        return RefError(
            "unsupported_form",
            "not a URL this server resolves (http(s):// or same-server /ext/)",
        )
    return None


def route_of_url(url: str) -> tuple[UrlRoute | None, RefError | None]:
    """URL(絶対 / 同一サーバの相対 `/ext/…` / `/api/v1/files/{id}/content`)が **どこの何か**
    を返す。`resolve_url` と同じ 1 箇所のルーティング(`_route_url`)の結果で、**読まない**
    (ネットワークにもファイルにも触れない純粋な関数。ログも出さない)。

    用途: `resolve_url` で取得できた URL について、呼び手(builtin の fetch_url)が
    ページ内リンクの基準(`route.page_url`)を得る — `/ext/{rest}` も一般の URL も、
    そのページ自身の URL を基準に urljoin する(通常の Web と同じ)。URL の分解を呼び手に
    複製させない。

    成功は `(UrlRoute, None)`、ルーティングできない URL は `(None, RefError)` —
    `resolve_url` が同じ入力に返すのと同じ code / reason(`unsupported_form` /
    `traversal`)。例外は 2 つ: **DNS は引かない**ので、`/ext/` 形でない絶対 URL はすべて
    `kind="general"` で返す(= 一般の規則で解決される候補。実際にホストが自分自身でないかは
    `resolve_url` が決める — `unknown_upstream` reason `server_self`)。このサーバの
    ファイル配信口 `/api/v1/files/{id}/content`(相対・絶対)は `kind="file"`・`rel` = id で
    返す(`resolve_url` は権限検査が要るので `unsupported_form` — 呼び手が `file_id_of_url`
    で読む)。`resolve_url` の成功後に呼ぶ用途ではずれない。**実在は見ない** — 「どこに属するか」
    だけで、読めるかどうかは `resolve_url` の責務(`/ext/` 自体・ディレクトリは `not_found`)。
    """
    file_id = file_id_of_url(url)
    if file_id:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}" if parts.scheme else None
        return UrlRoute(rel=file_id, query="", origin=origin, kind="file"), None
    err = _check_url(url)
    if err is not None:
        return None, err
    return _route_url(url)


async def _load_url(
    url: str, limit: int, hops: int = 0, redirect_status: int | None = None
) -> _Result:
    """URL(traversal / 制御文字は入口で拒否済み)を解決する: `/ext/{rest}` は `EXT_DIR` を
    ローカル読み、一般の URL は直接取得(`_load_general`)。`hops` は一般の URL で
    辿った redirect の回数、`redirect_status` はこの URL を指した redirect の status(転送先の
    再解決でだけ付く)。"""
    route, err = _route_url(url)
    if route is None:
        # warning はここで出す(`_route_url` は probe からも呼ばれる — 二重に残さない)
        log.warning(f"image_refs: {err.detail} ({url[:80]!r})")
        return None, err
    if route.general:
        return await _load_general(url, limit, hops, redirect_status)
    return await _load_ext(route.rel, limit)


def _mounted_file(target: Path) -> str | None:
    """target(`EXT_DIR` 起点、未 resolve)が配信される通常ファイルなら、sandbox 側の
    パス `/ext/{rest}`(正規形の rest — `_served_file`)を返す。blocking — thread で呼ぶ。"""
    served = _served_file(target)
    if served is None:
        return None
    return f"{_SANDBOX_MOUNT_PREFIX}{served[1]}"


async def mounted_path_for_url(url: str) -> str | None:
    """URL(絶対 / 同一サーバの `/ext/…`)が `/ext/{rest}` 形で、**`EXT_DIR` の `{rest}` が
    実在する通常ファイル** なら、sandbox 側のパス(`/ext/{rest}` — 相対 URL と同じ文字列。
    rest は percent-decode 済み・symlink を畳んだ形)を返す。それ以外
    (一般の URL・不在・ディレクトリ・`EXT_DIR` の外へ出る symlink・traversal・URL でない)は
    None — 理由は返さない(要る呼び手は `resolve_url` を続けて呼べば同じ規則で RefError が出る)。

    用途は「URL → sandbox の fs パス」の橋渡し(builtin sandbox の `send_to_workspace`):
    配備は同じ物を sandbox の `/ext/<name>` と nucllei の `EXT_DIR/<name>` の両方に置く規約
    (#138)なので、URL が指す実体は sandbox からも `/ext/{rest}` で見えていて、コピーする
    必要が無い。**sandbox 側に同じ bind が実際にあるかは nucllei からは確かめられない** —
    ここが見るのは nucllei 側の事実(`EXT_DIR/{rest}` が実在)だけで、同じ置き場である
    ことは配備の規約に依存する。
    """
    if not isinstance(url, str) or not _is_url(url):
        return None
    if has_path_traversal(url):
        return None
    route, _ = _route_url(url)
    if route is None or route.kind != "ext" or has_path_traversal(route.rel):
        # 一般の URL は配信元を持たない(sandbox に同じ実体は無い)
        return None
    candidate = ext_file_target(route.rel)
    if candidate is None:
        return None
    return await asyncio.to_thread(_mounted_file, candidate)


def _ext_url_for_file(target: Path) -> str | None:
    """target(`EXT_DIR` 起点、未 resolve)が `EXT_DIR` 配下の通常ファイルなら、その配信 URL
    (相対 `/ext/{rest}`)を返す。blocking — thread で呼ぶ。

    `_mounted_file` の逆。閉じ込めは同じ判定(resolve 済みの実体が `EXT_DIR` の中)— `..`・
    symlink で `EXT_DIR` の外へ出る物、不在、ディレクトリは None。rest は percent-encode する
    (日本語・空白・`#` `?` を含む名前が URL として壊れないように)。組んだ URL は
    `_route_url` → `ext_file_target` に通して同じファイルに戻ることを確かめてから返す —
    URL の形の規則をここに二重に持たず、/ext ゲート(`routers/ext.py`)と `resolve_url` が
    受ける形と往復で一致させるため。ゲートが 400 で断る名前(制御文字・バックスラッシュ)は
    None(配信されていない扱い)。
    """
    served = _served_file(target)
    if served is None:
        return None
    resolved, rest = served
    rest = quote(rest, safe="/")
    url = f"{_EXT_URL_PREFIX}{rest}"
    if has_path_traversal(url):
        return None
    route, _ = _route_url(url)
    if route is None or (route.kind, route.rel, route.query) != ("ext", rest, ""):
        return None
    back = ext_file_target(rest)
    if back is None or back.resolve() != resolved:
        return None
    return url


async def url_for_mounted_path(path: str) -> str | None:
    """sandbox の fs パス(`/ext/{rest}`)の実体が **nucllei 側(`EXT_DIR/{rest}`)にも実在する
    通常ファイル** なら、その URL(同一サーバの相対 `/ext/{rest}` — ASCII 名なら入力と同じ
    文字列、日本語等は percent-encode 済み)を返す。それ以外
    (nucllei 側に無い `/ext/…`・ディレクトリ・`EXT_DIR` の外へ出る symlink・`/workspace/…`・
    パスでない)は None — 理由は返さない(要る呼び手は `resolve_path` を続けて呼べば同じ
    境界で RefError が出る)。

    `mounted_path_for_url` の逆向きで、用途は「sandbox のパス → 利用者に見せられる URL」の
    橋渡し(builtin sandbox の `fetch_from_workspace`): 同じ実体が既に `/ext/…` で
    配信されているなら、コピーして file にする必要が無い。前提は `mounted_path_for_url` と
    同じ配備の規約(#138: sandbox の `/ext/<name>` と nucllei の `EXT_DIR/<name>` に同じ物を
    置く)—
    sandbox 側のそのパスが同じ実体かは nucllei からは確かめられない。公開範囲は広げない:
    返す URL は /ext ゲートが既に配信している物だけ。
    """
    if not isinstance(path, str) or has_path_traversal(path):
        return None
    local = local_path_for_sandbox_path(path)
    if local is None:
        return None
    return await asyncio.to_thread(_ext_url_for_file, local)


def _reject(ref) -> RefError | None:
    """3 つの入口に共通の前段。文字列でない / 制御文字・`..`・バックスラッシュを含む参照は
    どの形でも読まない(URL は path / query ごと文字列全体に掛ける — urlsplit は CR/LF/TAB を
    黙って落とすので、分解前に見る。sandbox パスの読み替え先は Path.resolve() が NUL で ValueError を
    投げて fail closed の約束を破るので、入口で一律に落とす)。"""
    if not isinstance(ref, str):
        return RefError(
            "unsupported_form", f"reference is not a string ({type(ref).__name__})"
        )
    if has_path_traversal(ref):
        log.warning("image_refs: rejecting reference with traversal/control chars")
        return RefError(
            "traversal", "reference contains '..', backslash or control chars"
        )
    return None


async def resolve_url(url: str, *, max_bytes: int | None = None) -> _Result:
    """**URL として** 解決する(tool の `url` 引数の入口)。

    受けるのはスキーム付きの絶対 URL(`http(s)://…`)と、同一サーバの相対 URL
    `/ext/{rest}`。値の中身を見て別の意味(sandbox のパス・file_id)に読み替えない —
    URL として解決できないものは `unsupported_form`。`/api/v1/files/{id}/content` は
    呼び手が `file_id_of_url` で先に拾う(module docstring「URL の解決」)。user を
    取らない: `/ext/` の公開範囲は配備が `EXT_DIR` に置いた物で、per-user ではない。

    戻り値・サイズ上限・失敗 code は `resolve_ref` と同じ。
    """
    limit = FETCH_MAX_BYTES if max_bytes is None else max_bytes
    err = _check_url(url)
    if err is not None:
        if err.code == "unsupported_form":
            log.warning(f"image_refs: not a resolvable URL ({str(url)[:80]!r})")
        return None, err
    return await _load_url(url, limit)


async def resolve_path(
    path: str, user_id: str, *, max_bytes: int | None = None
) -> _Result:
    """**sandbox の fs の絶対パスとして** 解決する(tool の `path` 引数の入口)。

    受けるのは `/workspace/…`(呼び出しユーザーの workspace)と `/ext/…`(sandbox の read-only
    の参照データ — nucllei 側では `EXT_DIR/…` を読む。module 冒頭の 2 つの root。URL の
    `/ext/{rest}` と同じ文字列だが、ここでは fs のパスとして percent-decode せずに読む)。
    URL が来ても URL としては解決しない — パスとして `unsupported_form`。相対パスも
    `unsupported_form`(`path` の形は絶対パス 1 つ)。

    戻り値・サイズ上限・失敗 code は `resolve_ref` と同じ。
    """
    limit = FETCH_MAX_BYTES if max_bytes is None else max_bytes
    err = _reject(path)
    if err is not None:
        return None, err
    if not _is_sandbox_path(path):
        log.warning(f"image_refs: not a sandbox path ({path[:80]!r})")
        return _fail(
            "unsupported_form",
            f"not a sandbox path (/workspace/ or {_SANDBOX_MOUNT_PREFIX})",
        )
    if path.startswith(WORKSPACE_PREFIX):
        return await _load_workspace(path[len(WORKSPACE_PREFIX) :], user_id, limit)
    local = local_path_for_sandbox_path(path)
    return await asyncio.to_thread(
        _read_confined, _inside_served(local), local, f"mount:{path}", limit
    )


async def resolve_ref(
    ref: str, user_id: str, *, max_bytes: int | None = None
) -> _Result:
    """**保存済みの参照文字列**(`#page` を含まない)をバイト列にする。

    引数名を持たない文字列(`context_images` のエントリ・image_url の url)の入口で、
    `stored_ref_kind` の振り分けに従うだけの薄い dispatcher — URL(`http(s)://…` /
    `/ext/…` — sandbox の RO マウントと同じ文字列だが URL として読む)は `resolve_url`、
    sandbox の workspace パス(`/workspace/…`)は
    `resolve_path`、どちらでもなければ `unsupported_form`(module docstring の表。file の
    配信 URL と裸の file id は権限検査が要るので呼び手が先に拾う)。**tool の引数には
    使わない** — `url` は `resolve_url`、`path` は `resolve_path` を直接呼ぶ(名前で
    解釈が決まる。ここを通すと値の形で意味が変わってしまう)。

    成功なら `(loaded, None)`、失敗なら `(None, RefError)`。例外は投げない(fail
    closed の約束 — 境界の外は全部 RefError に閉じる。3 つの入口とも同じ)。

    サイズ上限は `max_bytes`(None なら env の FETCH_MAX_BYTES)— どの経路
    (file / http stream / workspace)も同じ値で見る。上限は呼び手の都合(画像 inline は
    小さく、文書を読む用途は大きく)なので resolver の env 1 本で縛らない。`too_large`
    の `limit` には実際に使った値が入る。

    失敗理由は `RefError.code`(一覧は RefError の docstring)。呼ぶ側(feed 側の tool、
    送信直前 inline)はこれで「不在」「上限超過」「上流不達」を言い分ける — 本 module は
    warning ログも残すので、ログ側の文面は `detail` と同じ事実を指す。
    """
    err = _reject(ref)
    if err is not None:
        return None, err
    kind = stored_ref_kind(ref)
    if kind == "url":
        return await resolve_url(ref, max_bytes=max_bytes)
    if kind == "path":
        return await resolve_path(ref, user_id, max_bytes=max_bytes)
    log.warning(f"image_refs: unsupported reference form ({ref[:80]!r})")
    return _fail(
        "unsupported_form",
        "reference is neither a URL (http(s):// or same-server /ext/) nor a "
        f"sandbox path (/workspace/ or {_SANDBOX_MOUNT_PREFIX})",
    )


async def load_ref(ref: str, user_id: str) -> LoadedRef | None:
    """`resolve_ref` の薄い wrapper — 解決できなければ None(理由を要らない呼び手向け)。
    上限は従来どおり FETCH_MAX_BYTES(引数は増やさない)。"""
    loaded, _ = await resolve_ref(ref, user_id)
    return loaded


def log_mounts() -> None:
    """起動時 1 行: `EXT_DIR` に何が見えているか(sandbox の `_log_ro_mounts` と
    同じ流儀)。配備側が volume / symlink を足すだけで増える口で、env を見ても分からないため。
    ここに見えている物がそのまま `/ext/<rel>` で配信され、sandbox は同じ物を `/ext/<rel>` に
    見る規約(2026-10-03)。
    """
    root = Path(EXT_DIR)
    try:
        names = sorted(n for n in os.listdir(root) if not n.startswith("."))
    except OSError:
        names = []
    if names:
        log.info(
            f"[nucllei] ext data in {root} (served at /ext/, sandbox sees them at "
            f"{SANDBOX_MOUNT_ROOT}/): " + " ".join(f"{n} → /ext/{n}" for n in names)
        )
    else:
        log.info(
            f"[nucllei] {root} に参照用データは無い(/ext/** は全て 404。配備は "
            f"{root}/<name> に volume / symlink を置く)"
        )
    try:
        own = sorted(str(ip) for ip in self_addresses())
    except (
        Exception
    ) as e:  # noqa: BLE001 — 起動ログのためだけ。発見の失敗で起動を止めない
        log.warning(f"[nucllei] WARNING: 自分自身のアドレスを求められない: {e!r}")
        own = []
    log.info(
        "[nucllei] url= の一般 URL はどこでも取りに行く(行き先で絞らない)。取りに行かない"
        "のはこのサーバ自身だけ — loopback / リンクローカルに加えて実行時に求めた自分の"
        f"アドレス (self addresses): {' '.join(own) or '(無し)'}"
        + (
            f" / 設定で追加 (SELF_ADDRESSES): {' '.join(sorted(str(ip) for ip in SELF_ADDRESSES))}"
            if SELF_ADDRESSES
            else ""
        )
    )
