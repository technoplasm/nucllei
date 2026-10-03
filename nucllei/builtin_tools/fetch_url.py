"""組み込みツール: 院内 Web (登録した原本ホスト・院内の範囲) と同一サーバの /ext/… を URL で
1 ページずつ取得し、平文 + リンク一覧にして返す。

配備層 (prism-hu/llens の `tools/fetch_url.py`) から本体へ移設した (2026-10-03、#141)。
判断の根拠は feed_image / sandbox と同じ**依存の向き**: 受ける形・許可先・traversal・
サイズ上限・redirect の規律は全部 `utils/image_refs` の resolver にあり、tool は
`resolve_url` / `route_of_url` / `file_id_of_url` と `nucllei.env` の EXT_PUBLIC_BASES を
掴んでいる。配備側に置く限り版の交渉 (try/except の fallback、`_link_roots` の最小実装の
複製) が要った — builtin なら nucllei と同じ tag で出る (`_memos/builtin-tools.md` 1c)。
LLENS 固有の知識はこのファイルに無い (配信元・原本ホストは env。「doc に収録済みの資料は
取りに行かない」の案内は docstring の文面だけで、doc tool の存在には依存しない)。

**他の builtin と違い、取りに行ける先が env で決まる**: EXT_UPSTREAMS / EXT_PUBLIC_BASES /
EXT_INTRANET_CIDRS が全部空の配備では、どの URL を渡しても resolver が `unknown_upstream`
を返す = 一覧に生えているのに呼ぶと必ずエラー。sandbox と同じく、いずれかが設定された
配備でだけ登録する (ゲートは `__init__.py` 側)。

何のための tool か:
  収録済みの文書コーパス (配備層の doc tool) に無い院内 Web (病院情報管理システム等) を、
  モデルが**その場で 1 ページずつ辿る**ための取得口。

受ける URL の形 (2026-10-03 ken 決定: url = 絶対 URL か同一サーバの相対 URL):
  解決は共有 resolver (`image_refs.resolve_url`、feed_image / 送信直前 inline / send_to_workspace と
  共通) に一任する。名前空間は 3 つ (image_refs の module docstring「URL の解決」):
    (a) EXT_PUBLIC_BASES (`name=原本 URL のルート`) の `<base>/` 配下 — 職員が開く原本 URL
    (b) path が `/ext/{name}/…` の URL — 同一サーバの相対 `/ext/…` と、絶対 `https://<host>/ext/…`
        (既存チャット。host は問わない)。到達先は EXT_UPSTREAMS の backend
    (c) どちらにも当たらない絶対 URL で、ホストが院内の範囲 (EXT_INTRANET_CIDRS) にあるもの
  **解釈は引数名だけで決める** (feed_image と同じ): url に来た値は URL として `resolve_url` で
  解決し、`/mnt/…` `/workspace/…` を sandbox のパスとして読み替えない。URL として解決できない
  値は固定文 (`ref_errors.url_error`) の ERROR。値を調べて「それは run_shell で」と出し分けない —
  行き先の案内は prompt / skill の参照規則の側に書く。
  `/api/v1/files/{id}/content` (相対・絶対とも) は URL のルーティングとして file に当たる
  (`file_id_of_url`) — 所有者チェックの上で Storage から読み、同じ変換にかける
  (send_to_workspace(url=) と同じ扱い)。

SSRF: **tool 側に host allowlist も valve も持たない。** 許可先の真実源は env の
  EXT_UPSTREAMS (到達先) と EXT_PUBLIC_BASES (どの原本 URL がどの name か) と
  EXT_INTRANET_CIDRS (院内の範囲)。traversal 判定・サイズ上限・redirect の規律、受ける形と
  許可先の判定は全部 resolver 側にある。この tool は取得できた後に「どの名前空間の形か」
  (`route_of_url`) でリンクのルートを決めるだけ。上流を増やすのは配備の env を足す作業で
  あって、この file ではない。

戻り値の URL は 1 本 (原本 URL):
  リンク一覧も `# url` 行も、次の fetch_url / feed_image / send_to_workspace(url=) に
  そのまま渡せ、職員にもそのまま案内できる URL にする — 原本ホストのある上流は原本の
  絶対 URL、無い上流 (文書の図・原本など) は入力と同じ形の `/ext/{name}/…` (相対入力なら
  相対)、院内の一般 URL はページ自身の URL 基準 (通常の Web と同じ)。原本 URL のルートは
  `nucllei.env.EXT_PUBLIC_BASES` から name で引く — 受理判定 (resolver の match_public_base)
  と ERROR 文と docstring の許可先が**同じ 1 つの値**を見るようにするため、tool 側に valve を
  持たない (2026-09-07 レビュー: valve を管理画面で変えると「許可先は X」と言いながら X を
  拒否する、という真実源の分裂が起きた)。
  env に無い name のページ (`/ext/{name}/…` 形) は原本 URL を組めないので、リンクは
  入力 URL と同じ形の `/ext/{name}/…` (次の tool 呼び出しに渡せ、本文にも貼れる) にして、
  その旨をヘッダに 1 行出す。
  docstring に許可先 (原本ホスト) を名乗らせるのも同じ env から — tool の spec (モデルに
  見える説明) は起動時 sync で nucllei プロセス内の docstring から生成され DB に保存される
  ので、module 読み込み時に埋め込めば配備先ごとのホストが載る (module 末尾の置換)。

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
from urllib.parse import urljoin, urlsplit

from pydantic import BaseModel, Field

from nucllei.env import EXT_PUBLIC_BASES
from nucllei.models.files import Files
from nucllei.storage.provider import Storage
from nucllei.utils.ext_upstreams import ext_backend
from nucllei.utils.image_refs import file_id_of_url, resolve_url, route_of_url
from nucllei.utils.ref_errors import (
    UNKNOWN_UPSTREAM_WHAT,
    RefErrorHints,
    explain_ref_error,
    file_id_error,
    mb,
)
from nucllei.utils.tool_render import Text

log = logging.getLogger(__name__)

# ページの URL が絶対 (院内の一般 URL) かの判定。_link_roots / _resolve_href が使う。
_ABSOLUTE_PAGE_RE = re.compile(r"^https?://", re.IGNORECASE)

# 相対 href を解決するための仮 origin。ここに残っていれば「同じ上流の中」の意味。
_FAKE_ORIGIN = "http://ext.invalid/"

# docstring に許可先を埋める差し込み位置 (module 末尾で置換する。冒頭コメント参照)。
_DOC_BASES_SLOT = "{PUBLIC_BASES}"

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


def _describe_bases(bases: dict) -> str:
    """docstring / ERROR に載せる許可先 (原本ホスト) の一覧 (EXT_PUBLIC_BASES から)。"""
    if not bases:
        return "(未設定 — 登録された原本ホストは無い)"
    return "、".join(f"`{base}` ({name})" for name, base in bases.items())


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


# resolver が理由を付けずに失敗したとき (通常起きない) の文面。
_ERR_UNEXPLAINED = (
    "を取得できない (上流不達・不在・redirect・サイズ超過のいずれか。"
    "redirect は辿らないので、ディレクトリではなくページを直接指すこと "
    "— 例 '<ルート>/' ではなく '<ルート>/top.aspx')。"
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
    hand_over = "中身が要らなければ URL をそのまま案内する (ブラウザでは開ける)。"
    size_known = size is not None and err.size_exact is True
    if size_known:
        # 実サイズが確定している (size_exact=True)。
        if ceiling_mb is not None and size > ceiling_mb * mib:
            # 天井も超えている — max_mb を上げても読めない
            return (
                f"ERROR: {u} は {mb(size)} で、取得の上限{limit_txt} を超えている。"
                f"この tool の最大 {ceiling_mb:.1f} MB も超えるので中身は読めない。"
                "ユーザーには URL をそのまま案内する (ブラウザでは開ける)。"
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
        f"ERROR: {what}主 context では読まず、run_subagent に URL と指示を渡し、"
        f'子が fetch_url(url="{u}", max_mb={suggest}) で読む (子は offset で続きを取れる)。'
        f"{more}"
        "pandas 等で加工・集計したいだけなら send_to_workspace(url=…) で sandbox に取り込む "
        "(読むためのテキスト化には使わない)。"
        f"{hand_over}"
    )


def _explain_ref_error(u: str, err, ceiling_mb: Optional[float] = None) -> str:
    """resolver の RefError をモデル向けの ERROR 文にする (組み立ては `utils/ref_errors`)。

    この tool 固有の言い方: too_large は run_subagent + max_mb (天井 ceiling_mb)、
    unknown_upstream には許可先の原本ホスト (EXT_PUBLIC_BASES、受理判定と同じ dict) を添える。
    """
    hints = RefErrorHints(
        too_large=lambda ref, e: _too_large(ref, e, ceiling_mb),
        unexplained=f"ERROR: {{ref}} {_ERR_UNEXPLAINED}",
        unknown_upstream={
            **UNKNOWN_UPSTREAM_WHAT,
            "outside_public_bases": (
                "は許可された原本ホストの外 (院内の閉域ネットワークなので外部サイトには"
                "届かない。読めるのは院内 Web だけ)"
            ),
        },
        unknown_upstream_extra=f"許可先の原本ホストは {_describe_bases(EXT_PUBLIC_BASES)}。",
    )
    return explain_ref_error(u, err, arg="url", hints=hints)


def _resolve_href(
    href: str, page_path: str, public_base: str, base_prefix: str, link_base: str
):
    """href を絶対 URL (原本 URL) に解決する。辿れない href は None。

    page_path は今開いているページの上流内パス (例 "annai.aspx")。
    base_prefix は上流 base URL のパス部分 (例 "his-info"; 無ければ "")。仮 origin の下に
    この prefix を敷いて urljoin することで、相対 href (`unyou_mn.aspx`) と
    ルート相対 href (`/his-info/x.pdf` — 実際にこの形が出る) を同じ規則で畳める。
    prefix の外へ出た href は同じ上流に載らない = この経路では辿れないので落とす。
    public_base は原本 origin を指す絶対 href を畳むための照合先 (無ければ "")、
    link_base は出力する URL のルート (原本 URL のルート。無ければ自オリジンの `/ext/{name}`)。
    """
    href = (href or "").strip()
    if not href or href.startswith(("javascript:", "#", "mailto:", "tel:")):
        return None
    if _ABSOLUTE_PAGE_RE.match(page_path):
        # 院内の一般 URL (_link_roots 参照): 通常の Web と同じくページの URL 基準で解決する。
        # 院内の範囲かどうかは次の fetch_url で resolver が判定する (ここでは絞らない)
        resolved = urljoin(page_path, href).split("#", 1)[0]
        return resolved if _ABSOLUTE_PAGE_RE.match(resolved) else None

    root = _FAKE_ORIGIN + (base_prefix + "/" if base_prefix else "")
    # 絶対 URL が原本 origin を指しているなら、上流内パスに畳み直す。
    if public_base and href.startswith(public_base + "/"):
        rel = href[len(public_base) + 1 :]
    else:
        resolved = urljoin(root + page_path, href)
        if not resolved.startswith(root):
            return None  # 別ホスト / 上流 base の外。allowlist の外なので辿れない
        rel = resolved[len(root) :]

    rel = rel.split("#", 1)[0]
    if not rel or rel.startswith("?"):
        return None
    return f"{link_base}/{rel}"


def _html_to_lines(
    raw: bytes,
    page_path: str,
    public_base: str,
    base_prefix: str,
    link_base: str,
    content_type: str = "",
):
    """HTML を (本文行, リンク行) に変換する。

    - script / style / noscript / img / input を除去
    - <a href> は本文で `ラベル [n]` にし、末尾のリンク一覧に `[n] ラベル → URL` を出す
      (URL は絶対 URL 1 本 = 原本 URL。_resolve_href)
    - 空白正規化 (連続空白を 1 つに、空行は落とす)
    - encoding は Content-Type の charset、無ければ utf-8 (冒頭コメント)。lxml に bytes を
      渡すと meta charset を見に行くので、必ず自分で decode した str を渡す
    """
    from lxml import etree, html as lxml_html

    doc = lxml_html.fromstring(_decode(raw, content_type))
    etree.strip_elements(doc, *_DROP_TAGS, with_tail=False)

    links = []
    for a in doc.iter("a"):
        url = _resolve_href(
            a.get("href"), page_path, public_base, base_prefix, link_base
        )
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
            "案内するだけになる",
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
        """院内 Web のページを 1 枚取得して読める平文にする。HTML はリンク一覧付きの
        平文、PDF はテキスト層の抽出結果を返す。

        **doc に収録済みの資料はこの tool で取りに行かない** (感染対策マニュアル /
        医療安全管理マニュアル / 電話番号簿 / 病院情報管理システム FAQ)。それらは
        grep_doc / read_doc の担当で、こちらは**doc に無い院内情報を辿る**ための道具。

        url はスキーム付きの絶対 URL (`http(s)://…`) か、同一サーバの `/ext/…`・`/api/v1/files/{id}/content`。
        辿れるのは院内だけ — 登録された原本ホスト ({PUBLIC_BASES}) の配下、同一サーバの `/ext/…`、
        それ以外の院内の範囲 (IP アドレス直指定も可。DNS の無い環境では IP で指定する)。
        入口の 1 枚はこのルートにページ名を続ける (例 `<ルート>/top.aspx`)。2 枚目からは
        **組み立てず、戻り値や read_doc に出てきた URL の literal をそのままコピーする**
        (相対ならホストを補わない)。院内の閉域ネットワークなので外部サイト (インターネット) には届かない — 禁止ではなく物理的に取得できない。

        戻り値のリンク一覧の URL は 1 行に 1 本 — 原本ホストのある上流は**原本そのものの URL**、
        原本ホストの無い配信元は同一サーバの `/ext/…`。次の fetch_url(url=) / feed_image(url=) /
        send_to_workspace(url=) にそのまま渡せ、職員にもそのまま案内できる。どちらの用途でも
        1 文字も変えずにコピーする。

        大きいページを丸ごと読むと会話が埋まる。索引ページ (お知らせ一覧など) は必ず
        `query` を付けて該当行だけ取り (部分一致・大小文字と全角半角は無視。1,000 件級の一覧
        ページには必ず付ける)、実体ページや PDF は run_subagent に 1 ページ 1 本で投げること。
        既定を超える大きい資料はサブエージェントの中で max_mb を付けて読む (主 context では読まない)。

        失敗時は "ERROR: ..." で始まる説明 — 何が起きたか (存在しない / サイズ上限超過 /
        redirect / 上流のエラー / 院内 Web に届かない / 許可先の外) と次の一手を書く。
        「取れなかった」を全部リンク切れと言い換えないこと。

        :param url: スキーム付きの絶対 URL (`http(s)://…`) か、同一サーバの `/ext/…`・`/api/v1/files/{id}/content` (例 "<ルート>/unyou_mn.aspx"、または戻り値・read_doc に出た URL の literal をそのまま)。
        :param query: (任意) 指定すると、本文行・リンク行のうちこの語を含む行だけを返す。
        :param offset: 何文字目から返すか (0 起点)。続きは戻り値の next_offset を渡す。
        :param limit: 返す文字数 (既定 8000)。valve の max_output_chars で頭打ちになる。
        :param max_mb: (任意) 取得サイズの上限 (MB)。既定を超える資料を読むときだけ (天井あり)。
        :return: ヘッダ (url / 種別 / 分量) + 本文 + リンク一覧。
                 失敗時は "ERROR: ..." で始まる説明 — 何が起きたか (存在しない / サイズ上限超過 /
                 redirect / 上流のエラー / 院内 Web に届かない / 許可先の外) と次の一手を書く。
                 サイズ上限超過は run_subagent の子が max_mb を付けて読む (中身が要らなければ URL を
                 案内)。「取れなかった」を全部リンク切れと言い換えないこと。
        """
        if not __user__ or not __user__.get("id"):
            return "ERROR: ユーザー情報が取得できませんでした。"

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
            raw, content_type, err = await self._read_file(
                fid, __user__["id"], max_bytes, cap_mb
            )
            if err:
                return err
            path, public_base, base_prefix, link_base, page_url = "", "", "", "", u
            head = f"# {u}\n"
        else:
            # 引数名どおり URL として扱う (冒頭コメント)。受ける形・許可先の判定は resolver に
            # 一任する (tool 側で同じ振り分けを重ねない)。ここで URL を分解するのは、取得できた
            # 後にリンク一覧のルート (原本 URL のルート) と相対 href の畳み方を決めるためだけ。
            if __progress__:
                __progress__(f"取得中: {u}")
            # サイズ上限は max_bytes で渡す — 文書を読む用途なので画像 inline 用の既定上限を
            # 借りない。too_large の ERROR 文は RefError.limit (実際に使われた上限) から MB を出す。
            loaded, err = await resolve_url(u, max_bytes=max_bytes)
            if err is not None or loaded is None:
                return _explain_ref_error(u, err, ceiling_mb)
            if (
                len(loaded.data) > max_bytes
            ):  # resolver が守るが、念のため (fail closed)
                return (
                    f"ERROR: {u} は {mb(len(loaded.data))} で大きすぎる "
                    f"(この呼び出しの上限 {cap_mb:.1f} MB)。中身は読めない。"
                    "ユーザーには URL をそのまま案内する (ブラウザでは開ける)。"
                )
            content_type = loaded.content_type or ""
            raw = loaded.data

            path, public_base, link_base, base_prefix, qs = self._link_roots(u)
            # このページ自身の原本 URL。入力が (a) なら u と同じ、(b) なら public base で組み直す。
            # 直下 (path == "") は `<ルート>/` (相対 href の結合で `//` を作らない)。原本ルートが
            # あればそれで組む (相対入力でリンクのルートが `/ext/{name}` でも、職員に案内するのは原本)。
            if _ABSOLUTE_PAGE_RE.match(path):
                # 院内の一般 URL: 原本 URL は入力そのもの。/ext/ の注意書きは出さない
                page_url, head = u, f"# {u}\n"
            else:
                page_root = public_base or link_base
                page_url = f"{page_root}/{path}{qs}" if page_root else u
                head = self._head(u, page_url, public_base)

        if __progress__:
            __progress__("変換中")
        try:
            kind, body, links = self._to_lines(
                raw, content_type, path, public_base, base_prefix, link_base
            )
            if fid:
                # 添付・生成物の HTML の相対 href はどの名前空間にも属さない (`/page2.html` に
                # なってしまう) ので、file 経路ではリンク一覧を出さない
                links = []
        except Exception as e:
            log.error(f"[fetch_url] convert failed ({u}): {e!r}")
            return f"ERROR: 変換に失敗した ({e})。"

        media_type = _media_type(content_type)
        if kind == "other" and media_type.startswith("image/"):
            # 画像は平文にできないが**行き止まりではない** — feed_image が同じ URL を
            # url でそのまま受けて自分の目に入れられる (2026-09-04 に拡張子判定をやめたので、
            # 拡張子を持たない配信口の画像もそのまま渡せる)。ここで原本 URL だけを
            # 返すと、モデルは見られるものを見ずに職員へ丸投げする。
            return (
                head + f"種別: 画像 (content-type={media_type})。平文にはできない。\n"
                f'feed_image(url="{u}") で自分の目で見ること。'
            )
        if kind == "other":
            return (
                f"ERROR: 平文にできない形式 (content-type={media_type or '不明'})。"
                f" 職員にはこの URL を案内する: {page_url}"
            )
        if kind == "pdf-noText":
            # feed_image は拡張子ではなく取得した content-type で PDF を判定する。
            # log_write.aspx のような拡張子を持たない配信口でもそのまま渡してよい。
            hint = (
                f'feed_image(url="{u}") でページを見ること (pages="3" 等で範囲指定)。'
            )
            return (
                head + f"種別: PDF / テキスト層が無い (抽出 {len(body)} 字 < "
                f"{self.valves.pdf_text_char_threshold} 字)。スキャン画像の PDF。\n"
                f"{hint}\n"
                "内容を要約するより、原本 URL を職員に開いてもらう方が確実。"
            )

        return self._render(head, kind, body, links, query, offset, limit, url=u)

    # --- 以下 helper (モデルからは見えない) ---------------------------------

    @staticmethod
    def _link_roots(u: str):
        """取得できた URL について (上流内 path, 原本ルート, リンクのルート, href を畳む prefix, ?query)。

        **resolve_url が成功した後にだけ呼ぶ** (受理・許可先の判定はしない。リンク一覧を組む
        ためのルートを求めるだけ)。`route_of_url` で引く — リンクのルートは原本ルート
        (public_base) がある name なら入力が相対でも絶対でもそれ (`/ext/{name}/…` のゲートは
        上流の 3xx を 502 に落とすので、利用者が実際に開ける原本の URL を返す)。無い name
        (file backend) だけ、相対入力なら `/ext/{name}`、絶対なら `{origin}/ext/{name}`。
        院内の一般 URL (`route.intranet`) は名前空間を持たない — path にページの絶対 URL を
        入れて返し、`_resolve_href` が通常の Web と同じくページの URL 基準で解決する。
        ルーティングできない (resolver が通したのに、という構成のずれ) ときはリンクを組まない。
        """
        route, _err = route_of_url(u)
        if route is None:
            return "", "", "", "", ""
        if route.intranet:
            return route.page_url or u.split("#", 1)[0], "", "", "", ""
        name, path, qs = route.name, route.rel, route.query
        public_base = route.public_base or ""
        if public_base:
            link_base = public_base
        elif route.relative:
            link_base = f"/ext/{name}"
        else:
            link_base = f"{route.origin}/ext/{name}"
        return path, public_base, link_base, Tools._base_prefix(name, public_base), qs

    @staticmethod
    def _base_prefix(name: str, public_base: str) -> str:
        """ルート相対 href (`/his-info/x.pdf`) を畳むための prefix。ページの href は**職員側の
        origin** (原本 URL = EXT_PUBLIC_BASES) に対する相対なので、原本 URL のルートの
        パス部分 (例 "his-info") を使う。原本ルートが無い name だけ http backend のパスに
        落とす。file backend はローカル root なので prefix は無い。"""
        if public_base:
            return urlsplit(public_base).path.strip("/")
        backend = ext_backend(name)
        return (
            urlsplit(backend[1]).path.strip("/")
            if backend and backend[0] == "http"
            else ""
        )

    async def _read_file(self, fid: str, user_id: str, max_bytes: int, cap_mb: float):
        """url の `/api/v1/files/{id}/content` を file として読む → (raw, content_type, ERROR 文)。
        所有者チェックは feed_image の file_id 経路と同じ (#40 と同クラスの IDOR 封鎖)。
        content_type は Files の meta の生の値 (charset パラメータ付きのことがある)。"""
        rec = await asyncio.to_thread(Files.get_file_by_id, fid)  # v0.6.5 は sync
        if not rec or getattr(rec, "user_id", None) != user_id:
            # レコード無しとアクセス権無しは同じ固定文 (存在の有無を漏らさない)
            return None, "", file_id_error(fid)
        try:
            local = await asyncio.to_thread(Storage.get_file, rec.path)
            raw = await asyncio.to_thread(pathlib.Path(local).read_bytes)
        except Exception as e:  # noqa: BLE001
            log.error(f"[fetch_url] file {fid} read failed: {e!r}")
            return None, "", f"ERROR: file_id {fid} の実体を読めない。"
        if len(raw) > max_bytes:
            return (
                None,
                "",
                (
                    f"ERROR: このファイルは {mb(len(raw))} で、取得の上限 {cap_mb:.1f} MB を超えている。"
                    "添付は read_attachment(file_id=) で読む。"
                ),
            )
        meta = getattr(rec, "meta", None) or {}
        return raw, meta.get("content_type") or "", None

    @staticmethod
    def _head(u: str, page_url: str, public_base: str) -> str:
        """戻り値の先頭行群。`# url` + (入力と違うときだけ) 原本 URL、
        原本 URL のルートが無い name ならその旨 1 行。"""
        head = f"# {u}\n"
        if public_base and page_url != u:
            head += f"原本 (職員に案内する URL): {page_url}\n"
        if not public_base:
            head += (
                "注意: この資料の配信元には原本ホストが無いため、リンク一覧の URL は同一サーバの "
                "`/ext/…` (次の fetch_url / feed_image(url=) にそのまま渡せ、本文にもそのまま貼れる)。\n"
            )
        return head

    def _to_lines(
        self,
        raw: bytes,
        content_type: str,
        path: str,
        public_base: str,
        base_prefix: str,
        link_base: str,
    ):
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
            body, links = _html_to_lines(
                raw, path, public_base, base_prefix, link_base, content_type
            )
            return "html", body, links

        # JSON / XML / 素のテキスト系は本文をそのまま行にする (院内 API の応答・設定ファイル等。
        # 院内の範囲を直接取れるようになり (EXT_INTRANET_CIDRS)、JSON を返す口に当たることが増えた)
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
                f'ユーザーが見るものと同じものを見るなら feed_image(url="{url}", pages="1-3" 等) (重くない)。\n'
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
                    "語を短く・具体的にして引き直すか、query 無しで先頭から読むこと。"
                )
            all_lines = kept

        text = "\n".join(all_lines)
        cap = max(1, int(self.valves.max_output_chars))
        limit = max(1, min(int(limit or cap), cap))
        offset = max(0, int(offset or 0))
        if offset >= len(text):
            return (
                f"ERROR: offset={offset} は末尾 ({len(text)} 字) を超えている。"
                "offset を戻すか、query で絞ること。"
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


# 許可先 (原本ホスト) を tool の説明文に名乗らせる (冒頭コメント「戻り値の URL は 1 本」)。
# spec は起動時 sync で nucllei プロセス内のこの docstring から生成されるので、そのときの
# env EXT_PUBLIC_BASES (受理判定・ERROR 文と同じ dict) が載る。
Tools.fetch_url.__doc__ = Tools.fetch_url.__doc__.replace(
    _DOC_BASES_SLOT, _describe_bases(EXT_PUBLIC_BASES)
)
