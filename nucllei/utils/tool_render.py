"""tool 引数/結果の型駆動レンダリング(issue #20)。

表示契約は「引数/戻りの型」が持つ(一次情報: _memos/tool-arg-render.md)。
名前・長さのヒューリスティックは使わない。フォールバックは「型に忠実」
(素の str は文字列、object/array は JSON。推測して code にしない)。

宣言メカニズム:
- ネイティブ Python tool: ``Annotated[str, RenderAs(...)]``(糖衣 ``Code(language)``)。
  実行時/LLM 的には素の型のまま。
- 外部 OpenAPI tool: 標準 JSON Schema の ``contentMediaType``(独自 x- は発明しない)。

両者はここで正規化ディスクリプタ ``{kind, language}``(language は hljs 言語 id)
に落ち、``calls[].render`` としてイベント/永続化に同梱される。フロントは dumb に
ディスクリプタ通り描画するだけ。
"""

from dataclasses import dataclass
from typing import Annotated, Any, Optional, get_args, get_origin, get_type_hints


@dataclass(frozen=True)
class RenderAs:
    """Annotated に載せる表示マーカー。バリデーションには関与しない。"""

    kind: str
    language: Optional[str] = None


def Code(language: str, base: type = str):
    """code 表示宣言の生成器。``PythonCode = Code("python")`` のような別名は薄い糖衣。"""
    return Annotated[base, RenderAs(kind="code", language=language)]


# 戻り型用の糖衣。モデルへの見え方(直列化テキスト)は変えず、ユーザー表示だけ変える。
# `Table`(kind="table")は廃止(2026-07-09): tool 結果は「値を見る」ものでデザイン対象では
# ないため、フロントの table 描画経路ごと撤去した(`ada5904e8`)。
Text = Annotated[str, RenderAs(kind="text")]

# md として「レンダリングして」見せる戻り型(doc 系 tool の戻り等)。
# Code("markdown") は「md ソースのハイライト」— 表示契約が違うので別型。
# 結果表示専用の kind(引数側の描画集合 code/scalar/json には含めない)。
Markdown = Annotated[str, RenderAs(kind="markdown")]


####################################
# 言語 id / media type の正規化テーブル
####################################

# hljs id の別名(素通しが基本。フロントが不明 id を plaintext に落とすので網羅しない)
HLJS_ALIASES = {
    "py": "python",
    "js": "javascript",
    "ts": "typescript",
    "sh": "bash",
    "shell": "bash",
    "zsh": "bash",
}


def normalize_hljs_language(language: Optional[str]) -> str:
    lang = (language or "").strip().lower()
    if not lang:
        return "plaintext"
    return HLJS_ALIASES.get(lang, lang)


# media type → hljs id。慣用プレフィックス(text/x- 等)は素通し、不明は plaintext
MEDIA_TYPE_TO_HLJS = {
    "text/x-python": "python",
    "application/x-python-code": "python",
    "application/sql": "sql",
    "application/json": "json",
    "text/javascript": "javascript",
    "application/javascript": "javascript",
    "application/x-sh": "bash",
    "text/x-shellscript": "bash",
    "text/markdown": "markdown",
    "application/yaml": "yaml",
    "text/html": "html",
    "text/plain": "plaintext",
}


def media_type_to_hljs(media_type: Optional[str]) -> str:
    media = (media_type or "").strip().lower().split(";")[0]
    if media in MEDIA_TYPE_TO_HLJS:
        return MEDIA_TYPE_TO_HLJS[media]
    for prefix in ("text/x-", "application/x-", "text/"):
        if media.startswith(prefix):
            return normalize_hljs_language(media[len(prefix) :])
    return "plaintext"


# hljs id → contentMediaType(ネイティブ宣言を spec に反映するときの逆写像)
HLJS_TO_MEDIA_TYPE = {
    "python": "text/x-python",
    "sql": "application/sql",
    "json": "application/json",
    "javascript": "text/javascript",
    "bash": "application/x-sh",
    "markdown": "text/markdown",
    "yaml": "application/yaml",
    "html": "text/html",
    "plaintext": "text/plain",
}


def hljs_to_media_type(language: Optional[str]) -> str:
    lang = normalize_hljs_language(language)
    return HLJS_TO_MEDIA_TYPE.get(lang, f"text/x-{lang}")


####################################
# 型 → ディスクリプタ抽出
####################################


def _descriptor_from_annotation(annotation: Any) -> Optional[dict]:
    if get_origin(annotation) is not Annotated:
        return None
    for meta in get_args(annotation)[1:]:
        if isinstance(meta, RenderAs):
            descriptor = {"kind": meta.kind}
            if meta.kind == "code":
                descriptor["language"] = normalize_hljs_language(meta.language)
            return descriptor
    return None


def extract_render_from_function(func) -> dict:
    """ネイティブ tool 関数の Annotated から ``{"args": {...}, "result": {...}}`` を抽出。

    宣言のあるキーだけを持つ。宣言が無ければ空 dict(wire に render は載らない)。
    """
    try:
        hints = get_type_hints(func, include_extras=True)
    except Exception:
        # plugin モジュールの前方参照など、解決不能な注釈は表示宣言なし扱い
        return {}

    args = {}
    for name, annotation in hints.items():
        if name == "return" or name.startswith("__"):
            continue
        descriptor = _descriptor_from_annotation(annotation)
        if descriptor:
            args[name] = descriptor

    render = {}
    if args:
        render["args"] = args
    result = _descriptor_from_annotation(hints.get("return"))
    if result:
        render["result"] = result
    return render


def extract_render_from_spec(spec: dict) -> dict:
    """OpenAI function spec(OpenAPI 由来)の contentMediaType から args を抽出。

    contentMediaType は「この文字列は◯◯」という型情報なので kind=code に写像する。
    """
    args = {}
    properties = (spec.get("parameters") or {}).get("properties") or {}
    for name, prop in properties.items():
        if isinstance(prop, dict) and prop.get("contentMediaType"):
            args[name] = {
                "kind": "code",
                "language": media_type_to_hljs(prop["contentMediaType"]),
            }
    return {"args": args} if args else {}


def apply_render_to_spec(spec: dict, render: dict) -> None:
    """ネイティブ宣言から得た args ディスクリプタを spec に contentMediaType として反映。

    表示専用の別建てメタ(x-render)を作らず、標準 JSON Schema の語彙でモデルにも
    見せる(strip しない方針 — _memos/tool-arg-render.md)。既に宣言があれば触らない。
    """
    properties = (spec.get("parameters") or {}).get("properties") or {}
    for name, descriptor in (render.get("args") or {}).items():
        prop = properties.get(name)
        if descriptor.get("kind") == "code" and isinstance(prop, dict):
            prop.setdefault(
                "contentMediaType", hljs_to_media_type(descriptor.get("language"))
            )
