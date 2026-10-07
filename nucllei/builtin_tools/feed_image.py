"""組み込みツール: file_id の画像・PDF、URL (url)、sandbox の絶対パス (path) を
主モデル自身の目 (VLM) に同一ターンで見せる。File は発行しない。

配備層 (prism-hu/llens の `tools/feed_image.py`) から本体へ移設した (2026-10-03、#141)。
判断の根拠は sandbox と同じ**依存の向き**: この tool は `utils/image_refs` の resolver
(resolve_url / resolve_path / file_id_of_url / detect_kind / split_page / with_page) と
`models.files` / `storage.provider` を掴んでいて、配備側に置く限り「相手の nucllei がその
API を持っているか」を try/except で交渉し続けるしかなかった (旧 nucllei 向けの
`_OLD_RESOLVER` 固定文・公開定数のローカル同文がその痕跡)。builtin なら nucllei と同じ
tag で出るので交渉が要らない (`_memos/builtin-tools.md` 1c: 境界基準は**出荷単位**)。
LLENS 固有の知識はこのファイルに無い (許可先・配信元は全部 env と resolver 側)。

仕組み (汎用 tool-push 経路 #89/#90): tool は検証して `__feed_image__(参照)` を呼ぶだけ。
middleware が実行中 tool-call の sink から参照を harvest して results[i].context_images
(list[str]、DB に永続) へ載せ、送信直前 inline (`utils/file_images.py`) で 1 回だけ
base64 化してモデルへ再投入する。base64 は戻り境界にも DB スナップショットにも載らない。
前提: モデルが画像入力対応 (VLM) であること (text-only では無意味)。

参照の形 (#139 → 2026-09-07 に 3 形へ整理): sink に積めるのは file_id か参照文字列。
  - file_id: owner チェック (#40 と同クラスの IDOR 封鎖) を本 tool で行う。送信直前
    inline でも has_read_access_to_file が二重に効く (defense-in-depth)。
  - URL: ブラウザが開くもの。スキーム付きの絶対 URL (既存チャットに残る
    `https://<host>/ext/…`、それ以外の一般の URL — 院内でもインターネットでも。届くかは
    配備のネットワーク次第) と、同一サーバの相対 URL `/ext/{rest}` (配備層の read_doc の
    図・原本 PDF。env `EXT_MOUNTS` の対応表で静的配信 / proxy)。
  - sandbox の fs パス (`/ext/…` `/workspace/…`)。`/ext/…` は URL と sandbox のパスが同じ
    文字列で同じファイル (唯一の意図した重なり。url に来れば percent-decode して、path に
    来ればそのまま、どちらも `EXT_MOUNTS` の対応表で読む)。

引数 (2026-10-03 ken 決定 — `read_ref` / `ref` への統一 (#141) は不採用。tool 全体で引数名の
意味を固定する): `url` = URL (絶対、または同一サーバの `/ext/…`・`/api/v1/files/…`)、
`path` = sandbox の fs の絶対パス、`file_id` = 添付・生成物。
  **解釈は引数名だけで決める。値の形を見て別の引数の意味に読み替えない。** url は
  `resolve_url`、path は `resolve_path` に**引数名どおりに**渡す (形で振り分ける `resolve_ref`
  は保存済み参照文字列専用で、tool の引数には使わない)。名前どおりに解決できない値
  (url="/workspace/…" / path="https://…" 等) は resolver が `unsupported_form` を返し、tool はその
  引数が受ける形を書いた**固定文**の ERROR にする (`ref_errors.url_error` / `path_error`)。
  値を調べて「それは path に」と出し分けない — 行き先の案内は prompt / skill の参照規則の側に
  書く。失敗理由の言い分け (存在しない / サイズ超過 / redirect / 上流 / 届かない / 許可先の外)
  は解決結果に基づくので残す (`utils/ref_errors`)。
  url の `/api/v1/files/{id}/content` (このサーバのファイル配信口。相対・絶対とも) は URL の
  ルーティングとして file に当たる (`file_id_of_url`) — file_id 経路と同じ所有者チェックで
  読む (send_to_workspace(url=) と同じ扱い)。

  参照 → バイトの解決規則は `utils/image_refs.py` が**一箇所で**持つ。URL の上流解決
  (path が /ext/{rest} なら host を問わず `EXT_MOUNTS` の対応表で読み — 静的はファイル、proxy は上流、
  それ以外は一般の URL としてそのホストに直接 — 行き先で絞らず、断るのはこのサーバ自身だけ。
  2026-10-03 ken 決定) も
  traversal 判定もサイズ上限もそこにある — tool 側に allowlist も on/off valve も持たない
  (境界は resolver が持つ。tool のスイッチはモデルが届く範囲を変えない)。
  tool は call 時に同じ resolver で読んで検証 (存在・種別・サイズ・ページ数) し、参照を
  積むだけ。inline 層も同じ resolver で実体化し、PDF はページ指定 (`#page=N`、with_page。
  URL でも同じ) を送信直前にラスタライズする。
  **File も Storage も作らない。** 旧実装は PDF ページを 1 枚ずつ PNG にして Storage + Files
  に登録していた (ユーザーが上げても作ってもいないものが GC 無しで積もる) — #139 で撤去し、
  file_id の PDF も `/api/v1/files/<id>/content#page=N` の参照で積む。

経緯 (今も真):
  - 2026-08-26: 文書の原本 PDF (当時は `/ext/` 相対) の feed を追加 (それまで image/* だけで、
    モデルが節 PDF のページを見る経路が無く、`/ext/` を fs パスと誤認した sandbox への
    誤フォールバック (cp → ENOENT) を誘発していた)。
  - 2026-09-04: 種別判定を拡張子から content-type / magic bytes に移した。院内の配信口は
    拡張子を持たない形が普通 (病院情報管理システムの資料リンクは全て
    `log_write.aspx?...&LI_URL=x.pdf`) で、拡張子判定では院内資料を目で見る経路が
    丸ごと無かった。今は resolver の detect_kind が同じ規律 (fetch_url は content-type と
    先頭バイトで自前判定)。
"""

import asyncio
import logging
import os
from typing import Callable, Optional

from pydantic import BaseModel, Field

from nucllei.models.files import Files
from nucllei.storage.provider import Storage
from nucllei.utils.image_refs import (
    REF_ARGS_RULE,
    detect_kind,
    file_id_of_url,
    pdf_page_count,
    resolve_path,
    resolve_url,
    split_page,
    url_for_mounted_path,
    with_page,
)
from nucllei.utils.tool_errors import ToolCallError, ToolError, ToolFailure, ToolOutcome
from nucllei.utils.tool_output import mark_error_kind
from nucllei.utils.ref_errors import (
    NEXT,
    RefErrorHints,
    explain_ref_error,
    file_id_error,
    mb,
    path_error,
    show_as_link,
    url_error,
)

log = logging.getLogger(__name__)

_IMAGE_EXTS = {"png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff", "gif"}

# モデルに返す文は「事実の文 + `次の手:` 以降」(`utils/ref_errors` の module docstring)。
# 別の toolkit の tool (sandbox の run_python / send_to_workspace / fetch_from_workspace、
# fetch_url、run_subagent) は載っていないことがあるので「〜が使えるなら」と条件付きで書く。

# resolver が理由を付けずに失敗したとき (通常起きない) の文面。
_ERR_UNEXPLAINED = "を読めない (存在しない / 許可された場所の外 / 大きすぎる)。"

# sandbox のファイルをユーザーに見せる方法 (fetch_from_workspace がリンクを返す)。
_SHOW_PATH = (
    "fetch_from_workspace が使えるなら、ユーザーに見せるときはそれに path を渡し、"
    "返ったリンクを本文に書く。"
)


def _hand_over(ref: str, is_path: bool) -> str:
    """「中身は読めない」の後の次の一手 (渡し方)。URL ならブラウザで開けるのでリンクで
    示せるが、sandbox の fs パスはブラウザに意味が無い (リンクにしろとは言わない)。"""
    if is_path:
        return "sandbox のファイルなので、" + _SHOW_PATH
    return show_as_link(ref)


def _too_large(ref: str, err, *, is_path: bool) -> str:
    """too_large の全文。resolver の size_exact: True なら size は実サイズ (Content-Length /
    fs の stat / Files レコード)、False なら打ち切り時点の受信量 = 実サイズの下限。
    ここは「読めない」と言うだけなので、下限なら「少なくとも」を付けて出す。size と limit の
    差から推測はしない。"""
    size, limit = err.size, err.limit
    if size is None:
        what = f"{ref} は 取得の上限"
    elif err.size_exact:
        what = f"{ref} は {mb(size)} あり、取得の上限"
    else:
        what = f"{ref} は少なくとも {mb(size)} あり、取得の上限"
    what += f" {mb(limit)}" if limit is not None else ""
    # resolver の上限 (既定 25 MB) は画像の上限 (valve max_image_mb、既定 8 MB) より大きいので、
    # ここに来た画像は _feed_path の「run_python でリサイズ」まで届かない。次の一手はここで言う:
    # fs なら sandbox から直接開いて縮小、URL なら send_to_workspace で取り込んでから縮小。
    if is_path:
        shrink = (
            "画像で run_python が使えるなら、このパスを開いて縮小し、出力を /workspace/… に"
            "書いてからその path を feed_image(path=…) に渡す。"
        )
    else:
        shrink = (
            "画像で send_to_workspace と run_python が使えるなら、send_to_workspace(url=…) で"
            " sandbox に取り込み、run_python で縮小してから /workspace/… の path を"
            " feed_image(path=…) に渡す。PDF のテキストが要り、run_subagent と fetch_url が"
            "使えるなら、run_subagent の子が fetch_url を max_mb 付きで読む。"
        )
    return (
        f"ERROR: {what} を超えている。このままでは自分の目に入れられない"
        f" (PDF は pages を絞っても上限は変わらない)。{NEXT}{shrink}{_hand_over(ref, is_path)}"
    )


# path の not_found: `/ext/…` は nucllei 側の /ext (EXT_MOUNTS) に無い (fetch_from_workspace と同文。
# ken 2026-10-04: 配備は nucllei と sandbox に同じ /ext を mount する — 無いのは本当に無いか
# mount のずれ)。`/workspace/…` は sandbox が 404 を返した = 無い
_EXT_PATH_NOT_FOUND = (
    "ERROR: {ref} はこのサーバの /ext に無い (存在しないか、sandbox にだけ mount されている)。"
    + NEXT
    + "中身が要るなら sandbox で読んで使う。"
)
# 確かめ方は専用の list_workspace (sandbox の toolkit。feed_image だけが載っていることもあるので
# 条件付き — run_shell と同じ toolkit なので、run_shell の ls を案内する場面は無い)
_LIST_PARENT = "list_workspace が使えるなら、親ディレクトリを一覧してパスを確かめる。"
_WORKSPACE_PATH_NOT_FOUND = "ERROR: {ref} は存在しない。" + NEXT + _LIST_PARENT


def _hints(is_path: bool, ref: str = "") -> RefErrorHints:
    """この tool の言い分け (`utils/ref_errors`)。path (sandbox の fs パス) で来たものには
    「リンクで示す」と言わない — ブラウザに意味が無い。is_path は**どの引数で来たか**
    (値の形では判定しない)。url で来たものは共通の既定文 (redirect も既定 — 3 回まで辿る)。"""
    return RefErrorHints(
        too_large=lambda ref, err: _too_large(ref, err, is_path=is_path),
        unexplained=f"ERROR: {{ref}} {_ERR_UNEXPLAINED}",
        # path は置き場 (sandbox の fs の 2 つの root) で言い分ける — 値の形で別の引数に
        # 読み替えるのではなく、同じ path の中の区分 (sandbox の `_RO_MOUNT_ROOT` と同じ)
        not_found=(
            (
                _EXT_PATH_NOT_FOUND
                if ref.startswith("/ext/")
                else _WORKSPACE_PATH_NOT_FOUND
            )
            if is_path
            else RefErrorHints.not_found
        ),
        # 上流不達は URL (一般の URL) でしか起きないが、万一 fs で来ても「リンクで示す」とは言わない。
        unreachable=(
            "ERROR: {ref} に届かない (このサーバからは到達できないか、一時的な障害)。"
            + NEXT
            + (
                "run_shell が使えるなら ls で存在を確かめる。"
                if ref.startswith("/ext/")
                else _LIST_PARENT
            )
            if is_path
            else RefErrorHints.unreachable
        ),
        # sandbox の 404 は not_found に行くので、ここは「繋がらない」だけ
        workspace_unavailable=(
            "ERROR: {ref} は sandbox のファイルだが、sandbox に繋がらない (起動していないか、応答が無い)。"
            + NEXT
            + "時間を置いて 1 回だけ呼び直す。続くならユーザーに sandbox が使えないと伝える。"
        ),
    )


def _explain_ref_error(ref: str, err, is_path: bool = False) -> ToolError:
    """resolver の RefError をモデル向けの ToolError にする (組み立ては `utils/ref_errors`)。"""
    return explain_ref_error(
        ref, err, arg="path" if is_path else "url", hints=_hints(is_path, ref)
    )


def _md_label(name: str) -> str:
    """markdown リンクのラベルに入れるファイル名 (`[` `]` は崩れるので丸括弧に)。"""
    return (name or "").replace("[", "(").replace("]", ")")


def _read_raw(rec) -> Optional[bytes]:
    """Storage 実体から生バイトを読む (サイズ検証・ページ数取得用)。"""
    path = getattr(rec, "path", None)
    if not path:
        return None
    try:
        local = Storage.get_file(path)
        with open(local, "rb") as f:
            return f.read()
    except Exception as e:
        log.error(f"[feed_image] raw read failed: {e!r}")
        return None


def _kind(fname: Optional[str], content_type: Optional[str]) -> Optional[str]:
    """'pdf' / 'image' / None を返す (file_id 経路の Files メタ用)。"""
    ct = (content_type or "").lower()
    if ct == "application/pdf":
        return "pdf"
    if ct.startswith("image/"):
        return "image"
    if fname:
        ext = os.path.splitext(fname)[1].lower().lstrip(".")
        if ext == "pdf":
            return "pdf"
        if ext in _IMAGE_EXTS:
            return "image"
    return None


def _page_span(lo: int, hi: int) -> str:
    return f"p.{lo}" if lo == hi else f"p.{lo}-{hi}"


def _parse_pages(
    spec: Optional[str], n_pages: int
) -> tuple[list[int], list[str], bool]:
    """ "1-10" / "3" / "1-3,7" を 0-origin のページ番号リストへ。

    戻り値は (積むページ, 範囲外で落とした指定 (`p.12-15` 等), 読める指定が 1 つでもあったか)。
    spec 無しは全ページ。範囲外を黙って全ページに読み替えない (#145 — 旧: 指定が 1 ページも
    当たらないと全ページを積んでいた)。"""
    if not spec:
        return list(range(n_pages)), [], True
    out: list[int] = []
    dropped: list[str] = []
    parsed = False
    for part in str(spec).replace(" ", "").split(","):
        if not part:
            continue
        try:
            if "-" in part:
                a, b = part.split("-", 1)
                lo, hi = int(a), int(b)
            else:
                lo = hi = int(part)
        except ValueError:
            continue
        if lo > hi:
            continue
        parsed = True
        if lo < 1:
            dropped.append(_page_span(lo, min(hi, 0)))
        if hi > n_pages:
            dropped.append(_page_span(max(lo, n_pages + 1), hi))
        for p in range(max(1, lo), min(n_pages, hi) + 1):
            if (p - 1) not in out:
                out.append(p - 1)
    return out, dropped, parsed


def _iter_context_files(__files__, __metadata__):
    """チャット文脈のファイル参照を (file_id, filename, ref) で列挙。
    sandbox.py の同名関数と違い、無指定時の PDF 絞り込みに要る content_type を引くため
    元の参照 (ref) も返す。"""
    raw = []
    if __files__:
        raw = __files__
    elif __metadata__ and __metadata__.get("files"):
        raw = __metadata__["files"]
    for f in raw or []:
        inner = f.get("file", {}) if isinstance(f, dict) else {}
        fid = (f.get("id") if isinstance(f, dict) else None) or inner.get("id")
        fname = (
            (f.get("name") if isinstance(f, dict) else None)
            or inner.get("filename")
            or (inner.get("meta") or {}).get("name")
        )
        if fid:
            yield fid, fname, f


class Tools:
    class Valves(BaseModel):
        max_image_mb: float = Field(
            default=8.0,
            description="モデルに渡す画像の上限サイズ。超過はエラーを返す (リサイズを促す)。",
        )
        max_pages: int = Field(
            default=3,
            description=(
                "1 回の呼び出しで提示する PDF ページ数の上限。"
                "多量のページを一度に渡すと VLM 側の画像前処理でメモリを圧迫するので絞る。"
            ),
        )

    def __init__(self) -> None:
        self.valves = self.Valves()

    async def feed_image(
        self,
        url: Optional[str] = None,
        path: Optional[str] = None,
        file_id: Optional[str] = None,
        filename: Optional[str] = None,
        pages: Optional[str] = None,
        __user__: Optional[dict] = None,
        __files__: Optional[list] = None,
        __metadata__: Optional[dict] = None,
        __feed_image__: Optional[Callable[[str], None]] = None,
    ) -> str:
        """画像・PDF を自分の目 (VLM) に入れ、このターン内で実際に見て判断する (図・レイアウト・印影・手書き)。
        ユーザーには何も表示されず、Files にも登録しない — 見せるなら本文に `![図: …](URL)` を書く。

        対象は url / path / file_id のどれか 1 つ。受け取った形のまま渡す (組み立てない・書き換えない・
        ホストを補わない)。3 つとも省略すると、この会話に添付された PDF 全件 (filename で絞り込み。
        直接添付の画像は既に見えている)。
        PDF は pages のページ (無ければ url の #page=N、どちらも無ければ先頭 3 ページと総ページ数が返る)。

        戻り値は何を目に入れたかの説明。失敗時は "ERROR: ..." で始まる文 — 何が起きたか (事実) と、
        「次の手:」以降に自分が次にすること (ユーザーには伝えない)。

        :param url: (任意) スキーム付きの絶対 URL (`http(s)://…`) か、同一サーバの `/ext/…`・`/api/v1/files/{id}/content` (戻り値の literal をそのまま)。
        :param path: (任意) sandbox のファイルの絶対パス (`/workspace/…` `/ext/…`)。
        :param file_id: (任意) 見たい画像・PDF の file_id (添付・生成物の id。UUID)。
        :param filename: (任意) 対象ファイル名の部分一致フィルタ。
        :param pages: (任意) PDF のページ範囲。"3" / "4-6" / "1-2,7" 形式。画像には付けない。
        """
        # 使い方の細目 (失敗の種類・#page=N・file_id の出どころ等) は README_API §5.7.1。
        # 型の判定・ERROR 文の次の一手は実行時の戻り値が持つ。
        if not __user__ or not __user__.get("id"):
            raise ToolFailure("ユーザーを特定できない")
        if __feed_image__ is None:
            # 実行文脈外 (通常起こらない)。参照を push できないので明示エラー。
            raise ToolFailure("この呼び出しでは画像を目に入れる経路が使えない")

        # --- url / path ソース (#139): 参照文字列を resolver で読んで検証し、参照を積む ---
        # 扱いは引数名だけで決める (module docstring「引数」。値の形で読み替えない)。
        if url is not None or path is not None:
            if file_id or filename:
                raise ToolCallError(
                    "url / path は file_id / filename と同時に指定できない",
                    "添付・生成物は file_id、URL は url、sandbox のファイルは path の"
                    "どれか 1 つだけを指定する。",
                )
            u = url.strip() if isinstance(url, str) else ""
            p = path.strip() if isinstance(path, str) else ""
            if u and p:
                raise ToolCallError(
                    "url と path は同時に指定できない",
                    f"1 回の呼び出しでどちらか一方だけを渡す。{REF_ARGS_RULE}",
                )
            if not (u or p):
                raise url_error("") if url is not None else path_error("")
            if u:
                # このサーバのファイル配信口 (相対・絶対とも) は URL のルーティングとして
                # file に当たる — file_id 経路と同じ所有者チェックで読む (module docstring「引数」)。
                # file_id_of_url は fragment 付きでも id を返す。`#page=N` は split_page で別に読む
                fid = file_id_of_url(u)
                if fid:
                    _base, frag_page = split_page(u)
                    if frag_page is not None and not pages:
                        pages = str(frag_page)
                    return await self._feed_one_file(
                        fid, pages, __user__["id"], __feed_image__
                    )
            # user は dict ごと渡す — /ext/groups・/ext/public の ACL に role が要る (#152)
            return await self._feed_path(
                p or u, pages, __user__, __feed_image__, is_path=bool(p)
            )

        targets: list[str] = []
        if file_id:
            targets.append(file_id)
        else:
            for fid, fname, ref in _iter_context_files(__files__, __metadata__):
                ct = (
                    (ref.get("file", {}) if isinstance(ref, dict) else {}).get("meta")
                    or {}
                ).get("content_type")
                # 無指定は添付 PDF だけ (docstring)。添付画像は既にモデルの入力に入っている
                # ので、ここで拾うと同じ画像を二重に食わせる。
                if _kind(fname, ct) != "pdf":
                    continue
                if filename and (not fname or filename.lower() not in fname.lower()):
                    continue
                targets.append(fid)

        if not targets:
            # ここに来るのは url / path / file_id が無いときだけ (file_id の不在は _feed_file の
            # file_id_error)。filename で添付を指したのに一致する添付 PDF が無い = 指した物が無い
            # → outcome、何も指していない (添付 PDF も無い) = 呼び方の問題 → call (llens 判定
            # 2026-10-04)。文面は同じ
            raise ToolError(
                "対象が見つからない。無指定で対象になるのは添付 PDF だけ "
                "(添付画像は既に自分の目に入っている)",
                "添付画像を見直すなら file_id を、URL なら url を、sandbox のファイルなら"
                " path を指定する。",
                kind="outcome" if filename else "call",
            )

        if len(targets) == 1:
            # 1 件 (file_id 指定・添付 PDF 1 件) の失敗は呼び出しの失敗 (ToolError のまま上げる)
            return await self._feed_one_file(
                targets[0], pages, __user__["id"], __feed_image__
            )
        out: list[str] = []
        kinds: list[str] = []
        for fid in targets:
            # 複数の添付 PDF: 1 件の失敗で他を落とさない — その行に ERROR 文を置く (旧来どおり)
            try:
                out.append(
                    await self._feed_file(
                        fid, pages, __user__["id"], __feed_image__, kinds
                    )
                )
            except ToolError as e:
                kinds.append(e.kind)
                out.append(e.text)
        # 1 件も目に入らなかったときだけ旗 (#145)。種別が揃っていればそれ、混在は failure
        if len(kinds) == len(targets):
            mark_error_kind(kinds[0] if len(set(kinds)) == 1 else "failure")

        # role:tool の content は非空の status 文字列。画素は results[i].context_images 経由で届く。
        return "\n".join(out)

    async def _feed_one_file(self, fid: str, pages, user_id: str, feed) -> str:
        """file 1 件だけを指した呼び出し (file_id= / url= の配信口 / 添付 PDF 1 件)。
        `_feed_file` が目に入れずに返した行は、文面はそのままで種別の旗を立てる (#145 —
        旧: 「画像でも PDF でもないので目に入れなかった」等が緑だった)。"""
        kinds: list[str] = []
        text = await self._feed_file(fid, pages, user_id, feed, kinds)
        if kinds:
            mark_error_kind(kinds[0])
        return text

    async def _feed_file(
        self, fid: str, pages, user_id: str, feed, kinds: Optional[list] = None
    ) -> str:
        """file (添付・生成物) 1 件を所有者チェックの上で積み、報告 1 行を返す。
        file_id 引数と、url の `/api/v1/files/{id}/content` の両方がここを通る。
        目に入れずに返す行は、その種別を `kinds` に積む (`_feed_path` が raise する同じ場面の
        種別に揃える: 画像でも PDF でもない = call / 実体が読めない = fatal /
        大きすぎる = outcome / PDF が開けない = failure)。"""
        if kinds is None:
            kinds = []
        # 同期 DB / ディスク read はワーカースレッドへ退避してイベントループを塞がない
        # (#28)。to_thread を await するため関数自体は async のまま。
        rec = await asyncio.to_thread(Files.get_file_by_id, fid)  # v0.6.5 は sync
        # 所有者チェック (#40 と同クラスの IDOR 封鎖): file_id 直指定で他ユーザーのファイルを
        # 覗かせない。レコード無しとアクセス権無しは同じ固定文 (存在の有無を漏らさない)。
        if not rec:
            raise file_id_error(fid)
        owner = getattr(rec, "user_id", None)
        if owner != user_id:
            raise file_id_error(fid)

        meta = getattr(rec, "meta", None) or {}
        name = getattr(rec, "filename", None) or fid
        kind = _kind(name, meta.get("content_type"))
        if not kind:
            kinds.append("call")
            return f"- {name}: 画像でも PDF でもないので目に入れなかった"

        raw = await asyncio.to_thread(_read_raw, rec)
        if raw is None:
            kinds.append("fatal")
            return f"- {name}: ファイルの実体を読めない"

        if kind == "image":
            # サイズ検証: 巨大なファイルを提示前に弾く (実体化は送信直前 inline に
            # 寄せるが、上限超過はここで早期に返す)。
            if len(raw) > self.valves.max_image_mb * 1024 * 1024:
                kinds.append("outcome")
                return (
                    f"- {name}: 大きすぎる ({mb(len(raw))} > {self.valves.max_image_mb:.1f} MB)。"
                    + NEXT
                    + "send_to_workspace と run_python が使えるなら、send_to_workspace(file_id=…)"
                    " で sandbox に取り込んで縮小し、/workspace/… の path を feed_image(path=…) に渡す。"
                )
            # 汎用 tool-push: 実行中 tool-call の sink に file_id を積む。実体化は
            # 送信直前 inline に一任。base64 は戻り境界に載せない。
            feed(fid)
            return (
                f"- {name} を自分の目に入れた (file_id={fid}。この呼び出しではユーザーに表示していない)。"
                f"{NEXT}画面にまだ出ていない画像を見せるときだけ、本文に"
                f" ![{_md_label(name)}](/api/v1/files/{fid}/content) を書く。"
            )
        # PDF は File を作らず、内部 file URL にページを載せた参照で積む
        # (inline 層が送信直前にそのページをラスタライズする)。
        try:
            n_pages = await asyncio.to_thread(pdf_page_count, raw)
        except Exception as e:
            log.error(f"[feed_image] {name}: {e!r}")
            kinds.append("failure")
            return f"- {name}: PDF として開けない (壊れているか、暗号化されている)"
        return self._feed_pdf_pages(
            f"/api/v1/files/{fid}/content", name, n_pages, pages, feed
        )

    async def _feed_path(
        self, value: str, pages, user: dict, feed, *, is_path: bool
    ) -> str:
        """url / path の値を resolver (`utils/image_refs`) で読んで検証し、積む。

        is_path は**どの引数で来たか** (True = path → resolve_path、False = url → resolve_url)。
        値の形で読み替えない — 名前どおりに解決できない値は resolver の unsupported_form を
        固定文の ERROR にする (module docstring「引数」)。

        解決規則 (許可 root・上流・traversal・サイズ上限) は全部 resolver 側。ここは
        「読めたか」「画像か PDF か」「大きすぎないか」「何ページか」を見るだけ。
        読めない理由は resolver の RefError.code ごとに言い分ける (_explain_ref_error) —
        束ねた 1 文だとサイズ超過も 404 も「リンク切れ」に化ける。
        """
        p = (value or "").strip()
        ref, frag_page = split_page(p)
        if frag_page is not None and not pages:
            pages = str(frag_page)

        if is_path:
            loaded, err = await resolve_path(ref, user)
        else:
            loaded, err = await resolve_url(ref, user=user)
        if err is not None or loaded is None:
            raise _explain_ref_error(ref, err, is_path)

        kind = detect_kind(loaded)
        if kind == "image":
            if pages:
                raise ToolCallError(
                    "pages は PDF のときだけ指定できる (この参照は画像)",
                    "pages を外して呼び直す。",
                )
            size = len(loaded.data)
            if size > self.valves.max_image_mb * 1024 * 1024:
                # resolver の上限 (既定 25 MB) 以下・画像の上限 (既定 8 MB) 超の帯。縮小の手順は
                # 参照の形で違う: fs は run_python が直接開ける、URL は先に取り込む。
                how = (
                    "run_python が使えるなら、このパスを開いて縮小し、/workspace/… に書いた path を"
                    " path= で渡す。"
                    if is_path
                    else "send_to_workspace と run_python が使えるなら、send_to_workspace(url=…) で"
                    " sandbox に取り込み、run_python で縮小して /workspace/… の path を path= で渡す。"
                )
                raise ToolOutcome(
                    f"{ref} は大きすぎる ({mb(size)} > "
                    f"{self.valves.max_image_mb:.1f} MB)",
                    how,
                )
            feed(ref)
            if is_path:
                # `/ext/…` で nucllei 側にも実体がある path は同じ文字列が URL (配信されている) —
                # url= と同じ案内。/workspace や配信されていない /ext (doc 等) はブラウザに
                # 意味が無いので本文に貼らせない (llens 指摘 2026-10-03)
                served = await url_for_mounted_path(ref, user)
                if served:
                    return (
                        f"- {ref} を自分の目に入れた (ユーザーには表示されない。このパスは URL"
                        f"としても配信されている)。{NEXT}見せるなら本文に ![…]({served}) を書く。"
                    )
                return (
                    f"- {ref} を自分の目に入れた (ユーザーには表示されない。sandbox のパスは本文に"
                    f"貼れない)。{NEXT}{_SHOW_PATH}"
                )
            return (
                f"- {ref} を自分の目に入れた (ユーザーには表示されない)。"
                f"{NEXT}見せるなら本文に ![…]({ref}) を書く。"
            )
        if kind == "pdf":
            try:
                n_pages = await asyncio.to_thread(pdf_page_count, loaded.data)
            except Exception as e:
                log.error(f"[feed_image] {ref}: {e!r}")
                raise ToolFailure(
                    f"{ref} は PDF として開けない (壊れているか、暗号化されている)"
                )
            return self._feed_pdf_pages(ref, ref, n_pages, pages, feed)
        raise ToolCallError(
            f"{ref} は画像でも PDF でもない (content-type={loaded.content_type or '不明'})",
            (
                "run_shell が使えるなら、テキストはそれで読む。"
                if is_path
                else "fetch_url が使えるなら、テキストは fetch_url(url=…) で読む。"
            ),
        )

    def _feed_pdf_pages(self, ref: str, label: str, n_pages: int, pages, feed) -> str:
        """PDF の指定ページを `ref#page=N` の参照で順に push する (File は作らない)。
        指定が 1 ページも当たらなければ ToolOutcome (読めない指定だけなら ToolCallError)、
        一部だけ当たればそのページを積んで、落とした分を結果に 1 行添える (旗なし)。"""
        wanted, dropped, parsed = _parse_pages(pages, n_pages)
        if not parsed:
            raise ToolCallError(
                f"pages {pages!r} をページ範囲として読めない",
                '"3" / "4-6" / "1-2,7" の形で pages を付けて呼び直す。',
            )
        if not wanted:
            raise ToolOutcome(
                f"{label} は全 {n_pages} ページで、指定の {pages} は範囲外",
                f"1-{n_pages} の範囲で pages を付けて呼び直す。",
            )
        limit = self.valves.max_pages
        truncated = len(wanted) > limit > 0
        if truncated:
            wanted = wanted[:limit]
        for i in wanted:
            feed(with_page(ref, i + 1))

        msg = f"- {label} の p.{', p.'.join(str(i + 1) for i in wanted)} を自分の目に入れた (全 {n_pages} ページ。ユーザーには表示されない)"
        if dropped:
            msg += f"\n  指定のうち {', '.join(dropped)} は範囲外なので見ていない。"
        if truncated:
            msg += (
                f"\n  上限 {limit} ページで打ち切った。{NEXT}他のページが要るなら pages で"
                "範囲を指定して呼び直す。"
            )
        return msg
