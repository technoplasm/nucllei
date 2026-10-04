"""utils/ref_errors — resolver の RefError → モデル向け ERROR 文 (全 tool 共通) の番兵。

feed_image / fetch_url / sandbox(send_to_workspace(url=)) が別々に持っていた 3 本の
`_explain_ref_error` を 1 箇所にまとめた (#141)。ここで見るのは:
- code / reason ごとに分岐が届くこと (全 code・全 reason)
- 既定の文面 = feed_image と fetch_url が同文で持っていた言い方 (url 向け)
- hints による差し替えが効くこと、`arg` で形の固定文を選ぶこと
- 3 本の呼び手が渡す hints を通した文面を全文で固定すること (モデルに見える文を黙って
  変えない。2026-10-04 に「事実の文 + 次の手:」の形へ意図して書き換えた — README_API §5.1)
- モデルに見える文が「案内」「職員」・敬体・裸の `/ext/` `/api/v1/files/` を含まないこと
"""

from nucllei.utils.image_refs import (
    FILE_ID_ARG_FORM,
    PATH_ARG_FORM,
    REF_ARGS_RULE,
    URL_ARG_FORM,
    RefError,
)
from nucllei.utils.ref_errors import (
    UNKNOWN_UPSTREAM_WHAT,
    RefErrorHints,
    explain_ref_error,
    mb,
)
from nucllei.utils import ref_errors as _ref_errors
from test.util.tool_errors import text_of

# #145: ref_errors は ToolError を返す(呼び手が raise)。文面の番兵は `.text` で全文比較する
url_error = text_of(_ref_errors.url_error)
path_error = text_of(_ref_errors.path_error)
file_id_error = text_of(_ref_errors.file_id_error)

U = "https://his.test/docs/a.pdf"
P = "/workspace/out/a.pdf"


def _hints(**kw):
    # 差し替えテンプレートも `ERROR: 事実。次の手: …` の形で書く(#145 — ToolError.from_text が
    # 事実と次の手に分けて組み直すので、形の崩れたテンプレートは組み直しで「ERROR: 」「。」が付く)
    kw.setdefault(
        "too_large", lambda ref, err: f"ERROR: TOO_LARGE {ref} {err.size} {err.limit}。"
    )
    kw.setdefault("unexplained", "ERROR: UNEXPLAINED {ref} ({code})。")
    return RefErrorHints(**kw)


def _explain(err, arg="url", **kw):
    return explain_ref_error(
        U if arg == "url" else P, err, arg=arg, hints=_hints(**kw)
    ).text


# --- 固定文のヘルパ --------------------------------------------------------------


def test_fixed_form_helpers_compose_the_shared_sentences():
    assert (
        url_error("x")
        == f"ERROR: x は URL として解決できない。次の手: {URL_ARG_FORM} {REF_ARGS_RULE}"
    )
    assert (
        url_error("")
        == f"ERROR: (空) は URL として解決できない。次の手: {URL_ARG_FORM} {REF_ARGS_RULE}"
    )
    assert (
        path_error("x")
        == f"ERROR: x は sandbox のパスの形でない。次の手: {PATH_ARG_FORM} {REF_ARGS_RULE}"
    )
    assert path_error("").startswith("ERROR: (空) は sandbox のパスの形でない。")
    assert (
        file_id_error("f1")
        == f"ERROR: file_id f1 のレコードが無い、またはアクセス権が無い。次の手: {FILE_ID_ARG_FORM}"
    )
    assert mb(32_646_426) == "31.1 MB" and mb(25 * 1024 * 1024) == "25.0 MB"


# --- code ごとの分岐 ----------------------------------------------------------------


def test_none_and_unknown_codes_fall_back_to_unexplained():
    assert _explain(None) == f"ERROR: UNEXPLAINED {U} ()。"
    assert (
        _explain(RefError("something_new", "secret detail"))
        == f"ERROR: UNEXPLAINED {U} (something_new)。"
    )


def test_too_large_is_delegated_to_the_hint():
    assert (
        _explain(RefError("too_large", "x", size=3, limit=2))
        == f"ERROR: TOO_LARGE {U} 3 2。"
    )


def test_not_found_default_and_override():
    assert _explain(RefError("not_found", "x")) == (
        f"ERROR: {U} は存在しない (404 / ファイル無し)。次の手: ユーザーにはリンク切れと伝える。"
    )
    assert (
        _explain(RefError("not_found", "x"), arg="path", not_found="ERROR: NF {ref}。")
        == f"ERROR: NF {P}。"
    )


def test_upstream_status_redirect_reasons_and_plain_status():
    to_self = RefError("upstream_status", "x", status=302, reason="redirect_self")
    assert _explain(to_self) == (
        f"ERROR: {U} の redirect (302) の転送先が このサーバ自身 (または同居するサービス) なので"
        f"辿らなかった。次の手: ユーザーに示すなら [ラベル]({U}) のリンクにする (ブラウザでは開ける)。"
    )
    assert (
        _explain(to_self, redirect_self="ERROR: RS {ref} {status}。")
        == f"ERROR: RS {U} 302。"
    )
    loop = RefError("upstream_status", "x", status=302, reason="too_many_redirects")
    assert _explain(loop) == (
        f"ERROR: {U} は redirect が 4 回以上続いた (辿るのは 3 回まで。転送のループか、ログインが要るページの"
        "ことが多い)。次の手: 転送先のページを直接指す URL を渡す。"
        f"ユーザーに示すなら [ラベル]({U}) のリンクにする (ブラウザでは開ける)。"
    )
    assert _explain(loop, too_many_redirects="ERROR: TM {ref}。") == f"ERROR: TM {U}。"
    # reason 無しの 3xx (転送先が URL として組めない): 前半は共通、続きは hint
    assert _explain(RefError("upstream_status", "x", status=301)) == (
        f"ERROR: {U} は redirect (301) を返した。転送先を辿れなかった (転送先が示されていないか、"
        "取りに行ける URL ではない)。次の手: 転送先のページを直接指す URL が分かるならそれを渡す。"
        f"分からなければ、ユーザーには [ラベル]({U}) のリンクで示す (ブラウザでは開ける)。"
    )
    assert _explain(
        RefError("upstream_status", "x", status=301), redirect_next="NEXT。"
    ) == (f"ERROR: {U} は redirect (301) を返した。NEXT。")
    for status in (401, 403, 500, 503):
        assert _explain(RefError("upstream_status", "x", status=status)) == (
            f"ERROR: {U} の上流が {status} を返した。次の手: 5xx なら時間を置いて 1 回だけ再試行する"
            " (401 / 403 などは再試行しない)。"
            f"ユーザーに示すなら [ラベル]({U}) のリンクにする (ブラウザでは開ける)。"
        )
    assert _explain(RefError("upstream_status", "x")) == (
        f"ERROR: {U} の上流が エラー を返した。次の手: 5xx なら時間を置いて 1 回だけ再試行する"
        " (401 / 403 などは再試行しない)。"
        f"ユーザーに示すなら [ラベル]({U}) のリンクにする (ブラウザでは開ける)。"
    )


def test_upstream_unreachable_with_and_without_reason():
    assert (
        _explain(RefError("upstream_unreachable", "x", reason="unresolvable"))
        == _UNRESOLVABLE
    )
    assert _explain(RefError("upstream_unreachable", "x")) == _UNREACHABLE
    assert (
        _explain(RefError("upstream_unreachable", "x"), unreachable="ERROR: UR {ref}。")
        == f"ERROR: UR {U}。"
    )
    assert (
        _explain(
            RefError("upstream_unreachable", "x", reason="unresolvable"),
            unresolvable="ERROR: UN {ref}。",
        )
        == f"ERROR: UN {U}。"
    )


def test_unsupported_form_and_traversal_follow_the_argument():
    assert _explain(RefError("unsupported_form", "x")) == url_error(U)
    assert _explain(RefError("unsupported_form", "x"), arg="path") == path_error(P)
    trav = "は許可された範囲の外を指している (上の階層への参照 `..` や制御文字を含む)。"
    assert (
        _explain(RefError("traversal", "x"))
        == f"ERROR: {U} {trav}次の手: {URL_ARG_FORM}"
    )
    assert (
        _explain(RefError("traversal", "x"), arg="path")
        == f"ERROR: {P} {trav}次の手: {PATH_ARG_FORM}"
    )


def test_unknown_upstream_by_reason_with_extra_and_fallback():
    for reason, what in UNKNOWN_UPSTREAM_WHAT.items():
        err = RefError("unknown_upstream", "detail must not leak", reason=reason)
        assert _explain(err) == f"ERROR: {U} {what}。次の手: {URL_ARG_FORM}", reason
        assert "detail must not leak" not in _explain(err)
    # 知らない reason は None の 1 文面。extra は「。」の後、形の文の前
    err = RefError("unknown_upstream", "x", reason="brand_new_reason")
    assert (
        _explain(err)
        == f"ERROR: {U} {UNKNOWN_UPSTREAM_WHAT[None]}。次の手: {URL_ARG_FORM}"
    )
    assert _explain(err, unknown_upstream_extra="許可先は X。") == (
        f"ERROR: {U} {UNKNOWN_UPSTREAM_WHAT[None]}。次の手: 許可先は X。{URL_ARG_FORM}"
    )
    # 表の差し替え (sandbox は全部を自分の言い方で持つ)
    table = {**UNKNOWN_UPSTREAM_WHAT, "server_self": "は別の言い方"}
    err = RefError("unknown_upstream", "x", reason="server_self")
    assert (
        _explain(err, unknown_upstream=table)
        == f"ERROR: {U} は別の言い方。次の手: {URL_ARG_FORM}"
    )


def test_workspace_unavailable_needs_a_hint_or_falls_back():
    err = RefError("workspace_unavailable", "SANDBOX_URL is not configured")
    assert (
        _explain(err, arg="path") == f"ERROR: UNEXPLAINED {P} (workspace_unavailable)。"
    )
    assert (
        _explain(err, arg="path", workspace_unavailable="ERROR: WU {ref}。")
        == f"ERROR: WU {P}。"
    )


def test_templates_are_filled_by_replace_not_format():
    """`URL_ARG_FORM` の `{id}` を str.format に噛ませない — hint に `{id}` が入っていても壊れない。"""
    assert (
        _explain(RefError("not_found", "x"), not_found="ERROR: {ref} {id} {status}。")
        == f"ERROR: {U} {{id}} エラー。"
    )


# --- 3 本の呼び手の hints を通した文面 (全文で固定) ----------------------------------

_LINK = "ユーザーに示すなら [ラベル]({u}) のリンクにする (ブラウザでは開ける)。"
_LINK_OTHERWISE = (
    "分からなければ、ユーザーには [ラベル]({u}) のリンクで示す (ブラウザでは開ける)。"
)
_NOT_FOUND = f"ERROR: {U} は存在しない (404 / ファイル無し)。次の手: ユーザーにはリンク切れと伝える。"
_REDIRECT = (
    f"ERROR: {U} は redirect (302) を返した。転送先を辿れなかった (転送先が示されていないか、"
    "取りに行ける URL ではない)。次の手: 転送先のページを直接指す URL が分かるならそれを渡す。"
    + _LINK_OTHERWISE.format(u=U)
)
_REDIRECT_SELF = (
    f"ERROR: {U} の redirect (302) の転送先が このサーバ自身 (または同居するサービス) なので"
    "辿らなかった。次の手: " + _LINK.format(u=U)
)
_TOO_MANY = (
    f"ERROR: {U} は redirect が 4 回以上続いた (辿るのは 3 回まで。転送のループか、ログインが要る"
    "ページのことが多い)。次の手: 転送先のページを直接指す URL を渡す。"
    + _LINK.format(u=U)
)
_UNREACHABLE = (
    f"ERROR: {U} のホストに届かない (このサーバからは到達できないか、一時的な障害)。"
    f"次の手: ユーザーに示すなら [ラベル]({U}) のリンクにする (ブラウザでは開けることがある)。"
)
_UNRESOLVABLE = (
    f"ERROR: {U} のホスト名を解決できない。次の手: IP アドレスの URL が分かっているならそれで"
    "呼び直す (ホスト名を推測で IP に書き換えない)。" + _LINK_OTHERWISE.format(u=U)
)
_SERVER_SELF = (
    f"ERROR: {U} は このサーバ自身 (または同居するサービス) を指しているので取りに行かない。"
    f"次の手: {URL_ARG_FORM}"
)
_NOT_ALLOWED = f"ERROR: {U} は取得を許可されていない URL。次の手: {URL_ARG_FORM}"


def test_sandbox_wording():
    """send_to_workspace(url=): too_large と unexplained 以外は共通の既定文 (2026-10-04 に
    「登録した配信元」「許可先の外」「3 回を超えて」の独自文面を外した)。"""
    from nucllei.builtin_tools.sandbox import _explain_ref_error

    sandbox_explain = text_of(_explain_ref_error)

    cases = {
        RefError(
            "too_large", "x", size=3 * 1024 * 1024, limit=1024 * 1024, size_exact=True
        ): (
            f"ERROR: {U} は 3.0MB で、取り込みの上限 1.0MB を超えている。sandbox には取り込めない "
            "(上限は管理者の設定で、引数では上げられない)。リンク切れではない。次の手: "
            + _LINK.format(u=U)
        ),
        RefError("not_found", "x"): _NOT_FOUND,
        RefError(
            "upstream_status", "x", status=302, reason="redirect_self"
        ): _REDIRECT_SELF,
        RefError(
            "upstream_status", "x", status=302, reason="too_many_redirects"
        ): _TOO_MANY,
        RefError("upstream_status", "x", status=302): _REDIRECT,
        RefError("upstream_status", "x", status=503): (
            f"ERROR: {U} の上流が 503 を返した。次の手: 5xx なら時間を置いて 1 回だけ再試行する"
            " (401 / 403 などは再試行しない)。" + _LINK.format(u=U)
        ),
        RefError("upstream_unreachable", "x", reason="unresolvable"): _UNRESOLVABLE,
        RefError("upstream_unreachable", "x"): _UNREACHABLE,
        RefError("unknown_upstream", "x", reason="server_self"): _SERVER_SELF,
        # unknown_name / not_ext_form は 2026-10-03 に無くした — 知らない reason は 1 文面
        RefError("unknown_upstream", "x", reason="unknown_name"): _NOT_ALLOWED,
        RefError("unknown_upstream", "x"): _NOT_ALLOWED,
        RefError("traversal", "x"): (
            f"ERROR: {U} は許可された範囲の外を指している (上の階層への参照 `..` や制御文字を含む)。次の手: {URL_ARG_FORM}"
        ),
        RefError(
            "unsupported_form", "x"
        ): f"ERROR: {U} は URL として解決できない。次の手: {URL_ARG_FORM} {REF_ARGS_RULE}",
        # code (内部名) は出さない
        RefError("something_new", "x"): f"ERROR: {U} を取得できない。",
    }
    for err, want in cases.items():
        assert sandbox_explain(U, err) == want, err


def test_feed_image_wording():
    from nucllei.builtin_tools.feed_image import _explain_ref_error

    feed_explain = text_of(_explain_ref_error)

    cases_url = {
        RefError("not_found", "x"): _NOT_FOUND,
        RefError("upstream_status", "x", status=302): _REDIRECT,
        RefError("upstream_unreachable", "x"): _UNREACHABLE,
        RefError("unknown_upstream", "x", reason="server_self"): _SERVER_SELF,
        RefError("unknown_upstream", "x"): _NOT_ALLOWED,
        RefError("unknown_upstream", "x", reason="not_ext_form"): _NOT_ALLOWED,
        RefError(
            "something_new", "x"
        ): f"ERROR: {U} を読めない (存在しない / 許可された場所の外 / 大きすぎる)。",
    }
    for err, want in cases_url.items():
        assert feed_explain(U, err) == want, err
    cases_path = {
        RefError("not_found", "x"): (
            f"ERROR: {P} は存在しない。次の手: run_shell が使えるなら ls で確かめる。"
        ),
        RefError("upstream_unreachable", "x"): (
            f"ERROR: {P} に届かない (このサーバからは到達できないか、一時的な障害)。"
            "次の手: run_shell が使えるなら ls で存在を確かめる。"
        ),
        RefError("workspace_unavailable", "x"): (
            f"ERROR: {P} は sandbox のファイルだが、sandbox に繋がらない (起動していないか、応答が無い)。"
            "次の手: 時間を置いて 1 回だけ呼び直す。続くならユーザーに sandbox が使えないと伝える。"
        ),
        RefError("traversal", "x"): (
            f"ERROR: {P} は許可された範囲の外を指している (上の階層への参照 `..` や制御文字を含む)。次の手: {PATH_ARG_FORM}"
        ),
        RefError("unsupported_form", "x"): path_error(P),
    }
    for err, want in cases_path.items():
        assert feed_explain(P, err, True) == want, err
    # `/ext/…` の not_found は fetch_from_workspace と同文 (このサーバの /ext に無い)
    from nucllei.builtin_tools import sandbox

    _explain_mount_error = text_of(sandbox._explain_mount_error)

    ext = "/ext/doc/a.pdf"
    assert (
        feed_explain(ext, RefError("not_found", "x"), True)
        == _explain_mount_error(ext, RefError("not_found", "x"))
        == (
            f"ERROR: {ext} はこのサーバの /ext に無い (存在しないか、sandbox にだけ mount"
            " されている)。次の手: 中身が要るなら sandbox で読んで使う。"
        )
    )
    # too_large (url / path)
    big = RefError(
        "too_large", "x", size=32_646_426, limit=25 * 1024 * 1024, size_exact=True
    )
    assert feed_explain(U, big) == (
        f"ERROR: {U} は 31.1 MB あり、取得の上限 25.0 MB を超えている。このままでは自分の目に入れられない"
        " (PDF は pages を絞っても上限は変わらない)。次の手: 画像で send_to_workspace と run_python が"
        "使えるなら、send_to_workspace(url=…) で sandbox に取り込み、run_python で縮小してから"
        " /workspace/… の path を feed_image(path=…) に渡す。PDF のテキストが要り、run_subagent と"
        " fetch_url が使えるなら、run_subagent の子が fetch_url を max_mb 付きで読む。"
        + _LINK.format(u=U)
    )
    assert feed_explain(P, big, True) == (
        f"ERROR: {P} は 31.1 MB あり、取得の上限 25.0 MB を超えている。このままでは自分の目に入れられない"
        " (PDF は pages を絞っても上限は変わらない)。次の手: 画像で run_python が使えるなら、このパスを"
        "開いて縮小し、出力を /workspace/… に書いてからその path を feed_image(path=…) に渡す。"
        "sandbox のファイルなので、fetch_from_workspace が使えるなら、ユーザーに見せるときはそれに path を"
        "渡し、返ったリンクを本文に書く。"
    )
    assert feed_explain(U, RefError("too_large", "x")).startswith(
        f"ERROR: {U} は 取得の上限 を超えている。このままでは自分の目に入れられない"
    )


def test_fetch_url_wording(monkeypatch):
    """原本ホスト (EXT_PUBLIC_BASES) の句は 2026-10-03 に設定ごと無くなった。"""
    from nucllei.builtin_tools import fetch_url

    explain = text_of(fetch_url._explain_ref_error)
    cases = {
        RefError("not_found", "x"): _NOT_FOUND,
        RefError("upstream_status", "x", status=302): _REDIRECT,
        RefError(
            "upstream_status", "x", status=302, reason="redirect_self"
        ): _REDIRECT_SELF,
        RefError("unknown_upstream", "x", reason="server_self"): _SERVER_SELF,
        RefError("unknown_upstream", "x", reason="unknown_name"): _NOT_ALLOWED,
        RefError("unknown_upstream", "x"): _NOT_ALLOWED,
        RefError("unsupported_form", "x"): url_error(U),
        RefError("something_new", "x"): (
            f"ERROR: {U} を取得できない (届かない / 存在しない / redirect を辿れない / 大きすぎる"
            " のいずれか)。次の手: " + _LINK.format(u=U)
        ),
    }
    for err, want in cases.items():
        assert explain(U, err, 200.0) == want, err
    assert explain(U, None, 200.0) == cases[RefError("something_new", "x")]
    big = RefError(
        "too_large", "x", size=32_646_426, limit=25 * 1024 * 1024, size_exact=True
    )
    assert explain(U, big, 200.0) == (
        f"ERROR: {U} は 31.1 MB で、取得の上限 25.0 MB を超えている。次の手: この会話では読まない。"
        f'run_subagent が使えるなら URL と指示を渡し、子が fetch_url(url="{U}", max_mb=32) で読む'
        " (子は offset で続きを取れる)。pandas 等で加工・集計したいだけなら、send_to_workspace が"
        "使えるなら send_to_workspace(url=…) で sandbox に取り込む (読むためのテキスト化には使わない)。"
        "中身が要らなければ、" + _LINK.format(u=U)
    )


def test_model_visible_texts_name_no_deployment():
    """モデルに見える文面 (ERROR 文の既定値・tool 固有の言い分け・docstring) は配備を
    前提にしない — 特定サイトのページ名・本番ホスト・配備層の tool 名を書かない
    (2026-10-04、ken: nucllei は llens を前提にしない)。"""
    import dataclasses

    from nucllei.builtin_tools import feed_image, fetch_url
    from nucllei.utils.ref_errors import RefErrorHints

    texts = [
        f.default
        for f in dataclasses.fields(RefErrorHints)
        if isinstance(f.default, str)
    ]
    texts += [
        fetch_url._ERR_UNEXPLAINED,
        feed_image.Tools.feed_image.__doc__,
        fetch_url.Tools.fetch_url.__doc__,
    ]
    for is_path in (False, True):
        h = feed_image._hints(is_path)
        texts += [h.redirect_next, h.unreachable, h.not_found]
    for text in texts:
        for gone in (
            "top.aspx",
            "172.16.",
            "his-info",
            "院内 Web",
            "read_doc",
            "grep_doc",
        ):
            assert gone not in text, (gone, text)


# --- 監査: モデルに見える文の書き方 (2026-10-04、llens の監査依頼) -----------------------
#
# tool が返す文はモデルが本文へ写しうる。内部向けの指示語 (「〜を案内」) が見出しに写り、
# 裸の相対パス `/ext/…` がそのまま答えに出た (staging の実例)。owned な 6 ファイルの
# 文字列リテラル (docstring・log・Field の description を除く = モデルに返る文の部品) と
# 公開メソッドの docstring を走査する。

_AUDITED_MODULES = (
    "nucllei.utils.ref_errors",
    "nucllei.builtin_tools.feed_image",
    "nucllei.builtin_tools.fetch_url",
    "nucllei.builtin_tools.sandbox",
    "nucllei.builtin_tools.context_budget",
    "nucllei.builtin_tools.attachment_text",
)
_FORBIDDEN = ("案内", "職員", "です", "ます。", "ました", "ません", "ください")


def _model_visible_literals(module_name):
    """module の文字列リテラルのうち、モデルに返る文の部品になりうるもの。"""
    import ast
    import importlib
    import inspect

    module = importlib.import_module(module_name)
    tree = ast.parse(inspect.getsource(module))
    skip = set()
    for node in ast.walk(tree):
        # docstring (module / class / function)
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
            ):
                skip.add(id(body[0].value))
        # log.* の引数と Field(description=…) (管理画面向け) はモデルに返らない
        if isinstance(node, ast.Call):
            func = node.func
            is_log = (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "log"
            )
            is_field = isinstance(func, ast.Name) and func.id == "Field"
            if is_log or is_field:
                for sub in ast.walk(node):
                    skip.add(id(sub))
    out = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in skip
        ):
            out.append(node.value)
    # 公開メソッド (= tool 関数) の docstring はモデルに見える
    tools = getattr(module, "Tools", None)
    if tools is not None:
        for name, fn in vars(tools).items():
            if not name.startswith("_") and callable(fn) and fn.__doc__:
                out.append(fn.__doc__)
    return out


def _bare_paths(text):
    """`](…)` の中でも backtick の中でもない `/ext/` `/api/v1/files/` の出現。"""
    import re

    stripped = re.sub(r"`[^`]*`", "", text)
    hits = []
    for m in re.finditer(r"/ext/|/api/v1/files/", stripped):
        if stripped[max(0, m.start() - 2) : m.start()] != "](":
            hits.append(m.group(0))
    return hits


def _is_prose(text):
    """日本語か空白を含む = 文。URL を組むだけの断片 (`"/api/v1/files/"`) は対象外。"""
    return any(ord(ch) > 0x2FFF for ch in text) or " " in text


def test_model_visible_texts_follow_the_wording_rules():
    """「案内」「職員」・敬体を書かない。ユーザーに渡すパスは `[ラベル](URL)` のリンクで
    書く (裸の `/ext/…` `/api/v1/files/…` を出さない)。README_API §5.1。"""
    for module_name in _AUDITED_MODULES:
        for text in _model_visible_literals(module_name):
            for word in _FORBIDDEN:
                assert word not in text, (module_name, word, text)
            if _is_prose(text):
                assert not _bare_paths(text), (module_name, text)


def test_generated_error_texts_follow_the_wording_rules():
    """組み上がった ERROR 文 (全 code × 3 本の tool) でも同じ規則。参照そのもの (`{ref}`) の
    エコーは入力の写しなので除いて見る。リンクを示す文は必ず `次の手:` の後ろにある。"""
    from nucllei.builtin_tools import feed_image, fetch_url, sandbox

    codes = [
        RefError("too_large", "x", size=30 * 1024 * 1024, limit=25 * 1024 * 1024),
        RefError(
            "too_large",
            "x",
            size=30 * 1024 * 1024,
            limit=25 * 1024 * 1024,
            size_exact=True,
        ),
        RefError("too_large", "x", size=300 * 1024 * 1024, limit=1, size_exact=True),
        RefError("not_found", "x"),
        RefError("upstream_status", "x", status=302),
        RefError("upstream_status", "x", status=302, reason="redirect_self"),
        RefError("upstream_status", "x", status=302, reason="too_many_redirects"),
        RefError("upstream_status", "x", status=500),
        RefError("upstream_unreachable", "x"),
        RefError("upstream_unreachable", "x", reason="unresolvable"),
        RefError("unknown_upstream", "x", reason="server_self"),
        RefError("unknown_upstream", "x"),
        RefError("traversal", "x"),
        RefError("unsupported_form", "x"),
        RefError("workspace_unavailable", "x"),
        RefError("brand_new", "x"),
        None,
    ]
    ref = "/ext/a/b.pdf"
    texts = []
    for err in codes:
        texts += [
            feed_image._explain_ref_error(ref, err).text,
            feed_image._explain_ref_error(ref, err, True).text,
            fetch_url._explain_ref_error(ref, err, 200.0).text,
        ]
        if err is not None:
            texts += [sandbox._explain_ref_error(ref, err).text]
    for text in texts:
        for word in _FORBIDDEN:
            assert word not in text, (word, text)
        rest = text.replace(f"[ラベル]({ref})", "[ラベル](LINK)").replace(ref, "REF")
        assert not _bare_paths(rest), text
        assert "/ext/" not in rest.replace("`/ext/…`", ""), text
        if "[ラベル](" in text or "リンク切れと伝える" in text:
            assert text.index("次の手: ") < text.index("ユーザー"), text
        # モデルだけに向けた文 (引数の形・行き先の規則) は次の手の後ろ (llens 監査 A5)
        _assert_model_only_after_next(text)


# llens はモデルに「事実の文だけを伝え、次の手は回答に写さない」と指示する (2026-10-04 監査)。
# モデルだけに向けた文は必ず `次の手:` の後ろに置く
_MODEL_ONLY = (
    URL_ARG_FORM,
    PATH_ARG_FORM,
    FILE_ID_ARG_FORM,
    REF_ARGS_RULE,
    "本文に貼り直さない",
    "回答に写さない",
)


def _assert_model_only_after_next(text):
    for phrase in _MODEL_ONLY:
        if phrase in text:
            assert "次の手: " in text, (phrase, text)
            assert text.index("次の手: ") < text.index(phrase), (phrase, text)


def test_model_only_sentences_come_after_next_step():
    """形の固定文・表示の指示・通知の書式の指示は事実の文に混ぜない (A5 / A19 / B1)。"""
    from nucllei.builtin_tools import sandbox
    from nucllei.utils.execution_resume import NOTICE_HEADER
    from nucllei.utils.image_refs import WORKSPACE_PATH_ARG_FORM

    texts = [
        url_error("x"),
        url_error(""),
        path_error("x"),
        file_id_error("f1"),
        sandbox._DISPLAYED,
        sandbox._file_link_note("a.csv", "f1"),
        NOTICE_HEADER,
        text_of(sandbox._resolve_workspace_path)("rel/x", WORKSPACE_PATH_ARG_FORM),
        text_of(sandbox._resolve_workspace_path)("https://x/y", PATH_ARG_FORM),
    ]
    for text in texts:
        assert text, texts
        _assert_model_only_after_next(text)
    # 表示の指示そのものも次の手の後ろ
    assert sandbox._file_link_note("a.csv", "f1").startswith("次の手: ")
    assert NOTICE_HEADER.index("次の手: ") < NOTICE_HEADER.index("await_task")


def test_sandbox_error_texts_carry_no_exception_or_env_names():
    """sandbox の失敗はモデルに例外の文・HTTP の detail・env 名を返さない (A17 / A18)。
    中身はログにだけ残す。"""
    import aiohttp

    from nucllei.builtin_tools import sandbox

    secret = "http://sandbox.internal:8000/files/read secret-detail"
    errors = [
        aiohttp.ClientConnectionError(secret),
        TimeoutError(secret),
        sandbox.SandboxHTTPError(400, secret),
        sandbox.SandboxHTTPError(401, "SANDBOX_TOKEN が一致していない " + secret),
        sandbox.SandboxHTTPError(404, secret),
        sandbox.SandboxHTTPError(413, secret),
        sandbox.SandboxHTTPError(502, secret),
    ]
    texts = []
    for e in errors:
        texts += [
            sandbox._files_error(e, "/workspace/a.txt").text,
            sandbox._exec_error(e).text,
        ]
    for text in texts:
        assert text.startswith("ERROR: "), text
        assert "次の手: " in text, text
        for leak in ("secret-detail", "sandbox.internal", "SANDBOX_TOKEN", "Error("):
            assert leak not in text, (leak, text)
    auth = sandbox._files_error(sandbox.SandboxHTTPError(401, "x"), "/workspace/a").text
    assert (
        auth
        == sandbox._SANDBOX_AUTH
        == sandbox._exec_error(sandbox.SandboxHTTPError(401, "x")).text
    )
    assert "呼び直さない" in auth
