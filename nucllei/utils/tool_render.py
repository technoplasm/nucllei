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

**結果の表示は backend が実行後に 1 か所で確定する**(#153、ken 2026-10-07)。
``resolve_result_render`` が結果ごとに 1 つ決めて ``results[i].render`` に載せ、フロントは
それだけを見る(``calls[].render`` は引数の ``args`` だけを運ぶ)。結果の描画が呼び出しごとに
決まる tool(read_file — 拡張子で md / code / text)は戻り値を ``Rendered(text, kind, language)``
にする。
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


@dataclass(frozen=True)
class Rendered:
    """結果の表示種別を呼び出しごとに持つ戻り値(#153)。モデルへは ``text`` だけが渡る。

    ``normalize_tool_result`` が text に落とし、表示は ``resolve_result_render`` が
    ``descriptor()`` に確定する(静的な宣言より強い)。戻り型 Rendered は静的な結果宣言を
    作らない(``extract_render_from_function``) — 値が種別を持つので宣言は意味を持たない。
    切詰め・エラーで text に落ちる規則はそのまま。
    """

    text: str
    kind: str
    language: Optional[str] = None

    def descriptor(self) -> dict:
        descriptor = {"kind": self.kind}
        if self.kind == "code":
            descriptor["language"] = normalize_hljs_language(self.language)
        return descriptor


def resolve_result_render(
    raw: Any,
    static: Optional[dict] = None,
    *,
    failed: bool = False,
    truncated: bool = False,
) -> Optional[dict]:
    """結果の表示 ``results[i].render`` を確定する唯一の場所(同期 middleware と background
    runner が共有)。``None`` は text(フロントの既定)。優先順(上ほど強い):

    1. エラー(raise 由来の ERROR 文 — ``failed``)と切り詰めた結果 → text。JSON や宣言の
       描画はパース前提なので壊れた本文に当てない。``mark_error_kind`` の旗だけの結果は
       failed ではない(本文は通常の結果なので render を残す)
    2. ``Rendered`` → tool が明示した {kind, language}
    3. 静的な宣言 ``static``(戻り型の Annotated = ``tool_dict["render"]["result"]``。
       await_task は ``sink.set_result_render`` で実 tool の確定済みの表示に差し替える)
    4. dict / list → json
    5. どれでもなければ text

    宣言(3)を dict / list(4)より上に置くのは、宣言の多くが dict の形への宣言だから
    (run_subagent の ``subagent``・load_skill の ``skill``・await_task の封筒への持ち上げ)。
    逆にすると dict を返すそれらが全部 json に落ちる。
    """
    if failed or truncated:
        return None
    if isinstance(raw, Rendered):
        return raw.descriptor()
    if isinstance(static, dict) and static.get("kind"):
        return dict(static)
    if isinstance(raw, (dict, list)):
        return {"kind": "json"}
    return None


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


# ファイルの拡張子 → hljs id(read_file の code 表示、#153)。ここに無い拡張子は code に
# しない(推測しない — 呼び手が text で出す)。md は code ではなく markdown 描画なので載せない。
# csv / tsv は hljs に言語が無いが、ヘッダの言語名として素通しする(フロントが plaintext に落とす)
EXTENSION_TO_HLJS = {
    **{
        ext: ext
        for ext in (
            "py",
            "sql",
            "sh",
            "bash",
            "zsh",
            "js",
            "ts",
            "json",
            "yaml",
            "csv",
            "tsv",
            "toml",
            "ini",
            "xml",
            "html",
            "css",
            "scss",
            "r",
            "go",
            "java",
            "c",
            "cpp",
            "php",
            "lua",
            "swift",
            "diff",
        )
    },
    "yml": "yaml",
    "mjs": "javascript",
    "cjs": "javascript",
    "jsx": "javascript",
    "tsx": "typescript",
    "jsonl": "json",
    "ipynb": "json",
    "htm": "html",
    "svg": "xml",
    "cfg": "ini",
    "conf": "ini",
    "rb": "ruby",
    "rs": "rust",
    "h": "c",
    "cc": "cpp",
    "hpp": "cpp",
    "cs": "csharp",
    "kt": "kotlin",
    "pl": "perl",
    "tex": "latex",
    "patch": "diff",
    "ps1": "powershell",
    "bat": "dos",
}


def hljs_language_for_extension(ext: Optional[str]) -> Optional[str]:
    """拡張子(``.py`` / ``py``、大文字小文字は問わない)→ hljs id。表に無ければ None。"""
    key = (ext or "").strip().lower().lstrip(".")
    lang = EXTENSION_TO_HLJS.get(key)
    return normalize_hljs_language(lang) if lang else None


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
    if get_args(annotation)[0] is Rendered:
        # 値が自分の表示種別を持つ型 — 静的な宣言は付けない(FE が値の render より優先するため)
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
