"""組み込みツール: View Image(issue #42 / #89)。

BE に置かれた画像(file_id)を主モデル自身の目に「同一ターンで」見せる。
自分が run_python で生成した画像(/workspace/outputs/ 保存で自動添付された
file_id)や既存添付の画像を、自分で確認・評価したいときに file_id を渡す。

仕組み(#89、汎用 tool-push 経路に載る):本 tool は file_id を検証して
`__feed_image__(file_id)`(どの tool でも使える汎用ヘルパ)を呼ぶだけ。middleware が
実行中 tool-call の sink から file_id を harvest して results[i].model_images へ載せ、
convert_content_blocks_to_messages が `/api/v1/files/{id}/content` の image_url を組み、
送信直前に inline_internal_image_urls が 1 回だけ base64 化してモデルへ再投入する。
base64 は戻り境界にも DB スナップショットにも載らない。view_image はこの機構の
「モデルが能動的に file_id を見に行く(model-pull)」薄い入口で、VLM 注入を独占しない。

前提(満たされないと画素は届かない):
1. モデルの function_calling が native であること。
2. 主モデルが VLM(画像入力対応)であること。text-only では無意味。

認可(#40 と同クラスの IDOR 封鎖):file_id の所有者が呼び出しユーザーと一致する
場合のみ提示する(file_id 直指定で他ユーザーのファイルを覗かせない。送信直前 inline
でも has_read_access_to_file が二重に効く)。
"""

import asyncio
import logging
from typing import Any, Awaitable, Callable, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# --- Nucllei 内部 API(ランタイムで解決) -------------------------------------
try:
    from nucllei.models.files import Files
    from nucllei.storage.provider import Storage
except Exception:  # ローカル静的解析用フォールバック
    Files = None
    Storage = None


def _read_raw(rec) -> Optional[bytes]:
    """Storage 実体から生バイトを読む(サイズ検証用)。"""
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
            description="モデルに渡す画像の上限サイズ。超過はエラーを返す(リサイズを促す)。",
        )

    def __init__(self) -> None:
        self.valves = self.Valves()

    async def view_image(
        self,
        file_id: str,
        __user__: Optional[dict] = None,
        __feed_image__: Optional[Callable[[str], None]] = None,
        __event_emitter__: Optional[Callable[[Any], Awaitable[None]]] = None,
    ):
        """BE 上の画像(file_id)を自分自身に見せる。自分が生成 / 退避した画像を
        自分の目で確認・評価したいときに使う。画像はこのターン内であなた自身に
        提示されるので、続けて内容を評価・説明・修正できる。

        - 対象は画像(image/*)のみ。テキスト / PDF 等は対象外(それらは
          inspect_artifact / ask_subagent を使う)。
        - 自分が生成 / 退避した画像や既存添付の file_id を渡す(どの tool が返した
          file_id でもよい)。/api/v1/files/<id>/content の <id> 部分(UUID)が file_id。

        :param file_id: 見たい画像の file_id(UUID)
        """
        if Files is None:
            return "ERROR: nucllei 内部 API を解決できない(実行環境を確認)。"

        # 同期 DB / ディスク read はワーカースレッドへ退避してイベントループを
        # 塞がない(#28 の方針。本関数は __event_emitter__ を await するため async の
        # まま、ブロッキング部分だけ to_thread する)。
        rec = await asyncio.to_thread(Files.get_file_by_id, file_id)  # v0.6.5 は sync
        if not rec:
            return f"ERROR: file_id {file_id} のレコードが無い。"

        # 所有者チェック(sandbox.import_chat_file / prism view_image と同じ規律):
        # file_id 直指定で他ユーザーのファイルを覗かせない(#40 と同クラスの IDOR 封鎖)。
        user_id = (__user__ or {}).get("id")
        owner = getattr(rec, "user_id", None)
        if owner and owner != user_id:
            return f"ERROR: file_id {file_id} にアクセス権が無い。"

        meta = getattr(rec, "meta", None) or {}
        ct = (meta.get("content_type") or "").lower()
        if not ct.startswith("image/"):
            return (
                f"ERROR: file_id {file_id} は画像ではない(content_type={ct or '不明'})。"
                "テキスト/PDF 等は inspect_artifact か ask_subagent を使うこと。"
            )

        # サイズ検証: 壊れた/巨大なファイルを提示前に弾く(実体化は送信直前 inline に
        # 寄せるが、上限超過はここで早期に返す)。
        raw = await asyncio.to_thread(_read_raw, rec)
        if raw is None:
            return f"ERROR: 画像の実体を読めない(Storage 解決失敗): {file_id}"
        if len(raw) > self.valves.max_image_mb * 1024 * 1024:
            return (
                f"ERROR: 画像が大きすぎる({len(raw) / 1024 / 1024:.1f}MB > "
                f"{self.valves.max_image_mb}MB)。run_python でリサイズしてから渡すこと。"
            )

        filename = getattr(rec, "filename", None) or file_id
        if __feed_image__ is None:
            # 実行文脈外(通常起こらない)。file_id を push できないので明示エラー。
            return "ERROR: 画像提示チャネルが利用できない(実行文脈外)。"

        # 汎用 tool-push: 実行中 tool-call の sink に file_id を積む。実体化は
        # 送信直前 inline に一任。base64 は戻り境界に載せない。
        __feed_image__(file_id)

        if __event_emitter__:
            await __event_emitter__(
                {
                    "type": "status",
                    "data": {
                        "description": f"view_image: {filename} を提示",
                        "done": True,
                    },
                }
            )

        # role:tool の content は非空の status 文字列(空 content を拒否する互換
        # サーバ対策 + モデルへ「提示した」ことを伝える)。画素は model_images 経由で届く。
        return f"画像 {filename} を提示しました(file_id={file_id})。"
