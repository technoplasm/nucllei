"""run_shell の `rm` の歯止め。

sandbox へ送る前に、コマンドの中の素直な `rm` を見つけ、その対象が `/workspace` か `/ext` の下なら
実行せずに断る (消すのは remove_file — ゴミ箱へ移す)。

**これは安全の境界ではない** — よくある直接の `rm` を止める歯止めだけ。`find -delete`・`xargs rm`・
`python -c "os.remove(…)"`・`mv … /dev/null`・変数に入れたパス・eval・別の場所に書いたスクリプトの中の
`rm`・二重引用符の中の `$(…)` などは追わない。読み違えて rm でないコマンドを止めるほうが、見逃すより悪いので、
読み方は保守的:

- コマンドの語として現れた `rm` (と `unlink`) だけを見る — 行頭、`;` `&&` `||` `|` `&` 改行 `(` `$(`
  バッククォートの後、`if` `then` `do` などの予約語の後。`sudo` `env` `command` `exec` `nohup` `time` `nice`
  `timeout` と `VAR=値` の前置きは (値を取るオプションも) 飛ばす。`echo rm …` や `grep rm file` の `rm` は
  引数なので見ない
- 対象は `rm` の後の、オプションでもリダイレクトでもない語。相対パスは run_shell の作業ディレクトリ
  (`/workspace`) から解き、`cd` / `pushd` <リテラルのパス> があればそれに従う (サブシェルの `( … )` の中の
  cd は外へ持ち出さない。分からない cd・`cd -`・`popd` の後の相対パスは見ない)。`~` `$HOME` は `/workspace`
  (jail の HOME)、`$PWD` はその時の作業ディレクトリ。glob は最初のメタ文字の段の手前で見る
- 判定の前にパスを正規化する (`..` を畳む・先頭の `//` を 1 つに) — 除外の置き場から `..` で抜けたものは
  除外にならない。symlink は nucllei の側からは解けない (sandbox の fs を見ない)
- 行継続 (`\\` + 改行) はつなぎ、引用符の外で語頭の `#` から行末まではコメントとして捨てる
- 読めないもの (閉じていない引用符・ヒアドキュメント `<<`) は止めない

除外 (`allowed`): 配備が決める置き場 (sandbox の valve `rm_allowed_paths`)。既定は無し。各要素は絶対パスで、
その下 (その置き場そのものは含まない) の rm を通す。パスの各段で glob (`*` `?` `[…]`) を使える
(`/workspace/[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-*` = 作業フォルダの中)。glob を含む対象 (`rm …/*`) は、
glob の手前の段がその置き場の下にあるときだけ通す (置き場そのものを glob で空にするのは止める)。
"""

from __future__ import annotations

import fnmatch
import posixpath
import re
import shlex
from typing import Iterable, Optional

WORKDIR = "/workspace"
GUARDED_ROOTS = ("/workspace", "/ext")

# コマンドの区切り
_BOUNDARIES = {
    ";",
    "&&",
    "||",
    "|",
    "&",
    "(",
    ")",
    "|&",
    ";;",
    ";&",
    ";;&",
    "{",
    "}",
    "!",
}
_REDIRECTS = {">", ">>", "<", "<<<", ">&", "<&", "&>", "&>>", ">|", "<>"}
_OPERATORS = sorted(_BOUNDARIES | _REDIRECTS | {"<(", ">("}, key=len, reverse=True)
_PUNCT = set("();<>|&")
# コマンドの語の前に付いて、その後ろが本当のコマンドになる語 → 値を取るオプション
_PREFIXES = {
    "sudo": {"-u", "-g", "-C", "-D", "-h", "-p", "-U", "-r", "-t"},
    "env": {"-u", "-C", "-S"},
    "command": set(),
    "exec": {"-a"},
    "nohup": set(),
    "time": {"-f", "-o"},
    "nice": {"-n"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "builtin": set(),
}
_RESERVED = {"if", "then", "else", "elif", "do", "while", "until"}
_RM_WORDS = {"rm", "unlink"}
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_GLOB = re.compile(r"[*?\[]")


def parse_allowed(value: Optional[str]) -> list[str]:
    """valve の値 (改行・カンマ・空白区切り) を正規化した絶対パスの列にする。相対・空は捨てる。"""
    out = []
    for item in re.split(r"[\s,]+", value or ""):
        item = item.strip()
        if not item.startswith("/"):
            continue
        out.append(_norm(item))
    return out


def _norm(path: str) -> str:
    return re.sub(r"^/+", "/", posixpath.normpath(path))


def _prepare(command: str) -> Optional[str]:
    """コメントを捨て、引用符の外の改行を `;`、バッククォートを `(` `)` にした文字列。読めなければ None。"""
    text = re.sub(r"\\\r?\n", "", command.replace("\r\n", "\n"))
    if "<<" in text.replace("<<<", ""):
        return None  # ヒアドキュメントの本文をコマンドとして読まない
    out: list[str] = []
    quote = None
    backticks = 0
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if quote == "'":
            out.append(c)
            if c == "'":
                quote = None
        elif c == "\\" and i + 1 < n:
            out.append(text[i : i + 2])
            i += 2
            continue
        elif quote == '"':
            out.append(c)
            if c == '"':
                quote = None
        elif c in ("'", '"'):
            quote = c
            out.append(c)
        elif c == "#" and (not out or out[-1][-1:].isspace() or out[-1][-1:] in _PUNCT):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif c == "\n":
            out.append(" ; ")
        elif c == "`":
            backticks += 1
            out.append(" ( " if backticks % 2 else " ) ")
        else:
            out.append(c)
        i += 1
    if quote is not None:
        return None
    return "".join(out)


def _split_operators(token: str) -> list[str]:
    """記号だけの語 (`);` `)&&` など、shlex が続けてまとめたもの) を演算子ごとに割る。"""
    out = []
    while token:
        for op in _OPERATORS:
            if token.startswith(op):
                out.append(op)
                token = token[len(op) :]
                break
        else:
            out.append(token[0])
            token = token[1:]
    return out


def _tokens(command: str) -> Optional[list[str]]:
    text = _prepare(command)
    if text is None:
        return None
    lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        raw = list(lexer)
    except ValueError:
        return None
    tokens = []
    for tok in raw:
        if tok and set(tok) <= _PUNCT:
            tokens.extend(_split_operators(tok))
        else:
            tokens.append(tok)
    return tokens


def _resolve(word: str, cwd: Optional[str]) -> Optional[tuple[str, str]]:
    """rm の対象の語を (止めるかを見るパス, 除外を見るパス) にする — どちらも正規化した絶対パス。glob は
    最初のメタ文字の段の手前を前に、glob のままのパスを後ろに。解けない (変数・cwd 不明の相対) なら None。"""
    if word.startswith("~"):
        if word != "~" and not word.startswith("~/"):
            return None  # ~user は解かない
        word = WORKDIR + word[1:]
    for var, value in (
        ("${HOME}", WORKDIR),
        ("$HOME", WORKDIR),
        ("${PWD}", cwd),
        ("$PWD", cwd),
    ):
        if word.startswith(var):
            if value is None:
                return None
            word = value + word[len(var) :]
            break
    if "$" in word:
        return None
    if not word.startswith("/"):
        if cwd is None:
            return None
        word = posixpath.join(cwd, word)
    full = _norm(word)
    glob = _GLOB.search(full)
    if not glob:
        return full, full
    head = full[: glob.start()]
    head = head[: head.rfind("/")] or "/"
    return head, full


def _guarded(path: str) -> bool:
    """`/workspace` `/ext` の下か、その上の段 (`/` — `rm -rf /*` は両方を含む) か。"""
    prefix = path.rstrip("/") + "/"
    return any(
        path == root or path.startswith(root + "/") or root.startswith(prefix)
        for root in GUARDED_ROOTS
    )


def _below(path: str, allowed: Iterable[str]) -> bool:
    """`path` (glob を含まない) が除外の置き場の下か (その置き場そのものは含まない)。"""
    for base in allowed:
        if _GLOB.search(base):
            depth = base.count("/")
            parts = path.split("/")
            if len(parts) - 1 > depth and fnmatch.fnmatchcase(
                "/".join(parts[: depth + 1]), base
            ):
                return True
        elif path.startswith(base.rstrip("/") + "/"):
            return True
    return False


def _exempt(head: str, full: str, allowed: list[str]) -> bool:
    if head == full:
        return _below(full, allowed)
    # glob を含む対象: glob の手前の段が置き場の下にあるときだけ (置き場そのものを glob で空にしない)
    return _below(head, allowed)


def _skip_prefixes(tokens: list[str], i: int) -> int:
    """コマンドの語の位置まで進める (前置き・予約語・VAR=値・前置きのオプションと値を飛ばす)。"""
    n = len(tokens)
    while i < n and tokens[i] not in _BOUNDARIES:
        word = tokens[i]
        if word in _RESERVED or _ASSIGNMENT.match(word):
            i += 1
            continue
        if word in _PREFIXES:
            takes = _PREFIXES[word]
            i += 1
            while i < n and tokens[i].startswith("-") and tokens[i] not in _BOUNDARIES:
                i += 2 if tokens[i] in takes else 1
            if word == "timeout" and i < n and tokens[i] not in _BOUNDARIES:
                i += 1  # 時間の語
            continue
        break
    return i


def blocked_rm_targets(command: str, allowed: Iterable[str] = ()) -> list[str]:
    """`command` の中の、止める rm の対象 (正規化した絶対パス)。無ければ []。読めなければ []。"""
    tokens = _tokens(command)
    if not tokens:
        return []
    allowed = list(allowed)
    blocked: list[str] = []
    cwd: Optional[str] = WORKDIR
    stack: list[Optional[str]] = []
    i, n = 0, len(tokens)
    at_start = True
    while i < n:
        tok = tokens[i]
        if tok in _BOUNDARIES:
            if tok == "(":
                stack.append(cwd)
            elif tok == ")" and stack:
                cwd = stack.pop()
            at_start = True
            i += 1
            continue
        if not at_start:
            i += 1
            continue
        i = _skip_prefixes(tokens, i)
        if i >= n or tokens[i] in _BOUNDARIES:
            continue
        name = tokens[i]
        args = []
        j = i + 1
        while j < n and tokens[j] not in _BOUNDARIES:
            args.append(tokens[j])
            j += 1
        at_start = False
        i = j
        base = name.rsplit("/", 1)[-1]
        if base in ("cd", "pushd"):
            target = next((a for a in args if not a.startswith("-")), None)
            if "-" in args:
                cwd = None
            elif target is None:
                cwd = WORKDIR  # 引数なしの cd は HOME (/workspace)
            else:
                resolved = _resolve(target, cwd)
                cwd = resolved[1] if resolved and resolved[0] == resolved[1] else None
            continue
        if base == "popd":
            cwd = None
            continue
        if base not in _RM_WORDS:
            continue
        operands = []
        options_done = False
        k = 0
        while k < len(args):
            a = args[k]
            nxt = args[k + 1] if k + 1 < len(args) else None
            if a in _REDIRECTS:
                k += 2  # リダイレクトとその行き先
                continue
            if a.isdigit() and nxt in _REDIRECTS:
                k += 3  # 2>/dev/null など fd 番号付き
                continue
            if not options_done and a == "--":
                options_done = True
            elif options_done or not a.startswith("-") or a == "-":
                operands.append(a)
            k += 1
        for a in operands:
            resolved = _resolve(a, cwd)
            if resolved is None:
                continue
            head, full = resolved
            if (_guarded(head) or _guarded(full)) and not _exempt(head, full, allowed):
                blocked.append(full)
    return blocked
