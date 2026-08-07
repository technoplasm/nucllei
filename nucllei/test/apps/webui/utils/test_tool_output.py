"""tool 出力 sink(#89 画像 / #88 display)と各経路の回帰。

- feed_image は実行中 sink に file_id を積む。sink 未設定(文脈外)では no-op。
- execute_tool_calls: __feed_image__ を呼ぶ tool の結果に context_images が載り、
  呼ばない tool には載らない(型付きトリガのみで発火)。
- convert_content_blocks_to_messages: context_images の file_id から
  /api/v1/files/{id}/content の image_url user メッセージを 1 つ生成する。
- display(#88、人間の目): __display__ を呼ぶ tool の結果に display が載る。
  feed_image と対称の sink 経路だが、context_images と違い **モデルへ再注入しない**
  (convert が display を読まない)— 2 チャネル分離の本体。
- progress(#100、実行中の人間の目): __progress__ は貯めずに即時 emit して捨てる
  (揮発)。正規化・スロットル・close 後の遮断、async/sync 双方からの呼び出し、
  results に残らないこと、所要時間(duration)が成否に関わらず載ることを固定する。
"""

import asyncio

from nucllei.utils import tool_output
from nucllei.utils.tool_output import ToolOutputSink, feed_image, display, progress
from nucllei.utils.middleware import execute_tool_calls
from nucllei.utils.content_blocks import convert_content_blocks_to_messages


def test_feed_image_noop_outside_tool_context():
    # sink 未設定では黙って no-op(pipe/filter 等で安全)
    assert tool_output.current_sink.get() is None
    feed_image("x")  # 例外にならない


def test_sink_collects_under_context():
    sink = ToolOutputSink()
    token = tool_output.current_sink.set(sink)
    try:
        feed_image("a")
        feed_image("b")
        feed_image("")  # 空は無視
    finally:
        tool_output.current_sink.reset(token)
    assert sink.context_images() == ["a", "b"]


def _tool(callable_, name="probe"):
    return {
        name: {
            "callable": callable_,
            "spec": {"name": name, "parameters": {"properties": {}}},
        }
    }


def _call(name="probe"):
    return [
        {"id": f"functions.{name}:0", "function": {"name": name, "arguments": "{}"}}
    ]


def test_execute_tool_calls_harvests_context_images():
    async def shower():
        feed_image("file-123")
        return "画像を提示しました。"

    results = asyncio.run(execute_tool_calls(_call(), _tool(shower)))
    assert results[0]["content"] == "画像を提示しました。"
    assert results[0]["context_images"] == ["file-123"]


def test_execute_tool_calls_no_vlm_key_when_not_pushed():
    async def plain():
        return "ok"

    results = asyncio.run(execute_tool_calls(_call(), _tool(plain)))
    assert "context_images" not in results[0]


def test_failed_tool_push_is_not_harvested():
    """push した後に例外を投げた tool の画像は再注入しない(失敗 call は副産物を
    出さない — content=エラー文言と画像が混在する不整合を避ける)。"""

    async def boom():
        feed_image("orphan-img")
        raise RuntimeError("kaboom")

    results = asyncio.run(execute_tool_calls(_call(), _tool(boom)))
    assert "context_images" not in results[0]
    assert "kaboom" in results[0]["content"]


def test_sync_tool_sink_propagates_via_to_thread():
    """sync tool(#28 で asyncio.to_thread のワーカースレッド実行)から呼ばれた
    feed_image が sink に届くことを固定する。ContextVar が to_thread の
    copy_context でワーカースレッドへ伝播する load-bearing な前提の回帰
    (to_thread を copy_context を通さない実装へ退行させると壊れる)。"""

    def sync_tool():  # sync 関数 = tools.py が to_thread でラップする対象を模擬
        feed_image("sync-img")
        return "done"

    async def wrapper():
        return await asyncio.to_thread(sync_tool)

    results = asyncio.run(execute_tool_calls(_call(), _tool(wrapper)))
    assert results[0]["context_images"] == ["sync-img"]
    assert results[0]["content"] == "done"


def test_sink_isolated_per_tool_call():
    async def shower():
        feed_image("img-A")
        return "a"

    async def plain():
        return "b"

    calls = [
        {"id": "functions.probe:0", "function": {"name": "probe", "arguments": "{}"}},
        {"id": "functions.plain:0", "function": {"name": "plain", "arguments": "{}"}},
    ]
    tools = {**_tool(shower, "probe"), **_tool(plain, "plain")}
    results = asyncio.run(execute_tool_calls(calls, tools))
    assert results[0]["context_images"] == ["img-A"]
    assert "context_images" not in results[1]


def test_convert_builds_image_url_from_context_images():
    blocks = [
        {"type": "text", "content": "見て"},
        {
            "type": "tool_calls",
            "content": [
                {"id": "c1", "type": "function", "function": {"name": "view_image"}}
            ],
            "results": [
                {
                    "tool_call_id": "c1",
                    "content": "提示しました。",
                    "context_images": ["abc"],
                }
            ],
        },
    ]
    msgs = convert_content_blocks_to_messages(blocks)
    # 末尾に image_url を持つ user メッセージが 1 つ
    user_imgs = [
        m for m in msgs if m["role"] == "user" and isinstance(m["content"], list)
    ]
    assert len(user_imgs) == 1
    parts = user_imgs[0]["content"]
    urls = [p["image_url"]["url"] for p in parts if p.get("type") == "image_url"]
    assert urls == ["/api/v1/files/abc/content"]
    # data: を直接載せない(実体化は送信直前 inline)
    assert all(not u.startswith("data:") for u in urls)


def test_convert_context_images_mixed_file_id_and_ext_url():
    # #97: "/" 始まりのエントリ("/ext/{name}/..." 等)はそのまま URL、
    # それ以外は従来通り file_id として /api/v1/files/{id}/content に整形
    blocks = [
        {
            "type": "tool_calls",
            "content": [
                {"id": "c1", "type": "function", "function": {"name": "read_doc"}}
            ],
            "results": [
                {
                    "tool_call_id": "c1",
                    "content": "図を提示しました。",
                    "context_images": ["abc", "/ext/cage/figs/a.png"],
                }
            ],
        },
    ]
    msgs = convert_content_blocks_to_messages(blocks)
    user_imgs = [
        m for m in msgs if m["role"] == "user" and isinstance(m["content"], list)
    ]
    assert len(user_imgs) == 1
    urls = [
        p["image_url"]["url"]
        for p in user_imgs[0]["content"]
        if p.get("type") == "image_url"
    ]
    assert urls == ["/api/v1/files/abc/content", "/ext/cage/figs/a.png"]


def test_convert_no_user_message_without_context_images():
    blocks = [
        {
            "type": "tool_calls",
            "content": [
                {"id": "c1", "type": "function", "function": {"name": "probe"}}
            ],
            "results": [{"tool_call_id": "c1", "content": "ok"}],
        },
    ]
    msgs = convert_content_blocks_to_messages(blocks)
    assert not any(m["role"] == "user" and isinstance(m["content"], list) for m in msgs)


# --- display チャネル(#88、人間の目) -------------------------------------


def test_display_noop_outside_tool_context():
    # sink 未設定では黙って no-op(pipe/filter 等で安全)
    assert tool_output.current_sink.get() is None
    display("見せたい")  # 例外にならない


def test_sink_collects_display_under_context():
    sink = ToolOutputSink()
    token = tool_output.current_sink.set(sink)
    try:
        display("<audio controls></audio>")
        display("行2", "markdown")
        display("")  # 空 content は無視
        display("bad-kind", 123)  # 不正 kind は markdown へ正規化
    finally:
        tool_output.current_sink.reset(token)
    assert sink.displays() == [
        {"kind": "markdown", "content": "<audio controls></audio>"},
        {"kind": "markdown", "content": "行2"},
        {"kind": "markdown", "content": "bad-kind"},
    ]


def test_displays_snapshot_is_detached():
    # displays() は要素 dict を copy して返す(生参照を results へ漏らさない)
    sink = ToolOutputSink()
    sink.add_display("x")
    snap = sink.displays()
    snap[0]["content"] = "mutated"
    assert sink.displays()[0]["content"] == "x"


def test_execute_tool_calls_harvests_display():
    async def shower():
        display("プレイヤーを表示しました。")
        return "OK(表示済み)"

    results = asyncio.run(execute_tool_calls(_call(), _tool(shower)))
    assert results[0]["content"] == "OK(表示済み)"
    assert results[0]["display"] == [
        {"kind": "markdown", "content": "プレイヤーを表示しました。"}
    ]


def test_execute_tool_calls_no_display_key_when_not_pushed():
    async def plain():
        return "ok"

    results = asyncio.run(execute_tool_calls(_call(), _tool(plain)))
    assert "display" not in results[0]


def test_failed_tool_push_display_not_harvested():
    """push した後に例外を投げた tool の display は載せない(失敗 call は副産物を
    出さない — context_images と同じ規律)。"""

    async def boom():
        display("途中まで")
        raise RuntimeError("kaboom")

    results = asyncio.run(execute_tool_calls(_call(), _tool(boom)))
    assert "display" not in results[0]
    assert "kaboom" in results[0]["content"]


def test_sync_tool_display_propagates_via_to_thread():
    """sync tool(#28 の to_thread ワーカースレッド実行)から呼ばれた display が
    sink に届く(ContextVar が copy_context で伝播する前提の回帰)。"""

    def sync_tool():
        display("sync-disp")
        return "done"

    async def wrapper():
        return await asyncio.to_thread(sync_tool)

    results = asyncio.run(execute_tool_calls(_call(), _tool(wrapper)))
    assert results[0]["display"] == [{"kind": "markdown", "content": "sync-disp"}]
    assert results[0]["content"] == "done"


def test_display_and_context_images_coexist_on_same_result():
    """1 tool が両チャネルへ push した場合、両キーが独立に載る。"""

    async def both():
        feed_image("file-9")
        display("見せる")
        return "ok"

    results = asyncio.run(execute_tool_calls(_call(), _tool(both)))
    assert results[0]["context_images"] == ["file-9"]
    assert results[0]["display"] == [{"kind": "markdown", "content": "見せる"}]


def test_display_isolated_per_tool_call():
    async def shower():
        display("A")
        return "a"

    async def plain():
        return "b"

    calls = [
        {"id": "functions.probe:0", "function": {"name": "probe", "arguments": "{}"}},
        {"id": "functions.plain:0", "function": {"name": "plain", "arguments": "{}"}},
    ]
    tools = {**_tool(shower, "probe"), **_tool(plain, "plain")}
    results = asyncio.run(execute_tool_calls(calls, tools))
    assert results[0]["display"] == [{"kind": "markdown", "content": "A"}]
    assert "display" not in results[1]


def test_convert_does_not_inject_display_into_model_messages():
    """display は人間の目 — context_images と対称だが向きが逆で、モデルへは再注入しない。
    tool メッセージの content はそのまま、追加の user メッセージも生えない。"""
    blocks = [
        {
            "type": "tool_calls",
            "content": [{"id": "c1", "type": "function", "function": {"name": "tts"}}],
            "results": [
                {
                    "tool_call_id": "c1",
                    "content": "OK(表示済み)",
                    "display": [
                        {"kind": "markdown", "content": "<audio controls></audio>"}
                    ],
                }
            ],
        },
    ]
    msgs = convert_content_blocks_to_messages(blocks)
    # display 由来の user メッセージは生えない
    assert not any(m["role"] == "user" and isinstance(m["content"], list) for m in msgs)
    # tool メッセージは content 文字列のみ、display は漏れない
    tool_msgs = [m for m in msgs if m["role"] == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["content"] == "OK(表示済み)"
    assert "display" not in tool_msgs[0]
    # display 文字列がどのメッセージにも紛れ込まない(全 content を平串で確認)
    serialized = repr(msgs)
    assert "<audio controls>" not in serialized


# --- progress チャネル(#100、実行中の人間の目・揮発) ----------------------


class _FakeStream:
    """CompletionEventStream の emit だけを模した収集器。"""

    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)

    def progress_payloads(self):
        return [
            e["data"]["data"]
            for e in self.events
            if e["data"]["type"] == "tool:progress"
        ]


def _no_throttle(monkeypatch):
    """スロットルを無効化(送出間隔そのものを検証するテスト以外で使う)。"""
    monkeypatch.setattr(tool_output, "PROGRESS_THROTTLE_S", 0)


def test_progress_noop_outside_tool_context():
    # sink 未設定では黙って no-op(filter/pipe 等の文脈外でも安全)
    assert tool_output.current_sink.get() is None
    progress("進捗", current=1, total=10)  # 例外にならない


def test_progress_noop_without_emitter():
    # sink はあるが宛先が無い(headless・テスト)= 黙って捨てる
    sink = ToolOutputSink(tool_call_id="c1")
    token = tool_output.current_sink.set(sink)
    try:
        progress("見えない")
    finally:
        tool_output.current_sink.reset(token)


def test_progress_normalizes_indeterminate(monkeypatch):
    """不定に落ちる条件: total 不在 / total<=0 / current 不在 / bool。

    フロントは total の有無だけを見ればよい — 片方でも欠ければキーごと出さない。
    """
    _no_throttle(monkeypatch)
    seen = []
    sink = ToolOutputSink(tool_call_id="c1", emit_progress=seen.append)

    sink.push_progress("a")
    sink.push_progress("b", current=1, total=0)
    sink.push_progress("c", current=1, total=-5)
    sink.push_progress("d", total=10)  # current 不在
    sink.push_progress("e", current=True, total=10)  # bool は数値扱いしない
    sink.push_progress("f", current=1, total=True)

    assert all("total" not in p and "current" not in p for p in seen)
    assert [p["description"] for p in seen] == ["a", "b", "c", "d", "e", "f"]


def test_progress_clamps_current(monkeypatch):
    _no_throttle(monkeypatch)
    seen = []
    sink = ToolOutputSink(tool_call_id="c1", emit_progress=seen.append)

    sink.push_progress("x", current=-3, total=10)
    sink.push_progress("x", current=99, total=10)
    sink.push_progress("x", current=4, total=10)

    assert [(p["current"], p["total"]) for p in seen] == [(0, 10), (10, 10), (4, 10)]


def test_progress_keeps_last_description(monkeypatch):
    """description 省略で直前の文言を維持(tqdm の set_description 相当)。

    ループ内では push_progress(current=i, total=n) だけ呼べる。
    """
    _no_throttle(monkeypatch)
    seen = []
    sink = ToolOutputSink(tool_call_id="c1", emit_progress=seen.append)

    sink.push_progress("PDF を変換中")
    sink.push_progress(current=1, total=3)
    sink.push_progress("", current=2, total=3)
    sink.push_progress("後処理中", current=3, total=3)

    assert [p["description"] for p in seen] == [
        "PDF を変換中",
        "PDF を変換中",
        "PDF を変換中",
        "後処理中",
    ]


def test_progress_throttles_and_flushes_last_on_close():
    """ループ内で毎周呼んでも送出は間引かれ、抑制された最後の 1 通は close で届く。

    leading-edge のみだと末尾が落ちて古い値が画面に残るため、flush が要る。
    """
    seen = []
    sink = ToolOutputSink(tool_call_id="c1", emit_progress=seen.append)

    for i in range(100):
        sink.push_progress("処理中", current=i, total=100)

    # 先頭の 1 通だけが通っている(以降は 200ms 未満なので抑制)
    assert len(seen) == 1
    assert seen[0]["current"] == 0

    # 終端は done=True。抑制されていた最後の数値をここに畳んで載せる
    sink.close()
    assert len(seen) == 2
    assert seen[1]["current"] == 99
    assert seen[1]["done"] is True


def test_progress_done_bypasses_throttle():
    seen = []
    sink = ToolOutputSink(tool_call_id="c1", emit_progress=seen.append)

    sink.push_progress("開始")
    sink.push_progress("抑制される")
    sink.push_progress("完了", done=True)

    assert [p["description"] for p in seen] == ["開始", "完了"]
    assert seen[-1]["done"] is True


def test_start_marker_does_not_consume_the_throttle_window():
    """開始マーカはスロットルの窓を動かさない。

    動かしてしまうと、直後に tool が押す **最初の** description が 200ms の窓に
    巻き込まれて抑制され、close の flush(= tool 終了時)まで画面に出ない。
    実行中に見せるための機構なのに、実行が終わるまで何も出ないことになる。
    """
    seen = []
    sink = ToolOutputSink(tool_call_id="c1", emit_progress=seen.append)

    sink.mark_started()
    sink.push_progress("すぐ出したい最初の 1 通")

    assert [p["description"] for p in seen] == ["", "すぐ出したい最初の 1 通"]


def test_progress_after_close_is_dropped(monkeypatch):
    """close 後の push は捨てる。

    timeout で待ちだけ外れた orphan スレッド(middleware の有界リーク)が
    走り続けても、次の tool の進捗を汚さない。
    """
    _no_throttle(monkeypatch)
    seen = []
    sink = ToolOutputSink(tool_call_id="c1", emit_progress=seen.append)

    sink.push_progress("実行中")
    sink.close()
    sink.push_progress("orphan からの遅延 push")

    # 実行中 → 終端(done)の 2 通だけ。close 後の push は増えない
    assert [(p["description"], p["done"]) for p in seen] == [
        ("実行中", False),
        ("実行中", True),
    ]


def test_progress_close_is_idempotent():
    seen = []
    sink = ToolOutputSink(tool_call_id="c1", emit_progress=seen.append)
    sink.push_progress("a")
    sink.push_progress("b")  # 抑制される
    sink.close()
    sink.close()
    # 2 回目の close は何も流さない。終端には抑制された最新の文言が載る
    # (件数だけ見ると、間違った payload を流す退行を拾えない)
    assert [(p["description"], p["done"]) for p in seen] == [
        ("a", False),
        ("b", True),
    ]


def test_progress_from_async_tool_does_not_deadlock(monkeypatch):
    """async tool から呼んでもデッドロックしない(#100 の要諦)。

    async tool は utils/tools.py の iscoroutinefunction 分岐で to_thread を通らず
    イベントループ上で直接 await される。ここで future を待つ実装
    (run_coroutine_threadsafe(...).result())にするとループが自分自身を待って固まる。
    """
    _no_throttle(monkeypatch)
    stream = _FakeStream()

    async def slow():
        progress("問い合わせ中", current=1, total=2)
        progress("整形中", current=2, total=2)
        return "ok"

    async def run():
        return await asyncio.wait_for(
            execute_tool_calls(
                _call(),
                _tool(slow),
                events_stream=stream,
                metadata={"chat_id": "chat-1", "message_id": "msg-1"},
            ),
            timeout=5,
        )

    results = asyncio.run(run())
    assert results[0]["content"] == "ok"
    payloads = stream.progress_payloads()
    # 開始マーカ(description 空・タイマの起点)→ 進捗 2 件 → 終端(done・タイマの終点)
    assert [p["description"] for p in payloads] == [
        "",
        "問い合わせ中",
        "整形中",
        "整形中",
    ]
    assert [p["done"] for p in payloads] == [False, False, False, True]
    assert all(p["tool_call_id"] == "functions.probe:0" for p in payloads)
    envelope = stream.events[0]
    assert envelope["chat_id"] == "chat-1"
    assert envelope["message_id"] == "msg-1"


def test_progress_from_sync_tool_via_to_thread(monkeypatch):
    """sync tool(#28 の to_thread ワーカー)からの進捗もループへ渡る。

    emit は購読者集合を触るのでワーカーから直接呼んではならず、
    call_soon_threadsafe でループへ渡す経路を通る。
    """
    _no_throttle(monkeypatch)
    stream = _FakeStream()

    def sync_tool():
        progress("ワーカーから", current=1, total=1)
        return "done"

    async def wrapper():
        return await asyncio.to_thread(sync_tool)

    async def run():
        return await asyncio.wait_for(
            execute_tool_calls(
                _call(),
                _tool(wrapper),
                events_stream=stream,
                metadata={"chat_id": "c", "message_id": "m"},
            ),
            timeout=5,
        )

    results = asyncio.run(run())
    assert results[0]["content"] == "done"
    assert [p["description"] for p in stream.progress_payloads()] == [
        "",
        "ワーカーから",
        "ワーカーから",  # 終端(done)
    ]


def test_progress_is_volatile_and_absent_from_results(monkeypatch):
    """進捗は results に残らない(揮発)。残るのは所要時間だけ。"""
    _no_throttle(monkeypatch)
    stream = _FakeStream()

    async def worker():
        progress("途中経過")
        return "ok"

    results = asyncio.run(
        execute_tool_calls(_call(), _tool(worker), events_stream=stream, metadata={})
    )
    assert stream.progress_payloads()  # 流れてはいる
    assert "progress" not in results[0]
    assert "途中経過" not in repr(results[0])


def test_progress_isolated_per_tool_call(monkeypatch):
    """進捗は tool_call_id で宛先が分かれる(共用スロットの奪い合いが起きない)。"""
    _no_throttle(monkeypatch)
    stream = _FakeStream()

    async def first():
        progress("A の進捗")
        return "a"

    async def second():
        progress("B の進捗")
        return "b"

    calls = [
        {"id": "functions.probe:0", "function": {"name": "probe", "arguments": "{}"}},
        {"id": "functions.plain:0", "function": {"name": "plain", "arguments": "{}"}},
    ]
    tools = {**_tool(first, "probe"), **_tool(second, "plain")}
    asyncio.run(execute_tool_calls(calls, tools, events_stream=stream, metadata={}))

    # 各 call の開始マーカ → その call の進捗、の順に並ぶ(逐次実行なので
    # 2 本目のマーカは 1 本目が終わってから飛ぶ = タイマの起点が正しくずれる)
    assert [
        (p["tool_call_id"], p["description"]) for p in stream.progress_payloads()
    ] == [
        ("functions.probe:0", ""),
        ("functions.probe:0", "A の進捗"),
        ("functions.probe:0", "A の進捗"),  # 終端
        ("functions.plain:0", ""),
        ("functions.plain:0", "B の進捗"),
        ("functions.plain:0", "B の進捗"),  # 終端
    ]


def test_start_marker_emitted_even_without_tool_progress_calls():
    """tool が __progress__ を一度も呼ばなくても開始は通知される。

    ブロックは実行前の 1 回の emit で一斉に描かれるため、これが無いと
    フロントの経過時間タイマが「前の tool の待ち時間」から数え始めてしまう。
    開始マーカはスロットルの窓を動かさない(直後の初回 description を落とさない)。
    """
    stream = _FakeStream()

    async def silent():
        return "ok"

    asyncio.run(
        execute_tool_calls(_call(), _tool(silent), events_stream=stream, metadata={})
    )
    payloads = stream.progress_payloads()
    assert payloads == [
        {"tool_call_id": "functions.probe:0", "description": "", "done": False},
        {"tool_call_id": "functions.probe:0", "description": "", "done": True},
    ]


def test_progress_without_stream_is_noop():
    """events_stream 省略(headless・既存テスト)でも tool は普通に動く。"""

    async def worker():
        progress("誰も見ていない")
        return "ok"

    results = asyncio.run(execute_tool_calls(_call(), _tool(worker)))
    assert results[0]["content"] == "ok"


def test_tool_failure_still_records_duration():
    """所要時間は成否に関わらず残す(落ちた tool が何秒走ったかこそ知りたい)。

    context_images / display の tool_succeeded ゲートとは規律が異なる。
    """

    async def boom():
        raise RuntimeError("kaboom")

    results = asyncio.run(execute_tool_calls(_call(), _tool(boom)))
    assert "kaboom" in results[0]["content"]
    assert isinstance(results[0]["duration"], float)
    assert results[0]["duration"] >= 0


def test_duration_is_not_injected_into_model_messages():
    """所要時間はモデルへ漏らさない(人間の目だけの情報)。"""
    blocks = [
        {
            "type": "tool_calls",
            "content": [
                {"id": "c1", "type": "function", "function": {"name": "probe"}}
            ],
            "results": [
                {"tool_call_id": "c1", "content": "ok", "duration": 12.345},
            ],
        },
    ]
    msgs = convert_content_blocks_to_messages(blocks)
    assert "12.345" not in repr(msgs)
    assert "duration" not in repr(msgs)
