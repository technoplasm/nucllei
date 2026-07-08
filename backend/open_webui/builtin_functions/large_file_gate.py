"""既定 builtin Filter「注入 gate」(#52 プランB ③)。

prism `owui/filters/large_file_gate.py` の **昇格版**。llens は RAG オフ(添付の抽出テキストは
要約/チャンク化されず全文がそのまま注入される)なので、巨大添付だけで context を食い潰す。
本 filter は inlet で各添付の抽出テキスト(`File.data.content`)を tiktoken で実測し、

  **合算予算(max_total_tokens)を超える間、大きい順に本文を context から除去**(all-or-nothing、
  truncate しない)。除去したファイルは file_id を告知する system 指示を1つ注入する。

本文は消えるが Files API には残るので、モデルは sandbox tool(④ import_chat_file)で file_id
から取り戻して処理できる。予算内に収まるファイルは触らない(全文注入のまま)。

prism からの適応(fork v0.6.5):
- `Files.get_file_by_id` は **sync**(prism は async)→ await しない。
- prism の skill mention `<$id|label>` 自動ロードは fork に該当ローダが無いため撤去。
  除去 note は file_id + 取り扱い(sandbox tool で読め)を直接書く。
- pdf-vision-router 注記による「画像提供済み」除外は、router 未移植のため一旦省略
  (移植時に failed 判定の誤検知除外として戻す)。
- 固定閾値(ファイル単位 100k)ではなく **合算予算 + 大きい順 eviction** に変更(memo ③「昇格」)。

builtin(id 接頭辞 `builtin_`)として出荷。per-model の filterIds 割り当てで適用する
(全 upload で走る Attachment と違い、gate はモデル文脈=inlet で per-model)。
"""

import logging
from typing import Any, Awaitable, Callable, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

try:
    import tiktoken
except Exception:  # pragma: no cover
    tiktoken = None

from open_webui.models.files import Files


def _p(msg: str, level: str = "info") -> None:
    getattr(logger, level)(f"[large-file-gate] {msg}")


def _fmt_bytes(n: Optional[int]) -> str:
    if not n:
        return "?"
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.1f}MB"
    if n >= 1024:
        return f"{n / 1024:.1f}KB"
    return f"{n}B"


def _iter_file_items(body: dict):
    """body['files'] と body['metadata']['files'] の添付参照を (id, name) で列挙(dedup)。"""
    seen: set[str] = set()
    sources = [
        body.get("files"),
        (body.get("metadata") or {}).get("files"),
    ]
    for src in sources:
        if not isinstance(src, list):
            continue
        for f in src:
            if not isinstance(f, dict):
                continue
            inner = f.get("file") or {}
            fid = f.get("id") or inner.get("id")
            if not fid or fid in seen:
                continue
            seen.add(fid)
            name = (
                inner.get("filename")
                or f.get("name")
                or (inner.get("meta") or {}).get("name")
                or fid
            )
            yield fid, name


def _remove_ids(body: dict, ids: set[str]) -> None:
    """body['files'] / metadata['files'] から該当 id の添付を取り除く。"""

    def _filtered(lst):
        out = []
        for f in lst:
            inner = f.get("file") or {} if isinstance(f, dict) else {}
            fid = (f.get("id") if isinstance(f, dict) else None) or inner.get("id")
            if fid in ids:
                continue
            out.append(f)
        return out

    if isinstance(body.get("files"), list):
        body["files"] = _filtered(body["files"])
    md = body.get("metadata")
    if isinstance(md, dict) and isinstance(md.get("files"), list):
        md["files"] = _filtered(md["files"])


class Filter:
    class Valves(BaseModel):
        priority: int = Field(
            default=50,
            description="注入 gate の filter 優先度(小さいほど先。抽出系の後・計測系の前)",
        )
        max_total_tokens: int = Field(
            default=100000,
            description=(
                "添付の抽出テキストの合算がこの token 数を超える間、大きいものから本文を "
                "context に入れず除去する。単一/複数添付の合算予算。"
            ),
        )
        min_failed_raw_bytes: int = Field(
            default=100 * 1024,
            description=(
                "raw がこのサイズ以上なのに抽出が空なら『抽出失敗』とみなし告知する。"
                "中身の無い小さな空ファイルを誤検知しないための下限。"
            ),
        )
        encoding_name: str = Field(
            default="cl100k_base",
            description="tiktoken エンコーディング名(token 実測用)",
        )
        chars_per_token_fallback: float = Field(
            default=2.0,
            description="tiktoken が使えない時に文字数から token を概算する係数(chars/token)",
        )
        debug: bool = Field(
            default=False, description="判定結果を status で流す(検証用)"
        )

    def __init__(self) -> None:
        # 静的 True にはしない(全ファイル一律 skip になる)。inlet で個別に間引く。
        self.file_handler = False
        self.valves = self.Valves()
        self._enc = None

    def _count_tokens(self, text: str) -> int:
        if not text:
            return 0
        if tiktoken is not None:
            try:
                if self._enc is None:
                    self._enc = tiktoken.get_encoding(self.valves.encoding_name)
                return len(self._enc.encode(text, disallowed_special=()))
            except Exception as e:
                _p(f"tiktoken 失敗、概算に fallback: {e!r}", level="warning")
        ratio = self.valves.chars_per_token_fallback or 1.0
        return int(len(text) / ratio)

    def _build_note(self, excluded: list[dict]) -> str:
        """除去(oversize)/ 抽出失敗(failed)したファイルの行動指示を組む(本文は出さない)。"""
        oversize = [e for e in excluded if e["reason"] == "oversize"]
        failed = [e for e in excluded if e["reason"] == "failed"]
        lines = ["[システム指示 / large-file-gate]"]

        if oversize:
            lines.append(
                "以下の添付は合算 context 予算を超えるため本文を context に入れていない "
                "(file_id から取得できる):"
            )
            for e in oversize:
                lines.append(
                    f"- {e['name']} (file_id={e['id']}, {e['content_type'] or '?'}, "
                    f"抽出≈{e['tokens']} tokens, raw {_fmt_bytes(e['size'])})"
                )
            lines.append(
                "→ 中身を主 context に展開せず、sandbox tool(import_chat_file)で file_id "
                "から取り込んで処理すること。"
            )

        if failed:
            lines.append(
                "以下の添付は抽出が空だった(大きすぎて処理がタイムアウトした可能性)。"
                "本文は context に無い:"
            )
            for e in failed:
                lines.append(
                    f"- {e['name']} (file_id={e['id']}, {e['content_type'] or '?'}, "
                    f"raw {_fmt_bytes(e['size'])})"
                )
            lines.append(
                "→ 内容が必要なら sandbox tool(import_chat_file)で原本を読み直す。"
                "読めなければ大きすぎる旨をユーザーに伝える。"
            )

        return "\n".join(lines)

    def _inject_note(self, body: dict, note: str) -> None:
        """最新 user message の直前に system 指示を挿入(前段 prefix を壊さない)。"""
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            return
        insert_pos = len(messages) - 1
        if messages[insert_pos].get("role") != "user":
            insert_pos = len(messages)
        messages.insert(insert_pos, {"role": "system", "content": note})
        body["messages"] = messages

    async def inlet(
        self,
        body: dict,
        __event_emitter__: Optional[Callable[[Any], Awaitable[None]]] = None,
        __user__: Optional[dict] = None,
    ) -> dict:
        # inlet が例外を投げるとチャット全体が壊れる。何が起きても body を通す(fail-open)。
        # 最悪「巨大本文がそのまま入る」= 元の挙動に戻るだけで、チャットは止めない。
        try:
            return await self._gate(body, __event_emitter__)
        except Exception as e:
            _p(f"inlet 失敗 → 無干渉で通過 (fail-open): {e!r}", level="error")
            return body

    async def _gate(
        self,
        body: dict,
        __event_emitter__: Optional[Callable[[Any], Awaitable[None]]],
    ) -> dict:
        candidates = list(_iter_file_items(body))
        if not candidates:
            return body

        # 1) 各添付を計測(sizable/failed 判定の材料)
        infos: list[dict] = []
        for fid, name in candidates:
            rec = Files.get_file_by_id(fid)  # sync (v0.6.5)
            if not rec:
                continue
            content = (getattr(rec, "data", None) or {}).get("content") or ""
            meta = getattr(rec, "meta", None) or {}
            size = meta.get("size") or 0
            tokens = self._count_tokens(content)
            infos.append(
                {
                    "id": fid,
                    "name": name,
                    "tokens": tokens,
                    "empty": not content.strip(),
                    "content_type": meta.get("content_type"),
                    "size": meta.get("size"),
                }
            )

        excluded: list[dict] = []

        # 2) 抽出失敗(raw は大きいのに本文が空)= 告知するが body からは外さない
        #    (本文は既に空で害が無く、body に残せば import_chat_file が拾える)
        for info in infos:
            if (
                info["empty"]
                and (info["size"] or 0) >= self.valves.min_failed_raw_bytes
            ):
                excluded.append({**info, "reason": "failed"})

        failed_ids = {e["id"] for e in excluded}

        # 3) 合算予算: 抽出テキストを持つファイルの token 合計が予算を超える間、
        #    大きい順に除去(oversize)。予算内に収まるものは残す(全文注入)。
        sized = sorted(
            (i for i in infos if i["id"] not in failed_ids and i["tokens"] > 0),
            key=lambda i: i["tokens"],
            reverse=True,
        )
        total = sum(i["tokens"] for i in sized)
        for info in sized:
            if total <= self.valves.max_total_tokens:
                break
            excluded.append({**info, "reason": "oversize"})
            total -= info["tokens"]

        if not excluded:
            return body

        # oversize のみ body から外す(failed は残す=上記 2 の理由)。
        _remove_ids(body, {e["id"] for e in excluded if e["reason"] == "oversize"})
        self._inject_note(body, self._build_note(excluded))

        summary = ", ".join(
            f"{e['name']}({e['tokens']}tok/{e['reason']})" for e in excluded
        )
        _p(f"除去 {len(excluded)} 件: {summary}", level="warning")
        if self.valves.debug and __event_emitter__:
            try:
                await __event_emitter__(
                    {
                        "type": "status",
                        "data": {
                            "description": f"large-file-gate: {len(excluded)} 件を除外 ({summary})",
                            "done": True,
                        },
                    }
                )
            except Exception:
                pass

        return body
