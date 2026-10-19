"""音声入力の通し録音に付く meta.dictation の受け口と `<source>` の印の回帰。

マイクの音声入力は区切りごとに文字起こしするので、区切りで語が壊れうる。録音の最初から
最後までを 1 本の音声として発言に添付し、配備側の tool が原本から読み直せるようにする
(_memos/audio.md「通し録音の添付」)。

- upload の Form `dictation`(JSON)だけが Files.meta へ書き込める。形を検証し、
  それ以外のキーをクライアントが meta に足す口は無い
- モデルへ注入する `<source>` タグには `dictation="true"` の印だけが載る
  (区間一覧・本文は prompt に入れない)
"""

import json

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user

DICTATION = {
    "version": 1,
    "session_id": "0b0e7c1e-6f0a-4c0e-9a57-0c9a3f6f2a11",
    "part": 1,
    "parts": 1,
    "offset_sec": 0.0,
    "duration_sec": 27.6,
    "container": "webm/opus",
    "segments": [
        {"i": 1, "start_sec": 0.0, "end_sec": 11.6, "cut": "breath", "text": "一つ目"},
        {"i": 2, "start_sec": 11.6, "end_sec": 27.6, "cut": "end", "text": "二つ目"},
    ],
    "joined_text": "一つ目 二つ目",
}


def _with(**overrides):
    return {**DICTATION, **overrides}


class TestFileDictation(AbstractPostgresTest):
    BASE_PATH = "/api/v1/files"

    def _upload(
        self, dictation=None, name="rec.webm", content_type="audio/webm", extra=None
    ):
        data = dict(extra or {})
        if dictation is not None:
            data["dictation"] = (
                dictation if isinstance(dictation, str) else json.dumps(dictation)
            )
        with mock_webui_user(id="1", role="admin"):
            return self.fast_api_client.post(
                self.create_url("/?process=false"),
                files={"file": (name, b"\x1aE\xdf\xa3dummy", content_type)},
                data=data,
            )

    def test_dictation_is_stored_in_meta(self):
        r = self._upload(DICTATION)
        assert r.status_code == 200, r.text
        assert r.json()["meta"]["dictation"] == DICTATION

        with mock_webui_user(id="1", role="admin"):
            got = self.fast_api_client.get(self.create_url(f"/{r.json()['id']}"))
        assert got.json()["meta"]["dictation"] == DICTATION

    def test_later_part_of_a_split_recording(self):
        # 長い録音の 2 本目: i は録音全体の通し番号で続き、秒はこのファイルの先頭から
        second = _with(
            part=2,
            parts=2,
            offset_sec=3605.2,
            segments=[
                {**DICTATION["segments"][0], "i": 251},
                {**DICTATION["segments"][1], "i": 252},
            ],
        )
        r = self._upload(second)
        assert r.status_code == 200, r.text
        assert r.json()["meta"]["dictation"] == second

    def test_upload_without_dictation_has_no_key(self):
        r = self._upload()
        assert r.status_code == 200, r.text
        assert "dictation" not in r.json()["meta"]

    def test_client_cannot_write_other_meta_keys(self):
        # Form に別名のフィールドを足しても meta には載らない
        r = self._upload(DICTATION, extra={"meta": '{"evil": 1}', "evil": "1"})
        assert r.status_code == 200, r.text
        assert set(r.json()["meta"].keys()) == {
            "name",
            "content_type",
            "size",
            "data",
            "dictation",
        }

    def test_invalid_shapes_are_rejected(self):
        segment = DICTATION["segments"][0]
        bad = [
            "not json",
            json.dumps([1, 2]),
            _with(version=2),
            _with(evil="x"),
            _with(container="webm/opus; rm -rf"),
            _with(segments=[]),
            _with(segments=[{**segment, "cut": "other"}]),
            _with(segments=[{**segment, "evil": 1}]),
            _with(segments=[segment, {**DICTATION["segments"][1], "i": 3}]),
            _with(session_id="../../etc"),
            _with(part=0),
            _with(part=2, parts=1),
            _with(offset_sec=-1),
            _with(segments=[{**segment, "start_sec": 5.0, "end_sec": 1.0}]),
            # 区間が前の区間に食い込む
            _with(
                segments=[
                    segment,
                    {**segment, "i": 2, "start_sec": 3.0, "end_sec": 20.0},
                ]
            ),
            {k: v for k, v in DICTATION.items() if k != "joined_text"},
        ]
        for payload in bad:
            r = self._upload(payload)
            assert r.status_code == 400, (payload, r.text)

    def test_oversize_is_rejected(self):
        # Form の 1 フィールド上限(1MB)より手前で、こちらの上限に当たる大きさ
        r = self._upload(_with(joined_text="a" * (512 * 1024)))
        assert r.status_code == 400, r.text
        assert "dictation must not exceed" in r.text

    def test_rejected_for_non_audio(self):
        r = self._upload(DICTATION, name="note.txt", content_type="text/plain")
        assert r.status_code == 400, r.text

    def test_source_tag_carries_dictation_mark(self):
        from nucllei.retrieval.utils import get_sources_from_files
        from nucllei.utils.middleware import build_source_context

        dictated = self._upload(DICTATION).json()["id"]
        plain = self._upload(name="memo.webm").json()["id"]

        sources = get_sources_from_files(
            request=None,
            files=[{"type": "file", "id": dictated}, {"type": "file", "id": plain}],
        )
        context = build_source_context(sources)

        lines = context.split("\n")
        assert lines[0].startswith(
            f'<source file_id="{dictated}" name="rec.webm" dictation="true" '
            'extraction="none">'
        )
        # 普通の音声添付には印が付かない(それ以外の属性は同じ)
        assert lines[1].startswith(
            f'<source file_id="{plain}" name="memo.webm" extraction="none">'
        )
        # 分かれていない録音には part / parts を載せない
        assert "part=" not in context
        # 区間一覧・本文は prompt に入れない
        assert "一つ目" not in context
        assert "start_sec" not in context

    def test_text_attachment_without_extraction_points_to_read_attachment(self):
        """抽出の無い添付でも text 系(read_attachment が raw を decode して読む型)なら、
        「読めない」と言わず read_attachment を名指しする。content_type は sources の
        metadata に無いので Files.meta から引く(2026-10-19)。"""
        from nucllei.retrieval.utils import get_sources_from_files
        from nucllei.utils.middleware import build_source_context
        from nucllei.utils.tool_errors import turn_tools

        text = self._upload(name="memo.txt", content_type="text/plain").json()["id"]
        audio = self._upload(name="memo.webm").json()["id"]
        sources = get_sources_from_files(
            request=None,
            files=[{"type": "file", "id": text}, {"type": "file", "id": audio}],
        )
        fact = "文字が抜き出されていない — 本文はこの会話に入っていない"
        with turn_tools(["feed_image", "read_attachment"]):
            lines = build_source_context(sources).split("\n")
        assert lines == [
            f'<source file_id="{text}" name="memo.txt" extraction="none">'
            f'({fact}。次の手: 中身は read_attachment(file_id="{text}") で読める)</source>',
            f'<source file_id="{audio}" name="memo.webm" extraction="none">'
            f"({fact}。次の手: 中身が要るなら、PDF のページは feed_image で見る。"
            "read_attachment では読めない)</source>",
        ]

    def test_payload_names_only_the_tools_of_the_turn(self, monkeypatch):
        """process_chat_payload が組む `<source>` の次の手は、このターンでモデルに出す tool
        (tools_dict)だけを名指しする(2026-10-19 llens 監査 (3) / (13))。"""
        import asyncio
        import types

        from starlette.requests import Request

        from nucllei.main import app
        from nucllei.utils import middleware

        plain = self._upload(name="memo.webm").json()["id"]

        async def feed_image(ref: str = ""):
            return ""

        def fake_get_tools(request, tool_ids, user, extra_params, **kwargs):
            return {
                "feed_image": {
                    "callable": feed_image,
                    "spec": {
                        "name": "feed_image",
                        "parameters": {"properties": {"ref": {"type": "string"}}},
                    },
                }
            }

        monkeypatch.setattr(middleware, "get_tools", fake_get_tools)
        model = {
            "id": "tp",
            "name": "tp",
            "owned_by": "openai",
            "info": {"meta": {"toolIds": ["builtin_feed_image"]}},
        }
        user = types.SimpleNamespace(
            id="1", name="Taro", email="t@example.invalid", role="admin", info=None
        )
        saved_models = getattr(app.state, "MODELS", None)
        app.state.MODELS = {"tp": model}
        try:
            request = Request(
                scope={
                    "type": "http",
                    "app": app,
                    "method": "POST",
                    "path": "/api/chat/completions",
                    "headers": [],
                    "query_string": b"",
                }
            )
            form_data = {
                "model": "tp",
                "stream": True,
                "messages": [{"role": "user", "content": "読んで"}],
                "files": [{"type": "file", "id": plain}],
            }
            form_data, metadata, _ = asyncio.run(
                middleware.process_chat_payload(
                    request,
                    form_data,
                    user,
                    {"user_id": "1", "model": model},
                    model,
                    None,
                )
            )
        finally:
            app.state.MODELS = saved_models

        system = form_data["messages"][0]
        assert system["role"] == "system"
        assert (
            f'<source file_id="{plain}" name="memo.webm" extraction="none">'
            "(文字が抜き出されていない — 本文はこの会話に入っていない。次の手: 中身が要るなら、"
            "PDF のページは feed_image で見る)</source>"
        ) in system["content"]
        assert "send_to_workspace" not in system["content"]

    def test_source_tag_of_a_split_recording_carries_part(self):
        # 長い録音が 2 本に分かれたとき: どれを読み直すか決められるよう、全ファイルに
        # part / parts が載る
        from nucllei.retrieval.utils import get_sources_from_files
        from nucllei.utils.middleware import build_source_context

        first = self._upload(_with(part=1, parts=2)).json()["id"]
        second = self._upload(
            _with(
                part=2,
                parts=2,
                offset_sec=3605.2,
                segments=[
                    {**DICTATION["segments"][0], "i": 3},
                    {**DICTATION["segments"][1], "i": 4},
                ],
            )
        ).json()["id"]

        sources = get_sources_from_files(
            request=None,
            files=[{"type": "file", "id": first}, {"type": "file", "id": second}],
        )
        lines = build_source_context(sources).split("\n")

        assert lines[0].startswith(
            f'<source file_id="{first}" name="rec.webm" dictation="true" part="1" '
            'parts="2" extraction="none">'
        )
        assert lines[1].startswith(
            f'<source file_id="{second}" name="rec.webm" dictation="true" part="2" '
            'parts="2" extraction="none">'
        )


def test_build_source_context_attributes():
    """`<source>` の属性(抽出状態 3 種 + 印)。DB 不要の純関数。"""
    from nucllei.utils.middleware import build_source_context

    sources = [
        {
            "document": ["本文", "失敗の理由", ""],
            "metadata": [
                {"file_id": "a", "extraction_status": "success"},
                {"file_id": "b", "extraction_status": "failed"},
                {"file_id": "c", "extraction_status": None, "dictation": True},
            ],
        },
        # 抽出済み(Attachment が文字起こしした等)でも印は付く
        {
            "document": ["書き起こし"],
            "metadata": [
                {"file_id": "d", "extraction_status": "success", "dictation": True}
            ],
        },
        # 分かれた録音の 2 本目 / 分かれていない録音(parts = 1)は part を載せない
        {
            "document": ["", ""],
            "metadata": [
                {
                    "file_id": "e",
                    "dictation": True,
                    "dictation_part": 2,
                    "dictation_parts": 3,
                },
                {
                    "file_id": "f",
                    "dictation": True,
                    "dictation_part": 1,
                    "dictation_parts": 1,
                },
            ],
        },
    ]
    # ターンの tool が立っていない(名指しできる tool が無い)ときは事実だけ。
    # failed の例外の文(「失敗の理由」)はモデルに出さない(log にだけ残す)
    none_body = "(文字が抜き出されていない — 本文はこの会話に入っていない)"
    assert build_source_context(sources).split("\n") == [
        '<source file_id="a">本文</source>',
        f'<source file_id="b" extraction="failed">{none_body}</source>',
        f'<source file_id="c" dictation="true" extraction="none">{none_body}</source>',
        '<source file_id="d" dictation="true">書き起こし</source>',
        '<source file_id="e" dictation="true" part="2" parts="3" extraction="none">'
        f"{none_body}</source>",
        f'<source file_id="f" dictation="true" extraction="none">{none_body}</source>',
    ]


def test_build_source_context_name_attribute_is_escaped():
    """`name=` はファイル名(属性値としてエスケープ)。連番の `id=` は載せない。"""
    from nucllei.utils.middleware import build_source_context

    sources = [
        {
            "document": ["本文"],
            "metadata": [
                {
                    "file_id": "a",
                    "name": 'a"b<c>&d.pdf',
                    "extraction_status": "success",
                }
            ],
        }
    ]
    assert build_source_context(sources) == (
        '<source file_id="a" name="a&quot;b&lt;c&gt;&amp;d.pdf">本文</source>'
    )


def test_no_text_note_names_only_tools_in_turn():
    """本文の無い添付の次の手は、このターンに出ている tool だけを名指しする(llens 監査 (3) / (13))。"""
    from nucllei.utils.middleware import build_source_context
    from nucllei.utils.tool_errors import turn_tools

    sources = [
        {
            "document": ["", "Traceback: /srv/secret/path"],
            "metadata": [
                {"file_id": "n", "extraction_status": None},
                {"file_id": "f", "extraction_status": "failed"},
            ],
        }
    ]
    only_none = [
        {"document": [""], "metadata": [{"file_id": "n", "extraction_status": None}]}
    ]
    fact = "文字が抜き出されていない — 本文はこの会話に入っていない"

    with turn_tools(["feed_image", "send_to_workspace", "read_attachment"]):
        lines = build_source_context(sources).split("\n")
    full = (
        f"({fact}。次の手: 中身が要るなら、PDF のページは feed_image で見て、"
        "加工・変換は send_to_workspace で取り込む。read_attachment では読めない)"
    )
    assert lines == [
        f'<source file_id="n" extraction="none">{full}</source>',
        f'<source file_id="f" extraction="failed">{full}</source>',
    ]
    assert "secret" not in "\n".join(lines)

    with turn_tools(["feed_image"]):
        assert build_source_context(only_none) == (
            f'<source file_id="n" extraction="none">({fact}。次の手: 中身が要るなら、'
            "PDF のページは feed_image で見る)</source>"
        )
    with turn_tools(["send_to_workspace", "run_python"]):
        assert build_source_context(only_none) == (
            f'<source file_id="n" extraction="none">({fact}。次の手: 中身が要るなら、'
            "加工・変換は send_to_workspace で取り込む)</source>"
        )
    with turn_tools(["read_attachment"]):
        assert build_source_context(only_none) == (
            f'<source file_id="n" extraction="none">({fact}。次の手: '
            "read_attachment では読めない)</source>"
        )


def test_no_text_note_text_type_points_to_read_attachment():
    """none で content_type が text 系なら read_attachment で読めると書く。判定は
    read_attachment と同じ関数(`attachment_text._is_text_ct`)。failed は text 系でも
    「読めない」のまま(read_attachment も outcome で断る)。"""
    from nucllei.utils.middleware import build_source_context
    from nucllei.utils.tool_errors import turn_tools

    fact = "文字が抜き出されていない — 本文はこの会話に入っていない"

    def one(ct, status=None):
        return [
            {
                "document": [""],
                "metadata": [
                    {"file_id": "t", "extraction_status": status, "content_type": ct}
                ],
            }
        ]

    with turn_tools(["feed_image", "send_to_workspace", "read_attachment"]):
        for ct in ("text/plain", "text/csv", "application/json", "application/ld+json"):
            assert build_source_context(one(ct)) == (
                f'<source file_id="t" extraction="none">({fact}。次の手: 中身は '
                'read_attachment(file_id="t") で読める。'
                "加工・変換は send_to_workspace で取り込む)</source>"
            ), ct
        assert build_source_context(one("text/plain", "failed")) == (
            f'<source file_id="t" extraction="failed">({fact}。次の手: 中身が要るなら、'
            "PDF のページは feed_image で見て、加工・変換は send_to_workspace で取り込む。"
            "read_attachment では読めない)</source>"
        )
        assert "read_attachment では読めない" in build_source_context(
            one("application/pdf")
        )
    # read_attachment がこのターンに無ければ名指ししない
    with turn_tools(["feed_image"]):
        assert build_source_context(one("text/plain")) == (
            f'<source file_id="t" extraction="none">({fact})</source>'
        )
