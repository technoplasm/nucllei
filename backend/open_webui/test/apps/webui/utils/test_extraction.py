"""既定(非 docling)テキスト抽出 内部 API(utils/extraction.py、#52)の回帰。

fork は builtin Attachment を持たないが、docling ではない素の抽出経路を内部ヘルパとして
提供する。prism の Attachment Function が `process(file)` 内から呼ぶ想定。純関数なので DB 不要。

契約:
- `extract_text(filename, file_path, content_type)` = storage 非依存の純関数。
- whitelist 方式 — 未対応の拡張子/mime は `UnsupportedFileError`(寛容フォールバックしない)。
- `.msg`(GPL の extract_msg)・OCR・docling/tika 系は含めない。
- `extract_text_from_file(file)` = FileModel から Storage 経由でパス解決する薄いラッパ。
"""

from types import SimpleNamespace

import pytest

from open_webui.utils.extraction import (
    UnsupportedFileError,
    extract_text,
    extract_text_from_file,
    is_supported,
)


def _write(tmp_path, name: str, content: str) -> str:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return str(p)


def test_plain_text(tmp_path):
    path = _write(tmp_path, "note.txt", "hello llens")
    assert "hello llens" in extract_text("note.txt", path, "text/plain")


def test_known_source_ext(tmp_path):
    path = _write(tmp_path, "s.py", "def f():\n    return 42\n")
    out = extract_text("s.py", path)
    assert "return 42" in out


def test_markdown(tmp_path):
    path = _write(tmp_path, "d.md", "# Title\n\nbody text")
    assert "body text" in extract_text("d.md", path)


def test_csv(tmp_path):
    path = _write(tmp_path, "t.csv", "a,b\n1,2\n")
    out = extract_text("t.csv", path)
    assert "a" in out and "1" in out


def test_unsupported_binary_raises(tmp_path):
    path = _write(tmp_path, "blob.bin", "\x00\x01rubbish")
    with pytest.raises(UnsupportedFileError):
        extract_text("blob.bin", path, "application/octet-stream")


def test_text_mime_without_known_ext_is_supported(tmp_path):
    # 拡張子は未知でも content_type が text/* なら扱う
    path = _write(tmp_path, "weird.qqq", "plain content")
    assert "plain content" in extract_text("weird.qqq", path, "text/plain")


def test_is_supported():
    assert is_supported("a.pdf")
    assert is_supported("a.py")
    assert is_supported("a.docx")
    assert is_supported("noext", "text/markdown")
    assert is_supported("book.epub")
    # 未対応バイナリ
    assert not is_supported("x.bin", "application/octet-stream")
    # .msg は除外(GPL、#2)
    assert not is_supported("mail.msg")


def test_msg_is_excluded(tmp_path):
    path = _write(tmp_path, "mail.msg", "whatever")
    with pytest.raises(UnsupportedFileError):
        extract_text("mail.msg", path, "application/vnd.ms-outlook")


def test_extract_text_from_file_wrapper(tmp_path, monkeypatch):
    path = _write(tmp_path, "stored_blob", "wrapper content")
    # ラッパは関数内で `from open_webui.storage.provider import Storage` するので、
    # モジュール属性 Storage を差し替えれば get_file(file.path)->ローカルパスを注入できる。
    import open_webui.storage.provider as provider

    monkeypatch.setattr(provider, "Storage", SimpleNamespace(get_file=lambda p: path))

    file = SimpleNamespace(
        id="f1",
        filename="report.txt",
        path="uploads/report.txt",
        meta={"content_type": "text/plain"},
    )
    assert "wrapper content" in extract_text_from_file(file)


def test_extract_text_from_file_no_path_raises():
    file = SimpleNamespace(id="f2", filename="x.txt", path=None, meta={})
    with pytest.raises(UnsupportedFileError):
        extract_text_from_file(file)
