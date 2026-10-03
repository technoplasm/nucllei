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
    図・原本 PDF。nucllei の `/mnt/{rest}` をそのまま配信)。
  - sandbox の fs パス (`/mnt/…` `/workspace/…`)。

引数 (2026-10-03 ken 決定 — `read_ref` / `ref` への統一 (#141) は不採用。tool 全体で引数名の
意味を固定する): `url` = URL (絶対、または同一サーバの `/ext/…`・`/api/v1/files/…`)、
`path` = sandbox の fs の絶対パス、`file_id` = 添付・生成物。
  **解釈は引数名だけで決める。値の形を見て別の引数の意味に読み替えない。** url は
  `resolve_url`、path は `resolve_path` に**引数名どおりに**渡す (形で振り分ける `resolve_ref`
  は保存済み参照文字列専用で、tool の引数には使わない)。名前どおりに解決できない値
  (url="/mnt/…" / path="https://…" 等) は resolver が `unsupported_form` を返し、tool はその
  引数が受ける形を書いた**固定文**の ERROR にする (`ref_errors.url_error` / `path_error`)。
  値を調べて「それは path に」と出し分けない — 行き先の案内は prompt / skill の参照規則の側に
  書く。失敗理由の言い分け (存在しない / サイズ超過 / redirect / 上流 / 届かない / 許可先の外)
  は解決結果に基づくので残す (`utils/ref_errors`)。
  url の `/api/v1/files/{id}/content` (このサーバのファイル配信口。相対・絶対とも) は URL の
  ルーティングとして file に当たる (`file_id_of_url`) — file_id 経路と同じ所有者チェックで
  読む (send_to_workspace(url=) と同じ扱い)。

  参照 → バイトの解決規則は `utils/image_refs.py` が**一箇所で**持つ。URL の上流解決
  (path が /ext/{rest} なら host を問わず nucllei の `/mnt/{rest}` をローカル読み、
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
    with_page,
)
from nucllei.utils.ref_errors import (
    RefErrorHints,
    explain_ref_error,
    file_id_error,
    mb,
    path_error,
    url_error,
)

log = logging.getLogger(__name__)

_IMAGE_EXTS = {"png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff", "gif"}

# resolver が理由を付けずに失敗したとき (通常起きない) の文面。
_ERR_UNEXPLAINED = "を読めない (存在しない / 許可された場所の外 / 大きすぎる)。"
_HINT_LITERAL = (
    "read_doc / fetch_url の戻り値に出た URL の literal をそのまま渡すこと。"
)

# sandbox のファイルをユーザーに見せる方法 (fetch_from_workspace が URL か file_id を返す)。
_SHOW_PATH = (
    "見せるなら fetch_from_workspace(path=…) に渡し、返った URL か file_id で案内する。"
)


def _hand_over(is_path: bool) -> str:
    """「中身は読めない」の後の次の一手。URL ならブラウザで開けるので案内できるが、
    sandbox の fs パスはブラウザに意味が無い (URL を案内しろとは言わない)。"""
    if is_path:
        return "sandbox のファイルなので、" + _SHOW_PATH
    return "ユーザーには URL をそのまま案内する (ブラウザでは開ける)。"


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
            "画像なら run_python でこのパスを開いて縮小し、出力を /workspace/… に書いてから"
            "その path を feed_image(path=…) に渡す。PDF なら pages を絞っても上限は変わらない。"
        )
    else:
        shrink = (
            "画像なら send_to_workspace(url=…) で sandbox に取り込み、run_python で縮小して"
            "から /workspace/… の path を feed_image(path=…) に渡す。PDF なら pages を絞っても上限は"
            "変わらない (テキストが要るなら fetch_url を run_subagent の子が max_mb 付きで読む)。"
        )
    return f"ERROR: {what} を超えている。このままでは自分の目に入れられない。{shrink}{_hand_over(is_path)}"


def _hints(is_path: bool) -> RefErrorHints:
    """この tool の言い分け (`utils/ref_errors`)。path (sandbox の fs パス) で来たものには
    「URL を案内」と言わない — ブラウザに意味が無い。is_path は**どの引数で来たか**
    (値の形では判定しない)。"""
    return RefErrorHints(
        too_large=lambda ref, err: _too_large(ref, err, is_path=is_path),
        unexplained=f"ERROR: {{ref}} {_ERR_UNEXPLAINED}",
        not_found=(
            "ERROR: {ref} は存在しない (ファイル無し)。run_shell の ls で存在を確認すること。"
            if is_path
            else RefErrorHints.not_found
        ),
        redirect_next=(
            "redirect は辿らないので、ディレクトリではなくファイルやページを直接指す URL を渡すこと "
            "(例 '<ルート>/' ではなく '<ルート>/top.aspx')。" + _HINT_LITERAL
        ),
        # 上流不達は URL (一般の URL) でしか起きないが、万一 fs で来ても「URL を案内」とは言わない。
        unreachable=(
            "ERROR: {ref} の院内 Web に届かない (この配備からは到達できない、または一時的な障害)。"
            "run_shell の ls で存在を確認すること。"
            if is_path
            else RefErrorHints.unreachable
        ),
        workspace_unavailable=(
            "ERROR: {ref} は sandbox のファイルだが読めない (sandbox が起動していないか、ファイルが無い)。"
            "list_workspace で存在を確認すること。"
        ),
    )


def _explain_ref_error(ref: str, err, is_path: bool = False) -> str:
    """resolver の RefError をモデル向けの ERROR 文にする (組み立ては `utils/ref_errors`)。"""
    return explain_ref_error(
        ref, err, arg="path" if is_path else "url", hints=_hints(is_path)
    )


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


def _parse_pages(spec: Optional[str], n_pages: int) -> list[int]:
    """ "1-10" / "3" / "1-3,7" を 0-origin のページ番号リストへ。"""
    if not spec:
        return list(range(n_pages))
    out: list[int] = []
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
        for p in range(max(1, lo), min(n_pages, hi) + 1):
            if (p - 1) not in out:
                out.append(p - 1)
    return out or list(range(n_pages))


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
        """画像・PDF を自分自身の目 (VLM) に入れる。図・レイアウト・印影・手書きなど、
        文字起こしではなく実際に見て判断するためのもの。このターン内に自分の入力へ入るので、
        続けて内容を評価・説明・修正できる。**ユーザーには何も表示されない** — ユーザーに
        見せるなら、見た上で本文に markdown (`![図: …](URL)`) を書く。

        画像はそのまま、PDF は `pages` のページを画像化して自分の目に入れる (`pages` は PDF の
        ときだけ意味を持つ。省略時は先頭 3 ページと総ページ数が返る)。**Files には登録しない** —
        ユーザーの添付には残らない。

        見る対象は url / path / file_id のどれか 1 つ。受け取った形のまま渡す:

        - url: スキーム付きの絶対 URL (`http(s)://…`) か、同一サーバの `/ext/…`・
          `/api/v1/files/{id}/content`。read_doc / fetch_url の戻り値の literal をそのまま (図・原本 PDF・
          院内 Web の資料)。組み立てない・書き換えない・ホストを補わない。
          `#page=N` 付きの literal はそのまま渡してよい (`pages` を付ければそちらが優先)。
          http(s) の URL は院内でもインターネットでもそのまま渡す — どこに届くかは配備の
          ネットワークが決める (断るのはこのサーバ自身を指す URL だけ)。
        - path: sandbox のファイルの絶対パス (`/workspace/…` `/mnt/…`)。
        - file_id: 添付・生成物の id。run_python の戻り値やシステム注記の file_id もここ
          (本文リンク `/api/v1/files/{id}/content` しか手元に無ければ url= にそのまま渡す)。

        3 つとも省略すると、この会話に添付された PDF 全件が対象 (filename で部分一致の絞り込み)。
        直接添付の画像は既に見えているので対象にしない (見直すなら file_id を明示)。

        中身が画像か PDF かはこちらが読んで判定する (URL が拡張子を持たない配信口
        `…/log_write.aspx?…` でも同じ)。

        失敗時は "ERROR: ..." で始まる説明 — 何が起きたか (存在しない / サイズ上限超過 /
        redirect / 上流のエラー / 届かない / このサーバ自身 / sandbox が読めない) と次の
        一手を書く。「読めなかった」を全部リンク切れと言い換えないこと。

        :param url: (任意) スキーム付きの絶対 URL (`http(s)://…`) か、同一サーバの `/ext/…`・`/api/v1/files/{id}/content` (戻り値の literal)。
        :param path: (任意) sandbox のファイルの絶対パス (`/workspace/…` `/mnt/…`)。
        :param file_id: (任意) 見たい画像・PDF の file_id (UUID)。
        :param filename: (任意) 対象ファイル名の部分一致フィルタ。
        :param pages: (任意) PDF のページ範囲。"3" / "4-6" / "1-2,7" 形式。画像には付けない。
        :return: 何を自分の目に入れたかの説明 (ユーザー表示ではない) / 失敗時は "ERROR: ..." で始まる
                 説明 — 何が起きたか (存在しない / サイズ上限超過 / redirect / 上流のエラー /
                 届かない / このサーバ自身 / sandbox が読めない) と次の一手を書く。
                 「読めなかった」を全部リンク切れと言い換えないこと。
        """
        if not __user__ or not __user__.get("id"):
            return "ERROR: ユーザー情報が取得できませんでした。"
        if __feed_image__ is None:
            # 実行文脈外 (通常起こらない)。参照を push できないので明示エラー。
            return "ERROR: 画像提示チャネルが利用できない (実行文脈外)。"

        # --- url / path ソース (#139): 参照文字列を resolver で読んで検証し、参照を積む ---
        # 扱いは引数名だけで決める (module docstring「引数」。値の形で読み替えない)。
        if url is not None or path is not None:
            if file_id or filename:
                return (
                    "ERROR: url / path は file_id/filename と同時指定できない。"
                    "添付・生成物は file_id で、URL は url で、sandbox のファイルは path で、"
                    "どれか 1 つだけを指定すること。"
                )
            u = url.strip() if isinstance(url, str) else ""
            p = path.strip() if isinstance(path, str) else ""
            if u and p:
                return (
                    "ERROR: url と path は同時に指定できない。1 回の呼び出しでどちらか一方だけを渡すこと。"
                    f"{REF_ARGS_RULE}"
                )
            if not (u or p):
                return url_error("") if url is not None else path_error("")
            if u:
                # このサーバのファイル配信口 (相対・絶対とも) は URL のルーティングとして
                # file に当たる — file_id 経路と同じ所有者チェックで読む (module docstring「引数」)。
                # file_id_of_url は fragment 付きでも id を返す。`#page=N` は split_page で別に読む
                fid = file_id_of_url(u)
                if fid:
                    _base, frag_page = split_page(u)
                    if frag_page is not None and not pages:
                        pages = str(frag_page)
                    return await self._feed_file(
                        fid, pages, __user__["id"], __feed_image__
                    )
            return await self._feed_path(
                p or u, pages, __user__["id"], __feed_image__, is_path=bool(p)
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
            return (
                "ERROR: 提示対象が見つからない。無指定で対象になるのは添付 PDF だけ "
                "(添付画像は既に自分の目に入っている。見直すなら file_id を明示)。"
                "URL なら url で、sandbox のファイルなら path で指定すること。"
            )

        out: list[str] = []
        for fid in targets:
            out.append(
                await self._feed_file(fid, pages, __user__["id"], __feed_image__)
            )

        # role:tool の content は非空の status 文字列。画素は results[i].context_images 経由で届く。
        return "\n".join(out)

    async def _feed_file(self, fid: str, pages, user_id: str, feed) -> str:
        """file (添付・生成物) 1 件を所有者チェックの上で積み、報告 1 行を返す。
        file_id 引数と、url の `/api/v1/files/{id}/content` の両方がここを通る。"""
        # 同期 DB / ディスク read はワーカースレッドへ退避してイベントループを塞がない
        # (#28)。to_thread を await するため関数自体は async のまま。
        rec = await asyncio.to_thread(Files.get_file_by_id, fid)  # v0.6.5 は sync
        # 所有者チェック (#40 と同クラスの IDOR 封鎖): file_id 直指定で他ユーザーのファイルを
        # 覗かせない。レコード無しとアクセス権無しは同じ固定文 (存在の有無を漏らさない)。
        if not rec:
            return file_id_error(fid)
        owner = getattr(rec, "user_id", None)
        if owner != user_id:
            return file_id_error(fid)

        meta = getattr(rec, "meta", None) or {}
        name = getattr(rec, "filename", None) or fid
        kind = _kind(name, meta.get("content_type"))
        if not kind:
            return f"- {name}: 画像でも PDF でもない (skip)"

        raw = await asyncio.to_thread(_read_raw, rec)
        if raw is None:
            return f"- {name}: 実体を読めない (Storage 解決失敗)"

        if kind == "image":
            # サイズ検証: 巨大なファイルを提示前に弾く (実体化は送信直前 inline に
            # 寄せるが、上限超過はここで早期に返す)。
            if len(raw) > self.valves.max_image_mb * 1024 * 1024:
                return (
                    f"- {name}: 大きすぎる ({len(raw) / 1024 / 1024:.1f}MB > "
                    f"{self.valves.max_image_mb}MB)。run_python でリサイズしてから渡すこと"
                )
            # 汎用 tool-push: 実行中 tool-call の sink に file_id を積む。実体化は
            # 送信直前 inline に一任。base64 は戻り境界に載せない。
            feed(fid)
            return (
                f"- {name} を自分の目に入れた (file_id={fid}。ユーザーには表示されない)"
            )
        # PDF は File を作らず、内部 file URL にページを載せた参照で積む
        # (inline 層が送信直前にそのページをラスタライズする)。
        try:
            n_pages = await asyncio.to_thread(pdf_page_count, raw)
        except Exception as e:
            log.error(f"[feed_image] {name}: {e!r}")
            return f"- {name}: PDF を開けない ({e})"
        return self._feed_pdf_pages(
            f"/api/v1/files/{fid}/content", name, n_pages, pages, feed
        )

    async def _feed_path(
        self, value: str, pages, user_id: str, feed, *, is_path: bool
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
            loaded, err = await resolve_path(ref, user_id)
        else:
            loaded, err = await resolve_url(ref)
        if err is not None or loaded is None:
            return _explain_ref_error(ref, err, is_path)

        kind = detect_kind(loaded)
        if kind == "image":
            if pages:
                return (
                    "ERROR: pages は PDF のときだけ指定できる (この参照は画像)。"
                    "pages を外して呼び直すこと。"
                )
            size = len(loaded.data)
            if size > self.valves.max_image_mb * 1024 * 1024:
                # resolver の上限 (既定 25 MB) 以下・画像の上限 (既定 8 MB) 超の帯。縮小の手順は
                # 参照の形で違う: fs は run_python が直接開ける、URL は先に取り込む。
                how = (
                    "run_python でこのパスを開いて縮小し、/workspace/… に書いた path を path= で渡すこと。"
                    if is_path
                    else "send_to_workspace(url=…) で sandbox に取り込み、run_python で縮小して "
                    "/workspace/… の path を path= で渡すこと。"
                )
                return (
                    f"ERROR: {ref} は大きすぎる ({size / 1024 / 1024:.1f}MB > "
                    f"{self.valves.max_image_mb}MB)。{how}"
                )
            feed(ref)
            if is_path:
                # sandbox のパスはブラウザに意味が無い — 本文に貼らせない
                return (
                    f"- {ref} を自分の目に入れた (ユーザーには表示されない。sandbox のパスは本文に"
                    f"貼れない — {_SHOW_PATH})"
                )
            return f"- {ref} を自分の目に入れた (ユーザーには表示されない。見せるなら本文に ![…]({ref}) を書く)"
        if kind == "pdf":
            try:
                n_pages = await asyncio.to_thread(pdf_page_count, loaded.data)
            except Exception as e:
                log.error(f"[feed_image] {ref}: {e!r}")
                return f"ERROR: PDF を開けない ({e})。"
            return self._feed_pdf_pages(ref, ref, n_pages, pages, feed)
        return f"ERROR: 画像でも PDF でもない (content-type={loaded.content_type or '不明'})。" + (
            "テキストなら run_shell で読むこと。"
            if is_path
            else "テキストなら fetch_url(url=) で読むこと。"
        )

    def _feed_pdf_pages(self, ref: str, label: str, n_pages: int, pages, feed) -> str:
        """PDF の指定ページを `ref#page=N` の参照で順に push する (File は作らない)。"""
        wanted = _parse_pages(pages, n_pages)
        limit = self.valves.max_pages
        truncated = len(wanted) > limit > 0
        if truncated:
            wanted = wanted[:limit]
        for i in wanted:
            feed(with_page(ref, i + 1))

        msg = f"- {label} の p.{', p.'.join(str(i + 1) for i in wanted)} を自分の目に入れた (全 {n_pages} ページ。ユーザーには表示されない)"
        if truncated:
            msg += (
                f"\n  上限 {limit} ページで打ち切った。他のページが要るなら pages で"
                f"範囲を指定して呼び直すこと"
            )
        return msg
