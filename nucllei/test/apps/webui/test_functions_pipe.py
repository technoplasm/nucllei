"""pipe(Function type=pipe)の SSE 出力(functions.process_line)。

この経路は upstream から引き継いだままテストが無かった(#99 の B で
SSE 直列化を触った際に判明)。Pipe は feature-triage で「残置」= セレクタに
出る自作モデル / モデルルーティングの受け皿として意図的に保持している資産
なので、緩い入力契約(BaseModel / dict / bytes / str / data: 前置済み str)を
ここで固定する。
"""

import json

import pytest
from pydantic import BaseModel

from nucllei.functions import process_line

FORM = {"model": "llens-assistant"}


class _Chunk(BaseModel):
    content: str


def _payload(line: str) -> dict:
    """SSE 1 行から JSON 本体を取り出す。"""
    assert line.startswith("data: ")
    assert line.endswith("\n\n")
    return json.loads(line[len("data: ") : -2])


# --- 型ごとの落とし方 -------------------------------------------------------


def test_basemodel_is_passed_through_without_double_serialization():
    """pydantic の自前 JSON を再度 json.dumps しない(文字列の二重引用を作らない)。"""
    line = process_line(FORM, _Chunk(content="曝露時の対応"))
    assert _payload(line) == {"content": "曝露時の対応"}


def test_dict_is_serialized_as_json():
    line = process_line(FORM, {"choices": [{"delta": {"content": "針刺し"}}]})
    assert _payload(line) == {"choices": [{"delta": {"content": "針刺し"}}]}


def test_bytes_are_decoded_as_utf8():
    line = process_line(FORM, "針刺し・切創".encode("utf-8"))
    assert _payload(line)["choices"][0]["delta"]["content"] == "針刺し・切創"


def test_already_framed_line_is_passed_through():
    """既に ``data:`` 前置済みの str は封筒に入れ直さず終端だけ足す。"""
    assert process_line(FORM, 'data: {"a": 1}') == 'data: {"a": 1}\n\n'


def test_bare_string_is_wrapped_in_chunk_envelope():
    line = process_line(FORM, "曝露時の対応")
    payload = _payload(line)
    assert payload["object"] == "chat.completion.chunk"
    assert payload["model"] == "llens-assistant"
    assert payload["choices"][0]["delta"]["content"] == "曝露時の対応"


def test_empty_string_becomes_finish_chunk():
    """空文字はチャンク封筒の規約どおり finish_reason=stop に落ちる。"""
    payload = _payload(process_line(FORM, ""))
    assert payload["choices"][0]["finish_reason"] == "stop"
    assert "content" not in payload["choices"][0]["delta"]


# --- トークン効率(#99)-----------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "針刺し・切創",
        {"choices": [{"delta": {"content": "曝露時の対応"}}]},
        "曝露".encode("utf-8"),
    ],
)
def test_non_ascii_is_never_escaped(line):
    """どの入力型を通っても \\uXXXX 展開しない(#99 B の回帰)。"""
    assert "\\u" not in process_line(FORM, line)


def test_basemodel_non_ascii_is_never_escaped():
    assert "\\u" not in process_line(FORM, _Chunk(content="針刺し・切創"))


# --- SSE の枠組み -----------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    ["曝露", {"a": 1}, "データ".encode("utf-8"), 'data: {"a": 1}'],
)
def test_every_return_is_a_single_sse_frame(line):
    out = process_line(FORM, line)
    assert out.startswith("data: ")
    assert out.endswith("\n\n")
    assert out.count("\n\n") == 1
