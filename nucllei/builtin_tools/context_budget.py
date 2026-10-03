"""組み込みツール: context 使用量・残量(2026-10-03、llens `tools/context_budget.py` から移設)。

モデル自身が「自分がどれだけ context を食ったか」を問い合わせる。真実源は nucllei が
モデル呼び出しごとに永続化する実測 usage(`utils/middleware._record_usage` →
`Chats.record_usage_by_id_and_message_id` → `chat.meta["usage"]`)。tiktoken 等の推定ではなく
エンジン(SGLang 等)が返した本物の usage を読む。

なぜ llens から移すか: 記録がモデル呼び出しごと(tool ループの各 stream 末)になった
(#102)ので、tool ループの途中で呼べば**今ターンの最新呼び出し**の値が読める(llens 版は
「直前ターン終了時点」で 1 ターン古く、context 長を valve の手書き定数に頼っていた)。
context 長はまず記録された `context_size`、無ければ `get_model_context_size`(endpoint の
申告 → `meta.context_size`)、それも無ければ valve `fallback_context_size`(管理画面のツール一覧の
歯車。既定 0 = 使わない — endpoint が context 長を申告しない接続向けの最後の手段。ken 2026-10-03)。

**認可は owner-only 固定**(`__user__` の chat のみ — builtin_history と同じ)。
"""

import logging
from typing import Optional

from pydantic import BaseModel, Field

from nucllei.models.chats import Chats

log = logging.getLogger(__name__)


def _positive_int(value) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value > 0:
        return int(value)
    return None


class Tools:
    class Valves(BaseModel):
        fallback_context_size: int = Field(
            default=0,
            description=(
                "context 長の最後の既定値 (token)。記録された usage にも endpoint の申告にも"
                "モデルの meta.context_size にも無いときだけ使う。0 なら使わない (残量は出さない)"
            ),
        )

    def __init__(self) -> None:
        self.valves = self.Valves()

    async def get_context_budget(
        self,
        __metadata__: Optional[dict] = None,
        __user__: Optional[dict] = None,
        __request__=None,
        __model__: Optional[dict] = None,
    ) -> dict:
        """現在の会話の context 使用量と残量を返す。作業が伸びて(長い推論・tool の連打)
        自分がどれだけ context を消費したか確かめたいときに呼ぶ。値は推定ではなく、
        このターンの最新のモデル呼び出し時点でエンジンが実測した usage(入力 + 再送される
        出力)。いま実行中のステップの tool 結果はまだ含まれない(次の呼び出しで加算される)。
        残量が少なければ、新しい長尺タスクを避ける・結論を早めに確定する等の判断材料にする。

        :return: {ok, used_tokens, remaining_tokens, context_size, used_pct, note} / 取得不能時は {ok: False, error}
        """
        user_id = (__user__ or {}).get("id")
        if not user_id:
            return {"ok": False, "error": "ユーザー文脈が無い(__user__ 未注入)。"}

        chat_id = (__metadata__ or {}).get("chat_id")
        if not chat_id:
            return {"ok": False, "error": "chat_id が無い(chat 経路からのみ呼べる)。"}

        # owner-only: 他ユーザーの chat_id は「無い」扱い(所有者検証を漏らさない)
        chat = Chats.get_chat_by_id_and_user_id(chat_id, user_id)
        usage = ((chat.meta if chat else None) or {}).get("usage") or None
        if not usage:
            return {
                "ok": False,
                "error": "この会話の usage がまだ無い(最初のモデル呼び出しの完了前)。",
            }

        # 分子: carried_tokens(最後の呼び出しの prompt + 再送される出力 = 次の呼び出しの
        # 先頭の量)> prompt_tokens(最後の呼び出しの入力)> total_tokens の順で採る
        used = None
        for key in ("carried_tokens", "prompt_tokens", "total_tokens"):
            value = usage.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                used = int(value)
                break
        if used is None:
            return {
                "ok": False,
                "error": "この会話の usage に token 数が無い(エンジンが usage を返していない)。",
            }

        # context 長: 記録済み > モデルから解決(endpoint 申告 → meta.context_size)。
        # 定数のフォールバックは持たない — 分からなければ null で返す
        context_size = _positive_int(usage.get("context_size"))
        if context_size is None and __request__ is not None and __model__:
            try:
                from nucllei.utils.middleware import get_model_context_size

                context_size = _positive_int(
                    get_model_context_size(__request__, __model__)
                )
            except Exception as e:  # noqa: BLE001 — 解決失敗は「不明」として返す
                log.debug(f"context_size resolve failed: {e!r}")

        fallback_used = False
        if context_size is None:
            context_size = _positive_int(self.valves.fallback_context_size)
            fallback_used = context_size is not None

        note = (
            "このターンの最新のモデル呼び出し時点の実測 usage(入力 + 再送される出力)。"
            "いま実行中のステップの tool 結果はまだ含まれない。"
        )
        if context_size is None:
            return {
                "ok": True,
                "used_tokens": used,
                "remaining_tokens": None,
                "context_size": None,
                "used_pct": None,
                "note": note
                + " context 長が不明(endpoint が申告せず、モデルの meta.context_size も既定値も未設定)"
                "のため残量は出せない。",
            }

        if fallback_used:
            note += " context 長は管理者が設定した既定値(endpoint の申告ではない)。"
        remaining = max(0, context_size - used)
        return {
            "ok": True,
            "used_tokens": used,
            "remaining_tokens": remaining,
            "context_size": context_size,
            "used_pct": round(used / context_size * 100.0, 1),
            "note": note,
        }
