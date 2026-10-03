"""resolver の `RefError` をモデル向けの ERROR 文にする(全 tool 共通、2026-10-03)。

`utils/image_refs` の resolver(`resolve_url` / `resolve_path`)は失敗理由を
`RefError`(code + reason + size / limit / status)で返す。それをモデルに見せる文に組むのは
tool の仕事だが、同じ code を 3 本の tool(feed_image / fetch_url / sandbox の
send_to_workspace(url=))が別々に言い分けていた — llens からの移設(nucllei #141)で
ここ 1 箇所にまとめる。

文面は「**事実の文** + `次の手:` 以降(モデルだけに向けた指示)」の 2 部(2026-10-04、
README_API §5.1「事実の文 + 次の手」)。事実の文はユーザーにそのまま伝えてよい言い方にし
(内部名・env 名・`detail` は出さない。`detail` は log に残す)、配備は自分のモデルに「事実は
伝え、次の手は伝えない」と言える。常体(だ・である)で書く。ユーザーに渡すリンクは
`[ラベル](URL)` の markdown で書かせる(裸の相対パスを本文に出させない — `SHOW_AS_LINK`)。
同じ toolkit に無い tool を次の手に挙げるときは「〜が使えるなら」と条件付きで書く。「読めなかった」を
全部リンク切れと言い換えさせないために、code / reason ごとに言い分ける(実走 2026-09-06:
32.6 MB の PDF が上限に当たったのを束ねた 1 文のせいでモデルが「リンク切れ」と誤報した)。

役割分担:

- **何が起きたか**(code / reason に対応する部分)と既定の次の手はここが持つ。3 本の
  tool が同じ状況を言う文は同じにする(2026-10-04 の監査で、sandbox だけが持っていた
  言い換え — 「登録した配信元」「許可先の外」「3 回を超えて」 — は既定に揃えて外した)。
- **次にどうするか**が tool ごとに違う code(too_large: fetch_url は run_subagent + max_mb、
  feed_image は縮小して path= で渡す、send_to_workspace は上限が valve …、path で来た参照の
  not_found / unreachable)だけ、呼び手が `RefErrorHints` でその code のテンプレートごと
  差し替える。
- `arg`(`"url"` / `"path"`)は **どの引数で来たか**(値の形では判定しない)。形の誤り
  (`unsupported_form`)と traversal で添える固定文(`URL_ARG_FORM` / `PATH_ARG_FORM`)を選ぶ。

テンプレートの差し込み語は `{ref}`(参照の literal)・`{status}`(HTTP status)・`{code}`
(RefError.code。内部名なので builtin の文面では使っていない)の 3 つだけで、`str.replace` で埋める(`URL_ARG_FORM` の `{id}` を
`str.format` に噛ませない)。
"""

import logging
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Callable, Literal, Mapping

from nucllei.utils.image_refs import (
    FILE_ID_ARG_FORM,
    PATH_ARG_FORM,
    REF_ARGS_RULE,
    URL_ARG_FORM,
    RefError,
)

log = logging.getLogger(__name__)

RefArg = Literal["url", "path"]


# --- 形の誤り・file の固定文(image_refs の module docstring「エラーに添える固定文」) ------


def url_error(value: str) -> str:
    """url が URL として解決できない(値を調べて文面を出し分けない)。"""
    return f"ERROR: {value or '(空)'} は URL として解決できない。{NEXT}{URL_ARG_FORM} {REF_ARGS_RULE}"


def path_error(value: str) -> str:
    """path が sandbox のパスの形でない。"""
    return f"ERROR: {value or '(空)'} は sandbox のパスの形でない。{NEXT}{PATH_ARG_FORM} {REF_ARGS_RULE}"


def file_id_error(fid: str) -> str:
    """file が引けない(「無い」と「権限が無い」を言い分けない — 存在の有無を漏らさない)。"""
    return f"ERROR: file_id {fid} のレコードが無い、またはアクセス権が無い。{NEXT}{FILE_ID_ARG_FORM}"


def mb(n) -> str:
    """バイト数を MB(小数 1 桁)の表示に。ERROR 文はモデルがユーザーへ転記しがちなので単位を揃える。"""
    return f"{n / 1024 / 1024:.1f} MB"


# --- 「事実の文 + 次の手」の共通部品 (module docstring) ---------------------------------

# 次の手 (モデルだけに向けた指示) の始まり。この語より前が事実の文
NEXT = "次の手: "
# URL をユーザーに渡すときの言い方 (全 tool 共通の 1 文)。`{ref}` は渡す URL — 裸の相対パス
# (`/ext/…`) を本文に出させず、必ず markdown リンクにさせる
SHOW_AS_LINK = (
    "ユーザーに示すなら [ラベル]({ref}) のリンクにする (ブラウザでは開ける)。"
)
# 404 だけはリンクを示しても開けない
TELL_BROKEN_LINK = "ユーザーにはリンク切れと伝える。"


def show_as_link(url: str) -> str:
    """`SHOW_AS_LINK` に URL を埋めた 1 文 (テンプレートでなく全文を組む呼び手用)。"""
    return SHOW_AS_LINK.replace("{ref}", url)


# `unknown_upstream` の内訳(`RefError.reason`)→ 「何が起きたか」(feed_image / fetch_url の
# 共通文。`None` は reason が無い・知らない値のときの 1 文面)。文は `{ref} ` の後ろに続け、
# 末尾の「。」は組み立て側が付ける。detail は使わない — 文面は固定でない。
UNKNOWN_UPSTREAM_WHAT: Mapping[str | None, str] = MappingProxyType(
    {
        # 一般の URL で取りに行かない唯一の相手 (2026-10-03。行き先で絞らない — 外部サイトは
        # 「届かない」なら upstream_unreachable で、ここには来ない)。`not_ext_form` /
        # `unknown_name` (`/ext/{name}/` の name の概念) は同日に /ext の設定ごと無くした —
        # `/ext/…` の失敗は not_found だけ
        "server_self": (
            "は このサーバ自身 (または同居するサービス) を指しているので取りに行かない"
        ),
        None: "は取得を許可されていない URL",
    }
)

# reason 無しの 3xx(redirect は 3 回まで辿る — ここに来るのは転送先が URL として組めない
# とき)の「何が起きたか」の前半。続きは `RefErrorHints.redirect_next`
_REDIRECT_WHAT = "ERROR: {ref} は redirect ({status}) を返した。"
# 2xx / 3xx / 404 以外の上流 status。3 本とも同文
_UPSTREAM_STATUS = (
    "ERROR: {ref} の上流が {status} を返した。"
    + NEXT
    + "5xx なら時間を置いて 1 回だけ再試行する (401 / 403 などは再試行しない)。"
    + SHOW_AS_LINK
)
# traversal(`..` / 制御文字)。添える形の文は arg で選ぶ
_TRAVERSAL = "ERROR: {ref} は許可された範囲の外を指している (上の階層への参照 `..` や制御文字を含む)。"


@dataclass(frozen=True)
class RefErrorHints:
    """tool ごとの言い分け。各フィールドはテンプレート(`{ref}` `{status}` `{code}`)か、
    `too_large` だけは全文を組む callable `(ref, err) -> str`(次の一手が tool ごとに全く
    違い、size / limit / size_exact の読み方も tool の文脈に依るため)。

    既定値は全 tool 共通の言い方(url で来た参照向け)。事実の文 + `次の手:`(module docstring)。
    """

    too_large: Callable[[str, RefError], str]
    # resolver が理由を付けずに失敗した・知らない code(通常起きない)
    unexplained: str
    not_found: str = (
        "ERROR: {ref} は存在しない (404 / ファイル無し)。" + NEXT + TELL_BROKEN_LINK
    )
    # reason 無しの 3xx(`_REDIRECT_WHAT` に続ける。Location が無い・URL として組めない・
    # 同一サーバの /ext 以外を指す — どれも「辿れなかった」)
    redirect_next: str = (
        "転送先を辿れなかった (転送先が示されていないか、取りに行ける URL ではない)。"
        + NEXT
        + "転送先のページを直接指す URL が分かるならそれを渡す。分からなければ "
        + SHOW_AS_LINK
    )
    # 一般の URL の redirect の転送先がこのサーバ自身 / 回数超過(reason 付き)
    redirect_self: str = (
        "ERROR: {ref} の redirect ({status}) の転送先が このサーバ自身 (または同居するサービス)"
        " なので辿らなかった。" + NEXT + SHOW_AS_LINK
    )
    too_many_redirects: str = (
        "ERROR: {ref} は redirect が 4 回以上続いた (辿るのは 3 回まで。転送のループか、"
        "ログインが要るページのことが多い)。"
        + NEXT
        + "転送先のページを直接指す URL を渡す。"
        + SHOW_AS_LINK
    )
    unresolvable: str = (
        "ERROR: {ref} のホスト名を解決できない。"
        + NEXT
        + "IP アドレスの URL が分かっているならそれで呼び直す (ホスト名を推測で IP に書き換えない)。"
        + "分からなければ "
        + SHOW_AS_LINK
    )
    unreachable: str = (
        "ERROR: {ref} のホストに届かない (このサーバからは到達できないか、一時的な障害)。"
        + NEXT
        + SHOW_AS_LINK.replace("開ける", "開けることがある")
    )
    # reason → 何が起きたか(`UNKNOWN_UPSTREAM_WHAT` と同じ形)。`extra` は tool が添える
    # 許可先の一覧など(「。」の後、`URL_ARG_FORM` の前)
    # Python 3.11 (image) の dataclass は mappingproxy を既定値に置けない(unhashable 判定)
    # ので default_factory で同じ不変 Mapping を返す
    unknown_upstream: Mapping[str | None, str] = field(
        default_factory=lambda: UNKNOWN_UPSTREAM_WHAT
    )
    unknown_upstream_extra: str = ""
    # sandbox のパスを扱う tool だけ持つ。None なら `unexplained` に落ちる
    workspace_unavailable: str | None = None


def _fill(template: str, ref: str, status=None, code: str = "") -> str:
    return (
        template.replace("{ref}", ref)
        .replace("{status}", str(status) if status is not None else "エラー")
        .replace("{code}", code)
    )


def explain_ref_error(
    ref: str, err: RefError | None, *, arg: RefArg, hints: RefErrorHints
) -> str:
    """`RefError` をモデル向けの ERROR 文に組む(module docstring)。

    `err` が None(resolver が理由を付けずに失敗した — 通常起きない)は `hints.unexplained`。
    code の集合は `image_refs.RefError` の docstring。知らない code も `unexplained`
    (detail は出さない)。
    """
    if err is None:
        return _fill(hints.unexplained, ref)
    code = err.code or ""
    log.info(f"ref_errors: resolver refused {ref} (arg={arg}): {code} {err.detail}")

    if code == "too_large":
        return hints.too_large(ref, err)
    if code == "not_found":
        return _fill(hints.not_found, ref)
    if code == "upstream_status":
        # 一般の URL だけ redirect を 3 回まで追う。その失敗の内訳は reason
        if err.reason == "redirect_self":
            return _fill(hints.redirect_self, ref, err.status)
        if err.reason == "too_many_redirects":
            return _fill(hints.too_many_redirects, ref, err.status)
        if err.status is not None and 300 <= int(err.status) < 400:
            return _fill(_REDIRECT_WHAT + hints.redirect_next, ref, err.status)
        return _fill(_UPSTREAM_STATUS, ref, err.status)
    if code == "upstream_unreachable":
        if err.reason == "unresolvable":
            return _fill(hints.unresolvable, ref)
        return _fill(hints.unreachable, ref)
    if code == "unsupported_form":
        return path_error(ref) if arg == "path" else url_error(ref)
    if code == "unknown_upstream":
        table = hints.unknown_upstream
        what = table[err.reason] if err.reason in table else table[None]
        return (
            f"ERROR: {ref} {what}。{NEXT}{hints.unknown_upstream_extra}{URL_ARG_FORM}"
        )
    if code == "traversal":
        return (
            _fill(_TRAVERSAL, ref)
            + NEXT
            + (PATH_ARG_FORM if arg == "path" else URL_ARG_FORM)
        )
    if code == "workspace_unavailable" and hints.workspace_unavailable is not None:
        return _fill(hints.workspace_unavailable, ref)
    return _fill(hints.unexplained, ref, code=code)
