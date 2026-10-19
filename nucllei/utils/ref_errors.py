"""resolver の `RefError` をモデル向けの ERROR(`ToolError`)にする(全 tool 共通、2026-10-03)。

2026-10-04 (#145): 文字列を返すのをやめ、`utils/tool_errors.ToolError` を返す(呼び手が raise
する)。文面はそのまま — 組み立ては `ToolError` の 1 箇所、kind は code で決める (`REF_ERROR_KINDS`)。

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
次の手で別の tool を名指しするのは、そのターンでモデルに出ている tool だけ(`tool_errors.tool_in_turn`。
2026-10-19 llens 監査 (13) で「〜が使えるなら」の条件付きの書き方をやめた)。「読めなかった」を
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
  (`unsupported_form`)で添える固定文(`URL_ARG_FORM` / `PATH_ARG_FORM`)と traversal の
  呼び直し方を選ぶ。行き先の規則 `REF_ARGS_RULE` は値が別の引数の種類に見える(取り違え)
  ときだけ添える(`image_refs.ref_args_rule_for`、2026-10-04)。

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
    URL_ARG_FORM,
    RefError,
    ref_args_rule_for,
)
from nucllei.utils.tool_errors import (
    NEXT,
    ToolCallError,
    ToolError,
    ToolErrorKind,
    ToolOutcome,
    tool_in_turn,
)

log = logging.getLogger(__name__)

RefArg = Literal["url", "path"]


# --- 形の誤り・file の固定文(image_refs の module docstring「エラーに添える固定文」) ------


def _with_rule(form: str, arg: str, value: str) -> str:
    """受ける形の文 + 取り違えらしいときだけ `REF_ARGS_RULE`(`image_refs.ref_args_rule_for`)。"""
    rule = ref_args_rule_for(arg, value)
    return f"{form} {rule}" if rule else form


def url_error(value: str) -> ToolCallError:
    """url が URL として解決できない。受ける形を固定文で添え、値が URL に見えない(パス・id を
    取り違えたらしい)ときだけ行き先の規則も添える。呼び手が raise する。"""
    return ToolCallError(
        f"{value or '(空)'} は URL として解決できない",
        _with_rule(URL_ARG_FORM, "url", value),
    )


def path_error(value: str, arg_form: str = PATH_ARG_FORM) -> ToolCallError:
    """path が sandbox のパスの形でない。受ける形の文 `arg_form`(workspace だけを扱う tool は
    `WORKSPACE_PATH_ARG_FORM`)を添え、値が URL / id に見える(取り違えたらしい)ときだけ
    行き先の規則も添える — 相対パス等、パスとして崩れているだけなら添えない。呼び手が raise する。"""
    return ToolCallError(
        f"{(value or '').strip() or '(空)'} は sandbox のパスの形でない",
        _with_rule(arg_form, "path", value),
    )


# file が引けないとき(レコードが無い / アクセス権が無い)の事実の文と次の手。2 つは言い分けない
# (存在の有無を漏らさない)が、区別できないことは事実の文で言い、ユーザーに「無い」と
# 言い切らせない(llens staging 2026-10-04)。次の手は file_id の確かめ方(chat_files は別の
# toolkit — このターンに出ているものだけ名指しする。`_no_file_next`)
NO_FILE = "のレコードが無い、またはアクセス権が無い (どちらなのかは区別できない)"
_NO_FILE_TELL = (
    "ユーザーには「このファイルは開けなかった」と伝え、存在しないとは言い切らない。"
)


def _no_file_next() -> str:
    """file が引けないときの次の手。file_id の確かめ方は、このターンに出ている tool で言う。"""
    finders = [n for n in ("list_chat_files", "grep_chat_files") if tool_in_turn(n)]
    if finders:
        check = f"{' か '.join(finders)} でこのチャットの file_id を確かめて渡し直す。"
    else:
        check = "会話に出ている file_id を確かめて渡し直す。"
    return check + _NO_FILE_TELL


def file_id_error(fid: str, *, given: str | None = None) -> ToolCallError:
    """file が引けない(「無い」と「権限が無い」を言い分けない — 存在の有無を漏らさない)。
    `given` はモデルが渡した言い方(url= の配信口 URL で来たときはその URL。既定は
    `file_id <id>`)。file_id 引数の値が id の形でない(URL・パス・ファイル名を取り違えた
    らしい)ときだけ、受ける形と行き先の規則を前に添える。呼び手が raise する。"""
    form = ""
    if given is None:
        rule = ref_args_rule_for("file_id", fid)
        form = f"{FILE_ID_ARG_FORM} {rule} " if rule else ""
    return ToolCallError(
        f"{given or f'file_id {fid}'} {NO_FILE}", form + _no_file_next()
    )


def ext_not_found(ref: str) -> ToolOutcome:
    """`/ext/…` の path が nucllei 側の /ext に無い (feed_image(path=) と fetch_from_workspace の
    共通文。2026-10-19 llens 決定で 2 つを揃えた)。どちらも nucllei の /ext を読む — 本当に無いか、
    sandbox にだけ mount されているかは分けられないので「存在しない」と言い切らない (mount の語は
    出さない)。`/ext/…` は受け取った literal を書き換えた (組み立てた) ことが多いので、次の手は
    まずそれを確かめさせ、親ディレクトリの一覧はこのターンにある tool だけで言う。呼び手が raise する。"""
    names = [n for n in ("list_workspace", "run_shell") if tool_in_turn(n)]
    nxt = "受け取った literal のまま渡したか確かめ" + (
        f"、{' か '.join(names)} で親ディレクトリを一覧する。" if names else "る。"
    )
    return ToolOutcome(
        f"{ref} はこのサーバの /ext に無い (存在しないか、sandbox の中にだけある)", nxt
    )


def mb(n) -> str:
    """バイト数を MB(小数 1 桁)の表示に。ERROR 文はモデルがユーザーへ転記しがちなので単位を揃える。"""
    return f"{n / 1024 / 1024:.1f} MB"


# --- 「事実の文 + 次の手」の共通部品 (module docstring) ---------------------------------

# 次の手 (モデルだけに向けた指示) の始まり (`NEXT`) は utils/tool_errors が持つ — この語より前が
# 事実の文。旧来の import 先としてここからも再輸出する
# URL をユーザーに渡すときの言い方 (全 tool 共通の 1 文)。`{ref}` は渡す URL — 裸の相対パス
# (`/ext/…`) を本文に出させず、必ず markdown リンクにさせる
SHOW_AS_LINK = (
    "ユーザーに示すなら [ラベル]({ref}) のリンクにする (ブラウザでは開ける)。"
)
# 「分からなければ」に続ける形 (SHOW_AS_LINK をそのまま繋ぐと「分からなければ ユーザーに示すなら…」で
# 1 文にならない — llens staging 2026-10-04)
SHOW_AS_LINK_OTHERWISE = (
    "分からなければ、ユーザーには [ラベル]({ref}) のリンクで示す (ブラウザでは開ける)。"
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

# server_self の次の手の先頭 (行動)。続けて URL_ARG_FORM (受け付ける形の説明)
_SERVER_SELF_NEXT = "同じサーバのファイルは `/ext/…` の相対 URL か file_id で渡し直す。"

# reason 無しの 3xx(redirect は 3 回まで辿る — ここに来るのは転送先が URL として組めない
# とき)の「何が起きたか」の前半。続きは `RefErrorHints.redirect_next`
_REDIRECT_WHAT = "ERROR: {ref} は redirect ({status}) を返した。"


def _upstream_status_text(ref: str, status) -> str:
    """2xx / 3xx 以外の HTTP status(3 本とも同文)。次の手は status で言い分ける
    (2026-10-19 llens 監査 (6) — 旧「5xx なら時間を置いて 1 回だけ再試行」はモデルが時間を
    置けず、4xx まで呼び直させていた)。4xx は呼び直しても同じ、5xx だけ 1 回呼び直す。"""
    try:
        code = int(status)
    except (TypeError, ValueError):
        code = None
    link = SHOW_AS_LINK
    if code is None:
        what = "HTTP のエラーを返した"
        act = "呼び直しても同じことが多い。"
    elif code in UPSTREAM_STATUS_OUTCOME:
        what = f"HTTP {code} を返した (ページが無い)"
        act = "呼び直しても同じ。"
        link = TELL_BROKEN_LINK
    elif code in (401, 403):
        what = f"HTTP {code} を返した (ログインか権限が要るページ)"
        act = "呼び直しても同じ。"
        link = SHOW_AS_LINK.replace("開ける", "開けることがある")
    elif code == 429:
        what = f"HTTP {code} を返した (アクセスが多すぎるとして断られた)"
        act = "続けて呼び直さない。"
    elif 400 <= code < 500:
        what = f"HTTP {code} を返した (要求を受け付けなかった)"
        act = "同じ URL で呼び直しても同じ。"
    elif 500 <= code < 600:
        what = f"HTTP {code} を返した (相手のサーバー側の障害)"
        act = "1 回だけ呼び直し、同じならやめる。"
    else:
        what = f"HTTP {code} を返した"
        act = "呼び直しても同じことが多い。"
    return f"ERROR: {ref} は {what}。{NEXT}{act}{link.replace('{ref}', ref)}"


# traversal(`..` / 制御文字)。添える形の文は arg で選ぶ
_TRAVERSAL = "ERROR: {ref} は許可された範囲の外を指している (上の階層への参照 `..` や制御文字を含む)。"
# traversal の次の手は呼び直し方 (受ける形の説明文だけでは一手にならない — llens staging
# 2026-10-04)。値は正しい種類なので取り違えの規則は添えない
_TRAVERSAL_NEXT_PATH = (
    "`..` や制御文字を含まない絶対パス (`/workspace/…` `/ext/…`) で呼び直す。"
)
_TRAVERSAL_NEXT_URL = "`..` や制御文字を含まない URL で呼び直す。"


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
        + "転送先のページを直接指す URL が分かるならそれを渡す。"
        + SHOW_AS_LINK_OTHERWISE
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
        + SHOW_AS_LINK_OTHERWISE
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
) -> ToolError:
    """`RefError` をモデル向けの `ToolError` に組む(module docstring)。呼び手が raise する。

    `err` が None(resolver が理由を付けずに失敗した — 通常起きない)は `hints.unexplained`。
    code の集合は `image_refs.RefError` の docstring。知らない code も `unexplained`
    (detail は出さない)。文面はテンプレート(`ERROR: 事実。次の手: …`)のまま
    `ToolError.from_text` で事実と次の手に分け、kind は code で決める(`REF_ERROR_KINDS`)。
    """
    if err is None:
        # 理由の無い失敗は resolver 側の取りこぼし(通常起きない)= バグ → fatal
        return _tool_error(_fill(hints.unexplained, ref), "fatal")
    code = err.code or ""
    log.info(f"ref_errors: resolver refused {ref} (arg={arg}): {code} {err.detail}")
    return _tool_error(_explain_text(ref, err, code, arg, hints), _ref_error_kind(err))


# RefError.code → ToolError の kind(utils/tool_errors)。答えが「無い」(404・大きすぎる)は
# outcome、引数の形・取りに行かない相手を指した・範囲外は call、届かない・上流の失敗は failure。
# 載っていない code(unexplained に落ちるもの)は fatal — 言い分けられない code はこちらの
# 取りこぼし(バグ)
REF_ERROR_KINDS: Mapping[str, ToolErrorKind] = MappingProxyType(
    {
        "too_large": "outcome",
        "not_found": "outcome",
        "upstream_status": "failure",
        "upstream_unreachable": "failure",
        "unsupported_form": "call",
        "unknown_upstream": "call",
        "traversal": "call",
        "workspace_unavailable": "failure",
    }
)


# upstream_status のうち「無い」と同じ扱いにする HTTP status(llens 判定 2026-10-04)。
# 401 / 403・他の 4xx・5xx・redirect の失敗は failure のまま
UPSTREAM_STATUS_OUTCOME = frozenset({404, 410})


def _ref_error_kind(err: RefError) -> ToolErrorKind:
    if (err.code or "") == "upstream_status" and not err.reason:
        try:
            if int(err.status) in UPSTREAM_STATUS_OUTCOME:
                return "outcome"
        except (TypeError, ValueError):
            pass
    return REF_ERROR_KINDS.get(err.code or "", "fatal")


def _tool_error(text: str | ToolError, kind: ToolErrorKind) -> ToolError:
    if isinstance(text, ToolError):
        return text
    return ToolError.from_text(text, kind=kind)


def _explain_text(
    ref: str, err: RefError, code: str, arg: RefArg, hints: RefErrorHints
) -> str | ToolError:
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
        return _upstream_status_text(ref, err.status)
    if code == "upstream_unreachable":
        if err.reason == "unresolvable":
            return _fill(hints.unresolvable, ref)
        return _fill(hints.unreachable, ref)
    if code == "unsupported_form":
        return path_error(ref) if arg == "path" else url_error(ref)
    if code == "unknown_upstream":
        table = hints.unknown_upstream
        what = table[err.reason] if err.reason in table else table[None]
        # server_self は説明 (URL_ARG_FORM) の前に行動を置く (llens 判定 2026-10-04)
        act = _SERVER_SELF_NEXT if err.reason == "server_self" else ""
        return f"ERROR: {ref} {what}。{NEXT}{act}{hints.unknown_upstream_extra}{URL_ARG_FORM}"
    if code == "traversal":
        return (
            _fill(_TRAVERSAL, ref)
            + NEXT
            + (_TRAVERSAL_NEXT_PATH if arg == "path" else _TRAVERSAL_NEXT_URL)
        )
    if code == "workspace_unavailable" and hints.workspace_unavailable is not None:
        return _fill(hints.workspace_unavailable, ref)
    return _fill(hints.unexplained, ref, code=code)
