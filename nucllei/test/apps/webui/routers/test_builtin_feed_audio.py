"""builtin_feed_audio (音声を自分の耳に入れる — feed_image の音声版) のユニットテスト。

見るのは tool の振る舞いだけ (解決規則は test_image_refs_resolve.py、実体化は test_audio_input.py):
- 音声を聞けるモデル (capabilities.audio) にだけ出る (`Tools.available`)
- 引数: url / path / file_id のどれか 1 つ (引数名どおりの resolver へ)、start / end の形と前後
- file_id (と url の配信口) は所有者チェック。無いのと他人のものは同じ固定文
- 区間: 範囲外は ERROR、上限を超える区間は ERROR (分けて呼ぶ例を出す)、end は長さで切る
- 積む参照: 全体はそのまま、区間は `#t=<開始>,<終了>`。File は作らない
"""

import asyncio
import types

import pytest
from test.util.tool_errors import call_error, call_text

from nucllei.utils.image_refs import RefError
from nucllei.utils.tool_errors import turn_tools

WAV = b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\0" * 32


@pytest.fixture(autouse=True)
def _tools_in_turn():
    with turn_tools(("feed_audio", "feed_image", "run_shell", "list_chat_files")):
        yield


def _mod():
    from nucllei.builtin_tools import feed_audio

    return feed_audio


class _Loaded:
    def __init__(self, data=WAV, content_type="audio/wav", name="x.wav"):
        self.data = data
        self.content_type = content_type
        self.name = name


class _Files:
    def __init__(self, records):
        self.records = records

    def get_file_by_id(self, fid):
        return self.records.get(fid)


def _install(monkeypatch, *, duration=600.0, table=None, records=None, max_minutes=120):
    mod = _mod()
    calls = []

    async def resolve_url(ref, user=None, **kwargs):
        calls.append(("url", ref))
        hit = (table or {}).get(ref)
        if hit is None:
            return None, RefError("not_found", "missing")
        return hit, None

    async def resolve_path(ref, user, **kwargs):
        calls.append(("path", ref))
        if not ref.startswith(("/workspace/", "/ext/")):
            return None, RefError("unsupported_form", "not a sandbox path")
        hit = (table or {}).get(ref)
        if hit is None:
            return None, RefError("not_found", "missing")
        return hit, None

    async def probe(data):
        return duration if data.startswith(b"RIFF") else None

    monkeypatch.setattr(mod, "resolve_url", resolve_url)
    monkeypatch.setattr(mod, "resolve_path", resolve_path)
    monkeypatch.setattr(mod, "probe_duration", probe)
    monkeypatch.setattr(mod, "Files", _Files(records or {}))
    monkeypatch.setattr(mod, "_read_raw", lambda rec: rec.raw)
    from nucllei.utils import audio_input

    monkeypatch.setattr(audio_input, "AUDIO_INPUT_MAX_MINUTES", max_minutes)
    return mod, calls


def _record(owner="u1", raw=WAV, ct="audio/wav", name="会議.wav"):
    return types.SimpleNamespace(
        id="f1", user_id=owner, filename=name, raw=raw, meta={"content_type": ct}
    )


def _run(monkeypatch, *, setup=None, **kwargs):
    mod, calls = _install(monkeypatch, **(setup or {}))
    fed = []
    result = asyncio.run(
        call_text(
            mod.Tools().feed_audio(
                __user__={"id": "u1"}, __feed_audio__=fed.append, **kwargs
            )
        )
    )
    return result, fed, calls


# ---- 出す / 出さない ----


def test_offered_only_to_audio_models():
    tools = _mod().Tools()
    assert tools.available(
        None, None, {"info": {"meta": {"capabilities": {"audio": True}}}}, {}
    )
    assert not tools.available(
        None, None, {"info": {"meta": {"capabilities": {"vision": True}}}}, {}
    )
    assert not tools.available(None, None, {}, {})


def test_registered_as_builtin_with_one_function():
    import nucllei.builtin_tools as pkg
    from nucllei.utils.tools import get_tool_specs

    entry = pkg.BUILTIN_TOOLS["builtin_feed_audio"]
    assert entry["module"] is _mod() and entry["name"] == "Feed Audio"
    specs = get_tool_specs(entry["module"].Tools())
    assert [s["name"] for s in specs] == [
        "feed_audio"
    ]  # available はフック (spec にしない)
    assert set(specs[0]["parameters"]["properties"]) == {
        "url",
        "path",
        "file_id",
        "start",
        "end",
    }


def test_registered_as_a_capability_tool():
    import nucllei.builtin_tools as pkg

    assert pkg.BUILTIN_TOOLS["builtin_feed_audio"]["capability"] == "audio"
    assert pkg.is_capability_tool_id("builtin_feed_audio")


def test_offered_by_the_audio_capability_not_by_tool_ids(monkeypatch):
    """能力に付く builtin: toolIds に無くても音声を聞けるモデルのターンに載り (get_capability_tools)、
    toolIds に残った builtin_feed_audio は読まない (行の無い id と同じく黙って飛ばす — 二重にならない)。"""
    from nucllei.utils import tools as tools_mod

    def row(tool_id):
        if tool_id != "builtin_feed_audio":
            return None
        return types.SimpleNamespace(
            id=tool_id,
            user_id="system",
            access_control={"read": {"user_ids": [], "group_ids": []}},
            name="Feed Audio",
            specs=tools_mod.get_tool_specs(_mod().Tools()),
        )

    monkeypatch.setattr(tools_mod.Tools, "get_tool_by_id", row)
    monkeypatch.setattr(tools_mod.Tools, "get_tool_valves_by_id", lambda tool_id: {})
    request = types.SimpleNamespace(
        app=types.SimpleNamespace(
            state=types.SimpleNamespace(TOOLS={"builtin_feed_audio": _mod().Tools()})
        )
    )
    # 行の read ACL は空 — 能力に付く builtin は ACL を見ない
    user = types.SimpleNamespace(id="u1", role="user")
    for caps, expected in (
        ({"audio": True}, ["feed_audio"]),
        ({}, []),
        ({"audio": False}, []),
    ):
        extra = {
            "__metadata__": {
                "model": {"id": "m", "info": {"meta": {"capabilities": caps}}}
            },
            "__user__": {"id": "u1"},
        }
        assert list(tools_mod.get_capability_tools(request, user, extra)) == expected
        assert tools_mod.get_tools(request, ["builtin_feed_audio"], user, extra) == {}


# ---- 引数 ----


def test_needs_exactly_one_target(monkeypatch):
    result, fed, _ = _run(monkeypatch)
    assert result.startswith("ERROR: 対象が無い") and fed == []
    result, fed, _ = _run(monkeypatch, file_id="f1", path="/workspace/a.mp3")
    assert result.startswith("ERROR: path / file_id は同時に指定できない") and fed == []


def test_bad_times(monkeypatch):
    rec = {"f1": _record()}
    result, fed, _ = _run(
        monkeypatch, setup={"records": rec}, file_id="f1", start="abc"
    )
    assert result.startswith("ERROR: start 'abc' を時刻として読めない") and fed == []
    result, fed, _ = _run(
        monkeypatch, setup={"records": rec}, file_id="f1", start="2:00", end="1:00"
    )
    assert "end (1:00) が start (2:00) より前か同じ" in result and fed == []


# ---- file_id ----


def test_file_id_whole_and_range(monkeypatch):
    rec = {"f1": _record()}
    result, fed, _ = _run(monkeypatch, setup={"records": rec}, file_id="f1")
    assert fed == ["/api/v1/files/f1/content"]
    assert (
        result
        == "- 会議.wav の 全体 (10:00) を自分の耳に入れた (ユーザーには再生されない)。"
    )

    result, fed, _ = _run(
        monkeypatch, setup={"records": rec}, file_id="f1", start="1:30", end="9999"
    )
    # end は長さで切る
    assert fed == ["/api/v1/files/f1/content#t=90,600"]
    assert "1:30〜10:00 (全 10:00)" in result

    result, fed, _ = _run(monkeypatch, setup={"records": rec}, file_id="f1", end=60)
    assert fed == ["/api/v1/files/f1/content#t=,60"]


def test_file_id_owner_check(monkeypatch):
    """他人の file と無い file は同じ固定文 (存在の有無を漏らさない)。url の配信口も同じ。"""
    rec = {"f1": _record(owner="someone-else")}
    texts = set()
    for kwargs in ({"file_id": "f1"}, {"file_id": "nope"}):
        result, fed, _ = _run(monkeypatch, setup={"records": rec}, **kwargs)
        assert fed == [] and "アクセス権が無い" in result
        texts.add(result.replace("nope", "f1"))
    assert len(texts) == 1
    result, fed, calls = _run(
        monkeypatch, setup={"records": rec}, url="/api/v1/files/f1/content"
    )
    assert fed == [] and "アクセス権が無い" in result and calls == []


def test_not_audio_and_unreadable(monkeypatch):
    rec = {"f1": _record(raw=b"%PDF-1.7", ct="application/pdf", name="a.pdf")}
    result, fed, _ = _run(monkeypatch, setup={"records": rec}, file_id="f1")
    assert result.startswith("ERROR: a.pdf は音声ではない") and fed == []
    assert "feed_image" in result
    rec = {"f1": _record(raw=b"\x1aE\xdf\xa3broken", ct="audio/webm", name="b.webm")}
    result, fed, _ = _run(monkeypatch, setup={"records": rec}, file_id="f1")
    assert result.startswith("ERROR: b.webm は音声として読めない") and fed == []


# ---- 区間と上限 ----


def test_start_out_of_range(monkeypatch):
    rec = {"f1": _record()}
    result, fed, _ = _run(
        monkeypatch, setup={"records": rec}, file_id="f1", start="12:00"
    )
    assert result.startswith("ERROR: 会議.wav は全 10:00 で、start 12:00 は範囲外")
    assert fed == []


def test_over_the_limit_needs_a_section(monkeypatch):
    rec = {"f1": _record()}
    setup = {"records": rec, "duration": 3 * 3600.0, "max_minutes": 120}
    result, fed, _ = _run(monkeypatch, setup=setup, file_id="f1")
    assert fed == []
    assert "0:00〜3:00:00 は 3:00:00 あり、1 回で聞ける上限 120 分 を超える" in result
    assert 'start="0:00", end="2:00:00"' in result
    result, fed, _ = _run(
        monkeypatch, setup=setup, file_id="f1", start="2:00:00", end="3:00:00"
    )
    assert fed == ["/api/v1/files/f1/content#t=7200,10800"]


# ---- url / path ----


def test_url_and_path_go_to_their_resolvers(monkeypatch):
    table = {
        "https://h.test/a.mp3": _Loaded(name="a.mp3"),
        "/workspace/rec.wav": _Loaded(),
    }
    result, fed, calls = _run(
        monkeypatch, setup={"table": table}, url="https://h.test/a.mp3", start="0:10"
    )
    assert calls == [("url", "https://h.test/a.mp3")]
    assert fed == ["https://h.test/a.mp3#t=10,600"]
    result, fed, calls = _run(
        monkeypatch, setup={"table": table}, path="/workspace/rec.wav"
    )
    assert calls == [("path", "/workspace/rec.wav")] and fed == ["/workspace/rec.wav"]
    # 引数名どおり: path に URL は sandbox のパスの形でない (読み替えない)
    result, fed, calls = _run(
        monkeypatch, setup={"table": table}, path="https://h.test/a.mp3"
    )
    assert result.startswith("ERROR: https://h.test/a.mp3 は sandbox のパスの形でない")
    assert fed == []
    result, fed, _ = _run(
        monkeypatch, setup={"table": table}, url="https://h.test/none.mp3"
    )
    assert (
        result.startswith("ERROR: https://h.test/none.mp3 は存在しない") and fed == []
    )


def test_without_sink_or_user(monkeypatch):
    mod, _ = _install(monkeypatch, records={"f1": _record()})
    err = asyncio.run(
        call_error(
            mod.Tools().feed_audio(
                file_id="f1", __feed_audio__=None, __user__={"id": "u1"}
            )
        )
    )
    assert err.kind == "failure"
    err = asyncio.run(
        call_error(mod.Tools().feed_audio(file_id="f1", __feed_audio__=print))
    )
    assert err.kind == "failure"


def test_description_says_when_to_call_it():
    """いつ呼ぶかは tool の説明に集める (feed_image と同じ形)。全利用者で同じ固定文。"""
    from nucllei.utils.tools import tool_description_from_docstring

    desc = tool_description_from_docstring(_mod().Tools.feed_audio.__doc__)
    calls, rest = desc.split("呼ぶ場面:\n", 1)[1].split("呼ばない場面:\n", 1)
    assert "上限を超えて入らなかった添付" in calls and "start / end" in calls
    assert calls.startswith("- 会話に入っていない音声を聞く")
    assert "会話に既に音声が入っている添付 (前の発言の添付" in rest
    assert "毎ターン送り直されている" in rest and "区間なしで丸ごと入れ直す" in rest
    assert "{{" not in desc
