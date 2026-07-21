"""tool 出力 sink(#89)と VLM 画像経路の回帰。

- feed_image は実行中 sink に file_id を積む。sink 未設定(文脈外)では no-op。
- execute_tool_calls: __feed_image__ を呼ぶ tool の結果に model_images が載り、
  呼ばない tool には載らない(型付きトリガのみで発火)。
- convert_content_blocks_to_messages: model_images の file_id から
  /api/v1/files/{id}/content の image_url user メッセージを 1 つ生成する。
"""

import asyncio

from nucllei.utils import tool_output
from nucllei.utils.tool_output import ToolOutputSink, feed_image
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
    assert sink.model_images() == ["a", "b"]


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


def test_execute_tool_calls_harvests_model_images():
    async def shower():
        feed_image("file-123")
        return "画像を提示しました。"

    results = asyncio.run(execute_tool_calls(_call(), _tool(shower)))
    assert results[0]["content"] == "画像を提示しました。"
    assert results[0]["model_images"] == ["file-123"]


def test_execute_tool_calls_no_vlm_key_when_not_pushed():
    async def plain():
        return "ok"

    results = asyncio.run(execute_tool_calls(_call(), _tool(plain)))
    assert "model_images" not in results[0]


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
    assert results[0]["model_images"] == ["img-A"]
    assert "model_images" not in results[1]


def test_convert_builds_image_url_from_model_images():
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
                    "model_images": ["abc"],
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


def test_convert_no_user_message_without_model_images():
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
