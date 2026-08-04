"""SSE(text/event-stream)の 1 行を組み立てる唯一の場所。

**JSON の直列化は必ず `ensure_ascii=False`**(#99)。既定(True)だと日本語が
1 文字ごとに `\\uXXXX`(6 文字)へ展開され、素の 6 倍の回線量になる。
JSON としては同値なのでブラウザのパース結果は変わらず、**表示は壊れない** —
つまり壊れないまま黙って太る種類の欠陥で、書いた本人も気づけない。

`data: ` の前置と `\\n\\n` の終端もここに閉じ込める。SSE を吐く側が
f-string を自前で組むのをやめれば、`json.dumps` の既定に戻る事故は構造的に
起きなくなる(#99 は同じ書き方が 13 箇所へコピーされていた)。

回帰の番人は `test/apps/webui/utils/test_sse.py`(棚卸しの実行可能版)。
"""

import json


def sse_line(data) -> str:
    """dict/list を SSE の 1 行へ。"""
    return sse_raw(json.dumps(data, ensure_ascii=False))


def sse_raw(payload: str) -> str:
    """直列化済みの文字列を SSE の 1 行へ。

    pydantic の `model_dump_json()` など、既に JSON 文字列になっているものを
    二重に直列化せず流すための入口(model_dump_json も既定で非 ASCII を
    エスケープしないので、そのまま流してよい)。
    """
    return f"data: {payload}\n\n"
