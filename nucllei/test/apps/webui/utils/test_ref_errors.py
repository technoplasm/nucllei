"""utils/ref_errors — resolver の RefError → モデル向け ERROR 文 (全 tool 共通) の番兵。

feed_image / fetch_url / sandbox(send_to_workspace(url=)) が別々に持っていた 3 本の
`_explain_ref_error` を 1 箇所にまとめた (#141)。ここで見るのは:
- code / reason ごとに分岐が届くこと (全 code・全 reason)
- 既定の文面 = feed_image と fetch_url が同文で持っていた言い方 (url 向け)
- hints による差し替えが効くこと、`arg` で形の固定文を選ぶこと
- 3 本の呼び手が渡す hints を通した文面が、移設前の llens / sandbox の原文と一致すること
  (同じ状況の別の言い方を、まとめるときに黙って変えていない)
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
    file_id_error,
    mb,
    path_error,
    url_error,
)

U = "https://his.test/docs/a.pdf"
P = "/workspace/out/a.pdf"


def _hints(**kw):
    kw.setdefault(
        "too_large", lambda ref, err: f"TOO_LARGE {ref} {err.size} {err.limit}"
    )
    kw.setdefault("unexplained", "UNEXPLAINED {ref} ({code})")
    return RefErrorHints(**kw)


def _explain(err, arg="url", **kw):
    return explain_ref_error(U if arg == "url" else P, err, arg=arg, hints=_hints(**kw))


# --- 固定文のヘルパ --------------------------------------------------------------


def test_fixed_form_helpers_compose_the_shared_sentences():
    assert (
        url_error("x")
        == f"ERROR: x は URL として解決できない。{URL_ARG_FORM} {REF_ARGS_RULE}"
    )
    assert (
        url_error("")
        == f"ERROR: (空) は URL として解決できない。{URL_ARG_FORM} {REF_ARGS_RULE}"
    )
    assert (
        path_error("x")
        == f"ERROR: x は sandbox のパスの形でない。{PATH_ARG_FORM} {REF_ARGS_RULE}"
    )
    assert path_error("").startswith("ERROR: (空) は sandbox のパスの形でない。")
    assert (
        file_id_error("f1")
        == f"ERROR: file_id f1 のレコードが無い、またはアクセス権が無い。{FILE_ID_ARG_FORM}"
    )
    assert mb(32_646_426) == "31.1 MB" and mb(25 * 1024 * 1024) == "25.0 MB"


# --- code ごとの分岐 ----------------------------------------------------------------


def test_none_and_unknown_codes_fall_back_to_unexplained():
    assert _explain(None) == f"UNEXPLAINED {U} ()"
    assert (
        _explain(RefError("something_new", "secret detail"))
        == f"UNEXPLAINED {U} (something_new)"
    )


def test_too_large_is_delegated_to_the_hint():
    assert _explain(RefError("too_large", "x", size=3, limit=2)) == f"TOO_LARGE {U} 3 2"


def test_not_found_default_and_override():
    assert _explain(RefError("not_found", "x")) == (
        f"ERROR: {U} は存在しない (404 / ファイル無し)。リンク切れとして案内する。"
    )
    assert (
        _explain(RefError("not_found", "x"), arg="path", not_found="NF {ref}")
        == f"NF {P}"
    )


def test_upstream_status_redirect_reasons_and_plain_status():
    to_self = RefError("upstream_status", "x", status=302, reason="redirect_self")
    assert _explain(to_self) == (
        f"ERROR: {U} の redirect の転送先が このサーバ自身 (または同居するサービス) なので"
        "辿らなかった。ユーザーには URL をそのまま案内する。"
    )
    assert _explain(to_self, redirect_self="RS {ref} {status}") == f"RS {U} 302"
    loop = RefError("upstream_status", "x", status=302, reason="too_many_redirects")
    assert _explain(loop) == (
        f"ERROR: {U} は redirect が 4 回以上続いた (辿るのは 3 回まで)。転送先のページを直接指す URL を渡すこと。"
    )
    assert _explain(loop, too_many_redirects="TM {ref}") == f"TM {U}"
    # reason 無しの 3xx: 「何が起きたか」は共通、次の一手は hint
    assert _explain(RefError("upstream_status", "x", status=301)) == (
        f"ERROR: {U} は redirect (301) を返した。redirect は辿らないので、ディレクトリではなく"
        "ページを直接指すこと (例 '<ルート>/' ではなく '<ルート>/top.aspx')。"
    )
    assert _explain(
        RefError("upstream_status", "x", status=301), redirect_next="NEXT"
    ) == (f"ERROR: {U} は redirect (301) を返した。NEXT")
    for status in (401, 403, 500, 503):
        assert _explain(RefError("upstream_status", "x", status=status)) == (
            f"ERROR: {U} の上流が {status} を返した。時間を置いて再試行するか、ユーザーに URL をそのまま案内する。"
        )
    assert _explain(RefError("upstream_status", "x")) == (
        f"ERROR: {U} の上流が エラー を返した。時間を置いて再試行するか、ユーザーに URL をそのまま案内する。"
    )


def test_upstream_unreachable_with_and_without_reason():
    assert _explain(RefError("upstream_unreachable", "x", reason="unresolvable")) == (
        f"ERROR: {U} のホスト名を解決できない — DNS の無い環境では IP アドレスで指定する "
        "(例 `http://172.16.132.33/his-info/top.aspx`)。"
    )
    assert _explain(RefError("upstream_unreachable", "x")) == (
        f"ERROR: {U} の院内 Web に届かない (この配備からは到達できない、または一時的な障害)。"
        "ユーザーには URL をそのまま案内する (ブラウザでは開けることがある)。"
    )
    assert (
        _explain(RefError("upstream_unreachable", "x"), unreachable="UR {ref}")
        == f"UR {U}"
    )
    assert (
        _explain(
            RefError("upstream_unreachable", "x", reason="unresolvable"),
            unresolvable="UN {ref}",
        )
        == f"UN {U}"
    )


def test_unsupported_form_and_traversal_follow_the_argument():
    assert _explain(RefError("unsupported_form", "x")) == url_error(U)
    assert _explain(RefError("unsupported_form", "x"), arg="path") == path_error(P)
    trav = "は許可された範囲の外を指している (上の階層への参照 `..` や制御文字を含む)。"
    assert _explain(RefError("traversal", "x")) == f"ERROR: {U} {trav}{URL_ARG_FORM}"
    assert (
        _explain(RefError("traversal", "x"), arg="path")
        == f"ERROR: {P} {trav}{PATH_ARG_FORM}"
    )


def test_unknown_upstream_by_reason_with_extra_and_fallback():
    for reason, what in UNKNOWN_UPSTREAM_WHAT.items():
        err = RefError("unknown_upstream", "detail must not leak", reason=reason)
        assert _explain(err) == f"ERROR: {U} {what}。{URL_ARG_FORM}", reason
        assert "detail must not leak" not in _explain(err)
    # 知らない reason は None の 1 文面。extra は「。」の後、形の文の前
    err = RefError("unknown_upstream", "x", reason="brand_new_reason")
    assert _explain(err) == f"ERROR: {U} {UNKNOWN_UPSTREAM_WHAT[None]}。{URL_ARG_FORM}"
    assert _explain(err, unknown_upstream_extra="許可先は X。") == (
        f"ERROR: {U} {UNKNOWN_UPSTREAM_WHAT[None]}。許可先は X。{URL_ARG_FORM}"
    )
    # 表の差し替え (sandbox は全部を自分の言い方で持つ)
    table = {**UNKNOWN_UPSTREAM_WHAT, "server_self": "は別の言い方"}
    err = RefError("unknown_upstream", "x", reason="server_self")
    assert (
        _explain(err, unknown_upstream=table)
        == f"ERROR: {U} は別の言い方。{URL_ARG_FORM}"
    )


def test_workspace_unavailable_needs_a_hint_or_falls_back():
    err = RefError("workspace_unavailable", "SANDBOX_URL is not configured")
    assert _explain(err, arg="path") == f"UNEXPLAINED {P} (workspace_unavailable)"
    assert _explain(err, arg="path", workspace_unavailable="WU {ref}") == f"WU {P}"


def test_templates_are_filled_by_replace_not_format():
    """`URL_ARG_FORM` の `{id}` を str.format に噛ませない — hint に `{id}` が入っていても壊れない。"""
    assert (
        _explain(RefError("not_found", "x"), not_found="{ref} {id} {status}")
        == f"{U} {{id}} エラー"
    )


# --- 3 本の呼び手の hints を通した文面が原文 (移設前) と一致する -------------------------


def test_sandbox_wording_is_unchanged():
    from nucllei.builtin_tools.sandbox import _explain_ref_error as sandbox_explain

    cases = {
        RefError(
            "too_large", "x", size=3 * 1024 * 1024, limit=1024 * 1024, size_exact=True
        ): (
            f"ERROR: {U} は 3.0MB で、取り込みの上限 1.0MB を超えている。sandbox には取り込めない "
            "(上限は管理者の設定で、引数では上げられない)。リンク切れではない — ユーザーには URL を"
            "そのまま案内する (ブラウザでは開ける)。"
        ),
        RefError(
            "not_found", "x"
        ): f"ERROR: {U} は存在しない (404 / ファイル無し)。リンク切れとして案内する。",
        RefError("upstream_status", "x", status=302, reason="redirect_self"): (
            f"ERROR: {U} は redirect (302) を返したが、転送先が このサーバ自身 (または同居するサービス)"
            " なので辿らなかった。ユーザーには URL をそのまま案内する。"
        ),
        RefError("upstream_status", "x", status=302, reason="too_many_redirects"): (
            f"ERROR: {U} は redirect を 3 回を超えて繰り返した (転送のループか、ログインが要るページ)。"
            "ユーザーには URL をそのまま案内する。"
        ),
        RefError("upstream_status", "x", status=302): (
            f"ERROR: {U} は redirect (302) を返した。登録した配信元の redirect は辿らないので、"
            "ディレクトリではなくファイルやページを直接指す URL を渡すこと。"
        ),
        RefError("upstream_status", "x", status=503): (
            f"ERROR: {U} の上流が 503 を返した。時間を置いて再試行するか、ユーザーに URL をそのまま案内する。"
        ),
        RefError("upstream_unreachable", "x", reason="unresolvable"): (
            f"ERROR: {U} のホスト名を解決できない (この環境に DNS が無い場合は、ホスト名ではなく IP アドレスで指定する)。"
        ),
        RefError("upstream_unreachable", "x"): (
            f"ERROR: {U} の上流に届かない (この配備からは到達できない、または一時的な障害)。"
            "ユーザーには URL をそのまま案内する (ブラウザでは開けることがある)。"
        ),
        RefError("unknown_upstream", "x", reason="unknown_name"): (
            f"ERROR: {U} は許可先の外 (その配信元はこの配備に設定されていない)。{URL_ARG_FORM}"
        ),
        RefError("unknown_upstream", "x", reason="not_ext_form"): (
            f"ERROR: {U} は `/ext/<配信元>/<パス>` の形でない。{URL_ARG_FORM}"
        ),
        RefError("unknown_upstream", "x", reason="server_self"): (
            f"ERROR: {U} は このサーバ自身 (または同居するサービス) を指しているので取りに行かない。"
            f"{URL_ARG_FORM}"
        ),
        RefError("unknown_upstream", "x"): (
            f"ERROR: {U} は許可先の外 (この配備が取得を許可している上流の URL ではない)。{URL_ARG_FORM}"
        ),
        RefError("traversal", "x"): (
            f"ERROR: {U} は許可された範囲の外を指している (上の階層への参照 `..` や制御文字を含む)。{URL_ARG_FORM}"
        ),
        RefError(
            "unsupported_form", "x"
        ): f"ERROR: {U} は URL として解決できない。{URL_ARG_FORM} {REF_ARGS_RULE}",
        RefError("something_new", "x"): f"ERROR: {U} を取得できない (something_new)。",
    }
    for err, want in cases.items():
        assert sandbox_explain(U, err) == want, err


def test_feed_image_wording_is_unchanged():
    from nucllei.builtin_tools.feed_image import _explain_ref_error as feed_explain

    literal = "read_doc / fetch_url の戻り値に出た URL の literal をそのまま渡すこと。"
    cases_url = {
        RefError(
            "not_found", "x"
        ): f"ERROR: {U} は存在しない (404 / ファイル無し)。リンク切れとして案内する。",
        RefError("upstream_status", "x", status=302): (
            f"ERROR: {U} は redirect (302) を返した。redirect は辿らないので、ディレクトリではなく"
            f"ファイルやページを直接指す URL を渡すこと (例 '<ルート>/' ではなく '<ルート>/top.aspx')。{literal}"
        ),
        RefError("upstream_unreachable", "x"): (
            f"ERROR: {U} の院内 Web に届かない (この配備からは到達できない、または一時的な障害)。"
            "ユーザーには URL をそのまま案内する (ブラウザでは開けることがある)。"
        ),
        RefError("unknown_upstream", "x", reason="server_self"): (
            f"ERROR: {U} は このサーバ自身 (または同居するサービス) を指しているので取りに行かない。"
            f"{URL_ARG_FORM}"
        ),
        RefError("unknown_upstream", "x"): (
            f"ERROR: {U} は許可された配信元の外、または `/ext/<配信元>/<ファイル>` の形でない URL。{URL_ARG_FORM}"
        ),
        RefError(
            "something_new", "x"
        ): f"ERROR: {U} を読めない (存在しない / 許可された場所の外 / 大きすぎる)。",
    }
    for err, want in cases_url.items():
        assert feed_explain(U, err) == want, err
    cases_path = {
        RefError(
            "not_found", "x"
        ): f"ERROR: {P} は存在しない (ファイル無し)。run_shell の ls で存在を確認すること。",
        RefError("upstream_unreachable", "x"): (
            f"ERROR: {P} の院内 Web に届かない (この配備からは到達できない、または一時的な障害)。"
            "run_shell の ls で存在を確認すること。"
        ),
        RefError("workspace_unavailable", "x"): (
            f"ERROR: {P} は sandbox のファイルだが読めない (sandbox が起動していないか、ファイルが無い)。"
            "list_workspace で存在を確認すること。"
        ),
        RefError("traversal", "x"): (
            f"ERROR: {P} は許可された範囲の外を指している (上の階層への参照 `..` や制御文字を含む)。{PATH_ARG_FORM}"
        ),
        RefError("unsupported_form", "x"): path_error(P),
    }
    for err, want in cases_path.items():
        assert feed_explain(P, err, True) == want, err
    # too_large (url / path) — 原文の先頭と次の一手
    big = RefError(
        "too_large", "x", size=32_646_426, limit=25 * 1024 * 1024, size_exact=True
    )
    assert feed_explain(U, big).startswith(
        f"ERROR: {U} は 31.1 MB あり、取得の上限 25.0 MB を超えている。このままでは自分の目に入れられない。"
        "画像なら send_to_workspace(url=…) で sandbox に取り込み、"
    )
    assert feed_explain(U, big).endswith(
        "ユーザーには URL をそのまま案内する (ブラウザでは開ける)。"
    )
    assert feed_explain(P, big, True).endswith(
        "sandbox のファイルなので、見せるなら fetch_from_workspace(path=…) に渡し、返った URL か file_id で案内する。"
    )
    assert feed_explain(U, RefError("too_large", "x")) == (
        f"ERROR: {U} は 取得の上限 を超えている。このままでは自分の目に入れられない。"
        "画像なら send_to_workspace(url=…) で sandbox に取り込み、run_python で縮小してから /workspace/… の path を"
        " feed_image(path=…) に渡す。PDF なら pages を絞っても上限は変わらない (テキストが要るなら fetch_url を"
        " run_subagent の子が max_mb 付きで読む)。ユーザーには URL をそのまま案内する (ブラウザでは開ける)。"
    )


def test_fetch_url_wording_is_unchanged(monkeypatch):
    from nucllei.builtin_tools import fetch_url

    monkeypatch.setattr(fetch_url, "EXT_PUBLIC_BASES", {"his": "https://his.test/docs"})
    explain = fetch_url._explain_ref_error
    bases = "登録された原本ホストは `https://his.test/docs` (his)。"
    cases = {
        RefError(
            "not_found", "x"
        ): f"ERROR: {U} は存在しない (404 / ファイル無し)。リンク切れとして案内する。",
        RefError("upstream_status", "x", status=302): (
            f"ERROR: {U} は redirect (302) を返した。redirect は辿らないので、ディレクトリではなく"
            "ページを直接指すこと (例 '<ルート>/' ではなく '<ルート>/top.aspx')。"
        ),
        RefError("upstream_status", "x", status=302, reason="redirect_self"): (
            f"ERROR: {U} の redirect の転送先が このサーバ自身 (または同居するサービス) なので"
            "辿らなかった。ユーザーには URL をそのまま案内する。"
        ),
        RefError("unknown_upstream", "x", reason="server_self"): (
            f"ERROR: {U} は このサーバ自身 (または同居するサービス) を指しているので取りに行かない。"
            f"{bases}{URL_ARG_FORM}"
        ),
        RefError("unknown_upstream", "x", reason="unknown_name"): (
            f"ERROR: {U} の配信元は無い (この配備に構成されていない)。{bases}{URL_ARG_FORM}"
        ),
        RefError("unknown_upstream", "x"): (
            f"ERROR: {U} は許可された配信元の外、または `/ext/<配信元>/<ファイル>` の形でない URL。{bases}{URL_ARG_FORM}"
        ),
        RefError("unsupported_form", "x"): url_error(U),
        RefError("something_new", "x"): (
            f"ERROR: {U} を取得できない (上流不達・不在・redirect・サイズ超過のいずれか。"
            "redirect は辿らないので、ディレクトリではなくページを直接指すこと "
            "— 例 '<ルート>/' ではなく '<ルート>/top.aspx')。"
        ),
    }
    for err, want in cases.items():
        assert explain(U, err, 200.0) == want, err
    assert explain(U, None, 200.0) == cases[RefError("something_new", "x")]
    big = RefError(
        "too_large", "x", size=32_646_426, limit=25 * 1024 * 1024, size_exact=True
    )
    assert explain(U, big, 200.0) == (
        f"ERROR: {U} は 31.1 MB で、取得の上限 25.0 MB を超えている。主 context では読まず、run_subagent に URL と"
        f'指示を渡し、子が fetch_url(url="{U}", max_mb=32) で読む (子は offset で続きを取れる)。'
        "pandas 等で加工・集計したいだけなら send_to_workspace(url=…) で sandbox に取り込む "
        "(読むためのテキスト化には使わない)。中身が要らなければ URL をそのまま案内する (ブラウザでは開ける)。"
    )
