"""レシピ用の endpoint (`/recipes/index` `/recipes/folder`) と `/files/trash` の `name`
(2026-10-30、llens 依頼・ken 指示「ぜんぶ nuc にやらせて」)。

固定すること:

- `/recipes/index` は層ごとに `recipes/<名前>/` と RECIPE.md の先頭を返す。`.` 始まりは出さない、
  symlink のフォルダ・RECIPE.md は辿らず種類だけ、bind に無い層は `error` にして他の層は返す
- `/recipes/folder` は付属を含む全ファイルの本文を返し、symlink は `skipped` (辿らない)。
  合計が上限を超えたら 413
- `/files/trash` の `name` はゴミ箱での名前 (同名の `-2` 規則は同じ)、不正な名前は 400
- 一覧は毎リクエスト走るので、100 件の層でも速いこと (上限は緩く 1 秒)

実行:
    uv run task sandbox-unit
"""

import base64
import importlib.util
import os
import pathlib
import sys
import time

import pytest
from fastapi.testclient import TestClient

_MAIN = pathlib.Path(__file__).resolve().parents[1] / "app" / "main.py"

MEMBER_MOUNTS = [
    {"path": "/ext/public", "rw": False},
    {"path": "/ext/groups/EMR", "rw": True},
]


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("SANDBOX_WORKSPACES", str(tmp_path / "workspaces"))
    monkeypatch.delenv("SANDBOX_TOKEN", raising=False)
    spec = importlib.util.spec_from_file_location("sandbox_main_recipes", _MAIN)
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
    (ext / "groups" / "OTHER" / "recipes" / "secret").mkdir(parents=True)
    (ext / "groups" / "OTHER" / "recipes" / "secret" / "RECIPE.md").write_text("x")
    (ext / "public").mkdir()
    monkeypatch.setattr(mod, "RO_MOUNT_ROOT", ext)
    monkeypatch.setattr(os, "chown", lambda *a, **k: None)
    monkeypatch.setattr(os, "fchown", lambda *a, **k: None)
    ws = ws_root / "u1"
    ws.mkdir(parents=True)
    return mod, TestClient(mod.app), ws, ext


def _post(c, ep, **body):
    return c.post(ep, json={"user_id": "u1", **body})


def _recipe(base: pathlib.Path, name: str, md: str = "---\ntitle: t\n---\nbody\n"):
    d = base / "recipes" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "RECIPE.md").write_text(md, encoding="utf-8")
    return d


def _place(res, place):
    return next(p for p in res["places"] if p["place"] == place)


def test_index_layers_and_kinds(env):
    mod, c, ws, ext = env
    _recipe(ws, "集計", "---\ntitle: 集計\ndescription: 月次\n---\n本文\n")
    (ws / "recipes" / "空のフォルダ").mkdir()
    (ws / "recipes" / "散らばった.md").write_text("x")
    (ws / "recipes" / ".tmp").mkdir()
    (ws / "elsewhere").mkdir()
    os.symlink(ws / "elsewhere", ws / "recipes" / "リンク")
    d = _recipe(ws, "mdがリンク")
    (d / "RECIPE.md").unlink()
    os.symlink(ws / "elsewhere", d / "RECIPE.md")
    _recipe(ext / "groups" / "EMR", "抽出")
    r = _post(
        c,
        "/recipes/index",
        places=["/workspace", "/ext/groups/EMR", "/ext/groups/OTHER", "/ext/public"],
        mounts=MEMBER_MOUNTS,
    )
    assert r.status_code == 200, r.text
    res = r.json()
    w = _place(res, "/workspace")
    assert w["exists"] is True
    by = {x["name"]: x for x in w["recipes"]}
    assert ".tmp" not in by
    assert by["集計"]["type"] == "dir"
    head = base64.b64decode(by["集計"]["md"]["head_b64"]).decode()
    assert (
        head.startswith("---\ntitle: 集計") and by["集計"]["md"]["truncated"] is False
    )
    assert by["空のフォルダ"]["md"] is None
    assert by["散らばった.md"]["type"] == "file" and "md" not in by["散らばった.md"]
    assert by["リンク"]["type"] == "symlink" and "md" not in by["リンク"]
    assert by["mdがリンク"]["md"] == {"type": "symlink", "size": 0}
    g = _place(res, "/ext/groups/EMR")
    assert [x["name"] for x in g["recipes"]] == ["抽出"]
    # bind に無いグループは error、recipes/ の無い層は exists: False
    assert _place(res, "/ext/groups/OTHER")["error"] == 404
    assert _place(res, "/ext/public") == {
        "place": "/ext/public",
        "exists": False,
        "recipes": [],
    }


def test_index_head_is_capped(env):
    mod, c, ws, ext = env
    _recipe(ws, "長い", "---\ntitle: t\n---\n" + "あ" * 1000)
    r = _post(c, "/recipes/index", places=["/workspace"], head_bytes=10)
    md = _place(r.json(), "/workspace")["recipes"][0]["md"]
    assert len(base64.b64decode(md["head_b64"])) == 10 and md["truncated"] is True


def test_index_invalid_place(env):
    mod, c, ws, ext = env
    r = _post(c, "/recipes/index", places=["/etc"])
    assert _place(r.json(), "/etc")["error"] == 400


def test_index_is_fast_for_100_recipes(env):
    mod, c, ws, ext = env
    for i in range(100):
        _recipe(ws, f"r{i:03d}", "---\ntitle: t\ndescription: d\n---\n" + "x" * 2000)
    t0 = time.perf_counter()
    r = _post(c, "/recipes/index", places=["/workspace"])
    took = time.perf_counter() - t0
    assert len(_place(r.json(), "/workspace")["recipes"]) == 100
    assert took < 1.0, took


def test_folder_reads_all_files_and_skips_links(env):
    mod, c, ws, ext = env
    d = _recipe(ws, "抽出")
    (d / "q.sql").write_text("select 1")
    (d / "sub").mkdir()
    (d / "sub" / "RECIPE.md").write_text("nested")
    (ws / "outside.txt").write_text("secret")
    os.symlink(ws / "outside.txt", d / "link.txt")
    os.symlink(ws, d / "dirlink")
    r = _post(c, "/recipes/folder", path="recipes/抽出")
    assert r.status_code == 200, r.text
    res = r.json()
    files = {f["rel"]: base64.b64decode(f["b64"]).decode() for f in res["files"]}
    assert files == {
        "RECIPE.md": "---\ntitle: t\n---\nbody\n",
        "q.sql": "select 1",
        "sub/RECIPE.md": "nested",
    }
    skipped = {x["rel"]: x["why"] for x in res["skipped"]}
    assert skipped == {"link.txt": "symlink", "dirlink": "symlink"}
    assert res["dirs"] == ["sub"]
    # ext も同じ。bind に無いものは 404
    _recipe(ext / "groups" / "EMR", "共有")
    r = _post(
        c, "/recipes/folder", path="/ext/groups/EMR/recipes/共有", mounts=MEMBER_MOUNTS
    )
    assert [f["rel"] for f in r.json()["files"]] == ["RECIPE.md"]
    r = _post(
        c,
        "/recipes/folder",
        path="/ext/groups/OTHER/recipes/secret",
        mounts=MEMBER_MOUNTS,
    )
    assert r.status_code == 404


def test_folder_missing_and_not_dir_and_limit(env):
    mod, c, ws, ext = env
    r = _post(c, "/recipes/folder", path="recipes/無い")
    assert r.json()["exists"] is False
    (ws / "recipes").mkdir()
    (ws / "recipes" / "f.md").write_text("x")
    r = _post(c, "/recipes/folder", path="recipes/f.md")
    assert r.json()["is_dir"] is False and r.json()["type"] == "file"
    d = _recipe(ws, "大きい")
    (d / "big.bin").write_bytes(b"x" * 2000)
    r = _post(c, "/recipes/folder", path="recipes/大きい", limit_bytes=1000)
    assert r.status_code == 413
    # 経路の symlink は辿らない
    (ws / "real").mkdir()
    _recipe(ws / "real", "x")
    os.symlink(ws / "real" / "recipes", ws / "linked")
    r = _post(c, "/recipes/folder", path="linked/x")
    assert r.status_code in (400, 404)


def test_trash_with_name(env):
    mod, c, ws, ext = env
    d = _recipe(ws, "集計")
    r = _post(c, "/files/trash", path="recipes/集計/RECIPE.md", name="集計.RECIPE.md")
    assert r.status_code == 200, r.text
    assert r.json()["path"] == "trash/集計.RECIPE.md"
    assert (ws / "trash" / "集計.RECIPE.md").read_text().startswith("---")
    assert not (d / "RECIPE.md").exists()
    (d / "RECIPE.md").write_text("v2")
    r = _post(c, "/files/trash", path="recipes/集計/RECIPE.md", name="集計.RECIPE.md")
    assert r.json()["path"] == "trash/集計.RECIPE-2.md"
    # ext でも同じ
    g = _recipe(ext / "groups" / "EMR", "抽出")
    r = _post(
        c,
        "/files/trash",
        path="/ext/groups/EMR/recipes/抽出/RECIPE.md",
        name="抽出.RECIPE.md",
        mounts=MEMBER_MOUNTS,
    )
    assert r.json()["path"] == "/ext/groups/EMR/trash/抽出.RECIPE.md"
    assert not (g / "RECIPE.md").exists()


@pytest.mark.parametrize("name", ["", "a/b", ".hidden", "x" * 256, "a\x00b"])
def test_trash_name_invalid(env, name):
    mod, c, ws, ext = env
    _recipe(ws, "集計")
    r = _post(c, "/files/trash", path="recipes/集計/RECIPE.md", name=name)
    assert r.status_code == 400
    assert (ws / "recipes" / "集計" / "RECIPE.md").exists()


def test_index_entries_and_stat_many(env):
    mod, c, ws, ext = env
    d = _recipe(ws, "前処理")
    (d / "prep.sh").write_text("echo hi")
    (ws / "notes.md").write_text("n")
    os.symlink(ws / "notes.md", d / "link.md")
    r = _post(c, "/recipes/index", places=["/workspace"])
    item = _place(r.json(), "/workspace")["recipes"][0]
    assert item["entries"] == {
        "RECIPE.md": "file",
        "prep.sh": "file",
        "link.md": "symlink",
    }
    _recipe(ext / "groups" / "EMR", "抽出")
    r = _post(
        c,
        "/files/stat_many",
        paths=[
            "/workspace/notes.md",
            "/workspace/無い.md",
            "/workspace/recipes/前処理",
            "/workspace/recipes/前処理/link.md",
            "/ext/groups/EMR/recipes/抽出/RECIPE.md",
            "/ext/groups/OTHER/recipes/secret/RECIPE.md",
            "/etc/passwd",
            "/workspace/../etc",
        ],
        mounts=MEMBER_MOUNTS,
    )
    assert r.status_code == 200, r.text
    assert r.json()["results"] == {
        "/workspace/notes.md": "file",
        "/workspace/無い.md": None,
        "/workspace/recipes/前処理": "dir",
        "/workspace/recipes/前処理/link.md": "symlink",
        "/ext/groups/EMR/recipes/抽出/RECIPE.md": "file",
        "/ext/groups/OTHER/recipes/secret/RECIPE.md": None,
        "/etc/passwd": None,
        "/workspace/../etc": None,
    }
