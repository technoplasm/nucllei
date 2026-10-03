"""resolver の `RefError` をモデル向けの ERROR 文にする(全 tool 共通、2026-10-03)。

`utils/image_refs` の resolver(`resolve_url` / `resolve_path`)は失敗理由を
`RefError`(code + reason + size / limit / status)で返す。それをモデルに見せる文に組むのは
tool の仕事だが、同じ code を 3 本の tool(feed_image / fetch_url / sandbox の
send_to_workspace(url=))が別々に言い分けていた — llens からの移設(nucllei #141)で
ここ 1 箇所にまとめる。

文面は「何が起きたか + 次にどうするか」。モデルがそのままユーザーに転記しても差し支えない
言い方にする(内部名・env 名・`detail` は出さない。`detail` は log に残す)。「読めなかった」を
全部リンク切れと言い換えさせないために、code / reason ごとに言い分ける(実走 2026-09-06:
32.6 MB の PDF が上限に当たったのを束ねた 1 文のせいでモデルが「リンク切れ」と誤報した)。

役割分担:

- **何が起きたか**(code / reason に対応する部分)はここが持つ。feed_image と fetch_url が
  同文で持っていた言い方を既定にする。
- **次にどうするか**は tool ごとに違う(fetch_url は run_subagent + max_mb、feed_image は
  縮小して path= で渡す、send_to_workspace は上限が valve …)ので、呼び手が `RefErrorHints`
  で渡す。既定(feed_image / fetch_url の共通文)と違う言い方を持つ tool は、その code の
  テンプレートごと差し替える — 3 本が同じ状況を別の言葉で言っていた箇所は、まとめるときに
  文面を変えない(モデルに見える文を黙って変えない)。
- `arg`(`"url"` / `"path"`)は **どの引数で来たか**(値の形では判定しない)。形の誤り
  (`unsupported_form`)と traversal で添える固定文(`URL_ARG_FORM` / `PATH_ARG_FORM`)を選ぶ。

テンプレートの差し込み語は `{ref}`(参照の literal)・`{status}`(HTTP status)・`{code}`
(RefError.code)の 3 つだけで、`str.replace` で埋める(`URL_ARG_FORM` の `{id}` を
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
    return f"ERROR: {value or '(空)'} は URL として解決できない。{URL_ARG_FORM} {REF_ARGS_RULE}"


def path_error(value: str) -> str:
    """path が sandbox のパスの形でない。"""
    return f"ERROR: {value or '(空)'} は sandbox のパスの形でない。{PATH_ARG_FORM} {REF_ARGS_RULE}"


def file_id_error(fid: str) -> str:
    """file が引けない(「無い」と「権限が無い」を言い分けない — 存在の有無を漏らさない)。"""
    return f"ERROR: file_id {fid} のレコードが無い、またはアクセス権が無い。{FILE_ID_ARG_FORM}"


def mb(n) -> str:
    """バイト数を MB(小数 1 桁)の表示に。ERROR 文はモデルがユーザーへ転記しがちなので単位を揃える。"""
    return f"{n / 1024 / 1024:.1f} MB"


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

# redirect(3xx、辿らなかった)の「何が起きたか」。次の一手は tool ごと(`RefErrorHints.redirect_next`)
_REDIRECT_WHAT = "ERROR: {ref} は redirect ({status}) を返した。"
# 2xx / 3xx / 404 以外の上流 status。3 本とも同文
_UPSTREAM_STATUS = (
    "ERROR: {ref} の上流が {status} を返した。"
    "時間を置いて再試行するか、ユーザーに URL をそのまま案内する。"
)
# traversal(`..` / 制御文字)。添える形の文は arg で選ぶ
_TRAVERSAL = "ERROR: {ref} は許可された範囲の外を指している (上の階層への参照 `..` や制御文字を含む)。"


@dataclass(frozen=True)
class RefErrorHints:
    """tool ごとの言い分け。各フィールドはテンプレート(`{ref}` `{status}` `{code}`)か、
    `too_large` だけは全文を組む callable `(ref, err) -> str`(次の一手が tool ごとに全く
    違い、size / limit / size_exact の読み方も tool の文脈に依るため)。

    既定値は feed_image と fetch_url が同文で持っていた言い方(url で来た参照向け)。
    """

    too_large: Callable[[str, RefError], str]
    # resolver が理由を付けずに失敗した・知らない code(通常起きない)
    unexplained: str
    not_found: str = (
        "ERROR: {ref} は存在しない (404 / ファイル無し)。リンク切れとして案内する。"
    )
    # 3xx(`_REDIRECT_WHAT` に続ける次の一手)
    redirect_next: str = (
        "redirect は辿らないので、ディレクトリではなくページを直接指すこと "
        "(例 '<ルート>/' ではなく '<ルート>/top.aspx')。"
    )
    # 一般の URL の redirect の転送先がこのサーバ自身 / 回数超過(reason 付き)
    redirect_self: str = (
        "ERROR: {ref} の redirect の転送先が このサーバ自身 (または同居するサービス) なので"
        "辿らなかった。ユーザーには URL をそのまま案内する。"
    )
    too_many_redirects: str = (
        "ERROR: {ref} は redirect が 4 回以上続いた (辿るのは 3 回まで)。"
        "転送先のページを直接指す URL を渡すこと。"
    )
    unresolvable: str = (
        "ERROR: {ref} のホスト名を解決できない — DNS の無い環境では IP アドレスで指定する "
        "(例 `http://172.16.132.33/his-info/top.aspx`)。"
    )
    unreachable: str = (
        "ERROR: {ref} の院内 Web に届かない (この配備からは到達できない、または一時的な障害)。"
        "ユーザーには URL をそのまま案内する (ブラウザでは開けることがある)。"
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
            return _fill(_REDIRECT_WHAT, ref, err.status) + hints.redirect_next
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
        return f"ERROR: {ref} {what}。{hints.unknown_upstream_extra}{URL_ARG_FORM}"
    if code == "traversal":
        return _fill(_TRAVERSAL, ref) + (
            PATH_ARG_FORM if arg == "path" else URL_ARG_FORM
        )
    if code == "workspace_unavailable" and hints.workspace_unavailable is not None:
        return _fill(hints.workspace_unavailable, ref)
    return _fill(hints.unexplained, ref, code=code)
