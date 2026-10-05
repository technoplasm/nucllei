"""組み込みツール: Web のページ (スキーム付きの http(s) URL ならどこでも) と同一サーバの /ext/… を
URL で 1 ページずつ取得し、平文 + リンク一覧にして返す。

配備層 (prism-hu/llens の `tools/fetch_url.py`) から本体へ移設した (2026-10-03、#141)。
判断の根拠は feed_image / sandbox と同じ**依存の向き**: 受ける形・許可先・traversal・
サイズ上限・redirect の規律は全部 `utils/image_refs` の resolver にあり、tool は
`resolve_url` / `route_of_url` / `file_id_of_url` を掴んでいるだけ。配備側に置く限り版の
交渉 (try/except の fallback、`_link_roots` の最小実装の複製) が要った — builtin なら
nucllei と同じ tag で出る (`_memos/builtin-tools.md` 1c)。LLENS 固有の知識はこのファイルに
無い (配信元は env。「doc に収録済みの資料は取りに行かない」の案内は docstring の文面だけで、
doc tool の存在には依存しない)。

**常に登録** (2026-10-03 ken 決定): url= はスキーム付きの http(s) URL ならどこでも取りに行く
(院内でもインターネットでも)。届くかどうかは配備のネットワークが決める — 「閉域だから外には
届かない」は prompt / skill に書く文であって、この tool は行き先で絞らない。取りに行かないのは
**このサーバ自身 (と同居するサービス)** だけ (resolver の `server_self`)。同日の朝まで
EXT_UPSTREAMS / EXT_PUBLIC_BASES / EXT_INTRANET_CIDRS のいずれかでゲートしていたが、
行き先で機能を絞らない決定と一緒に外した (EXT_INTRANET_CIDRS も同日に撤去。同日のうちに
EXT_UPSTREAMS / EXT_PUBLIC_BASES / EXT_FILE_SCOPES も設定ごと無くした — 原本ホストの対応 (docstring の
原本ホストのスロット・ERROR 文の「登録された原本ホストは …」・ヘッダの原本行) はそのとき消えた。
2026-10-05 から `/ext/<name>` の行き先は env `EXT_MOUNTS` の対応表 (静的 / proxy))。

何のための tool か:
  収録済みの文書コーパス (配備層の doc tool) に無い Web (院内の病院情報管理システム等) を、
  モデルが**その場で 1 ページずつ辿る**ための取得口。

受ける URL の形 (2026-10-03 ken 決定: url = 絶対 URL か同一サーバの相対 URL):
  解決は共有 resolver (`image_refs.resolve_url`、feed_image / 送信直前 inline / send_to_workspace と
  共通) に一任する。名前空間は 2 つ (image_refs の module docstring「URL の解決」):
    (a) path が `/ext/{rest}` の URL — 同一サーバの相対 `/ext/…` と、絶対 `https://<host>/ext/…`
        (既存チャット。host は問わない)。`EXT_MOUNTS` の対応表で読む — 静的はローカル読み、
        proxy は上流へ GET (sandbox からは同じ物が同じ文字列 `/ext/{rest}` で見える規約)
    (b) それ以外の絶対 URL — 一般の URL。そのホストに直接取りに行く (自分自身を除く)
  **解釈は引数名だけで決める** (feed_image と同じ): url に来た値は URL として `resolve_url` で
  解決し、`/workspace/…` を sandbox のパスとして読み替えない。URL として解決できない
  値は固定文 (`ref_errors.url_error`) の ERROR。値を調べて「それは run_shell で」と出し分けない —
  行き先の案内は prompt / skill の参照規則の側に書く。
  `/api/v1/files/{id}/content` (相対・絶対とも) は URL のルーティングとして file に当たる
  (`file_id_of_url`) — 所有者チェックの上で Storage から読み、同じ変換にかける
  (send_to_workspace(url=) と同じ扱い)。

SSRF: **tool 側に host allowlist も valve も持たない。** `/ext/` が配信するのは配備が env
  `EXT_MOUNTS` に書いた物 (静的の dir / proxy の上流 — SSRF の真実源はこの表 1 本)。一般の URL は行き先で絞らず、resolver が「このサーバ自身 (と同居する
  サービス)」だけを実行時に求めた集合で断る (`ext_upstreams.self_addresses`)。traversal 判定・
  サイズ上限・redirect の規律、受ける形の判定は全部 resolver 側にある。この tool は取得できた
  後にページ自身の URL (`route_of_url(u).page_url`) でリンクを解決するだけ。

戻り値の URL は 1 本:
  リンク一覧も `url:` 行も、次の fetch_url / feed_image / send_to_workspace(url=) に
  そのまま渡せ、ユーザーにもリンクで示せる URL にする — ページ内リンクは `/ext/…` のページでも
  一般の URL でも**そのページ自身の URL 基準の urljoin** (通常の Web と同じ。`/ext/…` の相対
  入力なら相対 `/ext/…` のまま、絶対なら `{origin}/ext/…`)。基準 URL は resolver の
  `route_of_url` から引く — tool 側に valve を持たない (2026-09-07 レビュー: valve を管理
  画面で変えると docstring と判定がずれる、という真実源の分裂が起きた)。
  `/ext/…` の相対ページでは、`/ext/` の外へ出る href (ルート相対 `/x.pdf`・別ホストの絶対 URL)
  はこのサーバの URL ではないので落とす。絶対の `/ext/…` ページでは http(s) の結果だけ残す
  (一般の URL と同じ)。

HTML を markitdown に通さない理由 (実測、2026-09-04):
  病院情報管理システムは ASP.NET WebForms でレイアウトが全部 table。markitdown は
  レイアウトテーブルとスペーサ画像まで Markdown テーブルに起こすので、
  一覧ページで 生 596K 字 → markitdown 191K 字 / 自前変換 70K 字。
  PDF は markitdown (添付の抽出と同じ作法) のまま。

パーサに lxml.html を使う理由:
  nucllei の依存に既にあり (markitdown 経由)、bs4 を選んでも書けるが依存を増やす理由が無い。
  変換は汎用 (block タグ → 行、script / style / noscript / img / input を落とす) で、
  配備固有のフックは持たない。

encoding を自動判定しない理由:
  Content-Type の `charset` があればそれ、無ければ utf-8 (errors="replace") で decode する。
  自動判定 (requests の apparent_encoding 相当) は、charset=utf-8 を名乗る院内サイトで
  数ページだけ別 encoding を推定して全文化ける (実測)。**判定しない。** lxml に bytes を渡すと
  meta charset を見に行くので、必ず自分で decode した str を渡す。
  ※ resolver の `LoadedRef.content_type` はパラメータを落とした型だけ (`text/html`) なので、
  http 経路では charset は届かず utf-8 になる。file 経路 (Files の meta) は生の値を見る。
"""

import asyncio
import codecs
import io
import logging
import math
import pathlib
import re
import unicodedata
from typing import Optional
from urllib.parse import urljoin

from pydantic import BaseModel, Field

from nucllei.models.files import Files
from nucllei.storage.provider import Storage
from nucllei.utils.image_refs import file_id_of_url, resolve_url, route_of_url
from nucllei.utils.tool_errors import ToolError, ToolFailure, ToolFatal, ToolOutcome
from nucllei.utils.ref_errors import (
    NEXT,
    SHOW_AS_LINK,
    RefErrorHints,
    explain_ref_error,
    file_id_error,
    mb,
    show_as_link,
)
from nucllei.utils.tool_render import Text

log = logging.getLogger(__name__)

# ページの URL が絶対かの判定。_resolve_href が使う。
_ABSOLUTE_PAGE_RE = re.compile(r"^https?://", re.IGNORECASE)

# 平文化のとき改行を入れるタグ (レイアウト table のセル単位で 1 行にする)。
_BLOCK_TAGS = frozenset(
    """address article aside blockquote br caption dd div dl dt fieldset figcaption
    figure footer form h1 h2 h3 h4 h5 h6 header hr li main nav ol option p pre section
    table tbody td tfoot th thead tr ul""".split()
)

# 平文化の前に丸ごと落とすタグ。script/style はノイズ、img は alt ごと不要
# (図は feed_image で見る)、input は ASP.NET の __VIEWSTATE (数十 KB の base64) がここに居る。
_DROP_TAGS = ("script", "style", "noscript", "img", "input")

# Content-Type の charset パラメータ (`text/html; charset=Shift_JIS`)。
_CHARSET_RE = re.compile(r";\s*charset\s*=\s*\"?([A-Za-z0-9_.:-]+)\"?", re.IGNORECASE)


def _norm_space(s: str) -> str:
    """ラベル用の空白正規化 (改行込みで 1 スペースに畳む)。"""
    return re.sub(r"\s+", " ", s or "").strip()


def _fold(s: str) -> str:
    """query 照合用の正規化 (NFKC + case-insensitive)。"""
    return unicodedata.normalize("NFKC", s).casefold()


def _media_type(content_type: str) -> str:
    """Content-Type からパラメータ (charset 等) を落とした型 (`text/html`)。小文字。"""
    return (content_type or "").split(";")[0].strip().lower()


def _charset_of(content_type: str) -> str:
    """Content-Type の charset。無い・Python が知らない名前なら utf-8 (冒頭コメント「encoding」)。"""
    m = _CHARSET_RE.search(content_type or "")
    if not m:
        return "utf-8"
    try:
        return codecs.lookup(m.group(1)).name
    except LookupError:
        return "utf-8"


def _decode(raw: bytes, content_type: str) -> str:
    return raw.decode(_charset_of(content_type), errors="replace")


# モデルに返す文は「事実の文 + `次の手:` 以降」(`utils/ref_errors` の module docstring)。
# 別の toolkit の tool (feed_image / send_to_workspace / run_subagent / read_attachment) は
# 載っていないことがあるので「〜が使えるなら」と条件付きで書く。

# resolver が理由を付けずに失敗したとき (通常起きない) の文面 (`{ref}` は ref_errors が埋める)。
_ERR_UNEXPLAINED = (
    "を取得できない (届かない / 存在しない / redirect を辿れない / 大きすぎる のいずれか)。"
    + NEXT
    + SHOW_AS_LINK
)


def _too_large(u: str, err, ceiling_mb: Optional[float]) -> str:
    """too_large の全文。ceiling_mb は引数 max_mb で引き上げられる天井 (valve) — 次の一手を
    決めるのに使う。

    resolver の size / size_exact の意味:
      size_exact=True  … size は実サイズ (Content-Length / fs の stat / Files レコード)。
      size_exact=False … Content-Length 無しで打ち切った — size は**打ち切り時点の受信量**
                         (limit を超えた最初の chunk まで) で、実サイズの下限でしかない。
    下限を確定扱いにすると「25.0 MB で上限 25.0 MB を超えている … max_mb=26」となり、
    26 では 32.6 MB の実体にまた足りない (実走 2026-09-06 の退行)。size と limit の
    差から推測はしない — 判定は resolver の size_exact だけに拠る。
    """
    size, limit = err.size, err.limit
    mib = 1024 * 1024
    limit_txt = f" {mb(limit)}" if limit is not None else ""
    hand_over = "中身が要らなければ、" + show_as_link(u)
    size_known = size is not None and err.size_exact is True
    if size_known:
        # 実サイズが確定している (size_exact=True)。
        if ceiling_mb is not None and size > ceiling_mb * mib:
            # 天井も超えている — max_mb を上げても読めない
            return (
                f"ERROR: {u} は {mb(size)} で、取得の上限{limit_txt} を超えている。"
                f"この tool の最大 {ceiling_mb:.1f} MB も超えるので中身は読めない。"
                + NEXT
                + show_as_link(u)
            )
        # X を超える整数 MB を具体値で示す (31.1 MB → 32)。
        suggest = str(int(size // mib) + 1)
        more = ""
        what = f"{u} は {mb(size)} で、取得の上限{limit_txt} を超えている。"
    elif limit is not None:
        # 実サイズ不明 (size_exact が False、または size が None): size があっても
        # 打ち切り時点の受信量 (limit のすぐ上) でしかない。上限の 2 倍を提案し、
        # 足りなければさらに上げるよう添える (天井は valve)。
        seen = size if size is not None else limit
        n = max(int(seen // mib) + 1, math.ceil(limit / mib) * 2)
        if ceiling_mb is not None:
            n = min(n, int(ceiling_mb))
        suggest = str(n)
        more = (
            f"足りなければさらに上げる (天井 {ceiling_mb:.1f} MB)。"
            if ceiling_mb is not None
            else "足りなければさらに上げる。"
        )
        what = f"{u} は少なくとも {mb(seen)} あり、取得の上限 {mb(limit)} を超えている (実サイズは不明)。"
    else:
        suggest, more = "<X を超える値>", ""
        what = f"{u} は取得の上限を超えている (実サイズは不明)。"
    return (
        f"ERROR: {what}{NEXT}この会話では読まない。run_subagent が使えるなら URL と指示を渡し、"
        f'子が fetch_url(url="{u}", max_mb={suggest}) で読む (子は offset で続きを取れる)。'
        f"{more}"
        "pandas 等で加工・集計したいだけなら、send_to_workspace が使えるなら"
        " send_to_workspace(url=…) で sandbox に取り込む (読むためのテキスト化には使わない)。"
        f"{hand_over}"
    )


def _explain_ref_error(u: str, err, ceiling_mb: Optional[float] = None) -> ToolError:
    """resolver の RefError をモデル向けの ToolError にする (組み立ては `utils/ref_errors`)。

    この tool 固有の言い方: too_large は run_subagent + max_mb (天井 ceiling_mb)。
    """
    hints = RefErrorHints(
        too_large=lambda ref, e: _too_large(ref, e, ceiling_mb),
        unexplained=f"ERROR: {{ref}} {_ERR_UNEXPLAINED}",
    )
    return explain_ref_error(u, err, arg="url", hints=hints)


def _resolve_href(href: str, page_url: str):
    """href をページ自身の URL (`page_url`) 基準で解決する (通常の Web と同じ urljoin)。
    辿れない href は None。

    page_url が絶対 (一般の URL、絶対の `/ext/…`) なら http(s) の結果だけ残す (取りに行けるかは
    次の fetch_url で resolver が判定する — ここでは絞らない)。相対の `/ext/…` ページなら、
    `/ext/` の中に留まる結果だけ残す (ルート相対 `/x.pdf` や別ホストはこのサーバの URL では
    ない)。page_url が空 (file 経路・ルーティングできない) ならリンクを組まない。
    """
    href = (href or "").strip()
    if not href or href.startswith(("javascript:", "#", "mailto:", "tel:")):
        return None
    if not page_url:
        return None
    resolved = urljoin(page_url, href).split("#", 1)[0]
    if _ABSOLUTE_PAGE_RE.match(page_url):
        return resolved if _ABSOLUTE_PAGE_RE.match(resolved) else None
    return resolved if resolved.startswith("/ext/") else None


def _html_to_lines(raw: bytes, page_url: str, content_type: str = ""):
    """HTML を (本文行, リンク行) に変換する。

    - script / style / noscript / img / input を除去
    - <a href> は本文で `ラベル [n]` にし、末尾のリンク一覧に `[n] ラベル → URL` を出す
      (URL は 1 本。ページ自身の URL 基準 — _resolve_href)
    - 空白正規化 (連続空白を 1 つに、空行は落とす)
    - encoding は Content-Type の charset、無ければ utf-8 (冒頭コメント)。lxml に bytes を
      渡すと meta charset を見に行くので、必ず自分で decode した str を渡す
    """
    from lxml import etree, html as lxml_html

    doc = lxml_html.fromstring(_decode(raw, content_type))
    etree.strip_elements(doc, *_DROP_TAGS, with_tail=False)

    links = []
    for a in doc.iter("a"):
        url = _resolve_href(a.get("href"), page_url)
        if url is None:
            continue  # postback (javascript:) / ページ内 (#) / 別ホスト
        n = len(links) + 1
        label = _norm_space(a.text_content()) or "(ラベルなし)"
        links.append(f"[{n}] {label} → {url}")
        a.tail = f" [{n}]" + (a.tail or "")

    for el in doc.iter():
        if el.tag in _BLOCK_TAGS:
            el.text = "\n" + (el.text or "")
            el.tail = "\n" + (el.tail or "")

    body = [
        line
        for line in (_norm_space(ln) for ln in doc.text_content().split("\n"))
        if line
    ]
    return body, links


def _pdf_to_text(raw: bytes) -> str:
    """PDF のテキスト層を抽出する (添付の抽出と同じ markitdown)。"""
    from markitdown import MarkItDown

    result = MarkItDown().convert_stream(io.BytesIO(raw), file_extension=".pdf")
    return (result.text_content or "").strip()


# text/* 以外で「平文としてそのまま返す」content-type (JSON / XML / YAML / CSV 系)。
_PLAIN_APP_TYPES = frozenset(
    {
        "application/json",
        "application/xml",
        "application/yaml",
        "application/x-yaml",
        "application/csv",
        "application/x-ndjson",
        "application/javascript",
    }
)


class Tools:
    # ※ クラス docstring は toolkit 共通注記としてモデルに見せる。
    #   この tool は 1 関数なので docstring は関数側だけに置く。

    class Valves(BaseModel):
        max_fetch_mb: float = Field(
            default=25.0,
            description="1 ページの取得サイズ上限 (MB) の既定。resolver に max_bytes として渡す (文書を"
            "読む用途なので、画像 inline 用の既定上限は借りない)。既定は控えめ (大きいと主 context が"
            "頓死する)。上限を超えるものは中身を読まず、run_subagent + 引数 max_mb で読むか URL を"
            "リンクで示すだけになる",
        )
        max_fetch_mb_ceiling: float = Field(
            default=200.0,
            description="引数 max_mb で引き上げられる上限の天井 (MB)。これを超える資料はどう呼んでも"
            "読まない",
        )
        max_output_chars: int = Field(
            default=40000,
            description="1 回の呼び出しでモデルに返す本文の上限文字数 (limit 引数の上限も兼ねる)。"
            "続きは offset で取る",
        )
        pdf_text_char_threshold: int = Field(
            default=100,
            description="PDF 抽出テキストがこの文字数未満ならテキスト層無し (スキャン PDF) と "
            "みなして feed_image へ誘導する",
        )

    def __init__(self) -> None:
        self.valves = self.Valves()

    async def fetch_url(
        self,
        url: str,
        query: Optional[str] = None,
        offset: int = 0,
        limit: int = 8000,
        max_mb: Optional[int] = None,
        __user__: Optional[dict] = None,
        __progress__=None,
    ) -> Text:
        """Web のページ・文書を 1 枚取得して平文にする (HTML はリンク一覧付き、PDF はテキスト層)。

        url は受け取った形のまま渡す: 2 枚目からは組み立てず、戻り値のリンク一覧などに出た URL の
        literal を 1 文字も変えずにコピーする (相対ならホストを補わない)。
        大きいページは query で該当行に絞る (続きは offset)。

        戻り値はヘッダ (url / 種別 / 分量) + 本文 + リンク一覧。
        失敗時は "ERROR: ..." で始まる文 — 何が起きたか (事実) と、「次の手:」以降に自分が次に
        すること (ユーザーには伝えない)。

        :param url: スキーム付きの絶対 URL (`http(s)://…`) か、同一サーバの `/ext/…`・`/api/v1/files/{id}/content` (戻り値などに出た URL の literal をそのまま)。
        :param query: (任意) 指定すると、本文行・リンク行のうちこの語を含む行だけを返す。
        :param offset: 何文字目から返すか (0 起点)。続きは戻り値の next_offset を渡す。
        :param limit: 返す文字数 (既定 8000)。valve の max_output_chars で頭打ちになる。
        :param max_mb: (任意) 取得サイズの上限 (MB)。既定を超える資料を読むときだけ (天井あり)。
        """
        # 使い方の細目 (doc との使い分け・query の使い方・run_subagent / max_mb・失敗の種類・
        # リンク一覧の形等) は README_API §5.7.1。ERROR 文の次の一手は実行時の戻り値が持つ。
        if not __user__ or not __user__.get("id"):
            raise ToolFailure("ユーザーを特定できない")

        u = (url or "").strip()

        # サイズ上限: 既定は valve max_fetch_mb (控えめ。主 context を守る)。引数 max_mb が
        # あればそれを使うが、valve max_fetch_mb_ceiling で天井を押さえる。
        ceiling_mb = float(self.valves.max_fetch_mb_ceiling)
        if max_mb is None:
            cap_mb = float(self.valves.max_fetch_mb)
        else:
            cap_mb = min(float(max_mb), ceiling_mb)
        max_bytes = int(cap_mb * 1024 * 1024)

        # このサーバのファイル配信口 (相対・絶対とも) は URL のルーティングとして file に当たる
        # (冒頭コメント)。HTTP では取りに行かず、所有者チェックの上で Storage から読む。
        # file_id_of_url は fragment 付きでも id を返す
        fid = file_id_of_url(u)
        if fid:
            raw, content_type = await self._read_file(
                fid, __user__["id"], max_bytes, cap_mb
            )
            # 添付・生成物の HTML の相対 href はどの名前空間にも属さない (`/page2.html` に
            # なってしまう) ので、file 経路ではリンク一覧を出さない (page_url 空)
            page_url = ""
        else:
            # 引数名どおり URL として扱う (冒頭コメント)。受ける形・行き先の判定は resolver に
            # 一任する (tool 側で同じ振り分けを重ねない)。ここで URL を分解するのは、取得できた
            # 後にページ内リンクの基準 (ページ自身の URL) を得るためだけ。
            if __progress__:
                __progress__(f"取得中: {u}")
            # サイズ上限は max_bytes で渡す — 文書を読む用途なので画像 inline 用の既定上限を
            # 借りない。too_large の ERROR 文は RefError.limit (実際に使われた上限) から MB を出す。
            loaded, err = await resolve_url(u, max_bytes=max_bytes)
            if err is not None or loaded is None:
                raise _explain_ref_error(u, err, ceiling_mb)
            if (
                len(loaded.data) > max_bytes
            ):  # resolver が守るが、念のため (fail closed)
                raise ToolOutcome(
                    f"{u} は {mb(len(loaded.data))} で大きすぎる "
                    f"(この呼び出しの上限 {cap_mb:.1f} MB)。中身は読めない",
                    show_as_link(u),
                )
            content_type = loaded.content_type or ""
            raw = loaded.data
            # このページ自身の URL (ユーザーにリンクで示す 1 本 = ページ内リンクの基準)
            page_url = self._page_url(u)
        # 先頭行は `url: …` (見出し `# …` にしない — モデルが本文の見出しに写しやすい)
        head = f"url: {u}\n"

        if __progress__:
            __progress__("変換中")
        try:
            kind, body, links = self._to_lines(raw, content_type, page_url)
        except Exception as e:
            log.error(f"[fetch_url] convert failed ({u}): {e!r}")
            # 変換器 (PDF / HTML の解析ライブラリ) が想定外に落ちた → fatal
            raise ToolFatal(
                f"{u} の中身を平文に変換できなかった", show_as_link(page_url or u)
            )

        media_type = _media_type(content_type)
        if kind == "other" and media_type.startswith("image/"):
            # 画像は平文にできないが**行き止まりではない** — feed_image が同じ URL を
            # url でそのまま受けて自分の目に入れられる (2026-09-04 に拡張子判定をやめたので、
            # 拡張子を持たない配信口の画像もそのまま渡せる)。ここで原本 URL だけを
            # 返すと、モデルは見られるものを見ずにユーザーへ丸投げする。
            return (
                head + f"種別: 画像 (content-type={media_type})。平文にはできない。\n"
                f'{NEXT}feed_image が使えるなら feed_image(url="{u}") で自分の目で見る。'
            )
        if kind == "other":
            raise ToolOutcome(
                f"{u} は平文にできない形式 (content-type={media_type or '不明'})",
                f'加工が要るなら、send_to_workspace が使えるなら send_to_workspace(url="{u}")'
                " で sandbox に取り込む。" + show_as_link(page_url or u),
            )
        if kind == "pdf-noText":
            # feed_image は拡張子ではなく取得した content-type で PDF を判定する。
            # log_write.aspx のような拡張子を持たない配信口でもそのまま渡してよい。
            hint = f'feed_image が使えるなら feed_image(url="{u}") でページを見る (pages="3" 等で範囲指定)。'
            return (
                head + f"種別: PDF / テキスト層が無い (抽出 {len(body)} 字 < "
                f"{self.valves.pdf_text_char_threshold} 字)。スキャン画像の PDF。\n"
                f"{NEXT}{hint}"
                f"見られない・読み取れないときは、ユーザーに原本を [ラベル]({u}) のリンクで示す。"
            )

        return self._render(head, kind, body, links, query, offset, limit, url=u)

    # --- 以下 helper (モデルからは見えない) ---------------------------------

    @staticmethod
    def _page_url(u: str) -> str:
        """取得できた URL について、ページ自身の URL (ページ内リンクの基準。fragment 抜き)。

        **resolve_url が成功した後にだけ呼ぶ** (受理・許可先の判定はしない)。`route_of_url` で
        引く — `/ext/{rest}` は相対入力なら相対 `/ext/{rest}`、絶対なら `{origin}/ext/{rest}`、
        一般の URL はその URL そのもの (`route.page_url`)。ルーティングできない (resolver が
        通したのに、という構成のずれ) ときは "" (リンクを組まない)。
        """
        route, _err = route_of_url(u)
        if route is None:
            return ""
        return route.page_url or ""

    async def _read_file(self, fid: str, user_id: str, max_bytes: int, cap_mb: float):
        """url の `/api/v1/files/{id}/content` を file として読む → (raw, content_type)。
        読めないときは ToolError を raise する。
        所有者チェックは feed_image の file_id 経路と同じ (#40 と同クラスの IDOR 封鎖)。
        content_type は Files の meta の生の値 (charset パラメータ付きのことがある)。"""
        rec = await asyncio.to_thread(Files.get_file_by_id, fid)  # v0.6.5 は sync
        if not rec or getattr(rec, "user_id", None) != user_id:
            # レコード無しとアクセス権無しは同じ固定文 (存在の有無を漏らさない)
            raise file_id_error(fid)
        try:
            local = await asyncio.to_thread(Storage.get_file, rec.path)
            raw = await asyncio.to_thread(pathlib.Path(local).read_bytes)
        except Exception as e:  # noqa: BLE001
            log.error(f"[fetch_url] file {fid} read failed: {e!r}")
            # レコードはあるのに実体が読めない = ストレージが壊れている → fatal
            raise ToolFatal(f"file_id {fid} の実体を読めない")
        if len(raw) > max_bytes:
            raise ToolOutcome(
                f"このファイルは {mb(len(raw))} で、取得の上限 {cap_mb:.1f} MB を超えている",
                "read_attachment が使えるなら read_attachment(file_id=…) で抽出テキストを読む。",
            )
        meta = getattr(rec, "meta", None) or {}
        return raw, meta.get("content_type") or ""

    def _to_lines(self, raw: bytes, content_type: str, page_url: str):
        """取得したバイト列を (種別, 本文行 or 抽出文字列, リンク行) に分解する。

        種別は content-type (パラメータ抜きの型) で決める。**拡張子では決めない** —
        `log_write.aspx?...` という配信口が PDF/Excel をそのまま返すため、パスの拡張子は
        当てにならない (実測 2026-09-04)。content_type は生の値 (charset 付きなら decode に使う)。
        """
        media_type = _media_type(content_type)
        is_pdf = media_type == "application/pdf" or raw[:5] == b"%PDF-"
        if is_pdf:
            text = _pdf_to_text(raw)
            if len(text) < self.valves.pdf_text_char_threshold:
                return "pdf-noText", text, []
            return (
                "pdf",
                [ln for ln in (_norm_space(x) for x in text.split("\n")) if ln],
                [],
            )

        is_html = media_type in ("text/html", "application/xhtml+xml") or (
            not media_type and b"<html" in raw[:4096].lower()
        )
        if is_html:
            body, links = _html_to_lines(raw, page_url, content_type)
            return "html", body, links

        # JSON / XML / 素のテキスト系は本文をそのまま行にする (院内 API の応答・設定ファイル等。
        # 一般の URL を直接取れるようになり、JSON を返す口に当たることが増えた)
        if (
            media_type.startswith("text/")
            or media_type in _PLAIN_APP_TYPES
            or media_type.endswith("+json")
            or media_type.endswith("+xml")
        ):
            text = _decode(raw, content_type)
            return (
                "text",
                [ln for ln in (_norm_space(x) for x in text.split("\n")) if ln],
                [],
            )

        return "other", [], []

    def _render(self, head, kind, body, links, query, offset, limit, url=None):
        """行の集合をモデルに返す 1 本の文字列へ (query 絞り込み → offset/limit で刻む)。"""
        if kind == "pdf" and url:
            # 読んだのはテキスト層だけ。ユーザーが開いて見るものと同じものを見る経路は feed_image で、
            # 重い処理ではない (ken 2026-09-07: PDF はできるだけ見る)。
            head += (
                "テキスト層を読んだだけで、レイアウト・図・表・印影は見ていない。"
                f'{NEXT}feed_image が使えるなら feed_image(url="{url}", pages="1-3" 等) で'
                "ユーザーが見るものと同じものを見る (重くない)。\n"
            )
        link_header = f"--- リンク ({len(links)} 件) ---" if links else None
        all_lines = list(body) + ([link_header] + links if links else [])
        total_lines = len(body) + len(links)

        note = ""
        if query:
            q = _fold(query)
            kept = [ln for ln in body + links if q in _fold(ln)]
            note = f'query="{query}" に一致した行: {len(kept)} / {total_lines}\n'
            if not kept:
                return (
                    head
                    + f"種別: {kind} / 本文 {len(body)} 行 + リンク {len(links)} 件\n"
                    + f'query="{query}" に一致する行が無い。'
                    + NEXT
                    + "語を短く・具体的にして引き直すか、query 無しで先頭から読む。"
                )
            all_lines = kept

        text = "\n".join(all_lines)
        cap = max(1, int(self.valves.max_output_chars))
        limit = max(1, min(int(limit or cap), cap))
        offset = max(0, int(offset or 0))
        if offset >= len(text):
            raise ToolOutcome(
                f"offset={offset} は末尾 ({len(text)} 字) を超えている",
                "offset を戻すか、query で絞る。",
            )
        shown = text[offset : offset + limit]
        end = offset + len(shown)
        more = end < len(text)

        head += f"種別: {kind} / 本文 {len(body)} 行 + リンク {len(links)} 件 / 平文 {len(text)} 字\n"
        head += note
        head += f"表示: {offset}-{end} 字"
        if more:
            head += (
                f" (続きあり)\n続きは fetch_url(url=..., offset={end}) で取る。"
                f"next_offset={end}"
            )
        else:
            head += " (末尾まで)"
        return f"{head}\n\n{shown}"
