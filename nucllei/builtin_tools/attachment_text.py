"""組み込みツール: 添付の抽出テキスト読み出し(file_id → 文字。issue #113)。

Attachment 抽出(README_API §7 — 配備側の Attachment function が
`Files.data.content` に書く)の**読み口**。書く側(抽出エンジン — markitdown 等)は
配備の選択だが、data.content の契約と Storage 実体は本体のものなので、読み口は
builtin として本体が持つ(2026-08-16 決定)。

主用途はサブエージェント(#107)の資料搬入: 親は run_subagent の instruction に
file_id を書くだけで、子 preset に本 tool を載せておけば子が資料を自分の context に
読み込める(資料本文は親 context に出ない)。

認可は **owner-only の file_id 直指定**(feed_image 等の配備 tool と同じ
「id を知っていれば読める」capability)。chat スコープにしない — 子 agent session は
親と別 chat のため、chat 縛りでは資料搬入が成立しない。全件ブラウズは提供しない
(発見面は builtin_chat_files がメタデータのみで持つ)。
"""

import logging
from typing import Optional

from nucllei.models.files import Files
from nucllei.storage.provider import Storage
from nucllei.utils.tool_errors import (
    ToolCallError,
    ToolFailure,
    ToolOutcome,
    tool_in_turn,
)

log = logging.getLogger(__name__)

# 1 回の呼び出しで返す文字数の既定と上限。TOOL_RESULT_MAX_CHARS(既定 65536)の
# 一律切り詰めに先んじて自前の窓で返し、続きは offset で取らせる(尻尾が黙って
# 落ちるのではなく、続きの取り方が戻り値に残る)。
DEFAULT_MAX_CHARS = 40000
MAX_MAX_CHARS = 60000

# raw を text として decode してよい content_type(これ以外の binary は decode しない)
_TEXT_CT_PREFIXES = ("text/",)
_TEXT_CT_EXACT = {"application/json", "application/xml", "application/csv"}


def _is_text_ct(ct: str) -> bool:
    ct = (ct or "").lower()
    return (
        any(ct.startswith(p) for p in _TEXT_CT_PREFIXES)
        or ct in _TEXT_CT_EXACT
        or ct.endswith("+json")
        or ct.endswith("+xml")
        or ct.endswith("csv")
    )


def _read_raw(rec) -> Optional[bytes]:
    path = getattr(rec, "path", None)
    if not path:
        return None
    try:
        local = Storage.get_file(path)
        with open(local, "rb") as f:
            return f.read()
    except Exception:
        log.exception(f"read_attachment: raw read failed (file_id={rec.id})")
        return None


def _resolve_text(rec) -> tuple[Optional[str], str]:
    """テキストとして読める中身を返す。(content, source)。

    1) 抽出が失敗した(data.status == "failed")ものは None, "failed" — data.content には
       attachment の例外の文が入っている(`utils/attachment.py`)ので本文として返さない
       (内部のパス・URL を含みうる。middleware の `<source extraction="failed">` と同じ扱い。
       2026-10-19 — 旧: 例外の文を本文として返していた)。raw の decode もしない
    2) Attachment 抽出済みテキスト(data.content — PDF/Office はここ)を優先
    3) 無ければ content_type が text 系のときだけ Storage raw を decode
    4) それ以外(binary / 抽出未完)は None
    """
    data = getattr(rec, "data", None) or {}
    if data.get("status") == "failed":
        content = (data.get("content") or "")[:200]
        log.info(f"read_attachment: extraction failed (file_id={rec.id}): {content!r}")
        return None, "failed"
    content = data.get("content")
    if content:
        return content, "extracted"
    ct = (getattr(rec, "meta", None) or {}).get("content_type") or ""
    if _is_text_ct(ct):
        raw = _read_raw(rec)
        if raw is not None:
            return raw.decode("utf-8", "replace"), "raw"
    return None, "none"


def _no_text_next(file_id: str) -> str:
    """抽出テキストが無いときの次の手。このターンに出ている tool だけで言い、種類は「〜なら」で
    条件にする (決めつけない)。どれも無ければ、読めないことを伝える。"""
    steps = []
    if tool_in_turn("feed_image"):
        steps.append(f'画像・PDF なら feed_image(file_id="{file_id}") で見る。')
    if tool_in_turn("send_to_workspace"):
        steps.append(
            f'それ以外で中身が要るなら send_to_workspace(file_id="{file_id}") で sandbox に'
            "取り込み、形式に合った道具で扱う。"
        )
    if not steps:
        return "この file_id を読み直さない。中身が要る話なら、読めないことをユーザーに伝える。"
    return "".join(steps)


class Tools:
    def read_attachment(
        self,
        file_id: str,
        offset: int = 0,
        max_chars: int = DEFAULT_MAX_CHARS,
        __user__: Optional[dict] = None,
    ) -> dict:
        """添付・生成物の抽出テキストを file_id から区切って読む (自分のファイルのみ)。
        本文が既にこの会話に入っている添付 (`<source>` にあるもの) には呼ばない。
        使うのは、本文が会話に入っていない file_id (大きすぎて読み込まれなかった添付・
        tool やサブエージェントが作ったもの・instruction で file_id だけ渡されたもの) を読むときだけ。
        長い文書は offset で続きを取る (next_offset が返る間は続きがある)。
        テキスト層が無いファイルは読めない — 見るなら feed_image。

        :param file_id: 読むファイル。添付・生成物の id
        :param offset: 読み始めの文字位置(省略時 0 = 先頭。続きは前回の next_offset)
        :param max_chars: 1 回で返す最大文字数(省略時 40000)
        """
        # 戻り値は {ok, file_id, filename, content_type, source, total_chars, offset, text,
        # next_offset?}。docstring には「いつ呼ぶか」だけを書く (2026-10-19 llens 監査 (2) で
        # 絞った。next_offset の意味だけは本文に残す)。spec ビルダは `:return:` をモデルに見せない
        # (README_API §5.1)
        user_id = (__user__ or {}).get("id")
        if not user_id:
            raise ToolFailure("ユーザーを特定できない")

        rec = Files.get_file_by_id(file_id)
        # 所有者チェックは fail-closed(owner が falsy でも拒否 — IDOR 同型の穴を
        # 開けない)。「レコードが無い」と「所有者でない」は同じ文で返す(存在の有無を
        # 漏らさない)。文面は全 tool 共通(`image_refs.py` の module docstring
        # 「エラーに添える固定文」)— file_id が受ける形の固定文を添え、値は調べない
        if not rec or getattr(rec, "user_id", None) != user_id:
            # image_refs(ref_errors が import する)は builtin_tools(sandbox)を
            # import する — 循環を避けて呼び出し時に引く
            from nucllei.utils.ref_errors import file_id_error

            raise file_id_error(file_id)

        content, source = _resolve_text(rec)
        meta = getattr(rec, "meta", None) or {}
        if source == "failed":
            # 例外の文は出さない (log にだけ残した)。事実は middleware の <source> と同じ言い方
            raise ToolOutcome(
                f"file_id {file_id} は文字が抜き出されていない (抽出に失敗した。"
                f"content_type={meta.get('content_type') or '不明'})",
                _no_text_next(file_id),
            )
        if content is None:
            # ファイルの種類を決めつけて別の tool へ誘導しない (音声など、feed_image が当たらない
            # 物にも同じ文が出る — 「〜なら」で言う)。次の手はこのターンにある tool だけ
            # (2026-10-19 llens 監査: 次の手の無い ERROR に次の手を足す)
            raise ToolOutcome(
                f"file_id {file_id} には抽出テキストが無い"
                f"(content_type={meta.get('content_type') or '不明'})",
                _no_text_next(file_id),
            )

        try:
            offset = max(0, int(offset))
            max_chars = int(max_chars)
        except (TypeError, ValueError):
            raise ToolCallError("offset / max_chars が整数でない", "整数で渡す。")
        max_chars = max(1, min(max_chars, MAX_MAX_CHARS))

        total = len(content)
        window = content[offset : offset + max_chars]
        result = {
            "ok": True,
            "file_id": file_id,
            "filename": getattr(rec, "filename", None),
            "content_type": meta.get("content_type"),
            "source": source,
            "total_chars": total,
            "offset": offset,
            "text": window,
        }
        if offset + len(window) < total:
            result["next_offset"] = offset + len(window)
        return result
