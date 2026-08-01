"""tool 出力 sink(#89 画像 / #88 display)と各経路の回帰。

- feed_image は実行中 sink に file_id を積む。sink 未設定(文脈外)では no-op。
- execute_tool_calls: __feed_image__ を呼ぶ tool の結果に context_images が載り、
  呼ばない tool には載らない(型付きトリガのみで発火)。
- convert_content_blocks_to_messages: context_images の file_id から
  /api/v1/files/{id}/content の image_url user メッセージを 1 つ生成する。
- display(#88、人間の目): __display__ を呼ぶ tool の結果に display が載る。
  feed_image と対称の sink 経路だが、context_images と違い **モデルへ再注入しない**
  (convert が display を読まない)— 2 チャネル分離の本体。
"""

import asyncio

from nucllei.utils import tool_output
from nucllei.utils.tool_output import ToolOutputSink, feed_image, display
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
