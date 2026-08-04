"""SSE 直列化の一貫性(#99)。

#99 の欠陥は「json.dumps の既定が ensure_ascii=True」に起因し、同じ書き方が
13 箇所へコピーされて広がっていた。JSON としては同値なので表示は壊れず、
太っていることに誰も気づけない — テストが無ければ必ず再発する。

このモジュールは 2 つを固定する:

1. `utils/sse.py` の入口が実際に非 ASCII をエスケープしないこと
2. **リポジトリ全体の棚卸し** — `json.dumps` を裸で呼んでいる箇所が、
   意図して除外したもの(下の allowlist)だけであること
"""

import ast
import json
from pathlib import Path

import pytest

from nucllei.utils.sse import sse_line, sse_raw

# nucllei パッケージのルート(この階層 = test/apps/webui/utils)
PACKAGE_ROOT = Path(__file__).resolve().parents[4]

# --- 棚卸しの allowlist(#99 の C 分類)-------------------------------------
#
# ここに載る = 「ensure_ascii が無くて正しい」。載せるときは理由を書くこと。
# 迷ったら ensure_ascii=False を付ける側が安全(壊れる方向の違いは無い)。

# ファイルごと対象外(そのファイルの json.dumps はすべて非対象)
EXEMPT_FILES = {
    "internal/db.py": "DB カラムのストレージ表現。モデルにもブラウザにも出ない",
    "utils/logger.py": "ログ出力",
    "main.py": "ログ出力",
    "utils/access_control.py": "json.loads(json.dumps(x)) の deepcopy 代用",
    "utils/misc.py": "logit_bias の内部処理",
    "tools/seed.py": "自 API への送信ボディ(受け手がデコードする)",
    "routers/audio.py": "ファイル書き出しと外部 STT/TTS への送信ボディ",
    "routers/openai.py": "推論バックエンドへの送信ボディ",
}

# 呼び出しごと対象外(対象と非対象が同居するファイル)。
# キーは (相対パス, 正規化した呼び出しソース)。ソースが変わったら再レビューさせる。
EXEMPT_CALLS = {
    (
        "utils/middleware.py",
        "json.dumps(f, sort_keys=True)",
    ): "dict の重複排除キー。文字列は外へ出ず、決定性だけが要件",
    (
        "routers/ollama.py",
        "json.dumps(payload)",
    ): "推論バックエンドへの送信ボディ。受け手がデコードするのでトークンに影響しない",
    (
        "routers/ollama.py",
        "json.dumps(create_payload)",
    ): "ollama /api/create への送信ボディ",
}


def _normalize(segment: str) -> str:
    return " ".join(segment.split())


def _iter_source_files():
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        if "test" in path.relative_to(PACKAGE_ROOT).parts:
            continue
        yield path


def _bare_json_dumps_calls():
    """ensure_ascii=False を明示していない json.dumps 呼び出しを列挙する。"""
    found = []
    for path in _iter_source_files():
        rel = path.relative_to(PACKAGE_ROOT).as_posix()
        if rel in EXEMPT_FILES:
            continue

        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (
                isinstance(func, ast.Attribute)
                and func.attr == "dumps"
                and isinstance(func.value, ast.Name)
                and func.value.id == "json"
            ):
                continue

            explicit_false = any(
                kw.arg == "ensure_ascii"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is False
                for kw in node.keywords
            )
            if explicit_false:
                continue

            segment = _normalize(ast.get_source_segment(source, node) or "")
            found.append((rel, node.lineno, segment))
    return found


def test_no_unreviewed_bare_json_dumps():
    """裸の json.dumps は allowlist に載っているものだけ(#99 の棚卸し)。

    落ちたときの直し方:
      - SSE へ流すなら utils/sse.py の sse_line() を使う
      - モデルに渡る文字列なら ensure_ascii=False を付ける
      - どちらでもない(ログ・DB・送信ボディ等)なら上の allowlist へ理由付きで追加
    """
    offenders = [
        (rel, lineno, segment)
        for rel, lineno, segment in _bare_json_dumps_calls()
        if (rel, segment) not in EXEMPT_CALLS
    ]

    assert offenders == [], "ensure_ascii=False の無い json.dumps:\n" + "\n".join(
        f"  {rel}:{lineno}  {segment}" for rel, lineno, segment in offenders
    )


def test_allowlist_entries_all_still_exist():
    """allowlist が現実に追随していること(消えた除外を残さない)。

    ファイル除外は対象ファイルの存在、呼び出し除外は実際に裸の呼び出しが
    残っていることを見る。撤去済みの除外は削れという合図。
    """
    for rel in EXEMPT_FILES:
        assert (PACKAGE_ROOT / rel).exists(), f"EXEMPT_FILES の {rel} が存在しない"

    live = {(rel, segment) for rel, _, segment in _bare_json_dumps_calls()}
    stale = set(EXEMPT_CALLS) - live
    assert stale == set(), f"EXEMPT_CALLS に実体の無い項目が残っている: {stale}"


def test_no_hand_built_sse_lines():
    """`data: ` の行組み立ては utils/sse.py の外でやらない。

    json.dumps を経由せず f-string で JSON を手組みする書き方(ollama の
    ダウンロード進捗が実際にそうだった)は上の棚卸しをすり抜けるため、
    枠組みそのものを 1 箇所に閉じ込めて塞ぐ。
    """
    offenders = []
    for path in _iter_source_files():
        rel = path.relative_to(PACKAGE_ROOT).as_posix()
        if rel == "utils/sse.py":
            continue

        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            # f-string の先頭が "data: " で始まるもの(定数のみの
            # "data: [DONE]" は JoinedStr にならないので対象外)
            if not isinstance(node, ast.JoinedStr) or not node.values:
                continue
            head = node.values[0]
            if isinstance(head, ast.Constant) and str(head.value).startswith("data: "):
                offenders.append(f"{rel}:{node.lineno}")

    assert offenders == [], (
        "SSE 行は utils/sse.py の sse_line()/sse_raw() で組むこと: " f"{offenders}"
    )


def test_no_module_imports_dumps_directly():
    """`from json import dumps` は allowlist の検出を素通りするので禁じる。"""
    offenders = []
    for path in _iter_source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "json":
                names = [a.name for a in node.names]
                if "dumps" in names or "dump" in names:
                    offenders.append(f"{path.relative_to(PACKAGE_ROOT)}:{node.lineno}")
    assert offenders == [], f"json.dumps は json 経由で呼ぶこと: {offenders}"


# --- utils/sse.py の入口 ----------------------------------------------------


def test_sse_line_keeps_non_ascii_unescaped():
    line = sse_line({"content": "針刺し・切創"})
    assert line == 'data: {"content": "針刺し・切創"}\n\n'
    assert "\\u" not in line


def test_sse_line_is_valid_sse_framing():
    line = sse_line({"a": 1})
    assert line.startswith("data: ")
    assert line.endswith("\n\n")
    assert json.loads(line[len("data: ") : -2]) == {"a": 1}


def test_sse_line_accepts_list_payloads():
    assert sse_line([{"値": 1}]) == 'data: [{"値": 1}]\n\n'


def test_sse_raw_does_not_double_serialize():
    """既に JSON 文字列になっているものは二重に直列化しない。"""
    assert sse_raw('{"content": "曝露"}') == 'data: {"content": "曝露"}\n\n'


@pytest.mark.parametrize("text", ["針刺し・切創", "曝露時の対応", "ﾊﾝﾞ"])
def test_sse_line_is_shorter_than_escaped_form(text):
    """非 ASCII があるほど素の json.dumps より短い(回線量の回帰を可視化)。"""
    ours = sse_line({"content": text})
    escaped = f'data: {json.dumps({"content": text})}\n\n'
    assert len(ours) < len(escaped)
    assert len(escaped) - len(ours) == 5 * len(text)
