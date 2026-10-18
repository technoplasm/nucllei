"""utils/tool_errors(#145)— tool の失敗の 1 つの形と、middleware が結果に載せる種別の番兵。

- 文はここ 1 箇所で組む: `ERROR: {fact}。次の手: {next}`(次の手が無ければ `ERROR: {fact}。`)
- kind は call / failure / outcome / violation / fatal の 5 つ。wire の result に `error_kind`
  として載る(赤は violation / fatal だけ — ken 2026-10-04)
- execute_tool_calls: ToolError・例外(fatal)・タイムアウト(failure)・一覧に無い tool・
  読めない arguments・署名と合わない引数(#145 項目 2 — violation)を、種別付きの結果にする
- 旧来の `ERROR:` 文字列を返すカスタム tool は文面そのまま failure にフラグが立つ
- ref_errors のテンプレートを ToolError に通しても文面は 1 文字も変わらない
"""

import asyncio

import pytest

from nucllei.utils import ref_errors
from nucllei.utils.image_refs import RefError
from nucllei.utils.middleware import execute_tool_calls
from nucllei.utils.tool_errors import (
    ToolCallError,
    ToolError,
    ToolFailure,
    ToolFatal,
    ToolOutcome,
    ToolViolation,
    format_tool_error,
    tool_exception_text,
)


def _tool(callable_, name="probe", properties=None, **extra):
    return {
        name: {
            "callable": callable_,
            "spec": {"name": name, "parameters": {"properties": properties or {}}},
            **extra,
        }
    }


def _call(arguments, name="probe", index=0):
    return {
        "id": f"functions.{name}:{index}",
        "function": {"name": name, "arguments": arguments},
    }


def _run(calls, tools):
    return asyncio.run(execute_tool_calls(calls, tools))


# --- 文の形 -----------------------------------------------------------------------


def test_text_is_fact_then_next():
    assert (
        ToolError("事実だ", "次にこうする。").text
        == "ERROR: 事実だ。次の手: 次にこうする。"
    )
    assert ToolError("事実だ").text == "ERROR: 事実だ。"
    # 事実の末尾の「。」は 1 つに揃える(付けても付けなくても同じ文)
    assert ToolError("事実だ。").text == ToolError("事実だ").text
    assert ToolError("事実だ。。", "  ").text == "ERROR: 事実だ。"
    # 括弧で終わる事実も「。」で閉じる(既存文面の形)
    assert ToolError("大きい (上限 1 MB)", "分ける。").text == (
        "ERROR: 大きい (上限 1 MB)。次の手: 分ける。"
    )
    assert format_tool_error("a", "b") == "ERROR: a。次の手: b"
    assert str(ToolError("a", "b")) == "ERROR: a。次の手: b"


def test_kinds_of_the_subclasses():
    assert ToolError("x").kind == "failure"
    assert ToolCallError("x").kind == "call"
    assert ToolFailure("x").kind == "failure"
    assert ToolOutcome("x").kind == "outcome"
    assert ToolViolation("x").kind == "violation"
    assert ToolFatal("x").kind == "fatal"
    assert ToolError("x", kind="outcome").kind == "outcome"
    assert ToolError("x", kind="fatal").kind == "fatal"
    assert ToolCallError("x", kind="failure").kind == "failure"
    with pytest.raises(ValueError):
        ToolError("x", kind="oops")


def test_from_text_splits_at_the_first_next():
    e = ToolOutcome.from_text("ERROR: A。B。次の手: C。次の手: D。")
    assert (e.fact, e.next, e.kind) == ("A。B", "C。次の手: D。", "outcome")
    assert e.text == "ERROR: A。B。次の手: C。次の手: D。"
    # ERROR: が無い・次の手が無い文も同じ形に揃う
    assert ToolError.from_text("A。").text == "ERROR: A。"
    assert ToolError.from_text("ERROR: A。", kind="call").kind == "call"


def test_tool_exception_text_is_a_failure_sentence():
    assert tool_exception_text("t", ValueError("x" * 500)) == (
        f"ERROR: ツール t の実行中にエラーが起きた (ValueError: {'x' * 200})。"
        "次の手: 引数を見直して 1 回だけ呼び直す。続くならユーザーに失敗を伝える。"
    )


# --- ref_errors: テンプレートの文面を変えない・kind の対応 ----------------------------------


_CODES = [
    RefError("too_large", "x", size=30 * 1024 * 1024, limit=25 * 1024 * 1024),
    RefError(
        "too_large", "x", size=30 * 1024 * 1024, limit=25 * 1024 * 1024, size_exact=True
    ),
    RefError("too_large", "x"),
    RefError("not_found", "x"),
    RefError("upstream_status", "x", status=302),
    RefError("upstream_status", "x", status=302, reason="redirect_self"),
    RefError("upstream_status", "x", status=302, reason="too_many_redirects"),
    RefError("upstream_status", "x", status=500),
    RefError("upstream_status", "x"),
    RefError("upstream_unreachable", "x"),
    RefError("upstream_unreachable", "x", reason="unresolvable"),
    RefError("unknown_upstream", "x", reason="server_self"),
    RefError("unknown_upstream", "x"),
    RefError("traversal", "x"),
    RefError("unsupported_form", "x"),
    RefError("workspace_unavailable", "x"),
    RefError("brand_new", "x"),
]


def _tool_hints():
    """3 本の tool が実際に渡す hints(引数の別も)。"""
    from nucllei.builtin_tools import feed_image, fetch_url, sandbox
    from nucllei.utils.ref_errors import RefErrorHints

    ref = "/ext/a/b.pdf"
    out = [
        ("url", feed_image._hints(False, ref)),
        ("path", feed_image._hints(True, ref)),
        ("path", feed_image._hints(True, "/workspace/b.pdf")),
        (
            "url",
            RefErrorHints(
                too_large=lambda r, e: fetch_url._too_large(r, e, 200.0),
                unexplained=f"ERROR: {{ref}} {fetch_url._ERR_UNEXPLAINED}",
            ),
        ),
        (
            "url",
            RefErrorHints(
                too_large=sandbox._too_large_import,
                unexplained="ERROR: {ref} を取得できない。",
            ),
        ),
    ]
    return ref, out


def test_ref_error_templates_round_trip_unchanged():
    """テンプレートが組んだ文と ToolError を通した文が 1 文字も違わない(#145 は仕組みだけ
    変える — llens が判定した文面はそのまま)。"""
    ref, hint_sets = _tool_hints()
    for arg, hints in hint_sets:
        for err in _CODES:
            raw = ref_errors._explain_text(ref, err, err.code, arg, hints)
            raw = raw.text if isinstance(raw, ToolError) else raw
            assert ref_errors.explain_ref_error(
                ref, err, arg=arg, hints=hints
            ).text == (raw), (arg, err)
        none = ref_errors.explain_ref_error(ref, None, arg=arg, hints=hints)
        # 理由の無い失敗は resolver の取りこぼし = fatal
        assert none.kind == "fatal"
        assert none.text == hints.unexplained.replace("{ref}", ref).replace(
            "{code}", ""
        )


def test_ref_error_kinds_by_code():
    expected = {
        "too_large": "outcome",
        "not_found": "outcome",
        "upstream_status": "failure",
        "upstream_unreachable": "failure",
        "unsupported_form": "call",
        "unknown_upstream": "call",
        "traversal": "call",
        "workspace_unavailable": "failure",
    }
    assert dict(ref_errors.REF_ERROR_KINDS) == expected
    ref, hint_sets = _tool_hints()
    arg, hints = hint_sets[1]  # path(workspace_unavailable の文を持つ)
    for err in _CODES:
        got = ref_errors.explain_ref_error(ref, err, arg=arg, hints=hints).kind
        # 載っていない code(unexplained に落ちる)は fatal
        assert got == expected.get(err.code, "fatal"), err
    # 形の固定文は call
    assert ref_errors.url_error("x").kind == "call"
    # upstream_status は status で分ける: 404 / 410 は「無い」= outcome、それ以外は failure
    # (llens 判定 2026-10-04)
    for status, kind in (
        (404, "outcome"),
        (410, "outcome"),
        (401, "failure"),
        (403, "failure"),
        (400, "failure"),
        (500, "failure"),
        (503, "failure"),
        (301, "failure"),
        (None, "failure"),
    ):
        err = RefError("upstream_status", "x", status=status)
        assert ref_errors.explain_ref_error(ref, err, arg=arg, hints=hints).kind == (
            kind
        ), status
    for reason in ("redirect_self", "too_many_redirects"):
        err = RefError("upstream_status", "x", status=302, reason=reason)
        assert (
            ref_errors.explain_ref_error(ref, err, arg=arg, hints=hints).kind
            == "failure"
        )
    assert ref_errors.path_error("x").kind == "call"
    assert ref_errors.file_id_error("f").kind == "call"


# --- execute_tool_calls: 種別付きの結果 ---------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        ToolCallError("引数 x の形が違う", "x を整数で渡す。"),
        ToolFailure("上流に届かない", "時間を置いて呼び直す。"),
        ToolOutcome("一致する行が無い"),
        ToolFatal("sandbox の応答を読めない"),
    ],
)
def test_tool_error_becomes_text_and_kind(error):
    async def probe():
        raise error

    # 戻り型宣言(json 等)があっても ERROR 文は text で描く
    results = _run([_call("{}")], _tool(probe, render={"result": {"kind": "subagent"}}))
    assert results[0]["content"] == error.text
    assert results[0]["error_kind"] == error.kind
    assert "render" not in results[0]
    assert results[0]["duration"] >= 0


def test_success_has_no_error_kind():
    async def probe():
        return {"ok": True}

    results = _run([_call("{}")], _tool(probe))
    assert "error_kind" not in results[0]
    assert results[0]["render"] == {"kind": "json"}


def test_exception_is_fatal_with_the_class_name_only():
    """捕らえていない例外はバグ = fatal(赤)。文面は変えない。"""

    async def probe():
        raise RuntimeError("http://internal.example/secret " + "y" * 300)

    results = _run([_call("{}")], _tool(probe))
    assert results[0]["error_kind"] == "fatal"
    assert results[0]["content"] == tool_exception_text(
        "probe", RuntimeError("http://internal.example/secret " + "y" * 300)
    )
    assert results[0]["content"].startswith(
        "ERROR: ツール probe の実行中にエラーが起きた (RuntimeError: "
    )


def test_timeout_is_a_failure():
    async def probe():
        await asyncio.sleep(5)

    results = _run([_call("{}")], _tool(probe, timeout=0.05))
    assert results[0]["error_kind"] == "failure"
    assert results[0]["content"] == (
        "ERROR: ツール probe の実行が 0.05 秒の上限でタイムアウトした。"
        "次の手: 入力を小さく分けて呼び直す。分単位かかる処理で background を付けられるなら"
        " background: true で呼び直す。"
    )


def test_unknown_tool_is_a_violation():
    results = _run([_call("{}", name="ghost")], {})
    assert results[0]["error_kind"] == "violation"
    assert results[0]["content"] == (
        "ERROR: ツール ghost は今は使えない (このターンの一覧に無い)。"
        "次の手: 一覧にある tool だけを使う。"
        "load_skill が使えて、skill の tool なら先に読み込む。"
    )


@pytest.mark.parametrize("arguments", ["{not json", "[1, 2]", "null", "42"])
def test_unparseable_arguments_are_a_violation(arguments):
    calls = []

    async def probe(**kwargs):
        calls.append(kwargs)
        return "ok"

    results = _run([_call(arguments)], _tool(probe))
    assert calls == []  # tool は走らない(旧: 黙って {} で呼んでいた)
    assert results[0]["error_kind"] == "violation"
    assert results[0]["content"] == (
        "ERROR: ツール probe の引数を JSON のオブジェクトとして読めない。"
        "次の手: 引数を正しい JSON で呼び直す。"
    )


@pytest.mark.parametrize("arguments", ["", "   ", None])
def test_empty_arguments_still_mean_no_arguments(arguments):
    async def probe():
        return "ok"

    results = _run([_call(arguments)], _tool(probe))
    assert results[0]["content"] == "ok"
    assert "error_kind" not in results[0]


def test_missing_required_argument_is_a_violation():
    """#145 項目 2: 必須引数の欠落は生の TypeError ではなく専用の ERROR 文。"""
    calls = []

    def read_doc(path: str, offset: int = 0, __user__=None):
        calls.append(path)
        return "body"

    from nucllei.utils.tools import get_async_tool_function_and_apply_extra_params

    # 実際の tool と同じ包み方(予約引数を partial で束ね、sync はスレッドで)
    wrapped = get_async_tool_function_and_apply_extra_params(
        read_doc, {"__user__": {"id": "u1"}}
    )
    tools = _tool(
        wrapped,
        name="read_doc",
        properties={"path": {"type": "string"}, "offset": {"type": "integer"}},
    )
    results = _run([_call("{}", name="read_doc")], tools)
    assert calls == []
    assert results[0]["error_kind"] == "violation"
    assert results[0]["content"] == (
        "ERROR: ツール read_doc の必須の引数 path が無い。次の手: 引数を付けて呼び直す。"
    )
    # 揃っていれば通常どおり
    results = _run([_call('{"path": "/a"}', name="read_doc")], tools)
    assert results[0]["content"] == "body" and "error_kind" not in results[0]


def test_argument_in_spec_but_not_in_signature_is_a_violation():
    async def probe(a: str):
        return a

    tools = _tool(probe, properties={"a": {}, "b": {}})
    results = _run([_call('{"a": "x", "b": "y"}')], tools)
    assert results[0]["error_kind"] == "violation"
    assert results[0]["content"] == (
        "ERROR: ツール probe に引数 b は無い。次の手: その引数を外して呼び直す。"
    )
    # spec に無い引数は従来どおり黙って落とす(エラーにしない)
    results = _run([_call('{"a": "x", "zzz": 1}')], _tool(probe, properties={"a": {}}))
    assert results[0]["content"] == "x" and "error_kind" not in results[0]


def test_var_keyword_callable_skips_the_signature_check():
    async def probe(**kwargs):
        return sorted(kwargs)

    results = _run([_call('{"b": 1}')], _tool(probe, properties={"b": {}}))
    assert "error_kind" not in results[0]


# --- 引数を型の注釈に揃える(2026-10-18、run_shell の timeout_s="120" で落ちた件) ---------


def _typed_probe(calls):
    from typing import Annotated, Optional

    def probe(
        n: int = 0,
        t: Optional[int] = None,
        x: float = 0.0,
        flag: bool = False,
        tagged: Annotated[Optional[int], "meta"] = None,
        s: str = "",
        __user__=None,
    ):
        calls.append(dict(n=n, t=t, x=x, flag=flag, tagged=tagged, s=s))
        return "ok"

    from nucllei.utils.tools import get_async_tool_function_and_apply_extra_params

    # 実際の tool と同じ包み方(sync はスレッドで — partial の奥の注釈を読めること)
    wrapped = get_async_tool_function_and_apply_extra_params(
        probe, {"__user__": {"id": "u1"}}
    )
    names = ("n", "t", "x", "flag", "tagged", "s")
    return _tool(wrapped, properties={k: {} for k in names})


def test_string_numbers_are_coerced_to_the_annotated_type():
    calls = []
    tools = _typed_probe(calls)
    arguments = (
        '{"n": " 120 ", "t": "-3", "x": "1.5", "flag": "TRUE", "tagged": "7",'
        ' "s": "42"}'
    )
    results = _run([_call(arguments)], tools)
    assert results[0]["content"] == "ok" and "error_kind" not in results[0]
    assert calls == [dict(n=120, t=-3, x=1.5, flag=True, tagged=7, s="42")]

    # str 以外の値・None には触らない(広げない)
    calls.clear()
    results = _run([_call('{"n": 5, "t": null, "flag": false}')], tools)
    assert calls == [dict(n=5, t=None, x=0.0, flag=False, tagged=None, s="")]


@pytest.mark.parametrize(
    "arguments, name, label",
    [
        ('{"n": "abc"}', "n", ("整数", "整数で")),
        ('{"t": "1.5"}', "t", ("整数", "整数で")),
        ('{"x": "nan"}', "x", ("数値", "数値で")),
        ('{"flag": "yes"}', "flag", (" true か false", " true か false で")),
        ('{"flag": "1"}', "flag", (" true か false", " true か false で")),
    ],
)
def test_uncoercible_string_is_a_call_error(arguments, name, label):
    what, how = label
    calls = []
    results = _run([_call(arguments)], _typed_probe(calls))
    assert calls == []  # tool は走らない
    assert results[0]["error_kind"] == "call"
    value = arguments.split('": ')[1].rstrip("}")
    assert results[0]["content"] == (
        f"ERROR: ツール probe の引数 {name} は{what} ({value.replace(chr(34), chr(39))}"
        f" は読めない)。次の手: {name} を{how}渡して呼び直す。"
    )


def test_unannotated_callable_is_passed_through():
    async def probe(**kwargs):
        return kwargs

    results = _run([_call('{"b": "1"}')], _tool(probe, properties={"b": {}}))
    assert results[0]["content"] == '{"b": "1"}'


def test_returned_error_leading_string_is_a_normal_result():
    """返した文字列は何で始まっても通常の結果(ken 決定 — `ERROR:` の文字列を失敗と
    見なす読み替えはしない)。失敗を伝えるなら ToolError を raise する。"""
    text = "ERROR: 上流がエラーを返した。次の手: 呼び直す。"

    async def probe():
        return text

    for extra in ({"tool_id": "llens_cage"}, {"tool_id": "builtin_sandbox"}, {}):
        results = _run([_call("{}")], _tool(probe, **extra))
        assert results[0]["content"] == text, extra  # 文面は変えない
        assert "error_kind" not in results[0], extra


_CUSTOM_TOOL_SOURCE = """
from nucllei.utils.tool_errors import ToolCallError


class Tools:
    def lookup(self, code: str, __user__: dict = None) -> str:
        \"\"\"コードを引く。

        :param code: 引くコード
        \"\"\"
        if not code.isdigit():
            raise ToolCallError(f"code {code} は数字でない", "数字だけで渡す。")
        if code == "0":
            raise RuntimeError("boom")
        return f"found {code}"
"""


def test_custom_db_tool_can_raise_tool_error():
    """配備層のカスタム tool(DB の content を exec でロード)も ToolError を import して
    raise できる(README_API §5.1)。想定外の例外は汎用の fatal 経路。"""
    from nucllei.utils.plugin import load_tool_module_by_id
    from nucllei.utils.tools import get_async_tool_function_and_apply_extra_params

    module, _ = load_tool_module_by_id("custom_lookup", content=_CUSTOM_TOOL_SOURCE)
    callable_ = get_async_tool_function_and_apply_extra_params(
        module.lookup, {"__user__": {"id": "u1"}}
    )
    tools = _tool(
        callable_, name="lookup", properties={"code": {}}, tool_id="custom_lookup"
    )

    results = _run([_call('{"code": "abc"}', name="lookup")], tools)
    assert results[0]["error_kind"] == "call"
    assert results[0]["content"] == (
        "ERROR: code abc は数字でない。次の手: 数字だけで渡す。"
    )

    results = _run([_call('{"code": "0"}', name="lookup")], tools)
    assert results[0]["error_kind"] == "fatal"
    assert results[0]["content"] == tool_exception_text("lookup", RuntimeError("boom"))

    results = _run([_call('{"code": "12"}', name="lookup")], tools)
    assert results[0]["content"] == "found 12" and "error_kind" not in results[0]


_CUSTOM_TYPED_TOOL_SOURCE = """
from typing import Optional, Union


class Tools:
    def search(
        self,
        query: str,
        offset: int = 0,
        regex: bool = True,
        max_rows: Optional[int] = None,
        either: Union[int, str] = 0,
        loose=None,
        __user__: dict = None,
    ) -> str:
        \"\"\"検索する。\"\"\"
        return repr((query, offset, regex, max_rows, either, loose))
"""


def test_custom_db_tool_arguments_are_coerced_too():
    """配備層のカスタム tool(DB の content を exec でロード)も同じ呼び出し口で揃える。
    XML 形式の tool call を返すモデル(値がすべて文字列)で `regex: bool` に "false" が
    来ると、揃えなければ真として逆の動きをする。str の引数・注釈の無い引数・複数の型の
    Union には触らない。"""
    from nucllei.utils.plugin import load_tool_module_by_id
    from nucllei.utils.tools import get_async_tool_function_and_apply_extra_params

    module, _ = load_tool_module_by_id(
        "custom_search", content=_CUSTOM_TYPED_TOOL_SOURCE
    )
    callable_ = get_async_tool_function_and_apply_extra_params(
        module.search, {"__user__": {"id": "u1"}}
    )
    names = ("query", "offset", "regex", "max_rows", "either", "loose")
    tools = _tool(
        callable_,
        name="search",
        properties={k: {} for k in names},
        tool_id="custom_search",
    )

    arguments = (
        '{"query": "120", "offset": "5", "regex": "False", "max_rows": "10",'
        ' "either": "7", "loose": "8"}'
    )
    results = _run([_call(arguments, name="search")], tools)
    assert "error_kind" not in results[0], results[0]
    assert results[0]["content"] == repr(("120", 5, False, 10, "7", "8"))

    results = _run([_call('{"query": "q", "regex": "yes"}', name="search")], tools)
    assert results[0]["error_kind"] == "call"
    assert results[0]["content"] == (
        "ERROR: ツール search の引数 regex は true か false ('yes' は読めない)。"
        "次の手: regex を true か false で渡して呼び直す。"
    )


def test_one_failing_call_does_not_flag_its_siblings():
    async def ok():
        return "fine"

    async def bad():
        raise ToolOutcome("無い")

    tools = {**_tool(ok, name="ok"), **_tool(bad, name="bad")}
    results = _run(
        [_call("{}", name="ok", index=0), _call("{}", name="bad", index=1)], tools
    )
    assert "error_kind" not in results[0]
    assert results[1]["error_kind"] == "outcome"
    assert results[1]["content"] == "ERROR: 無い。"


# --- 結果に付ける旗(tool_output.mark_error_kind — raise しない)--------------------


def test_marked_result_keeps_text_render_and_side_channels():
    """中身のある結果に付けた失敗の旗は error_kind だけを足す — 本文・render・display は
    成功時と同じ(raise の ToolError だけが render と副産物を落とす)。"""
    from nucllei.utils import tool_output

    async def probe():
        tool_output.display("表示")
        tool_output.mark_error_kind("failure")
        return {"stdout": "x", "error": "Traceback"}

    results = _run([_call("{}")], _tool(probe))
    assert results[0]["error_kind"] == "failure"
    assert results[0]["content"] == '{"stdout": "x", "error": "Traceback"}'
    assert results[0]["render"] == {"kind": "json"}
    assert results[0]["display"] == [{"kind": "markdown", "content": "表示"}]


def test_marked_result_kind_is_validated_and_noop_outside_a_tool():
    from nucllei.utils import tool_output

    tool_output.mark_error_kind("failure")  # sink 無し — no-op
    sink = tool_output.ToolOutputSink()
    for bad in ("violation", "oops"):
        with pytest.raises(ValueError):
            sink.mark_error_kind(bad)
    assert sink.error_kind() is None
    sink.mark_error_kind("fatal")
    assert sink.error_kind() == "fatal"


def test_raised_error_wins_over_a_mark():
    from nucllei.utils import tool_output

    async def probe():
        tool_output.mark_error_kind("failure")
        raise ToolFatal("壊れた")

    results = _run([_call("{}")], _tool(probe))
    assert results[0]["error_kind"] == "fatal"
    assert results[0]["content"] == "ERROR: 壊れた。"
