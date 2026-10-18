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

**エラーに添える固定文**: 形の誤りには、その引数が受ける形を固定の文で添える(下の定数
`URL_ARG_FORM` / `PATH_ARG_FORM` / `WORKSPACE_PATH_ARG_FORM` / `FILE_ID_ARG_FORM`。builtin と
配備層の tool が同じ文を import して使う)。行き先は tool ごとに書かず、**どの tool でも同じ
1 文** `REF_ARGS_RULE` として持つ。`REF_ARGS_RULE`(と file_id の `FILE_ID_ARG_FORM`)は
**値が別の引数の種類に見えるとき(取り違え)だけ**添える — `ref_args_rule_for`(2026-10-04
llens staging: 相対パスの path にまで取り違えの規則が付き、関係の無い定型文になっていた)。
値の中身で解決や救済を変えるのではなく、添える文を誤りの種類で選ぶだけ。組み立ては全 tool 共通:

- url が URL として解決できない:
  `ERROR: <値> は URL として解決できない。` + `URL_ARG_FORM`(+ ` ` + `REF_ARGS_RULE`)
- path が sandbox のパスの形でない:
  `ERROR: <値> は sandbox のパスの形でない。` + `PATH_ARG_FORM`(workspace だけを扱う tool は
  `WORKSPACE_PATH_ARG_FORM`)(+ ` ` + `REF_ARGS_RULE`)
- file_id が引けない: `ref_errors.file_id_error`(「無い」と「権限が無い」を言い分けない —
  存在の有無を漏らさない。区別できないことを事実の文で言い、次の手で file_id の確かめ方と
  ユーザーへの伝え方を言う)

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

- path が `/ext/<name>/<sub>` なら **env `EXT_MOUNTS` の対応表**(`ext_upstreams.ext_mount`)で
  行き先を決める(2026-10-05): **静的**(値が絶対パス)なら `<dir>/<sub>` をローカル読み、**proxy**
  (値が `http(s)://`)なら上流の `<base>/<sub>?<query>` へ HTTP GET(`_load_proxy`。サイズ上限・
  404 → not_found・2xx 以外 → upstream_status・不達 / タイムアウト → upstream_unreachable は一般の
  URL と同じ。redirect は上流の base 配下だけ `/ext/<name>/…` として辿る — /ext ゲートの書き換えと
  同じ判定)。表に無い name は `not_found`。/ext ゲート(routers/ext.py)と同じ表・同じ閉じ込め
  なので、ブラウザで開ける `/ext/…` はモデルも同じ物を読める(`feed_image(url="/ext/doc-static/…png")`
  が proxy の先から取れる)。絶対 URL は **host を問わない** — 与えられた host には接続しない。
  相対 `/ext/{rest}` は「今のサーバの URL」= 同じ対応表。静的の閉じ込めは `_read_confined`
  (resolve 済みの実体が mount の root の中の通常ファイル。外へ出る symlink・ディレクトリ・不在は
  `not_found`)。
- `/ext/` と `/ext/<name>/`(rest が空・ディレクトリ)も形としては正しく、静的なら `not_found`
  (一覧は出さない — ゲートも 404)。proxy の name は上流が返す物次第。
- `/ext/` 形でない絶対 URL は **一般の URL** として、そのホストに直接取りに行く
  (下記「一般の URL」)。行き先で絞らない — 唯一取りに行かないのは **このサーバ自身
  (と同居するサービス)** だけ(`unknown_upstream` reason `server_self`)。proxy の上流は
  `EXT_MOUNTS` に配備が書いた固定の相手なので、この判定は掛けない(SSRF の真実源は表 1 本)。
- 経緯: 2026-10-03 までは EXT_UPSTREAMS(name ごとの http 中継 / `file://` root)・EXT_FILE_SCOPES・
  EXT_PUBLIC_BASES の 3 変数があり、同日に `EXT_DIR`(置いた物を暗黙に全部配る)一本にした。
  2026-10-05 に値の形で静的と proxy を選べる 1 本の対応表 `EXT_MOUNTS` に戻した(ken「入口を一つに
  して、このパスは proxy、このパスは静的、みたいにしたらいい」。目的は doc の配信を cage に持たせる
  こと)。URL の形は変わらない(`/ext/cage/static/X`)。2026-10-06 に llens 側が公開 URL を
  `/ext/doc/…`(doc の HTML)・`/ext/doc-static/…`(図・PDF)に変えた(`/ext/cage` は廃止)。

**一般の URL**(2026-10-03、ken 決定)。`url=` はスキーム付きの `http(s)://` URL なら
**どこでも**取りに行く — 院内でもインターネットでも。「閉域だから外には届かない」は
prompt / skill に書く文であって、tool のゲートではない(届くかどうかは配備のネットワークが
決める)。同日に一度入れた院内の範囲(env EXT_INTRANET_CIDRS。宣言した CIDR の中だけ取る)は
**同日に撤回**した — 行き先で機能を絞っていたし、固定 CIDR の拒否(docker 既定帯 172.17/16)は
本番の bridge が 10.106.x なので効かず、守れないのに正当な院内を巻き込みかねなかった。

| 名前空間                                   | 誰が決める       | 解決                                          | redirect                 |
| ------------------------------------------ | ---------------- | --------------------------------------------- | ------------------------ |
| `/ext/<name>/{sub}`(相対・絶対)          | 配備(`EXT_MOUNTS`)| 静的は `<dir>/{sub}` をローカル読み、proxy は `<base>/{sub}` へ GET(与えられた host は見ない)| 静的: —、proxy: base 配下だけ 3 回まで |
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
  (またアドレスに固定)、`/ext/{rest}` 形なら `EXT_MOUNTS` の対応表で読む(静的なら
  そこから先の redirect は無い)。転送先が自分自身なら `upstream_status`(転送を返した 3xx の
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
- `mounted_path_for_url` / `url_for_mounted_path` / `/ext` ゲートは無関係(`EXT_MOUNTS` の話)。
- **`/api/v1/files/{id}/content`**(このサーバのファイル配信口。相対、または絶対 — host は
  `/ext/` と同じく問わない: 与えられた host には接続せず Storage から読むので)は、URL の
  ルーティングとしては `file_id_of_url` が id を取り出すところまで。バイトの読み出しは
  per-user の権限検査が要る(`resolve_url` の `user` は `/ext/groups` `/ext/public` の ACL
  — #152、`utils/ext_acl` — にだけ使い、file の権限は見ない)ので、呼び手が自分の検査で読む: 送信直前 inline
  (`file_images.py`、読み取り権限)と `send_to_workspace(url=)`(所有者)。`resolve_url` に
  そのまま渡せば `unsupported_form`。**fragment は末尾の `#page=N` だけが意味を持つ**
  (`split_page`)。それ以外の fragment は無視して同じ file に当てる(fragment は
  サーバに送られない、ブラウザ側だけの情報 — どの tool でも同じ)。
- **URL がどこに属するか**だけが要る呼び手(配備層の fetch_url がリンク一覧のルートを
  組む)は `route_of_url` を使う — 同じ `_route_url` の結果を返すだけで、読まない。

**`/ext/groups/<g>` と `/ext/public` の ACL**(#152): どの入口(`resolve_url` / `resolve_path` /
`resolve_ref` / 橋渡し 2 つ)も `user` を取り、`utils/ext_acl.ext_access` が none の物は
`not_found`(不在と同じ — 存在を漏らさない。redirect の先も同じ)。`user` 無しは見えない
(fail closed)。静的配信の閉じ込めは `/ext/groups` だけ `<root>/<グループ名>`(`_confine_base`)。

**sandbox の fs パス**:

- `/ext/...` — sandbox(jail)の read-only の参照用データ(#138。2026-10-03 までは `/mnt`)。
  これは **sandbox 側のパスの形** で、URL `/ext/...` と同じ物という配備の規約なので、同じ
  対応表(`EXT_MOUNTS`)で読む — 静的は `<dir>/<sub>`(resolve して root の中でなければ読まない)、
  proxy は上流へ GET。nucllei 自身の `/ext` や `/mnt` は見ない(host 直起動で host の fs を
  晒さないため)。URL の `/ext/{rest}` と同じ文字列だが、`path=` に来たものは
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

どちらも **/ext ゲートが同じ文字列で配信している物のときだけ** 値を返す — `/ext/<name>/<sub>` ↔
`/ext/<name>/<sub>` は **同じ文字列**(sub は URL 側が percent-encoded、fs 側が decode 済み —
なので `url_for_mounted_path` が返す URL は、文書の本文にある生の日本語 URL とは文字列が一致
しないことがある。同じファイルの別表記)。静的の name は `<dir>/<sub>` の実在を見る。proxy の
name は、URL → パスの向きは返さない(上流が返す物 — cage が描画した HTML 等 — が jail の同じ
パスに在るとは限らない。呼び手は上流から取り込む)、パス → URL の向きは上流が 2xx を返すかを
HEAD で確かめる(base の中への redirect は辿り、飛び先の URL を返す — cage は `…/_index.md` を
302 で `…/` へ飛ばす)。sandbox の `/ext/X` が nucllei 側で配信されていなければ「このサーバの /ext に
無い」(not_found)。配備は nucllei と sandbox に**同じ組を mount する**規約(ken 2026-10-04)なので、
通常この場合は起きない。`/ext` のファイルは URL で示すだけで、sandbox から汲み出して添付にする
経路は持たない(生の md などを添付にして file_id を無駄に増やさない)。2026-10-03 までの
「配信範囲の外は添付もしない」(`mounted_path_withheld`)は撤去済み。
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
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable
from urllib.parse import quote, unquote, urljoin, urlsplit

import httpx

from nucllei.env import FETCH_MAX_BYTES, SELF_ADDRESSES, SRC_LOG_LEVELS
from nucllei.utils.ext_acl import confine_depth, ext_access, is_group_dir_name
from nucllei.utils.ext_upstreams import (
    MAX_REDIRECTS,
    ExtMount,
    ext_mount,
    get_ext_client,
    has_path_traversal,
    pin_address,
    proxy_timeout,
    self_addresses,
    split_ext_rest,
    under_base,
    upstream_url,
)

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])

# `/ext/<name>` の行き先は env `EXT_MOUNTS` 1 本の対応表(`ext_upstreams.ext_mount`。2026-10-05):
# 値が `http(s)://` なら proxy(上流へ HTTP GET)、絶対パスなら静的(そのディレクトリをローカル読み)。
# 表に無い name は not_found。/ext ゲート(routers/ext.py)と同じ表・同じ閉じ込め。
#
# - `SANDBOX_MOUNT_ROOT` = `/ext` — **sandbox(jail)側の read-only mount の置き場**
#   (`sandbox/app/main.py` の RO_MOUNT_ROOT)。モデルが `path=` に書く形(`/ext/…`)は
#   こちらで、nucllei の fs のパスではない(nucllei 自身の `/ext` は見ない)。URL の prefix と
#   同じ `/ext` にしてあるので、**sandbox のパスと URL は同じ文字列** — `/ext/doc-static/X` は
#   jail で開いても、ブラウザで開いても、tool の `url=` / `path=` に渡しても同じ物。これが
#   唯一の意図した重なりで、引数名は読み方(URL は percent-decode、path はそのまま)だけを決める。
#
# 橋渡しは「sandbox の `/ext/<name>/X` = URL `/ext/<name>/X`」— 配備は同じ物を sandbox の
# `/ext/<name>` と `EXT_MOUNTS` の `<name>` に置く規約(sandbox 側の bind の有無は nucllei からは
# 確かめられない)。2026-10-03〜05 は `EXT_DIR`(置いた物を暗黙に全部配る)だった。
SANDBOX_MOUNT_ROOT = "/ext"
_SANDBOX_MOUNT_PREFIX = SANDBOX_MOUNT_ROOT + "/"

# sandbox の workspace 参照。prefix を落とした残りが `/files/read` の相対 path。
WORKSPACE_PREFIX = "/workspace/"

# PDF ページ画像化の解像度。valve にしない — モデルが読める解像度は配備で変わらない。
PDF_RASTER_DPI = 150

# URL の **path 部** "/ext[/{rest}]"(query / fragment は urlsplit で分離済み)。
# 絶対 URL なら host は問わない(module docstring)。rest は `<name>/<sub>`
# (percent-encoded のまま)。空でもよい(name 無し → not_found)。
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

# file の id(Files のレコード id は uuid4 — routers/files・sandbox の登録とも)
_FILE_ID_FORM = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
# スキーム付き(`http:` `ftp:` …)か `//host` — URL のつもりで書かれた値
_URL_LIKE = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.\-]*:|//)")


def ref_args_rule_for(arg: str, value: str) -> str:
    """形の誤りのエラーに `REF_ARGS_RULE` を添えるか(module docstring「エラーに添える固定文」)。

    添えるのは **別の引数の値を取り違えて渡したらしい** ときだけ — url に URL でない値
    (sandbox のパス・id・ファイル名)、path に URL / 配信口 URL / id、file_id に id の形でない値。
    値が正しい種類で形だけ崩れている(path に相対パス `work/…`・`/tmp/…`、url に `ftp://…`)
    なら添えない — 取り違えの規則はその誤りと無関係で、正しい次の一手(受ける形)を薄める
    (llens staging 2026-10-04: write_file の `work/…` に規則が付いていた)。添える・添えないは
    誤りの種類で決め、tool では分けない。空の値には添えない。戻り値は規則の文か ""。
    """
    v = (value or "").strip()
    if not v:
        return ""
    if arg == "url":
        mixed = not _URL_LIKE.match(v)
    elif arg == "path":
        mixed = bool(
            _URL_LIKE.match(v)
            or INTERNAL_FILE_URL_PATTERN.match(v)
            or _FILE_ID_FORM.match(v)
        )
    elif arg == "file_id":
        mixed = not _FILE_ID_FORM.match(v)
    else:
        mixed = False
    return REF_ARGS_RULE if mixed else ""


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
    """読めた中身。`sink` を渡して読んだ (`spooled`) ときは中身は sink に書いてあり、`data` は
    型の判定に使った先頭 (`_SNIFF_BYTES`) だけ、`size` が全体のバイト数(send_to_workspace(url=) が
    1 GiB 級を一時ファイルに落として sandbox へストリーミングで渡すため)。"""

    data: bytes
    content_type: str
    name: str
    size: int | None = None
    spooled: bool = False


# sink に落とすときに手元に残す先頭のバイト数(`_sniff` の magic に足りる長さ)
_SNIFF_BYTES = 64


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
      (`/ext/` 自体を含む)・表に無い name・静的 mount の外へ出る symlink、一般の URL・proxy の
      上流の 404、workspace の 404
    - `too_large` — サイズ上限超過(file・http・workspace の 3 経路とも)。
      `size` は実サイズ(http stream 打ち切りなら Content-Length か打ち切り時点の
      受信量)、`limit` は上限。`size_exact` は `size` が確定値か下限かの区別 —
      True: stat(静的 mount / sandbox 413)・全量読了(workspace)・
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

    - `kind` — `"ext"`(`/ext/{rest}` = `EXT_MOUNTS` の対応表で読む物)、`"general"`
      (`/ext/` 形でない **一般の URL**。module docstring「一般の URL」)、`"file"`
      (このサーバのファイル配信口 `/api/v1/files/{id}/content` — `route_of_url` だけが返す。
      `resolve_url` は読まない: 権限検査が要るので呼び手が `file_id_of_url` で読む)
    - `rel` — `"ext"`: `/ext/` 以降の `<name>/<sub>`(URL に書かれたまま = percent-encoded。先頭 `/`・
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


def detect_media_type(loaded: LoadedRef) -> str | None:
    """種別を決めた型(`image/*` か `application/pdf`、パラメータを除いた小文字)。どちらでも
    なければ None。magic bytes 優先、次に content-type、次に拡張子。

    `image/*` は形式を絞らない — どの形式をモデルに渡すかは呼び手が決める(feed_image は
    VLM に渡せる形式だけを通す)。"""
    for content_type in (
        _sniff(loaded.data),
        loaded.content_type,
        mimetypes.guess_type(loaded.name)[0],
    ):
        base = (content_type or "").split(";")[0].strip().lower()
        if base.startswith("image/") or base == "application/pdf":
            return base
    return None


def detect_kind(loaded: LoadedRef) -> str | None:
    """ "image" / "pdf" / None(`detect_media_type` の大分類)。"""
    media_type = detect_media_type(loaded)
    if media_type is None:
        return None
    return "pdf" if media_type == "application/pdf" else "image"


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
    inside: Callable[[Path], bool],
    target: Path,
    label: str,
    limit: int,
    sink: BinaryIO | None = None,
) -> _Result:
    """resolve した実体が `inside` を満たす通常ファイルだけを読む。blocking — thread で
    呼ぶ。`inside` は閉じ込め: URL `/ext/{rest}` も sandbox パス `/ext/{rest}` も、静的 mount の
    root の中(/ext ゲート `routers/ext.py` の `static_file` と同じ判定)。"""
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
        if sink is not None:
            with resolved.open("rb") as f:
                head = f.read(_SNIFF_BYTES)
                sink.write(head)
                shutil.copyfileobj(f, sink, 1024 * 1024)
            return (
                LoadedRef(
                    data=head,
                    content_type=_content_type_for(head, None, resolved.name),
                    name=resolved.name,
                    size=size,
                    spooled=True,
                ),
                None,
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


def _static_root(mount: ExtMount) -> Path | None:
    """静的 mount の root(resolve 済み — 閉じ込めの基準)。判定できなければ None。blocking。"""
    try:
        return Path(mount.target).resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def static_file(mount: ExtMount, sub: str) -> tuple[Path, str] | None:
    """静的 mount の `sub`(decode 済み、mount の root からの相対)が配信される通常ファイルなら
    `(resolve 済みの実体, 正規形の sub)`。正規形の sub は root からの実体の相対(mount の中の
    symlink は実体に畳む)。不在・ディレクトリ・root の外へ出る symlink・判定不能は None。
    blocking — thread で呼ぶ。/ext ゲート(routers/ext.py)・resolver・橋渡しの共通の判定。
    traversal は呼び手が先に弾いていること。"""
    root = _static_root(mount)
    if root is None:
        return None
    base = _confine_base(mount, root, sub)
    if base is None:
        return None
    try:
        resolved = (root / sub.lstrip("/")).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if not resolved.is_relative_to(base) or not resolved.is_file():
        return None
    return resolved, resolved.relative_to(root).as_posix()


def _confine_base(mount: ExtMount, root: Path, sub: str) -> Path | None:
    """閉じ込めの基準。通常は mount の root、`/ext/groups` は `<root>/<グループ名>`(#152 —
    グループの中の symlink で他グループへ出させない。`ext_acl.confine_depth`)。グループの
    ディレクトリ自体が symlink・名前として使えない・無いときは None。blocking。"""
    depth = confine_depth(mount.name)
    if not depth:
        return root
    head = sub.lstrip("/").split("/")[:depth]
    if len(head) < depth or not all(is_group_dir_name(h) for h in head):
        return None
    base = root.joinpath(*head)
    try:
        # 段ごとに symlink でないこと(resolve の結果が字面どおり = 経路に symlink が無い)
        if base.resolve() != base or not base.is_dir():
            return None
    except (OSError, RuntimeError, ValueError):
        return None
    return base


def _ext_parts(rest: str, *, decode: bool) -> tuple[ExtMount | None, str] | None:
    """`/ext/` 以降の `rest` を `(mount, sub)` に。URL の rest は percent-decode してから、
    sandbox のパスはそのまま(`decode`)分ける。decode 前後のどちらかに traversal があれば None。
    表に無い name は `(None, sub)`。"""
    if has_path_traversal(rest):
        return None
    decoded = unquote(rest) if decode else rest
    if has_path_traversal(decoded):
        return None
    name, sub = split_ext_rest(decoded)
    return ext_mount(name), sub


def _visible(user, mount: ExtMount, sub: str) -> bool:
    """`/ext/<name>/<sub>` がこの user に見えるか(`ext_acl.ext_access` が none でない。#152)。
    見えない物は呼び手が「無い」と同じ扱いにする(存在を漏らさない)。"""
    return ext_access(user, f"{mount.name}/{sub}") != "none"


async def _load_ext(
    rest: str,
    limit: int,
    *,
    user=None,
    query: str = "",
    decode: bool = True,
    hops: int = 0,
    sink: BinaryIO | None = None,
) -> _Result:
    """`/ext/{rest}` を読む — 静的の name はファイル、proxy の name は上流へ GET
    (`_load_proxy`)、表に無い name は not_found。`/ext/groups` `/ext/public` は `user` の
    ACL(`ext_acl`)で見えなければ not_found(#152)。"""
    parts = _ext_parts(rest, decode=decode)
    if parts is None:
        log.warning("image_refs: rejecting traversal ext path")
        return _fail("traversal", "traversal in ext path")
    mount, sub = parts
    label = f"ext:{rest}"
    if mount is None:
        log.warning(f"image_refs: no /ext mount for this name ({label})")
        return _fail("not_found", f"no /ext mount for this name ({label})")
    if not _visible(user, mount, sub):
        log.warning(f"image_refs: not visible to this user ({label})")
        return _fail("not_found", f"not visible to this user ({label})")
    if mount.proxy:
        return await _load_proxy(mount, sub, query, limit, hops, user, sink)
    root = await asyncio.to_thread(_static_root, mount)
    if root is None:
        return _fail("not_found", f"cannot resolve mount root ({label})")
    base = await asyncio.to_thread(_confine_base, mount, root, sub)
    if base is None:
        return _fail("not_found", f"no such place ({label})")
    return await asyncio.to_thread(
        _read_confined, _is_under(base), root / sub.lstrip("/"), label, limit, sink
    )


async def _load_proxy(
    mount: ExtMount,
    sub: str,
    query: str,
    limit: int,
    hops: int,
    user=None,
    sink: BinaryIO | None = None,
) -> _Result:
    """proxy の name を上流へ GET する(`EXT_MOUNTS` の固定の相手 — 自分自身判定は掛けない)。
    404 → not_found、2xx 以外 → upstream_status、接続失敗・タイムアウト → upstream_unreachable、
    上限 → too_large(`_read_response`)。redirect は上流の base 配下なら `/ext/<name>/…` として
    辿る(ゲートの書き換えと同じ判定 `under_base`。`MAX_REDIRECTS` まで)、外なら upstream_status。
    cookie / 認証ヘッダは送らない(共有クライアント)。"""
    url = upstream_url(mount, sub, query)
    label = f"ext proxy name={mount.name}"
    try:
        request = get_ext_client().build_request("GET", url, timeout=proxy_timeout())
        response = await get_ext_client().send(request, stream=True)
        try:
            status = response.status_code
            location = response.headers.get("location")
            if status not in _REDIRECT_STATUSES:
                return await _read_response(
                    response, label, os.path.basename(sub), limit, sink
                )
            if hops >= MAX_REDIRECTS:
                log.warning(f"image_refs: too many redirects ({label})")
                return _fail(
                    "upstream_status",
                    f"more than {MAX_REDIRECTS} redirects ({label})",
                    status=status,
                    reason="too_many_redirects",
                )
            remainder = (
                under_base(urljoin(url, location), mount.target)
                if location and not has_path_traversal(location)
                else None
            )
            if remainder is None:
                log.warning(
                    f"image_refs: redirect outside the upstream base ({label}, "
                    f"status={status})"
                )
                return _fail(
                    "upstream_status",
                    f"redirect {status} outside the upstream base ({label})",
                    status=status,
                )
        finally:
            await response.aclose()
    except (httpx.HTTPError, httpx.InvalidURL) as e:
        log.warning(f"image_refs: ext proxy fetch error ({label}): {e!r}")
        return _fail(
            "upstream_unreachable",
            f"ext proxy fetch error ({label}): {type(e).__name__}: {e}",
        )
    path, _, q = remainder.partition("?")
    return await _load_ext(
        f"{mount.name}/{path}",
        limit,
        user=user,
        query=f"?{q}" if q else "",
        hops=hops + 1,
        sink=sink,
    )


async def _read_response(
    response: httpx.Response,
    label: str,
    filename: str,
    limit: int,
    sink: BinaryIO | None = None,
) -> _Result:
    """開いたストリーム応答を LoadedRef にする(一般の URL)。`sink` があれば中身はそこへ
    チャンクのまま書き、メモリには先頭だけを残す(`LoadedRef.spooled`)。
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
        if sink is None:
            chunks.append(chunk)
            continue
        if total - len(chunk) < _SNIFF_BYTES:
            chunks.append(chunk[: _SNIFF_BYTES - (total - len(chunk))])
        await asyncio.to_thread(sink.write, chunk)
    data = b"".join(chunks)
    if sink is not None:
        return (
            LoadedRef(
                data=data,
                content_type=_content_type_for(
                    data, response.headers.get("content-type"), filename
                ),
                name=filename,
                size=total,
                spooled=True,
            ),
            None,
        )
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
    url: str,
    limit: int,
    hops: int,
    redirect_status: int | None,
    user=None,
    sink: BinaryIO | None = None,
) -> _Result:
    """一般の URL(`/ext/` 形でない絶対 URL。`_route_url` が `kind="general"` と
    判定済み)を直接取得する(module docstring「一般の URL」)。

    - ホストのアドレスを決めて接続を固定(`pin_address`): IP リテラルはそのまま判定(閉域の
      本番は DNS が無く、院内の URL は IP リテラルが普通)、名前は `getaddrinfo`(`/etc/hosts`
      も効く)。自分自身 → `unknown_upstream`(reason `server_self`)、名前が引けない →
      `upstream_unreachable`(reason `unresolvable`)。それ以外は行き先で絞らない
    - 3xx + Location は `_load_url` に転送先を渡して辿る(hops を数える)。転送先は URL と
      して同じ規則で解決される — 一般の URL ならまた固定して取得、`/ext/{rest}` なら
      `EXT_MOUNTS` の対応表で読む。転送先が URL として組めなければ `upstream_status`(reason
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
                    response, label, os.path.basename(parts.path), limit, sink
                )
        finally:
            await response.aclose()
    except (httpx.HTTPError, httpx.InvalidURL) as e:
        log.exception(f"image_refs: general fetch error ({label})")
        return _fail(
            "upstream_unreachable",
            f"general fetch error ({label}): {type(e).__name__}: {e}",
        )
    return await _load_url(target, limit, hops + 1, status, user=user, sink=sink)


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
        # 上限は sandbox にも渡す (sandbox は自分の安全上限との小さいほう。超えれば 413 →
        # too_large。大きなファイルを転送してから断らない)
        res = await sandbox.sandbox_post(
            "/files/read",
            {"user_id": user_id, "path": rel_path, "file_limit_bytes": limit},
            60,
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
    形。nucllei の fs ではない — 読むときは `EXT_MOUNTS` の対応表で読む)。"""
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
    # rest が無い / 空 = name 無し(rel "")。他の rel と同じに扱う(読めば not_found)
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
    url: str,
    limit: int,
    hops: int = 0,
    redirect_status: int | None = None,
    *,
    user=None,
    sink: BinaryIO | None = None,
) -> _Result:
    """URL(traversal / 制御文字は入口で拒否済み)を解決する: `/ext/{rest}` は `EXT_MOUNTS` の
    対応表で読み(`_load_ext`)、一般の URL は直接取得(`_load_general`)。`hops` は一般の URL で
    辿った redirect の回数、`redirect_status` はこの URL を指した redirect の status(転送先の
    再解決でだけ付く)。"""
    route, err = _route_url(url)
    if route is None:
        # warning はここで出す(`_route_url` は probe からも呼ばれる — 二重に残さない)
        log.warning(f"image_refs: {err.detail} ({url[:80]!r})")
        return None, err
    if route.general:
        return await _load_general(url, limit, hops, redirect_status, user, sink)
    return await _load_ext(route.rel, limit, user=user, query=route.query, sink=sink)


async def mounted_path_for_url(url: str, user=None) -> str | None:
    """URL(絶対 / 同一サーバの `/ext/…`)が `/ext/<name>/<sub>` 形で、name が **静的** の mount
    かつ `<sub>` が実在する通常ファイルなら、sandbox 側のパス(`/ext/<name>/<sub>` — 相対 URL と
    同じ文字列。sub は percent-decode 済み・symlink を畳んだ形)を返す。それ以外
    (一般の URL・proxy の name・表に無い name・不在・ディレクトリ・root の外へ出る symlink・
    traversal・URL でない)は None — 理由は返さない(要る呼び手は `resolve_url` を続けて呼べば
    同じ規則で RefError が出る)。

    用途は「URL → sandbox の fs パス」の橋渡し(builtin sandbox の `send_to_workspace`):
    配備は同じ物を sandbox の `/ext/<name>` と `EXT_MOUNTS` の `<name>` に置く規約(#138)なので、
    URL が指す実体は sandbox からも見えていて、コピーする必要が無い。**proxy の name は None**
    — 上流が返す物(cage が描画した HTML 等)が jail の同じパスに在るとは限らないので、
    呼び手は `resolve_url`(上流へ GET)で取り込む。sandbox 側の bind の有無は nucllei からは
    確かめられない(見るのは nucllei 側の事実だけ)。
    """
    if not isinstance(url, str) or not _is_url(url):
        return None
    if has_path_traversal(url):
        return None
    route, _ = _route_url(url)
    if route is None or route.kind != "ext":
        # 一般の URL は配信元を持たない(sandbox に同じ実体は無い)
        return None
    parts = _ext_parts(route.rel, decode=True)
    if parts is None or parts[0] is None or parts[0].proxy:
        return None
    mount, sub = parts
    if not _visible(user, mount, sub):
        # #152: user に見えない置き場(他グループ)は jail にも無い
        return None
    served = await asyncio.to_thread(static_file, mount, sub)
    if served is None:
        return None
    return f"{_SANDBOX_MOUNT_PREFIX}{mount.name}/{served[1]}"


async def _proxy_served_rest(mount: ExtMount, sub: str) -> str | None:
    """proxy の上流が `<base>/<sub>` を(base の中の redirect を辿った先で)2xx で返すなら、
    その最終的な base からの残り(percent-encoded・`?query` 込み)。HEAD で確かめる(405 なら
    GET を開いてすぐ閉じる)。

    redirect は `_load_proxy` と同じ判定(`under_base`)で、Location が base 配下なら辿る —
    cage は doc の正規形でない住所(`…/_index.md`・`.md` 無し・末尾 `/` 無し)を 302 で正規形へ
    飛ばすので、飛び先で配信されていればその住所を返す(llens 2026-10-06)。base の外への
    redirect・`MAX_REDIRECTS` を超えた・2xx 以外・不達は None(= 配信されていない扱い)。"""
    client = get_ext_client()
    url = upstream_url(mount, sub)
    rest = quote(sub, safe="/")
    try:
        for _ in range(MAX_REDIRECTS + 1):
            for method in ("HEAD", "GET"):
                request = client.build_request(method, url, timeout=proxy_timeout())
                response = await client.send(request, stream=True)
                await response.aclose()
                if response.status_code != 405:
                    break
            if response.is_success:
                return rest
            location = response.headers.get("location")
            if response.status_code not in _REDIRECT_STATUSES or not location:
                return None
            if has_path_traversal(location):
                return None
            target = urljoin(url, location)
            remainder = under_base(target, mount.target)
            if remainder is None:
                return None
            url, rest = f"{mount.target}/{remainder}", remainder
        log.warning(
            f"image_refs: too many redirects in ext proxy probe (name={mount.name})"
        )
    except (httpx.HTTPError, httpx.InvalidURL) as e:
        log.warning(f"image_refs: ext proxy probe failed (name={mount.name}): {e!r}")
    return None


def _static_url(mount: ExtMount, sub: str) -> str | None:
    """静的 mount の `sub` が配信される通常ファイルなら、その配信 URL(相対
    `/ext/<name>/<sub>`、sub は実体の正規形を percent-encode)。blocking — thread で呼ぶ。
    組んだ URL を decode して同じ実体に戻ることを確かめてから返す(ゲートが 400 で断る名前 —
    制御文字・バックスラッシュ — は None)。"""
    served = static_file(mount, sub)
    if served is None:
        return None
    resolved, canonical = served
    url = f"{_EXT_URL_PREFIX}{mount.name}/{quote(canonical, safe='/')}"
    if has_path_traversal(url):
        return None
    back = static_file(mount, unquote(quote(canonical, safe="/")))
    if back is None or back[0] != resolved:
        return None
    return url


async def url_for_mounted_path(path: str, user=None) -> str | None:
    """sandbox の fs パス(`/ext/<name>/<sub>`)が **nucllei の /ext ゲートでも同じ URL で配信
    されている** なら、その URL(同一サーバの相対 `/ext/<name>/<sub>` — ASCII 名なら入力と同じ
    文字列、日本語等は percent-encode 済み)を返す:

    - 静的の name: `<dir>/<sub>` が実在する通常ファイル(root の外へ出る symlink は不可)
    - proxy の name: 上流が `<base>/<sub>` を 2xx で返す(HEAD で確かめる — `_proxy_served_rest`)。
      上流が base の中へ redirect する(cage の `…/_index.md` → `…/` 等)なら辿り、**飛び先の**
      `/ext/<name>/…` を返す

    それ以外(表に無い name・不在・ディレクトリ・`/workspace/…`・パスでない)は None — 理由は
    返さない(要る呼び手は `resolve_path` を続けて呼べば同じ境界で RefError が出る)。

    `mounted_path_for_url` の逆向きで、用途は「sandbox のパス → 利用者に見せられる URL」の
    橋渡し(builtin sandbox の `fetch_from_workspace`): 同じ物が既に `/ext/…` で配信されて
    いるなら、コピーして file にする必要が無い。前提は配備の規約(#138: sandbox の
    `/ext/<name>` と `EXT_MOUNTS` の `<name>` に同じ物を置く)— sandbox 側のそのパスが同じ
    実体かは nucllei からは確かめられない。公開範囲は広げない: 返す URL は /ext ゲートが
    既に配信している物だけ。
    """
    if not isinstance(path, str) or not path.startswith(_SANDBOX_MOUNT_PREFIX):
        return None
    parts = _ext_parts(path[len(_SANDBOX_MOUNT_PREFIX) :], decode=False)
    if parts is None or parts[0] is None:
        return None
    mount, sub = parts
    if not sub:
        return None
    if not _visible(user, mount, sub):
        return None  # #152: user に配信されない物の URL は返さない
    if mount.proxy:
        rest = await _proxy_served_rest(mount, sub)
        if rest is None:
            return None
        return f"{_EXT_URL_PREFIX}{mount.name}/{rest}"
    return await asyncio.to_thread(_static_url, mount, sub)


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


async def resolve_url(
    url: str, *, max_bytes: int | None = None, user=None, sink: BinaryIO | None = None
) -> _Result:
    """**URL として** 解決する(tool の `url` 引数の入口)。

    受けるのはスキーム付きの絶対 URL(`http(s)://…`)と、同一サーバの相対 URL
    `/ext/{rest}`。値の中身を見て別の意味(sandbox のパス・file_id)に読み替えない —
    URL として解決できないものは `unsupported_form`。`/api/v1/files/{id}/content` は
    呼び手が `file_id_of_url` で先に拾う(module docstring「URL の解決」)。
    `user`(#152)は `/ext/groups` `/ext/public` の ACL(`utils/ext_acl`)に使う — 見えない物は
    not_found(redirect の先も同じ)。None なら ACL の置き場は全部見えない(fail closed)。
    それ以外の `/ext/<name>` の公開範囲は配備が `EXT_MOUNTS` に書いた物で、per-user ではない。

    戻り値・サイズ上限・失敗 code は `resolve_ref` と同じ。`sink`(書ける binary file)を渡すと
    中身はメモリに貯めずにそこへ書く(`LoadedRef.spooled` — send_to_workspace(url=) が一時ファイルに
    落とすため)。失敗したときに sink に途中まで書かれていることがある(捨てるのは呼び手)。
    """
    limit = FETCH_MAX_BYTES if max_bytes is None else max_bytes
    err = _check_url(url)
    if err is not None:
        if err.code == "unsupported_form":
            log.warning(f"image_refs: not a resolvable URL ({str(url)[:80]!r})")
        return None, err
    return await _load_url(url, limit, user=user, sink=sink)


async def resolve_path(path: str, user, *, max_bytes: int | None = None) -> _Result:
    """**sandbox の fs の絶対パスとして** 解決する(tool の `path` 引数の入口)。

    受けるのは `/workspace/…`(呼び出しユーザーの workspace)と `/ext/…`(sandbox の read-only
    の参照データ — URL `/ext/…` と同じ `EXT_MOUNTS` の対応表で読む。URL の
    `/ext/{rest}` と同じ文字列だが、ここでは fs のパスとして percent-decode せずに読む)。
    URL が来ても URL としては解決しない — パスとして `unsupported_form`。相対パスも
    `unsupported_form`(`path` の形は絶対パス 1 つ)。

    `user` は呼び出しユーザー — `UserModel` / tool の `__user__` dict / user id の文字列
    (#152 までは user id だけを取っていた。文字列は role 不明 = 一般ユーザー扱い)。
    workspace はその user のもの、`/ext/groups` `/ext/public` は ACL(`utils/ext_acl`)で決まる。

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
        return await _load_workspace(
            path[len(WORKSPACE_PREFIX) :], _user_id_of(user), limit
        )
    # sandbox の `/ext/<name>/<sub>` は URL `/ext/<name>/<sub>` と同じ物(配備の規約)— 同じ
    # 対応表で読む(静的はファイル、proxy は上流へ GET)。fs のパスなので percent-decode しない
    return await _load_ext(
        path[len(_SANDBOX_MOUNT_PREFIX) :], limit, user=user, decode=False
    )


def _user_id_of(user) -> str | None:
    if isinstance(user, str) or user is None:
        return user
    if isinstance(user, dict):
        return user.get("id")
    return getattr(user, "id", None)


async def resolve_ref(ref: str, user, *, max_bytes: int | None = None) -> _Result:
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
        return await resolve_url(ref, max_bytes=max_bytes, user=user)
    if kind == "path":
        return await resolve_path(ref, user, max_bytes=max_bytes)
    log.warning(f"image_refs: unsupported reference form ({ref[:80]!r})")
    return _fail(
        "unsupported_form",
        "reference is neither a URL (http(s):// or same-server /ext/) nor a "
        f"sandbox path (/workspace/ or {_SANDBOX_MOUNT_PREFIX})",
    )


async def load_ref(ref: str, user) -> LoadedRef | None:
    """`resolve_ref` の薄い wrapper — 解決できなければ None(理由を要らない呼び手向け)。
    上限は従来どおり FETCH_MAX_BYTES(引数は増やさない)。"""
    loaded, _ = await resolve_ref(ref, user)
    return loaded


def log_mounts() -> None:
    """起動時 1 行: `/ext/<name>` の対応表(`EXT_MOUNTS`)と、静的の root が今見えているか。"""
    from nucllei import env

    if env.EXT_MOUNTS:
        entries = []
        for name, target in env.EXT_MOUNTS.items():
            mount = ExtMount(name, target)
            if mount.proxy:
                entries.append(f"/ext/{name}/ → proxy {target}/")
            else:
                ok = Path(target).is_dir()
                entries.append(
                    f"/ext/{name}/ → static {target}/"
                    + ("" if ok else " (WARNING: ディレクトリが無い)")
                )
        log.info(
            f"[nucllei] /ext mounts (EXT_MOUNTS。sandbox は同じ物を "
            f"{SANDBOX_MOUNT_ROOT}/<name> に見る規約): " + " / ".join(entries)
        )
    else:
        log.info("[nucllei] EXT_MOUNTS が空 — /ext/** は全て 404")
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
