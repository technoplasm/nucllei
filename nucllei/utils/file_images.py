"""内部画像参照の base64 インライン化(#69 画像の files 化 / #97 / #139)。

画像添付は base64 直入れをやめ files テーブル経由に統一した。チャット payload の
image_url には "/api/v1/files/{id}/content" 形式の内部 URL が入るが、モデル
(推論エンジン)側からこの URL へ到達・認証できる保証がない — モデルへ出る直前
(process_chat_payload 末尾)に、要求ユーザーが読める file だけを Storage から
読み出して data URL に変換する。

それ以外の **参照文字列**(URL = `https://…/ext/{name}/...`・同一サーバの相対
`/ext/{name}/...`(sandbox のパスと同じ文字列)・一般の URL、および sandbox の fs パス
"/workspace/..."、任意で "#page=N")も同じ層で解決する — feed_image が参照文字列を sink に積み、
context_images に文字列のまま永続し、送信直前のここが実体化する(File は発行
しない。context_images は次ターン以降の履歴にも再送される(#142)ので、読み出しは
そのチャットが続くかぎり毎ターンの送信ごとに走る。解決できなくなった参照は下記の
とおり "(image unavailable)" に落ちる)。「参照 → バイト」の規則は `utils/image_refs.py` 一箇所(tool 側と
共有。image_url の url は引数名を持たない保存済み文字列なので、先頭で振り分ける
`resolve_ref` を通す — 表は image_refs の module docstring)で、ここは種別で分岐するだけ: 画像はそのまま data URL、PDF は指定ページ
(既定 1)を PNG に画像化して data URL、SVG / TIFF / AVIF は PNG に変換して data URL(上流の VLM は
png / jpeg / webp / gif / bmp しか受けない — 載せる形式の保証はここ 1 箇所、`_materialize`)。per-user のアクセス検査は file_id 経路だけ —
URL / fs 参照の公開範囲は配備が `EXT_MOUNTS` に書いた物(= `/ext/` の配信元)/
呼び出しユーザーの workspace が境界で、解決層が fail closed で持つ。

変換できない参照(アクセス不可・削除済み・storage 欠落・root 外・非画像・サイズ超過・
不達・どの名前空間にも当たらない絶対 URL)は、URL のまま残すとモデル側がリクエストごと
落とし該当チャットが恒久的に失敗するため、text パーツ "(image unavailable)" に置換する。
全経路とも同一の置換にすることで、payload からアクセス不可と不存在を区別できない
(存在有無をモデル経由で漏らさない)。読めたが画像に変換できなかったもの(壊れた SVG・描画の
打ち切り・上記以外の image/*)だけは "(<名前>: 画像に変換できなかった (形式 …))" の注記にする
(読めた後なので存在は漏れない — 何が渡らなかったかをモデルが言える)。文字列 content のメッセージと、
VLM に渡せる形式の data URL は触らない(それ以外の形式の data URL は中身を読んで同じ出口を通す)。それ以外の形
("//host/..." プロトコル相対、http(s) 以外のスキーム、相対パス、"/ext/" でも sandbox でも
ない "/" 始まり)も unavailable に倒す —
headless API(`/api/chat/completions`)の image_url に外部 URL を渡した場合も同じ。

読み出し + base64 エンコード + PDF 画像化は blocking なので thread にオフロードする
(attachment dispatch と同じ方針 — event loop を塞がない)。
"""

import asyncio
import base64
import binascii
import hashlib
import logging
import mimetypes
from urllib.parse import unquote_to_bytes

from nucllei.models.files import Files
from nucllei.storage.provider import Storage
from nucllei.utils.access_control import has_read_access_to_file
from nucllei.utils.image_refs import (
    CONVERTED_IMAGE_TYPES,
    INTERNAL_FILE_URL_PATTERN as INTERNAL_FILE_URL_PATTERN,  # utils/skills.py が引く
    VLM_IMAGE_TYPES,
    LoadedRef,
    base_media_type,
    convert_image_to_png,
    detect_media_type,
    file_id_of_url,
    rasterize_pdf_page,
    resolve_ref,
    sniff_vlm_image_type,
    split_page,
)
from nucllei.env import SRC_LOG_LEVELS

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])

UNAVAILABLE_IMAGE_TEXT = "(image unavailable)"


class _Note(str):
    """image_url パーツの代わりに置く text(画像として送れなかった注記)。

    `_materialize` の戻り値は data URL か、これか、None("(image unavailable)")。ターン内
    キャッシュに同じ dict で載せるので、data URL と型で見分ける。"""


def _unconvertible_note(name: str, media_type: str) -> _Note:
    return _Note(f"({name}: 画像に変換できなかった (形式 {media_type}))")


def _to_data_url(content_type: str, raw: bytes) -> str:
    return f"data:{content_type};base64,{base64.b64encode(raw).decode('ascii')}"


def _data_url_media_type(url: str) -> str:
    return base_media_type(url[5:].split(",", 1)[0].split(";", 1)[0])


def _decode_data_url(url: str) -> LoadedRef | None:
    """data URL の中身を読む(blocking — thread で呼ぶ)。読めなければ None。"""
    header, sep, payload = url[5:].partition(",")
    if not sep:
        return None
    try:
        if ";base64" in header.lower():
            raw = base64.b64decode(payload, validate=False)
        else:
            raw = unquote_to_bytes(payload)
    except (ValueError, binascii.Error):
        return None
    return LoadedRef(data=raw, content_type=_data_url_media_type(url), name="")


def _load_file(file_id: str, user) -> LoadedRef | None:
    """file_id の実体を読む(blocking — thread で呼ぶ)。読めない・権限が無ければ None。"""
    if not has_read_access_to_file(file_id, user):
        log.warning(
            "file_images: skipping unreadable or missing file reference "
            f"(user_id={user.id}, file_id={file_id})"
        )
        return None

    file = Files.get_file_by_id(file_id)
    if not file or not file.path:
        log.warning(f"file_images: file has no stored content (file_id={file_id})")
        return None

    content_type = (file.meta or {}).get("content_type")
    if not content_type:
        content_type = mimetypes.guess_type(file.filename or "")[0]
    if not content_type:
        content_type = "application/octet-stream"

    try:
        with open(Storage.get_file(file.path), "rb") as f:
            raw = f.read()
    except Exception:
        log.exception(f"file_images: storage read failed (file_id={file_id})")
        return None

    return LoadedRef(data=raw, content_type=content_type, name=file.filename or "")


async def _materialize(
    loaded: LoadedRef, page: int | None, label: str, name: str
) -> str | None:
    """読めたバイト列を data URL にする。VLM に渡せる形式 (`VLM_IMAGE_TYPES`) の画像は
    そのまま、PDF は指定ページ(既定 1)を PNG 化、SVG / TIFF / AVIF (`CONVERTED_IMAGE_TYPES`)
    は PNG に変換。変換できなかったもの・それ以外の画像は注記 (`_Note`) を返す。画像でも PDF
    でもなければ None。

    file_id 経路と "/" 参照経路の共通の出口 — 添付 PDF も feed_image が
    "/api/v1/files/{id}/content#page=N" で積む(File を作らない、#139)ので、
    ページ画像化は経路によらずここ 1 箇所。上流が受けない形式を image_url に載せない保証も
    ここ 1 箇所に置く: チャットの添付、feed_image の context_images、DB から送り直される履歴の
    画像はどれもここを通る。image_url に載せる型は実バイト (magic) で決める — 申告が SVG で
    中身が PNG なら PNG として送り、申告が PNG で中身が違えば画像として送らない。
    """
    media_type = detect_media_type(loaded)
    if media_type == "application/pdf":
        try:
            png = await asyncio.to_thread(rasterize_pdf_page, loaded.data, page or 1)
        except Exception:
            log.exception(f"file_images: pdf rasterize failed ({label}, page={page})")
            return None
        return _to_data_url("image/png", png)
    vlm_type = sniff_vlm_image_type(loaded.data)
    if vlm_type:
        return _to_data_url(vlm_type, loaded.data)
    if media_type is None:
        log.warning(
            f"file_images: reference is neither image nor pdf ({label}, "
            f"content_type={loaded.content_type!r})"
        )
        return None
    if media_type in CONVERTED_IMAGE_TYPES:
        try:
            png = await convert_image_to_png(loaded.data, media_type)
        except Exception as e:
            log.warning(
                f"file_images: image conversion failed ({label}, "
                f"media_type={media_type}): {e!r}"
            )
        else:
            return _to_data_url("image/png", png)
    else:
        log.warning(
            f"file_images: image type not accepted by the model ({label}, "
            f"media_type={media_type})"
        )
    return _unconvertible_note(name, media_type)


async def inline_internal_image_urls(
    messages: list, user, cache: dict | None = None
) -> list:
    """messages(OpenAI 形式)内の内部参照の image_url パーツを data URL に置換する。

    messages を破壊的に書き換えて同じ list を返す。変換できない内部 file URL /
    絶対 URL / "/" 始まり参照パーツ(およびどの名前空間でもない URL)は text
    "(image unavailable)" に置換(冒頭 docstring 参照)。VLM に渡せる形式の data URL は無変更。

    cache: 呼び出しをまたいで data URL(変換不能の None も)を使い回す dict。tool
    ループの追撃は毎周 content_blocks から messages を作り直すので、渡さないと同じ
    tool 画像を追撃のたびに読み直して base64 化する。呼び手(1 ターン = 1 user)が
    スコープを持つ — ユーザーをまたいで共有しないこと(ACL はロード時に掛かる)。
    使い回すのは中身が変わらない参照だけ: file id(不変)と http(s) の URL(ターン内は
    同じ物とみなす)。sandbox のパス(`/workspace/…`)と同一サーバの `/ext/…` は同じ
    ターンの中でも上書きされうる(描き直して同じ path で feed_image し直す)ので、
    呼び出しごとに読み直し、変換結果だけを中身のハッシュで使い回す — 参照文字列で
    使い回すと古い画像が届いていた(llens 2026-10-19)。
    """
    # 同一呼び出し内で同じ file / 参照を複数回参照しても読み出しは 1 回(結果 None も
    # キャッシュ)。バイト列は参照単位、data URL はページ単位 — PDF の複数ページは
    # 同じ 1 本を読んでページだけ変える(上流 http の PDF をページ数ぶん取りに行かない)。
    # バイト列は呼び出し内だけ持つ(data URL が揃えば要らない)。
    loaded_cache: dict[str, LoadedRef | None] = {}
    data_cache: dict[str, str | None] = cache if cache is not None else {}

    async def materialize(key: str, loader, page: int | None, label: str, name=None):
        page_key = f"{key}#{page}"
        if page_key in data_cache:
            return data_cache[page_key]
        if key not in loaded_cache:
            loaded_cache[key] = await loader()
        loaded = loaded_cache[key]
        data_cache[page_key] = (
            None
            if loaded is None
            else await _materialize(loaded, page, label, name or loaded.name or label)
        )
        return data_cache[page_key]

    async def load(file_id: str, page: int | None) -> str | None:
        return await materialize(
            f"file:{file_id}",
            lambda: asyncio.to_thread(_load_file, file_id, user),
            page,
            f"file_id={file_id}",
        )

    async def resolve(ref: str) -> LoadedRef | None:
        # 置換の文面は理由を問わず同じ("(image unavailable)" — 存在有無を漏らさない)
        # だが、ログには code を残す。「リンク切れ」と「サイズ超過」を運用で見分ける
        # ためで、image_refs 側の warning と合わせて読む
        loaded, error = await resolve_ref(ref, user)
        if error is not None:
            log.warning(
                f"file_images: reference unavailable (ref={ref[:120]!r}, "
                f"code={error.code}, detail={error.detail}"
                + (
                    f", size{'=' if error.size_exact else '>='}{error.size}"
                    if error.size is not None
                    else ""
                )
                + (f", limit={error.limit}" if error.limit is not None else "")
                + (f", status={error.status}" if error.status is not None else "")
                + ")"
            )
        return loaded

    async def load_data_url(url: str) -> str | None:
        return await materialize(
            f"data:{hashlib.sha256(url.encode()).hexdigest()}",
            lambda: asyncio.to_thread(_decode_data_url, url),
            None,
            "data URL",
        )

    async def load_reference(ref: str, page: int | None) -> str | None:
        # 注記に出す名前は参照そのもの(モデルが知っている形 — 次の手に使える)
        if ref.startswith(("http://", "https://")):
            return await materialize(
                f"ref:{ref}", lambda: resolve(ref), page, f"ref={ref}", name=ref
            )
        # 上書きされうる参照(sandbox のパス・/ext/): 読むのは毎回(呼び出し内は 1 回)、
        # data URL は参照 + 中身のハッシュで引く — 同じ中身なら変換し直さない(参照も
        # キーに入れるのは、変換できないときの注記に出す名前が参照ごとに違うため)
        read_key = f"read:{ref}"
        if read_key not in loaded_cache:
            loaded_cache[read_key] = await resolve(ref)
        loaded = loaded_cache[read_key]
        if loaded is None:
            return None
        digest = hashlib.sha256(loaded.data).hexdigest()
        return await materialize(
            f"content:{ref}:{digest}",
            lambda: asyncio.sleep(0, loaded),
            page,
            f"ref={ref}",
            name=ref,
        )

    for message in messages or []:
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for part in content:
            if not (isinstance(part, dict) and part.get("type") == "image_url"):
                continue

            image_url = part.get("image_url")
            # OpenAI 形式は {"url": ...} だが、素の文字列も来うるので両対応
            url = image_url.get("url") if isinstance(image_url, dict) else image_url
            if not isinstance(url, str):
                continue

            # "#page=N" は file URL にも参照にも付く(PDF のページ指定)
            ref, page = split_page(url)
            file_id = file_id_of_url(ref)
            if file_id:
                data_url = await load(file_id, page)
            elif url.startswith("data:"):
                # 既に実体。VLM に渡せる形式ならそのまま送る(触らない)。それ以外
                # (headless API の data:image/svg+xml 等)は中身を読んで同じ出口へ
                if _data_url_media_type(url) in VLM_IMAGE_TYPES:
                    continue
                data_url = await load_data_url(url)
            else:
                # 残りは全部 resolver(image_refs)へ — URL(http(s):// / 同一サーバの
                # "/ext/")と sandbox の fs パス("/workspace/")だけが解決され、
                # それ以外("//host/..." プロトコル相対、ftp: / file: 等の他スキーム、
                # 相対パス)は None → "(image unavailable)"(fail closed。
                # URL のまま残すと推論エンジンがリクエストごと落とす)
                data_url = await load_reference(ref, page)
            if data_url is None or isinstance(data_url, _Note):
                text = UNAVAILABLE_IMAGE_TEXT if data_url is None else str(data_url)
                part.clear()
                part["type"] = "text"
                part["text"] = text
                continue

            if isinstance(image_url, dict):
                image_url["url"] = data_url
            else:
                part["image_url"] = data_url
    return messages
