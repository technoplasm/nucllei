"""添付 files の重複除去は file id 基準(監査 2026-10-05、refs #142)。

同じ id でメタデータ(status / itemId 等)だけ違う entry が 2 件来ると、旧実装の
JSON 全体一致(middleware)・判定なし(get_sources_from_files)では両方残り、
本文が system に 2 回入った(2,031 → 3,529 字)。
"""

import inspect
import types

from nucllei.retrieval import utils as retrieval_utils
from nucllei.retrieval.utils import dedupe_files_by_id, get_sources_from_files

ENTRY = {
    "type": "file",
    "id": "doc1",
    "name": "doc.txt",
    "status": "uploaded",
    "file": {"id": "doc1", "filename": "doc.txt"},
}


def test_same_id_with_different_metadata_is_one():
    other = {**ENTRY, "itemId": "re-attached", "status": "processed"}
    assert dedupe_files_by_id([ENTRY, other]) == [ENTRY]


def test_nested_file_id_counts_as_id():
    a = {"type": "file", "file": {"id": "doc2"}, "name": "a"}
    b = {"type": "file", "file": {"id": "doc2"}, "name": "b"}
    assert dedupe_files_by_id([a, b]) == [a]


def test_entries_without_id_fall_back_to_json_equality():
    a = {"type": "text", "content": "x"}
    b = {"type": "text", "content": "y"}
    assert dedupe_files_by_id([a, dict(a), b]) == [a, b]


def test_order_is_kept_and_distinct_ids_survive():
    a = {**ENTRY, "id": "a"}
    b = {**ENTRY, "id": "b"}
    assert dedupe_files_by_id([b, a, {**b, "status": "x"}]) == [b, a]


def test_sources_inject_one_body_per_file_id(monkeypatch):
    from nucllei.utils.middleware import build_source_context

    body = "【FILE-BODY】" + "文" * 50
    monkeypatch.setattr(
        retrieval_utils.Files,
        "get_file_by_id",
        lambda file_id: types.SimpleNamespace(
            data={"content": body, "status": "success"},
            filename="doc.txt",
            meta={},
        ),
    )
    other = {**ENTRY, "itemId": "re-attached"}
    sources = get_sources_from_files(request=None, files=[ENTRY, other])
    assert len(sources) == 1
    assert build_source_context(sources).count("【FILE-BODY】") == 1


def test_process_chat_payload_dedupes_by_id():
    # process_chat_payload の重複除去が id 基準のヘルパを使う(JSON 全体一致に戻さない)
    from nucllei.utils import middleware

    source = inspect.getsource(middleware.process_chat_payload)
    assert "dedupe_files_by_id(files)" in source
    assert "json.dumps(f, sort_keys=True)" not in source
