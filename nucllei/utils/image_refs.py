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
  解決する — `/ext/{name}/…` → `resolve_url`、`/api/v1/files/{id}/content` →
  `file_id_of_url` で id を取り、呼び手が自分の権限検査で読む)
- **`path` = sandbox の fs の絶対パス**(`/workspace/…` / `/mnt/…`)→ `resolve_path`。
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
| `/ext/`                      | `url`     | 同一サーバの URL                   | `resolve_url`                  |
| `/workspace/`                | `path`    | sandbox の fs(ユーザーの workspace)| `resolve_path`               |
| `/mnt/`                      | `path`    | sandbox の fs(RO マウント)       | `resolve_path`                 |
| `/` 始まりでない裸の文字列   | `file_id` | file の id(`__feed_image__(file_id)` が積む形)| 呼び手(`/api/v1/files/{id}/content` に組む)|
| それ以外(他の `/…`・空)    | —         | —                                  | `unsupported_form`             |

FE の `chat-messages.ts`(`contextImageUrl`)は「`/` 始まりと `http(s)://` はそのまま、
それ以外は file id」で、この表と同じ結果になる(`/` 始まりの中の区分は送信直前の
BE が行う)。

**URL の解決**(絶対・相対とも `_route_url` 1 箇所):

- path が `/ext/{name}/{rest}` なら EXT_UPSTREAMS の backend(`ext_upstreams.ext_backend`)で
  解決する。絶対 URL は **host を問わない** — 解決が backend 経由で、与えられた host には
  接続しないため(SSRF の真実源は EXT_UPSTREAMS 一本)。相対 `/ext/{name}/{rest}` は
  「今のサーバの URL」= 同じ backend。
- それ以外の絶対 URL は env EXT_PUBLIC_BASES(`name=院内 web の原本 URL のルート`)の
  いずれかの `<base>` そのもの、または `<base>/` に前方一致すれば、その name の backend に
  残り(`rel`)を渡して解決する(`ext_upstreams.match_public_base`)。
- **name の直下(rel が空)も正しい形** — `/ext/{name}`・`/ext/{name}/`・原本の `<base>`・
  `<base>/` はどれも「その name のルート」を指す(rel = `""`。末尾 `/` の有無は区別しない)。
  特別扱いはせず、他の rel と同じに backend へ渡す: http backend は上流の `<base>/` を GET
  する(/ext ゲートが `/ext/{name}/` を中継するのと同じ URL。上流が redirect を返せば
  `upstream_status`)。file backend は root = ディレクトリなので `not_found`(一覧は
  出さない — ゲートも 404)。2026-10-03 までは原本の `<base>/` を上流に問い合わせずに
  `not_found` にしていて、実在するトップページ(上流は 302)が「リンク切れ」と案内された。
- どれにも当たらなければ失敗 `unknown_upstream`(fail closed。任意の URL を取りに行く
  口ではない)。
- file backend なら root 内をローカル読み、http backend なら httpx で取得
  (content-type は上流ヘッダ)。file backend が配信する範囲は /ext ゲートと同じ判定
  (`ext_upstreams.ext_file_served`: root の中 + env EXT_FILE_SCOPES)— 範囲外は `not_found`。
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

- `/mnt/...`(`RO_MOUNT_ROOT` 配下)— nucllei 自身に read-only で bind された参照用
  データ(sandbox と同じ置き場 #138)。resolve() して root 内でなければ読まない。
- `/workspace/...` — sandbox の `/files/read`(呼び出しユーザーの workspace に閉じる)。
  jail 内で作ったものをモデルが見るだけなら、Files に登録する必要は無い。

**URL と sandbox のパスの橋渡し**(同じ実体が両方の名前を持つとき)。モデルは参照を
読み替えない — 読み替えが要る場面は tool が **解決結果** に基づいて橋渡しする(値の形では
なく、解決した先の実ファイルがどこにあるかで決まる):

| 向き                          | 関数                    | 使う tool                    |
| ----------------------------- | ----------------------- | ---------------------------- |
| URL → sandbox で開けるパス    | `mounted_path_for_url`  | `send_to_workspace(url=)`    |
| sandbox のパス → 見せられる URL | `url_for_mounted_path` | `fetch_from_workspace(path)` |

どちらも「EXT_UPSTREAMS の file backend が配信する範囲(root の中。env EXT_FILE_SCOPES が
あればそのディレクトリの中 — `ext_upstreams.ext_file_served`)、かつ `RO_MOUNT_ROOT` 配下の
実ファイル」のときだけ値を返す(`/ext/{name}/{rest}` ↔ `<root>/{rest}`。rest は URL 側が
percent-encoded、fs 側が decode 済み — なので `url_for_mounted_path` が返す URL は、
文書の本文にある生の日本語 URL とは文字列が一致しないことがある。同じファイルの別表記)。
利用者に見せられるのは URL だけ(sandbox のパスはブラウザでは開けない)なので、
どの file backend の root 配下でもない `/mnt/…` は tool が file にする(`resolve_path` で
読んで添付 — 重複になるが他に手段が無い)。**file backend の root 配下にあって配信範囲の
外**の物は、添付にもしない(`mounted_path_withheld` — 配信範囲の設定は「利用者に渡して
よい範囲」という 1 つの意味を持つ。URL では 404 なのに添付なら渡る、を作らない)。

**相対 `/ext/…` の経緯**: #139(2026-09-07)は「"/" 始まり = sandbox の fs だけ、`/ext/…` は
受けない(失敗 `relative_ext`)、URL は必ず絶対」と決めていた。理由は取り違え — URL と
fs パスが同じ "/" 始まりで同居すると、モデルが `/ext/` を fs と誤認したり fs パスを URL に
書き換えたりする。2026-10-03 に覆した(ken 決定): 文書の図は相対 `/ext/cage/static/X` の
ままモデルに渡す。生成物の本文リンク `/api/v1/files/{id}/content` が相対で運用できているのと
同じで、相対なら origin や http/https が変わっても表示が壊れず、origin を補う仕組み
(リクエストヘッダから組む・組めないときの fallback・サブエージェント経由で別ホストになる
問題)が要らなくなる。`/` 始まりが URL か sandbox のパスかは **引数名**(`url` / `path`)と
文脈(markdown のリンク・画像か、コードの中か)で決まる。既存の絶対 URL
(`https://<任意の host>/ext/{name}/…`)は引き続き受ける。

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
from functools import partial
from pathlib import Path
from typing import Callable
from urllib.parse import quote, unquote, urlsplit

import httpx

from nucllei.builtin_tools import sandbox
from nucllei.env import (
    EXT_FILE_SCOPES,
    EXT_INLINE_MAX_BYTES,
    EXT_PUBLIC_BASES,
    EXT_UPSTREAMS,
    SRC_LOG_LEVELS,
)
from nucllei.utils.ext_upstreams import (
    ext_backend,
    ext_file_served,
    ext_file_withheld,
    get_ext_client,
    has_path_traversal,
    match_public_base,
)

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])

# 参照用データの置き場。sandbox(`sandbox/app/main.py`)と同じ定数で、env にしない —
# 配備側は compose の volume を `/mnt/<name>` に足すだけ(#138 と同じ流儀)。
# 呼び出し時に読む(import 時に固定しない)ので、テストは module 属性を差し替える。
RO_MOUNT_ROOT = Path("/mnt")

# sandbox の workspace 参照。prefix を落とした残りが `/files/read` の相対 path。
WORKSPACE_PREFIX = "/workspace/"

# PDF ページ画像化の解像度。valve にしない — モデルが読める解像度は配備で変わらない。
PDF_RASTER_DPI = 150

# URL の **path 部** "/ext/{name}[/{rest}]"(query / fragment は urlsplit で分離済み)。
# 絶対 URL なら host は問わない(module docstring)。name の文字種は EXT_UPSTREAMS の
# パース(env.py)と同じ [a-z0-9_-]+。rest は空でもよい(name の直下 = ルート。
# `/ext/{name}` と `/ext/{name}/` は同じ)— name が取れない形だけが一致しない。
EXT_URL_PATH_PATTERN = re.compile(r"^/ext/([a-z0-9_-]+)(?:/(.*))?$")

# tool の引数が受ける形の固定文(module docstring「エラーに添える固定文」)。公開名 —
# builtin(sandbox / attachment_text)と配備層の tool が import して、形の誤りのエラーに
# 同じ文を使う(llens の tool と文面を合意済み、2026-10-03。変えるときは両側を揃える)。
URL_ARG_FORM = (
    "url が受けるのは、スキーム付きの絶対 URL (`http(s)://…`) と、同一サーバの "
    "`/ext/…`・`/api/v1/files/{id}/content` だけ。"
)
PATH_ARG_FORM = "path が受けるのは、sandbox の絶対パス (`/workspace/…` `/mnt/…`) だけ。"
# workspace に書く / 一覧する tool(read-only の `/mnt` を扱えない)用
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
      同一サーバの `/ext/…`)でないもの、`resolve_path` なら `/workspace/` `/mnt/` で
      ないもの、`resolve_ref` ならそのどちらでもないもの(相対パス・空・`ftp:` 等)
    - `traversal` — 制御文字・`..`・バックスラッシュ・NUL
    - `unknown_upstream` — URL のルーティングの失敗。内訳は `reason`(下記)
    - `not_found` — file backend / mount でファイルが無い・ディレクトリ(file backend の
      name 直下 `/ext/{name}/` を含む)・root 外、http backend の 404、workspace の 404
    - `too_large` — サイズ上限超過(file・http・workspace の 3 経路とも)。
      `size` は実サイズ(http stream 打ち切りなら Content-Length か打ち切り時点の
      受信量)、`limit` は上限。`size_exact` は `size` が確定値か下限かの区別 —
      True: stat(file backend / `/mnt` / sandbox 413)・全量読了(workspace)・
      Content-Length(http)。False: Content-Length の無い http stream を打ち切った
      時点の受信量(実サイズは不明で、`size` は limit を少し超えた値になる)。
      too_large 以外の code では常に False(意味を持たない)
    - `upstream_status` — http backend の 2xx 以外(404 は not_found。3xx = redirect も
      ここ)。`status` に HTTP status
    - `upstream_unreachable` — http backend への接続失敗・タイムアウト・InvalidURL
    - `workspace_unavailable` — SANDBOX_URL 無し / sandbox API 失敗(404 は not_found、
      413 は too_large に寄せる)

    `reason` は code の内訳(code の集合は変えずに、同じ code の中を言い分けるための
    安定したフィールド)。値はこの閉じた集合で、今は `unknown_upstream` にだけ付く
    (他の code では None):

    - `unknown_name` — `/ext/{name}/…` 形(絶対・相対)か EXT_PUBLIC_BASES 配下だが、
      その name が EXT_UPSTREAMS に無い(未知の配信元)
    - `not_ext_form` — `/ext/` 始まりの相対 URL だが **name が取れない**(`/ext/`・
      `/ext//x`・name の文字種 `[a-z0-9_-]+` に合わない `/ext/CAGE/x`)。`/ext/{name}`・
      `/ext/{name}/`(name の直下)は正しい形で、これには当たらない — backend のルートとして
      解決する(http は上流のルートを GET、file は `not_found`)
    - `outside_public_bases` — どの名前空間にも当たらない絶対 URL(`/ext/{name}/…` 形でも
      EXT_PUBLIC_BASES 配下でもない = 外部サイト)

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
    """URL が **どの backend の何か**(`route_of_url` / `_route_url` の結果)。

    - `name` — EXT_UPSTREAMS の name(存在は確認済み)
    - `rel` — name 配下の相対パス(URL に書かれたまま = percent-encoded。先頭 `/`・
      query・fragment を含まない)。name の直下(`/ext/{name}`・`/ext/{name}/`・原本の
      `<base>`・`<base>/`)は `""`
    - `query` — `"?a=b"` か `""`
    - `public_base` — その name の原本の公開 URL のルート(EXT_PUBLIC_BASES[name]。
      末尾スラッシュ無し)。無ければ None
    - `origin` — 入力が絶対 URL なら `"scheme://host[:port]"`(与えられたまま。接続は
      しない)、同一サーバの相対 URL なら None
    - `ext_form` — URL の path が `/ext/{name}/…` 形なら True、EXT_PUBLIC_BASES 配下の
      原本 URL として当たったなら False
    """

    name: str
    rel: str
    query: str
    public_base: str | None
    origin: str | None
    ext_form: bool

    @property
    def relative(self) -> bool:
        """入力が同一サーバの相対 URL(スキーム無し)だったか。"""
        return self.origin is None


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
    呼ぶ。`inside` は置き場ごとの閉じ込め: file backend は `ext_file_served`(root +
    EXT_FILE_SCOPES。/ext ゲートと共有)、`/mnt` 直読みは `RO_MOUNT_ROOT` の中。"""
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


def _ext_file_target(root: Path, path: str) -> Path | None:
    """file backend の root と URL の path から、読む先のパス(未 resolve)を作る。
    decode 後に traversal を含むなら None。

    URL の path は percent-encoded のまま来る(文書に焼かれた URL は日本語
    ディレクトリを %E7%A5%9E… で持つ)。ブラウザ側 routers/ext.py は Starlette が
    decode 済みの path を受けるので、同じ URL が同じファイルに当たるようここでも
    decode してから結合する。traversal 判定は decode 後で見る(念のため再判定)。
    先頭 "/" は Path 結合で root を捨てさせるので落とす(routers/ext.py と同じ扱い)。
    """
    decoded = unquote(path)
    if has_path_traversal(decoded):
        return None
    return root / decoded.lstrip("/")


async def _load_ext(name: str, path: str, query: str, limit: int) -> _Result:
    # name の存在は `_route_url` が確認済み(未知の name はそこで unknown_upstream)
    if has_path_traversal(path):
        log.warning(f"image_refs: rejecting traversal ext path (name={name})")
        return _fail("traversal", f"traversal in ext path (name={name})")
    kind, target = ext_backend(name)
    if kind == "file":
        candidate = _ext_file_target(target, path)
        if candidate is None:
            log.warning(f"image_refs: rejecting traversal ext path (name={name})")
            return _fail("traversal", f"traversal in ext path (name={name})")
        return await asyncio.to_thread(
            _read_confined,
            partial(ext_file_served, name),
            candidate,
            f"ext:{name}",
            limit,
        )
    # http backend へは encode されたまま渡す(上流が decode する)
    return await _fetch_http(f"{target}/{path}{query or ''}", name, path, limit)


async def _fetch_http(url: str, name: str, path: str, limit: int) -> _Result:
    """http backend。redirect は辿らない(共有クライアント)。404 → not_found、
    それ以外の 2xx 以外(3xx 含む)→ upstream_status、上限超過 → too_large、
    接続失敗 / タイムアウト / InvalidURL → upstream_unreachable。"""
    try:
        async with get_ext_client().stream("GET", url) as response:
            if not response.is_success:
                status = response.status_code
                log.warning(
                    f"image_refs: ext fetch failed (name={name}, status={status})"
                )
                if status == 404:
                    return _fail("not_found", f"upstream 404 (name={name})", status=404)
                return _fail(
                    "upstream_status",
                    f"upstream returned {status} (name={name})",
                    status=status,
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
                        f"(name={name}, size{'=' if exact else '>='}{size}, "
                        f"limit={limit})"
                    )
                    return _fail(
                        "too_large",
                        f"upstream content exceeds size limit (name={name})",
                        size=size,
                        limit=limit,
                        size_exact=exact,
                    )
                chunks.append(chunk)
            declared = response.headers.get("content-type")
    except (httpx.HTTPError, httpx.InvalidURL) as e:
        # InvalidURL は httpx.HTTPError の subclass でない(#97 review)。制御文字は
        # has_path_traversal で既に弾いているが、fail closed の約束を破らないよう
        # 明示的に合流させる。
        log.exception(f"image_refs: ext fetch error (name={name})")
        return _fail(
            "upstream_unreachable",
            f"upstream fetch error (name={name}): {type(e).__name__}: {e}",
        )
    data = b"".join(chunks)
    filename = os.path.basename(path)
    return (
        LoadedRef(
            data=data,
            content_type=_content_type_for(data, declared, filename),
            name=filename,
        ),
        None,
    )


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
    """`resolve_path` が受ける形か — `/workspace/…` か `RO_MOUNT_ROOT`(`/mnt`)配下。"""
    return ref.startswith((WORKSPACE_PREFIX, f"{Path(RO_MOUNT_ROOT)}/"))


def stored_ref_kind(ref) -> str | None:
    """**保存済みの参照文字列**(`context_images` のエントリ・image_url の url)が何かを、
    先頭で機械的に振り分ける(module docstring の表。規則はここ 1 箇所)。`#page=N` は
    付いていてもよい。

    - `"file_url"` — `/api/v1/files/{id}/content`(相対、または絶対 = host 不問)
    - `"url"` — `http(s)://…` / 同一サーバの `/ext/…`(`resolve_url` が受ける形)
    - `"path"` — `/workspace/…` / `/mnt/…`(`resolve_path` が受ける形)
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
    """URL(スキーム付き / 同一サーバの相対 `/ext/…`)が **どの backend の何か** を決める
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
    if match:
        # 自オリジンの表示 URL(相対ならそのまま今のサーバ)。host は見ない(module docstring)
        # rest が無い / 空 = name の直下(rel "")。他の rel と同じに backend へ渡す
        name, rel = match.group(1), match.group(2) or ""
        ext_form = True
    elif origin is None:
        # 相対で来るのは `/ext/` 始まりだけ(`_is_url`)— name が取れない形
        return None, RefError(
            "unknown_upstream",
            "same-server URL is not of the form /ext/{name}/{path} (no upstream name)",
            reason="not_ext_form",
        )
    else:
        hit = match_public_base(url)
        if hit is None:
            return None, RefError(
                "unknown_upstream",
                "absolute URL is neither an /ext/{name}/ URL nor under "
                f"EXT_PUBLIC_BASES (external site: {origin})",
                reason="outside_public_bases",
            )
        name, rest = hit
        # rel が空 = 原本のルートそのもの。閉じずに backend へ渡す(module docstring)
        rel, sep, rel_query = rest.split("#", 1)[0].partition("?")
        query = f"?{rel_query}" if sep else ""
        ext_form = False

    if ext_backend(name) is None:
        return None, RefError(
            "unknown_upstream",
            f"/ext/{name}/ is not in EXT_UPSTREAMS (name={name})",
            reason="unknown_name",
        )
    return (
        UrlRoute(
            name=name,
            rel=rel,
            query=query,
            public_base=EXT_PUBLIC_BASES.get(name),
            origin=origin,
            ext_form=ext_form,
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
    """URL(絶対 / 同一サーバの相対 `/ext/…`)が **どの upstream の何か** を返す。
    `resolve_url` と同じ 1 箇所のルーティング(`_route_url`)の結果で、**読まない**
    (ネットワークにもファイルにも触れない純粋な関数。ログも出さない)。

    用途: `resolve_url` で取得できた URL について、呼び手(配備層の fetch_url)が
    ページ内リンクのルートを組む — 相対入力(`route.relative`)なら `/ext/{name}`、
    絶対で原本ルートがあれば `route.public_base`、無ければ `{route.origin}/ext/{name}`。
    URL の分解(`/ext/{name}/…`・EXT_PUBLIC_BASES・EXT_UPSTREAMS)を呼び手に複製させない。

    成功は `(UrlRoute, None)`、ルーティングできない URL は `(None, RefError)` —
    `resolve_url` が同じ入力に返すのと同じ code / reason(`unsupported_form` /
    `traversal` / `unknown_upstream` + reason)。name の直下(`/ext/{name}`・`/ext/{name}/`・
    原本の `<base>`・`<base>/`)は成功で、`rel` が `""`。**配信範囲(EXT_FILE_SCOPES)や
    実在は見ない** — 「どこに属するか」だけで、読めるかどうかは `resolve_url` の責務
    (file backend の直下は `resolve_url` では `not_found`)。`/api/v1/files/{id}/content` はここでは
    `unsupported_form`(`file_id_of_url` で先に拾う — `resolve_url` と同じ)。
    """
    err = _check_url(url)
    if err is not None:
        return None, err
    return _route_url(url)


async def _load_url(url: str, limit: int) -> _Result:
    """URL(traversal / 制御文字は入口で拒否済み)を backend で解決する。"""
    route, err = _route_url(url)
    if route is None:
        # warning はここで出す(`_route_url` は probe からも呼ばれる — 二重に残さない)
        log.warning(f"image_refs: {err.detail} ({url[:80]!r})")
        return None, err
    return await _load_ext(route.name, route.rel, route.query, limit)


def _mounted_file(name: str, target: Path) -> str | None:
    """target が name の file backend が配信する範囲(`ext_file_served`)の通常ファイルで、
    かつ RO_MOUNT_ROOT 配下にあれば、`RO_MOUNT_ROOT` 起点のパスを返す。blocking — thread で呼ぶ。"""
    mount = Path(RO_MOUNT_ROOT)
    try:
        resolved = target.resolve()
        mount_resolved = mount.resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if not ext_file_served(name, resolved):
        return None
    if not resolved.is_relative_to(mount_resolved) or not resolved.is_file():
        return None
    return str(mount / resolved.relative_to(mount_resolved))


async def mounted_path_for_url(url: str) -> str | None:
    """URL(絶対 / 同一サーバの `/ext/…`)の解決先が **file backend の実ファイルで、
    `RO_MOUNT_ROOT`(`/mnt`)配下** なら、その fs パス(`/mnt/...`)を返す。それ以外(http backend・`/mnt` の外の root・
    不在・許可先の外・URL でない)は None — 理由は返さない(要る呼び手は `resolve_url` を
    続けて呼べば同じ規則で RefError が出る)。

    用途は「URL → sandbox の fs パス」の橋渡し(builtin sandbox の `send_to_workspace`):
    `/mnt` 配下は sandbox と同じ置き場(#138 の規約 — 配備は同じ volume を nucllei と
    sandbox の両方の `/mnt/<name>` に bind する)なので、URL が指す実体は sandbox からも
    同じパスで見えていて、コピーする必要が無い。**sandbox 側に同じ bind が実際にあるかは
    nucllei からは確かめられない** — ここが見るのは nucllei 側の事実(解決先のローカル
    パスが `/mnt` 配下)だけで、同じ置き場であることは配備の規約に依存する。
    """
    if not isinstance(url, str) or not _is_url(url):
        return None
    if has_path_traversal(url):
        return None
    route, _ = _route_url(url)
    if route is None:
        return None
    backend = ext_backend(route.name)
    if backend[0] != "file" or has_path_traversal(route.rel):
        return None
    candidate = _ext_file_target(backend[1], route.rel)
    if candidate is None:
        return None
    return await asyncio.to_thread(_mounted_file, route.name, candidate)


def _ext_url_for_file(target: Path) -> str | None:
    """target が `RO_MOUNT_ROOT` 配下の通常ファイルで、EXT_UPSTREAMS のいずれかの
    file backend が配信する範囲(`ext_file_served`: root の中 + EXT_FILE_SCOPES)に
    あれば、その配信 URL(相対 `/ext/{name}/{rest}`)を返す。
    blocking — thread で呼ぶ。

    `_mounted_file` の逆。閉じ込めは同じ判定(resolve 済みの実体を `ext_file_served` に)—
    `..`・symlink で範囲の外へ出る物、root の中でも配信範囲の外の物、不在、ディレクトリは None。複数の backend に
    当たる(root が入れ子)ときは root が深い方、同じ深さなら name の辞書順で先の方。
    rest は percent-encode する(日本語・空白・`#` `?` を含む名前が URL として壊れない
    ように)。組んだ URL は `_route_url` → `_ext_file_target` に通して同じファイルに
    戻ることを確かめてから返す — URL の形の規則をここに二重に持たず、/ext ゲート
    (`routers/ext.py`)と `resolve_url` が受ける形と往復で一致させるため。ゲートが 400 で
    断る名前(制御文字・バックスラッシュ)は None(配信されていない扱い)。
    """
    mount = Path(RO_MOUNT_ROOT)
    try:
        resolved = target.resolve()
        mount_resolved = mount.resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if not resolved.is_relative_to(mount_resolved) or not resolved.is_file():
        return None
    best: tuple[int, str, Path] | None = None
    for name in sorted(EXT_UPSTREAMS):
        if not ext_file_served(name, resolved):
            continue
        root = ext_backend(name)[1]
        if best is None or len(root.parts) > best[0]:
            best = (len(root.parts), name, root)
    if best is None:
        return None
    _, name, root = best
    rest = quote(resolved.relative_to(root).as_posix(), safe="/")
    url = f"{_EXT_URL_PREFIX}{name}/{rest}"
    if has_path_traversal(url):
        return None
    route, _ = _route_url(url)
    if route is None or (route.name, route.rel, route.query) != (name, rest, ""):
        return None
    back = _ext_file_target(root, rest)
    if back is None or back != resolved:
        return None
    return url


async def url_for_mounted_path(path: str) -> str | None:
    """sandbox の fs パス(`RO_MOUNT_ROOT` = `/mnt` 配下)の実体が **EXT_UPSTREAMS の
    file backend で配信されている** なら、その URL(同一サーバの相対 `/ext/{name}/{rest}`)を
    返す。それ以外(配信されていない `/mnt/…`・不在・root の外・`/workspace/…`・パスで
    ない)は None — 理由は返さない(要る呼び手は `resolve_path` を続けて呼べば同じ境界で
    RefError が出る)。

    `mounted_path_for_url` の逆向きで、用途は「sandbox のパス → 利用者に見せられる URL」の
    橋渡し(builtin sandbox の `fetch_from_workspace`): 同じ実体が既に `/ext/…` で
    配信されているなら、コピーして file にする必要が無い。値の形(`/mnt/cage/` という
    文字列)ではなく解決結果(実ファイルが backend の root 配下)で決まる。前提は
    `mounted_path_for_url` と同じ配備の規約(#138: `/mnt` 配下は nucllei と sandbox が
    同じ volume を bind する)— sandbox 側のそのパスが同じ実体かは nucllei からは
    確かめられない。公開範囲は広げない: 返す URL は /ext ゲートが既に配信している物だけ。
    """
    if not isinstance(path, str) or has_path_traversal(path):
        return None
    if not path.startswith(f"{Path(RO_MOUNT_ROOT)}/"):
        return None
    return await asyncio.to_thread(_ext_url_for_file, Path(path))


def _withheld(target: Path) -> bool:
    """blocking — thread で呼ぶ。resolve できないパスは False(読みの失敗に任せる)。"""
    try:
        resolved = target.resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    return ext_file_withheld(resolved)


async def mounted_path_withheld(path: str) -> bool:
    """sandbox の fs パス(`RO_MOUNT_ROOT` = `/mnt` 配下)の実体が、**EXT_UPSTREAMS の
    file backend の root 配下にあって、どの backend の配信範囲にも無い** か
    (`ext_upstreams.ext_file_withheld` — /ext ゲートと同じ述語の裏返し)。

    True なら、そのファイルは利用者に渡さない(`fetch_from_workspace` は URL も返さず
    添付にもしない)。配信範囲(EXT_FILE_SCOPES)は「利用者に渡してよい範囲」という 1 つの
    意味を持つ — URL では 404 なのに添付なら渡る、を作らない。どの backend の root 配下でも
    ない `/mnt/…` と、scope 未設定の backend(root 全体が配信範囲)は False。実在は
    見ない(範囲の判定だけ。無いファイルでも範囲外なら True)。モデルの目(`resolve_path`
    での読み)には影響しない。
    """
    if not isinstance(path, str) or has_path_traversal(path):
        return False
    if not path.startswith(f"{Path(RO_MOUNT_ROOT)}/"):
        return False
    return await asyncio.to_thread(_withheld, Path(path))


def _reject(ref) -> RefError | None:
    """3 つの入口に共通の前段。文字列でない / 制御文字・`..`・バックスラッシュを含む参照は
    どの形でも読まない(URL は path / query ごと文字列全体に掛ける — urlsplit は CR/LF/TAB を
    黙って落とすので、分解前に見る。`/mnt/` 直読みは Path.resolve() が NUL で ValueError を
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
    `/ext/{name}/…`。値の中身を見て別の意味(sandbox のパス・file_id)に読み替えない —
    URL として解決できないものは `unsupported_form`。`/api/v1/files/{id}/content` は
    呼び手が `file_id_of_url` で先に拾う(module docstring「URL の解決」)。user を
    取らない: 公開範囲は admin 管理の EXT_UPSTREAMS / EXT_PUBLIC_BASES で per-user ではない。

    戻り値・サイズ上限・失敗 code は `resolve_ref` と同じ。
    """
    limit = EXT_INLINE_MAX_BYTES if max_bytes is None else max_bytes
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

    受けるのは `/workspace/…`(呼び出しユーザーの workspace)と `/mnt/…`(RO マウント)。
    URL が来ても URL としては解決しない — パスとして `unsupported_form`。相対パスも
    `unsupported_form`(`path` の形は絶対パス 1 つ)。

    戻り値・サイズ上限・失敗 code は `resolve_ref` と同じ。
    """
    limit = EXT_INLINE_MAX_BYTES if max_bytes is None else max_bytes
    err = _reject(path)
    if err is not None:
        return None, err
    if not _is_sandbox_path(path):
        log.warning(f"image_refs: not a sandbox path ({path[:80]!r})")
        return _fail(
            "unsupported_form",
            f"not a sandbox path (/workspace/ or {Path(RO_MOUNT_ROOT)}/)",
        )
    if path.startswith(WORKSPACE_PREFIX):
        return await _load_workspace(path[len(WORKSPACE_PREFIX) :], user_id, limit)
    return await asyncio.to_thread(
        _read_confined,
        _is_under(Path(RO_MOUNT_ROOT).resolve()),
        Path(path),
        f"mount:{path}",
        limit,
    )


async def resolve_ref(
    ref: str, user_id: str, *, max_bytes: int | None = None
) -> _Result:
    """**保存済みの参照文字列**(`#page` を含まない)をバイト列にする。

    引数名を持たない文字列(`context_images` のエントリ・image_url の url)の入口で、
    `stored_ref_kind` の振り分けに従うだけの薄い dispatcher — URL(`http(s)://…` /
    `/ext/…`)は `resolve_url`、sandbox の fs パス(`/workspace/…` / `/mnt/…`)は
    `resolve_path`、どちらでもなければ `unsupported_form`(module docstring の表。file の
    配信 URL と裸の file id は権限検査が要るので呼び手が先に拾う)。**tool の引数には
    使わない** — `url` は `resolve_url`、`path` は `resolve_path` を直接呼ぶ(名前で
    解釈が決まる。ここを通すと値の形で意味が変わってしまう)。

    成功なら `(loaded, None)`、失敗なら `(None, RefError)`。例外は投げない(fail
    closed の約束 — 境界の外は全部 RefError に閉じる。3 つの入口とも同じ)。

    サイズ上限は `max_bytes`(None なら env の EXT_INLINE_MAX_BYTES)— どの経路
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
        f"sandbox path (/workspace/ or {Path(RO_MOUNT_ROOT)}/)",
    )


async def load_ref(ref: str, user_id: str) -> LoadedRef | None:
    """`resolve_ref` の薄い wrapper — 解決できなければ None(理由を要らない呼び手向け)。
    上限は従来どおり EXT_INLINE_MAX_BYTES(引数は増やさない)。"""
    loaded, _ = await resolve_ref(ref, user_id)
    return loaded


def log_mounts() -> None:
    """起動時 1 行: `RO_MOUNT_ROOT` に何が見えているか(sandbox の `_log_ro_mounts` と
    同じ流儀)。配備側が volume を足すだけで増える口で、env を見ても分からないため。
    EXT_UPSTREAMS の file root が無ければ WARNING — その name は全 404 になる。
    EXT_PUBLIC_BASES の name が EXT_UPSTREAMS に無ければ WARNING — その base 配下の
    URL は到達先が無く解決されない。
    """
    root = Path(RO_MOUNT_ROOT)
    try:
        names = sorted(n for n in os.listdir(root) if not n.startswith("."))
    except OSError:
        names = []
    if names:
        log.info(f"[nucllei] read-only mount: {' '.join(str(root / n) for n in names)}")
    else:
        log.info(f"[nucllei] {root} に参照用データは無い")
    for name in EXT_UPSTREAMS:
        backend = ext_backend(name)
        if backend and backend[0] == "file" and not backend[1].is_dir():
            log.warning(
                f"[nucllei] WARNING: EXT_UPSTREAMS {name} の root {backend[1]} が無い "
                f"(/ext/{name}/** は全て 404 になる)"
            )
    for name, scopes in EXT_FILE_SCOPES.items():
        backend = ext_backend(name)
        if backend is None or backend[0] != "file":
            log.warning(
                f"[nucllei] WARNING: EXT_FILE_SCOPES {name} に対応する file backend が "
                "EXT_UPSTREAMS に無い(この設定は効かない)"
            )
        elif not scopes:
            log.warning(
                f"[nucllei] WARNING: EXT_FILE_SCOPES {name} に有効なエントリが無い "
                f"(/ext/{name}/** は全て 404 になる)"
            )
        else:
            log.info(
                f"[nucllei] /ext/{name}/ の配信範囲: "
                + " ".join(str(backend[1] / scope) for scope in scopes)
            )
    for name, base in EXT_PUBLIC_BASES.items():
        if name not in EXT_UPSTREAMS:
            log.warning(
                f"[nucllei] WARNING: EXT_PUBLIC_BASES {name}={base} に対応する "
                f"EXT_UPSTREAMS が無い({base}/** の参照は解決されない)"
            )
