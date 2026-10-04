"""tool の失敗を 1 つの形にする(#145、2026-10-04 ken 指示「ツールのエラーも結果として
表示できるように。形式も揃える」)。

tool は失敗を **`ToolError` を raise して** 伝える(文字列や `{"ok": false, "error"}` を返さない)。
モデルに見せる文はここ 1 箇所(`format_tool_error`)で組む:

    ERROR: {fact}。次の手: {next}

- `fact` — 何が起きたか。常体の 1 文(複数文可)で、ユーザーにそのまま見せてよい言い方にする
  (内部名・env 名・例外の生文・内部 URL を出さない)。末尾の「。」は付けても付けなくてよい
  (組み立て側が 1 つに揃える)。
- `next` — モデルだけに向けた次の一手(README_API §5.1「事実の文 + 次の手」)。省略可。
- `kind` — 誰の問題か。チャット UI の見た目と wire の `error_kind` を決める。**赤はバグ・
  致命的なものとモデルの規約違反だけ**、ふつうの失敗は amber(2026-10-04 ken「あんまり赤は
  バグとか致命的なものでもないと出ないようにしたい / ちょっと値が見つからないとかは黄色でいい」
  「モデルの挙動や規約違反は赤としてみえるとこっちもわかりやすい」):
  - `call` — tool の中で見つけた呼び方の誤り(空の query・引数の値の形・不正な task_id /
    session_id・file_id の形)。呼び直せば直る。UI は amber「呼び直し」
  - `failure` — 実行時の失敗で、壊れているわけではないもの(タイムアウト・sandbox / 上流に
    届かない・上流の 5xx / 401 / 403・同時数や入れ子の上限・multi-worker・background の
    起動失敗・ユーザーやチャットの文脈が無い)。UI は amber「失敗」
  - `outcome` — 呼び出しは成立したが答えが「無い」(見つからない・404・範囲外・大きすぎる・
    一致なし)。UI は amber「該当なし」
  - `violation` — モデルが呼び出しの規約を破った(このターンの一覧に無い tool・arguments が
    JSON として読めない・必須引数の欠落・署名に無い引数)。tool を呼ぶ前に middleware が
    弾く。UI は赤「規約違反」
  - `fatal` — tool / サーバ自体が壊れている(設定の誤り・応答が読めない・登録の失敗・
    ライブラリの想定外の落ち方)。モデルやユーザーのせいではない。UI は赤「エラー」

middleware(同期実行)と exec runner(background)が `ToolError` を受けて文と kind を
結果に載せる。捕らえていない例外(型名 + 先頭 200 字だけを返す汎用経路
`tool_exception_text`)はバグなので `fatal`、タイムアウトは `failure`。

カスタム tool(配備層。DB に入った tool も plugin loader がそのまま import できる)は
`from nucllei.utils.tool_errors import ToolError, ToolCallError, ToolFailure, ToolOutcome, ToolFatal`
で raise する(`violation` は middleware 専用 — tool の中からは使わない)。**返した文字列は常に正常な結果** — `ERROR:` で始まっていても本文を見て
失敗扱いにはしない(テキストの嗅ぎ分けはどこにも置かない — ken 決定 2026-10-04)。
"""

from typing import Literal, Optional, get_args

ToolErrorKind = Literal["call", "failure", "outcome", "violation", "fatal"]
TOOL_ERROR_KINDS: tuple[str, ...] = get_args(ToolErrorKind)

# モデル向け文の頭(事実の文の前)と、次の手(モデルだけに向けた指示)の始まり。
# NEXT は ref_errors が再輸出する(旧来の import 先)
ERROR_PREFIX = "ERROR: "
NEXT = "次の手: "


def _strip_period(fact: str) -> str:
    fact = (fact or "").strip()
    while fact.endswith("。"):
        fact = fact[:-1].rstrip()
    return fact


def format_tool_error(fact: str, next: Optional[str] = None) -> str:
    """モデルに見せる ERROR 文を組む唯一の場所。"""
    text = f"{ERROR_PREFIX}{_strip_period(fact)}。"
    if next and next.strip():
        text += f"{NEXT}{next.strip()}"
    return text


class ToolError(Exception):
    """tool の失敗。raise すると middleware / exec runner が結果に変える(module docstring)。"""

    default_kind: ToolErrorKind = "failure"

    def __init__(
        self,
        fact: str,
        next: Optional[str] = None,
        *,
        kind: Optional[ToolErrorKind] = None,
    ):
        kind = kind or self.default_kind
        if kind not in TOOL_ERROR_KINDS:
            raise ValueError(f"unknown tool error kind: {kind!r}")
        self.fact = _strip_period(fact)
        self.next = next.strip() if next and next.strip() else None
        self.kind: ToolErrorKind = kind
        super().__init__(self.text)

    @property
    def text(self) -> str:
        return format_tool_error(self.fact, self.next)

    @classmethod
    def from_text(cls, text: str, *, kind: Optional[ToolErrorKind] = None):
        """組み立て済みの文(`ERROR: 事実。次の手: …`)から作る — テンプレートで文面を持つ
        `ref_errors` 用。`ERROR: ` の有無は問わず、最初の `次の手: ` で事実と次の手に分ける。"""
        body = (text or "").strip()
        if body.startswith(ERROR_PREFIX):
            body = body[len(ERROR_PREFIX) :]
        fact, sep, rest = body.partition(NEXT)
        return cls(fact, rest if sep else None, kind=kind)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(kind={self.kind!r}, {self.text!r})"


class ToolCallError(ToolError):
    """呼び方の誤り(呼び直せば直る)。"""

    default_kind: ToolErrorKind = "call"


class ToolFailure(ToolError):
    """実行時の失敗。"""

    default_kind: ToolErrorKind = "failure"


class ToolOutcome(ToolError):
    """呼び出しは成立したが答えが「無い」。"""

    default_kind: ToolErrorKind = "outcome"


class ToolViolation(ToolError):
    """モデルが呼び出しの規約を破った(tool を呼ぶ前に middleware が弾く)。"""

    default_kind: ToolErrorKind = "violation"


class ToolFatal(ToolError):
    """tool / サーバ自体が壊れている(設定・バグ。モデルやユーザーのせいではない)。"""

    default_kind: ToolErrorKind = "fatal"


def tool_exception_text(tool_name: str, e: BaseException) -> str:
    """tool が想定外の例外で落ちたときのモデル向け文 (kind は fatal — 捕らえていない例外は
    バグ)。例外は型名と先頭 200 字だけ — 全文は呼び手が log に出す (str(e) は内部 URL・パスを含みうる。2026-10-04 llens 監査 B5)。
    """
    msg = (str(e) or "").strip()
    what = e.__class__.__name__ + (f": {msg[:200]}" if msg else "")
    return format_tool_error(
        f"ツール {tool_name} の実行中にエラーが起きた ({what})",
        "引数を見直して 1 回だけ呼び直す。続くならユーザーに失敗を伝える。",
    )
