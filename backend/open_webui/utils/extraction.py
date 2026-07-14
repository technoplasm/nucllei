"""既定(非 docling)テキスト抽出の内部関数 API(#52 / v0.6.5 Default 経路の復元)。

fork 本体は builtin Attachment を持たない(#52)。代わりに、docling ではない「素の」
抽出経路を **内部ヘルパ**として提供し、prism の Attachment Function がその `process(file)`
内から呼べるようにする(機構は fork・ポリシーは prism。`utils/audio.py` と同型)。

方針:
- 2 段の API — storage 非依存の純関数 `extract_text(filename, file_path, content_type)` と、
  FileModel から `Storage` 経由でローカルパスを解決する薄いラッパ `extract_text_from_file(file)`。
- 拡張子/mime を `langchain_community` の既定ローダへ振り分け、各ページを `ftfy` で正規化して結合。
- **whitelist 方式**: 対応拡張子/mime は `SUPPORTED_EXTENSIONS` / `SUPPORTED_CONTENT_TYPES` で
  公開する。未対応を渡すと `UnsupportedFileError` を送出(呼び出し側は事前に whitelist を見て
  skip=None、あるいはそのまま failed を選べる)。upstream の「未知も TextLoader で読む」寛容
  フォールバックは採らない — バイナリ文字化けを content に残さないため。
- docling / tika / document_intelligence / mistral_ocr は **含めない**(prism の担当)。
- `.msg`(OutlookMessageLoader)は **除外** — extract_msg の GPL 依存連鎖(#2 ライセンス監査)。
- OCR(スキャン PDF / 画像)は **含めない** — テキスト PDF のみ(`PyPDFLoader`、画像抽出なし)。
- **sync**(ブロッキング)。attachment ディスパッチャ(`utils/attachment.py`)が sync ハンドラを
  threadpool にオフロードするため、prism の `process` は sync のまま呼べる。
"""

import logging
from typing import Optional

import ftfy
from langchain_community.document_loaders import (
    BSHTMLLoader,
    CSVLoader,
    Docx2txtLoader,
    PyPDFLoader,
    TextLoader,
    UnstructuredEPubLoader,
    UnstructuredExcelLoader,
    UnstructuredPowerPointLoader,
    UnstructuredRSTLoader,
    UnstructuredXMLLoader,
)

from open_webui.env import SRC_LOG_LEVELS

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])


class UnsupportedFileError(Exception):
    """既定抽出器が扱えない拡張子/mime を渡されたとき送出。

    呼び出し側(prism の Attachment Function)は、事前に `SUPPORTED_EXTENSIONS` /
    `SUPPORTED_CONTENT_TYPES` を見て None(skip)を返すか、送出させて failed にするかを選べる。
    """


# テキストとして読めるソースコード/設定ファイル拡張子(v0.6.5 Default から復元・`.msg` は除外)。
KNOWN_SOURCE_EXT = frozenset(
    {
        "go",
        "py",
        "java",
        "sh",
        "bat",
        "ps1",
        "cmd",
        "js",
        "ts",
        "css",
        "cpp",
        "hpp",
        "h",
        "c",
        "cs",
        "sql",
        "log",
        "ini",
        "pl",
        "pm",
        "r",
        "dart",
        "dockerfile",
        "env",
        "php",
        "hs",
        "hsc",
        "lua",
        "nginxconf",
        "conf",
        "m",
        "mm",
        "plsql",
        "perl",
        "rb",
        "rs",
        "db2",
        "scala",
        "bash",
        "swift",
        "vue",
        "svelte",
        "ex",
        "exs",
        "erl",
        "lhs",
        "tsx",
        "jsx",
        "json",
    }
)

# 構造化ドキュメントの専用ローダを持つ拡張子。
_STRUCTURED_EXT = frozenset(
    {
        "pdf",
        "csv",
        "rst",
        "xml",
        "htm",
        "html",
        "md",
        "epub",
        "docx",
        "xls",
        "xlsx",
        "ppt",
        "pptx",
    }
)

# 明示的にサポートする拡張子(公開・whitelist)。
SUPPORTED_EXTENSIONS = _STRUCTURED_EXT | KNOWN_SOURCE_EXT

# 拡張子が無い/曖昧なとき content_type で拾う office/epub mime(公開・whitelist)。
SUPPORTED_CONTENT_TYPES = frozenset(
    {
        "application/epub+zip",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }
)

_WORD_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_EXCEL_MIME = {
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
_PPT_MIME = {
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def _file_ext(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _is_text(file_ext: str, content_type: Optional[str]) -> bool:
    return file_ext in KNOWN_SOURCE_EXT or bool(
        content_type and content_type.startswith("text/")
    )


def is_supported(filename: str, content_type: Optional[str] = None) -> bool:
    """`extract_text` が扱える拡張子/mime かどうか(呼び出し側の skip 判定用)。"""
    file_ext = _file_ext(filename)
    return (
        file_ext in SUPPORTED_EXTENSIONS
        or (content_type in SUPPORTED_CONTENT_TYPES)
        or _is_text(file_ext, content_type)
    )


def _get_loader(filename: str, file_path: str, content_type: Optional[str]):
    file_ext = _file_ext(filename)

    if file_ext == "pdf":
        # OCR は含めない(テキスト PDF のみ・画像抽出なし)。
        return PyPDFLoader(file_path)
    if file_ext == "csv":
        return CSVLoader(file_path, autodetect_encoding=True)
    if file_ext == "rst":
        return UnstructuredRSTLoader(file_path, mode="elements")
    if file_ext == "xml":
        return UnstructuredXMLLoader(file_path)
    if file_ext in ("htm", "html"):
        return BSHTMLLoader(file_path, open_encoding="unicode_escape")
    if file_ext == "md":
        return TextLoader(file_path, autodetect_encoding=True)
    if file_ext == "epub" or content_type == "application/epub+zip":
        return UnstructuredEPubLoader(file_path)
    if file_ext == "docx" or content_type == _WORD_MIME:
        return Docx2txtLoader(file_path)
    if file_ext in ("xls", "xlsx") or content_type in _EXCEL_MIME:
        return UnstructuredExcelLoader(file_path)
    if file_ext in ("ppt", "pptx") or content_type in _PPT_MIME:
        return UnstructuredPowerPointLoader(file_path)
    if _is_text(file_ext, content_type):
        return TextLoader(file_path, autodetect_encoding=True)

    raise UnsupportedFileError(
        f"unsupported file for default extraction: {filename!r} (content_type={content_type!r})"
    )


def extract_text(
    filename: str, file_path: str, content_type: Optional[str] = None
) -> str:
    """`file_path` のファイルを既定ローダで抽出し、全文テキストを返す(storage 非依存)。

    - `filename` は拡張子判定に使う(実パスと別名でよい)。`content_type` は補助。
    - 各ページ(Document.page_content)を `ftfy.fix_text` で正規化し `\\n\\n` で結合。
    - 未対応の拡張子/mime は `UnsupportedFileError`。読取/パース失敗はローダの例外を素通し。
    """
    loader = _get_loader(filename, file_path, content_type)
    docs = loader.load()
    return "\n\n".join(ftfy.fix_text(doc.page_content) for doc in docs)


def extract_text_from_file(file) -> str:
    """FileModel から `Storage` 経由でローカルパスを解決して `extract_text` を呼ぶ薄いラッパ。

    prism の Attachment Function が `process(file)` 内から 1 行で呼ぶための入口。
    `file.path` 未設定(実体なし)や未対応型は `UnsupportedFileError`。
    """
    from open_webui.storage.provider import Storage

    if not file.path:
        raise UnsupportedFileError(
            f"file {getattr(file, 'id', '?')} has no stored path"
        )

    local_path = Storage.get_file(file.path)
    content_type = (file.meta or {}).get("content_type")
    return extract_text(file.filename, local_path, content_type)
