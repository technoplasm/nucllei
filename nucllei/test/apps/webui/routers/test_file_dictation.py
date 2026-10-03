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
            f'<source id="1" file_id="{dictated}" dictation="true" extraction="none">'
        )
        # 普通の音声添付には印が付かない(それ以外の属性は同じ)
        assert lines[1].startswith(
            f'<source id="2" file_id="{plain}" extraction="none">'
        )
        # 分かれていない録音には part / parts を載せない
        assert "part=" not in context
        # 区間一覧・本文は prompt に入れない
        assert "一つ目" not in context
        assert "start_sec" not in context

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
            f'<source id="1" file_id="{first}" dictation="true" part="1" parts="2" '
            'extraction="none">'
        )
        assert lines[1].startswith(
            f'<source id="2" file_id="{second}" dictation="true" part="2" parts="2" '
            'extraction="none">'
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
    none_body = "(テキスト抽出されていない。次の手: 中身が要るなら file_id を受ける tool に渡す)"
    assert build_source_context(sources).split("\n") == [
        '<source id="1" file_id="a">本文</source>',
        '<source id="2" file_id="b" extraction="failed">失敗の理由</source>',
        f'<source id="3" file_id="c" dictation="true" extraction="none">{none_body}</source>',
        '<source id="4" file_id="d" dictation="true">書き起こし</source>',
        '<source id="5" file_id="e" dictation="true" part="2" parts="3" extraction="none">'
        f"{none_body}</source>",
        f'<source id="6" file_id="f" dictation="true" extraction="none">{none_body}</source>',
    ]
