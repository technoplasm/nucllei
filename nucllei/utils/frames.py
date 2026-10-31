"""サーバが組む枠 (`<conversation-context>` 等) を、記憶・レシピ・添付・利用者の本文から作れないようにする。

枠の名前に見える箇所 — `<`、`/` (任意)、枠の名前 — を探し、その箇所の先頭の `<` だけを `&lt;` にする。
探すときは行を NFKD にし、見えない文字 (書式・結合・異体字セレクタ・タグ文字・既定で無視される文字) を除き、
`<` に似た括弧 (`＜` `﹤` `‹` `⟨` `˂` `〈` `≮` 等) を `<`、ラテン文字に似たキリル・ギリシャ文字をラテン文字に
そろえる。名前の語の間の空白・`_` `-` `.` `/` ダッシュ類・長音の変種、大小は問わない。一致しない `<` と
ほかの行は 1 字も変えない (記憶の目次の行を edit_file の old_text にそのまま写せるように・HTML の `<b>` 等を
壊さないように)。何度掛けても同じ結果で、入力だけで決まる (毎ターン同じバイトになる)。

掛ける対象: 添付の `<source>` の本文と利用者が書いた本文 (utils/conversation_context)、記憶の目次の行
(utils/memory_index)、レシピの一覧 (task.fetch_context_sections)。前置きのテンプレートの地の文 (配備が書く) には
掛けない。記憶の目次・レシピの一覧・添付の本文には記憶の目次の枠 `<memory-index>` の名前も含める。
"""

from __future__ import annotations

import re
import unicodedata

CONTEXT_TAG = "conversation-context"
CONTEXT_UPDATE_TAG = "conversation-context-update"

# 探すときに `<` とみなす文字 (NFKD で `<` を含む文字もそうみなす)
_LT_LIKE = frozenset(
    "<\u2039\u27e8\u02c2\u3008\u2329\u276e\u29fc\ufe64\uff1c\u1438\u227a"
)
# 既定で無視される (見えない) 文字のうち、一般カテゴリ Cf / Mn / Me に入らないもの
_IGNORABLE_EXTRA = frozenset(
    "\u034f\u115f\u1160\u17b4\u17b5\u180b\u180c\u180d\u180e\u180f\u3164\uffa0"
)
# ラテン文字に似たキリル・ギリシャ文字
_CONFUSABLE = str.maketrans(
    {
        "\u0430": "a",
        "\u0435": "e",
        "\u043e": "o",
        "\u0440": "p",
        "\u0441": "c",
        "\u0445": "x",
        "\u0443": "y",
        "\u0456": "i",
        "\u0455": "s",
        "\u0458": "j",
        "\u0501": "d",
        "\u04bb": "h",
        "\u043a": "k",
        "\u043c": "m",
        "\u043d": "h",
        "\u0442": "t",
        "\u0432": "b",
        "\u0410": "A",
        "\u0415": "E",
        "\u041e": "O",
        "\u0420": "P",
        "\u0421": "C",
        "\u0422": "T",
        "\u0425": "X",
        "\u041a": "K",
        "\u041c": "M",
        "\u041d": "H",
        "\u0412": "B",
        "\u0406": "I",
        "\u0405": "S",
        "\u0408": "J",
        "\u03bf": "o",
        "\u03bd": "v",
        "\u03b9": "i",
        "\u03ba": "k",
        "\u039f": "O",
        "\u0391": "A",
        "\u0395": "E",
        "\u03a4": "T",
        "\u0399": "I",
        "\u039a": "K",
        "\u039c": "M",
        "\u039d": "N",
        "\u03a1": "P",
        "\u03a7": "X",
    }
)
_SEP = r"[\s_\-\u2010-\u2015\u2212\u30fc\u30fb\u00b7./\\:]*"
_CORE_NAMES = rf"conversation{_SEP}context(?:{_SEP}update)?|source"
_MEMORY_NAME = rf"memory{_SEP}index"
_FRAMEISH = re.compile(rf"<\s*/?\s*(?:{_CORE_NAMES})\b", re.IGNORECASE)
_FRAMEISH_WITH_MEMORY = re.compile(
    rf"<\s*/?\s*(?:{_CORE_NAMES}|{_MEMORY_NAME})\b", re.IGNORECASE
)


def _ignorable(ch: str) -> bool:
    cp = ord(ch)
    return (
        unicodedata.category(ch) in ("Cf", "Mn", "Me")
        or ch in _IGNORABLE_EXTRA
        or 0xFE00 <= cp <= 0xFE0F
        or 0xE0000 <= cp <= 0xE0FFF
    )


def _probe(line: str) -> tuple[str, list[int]]:
    """照合用の文字列と、その各文字が元の行の何文字目から来たか。"""
    chars: list[str] = []
    origin: list[int] = []
    for i, ch in enumerate(line):
        if ch in _LT_LIKE:
            chars.append("<")
            origin.append(i)
            continue
        for d in unicodedata.normalize("NFKD", ch):
            if _ignorable(d):
                continue
            chars.append(d.translate(_CONFUSABLE))
            origin.append(i)
    return "".join(chars), origin


def defuse_line(line: str, memory_index: bool = False) -> str:
    """1 行を無害化する (枠の名前に見える箇所の `<` だけ `&lt;` に)。`memory_index` なら `<memory-index>` の
    枠の名前も。"""
    pattern = _FRAMEISH_WITH_MEMORY if memory_index else _FRAMEISH
    probe, origin = _probe(line)
    hits = {origin[m.start()] for m in pattern.finditer(probe)}
    if not hits:
        return line
    return "".join("&lt;" if i in hits else ch for i, ch in enumerate(line))


def defuse(text: str, memory_index: bool = False) -> str:
    """複数行の文を行ごとに無害化する (改行は `\\n` で切る)。"""
    if not text:
        return text
    return "\n".join(defuse_line(line, memory_index) for line in text.split("\n"))
