"""テスト用: ToolError(#145)を「モデルに見える文」に戻す小道具。

builtin tool は失敗を `ToolError` の raise で伝える。文面の番兵テストは従来どおり文字列を
全文比較したいので、raise された ToolError / 返された ToolError を `.text` に揃える。
"""

import functools
import inspect

from nucllei.utils.tool_errors import ToolError


def as_text(value):
    """ToolError なら `.text`、それ以外はそのまま。"""
    return value.text if isinstance(value, ToolError) else value


def text_of(fn):
    """fn(同期 / async どちらも)を包み、raise / return された ToolError を `.text` にする。"""
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            try:
                return as_text(await fn(*args, **kwargs))
            except ToolError as e:
                return e.text

        return async_wrapper

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return as_text(fn(*args, **kwargs))
        except ToolError as e:
            return e.text

    return wrapper


async def call_text(awaitable):
    """`await tool(...)` の結果を文字列で。ToolError は `.text`。"""
    try:
        return as_text(await awaitable)
    except ToolError as e:
        return e.text


async def call_error(awaitable) -> ToolError:
    """ToolError が raise されることを確かめて返す。"""
    try:
        result = await awaitable
    except ToolError as e:
        return e
    raise AssertionError(f"ToolError が raise されなかった: {result!r}")
