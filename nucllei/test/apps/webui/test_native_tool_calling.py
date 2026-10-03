"""native function calling 一本化(issue #18 / #35)の回帰テスト。

- 非 native(プロンプトベース)tool calling 経路は削除済み。ツールが指定されたら
  function_calling パラメータに関係なく常に native 経路(payload の "tools")で
  モデルへ渡ることを固定する。
- ツール呼び出しの反復上限は TOOL_CALL_MAX_ITERATIONS
  (既定 30 / -1 で無制限)。上限到達時はチャット内エラーイベントを発行する。

反復ループのテストは issue #29 の実キャプチャ fixture(SGLang × Kimi K2.6)を
リプレイして行う。
"""

import asyncio
import json
import os
import subprocess
import sys
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from starlette.responses import StreamingResponse

from test.util import model_frames as mf
from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user

####################################
# 反復上限の env パース(既定 30 / -1 無制限 / 不正値は既定)
####################################


# nucllei パッケージを含む backend ディレクトリ(サブプロセスの import パス用)。
# CI では cwd や sys.path が異なり `from nucllei...` が解決できないため明示する。
import nucllei

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(nucllei.__file__)))


def _parse_iterations_env(value):
    """サブプロセスで nucllei.env を import し直してパース結果を得る。

    env.py は import 時に値が確定するため、プロセス内での再 import では
    テストできない(他モジュールが古い値を保持する)。
    """
    code = (
        "from nucllei.env import TOOL_CALL_MAX_ITERATIONS as v;"
        "print('ITERATIONS=%d' % v)"
    )
    env = {**os.environ}
    env["PYTHONPATH"] = os.pathsep.join(
        [_BACKEND_DIR, *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])]
    )
    if value is None:
        env.pop("TOOL_CALL_MAX_ITERATIONS", None)
    else:
        env["TOOL_CALL_MAX_ITERATIONS"] = value
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        cwd=_BACKEND_DIR,
        check=True,
    )
    lines = [l for l in result.stdout.splitlines() if l.startswith("ITERATIONS=")]
    assert lines, result.stdout
    return int(lines[-1][len("ITERATIONS=") :])


def test_max_tool_call_iterations_default_is_30():
    assert _parse_iterations_env(None) == 30


def test_max_tool_call_iterations_minus_one_means_unlimited():
    assert _parse_iterations_env("-1") == -1


def test_max_tool_call_iterations_invalid_falls_back_to_default():
    assert _parse_iterations_env("not-a-number") == 30


####################################
# native ツール呼び出しループ(fixture リプレイ)
####################################


def _sse_response(name: str) -> StreamingResponse:
    """実キャプチャ SSE を行単位で流す StreamingResponse。"""
    raw = mf.load_raw(name).decode("utf-8")

    async def gen():
        for line in raw.split("\n"):
            yield line + "\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


async def _read_sse_events(response):
    """POST 応答 SSE を読み切り、封筒({"chat_id","message_id","data"})の data を集める。

    wire v2 ではバックグラウンドタスクの終端でイベントバスが close され
    SSE が EOF するため、読み切り = タスクの完走待ちになる。
    先頭は task 封筒({"type":"task","data":{"task_id":...}})。
    """
    events = []
    async for line in response.body_iterator:
        line = line.decode("utf-8") if isinstance(line, bytes) else line
        for part in line.split("\n"):
            if part.startswith("data: "):
                events.append(json.loads(part[len("data: ") :])["data"])
    return events


async def _drive_tool_call_loop(
    monkeypatch,
    cap,
    followup_responses,
    initial_fixture="tool_call",
    extra_tools=None,
    upserts=None,
    omit_default_tool=False,
    initial_response=None,
):
    """process_chat_response をツール呼び出し SSE で駆動する(wire v2)。

    実物の CompletionEventStream を購読して process_chat_response へ渡し、
    返る SSE(StreamingResponse)を読み切ることでタスク完走を待つ。

    followup_responses: ツール実行後の継続リクエストへの応答(fixture 名)の列。
    尽きた後は常に tool_call を返す(上限テスト用)。
    initial_fixture: 初回応答に使う fixture 名(引数なし/特殊文字引数の検証で差し替える)。
    initial_response: 初回応答そのもの(StreamingResponse または非 stream dict)。
    指定時は initial_fixture より優先(異常系 SSE・非 stream 応答の駆動用)。
    extra_tools: metadata["tools"] に追加登録するツール(get_current_time など)。
    戻り値: (emit されたイベント列, 継続リクエスト数, ツール実行時の引数列)
    """
    from nucllei.utils import middleware
    from nucllei.utils.completion_events import CompletionEventStream

    completion_calls = []
    tool_invocations = []
    followups = list(followup_responses)

    async def fake_generate_chat_completion(request, form_data, user):
        completion_calls.append(form_data)
        name = followups.pop(0) if followups else "tool_call"
        return _sse_response(name)

    if cap is not None:
        monkeypatch.setattr(middleware, "TOOL_CALL_MAX_ITERATIONS", cap)
    monkeypatch.setattr(middleware, "get_sorted_filter_ids", lambda model: [])
    monkeypatch.setattr(
        middleware, "generate_chat_completion", fake_generate_chat_completion
    )

    # DB 非依存にする(チャット保存はこのテストの対象外。実装内の DB 呼び出しは
    # asyncio.to_thread 経由になったが、クラス属性の monkeypatch はそのまま効く)
    monkeypatch.setattr(
        middleware.Chats,
        "get_message_by_id_and_message_id",
        lambda chat_id, message_id: None,
    )
    monkeypatch.setattr(
        middleware.Chats,
        "upsert_message_to_chat_by_id_and_message_id",
        lambda chat_id, message_id, data: (
            upserts.append(data) if upserts is not None else None
        ),
    )
    monkeypatch.setattr(
        middleware.Chats, "get_chat_title_by_id", lambda chat_id: "chat"
    )
    monkeypatch.setattr(
        middleware.Chats, "get_messages_by_chat_id", lambda chat_id: None
    )
    monkeypatch.setattr(
        middleware.Chats,
        "record_usage_by_id_and_message_id",
        lambda chat_id, message_id, usage, context_size=None: None,
    )

    async def get_weather(**kwargs):
        tool_invocations.append(kwargs)
        return "晴れ"

    weather_spec = {
        "name": "get_weather",
        "description": "天気を返す",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string"},
                "detail": {"type": "string"},
            },
            "required": ["city"],
        },
    }

    tools = (
        {}
        if omit_default_tool
        else {"get_weather": {"spec": weather_spec, "callable": get_weather}}
    )
    if extra_tools:
        tools.update(extra_tools)
    metadata = {
        "chat_id": "chat-1",
        "message_id": "message-1",
        "tools": tools,
    }
    form_data = {
        "model": "kimi-k2.6",
        "stream": True,
        "messages": [{"role": "user", "content": "札幌の天気は?"}],
        "tools": [{"type": "function", "function": weather_spec}],
    }
    user = types.SimpleNamespace(
        id="1", email="john.doe@nucllei.local", name="John Doe", role="admin"
    )
    request = types.SimpleNamespace(
        app=types.SimpleNamespace(
            state=types.SimpleNamespace(
                WEBUI_NAME="llens",
                config=types.SimpleNamespace(WEBUI_URL="http://localhost"),
            )
        )
    )
    model = {"id": "kimi-k2.6", "name": "kimi-k2.6"}

    events_stream = CompletionEventStream(chat_id="chat-1", message_id="message-1")
    queue = events_stream.subscribe()

    response = await middleware.process_chat_response(
        request,
        (
            initial_response
            if initial_response is not None
            else _sse_response(initial_fixture)
        ),
        form_data,
        user,
        metadata,
        model,
        [],
        {},
        events_stream=events_stream,
        queue=queue,
    )
    assert isinstance(response, StreamingResponse)
    # SSE 読み切り = タスク完走待ち(タスク終端で stream close → EOF)
    events = await asyncio.wait_for(_read_sse_events(response), timeout=30)
    return events, completion_calls, tool_invocations


def _iteration_limit_errors(events):
    return [
        e["data"]["error"]["content"]
        for e in events
        if e.get("type") == "chat:completion"
        and isinstance(e.get("data", {}).get("error"), dict)
        and "iteration limit" in e["data"]["error"].get("content", "")
    ]


def test_native_tool_loop_executes_tool_and_completes(monkeypatch):
    """tool_call → ツール実行 → 継続 → 通常応答で正常終了する(native 経路)。"""
    events, completion_calls, tool_invocations = asyncio.run(
        _drive_tool_call_loop(monkeypatch, cap=None, followup_responses=["text_basic"])
    )

    # 実キャプチャの引数がパースされてツールに渡っている
    assert len(tool_invocations) == 1
    assert tool_invocations[0]["city"] == "札幌"

    # 継続リクエストは 1 回(その応答が通常応答なのでループ終了)
    assert len(completion_calls) == 1
    assert completion_calls[0]["tools"], "継続リクエストにも tools が付く"

    # エラーは出ず、done イベントで完了する
    assert _iteration_limit_errors(events) == []
    assert any(
        e.get("type") == "chat:completion" and e.get("data", {}).get("done")
        for e in events
    )


def test_native_tool_loop_parses_no_args_call(monkeypatch):
    """引数なし tool call(tool_call_no_args): arguments が空 {} でツールが実行される。

    実キャプチャでは Kimi は arguments を "{}" で流す(引数なしツールでも空文字では
    ない)。middleware は空 {} をそのままツールへ渡す(properties が空なので許可引数も空)。
    """
    invocations = []

    async def get_current_time(**kwargs):
        invocations.append(kwargs)
        return "12:00"

    clock_spec = {
        "name": "get_current_time",
        "description": "現在時刻を返す。",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }
    extra = {"get_current_time": {"spec": clock_spec, "callable": get_current_time}}

    events, completion_calls, _ = asyncio.run(
        _drive_tool_call_loop(
            monkeypatch,
            cap=None,
            followup_responses=["text_basic"],
            initial_fixture="tool_call_no_args",
            extra_tools=extra,
        )
    )

    # 引数なしでツールが 1 回実行され、渡る引数は空 dict
    assert len(invocations) == 1
    assert invocations[0] == {}

    # 継続リクエストは 1 回、エラーなく完了
    assert len(completion_calls) == 1
    assert _iteration_limit_errors(events) == []
    assert any(
        e.get("type") == "chat:completion" and e.get("data", {}).get("done")
        for e in events
    )


def test_native_tool_loop_parses_unicode_args_call(monkeypatch):
    """特殊文字入り引数(tool_call_unicode_args)が dict に正しくパースされる。

    実キャプチャでは Kimi は arguments を厳密 JSON(引用符 \\"、改行 \\n、
    バックスラッシュ \\\\ を正しくエスケープ、絵文字/日本語は生 UTF-8)で流す。
    middleware は json.loads を一次に試す(ast.literal_eval は Python リテラル風を
    出すモデル向けのフォールバック)。ここではパース結果の中身を固定する。
    """
    events, completion_calls, tool_invocations = asyncio.run(
        _drive_tool_call_loop(
            monkeypatch,
            cap=None,
            followup_responses=["text_basic"],
            initial_fixture="tool_call_unicode_args",
        )
    )

    assert len(tool_invocations) == 1
    args = tool_invocations[0]
    assert args["city"] == "札幌"
    # detail には引用符・改行・バックスラッシュ・絵文字が復元されている
    detail = args["detail"]
    assert '"寒い"' in detail  # 二重引用符
    assert "\n" in detail  # 実際の改行文字
    assert "\\" in detail  # バックスラッシュ
    assert "☃" in detail  # 絵文字

    assert len(completion_calls) == 1
    assert _iteration_limit_errors(events) == []


def test_sync_tool_does_not_block_event_loop():
    """sync の tool 関数はワーカースレッドで実行される(#28 前半)。

    従来はイベントループ上で直接呼んでおり、ブロッキングする tool の間
    サーバ全体のストリーミングが止まっていた。sync tool の実行中に
    イベントループ上の別タスクが進めることを固定する。
    """
    import time as time_mod

    from nucllei.utils.tools import get_async_tool_function_and_apply_extra_params

    def slow_sync_tool():
        time_mod.sleep(0.3)
        return "done"

    wrapped = get_async_tool_function_and_apply_extra_params(slow_sync_tool, {})

    async def run():
        ticks = []

        async def ticker():
            # sync tool の 0.3 秒の間にループが回っていれば tick が積もる
            for _ in range(10):
                ticks.append(time_mod.monotonic())
                await asyncio.sleep(0.02)

        result, _ = await asyncio.gather(wrapped(), ticker())
        return result, ticks

    result, ticks = asyncio.run(run())
    assert result == "done"
    # ループがブロックされていれば tick はほぼ同時刻に潰れる。
    # 0.3 秒のブロッキング中に 5 回以上刻めていること(= 並行に進んでいた)。
    spread = ticks[-1] - ticks[0]
    assert len(ticks) == 10
    assert spread > 0.1, f"event loop was blocked (tick spread {spread:.3f}s)"


def _sse_response_from_frames(frames: list[str]) -> StreamingResponse:
    """インライン組み立ての SSE(実キャプチャでは作れない異常系用)。"""

    async def gen():
        for frame in frames:
            yield frame + "\n"
        yield "data: [DONE]\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


def test_finish_tool_calls_without_parsed_calls_surfaces_error(monkeypatch):
    """finish_reason=tool_calls なのに tool call を 1 つも復元できなければエラーを出す(#18-2)。

    チャンク境界などでデルタを取りこぼした場合、従来は黙って通常終了していた
    (無音の失敗モード)。チャット内エラーとして可視化することを固定する。
    実キャプチャでは作れない異常系なので、finish フレームだけ実キャプチャと同形の
    インライン SSE を使う。
    """
    # content デルタ + finish(tool_calls)のみ — tool_calls デルタが 1 つも無い
    broken_stream = _sse_response_from_frames(
        [
            'data: {"choices":[{"index":0,"delta":{"reasoning_content":null,"role":"assistant","content":"呼びます"},"finish_reason":null}]}',
            'data: {"choices":[{"index":0,"delta":{"reasoning_content":null},"finish_reason":"tool_calls","matched_stop":163586}]}',
        ]
    )

    events, completion_calls, _ = asyncio.run(
        _drive_tool_call_loop(
            monkeypatch,
            cap=None,
            followup_responses=[],
            initial_response=broken_stream,
            omit_default_tool=True,
        )
    )

    error_events = [
        e["data"]["error"]["content"] for e in events if e.get("data", {}).get("error")
    ]
    assert any("no tool call could be reconstructed" in c for c in error_events)
    # tool call が組めていないのでループは回らない
    assert completion_calls == []


def test_normal_tool_call_stream_has_no_finish_mismatch_error(monkeypatch):
    """実キャプチャ(finish_reason=tool_calls + 正常デルタ)ではガードが発火しない。"""
    events, completion_calls, _ = asyncio.run(
        _drive_tool_call_loop(
            monkeypatch,
            cap=None,
            followup_responses=["text_basic"],
        )
    )
    error_events = [
        e["data"]["error"]["content"] for e in events if e.get("data", {}).get("error")
    ]
    assert error_events == []
    assert len(completion_calls) == 1


def test_unavailable_tool_call_returns_explicit_message(monkeypatch):
    """tools 配列に無い tool の呼び出しは明示メッセージを返す(#18-2)。

    履歴に残った過去の tool call を真似るケース(skill 削除・権限剥奪・分岐)で
    起きうる。黙って None を返すとモデルが状況を知れないため、軌道修正できる
    メッセージを tool result として返す(例外・打ち切りにはしない)。
    """
    events, completion_calls, tool_invocations = asyncio.run(
        _drive_tool_call_loop(
            monkeypatch,
            cap=None,
            followup_responses=["text_basic"],
            omit_default_tool=True,  # get_weather を tools から外す
        )
    )

    # ツールは実行されず、ループは 1 回の継続で完走する
    assert tool_invocations == []
    assert len(completion_calls) == 1
    assert _iteration_limit_errors(events) == []

    # 継続リクエストの tool result に明示メッセージが載る
    tool_messages = [
        m for m in completion_calls[0]["messages"] if m.get("role") == "tool"
    ]
    assert len(tool_messages) == 1
    assert tool_messages[0]["content"] == (
        "ツール get_weather は今は使えない (このターンの一覧に無い)。"
        "次の手: 一覧にある tool だけを使う。load_skill が使えて、skill の tool なら先に読み込む。"
    )


####################################
# tool call 表示ディスクリプタの同梱(issue #20)
####################################


RUN_PYTHON_RENDER = {"args": {"code": {"kind": "code", "language": "python"}}}


def _run_python_tool():
    """tool_call_long_code fixture(run_python 実キャプチャ)に対応するツール。"""

    async def run_python(**kwargs):
        return "ok"

    spec = {
        "name": "run_python",
        "description": "Python コードを実行する。",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "contentMediaType": "text/x-python"}
            },
            "required": ["code"],
        },
    }
    return {
        "run_python": {
            "spec": spec,
            "callable": run_python,
            "render": RUN_PYTHON_RENDER,
        }
    }


def test_tool_call_render_descriptor_rides_events_and_persistence(monkeypatch):
    """render(型駆動表示ディスクリプタ)が calls に同梱され、chat:completion
    イベントと DB 永続化の双方に同形で乗る(リロード時に spec 再取得不要)。"""
    upserts = []
    events, completion_calls, _ = asyncio.run(
        _drive_tool_call_loop(
            monkeypatch,
            cap=None,
            followup_responses=["text_basic"],
            initial_fixture="tool_call_long_code",
            extra_tools=_run_python_tool(),
            upserts=upserts,
        )
    )
    assert len(completion_calls) == 1

    final = _final_done_data(events)
    tool_blocks = [b for b in final["content_blocks"] if b["type"] == "tool_calls"]
    assert len(tool_blocks) == 1
    call = tool_blocks[0]["calls"][0]
    assert call["function"]["name"] == "run_python"
    assert call["render"] == RUN_PYTHON_RENDER

    # code 引数は複数行コードの JSON 文字列(フロントは render に従って
    # アンエスケープ + python ハイライトで描画する)
    args = json.loads(call["function"]["arguments"])
    assert "\n" in args["code"]

    persisted = [u for u in upserts if "content_blocks" in u]
    assert persisted[-1]["content_blocks"] == final["content_blocks"]


def test_streaming_snapshots_carry_partial_tool_call_arguments(monkeypatch):
    """ストリーム中の chat:completion に open な tool_calls ブロック(results 無し)
    が載り、部分 arguments が単調に伸びる(#20 ステップ5 = フロント逐次描画の前提)。"""
    events, completion_calls, _ = asyncio.run(
        _drive_tool_call_loop(
            monkeypatch,
            cap=None,
            followup_responses=["text_basic"],
            initial_fixture="tool_call_long_code",
            extra_tools=_run_python_tool(),
        )
    )

    open_calls = []  # (call dict) results 未着の snapshot 群
    for e in events:
        if e.get("type") != "chat:completion":
            continue
        blocks = e.get("data", {}).get("content_blocks")
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            if block["type"] == "tool_calls" and not block.get("results"):
                open_calls.append(block["calls"][0])

    assert open_calls, "ストリーム中の open tool_calls スナップショットが無い"

    # arguments は単調に伸びる(スナップショットの累積性)
    prev = ""
    for call in open_calls:
        args = call["function"]["arguments"]
        assert args.startswith(prev), "arguments が後退した"
        prev = args

    # 途中スナップショットには JSON として閉じていない状態が含まれる
    # (完成後にまとめて emit していない = 逐次 emit の証明)
    incomplete = 0
    for call in open_calls[:-1]:
        try:
            json.loads(call["function"]["arguments"])
        except Exception:
            incomplete += 1
    assert incomplete > 0

    # render(表示ディスクリプタ)は最初の snapshot から付与済み
    # (Kimi は初回フレームで id/name が確定する)
    assert open_calls[0]["render"] == RUN_PYTHON_RENDER

    # 最終スナップショットに tool_calls ブロックは 1 つだけ(open ブロックの再追加なし)
    final = _final_done_data(events)
    assert sum(1 for b in final["content_blocks"] if b["type"] == "tool_calls") == 1


def test_tool_call_without_declaration_has_no_render(monkeypatch):
    """宣言の無いツール(get_weather)の call に render は付かない
    (名前・長さヒューリスティックで推測しない)。"""
    events, _, _ = asyncio.run(
        _drive_tool_call_loop(monkeypatch, cap=None, followup_responses=["text_basic"])
    )
    blocks = _final_done_data(events)["content_blocks"]
    calls = [c for b in blocks if b["type"] == "tool_calls" for c in b["calls"]]
    assert calls
    assert all("render" not in c for c in calls)


def test_followup_tool_calls_carry_only_openai_keys(monkeypatch):
    """継続リクエストの assistant.tool_calls は OpenAI 形 {id, type, function}
    のみ(render / index などの内部キーをモデルへ漏らさない)。"""
    events, completion_calls, _ = asyncio.run(
        _drive_tool_call_loop(
            monkeypatch,
            cap=None,
            followup_responses=["text_basic"],
            initial_fixture="tool_call_long_code",
            extra_tools=_run_python_tool(),
        )
    )
    assistant = _assistant_tool_calls_message(completion_calls[0])
    assert assistant is not None
    for tc in assistant["tool_calls"]:
        assert set(tc.keys()) == {"id", "type", "function"}


####################################
# multi-turn 履歴の綴じ込み(フォローアップ request の messages を検査)
####################################


def _assistant_tool_calls_message(followup_form_data):
    """継続リクエストの messages から tool_calls を持つ assistant メッセージを返す。"""
    for m in followup_form_data["messages"]:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            return m
    return None


def _tool_messages(followup_form_data):
    return [m for m in followup_form_data["messages"] if m.get("role") == "tool"]


def test_followup_preserves_tool_call_history(monkeypatch):
    """継続リクエストに assistant(tool_calls)+ 対応する role:tool 結果が綴じ込まれる。

    tool 結果を text へ平坦化せず、tool_call_id で正しくリンクすることを固定する
    (これが崩れるとモデルが tool 出力を幻覚する)。
    """
    events, completion_calls, _ = asyncio.run(
        _drive_tool_call_loop(monkeypatch, cap=None, followup_responses=["text_basic"])
    )
    assert len(completion_calls) == 1
    msgs = completion_calls[0]["messages"]

    assistant = _assistant_tool_calls_message(completion_calls[0])
    assert assistant is not None, "assistant(tool_calls) が履歴に無い"
    tc = assistant["tool_calls"][0]
    assert tc["id"] == "functions.get_weather:0"
    assert tc["function"]["name"] == "get_weather"
    assert tc["function"].get("arguments")

    tool_msgs = _tool_messages(completion_calls[0])
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "functions.get_weather:0"
    # assistant の直後に対応する tool メッセージが来る(順序)
    assert msgs.index(tool_msgs[0]) == msgs.index(assistant) + 1


def test_followup_preserves_parallel_tool_calls(monkeypatch):
    """並列 tool call(tool_call_parallel): 1 assistant に 2 要素の tool_calls 配列 +
    id 対応の tool メッセージ 2 本が綴じ込まれる。"""
    events, completion_calls, tool_invocations = asyncio.run(
        _drive_tool_call_loop(
            monkeypatch,
            cap=None,
            followup_responses=["text_basic"],
            initial_fixture="tool_call_parallel",
        )
    )
    assert len(completion_calls) == 1

    assistant = _assistant_tool_calls_message(completion_calls[0])
    assert assistant is not None
    ids = [tc["id"] for tc in assistant["tool_calls"]]
    assert ids == ["functions.get_weather:0", "functions.get_weather:1"]

    tool_msgs = _tool_messages(completion_calls[0])
    assert [m["tool_call_id"] for m in tool_msgs] == [
        "functions.get_weather:0",
        "functions.get_weather:1",
    ]
    # 2 都市それぞれツールが実行されている
    assert len(tool_invocations) == 2


def test_content_between_parallel_calls_does_not_duplicate_tool_calls_block(
    monkeypatch,
):
    """並列 tool call の区切りに content デルタ(改行)を挟むストリームでも、
    tool_calls ブロックは 1 個だけ永続化され、継続リクエストに同じ call id の
    assistant(tool_calls)と [interrupted] の合成 result が混ざらない。

    実測(2026-10-02、qwen3.8-27b): 3 call のストリームで同じ calls のブロックが
    3 個永続化され(先頭 2 個は results: [])、同ターンの継続と次ターンの履歴に
    `a+3tc t t t` が 3 回([interrupted] 付き 2 回)再送されていた。実キャプチャの
    fixture が無い形なので、フレームはインラインで組む(call の形は vLLM の
    実キャプチャと同形 — 初回デルタに arguments キーが無い)。
    """
    from nucllei.utils.content_blocks import INTERRUPTED_TOOL_RESULT

    def frame(delta, finish_reason=None):
        return "data: " + json.dumps(
            {"choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}]},
            ensure_ascii=False,
        )

    cities = ["東京", "大阪", "札幌"]
    frames = []
    for index, city in enumerate(cities):
        frames += [
            frame({"content": "\n\n"}),
            frame(
                {
                    "tool_calls": [
                        {
                            "id": f"call-{index}",
                            "type": "function",
                            "index": index,
                            "function": {"name": "get_weather"},
                        }
                    ]
                }
            ),
            frame(
                {
                    "tool_calls": [
                        {
                            "index": index,
                            "function": {
                                "arguments": json.dumps(
                                    {"city": city}, ensure_ascii=False
                                )
                            },
                        }
                    ]
                }
            ),
        ]
    frames.append(frame({}, finish_reason="tool_calls"))

    upserts = []
    events, completion_calls, tool_invocations = asyncio.run(
        _drive_tool_call_loop(
            monkeypatch,
            cap=None,
            followup_responses=["text_basic"],
            initial_response=_sse_response_from_frames(frames),
            upserts=upserts,
        )
    )
    assert [inv["city"] for inv in tool_invocations] == cities
    assert len(completion_calls) == 1

    call_ids = ["call-0", "call-1", "call-2"]

    # 継続リクエスト: assistant(tool_calls)は 1 本、tool 結果は実物が 3 本だけ
    msgs = completion_calls[0]["messages"]
    assistants = [m for m in msgs if m.get("tool_calls")]
    assert len(assistants) == 1
    assert [tc["id"] for tc in assistants[0]["tool_calls"]] == call_ids
    tool_msgs = _tool_messages(completion_calls[0])
    assert [m["tool_call_id"] for m in tool_msgs] == call_ids
    assert all(m["content"] != INTERRUPTED_TOOL_RESULT for m in tool_msgs)

    # イベントと永続化: tool_calls ブロックは 1 個で、3 call 分の results を持つ
    final = _final_done_data(events)
    tool_blocks = [b for b in final["content_blocks"] if b["type"] == "tool_calls"]
    assert len(tool_blocks) == 1
    assert [c["id"] for c in tool_blocks[0]["calls"]] == call_ids
    assert [r["tool_call_id"] for r in tool_blocks[0]["results"]] == call_ids

    # 途中の永続化スナップショットにも重複ブロックは 1 度も現れない
    persisted = [u for u in upserts if "content_blocks" in u]
    assert persisted[-1]["content_blocks"] == final["content_blocks"]
    for snapshot in persisted:
        blocks = [b for b in snapshot["content_blocks"] if b["type"] == "tool_calls"]
        assert len(blocks) <= 1


def test_followup_reinjects_pushed_images_as_user_message(monkeypatch):
    """tool が __feed_image__(file_id) で push した画像(#89)は、継続リクエストで
    role:tool の直後に image_url(/api/v1/files/{id}/content 参照)を持つ user
    メッセージとして再投入される。要石: 継続メッセージにも
    inline_internal_image_urls が掛かる(ここでは fake で INLINED: マークを付け、
    追撃分に inline が適用されたことを固定する)。base64 は戻り境界に載らない。"""
    from nucllei.utils import middleware
    from nucllei.utils.tool_output import feed_image

    async def fake_inline(messages, user):
        # 追撃 messages に inline が掛かったことを証明するマーカ(内部 URL のみ)。
        for m in messages:
            if isinstance(m.get("content"), list):
                for p in m["content"]:
                    if p.get("type") == "image_url":
                        p["image_url"]["url"] = "INLINED:" + p["image_url"]["url"]
        return messages

    monkeypatch.setattr(middleware, "inline_internal_image_urls", fake_inline)

    async def get_weather(**kwargs):
        # 汎用 tool-push: 実行中 sink に file_id を積む(view_image でなくても使える)
        feed_image("img-file-1")
        return "天気図を提示しました。"

    spec = {
        "name": "get_weather",
        "description": "天気を返す",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    }
    events, completion_calls, _ = asyncio.run(
        _drive_tool_call_loop(
            monkeypatch,
            cap=None,
            followup_responses=["text_basic"],
            extra_tools={"get_weather": {"spec": spec, "callable": get_weather}},
        )
    )
    assert len(completion_calls) == 1
    msgs = completion_calls[0]["messages"]

    tool_msgs = _tool_messages(completion_calls[0])
    assert len(tool_msgs) == 1
    # role:tool の content は非空の status 文字列(画素は運ばない)
    assert tool_msgs[0]["content"] == "天気図を提示しました。"

    user_msg = msgs[msgs.index(tool_msgs[0]) + 1]
    assert user_msg["role"] == "user"
    injected = [
        p["image_url"]["url"]
        for p in user_msg["content"]
        if p.get("type") == "image_url"
    ]
    # file_id 参照が組まれ、追撃 inline(fake)が適用された
    assert injected == ["INLINED:/api/v1/files/img-file-1/content"]


def test_reasoning_not_leaked_into_followup_content(monkeypatch):
    """reasoning はモデルへの継続リクエストの assistant content に漏らさない(#18-2)。

    reasoning_then_tool は reasoning + 実応答 text + tool call を含む。継続リクエストの
    assistant メッセージ content には実応答 text だけが載り、UI 用の
    <details type="reasoning"> マークアップは含まれないこと。ただし UI への emit
    (chat:completion の content_blocks)には reasoning が残っていること。
    """
    events, completion_calls, _ = asyncio.run(
        _drive_tool_call_loop(
            monkeypatch,
            cap=None,
            followup_responses=["text_basic"],
            initial_fixture="reasoning_then_tool",
        )
    )
    assert len(completion_calls) == 1

    assistant = _assistant_tool_calls_message(completion_calls[0])
    assert assistant is not None
    # モデル向け履歴に reasoning の UI マークアップが漏れていない
    assert '<details type="reasoning"' not in (assistant.get("content") or "")

    # UI への emit(content_blocks)には reasoning が残っている(表示は維持)。
    # content 文字列は text join 派生値なので reasoning を含まない(#17 L0)
    final = _final_done_data(events)
    assert any(b["type"] == "reasoning" for b in final["content_blocks"])
    assert "<details" not in final["content"]


def test_open_reasoning_not_leaked_into_followup_content(monkeypatch):
    """content デルタを経ないケース(tool_call_no_args: reasoning から tool_calls へ
    直行)でも、継続リクエストの assistant content に reasoning が漏れない。"""

    async def get_current_time(**kwargs):
        return "12:00"

    clock_spec = {
        "name": "get_current_time",
        "description": "現在時刻を返す。",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }
    extra = {"get_current_time": {"spec": clock_spec, "callable": get_current_time}}

    events, completion_calls, _ = asyncio.run(
        _drive_tool_call_loop(
            monkeypatch,
            cap=None,
            followup_responses=["text_basic"],
            initial_fixture="tool_call_no_args",
            extra_tools=extra,
        )
    )
    assert len(completion_calls) == 1
    assistant = _assistant_tool_calls_message(completion_calls[0])
    assert assistant is not None
    content = assistant.get("content") or ""
    assert '<details type="reasoning"' not in content


def _reasoning_states(data):
    """final data の content_blocks から reasoning の done 状態を列挙する。"""
    return [b["done"] for b in data["content_blocks"] if b["type"] == "reasoning"]


def test_reasoning_closes_when_tool_calls_arrive_without_content(monkeypatch):
    """reasoning から content を経ず tool_calls へ直行しても「Thinking…」
    (done=false)が残らない(思考中が開きっぱなしになる問題)。

    tool_call_no_args: reasoning デルタの直後に tool_calls が来る実キャプチャ。
    閉じ条件が content 到着だけだと reasoning が done=false のまま永続化される。
    """

    async def get_current_time(**kwargs):
        return "12:00"

    clock_spec = {
        "name": "get_current_time",
        "description": "現在時刻を返す。",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }
    extra = {"get_current_time": {"spec": clock_spec, "callable": get_current_time}}

    events, completion_calls, _ = asyncio.run(
        _drive_tool_call_loop(
            monkeypatch,
            cap=None,
            followup_responses=["text_basic"],
            initial_fixture="tool_call_no_args",
            extra_tools=extra,
        )
    )
    assert len(completion_calls) == 1

    states = _reasoning_states(_final_done_data(events))
    assert states and all(states), states


def test_reasoning_closes_at_stream_end_on_client_cut(monkeypatch):
    """reasoning 途中でストリームが終端(切断)しても「Thinking…」が残らない。

    aborted_client_cut: reasoning デルタ 4 フレームで切断された実キャプチャ
    (finish/usage/[DONE] 無し)。ストリーム終端で reasoning を閉じる。
    """
    events, completion_calls, _ = asyncio.run(
        _drive_tool_call_loop(
            monkeypatch,
            cap=None,
            followup_responses=[],
            initial_fixture="aborted_client_cut",
        )
    )
    # tool call は発生しない(継続リクエスト無し)
    assert len(completion_calls) == 0

    states = _reasoning_states(_final_done_data(events))
    assert states and all(states), states


def test_tool_call_iteration_cap_emits_chat_error(monkeypatch):
    """上限到達時に黙って止まらず chat:completion エラーを emit する。"""
    events, completion_calls, tool_invocations = asyncio.run(
        _drive_tool_call_loop(monkeypatch, cap=2, followup_responses=[])
    )

    # 上限(2)まで反復して停止
    assert len(completion_calls) == 2
    assert len(tool_invocations) == 2

    errors = _iteration_limit_errors(events)
    assert len(errors) == 1
    assert "(2)" in errors[0]
    assert "TOOL_CALL_MAX_ITERATIONS" in errors[0]


def test_tool_call_iteration_cap_minus_one_is_unlimited(monkeypatch):
    """-1 は無制限: 既定の旧上限(10)を超えても継続し、正常終了する。"""
    followups = ["tool_call"] * 12 + ["text_basic"]
    events, completion_calls, tool_invocations = asyncio.run(
        _drive_tool_call_loop(monkeypatch, cap=-1, followup_responses=followups)
    )

    # 初回 + 12 回の tool_call をすべて処理し切っている
    assert len(tool_invocations) == 13
    assert len(completion_calls) == 13
    assert _iteration_limit_errors(events) == []
    assert any(
        e.get("type") == "chat:completion" and e.get("data", {}).get("done")
        for e in events
    )


####################################
# ツールは常に native 経路でモデルへ届く(HTTP 統合)
####################################


class MockOpenAIHandler(BaseHTTPRequestHandler):
    """最小の OpenAI 互換サーバ(/v1/models, /v1/chat/completions)。"""

    def log_message(self, *args):
        pass

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") == "/v1/models":
            self._json(
                {
                    "object": "list",
                    "data": [
                        {
                            "id": "mock-model",
                            "object": "model",
                            "created": 0,
                            "owned_by": "mock",
                        }
                    ],
                }
            )
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        if self.path.rstrip("/") == "/v1/chat/completions":
            self.server.received.append(payload)
            self._json(
                {
                    "id": "chatcmpl-mock",
                    "object": "chat.completion",
                    "created": 0,
                    "model": payload.get("model"),
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "ok"},
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                }
            )
        else:
            self._json({"error": "not found"}, 404)


CLOCK_TOOL_CONTENT = '''
class Tools:
    def get_current_time(self) -> str:
        """現在時刻を返す。"""
        return "12:00"
'''

CLOCK_TOOL_SPEC = {
    "name": "get_current_time",
    "description": "現在時刻を返す。",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

RUN_PYTHON_TOOL_CONTENT = '''
from nucllei.utils.tool_render import Code

class Tools:
    def run_python(self, code: Code("python")) -> str:
        """Python コードを実行する。
        :param code: 実行するコード
        """
        return "ok"
'''

OPTIONAL_PARAM_TOOL_CONTENT = '''
from typing import Optional

class Tools:
    def run_with_timeout(self, code: str, timeout_s: Optional[int] = None) -> str:
        """timeout 付きで実行する。
        :param code: 実行するコード
        :param timeout_s: wall clock 秒数
        """
        return "ok"
'''


class TestToolsAlwaysNative(AbstractPostgresTest):
    BASE_PATH = "/api"

    @classmethod
    def setup_class(cls):
        super().setup_class()
        cls.mock_server = ThreadingHTTPServer(("127.0.0.1", 0), MockOpenAIHandler)
        cls.mock_server.received = []
        cls.server_thread = threading.Thread(
            target=cls.mock_server.serve_forever, daemon=True
        )
        cls.server_thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.mock_server.server_address[1]}/v1"

        from nucllei.main import app

        app.state.config.ENABLE_OPENAI_API = True
        app.state.config.ENABLE_OLLAMA_API = False
        app.state.config.OPENAI_API_BASE_URLS = [cls.base_url]
        app.state.config.OPENAI_API_KEYS = ["sk-dummy"]
        app.state.config.OPENAI_API_CONFIGS = {}

    @classmethod
    def teardown_class(cls):
        cls.mock_server.shutdown()
        cls.mock_server.server_close()
        super().teardown_class()

    def setup_method(self):
        super().setup_method()
        from nucllei.main import app

        app.state.MODELS = {}
        app.state.OPENAI_MODELS = {}
        app.state.TOOLS = {}
        self.mock_server.received.clear()

    def _register_clock_tool(self):
        from nucllei.models.tools import ToolForm, Tools

        Tools.insert_new_tool(
            user_id="1",
            form_data=ToolForm(
                id="clock",
                name="clock",
                content=CLOCK_TOOL_CONTENT,
            ),
            specs=[dict(CLOCK_TOOL_SPEC)],
        )

    def _bind_tools_to_model(self, tool_ids):
        """llens(#54): tool はモデル割り当て(meta.toolIds)からサーバー側解決。
        mock-model に custom model 行を重ねて toolIds を持たせる。"""
        from nucllei.models.models import ModelForm, Models

        Models.insert_new_model(
            ModelForm(
                id="mock-model",
                name="mock-model",
                meta={"toolIds": tool_ids},
                params={},
                access_control=None,
            ),
            "1",
        )

    def test_model_assigned_tools_flow_to_model_as_native_tools(self):
        """モデル割り当て(meta.toolIds)の tool が native 形式で payload に載る。

        旧実装の既定(非 native)ではタスクモデルへの function_calling
        プロンプト呼び出しが挟まり、payload に tools は載らなかった。
        #54 以降、tool_ids はサーバー側解決(クライアント申告は廃止)。
        """
        self._register_clock_tool()
        self._bind_tools_to_model(["clock"])

        with mock_webui_user(id="1", role="admin"):
            response = self.fast_api_client.post(
                "/api/chat/completions",
                json={
                    "model": "mock-model",
                    "messages": [{"role": "user", "content": "何時?"}],
                    "stream": False,
                },
            )
        assert response.status_code == 200, response.text

        # 余計なプロンプトベース function_calling 呼び出しが無い(1 リクエストのみ)
        assert len(self.mock_server.received) == 1
        sent = self.mock_server.received[0]

        # tool spec が native 形式でそのまま届く
        assert "tools" in sent, sent.keys()
        assert [t["type"] for t in sent["tools"]] == ["function"]
        assert sent["tools"][0]["function"]["name"] == "get_current_time"

        # 旧経路のプロンプト注入("Available Tools:")が消えている
        for message in sent["messages"]:
            assert "Available Tools:" not in (message.get("content") or "")

    def test_client_supplied_tool_ids_are_ignored(self):
        """llens(#54): payload の tool_ids は無視される(チャット単位切り替えの撤去)。

        tool は実在し read 可能でも、モデルに割り当てられていなければ載らない —
        tool の有効範囲を決めるのはモデル設定のみ。
        """
        self._register_clock_tool()
        # モデルへの割り当ては行わない

        with mock_webui_user(id="1", role="admin"):
            response = self.fast_api_client.post(
                "/api/chat/completions",
                json={
                    "model": "mock-model",
                    "messages": [{"role": "user", "content": "何時?"}],
                    "stream": False,
                    "tool_ids": ["clock"],
                },
            )
        assert response.status_code == 200, response.text

        sent = self.mock_server.received[-1]
        assert "tools" not in sent, sent.get("tools")

    def test_render_declaration_reaches_model_payload_at_runtime(self):
        """Annotated(Code)宣言が DB 保存 spec に無くても、get_tools が生きた関数
        から抽出してモデル payload に contentMediaType を載せる(#20)。

        宣言追加前に保存されたツール(stale spec)でも再保存なしで効くことの固定。
        """
        from nucllei.models.tools import ToolForm, Tools

        Tools.insert_new_tool(
            user_id="1",
            form_data=ToolForm(
                id="runpy",
                name="runpy",
                content=RUN_PYTHON_TOOL_CONTENT,
            ),
            # contentMediaType の無い(宣言追加前の)保存 spec を意図的に登録
            specs=[
                {
                    "name": "run_python",
                    "description": "Python コードを実行する。",
                    "parameters": {
                        "type": "object",
                        "properties": {"code": {"type": "string"}},
                        "required": ["code"],
                    },
                }
            ],
        )

        self._bind_tools_to_model(["runpy"])

        with mock_webui_user(id="1", role="admin"):
            response = self.fast_api_client.post(
                "/api/chat/completions",
                json={
                    "model": "mock-model",
                    "messages": [{"role": "user", "content": "1+1 を計算して"}],
                    "stream": False,
                },
            )
        assert response.status_code == 200, response.text

        assert len(self.mock_server.received) == 1
        sent = self.mock_server.received[0]
        props = sent["tools"][0]["function"]["parameters"]["properties"]
        assert props["code"]["contentMediaType"] == "text/x-python"

    def test_optional_param_spec_without_type_key_loads(self):
        """Optional 引数の保存 spec は anyOf 形で "type" キーを持たない。

        get_tools のリクエスト時正規化(str→string hack)が val["type"] を直参照して
        KeyError で全リクエストが落ちていた(#18-3: sandbox tool の timeout_s で顕在化)。
        """
        from nucllei.models.tools import ToolForm, Tools

        Tools.insert_new_tool(
            user_id="1",
            form_data=ToolForm(
                id="optpy",
                name="optpy",
                content=OPTIONAL_PARAM_TOOL_CONTENT,
            ),
            specs=[
                {
                    "name": "run_with_timeout",
                    "description": "timeout 付きで実行する。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "string"},
                            "timeout_s": {
                                "anyOf": [{"type": "integer"}, {"type": "null"}]
                            },
                        },
                        "required": ["code"],
                    },
                }
            ],
        )

        self._bind_tools_to_model(["optpy"])

        with mock_webui_user(id="1", role="admin"):
            response = self.fast_api_client.post(
                "/api/chat/completions",
                json={
                    "model": "mock-model",
                    "messages": [{"role": "user", "content": "実行して"}],
                    "stream": False,
                },
            )
        assert response.status_code == 200, response.text

        sent = self.mock_server.received[-1]
        names = [t["function"]["name"] for t in sent["tools"]]
        assert "run_with_timeout" in names


####################################
# content_blocks イベント契約(issue #17 L0)
####################################


def _final_done_data(events):
    """最終 chat:completion(done=True)イベントの data を返す。"""
    finals = [
        e["data"]
        for e in events
        if e.get("type") == "chat:completion" and e.get("data", {}).get("done")
    ]
    assert finals, "done=True の最終イベントが emit されていない"
    return finals[-1]


def _joined_deltas(fixture_name, key):
    """fixture のデルタ(content / reasoning_content)を結合した生テキスト。"""
    return "".join(
        d.get(key) or "" for d, _ in mf.iter_deltas(mf.load_frames(fixture_name))
    )


def test_content_blocks_final_event_reasoning_and_text(monkeypatch):
    """最終イベントに content_blocks(累積スナップショット)が載る。reasoning は
    done=true + duration、text はモデルの散文がそのまま(join 破壊なし)入る。"""
    events, completion_calls, _ = asyncio.run(
        _drive_tool_call_loop(
            monkeypatch, cap=None, followup_responses=[], initial_fixture="reasoning"
        )
    )
    assert len(completion_calls) == 0

    final = _final_done_data(events)
    blocks = final.get("content_blocks")
    assert blocks is not None
    assert [b["type"] for b in blocks] == ["reasoning", "text"]

    reasoning, text = blocks
    assert reasoning["done"] is True
    # llens(#100): duration は小数(int 切り捨てだと 1 秒未満が全部 0 秒になる)。
    # 丸めは表示側の責務なので、ここは非負の実数であることだけを固定する。
    assert isinstance(reasoning["duration"], float)
    assert reasoning["duration"] >= 0
    assert reasoning["content"] == _joined_deltas("reasoning", "reasoning_content")
    # text はデルタ結合そのまま(終端で末尾ブロックの両端 strip のみ)
    assert text["content"] == _joined_deltas("reasoning", "content").strip()
    # content 文字列は text ブロック join の派生値(details 直列化はしない)
    assert final["content"] == text["content"]


def test_content_blocks_streaming_snapshot_marks_open_reasoning(monkeypatch):
    """ストリーミング途中のイベントにも content_blocks が載り、未確定の
    reasoning は done=false で表現される。"""
    events, _, _ = asyncio.run(
        _drive_tool_call_loop(
            monkeypatch, cap=None, followup_responses=[], initial_fixture="reasoning"
        )
    )
    open_reasoning_seen = any(
        any(
            b["type"] == "reasoning" and b["done"] is False
            for b in e["data"]["content_blocks"]
        )
        for e in events
        if e.get("type") == "chat:completion"
        and isinstance(e.get("data", {}).get("content_blocks"), list)
    )
    assert open_reasoning_seen


def test_content_blocks_tool_call_turn_structure(monkeypatch):
    """tool call ターンを跨いだブロック列が構造のまま届く:
    reasoning → text → tool_calls(calls + results)→ reasoning → text。"""
    events, completion_calls, _ = asyncio.run(
        _drive_tool_call_loop(
            monkeypatch,
            cap=None,
            followup_responses=["text_basic"],
            initial_fixture="reasoning_then_tool",
        )
    )
    assert len(completion_calls) == 1

    blocks = _final_done_data(events)["content_blocks"]
    assert [b["type"] for b in blocks] == [
        "reasoning",
        "text",
        "tool_calls",
        "reasoning",
        "text",
    ]

    # reasoning_then_tool は並列 2 call の実キャプチャ(index 順直列)
    tool_calls = blocks[2]
    assert [c["function"]["name"] for c in tool_calls["calls"]] == [
        "get_weather",
        "get_weather",
    ]
    assert {r["tool_call_id"] for r in tool_calls["results"]} == {
        c["id"] for c in tool_calls["calls"]
    }


def test_content_blocks_persisted_with_message(monkeypatch):
    """最終 upsert(DB 永続化)にもイベントと同形の content_blocks が載る
    (#27: blob 続投 + message 内 content_blocks)。"""
    upserts = []
    events, _, _ = asyncio.run(
        _drive_tool_call_loop(
            monkeypatch,
            cap=None,
            followup_responses=[],
            initial_fixture="reasoning",
            upserts=upserts,
        )
    )
    persisted = [u for u in upserts if "content_blocks" in u]
    assert persisted, "content_blocks が永続化されていない"
    assert persisted[-1]["content_blocks"] == _final_done_data(events)["content_blocks"]


####################################
# wire v2 の emit 規約(初回スナップショット + delta イベント + 構造スナップショット)
####################################


def _completion_datas(events):
    """chat:completion イベントの data 列。"""
    return [e["data"] for e in events if e.get("type") == "chat:completion"]


def _snapshot_datas(events):
    """content_blocks を含むスナップショットイベントの data 列。"""
    return [d for d in _completion_datas(events) if "content_blocks" in d]


def _delta_datas(events):
    """純追記の delta イベントのペイロード列。"""
    return [d["delta"] for d in _completion_datas(events) if "delta" in d]


def _patch_snapshot_interval(monkeypatch, value):
    from nucllei.utils import middleware

    monkeypatch.setattr(middleware, "SNAPSHOT_INTERVAL", value)


def test_wire_v2_pure_append_flows_as_delta_events(monkeypatch):
    """純追記はスナップショットではなく delta イベントで流れる(O(n²) 解消の回帰)。

    text_basic(reasoning 多数フレーム + text 1 フレーム)で、content_blocks
    スナップショットは構造変化点(初回・reasoning 開始・text 開始・done)の
    4 つだけになり、間の追記はすべて {"delta": {...}} で届く。
    """
    # スロットルスナップショットを無効化して構造点だけにする(遅マシン耐性)
    _patch_snapshot_interval(monkeypatch, 10**9)
    events, completion_calls, _ = asyncio.run(
        _drive_tool_call_loop(
            monkeypatch, cap=None, followup_responses=[], initial_fixture="text_basic"
        )
    )
    assert completion_calls == []

    # 最初の chat:completion はストリーム開始時の無条件スナップショット
    completions = _completion_datas(events)
    assert "content_blocks" in completions[0]

    snapshots = _snapshot_datas(events)
    assert len(snapshots) == 4  # 初回 + reasoning 開始 + text 開始 + done
    assert snapshots[-1].get("done") is True

    # 追記の主流は delta イベント(reasoning 開始フレームの中身は構造
    # スナップショット側に載るため、delta は残り全フレーム分)
    frames = mf.load_frames("text_basic")
    reasoning_frames = [
        d.get("reasoning_content")
        for d, _ in mf.iter_deltas(frames)
        if d.get("reasoning_content")
    ]
    deltas = _delta_datas(events)
    assert [d["block"] for d in deltas] == ["reasoning"] * (len(reasoning_frames) - 1)

    # 構造スナップショットの内容 + delta の結合 = fixture のデルタ結合(取りこぼしなし)
    open_reasoning = snapshots[1]["content_blocks"][-1]
    assert open_reasoning["type"] == "reasoning"
    joined = open_reasoning["content"] + "".join(d["content"] for d in deltas)
    assert joined == _joined_deltas("text_basic", "reasoning_content")

    # text は 1 フレーム(text ブロック開き = 構造スナップショット)なので delta 無し
    final = _final_done_data(events)
    assert final["content"] == _joined_deltas("text_basic", "content").strip()


def test_wire_v2_reasoning_boundaries_are_structural_snapshots(monkeypatch):
    """reasoning の開始/終了は構造スナップショット、間は delta(block=reasoning)。

    列: 初回スナップショット → reasoning 開始(done=false スナップショット)→
    reasoning delta × N → text 開始(reasoning done=true スナップショット)→
    text delta × M → done。
    """
    _patch_snapshot_interval(monkeypatch, 10**9)
    events, _, _ = asyncio.run(
        _drive_tool_call_loop(
            monkeypatch, cap=None, followup_responses=[], initial_fixture="reasoning"
        )
    )

    # usage イベントは順序検査の対象外
    seq = [d for d in _completion_datas(events) if "usage" not in d]

    assert "content_blocks" in seq[0]  # 初回スナップショット

    # reasoning 開始 = 構造スナップショット(open reasoning)
    assert "content_blocks" in seq[1]
    opened = seq[1]["content_blocks"][-1]
    assert opened["type"] == "reasoning" and opened["done"] is False

    # 間は reasoning delta のみ
    i = 2
    while "delta" in seq[i]:
        assert seq[i]["delta"]["block"] == "reasoning"
        i += 1
    assert i > 2, "reasoning delta イベントが無い"

    # text 開始 = reasoning を閉じた構造スナップショット
    blocks = seq[i]["content_blocks"]
    assert [b["type"] for b in blocks] == ["reasoning", "text"]
    assert blocks[0]["done"] is True

    # 以降 done まで text delta のみ
    i += 1
    while "delta" in seq[i]:
        assert seq[i]["delta"]["block"] == "text"
        i += 1
    assert seq[i].get("done") is True
    assert i == len(seq) - 1


def test_wire_v2_snapshot_interval_zero_snapshots_every_delta(monkeypatch):
    """SNAPSHOT_INTERVAL=0 では毎デルタがスナップショット + DB 永続化になる
    (スロットルの下限動作 = 途中参加収束・取りこぼし上限の保証)。"""
    _patch_snapshot_interval(monkeypatch, 0)
    upserts = []
    events, _, _ = asyncio.run(
        _drive_tool_call_loop(
            monkeypatch,
            cap=None,
            followup_responses=[],
            initial_fixture="text_basic",
            upserts=upserts,
        )
    )

    # delta イベントは 1 つも出ない(全て snapshot に置き換わる)
    assert _delta_datas(events) == []

    frames = mf.load_frames("text_basic")
    changed = [
        d
        for d, _ in mf.iter_deltas(frames)
        if d.get("reasoning_content") or d.get("content")
    ]
    # 初回(persist なし)+ 変更フレームごと + done で snapshot が並ぶ
    assert len(_snapshot_datas(events)) == len(changed) + 2
    # 変更フレームごとに永続化される(+ 終端の最終スナップショット)
    persisted = [u for u in upserts if "content_blocks" in u]
    assert len(persisted) == len(changed) + 1


####################################
# 非 stream 応答の正規化(ingest_model_response_json)
####################################


def test_non_streaming_response_ingested_as_done_snapshot(monkeypatch):
    """非 stream JSON 応答(実キャプチャ text_basic.json)が ingest 経由で
    合成 1 フレーム化され、スナップショット + done で届く(契約は stream と同一)。"""
    events, completion_calls, _ = asyncio.run(
        _drive_tool_call_loop(
            monkeypatch,
            cap=None,
            followup_responses=[],
            initial_response=mf.load_response("text_basic"),
        )
    )
    assert completion_calls == []
    assert _delta_datas(events) == []

    response = mf.load_response("text_basic")
    message = response["choices"][0]["message"]

    final = _final_done_data(events)
    assert [b["type"] for b in final["content_blocks"]] == ["reasoning", "text"]
    reasoning, text = final["content_blocks"]
    assert reasoning["done"] is True
    assert reasoning["content"] == message["reasoning_content"]
    assert text["content"] == message["content"].strip()
    assert final["content"] == message["content"].strip()


def test_non_streaming_tool_call_response_drives_tool_loop(monkeypatch):
    """非 stream 応答の tool_calls(tool_call.json)でも tool ループが機能する
    (index 無し完成形 tool_calls の合成 index 込み)。"""
    events, completion_calls, tool_invocations = asyncio.run(
        _drive_tool_call_loop(
            monkeypatch,
            cap=None,
            followup_responses=["text_basic"],
            initial_response=mf.load_response("tool_call"),
        )
    )

    # 実キャプチャの引数がパースされてツールに渡り、継続 1 回で完走する
    assert len(tool_invocations) == 1
    assert tool_invocations[0]["city"] == "札幌"
    assert len(completion_calls) == 1
    assert _iteration_limit_errors(events) == []
    assert _final_done_data(events)
