"""
title: View Image
author: Ken Enda
description: |
  BE に置かれた画像 (file_id) を主モデル自身の目に「同一ターンで」見せる tool。
  自分が run_python で生成した画像 (/workspace/outputs/ 保存で自動添付された
  file_id) や既存添付の画像を、自分で確認・評価したいときに file_id を渡す。

  仕組み: 本 tool は画像を data:image/...;base64 文字列として返すだけ。あとは
  OWUI の native function-calling ループが、その data URI を tool 結果から user
  メッセージの image_url に詰め替えてモデルへ再投入する (middleware.py の
  process_tool_result → input_image → "Extract images into a user message" 経路)。
  これにより VLM (Kimi 等) が同一ターンで画素を受け取り、続けて評価できる。

changelog:
  0.1.1: Pyodide 前提の文言を sandbox (run_python) に更新 (docstring / エラーメッセージ)。
         挙動は不変。
  0.1.0: 初版。
"""

# =============================================================================
# 前提 (これが満たされないと画素はモデルに届かない)
# -----------------------------------------------------------------------------
# 1. モデルの function_calling が native であること。非 native (task-model FC) だと
#    tool 結果はテキスト注入経路に乗り、画像は frontend 表示止まりになる。
# 2. 主モデルが VLM (画像入力対応) であること。text-only モデルでは無意味。
# 3. 返すのは data:image/...;base64,... の「インライン文字列」であること。
#    /api/v1/files/<id> URL を返すと OWUI が LLM 用ではなく frontend 表示用に振り分ける。
# subagent.py が画像を「別 context に隔離して要約」するのと逆で、本 tool は画素を
# 「主 context に取り込む」。用途で使い分ける。
# =============================================================================

import base64
import logging
from typing import Any, Awaitable, Callable, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# --- Nucllei 内部 API (ランタイムで解決) -------------------------------------
try:
    from nucllei.models.files import Files
    from nucllei.storage.provider import Storage
except Exception:  # ローカル静的解析用フォールバック
    Files = None
    Storage = None


def _read_raw(rec) -> Optional[bytes]:
    """Storage 実体から生バイトを読む (subagent.py と同じ経路)。"""
    if Storage is None:
        return None
    path = getattr(rec, "path", None)
    if not path:
        return None
    try:
        local = Storage.get_file(path)
        with open(local, "rb") as f:
            return f.read()
    except Exception as e:
        logger.error(f"[view_image] raw read failed: {e!r}")
        return None


class Tools:
    class Valves(BaseModel):
        max_image_mb: float = Field(
            default=8.0,
            description="モデルに渡す画像の上限サイズ。超過はエラーを返す (リサイズを促す)。",
        )

    def __init__(self) -> None:
        self.valves = self.Valves()

    async def view_image(
        self,
        file_id: str,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Callable[[Any], Awaitable[None]]] = None,
    ) -> str:
        """BE 上の画像 (file_id) を自分自身に見せる。自分が生成 / 退避した画像を
        自分の目で確認・評価したいときに使う。戻り値の画像はこのターン内であなた自身に
        提示されるので、続けて内容を評価・説明・修正できる。

        - 対象は画像 (image/*) のみ。テキスト / PDF 等は対象外 (それらは
          inspect_artifact / ask_subagent を使う)。
        - run_python の戻り値 (outputs/ 自動添付) や export_workspace_file が返す
          file_id を渡す。/api/v1/files/<id>/content の <id> 部分 (UUID) が file_id。

        :param file_id: 見たい画像の OWUI file_id (UUID)
        :return: 画像の data URI 文字列 (成功時) / エラー説明 (失敗時)
        """
        if Files is None:
            return "ERROR: nucllei 内部 API を解決できない (実行環境を確認)。"

        rec = Files.get_file_by_id(file_id)  # v0.6.5 は sync
        if not rec:
            return f"ERROR: file_id {file_id} のレコードが無い。"

        # 所有者チェック (sandbox.import_chat_file / prism view_image と同じ規律):
        # file_id 直指定で他ユーザーのファイルを覗かせない (#40 と同クラスの IDOR 封鎖)。
        user_id = (__user__ or {}).get("id")
        owner = getattr(rec, "user_id", None)
        if owner and owner != user_id:
            return f"ERROR: file_id {file_id} にアクセス権が無い。"

        meta = getattr(rec, "meta", None) or {}
        ct = (meta.get("content_type") or "").lower()
        if not ct.startswith("image/"):
            return (
                f"ERROR: file_id {file_id} は画像ではない (content_type={ct or '不明'})。"
                "テキスト/PDF 等は inspect_artifact か ask_subagent を使うこと。"
            )

        raw = _read_raw(rec)
        if raw is None:
            return f"ERROR: 画像の実体を読めない (Storage 解決失敗): {file_id}"
        if len(raw) > self.valves.max_image_mb * 1024 * 1024:
            return (
                f"ERROR: 画像が大きすぎる ({len(raw)/1024/1024:.1f}MB > "
                f"{self.valves.max_image_mb}MB)。run_python でリサイズしてから渡すこと。"
            )

        if __event_emitter__:
            await __event_emitter__(
                {
                    "type": "status",
                    "data": {
                        "description": f"view_image: {getattr(rec, 'filename', file_id)} を提示",
                        "done": True,
                    },
                }
            )

        b64 = base64.b64encode(raw).decode("ascii")
        # data URI 文字列をそのまま返す。OWUI の native FC ループがこれを検出して
        # user メッセージの image_url に詰め替え、モデルへ再投入する。
        return f"data:{ct};base64,{b64}"
