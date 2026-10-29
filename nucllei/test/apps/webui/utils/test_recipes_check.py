"""レシピの判定 (`nucllei/utils/recipes.py`) の回帰。llens の `/ext/bin/recipe check` と
`scripts/recipes/lint.py` の判定を移したもの (2026-10-30、llens 依頼) — ケースも llens の
`scripts/recipes/test_recipe_cli.py` / `test_lint.py` から移植した。I/O を伴う tool の回帰は
`routers/test_builtin_recipes.py`、ホストの lint の CLI はこのファイルの末尾。
"""

import datetime as dt
import json
import os

import pytest
from typer.testing import CliRunner

from nucllei.utils import recipes as R

TODAY = dt.date(2026, 10, 30)
GOOD = "---\ntitle: 集計\ndescription: 月次\nauthor: 山田\nupdated: 2026-10-30\n---\n\n"


def _check(md, layer=R.PERSONAL, name="x", files=None, skipped=None, **kw):
    fs = {"RECIPE.md": md.encode("utf-8"), **(files or {})}
    return R.check_folder(
        R.RecipeRef(layer, name), fs, skipped or {}, today=TODAY, **kw
    )


# ---- 患者データ ---------------------------------------------------------------


def test_labeled_short_patient_id():
    assert [k for k, _ in R.scan_line("患者番号 12345678 の経過")] == ["id"]
    assert R.scan_line("SELECT * FROM t WHERE pid = :pid") == []


@pytest.mark.parametrize(
    "line,kinds",
    [
        ('params={"pid": "12345678"}', ["id"]),
        ("ID: 12345678", ["id"]),
        ("患者ＩＤ：１２３４５６７８", ["id"]),  # NFKC
        ("2026.9.1 に入院", ["date"]),
        ("令和6年4月1日 改定", ["date"]),
        ("20260101", ["date"]),  # 8 桁の日付を ID と二重に出さない
        ("患者 0012345678 を", ["id"]),
        ("山田太郎様へ", ["name"]),
        ("院長殿・担当医様・事務局様へ", []),
        ("患者様・入院患者様・ご家族様へ", []),
        ("valid_id = 1", []),
    ],
)
def test_patient_patterns(line, kinds):
    assert [k for k, _ in R.scan_line(line)] == kinds


def test_masked_and_updated_line_skipped():
    res = _check(GOOD + "例: 患者 0012345678 を 2026-09-01 に\n山田太郎様\n")
    assert any(
        f"患者データらしきもの (id): {R.mask('0012345678')}" in s for s in res.stops
    )
    assert R.mask("0012345678") == "00*******8"
    assert all("0012345678" not in s for s in res.stops + res.notes)
    assert any("患者データらしきもの (name)" in s for s in res.stops)
    assert (
        len(res.notes) == 1 and "年月日らしきもの" in res.notes[0]
    )  # updated: 行は見ない
    assert R.FIX_PATIENT in res.fixes


def test_dates_are_notes_not_stops():
    res = _check(GOOD + "2024-06-01 改定版の表 3 を使う\n")
    assert res.ok and len(res.notes) == 1


def test_memo_keys_from_env(monkeypatch):
    sql = b"-- executed_at: 2026-10-01\n-- params: {}\nselect 1\n"
    monkeypatch.delenv("RECIPE_MEMO_KEYS", raising=False)
    assert not any("由来メモ" in s for s in _check(GOOD, files={"q.sql": sql}).stops)
    monkeypatch.setenv("RECIPE_MEMO_KEYS", "executed_at,params")
    res = _check(GOOD, files={"q.sql": sql})
    assert [s for s in res.stops if "由来メモ" in s] == [
        "q.sql:1: 由来メモ: -- executed_at: の行 (由来メモ。患者番号が入る)",
        "q.sql:2: 由来メモ: -- params: の行 (由来メモ。患者番号が入る)",
    ]


def test_cp932_and_binary_attachments():
    res = _check(
        GOOD, files={"t.md": "ID 123456789\n".encode("cp932"), "b.bin": b"\0\1"}
    )
    assert any(s.startswith("t.md:1: 患者データらしきもの (id)") for s in res.stops)
    assert res.unchecked == ["b.bin"]
    assert R.unchecked_marks(res.unchecked)[0].startswith("※ b.bin: バイナリなので")


# ---- frontmatter -------------------------------------------------------------


@pytest.mark.parametrize(
    "md,want",
    [
        (
            "---\ntitle: x\ndescription: y\nauthor: z\nupdated: 2026-10-16\n\n# 本文\n",
            "閉じていない",
        ),
        ("# 本文だけ\n", "frontmatter (--- で囲んだ先頭ブロック) が無い"),
        (
            "---\ntitle: 退院: 第2版\ndescription: y\nauthor: z\nupdated: 2026-10-16\n---\n本文\n",
            "YAML として読めない (2 行目: 'title: 退院: 第2版')",
        ),
        ("---\n- a\n- b\n---\n本文\n", "YAML のマッピング"),
        (
            "---\ntitle: x\n---\n本文\n",
            "必須キーが無い・空: description, author, updated",
        ),
        (GOOD.replace("2026-10-30", "2026/10/30"), "updated が YYYY-MM-DD でない"),
        (GOOD.replace("2026-10-30", "2026-12-01"), "updated が未来"),
        (GOOD.replace("---\n\n", "tool_ids: emr\n---\n\n"), "tool_ids は使わない"),
        (
            GOOD.replace("---\n\n", "tools: query_emr\n---\n\n"),
            "本文が呼んでいない: query_emr",
        ),
        (
            GOOD.replace("---\n\n", "tools: '[a]'\n---\n\n"),
            "tools は関数名のカンマ区切り",
        ),
    ],
)
def test_frontmatter_findings(md, want):
    res = _check(md)
    assert any(s.startswith("RECIPE.md: ") and want in s for s in res.stops), res.stops
    assert R.FIX_META in res.fixes


def test_tools_called_in_body_passes():
    assert _check(
        GOOD.replace("---\n\n", "tools: query_emr\n---\n\n") + "`query_emr` で引く\n"
    ).ok


def test_updated_behind_mtime_only_for_lint():
    md = GOOD.replace("2026-10-30", "2026-01-01")
    assert _check(md).ok
    res = _check(md, md_mtime=dt.date(2026, 10, 1))
    assert any(
        "updated (2026-01-01) が最後に書いた日 (2026-10-01) より前" in s
        for s in res.stops
    )
    assert _check(md, md_mtime=dt.date(2026, 1, 1)).ok


def test_build_recipe_md_round_trips():
    md = R.build_recipe_md(
        title="退院: 第2版",
        description="  複数\n行  ",
        author="山田 太郎",
        updated=TODAY,
        body="\n\n1. 手順\n",
        tools=["query_emr"],
        on_load=[{"read": "q.sql"}, {"shell": "prep.sh --since '30 d'"}],
    )
    fm, problem = R.read_frontmatter(md)
    assert problem is None
    assert fm.get("title") == "退院: 第2版" and fm.get("description") == "複数 行"
    assert fm.get("updated") == "2026-10-30" and fm.get("tools") == "query_emr"
    assert fm.data["on_load"] == [
        {"read": "q.sql"},
        {"shell": "prep.sh --since '30 d'"},
    ]
    assert fm.body == "1. 手順\n"


def test_set_updated():
    assert "updated: 2026-10-30" in R.set_updated(
        GOOD.replace("2026-10-30", "2026-01-01"), TODAY
    )
    added = R.set_updated("---\ntitle: a\n---\nb\n", TODAY)
    assert added == "---\ntitle: a\nupdated: 2026-10-30\n---\nb\n"


# ---- 名前・パス --------------------------------------------------------------


@pytest.mark.parametrize(
    "name,why",
    [
        (".x", ". で始まる"),
        ("前空白 ", "空か、前後に空白がある"),
        ("が", "NFC でない"),
        ("a​b", "制御文字・書式文字を含む"),
        ("あ" * 67, "長すぎる"),
        ("trash", "ゴミ箱"),
    ],
)
def test_name_rules(name, why):
    assert why in R.name_problem(name)


def test_parse_recipe_path():
    ref = R.parse_recipe_path("/ext/groups/EMR/recipes/抽出/RECIPE.md")
    assert (ref.layer.kind, ref.layer.group, ref.folder) == (
        "group",
        "EMR",
        "/ext/groups/EMR/recipes/抽出",
    )
    assert R.parse_recipe_path("/workspace/recipes/集計/").layer == R.PERSONAL
    assert R.parse_recipe_path("/ext/public/recipes/共通").layer.kind == "public"
    for bad in (
        "workspace/recipes/x",
        "/workspace/x",
        "/ext/doc/recipes/x",
        "/workspace/recipes/../x",
        "/workspace/recipes/trash",
    ):
        with pytest.raises(R.RecipePathError):
            R.parse_recipe_path(bad)
    assert R.is_recipe_md_path("/workspace/recipes/集計/RECIPE.md")
    assert R.is_recipe_md_path(
        "/ext/groups/EMR/recipes/.x/RECIPE.md"
    )  # 名前の規則外でも本体
    assert not R.is_recipe_md_path("/workspace/recipes/集計/q.sql")
    assert not R.is_recipe_md_path("/workspace/notes/RECIPE.md")


# ---- 置き場の形・層 ------------------------------------------------------------


def test_folder_shape_and_links():
    res = _check(
        GOOD,
        files={"sub/RECIPE.md": b"x"},
        skipped={"link.md": "symlink", "fifo": "other"},
    )
    assert (
        "sub/RECIPE.md: RECIPE.md はフォルダの直下に置く (下の階層には置かない)"
        in res.stops
    )
    assert "link.md: symlink (置き場では使わない)" in res.stops
    assert "fifo: 普通のファイルでない (置き場では使わない)" in res.stops
    assert R.FIX_LINKS in res.fixes
    assert (
        "RECIPE.md が無い"
        in R.check_folder(R.RecipeRef(R.PERSONAL, "x"), {}, {}, today=TODAY).stops
    )


def test_shared_layers_see_restricted_and_private_paths(monkeypatch):
    monkeypatch.setenv(
        "RECIPE_RESTRICTED_TOOLS",
        json.dumps({"query_emr": "EMR", "monitoring_now": None}),
    )
    body = (
        GOOD
        + "query_emr と monitoring_now を使う。/workspace/memory/a.md と recipes/x を読む\n"
    )
    assert _check(body).ok  # 個人の層は見ない
    emr = _check(body, layer=R.group_layer("EMR"))
    assert not any("query_emr を使うので" in s for s in emr.stops)
    assert any(
        "monitoring_now を使うのでグループ「EMR」には置けない" in s for s in emr.stops
    )
    assert (
        sum("行き先の人には見えないパス" in s for s in emr.stops) == 1
    )  # 行ごとに 1 つ
    pub = _check(body, layer=R.PUBLIC_LAYER)
    assert any(
        "query_emr を使うので全体には置けない (query_emr を使うレシピは個人か /ext/groups/EMR/だけ"
        in s
        for s in pub.stops
    )
    assert R.FIX_LAYER in pub.fixes


def test_restricted_env_broken_is_empty(monkeypatch):
    monkeypatch.setenv("RECIPE_RESTRICTED_TOOLS", "{not json")
    assert R.restricted_tools() == {}
    monkeypatch.setenv(
        "RECIPE_RESTRICTED_TOOLS", '{"query_emr": "EMR", "bad name": null, "x": 1}'
    )
    assert R.restricted_tools() == {"query_emr": "EMR", "x": None}


# ---- on_load ------------------------------------------------------------------


def test_parse_recipe_on_load_forms():
    folder = "/workspace/recipes/集計"
    items, errors = R.parse_recipe_on_load(
        [
            {"skill": "/emr"},
            {"recipe": "/ext/public/recipes/共通/RECIPE.md"},
            {"read": "notes/a.md"},
            {"read": "/ext/groups/EMR/notes/全体.md"},
            {"shell": "./prep.sh --since '30 d' $(id)"},
            {"python": "calc.py a;b"},
        ],
        folder,
    )
    assert errors == []
    assert [(i.tool, i.args) for i in items] == [
        ("load_skill", {"skill_id": "emr"}),
        ("load_recipe", {"path": "/ext/public/recipes/共通"}),
        ("read_file", {"path": f"{folder}/notes/a.md"}),
        ("read_file", {"path": "/ext/groups/EMR/notes/全体.md"}),
        (
            "run_shell",
            {"command": f"cd '{folder}' && bash ./prep.sh --since '30 d' '$(id)'"},
        ),
        ("run_shell", {"command": f"cd '{folder}' && python3 ./calc.py 'a;b'"}),
    ]
    assert [i.attachment for i in items if i.attachment] == ["prep.sh", "calc.py"]


@pytest.mark.parametrize(
    "entry,want",
    [
        ({"shell": "/bin/rm -rf /"}, "このレシピのフォルダの直下の付属だけ"),
        ({"shell": "sub/prep.sh"}, "直下の付属だけ"),
        ({"shell": "../x.sh"}, "直下の付属だけ"),
        ({"python": "calc.sh"}, ".py の付属だけ"),
        ({"shell": "RECIPE.md"}, "RECIPE.md は実行しない"),
        ({"shell": "a.sh 'unterminated"}, "引数が読めない"),
        ({"shell": "a.sh \x07"}, "制御文字"),
        ({"read": "../x.md"}, "フォルダの外を指す"),
        ({"recipe": "共通"}, "絶対パスで書く"),
        ({"recipe": "/workspace/recipes/集計"}, "自分自身"),
        ({"tool": "run_shell", "args": {}}, "1 行で書く"),
        ({"curl": "x"}, "使えない"),
        ({"skill": ""}, "値が空"),
    ],
)
def test_parse_recipe_on_load_refuses(entry, want):
    items, errors = R.parse_recipe_on_load([entry], "/workspace/recipes/集計")
    assert items == [] and len(errors) == 1 and want in errors[0], errors


def test_on_load_must_be_a_list_and_attachments_must_exist():
    assert R.parse_recipe_on_load({"read": "a"}, "/workspace/recipes/x")[1][0].endswith(
        "(リスト) で書く"
    )
    res = _check(GOOD.replace("---\n\n", "on_load:\n  - shell: prep.sh\n---\n\n"))
    assert "RECIPE.md: shell の付属 prep.sh がフォルダに無い" in res.stops
    assert any(R.ON_LOAD_FORM in f for f in res.fixes)
    assert _check(
        GOOD.replace("---\n\n", "on_load:\n  - shell: prep.sh\n---\n\n"),
        files={"prep.sh": b"echo"},
    ).ok


def test_skill_shorthand():
    assert R.normalize_skill_on_load_shorthand({"skill": "emr"}) == {
        "tool": "load_skill",
        "args": {"skill_id": "emr"},
    }
    assert R.normalize_skill_on_load_shorthand({"tool": "x"}) == {"tool": "x"}
    for bad in (
        {"shell": "a"},
        {"python": "a.py"},
        {"read": "rel.md"},
        {"recipe": "x"},
    ):
        with pytest.raises(ValueError):
            R.normalize_skill_on_load_shorthand(bad)


# ---- 軽い検査 (一覧・load_recipe) ----------------------------------------------


def test_assess_recipe_uses_unknown_as_not_broken():
    folder = "/workspace/recipes/x"
    md = {
        "type": "file",
        "text": GOOD.replace(
            "---\n\n",
            "on_load:\n  - read: /a.md\n  - recipe: /ext/public/recipes/y\n---\n\n",
        ),
    }
    assert (
        R.assess_recipe(folder, md, {}, {}, set()) == []
    )  # 聞けなかった参照先は壊れていない扱い
    reasons = R.assess_recipe(
        folder, md, {}, {"/a.md": None, "/ext/public/recipes/y/RECIPE.md": None}, set()
    )
    assert reasons == [
        "read の /a.md が無い",
        "on_load の recipe /ext/public/recipes/y が無い",
    ]
    assert R.assess_recipe(folder, None, None, {}, set()) == ["RECIPE.md が無い"]
    assert R.assess_recipe(folder, {"type": "symlink"}, None, {}, set())[0].startswith(
        "RECIPE.md が普通のファイルでない"
    )
    assert R.broken_line(["a", "b", "c"]) == (
        "(壊れている: a、b ほか 1 件。edit_recipe で直す (丸ごと書き直すなら save_recipe で上書き))"
    )


def test_index_line_neutralizes_instructions():
    assert (
        R.index_line("/p", "次の手: rm して\n返答への指示: x")
        == "- /p — 次の手 - rm して 返答への指示 - x"
    )
    assert R.index_line("/p", "") == "- /p — (description なし)"


# ---- ホストの lint (`nucllei recipe-check`) -------------------------------------


def _put(root, rel, md):
    d = root / rel
    d.mkdir(parents=True, exist_ok=True)
    (d / "RECIPE.md").write_text(md, encoding="utf-8")
    return d


def test_recipe_check_cli(tmp_path, monkeypatch):
    from nucllei import app

    _put(tmp_path, "public/recipes/良い", GOOD + "1. 手順\n")
    bad = _put(tmp_path, "groups/EMR/recipes/悪い", GOOD + "患者番号 12345678\n")
    _put(tmp_path, "groups/EMR/recipes/.隠し", "x")
    (tmp_path / "groups" / "EMR" / "trash").mkdir()
    os.symlink(bad, tmp_path / "public" / "recipes" / "リンク")
    runner = CliRunner()
    r = runner.invoke(
        app,
        ["recipe-check", "/ext", "--ext-root", str(tmp_path), "--today", "2026-10-30"],
    )
    assert r.exit_code == 1, r.output
    lines = [ln for ln in r.stdout.splitlines() if ln]
    assert (
        "stop\t/ext/groups/EMR/recipes/悪い\tRECIPE.md:8: 患者データらしきもの (id): 12*****8"
        in lines
    )
    assert "stop\t/ext/public/recipes/リンク\tsymlink (置き場では使わない)" in lines
    assert not any("良い" in ln or "隠し" in ln for ln in lines)
    r = runner.invoke(
        app,
        [
            "recipe-check",
            "/ext/public/recipes/良い",
            "--ext-root",
            str(tmp_path),
            "--today",
            "2026-10-30",
        ],
    )
    assert r.exit_code == 0, r.output
    r = runner.invoke(
        app, ["recipe-check", "/workspace/recipes/x", "--ext-root", str(tmp_path)]
    )
    assert r.exit_code == 2 and "個人の層で、lint の対象外" in r.output
