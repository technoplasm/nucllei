"""
description: OWUI が永続化した実測 usage(Chat.meta['usage'], #57)を読んで表示するだけの簡易トークンメーター。自前の usage 捕捉・累積はしない。
"""

from pydantic import BaseModel


def _fmt(n):
    # 1024 ベース(262144 → "256.0k")
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.1f}M"
    if n >= 1024:
        return f"{n / 1024:.1f}k"
    return str(int(n))


class Filter:
    class Valves(BaseModel):
        pass

    def __init__(self):
        self.valves = self.Valves()

    async def outlet(self, body, __metadata__=None, __event_emitter__=None):
        # outlet はターン完了後に呼ばれる = このターンの usage は #57 で永続化済み。
        # OWUI が記録した値を読むだけ(捕捉も累積も context_size valve も不要)。
        from open_webui.models.chats import Chats

        chat_id = (__metadata__ or {}).get("chat_id")
        if not (chat_id and __event_emitter__):
            return body

        chat = Chats.get_chat_by_id(chat_id)
        usage = ((chat.meta if chat else None) or {}).get("usage") or {}
        if not usage:
            return body

        p = usage.get("prompt_tokens", 0)
        c = usage.get("completion_tokens", 0)
        ctx = usage.get("context_size")

        if ctx:
            pct = 100.0 * p / ctx
            desc = f"🟢 {pct:.1f}% | {_fmt(p)}/{_fmt(ctx)} | IN {_fmt(p)} / OUT累計 {_fmt(c)}"
        else:
            desc = f"IN {_fmt(p)} / OUT累計 {_fmt(c)}"

        await __event_emitter__(
            {"type": "status", "data": {"description": desc, "done": True}}
        )
        return body
