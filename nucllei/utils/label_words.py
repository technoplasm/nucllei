"""tool の文がモデルへの指示・印に使う語 (`次の手:` `返答への指示:` `ERROR:` …) と、利用者・管理者が書いた
値の中のそれを見つける・区切りを変える判定。

tool の結果は `ERROR: 事実。次の手: …` の形で、モデルは「語 + コロン」を tool の指示・印として読む。
書いた人の値 (レシピのフォルダ名・付属の名前・参照先のパス・description、グループ名) がそのまま tool の文に
埋まると、値の中の `次の手:` が本物より先に出て偽の指示になる。ここはその判定だけを持つ (I/O も設定も
読まない):

- `has_label` — 語 + コロンを含むか。見た目の似た形も拾う: 文字ごとの NFKC、書式文字 (ゼロ幅等) と
  空白を落とす、casefold、コロンの仲間 (互換分解で `:` になる文字・Unicode の名前に COLON を含む
  文字・`_COLON_LIKE`)。名前の規則 (レシピのフォルダ名 `recipes.name_problem`、グループ名
  `group_names.validate_group_name`) が断るのに使う
- `defuse_labels` — 埋める値の区切りを変える (`次の手:` → `次の手 -`。似た形が残ればコロンの仲間を全部
  ` -` に)。`recipes.plain` / `recipes.neutral` が使う
"""

import functools
import re
import unicodedata

# 語 (この順に意味は無い)。tool の文の印・指示の見出しに使うもの
LABEL_WORDS = ("返答への指示", "次の手", "中止", "ERROR", "警告", "壊れている")

# よくある形 (全角・縦書き・比の記号のコロンも) — 区切りを変えるときの第一段
RE_LABEL = re.compile(r"(返答への指示|次の手|中止|ERROR|警告|壊れている)\s*[:：︓﹕∶꞉]")

# 正規化した形の語 (`_folded` の後で照合する)
_LABEL_WORDS_FOLDED = tuple(w.casefold() for w in LABEL_WORDS)

# 名前に COLON を含まず互換分解でも `:` にならないが、コロンに見える文字 (比の記号・縦書きの二点リーダー・
# 二点の句読点・ヘブライ文字の句点・ルーン文字の句読点)
_COLON_LIKE = frozenset("∶︰⁚׃᛬")


@functools.lru_cache(maxsize=4096)
def is_colon(c: str) -> bool:
    """コロンの仲間 (互換分解で `:` になる文字、Unicode の名前に COLON を含む文字、`_COLON_LIKE`)。"""
    return (
        c in _COLON_LIKE
        or unicodedata.normalize("NFKC", c) == ":"
        or "COLON" in unicodedata.name(c, "")
    )


def _folded(t: str) -> str:
    """照合用の形: 文字ごとに NFKC、書式文字 (ゼロ幅等) と空白を落とし、コロンの仲間は `:`、casefold。"""
    out = []
    for c in t:
        if unicodedata.category(c) == "Cf" or c.isspace():
            continue
        out.append(":" if is_colon(c) else unicodedata.normalize("NFKC", c))
    return "".join(out).casefold()


def has_label(t: str) -> bool:
    """`t` に指示・印の語 + コロンがあるか (見た目の似た形も)。"""
    f = _folded(t)
    return any(f"{w}:" in f for w in _LABEL_WORDS_FOLDED)


def defuse_labels(t: str) -> str:
    """指示・印の語 + コロンの区切りを変える (`次の手:` → `次の手 -`)。正規表現で拾えない似た形が残って
    いれば、値の中のコロンの仲間をすべて ` -` にする (どの語にもコロンが続かなくなる)。"""
    t = RE_LABEL.sub(r"\1 -", t)
    if has_label(t):
        t = "".join(" -" if is_colon(c) else c for c in t)
    return t
