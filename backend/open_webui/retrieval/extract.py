"""種別ごとのファイル取り込み内部API(#52 プランB)。

入口を種別で分ける(文書抽出 / STT / TTS …)。単一 `extract()` に一本化しない
— 種別で入出力も追加オプションも違うため、無理に共通シグネチャへ揃えない
(`_memos/file-handling.md` プランB節)。各関数は「設定で登録したエンジン」に対応し、
upload フック / inlet filter から呼べるほか、builtin tool(#18)としてモデルにも
露出させる薄い土台。呼び分け(content_type / 拡張子)はフック側がルータとして担う。

ここは **文書抽出**(ファイル → 文字列)。STT(音声 → 文字列)/ TTS(テキスト → 音声)は
既存 audio 経路の薄いラップとして別途足す。
"""

import logging

from fastapi import Request

from open_webui.models.files import FileModel, Files
from open_webui.storage.provider import Storage
from open_webui.retrieval.loaders.main import Loader
from open_webui.utils.misc import calculate_sha256_string

log = logging.getLogger(__name__)


def extract_file_text(
    request: Request,
    file: FileModel,
    *,
    force: bool = False,
) -> str:
    """文書ファイル → 本文テキスト(冪等・キャッシュ付き)。

    - `file.data['content']` が既にあり `force=False` ならそれを返し **再抽出しない**
      = 毎ターンの再 docling を防ぐ主キー(冪等・キャッシュ)。
    - 無ければ登録エンジン(`CONTENT_EXTRACTION_ENGINE`)の `Loader` で抽出し、
      `data.content` と hash を保存して返す。
    - `path` を持たない file(クライアント供給テキスト等)は既存 content をそのまま返す。

    OCR してるか等は呼ぶ側に不透明(ファイルを渡すと読んだ文字列が返る)。engine 選択と
    その付随オプションは設定(`request.app.state.config`)に対応する。
    """
    data = file.data or {}
    if not force:
        cached = data.get("content")
        if cached:
            return cached

    file_path = file.path
    if not file_path:
        return data.get("content", "")

    file_path = Storage.get_file(file_path)
    loader = Loader(
        engine=request.app.state.config.CONTENT_EXTRACTION_ENGINE,
        TIKA_SERVER_URL=request.app.state.config.TIKA_SERVER_URL,
        DOCLING_SERVER_URL=request.app.state.config.DOCLING_SERVER_URL,
        PDF_EXTRACT_IMAGES=request.app.state.config.PDF_EXTRACT_IMAGES,
        DOCUMENT_INTELLIGENCE_ENDPOINT=request.app.state.config.DOCUMENT_INTELLIGENCE_ENDPOINT,
        DOCUMENT_INTELLIGENCE_KEY=request.app.state.config.DOCUMENT_INTELLIGENCE_KEY,
        MISTRAL_OCR_API_KEY=request.app.state.config.MISTRAL_OCR_API_KEY,
    )
    docs = loader.load(file.filename, file.meta.get("content_type"), file_path)
    text_content = " ".join([doc.page_content for doc in docs])

    Files.update_file_data_by_id(file.id, {"content": text_content})
    Files.update_file_hash_by_id(file.id, calculate_sha256_string(text_content))
    log.debug(f"extract_file_text: file_id={file.id} len={len(text_content)}")
    return text_content
