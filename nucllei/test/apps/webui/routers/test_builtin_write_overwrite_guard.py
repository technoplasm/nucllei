"""write_file の上書きの条件: 既にあるファイルを上書きできるのは、同じ会話でそのパスを読んだか書いた後で、
その版 (sandbox の mtime + 大きさ) から変わっていないときだけ。全部の置き場に同じ規則。

sandbox は**本物のアプリ** (`sandbox/app/main.py`) を TestClient で動かし、tool の HTTP をそこへ流す
(排他の作成・条件付きの上書き・symlink を辿らない経路まで通す)。記録は chat_id があれば DB
(`chat_file_state`)、無ければ (headless) その 1 リクエストの `__metadata__`。
"""

import asyncio
import importlib.util
import os
import pathlib
import sys
import uuid

import pytest
from fastapi.testclient import TestClient

from test.util.tool_errors import call_text

_SANDBOX_MAIN = (
    pathlib.Path(__file__).resolve().parents[5] / "sandbox" / "app" / "main.py"
)

USER = {"id": "u1", "role": "user", "name": "山田"}
OTHER = {"id": "u2", "role": "user", "name": "佐藤"}
ADMIN = {"id": "a1", "role": "admin", "name": "管理"}
MEMBERS = {"u1": ["EMR"], "u2": ["EMR"], "a1": []}

E5_1 = (
    "ERROR: {p} は既にあり、この会話ではまだ読んでいないので上書きしない。次の手: 一部を直す・足すなら"
    " edit_file を使う (末尾に足すなら最後の行を old_text に含める)。丸ごと書き直すなら、先に read_file"
    " で今の中身を読んでから write_file を呼び直す。run_shell や run_python で書き直さない。"
)
E5_1_BINARY = (
    "ERROR: {p} は既にあり、この会話ではまだ読んでいないので上書きしない。次の手: 置き換えてよいか"
    "ユーザーに確かめてから、remove_file でゴミ箱へ移して write_file で作り直す。"
)
E5_2 = (
    "ERROR: {p} はこの会話で読んだ・書いた後に変わっている (別の会話・画面のファイルブラウザ・"
    "run_shell / run_python・同じ置き場のほかの人など)。上書きしていない。次の手: read_file で"
    "今の中身を読み直し、変わった分を踏まえて書く (一部だけなら edit_file)。"
)
E5_3 = (
    "ERROR: {p} は edit_file が読んでから書くまでの間に変わった。何も書いていない。"
    "次の手: 同じ edit_file を 1 回だけ呼び直す。"
)
E5_4 = (
    "ERROR: sandbox が古く、上書きしてよいかを確かめられない (sandbox の更新が要る)。次の手: 一部を直すなら"
    " edit_file を使う。丸ごと書き直す必要があるなら、そうユーザーに伝える。"
)


@pytest.fixture
def sbx(monkeypatch, tmp_path):
    """本物の sandbox を tmp に立て、nucllei の sandbox_post をそこへ繋ぐ。"""
    monkeypatch.setenv("SANDBOX_WORKSPACES", str(tmp_path / "workspaces"))
    monkeypatch.delenv("SANDBOX_TOKEN", raising=False)
    spec = importlib.util.spec_from_file_location(
        "sandbox_main_for_overwrite_guard", _SANDBOX_MAIN
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
    # 応答から落とすキー (古い sandbox の真似)
    drop: set = set()

    async def fake_post(path, payload, timeout_s):
        calls.append((path, dict(payload)))
        r = client.post(path, json=payload)
        if r.status_code != 200:
            raise sb.SandboxHTTPError(r.status_code, r.text)
        body = r.json()
        return {k: v for k, v in body.items() if k not in drop}

    monkeypatch.setattr(sb, "sandbox_post", fake_post)
    monkeypatch.setattr(nucllei.env, "SANDBOX_URL", "http://sandbox.test")
    monkeypatch.setattr(
        ext_acl,
        "member_group_access",
        lambda uid: {g: "rw" for g in MEMBERS.get(uid, [])},
    )
    monkeypatch.setattr(ext_acl, "all_group_names", lambda: ["EMR", "OTHER"])
    monkeypatch.setattr(task, "user_group_names", lambda uid: MEMBERS.get(uid, []))
    ws = ws_root / "u1"
    ws.mkdir(parents=True)
    return {"ws": ws, "ext": ext, "calls": calls, "drop": drop}


def _sb():
    from nucllei.builtin_tools.sandbox import Tools

    return Tools()


def run(coro):
    return asyncio.run(call_text(coro))


def _chat() -> dict:
    """1 リクエストの `__metadata__` (同じ chat の別のリクエストは同じ chat_id の別の dict)。"""
    return {"chat_id": f"test-{uuid.uuid4()}"}


def _writes(calls) -> list[dict]:
    return [p for path, p in calls if path == "/files/write"]


def _mark(p: pathlib.Path, ns: int) -> None:
    """mtime を決まった値にする (同じ刻みの中の書き込みに頼らず、変わったことをはっきりさせる)。"""
    os.utime(p, ns=(ns, ns))


# ---------------------------------------------------------------------------
# 記録が無い: 新しく作るだけ
# ---------------------------------------------------------------------------
def test_existing_file_not_read_is_not_overwritten(sbx):
    (sbx["ws"] / "memory").mkdir()
    (sbx["ws"] / "memory" / "MEMORY.md").write_text("前の中身")
    meta = _chat()
    out = run(_sb().write_file("/workspace/memory/MEMORY.md", "x", USER, meta))
    assert out == E5_1.format(p="/workspace/memory/MEMORY.md")
    # 排他の作成で送った (stat してから書く間に作られても上書きしない)
    assert [w.get("exclusive") for w in _writes(sbx["calls"])] == [True]
    assert (sbx["ws"] / "memory" / "MEMORY.md").read_text() == "前の中身"


def test_new_file_is_created_and_then_overwritable(sbx):
    meta = _chat()
    tool = _sb()
    out = run(tool.write_file("/workspace/out/report.md", "abc", USER, meta))
    assert out == "/workspace/out/report.md を新しく作った (UTF-8、3B)。"
    out = run(tool.write_file("/workspace/out/report.md", "abcd", USER, meta))
    assert out == "/workspace/out/report.md を上書きした (UTF-8、4B)。"
    first, second = _writes(sbx["calls"])
    assert first.get("exclusive") is True
    assert "exclusive" not in second
    assert second["expected_size"] == 3 and isinstance(second["expected_mtime"], float)
    assert (sbx["ws"] / "out" / "report.md").read_text() == "abcd"


def test_binary_extension_has_its_own_next_step(sbx):
    (sbx["ws"] / "figure.png").write_bytes(b"\x89PNG")
    out = run(_sb().write_file("/workspace/figure.png", "x", USER, _chat()))
    assert out == E5_1_BINARY.format(p="/workspace/figure.png")


def test_recipe_md_is_refused_before_the_overwrite_check(sbx):
    out = run(_sb().write_file("/workspace/recipes/r/RECIPE.md", "x", USER, _chat()))
    assert out.startswith(
        "ERROR: /workspace/recipes/r/RECIPE.md はレシピの本体 (RECIPE.md)"
    )
    assert sbx["calls"] == []


# ---------------------------------------------------------------------------
# 読んだ・書いた後は上書きできる (変わっていなければ)
# ---------------------------------------------------------------------------
def test_read_file_then_overwrite(sbx):
    (sbx["ws"] / "a.md").write_text("12345")
    meta = _chat()
    tool = _sb()
    run(tool.read_file("/workspace/a.md", __user__=USER, __metadata__=meta))
    seen = (sbx["ws"] / "a.md").stat().st_mtime_ns / 1e9
    out = run(tool.write_file("/workspace/a.md", "new", USER, meta))
    assert out == "/workspace/a.md を上書きした (UTF-8、3B)。"
    (w,) = _writes(sbx["calls"])
    assert w["expected_size"] == 5 and w["expected_mtime"] == seen


def test_reading_any_page_counts(sbx):
    (sbx["ws"] / "big.md").write_text("行\n" * 200)
    meta = _chat()
    tool = _sb()
    out = run(
        tool.read_file(
            "/workspace/big.md",
            offset=0,
            max_chars=10,
            __user__=USER,
            __metadata__=meta,
        )
    )
    assert "next_offset" in getattr(out, "text", out)
    assert "上書きした" in run(tool.write_file("/workspace/big.md", "x", USER, meta))


def test_edit_file_then_write_file(sbx):
    (sbx["ws"] / "a.md").write_text("one\ntwo\n")
    meta = _chat()
    tool = _sb()
    out = run(
        tool.edit_file("/workspace/a.md", "two", "2", __user__=USER, __metadata__=meta)
    )
    assert "を置き換えた" in out
    assert "上書きした" in run(tool.write_file("/workspace/a.md", "x", USER, meta))


def test_edit_file_does_not_launder_an_outside_change(sbx):
    """読んだ → 外で追記 → edit_file で 1 か所直す → write_file で丸ごと: 外の追記を知らないまま
    消させない (edit_file は古い記録を進めない)。edit_file そのものは通る。"""
    p = sbx["ws"] / "a.md"
    p.write_text("one\ntwo\n")
    _mark(p, 1_000_000_000_000_000_000)
    meta = _chat()
    tool = _sb()
    run(tool.read_file("/workspace/a.md", __user__=USER, __metadata__=meta))
    with p.open("a") as f:
        f.write("ほかの人の追記\n")
    out = run(
        tool.edit_file("/workspace/a.md", "two", "2", __user__=USER, __metadata__=meta)
    )
    assert "を置き換えた" in out
    out = run(tool.write_file("/workspace/a.md", "丸ごと", USER, meta))
    assert out == E5_2.format(p="/workspace/a.md")
    assert "ほかの人の追記" in p.read_text()
    # 読み直せば書ける
    run(tool.read_file("/workspace/a.md", __user__=USER, __metadata__=meta))
    assert "上書きした" in run(tool.write_file("/workspace/a.md", "丸ごと", USER, meta))


def test_edit_file_after_its_own_known_version_moves_the_record(sbx):
    """記録が edit_file の読んだ版と同じなら記録を進める (続けて write_file で上書きできる)。"""
    p = sbx["ws"] / "a.md"
    p.write_text("one\ntwo\n")
    meta = _chat()
    tool = _sb()
    run(tool.read_file("/workspace/a.md", __user__=USER, __metadata__=meta))
    run(tool.edit_file("/workspace/a.md", "two", "2", __user__=USER, __metadata__=meta))
    run(tool.edit_file("/workspace/a.md", "one", "1", __user__=USER, __metadata__=meta))
    assert "上書きした" in run(tool.write_file("/workspace/a.md", "x", USER, meta))


def test_text_extension_that_read_file_cannot_decode_gets_the_binary_next_step(sbx):
    """拡張子はテキストでも中身が UTF-8 / cp932 でない物 (EUC-JP 等) は read_file で読めない —
    読んでから書く道ではなく、ユーザーに確かめてゴミ箱へ移して作り直す道を言う。"""
    p = sbx["ws"] / "euc.txt"
    p.write_bytes("日本語のテキスト".encode("euc_jp"))
    meta = _chat()
    tool = _sb()
    out = run(tool.read_file("/workspace/euc.txt", __user__=USER, __metadata__=meta))
    assert out.startswith("ERROR: /workspace/euc.txt はテキストとして読めない")
    out = run(tool.write_file("/workspace/euc.txt", "x", USER, meta))
    assert out == E5_1_BINARY.format(p="/workspace/euc.txt")
    assert p.read_bytes() == "日本語のテキスト".encode("euc_jp")
    # 読めるテキストは今までどおり「読んでから」
    (sbx["ws"] / "ok.txt").write_text("読める")
    out = run(tool.write_file("/workspace/ok.txt", "x", USER, meta))
    assert out == E5_1.format(p="/workspace/ok.txt")


def test_dotdot_reaches_the_same_record(sbx):
    (sbx["ws"] / "b.md").write_text("b")
    meta = _chat()
    tool = _sb()
    run(tool.read_file("/workspace/x/../b.md", __user__=USER, __metadata__=meta))
    assert "上書きした" in run(tool.write_file("//workspace/./b.md", "y", USER, meta))


def test_changed_after_read_is_refused(sbx):
    p = sbx["ws"] / "a.md"
    p.write_text("v1")
    _mark(p, 1_000_000_000_000_000_000)
    meta = _chat()
    tool = _sb()
    run(tool.read_file("/workspace/a.md", __user__=USER, __metadata__=meta))
    # 別の会話・run_shell 等が書いた
    p.write_text("v2-other")
    out = run(tool.write_file("/workspace/a.md", "mine", USER, meta))
    assert out == E5_2.format(p="/workspace/a.md")
    assert p.read_text() == "v2-other"
    # 読み直せば書ける
    run(tool.read_file("/workspace/a.md", __user__=USER, __metadata__=meta))
    assert "上書きした" in run(tool.write_file("/workspace/a.md", "mine", USER, meta))


def test_same_mtime_but_different_size_is_refused(sbx):
    """mtime を戻されても (同じ刻みの別の書き込みも) 大きさが違えば拾う。"""
    p = sbx["ws"] / "a.md"
    p.write_text("v1")
    _mark(p, 1_000_000_000_000_000_000)
    meta = _chat()
    tool = _sb()
    run(tool.read_file("/workspace/a.md", __user__=USER, __metadata__=meta))
    p.write_text("v2-longer")
    _mark(p, 1_000_000_000_000_000_000)
    out = run(tool.write_file("/workspace/a.md", "mine", USER, meta))
    assert out == E5_2.format(p="/workspace/a.md")


def test_deleted_after_read_is_created_again(sbx):
    p = sbx["ws"] / "a.md"
    p.write_text("v1")
    meta = _chat()
    tool = _sb()
    run(tool.read_file("/workspace/a.md", __user__=USER, __metadata__=meta))
    p.unlink()
    out = run(tool.write_file("/workspace/a.md", "v2", USER, meta))
    assert out == (
        "/workspace/a.md を新しく作った (この会話で読んだ後に消されていた。UTF-8、2B)。"
    )
    conditional, exclusive = _writes(sbx["calls"])
    assert "expected_mtime" in conditional and exclusive.get("exclusive") is True
    # 新しい版を記録している — 続けて上書きできる
    assert "上書きした" in run(tool.write_file("/workspace/a.md", "v3", USER, meta))


def test_deleted_then_created_by_someone_else_is_refused(sbx, monkeypatch):
    """消されていたので作り直そうとしたら、その間に誰かが作っていた → 読み直し。"""
    import nucllei.builtin_tools.sandbox as sb

    p = sbx["ws"] / "a.md"
    p.write_text("v1")
    meta = _chat()
    tool = _sb()
    run(tool.read_file("/workspace/a.md", __user__=USER, __metadata__=meta))
    p.unlink()
    real = sb.sandbox_post

    async def racing(path, payload, timeout_s):
        if path == "/files/write" and payload.get("exclusive"):
            p.write_text("someone")
        return await real(path, payload, timeout_s)

    monkeypatch.setattr(sb, "sandbox_post", racing)
    out = run(tool.write_file("/workspace/a.md", "mine", USER, meta))
    assert out == E5_2.format(p="/workspace/a.md")
    assert p.read_text() == "someone"


# ---------------------------------------------------------------------------
# 置き場: /ext/groups・/ext/public も同じ
# ---------------------------------------------------------------------------
def test_group_and_public_places_follow_the_same_rule(sbx):
    notes = sbx["ext"] / "groups" / "EMR" / "notes"
    notes.mkdir()
    (notes / "a.md").write_text("group")
    meta = _chat()
    tool = _sb()
    out = run(tool.write_file("/ext/groups/EMR/notes/a.md", "x", USER, meta))
    assert out == E5_1.format(p="/ext/groups/EMR/notes/a.md")
    run(tool.read_file("/ext/groups/EMR/notes/a.md", __user__=USER, __metadata__=meta))
    assert "上書きした" in run(
        tool.write_file("/ext/groups/EMR/notes/a.md", "x", USER, meta)
    )
    # 同じグループのほかの人が書いた
    (notes / "a.md").write_text("other member wrote this")
    out = run(tool.write_file("/ext/groups/EMR/notes/a.md", "y", USER, meta))
    assert out == E5_2.format(p="/ext/groups/EMR/notes/a.md")

    (sbx["ext"] / "public" / "p.md").write_text("pub")
    meta = _chat()
    out = run(tool.write_file("/ext/public/p.md", "x", ADMIN, meta))
    assert out == E5_1.format(p="/ext/public/p.md")
    run(tool.read_file("/ext/public/p.md", __user__=ADMIN, __metadata__=meta))
    assert "上書きした" in run(tool.write_file("/ext/public/p.md", "x", ADMIN, meta))


def test_symlink_leaf_is_not_followed(sbx):
    """記録があっても末端の symlink は辿らない (sandbox が断る)。"""
    (sbx["ws"] / "real.md").write_text("r")
    (sbx["ws"] / "link.md").symlink_to(sbx["ws"] / "real.md")
    meta = _chat()
    tool = _sb()
    out = run(tool.read_file("/workspace/link.md", __user__=USER, __metadata__=meta))
    assert out.startswith("ERROR: /workspace/link.md は使えない path")
    out = run(tool.write_file("/workspace/link.md", "x", USER, meta))
    assert out.startswith("ERROR: /workspace/link.md は使えない path")
    assert (sbx["ws"] / "real.md").read_text() == "r"


# ---------------------------------------------------------------------------
# 記録の範囲: 会話 × 利用者 (DB)、headless はそのリクエストだけ
# ---------------------------------------------------------------------------
def test_record_survives_across_requests_of_the_same_chat(sbx):
    (sbx["ws"] / "a.md").write_text("a")
    chat_id = f"test-{uuid.uuid4()}"
    run(
        _sb().read_file(
            "/workspace/a.md", __user__=USER, __metadata__={"chat_id": chat_id}
        )
    )
    # 次のターン (別のリクエスト・別の tool インスタンス)
    out = run(_sb().write_file("/workspace/a.md", "x", USER, {"chat_id": chat_id}))
    assert out == "/workspace/a.md を上書きした (UTF-8、1B)。"

    from nucllei.models.chat_file_state import ChatFileStates

    stamp = ChatFileStates.get(chat_id, "u1", "/workspace/a.md")
    assert stamp.via == "write" and stamp.size == 1


def test_record_does_not_leak_to_other_chats_or_users(sbx):
    (sbx["ws"] / "a.md").write_text("a")
    chat_id = f"test-{uuid.uuid4()}"
    run(
        _sb().read_file(
            "/workspace/a.md", __user__=USER, __metadata__={"chat_id": chat_id}
        )
    )
    # 別の会話
    out = run(_sb().write_file("/workspace/a.md", "x", USER, _chat()))
    assert out == E5_1.format(p="/workspace/a.md")
    # 同じ chat_id でも別の利用者 (同じグループの置き場で効かない)
    notes = sbx["ext"] / "groups" / "EMR"
    (notes / "g.md").write_text("g")
    run(
        _sb().read_file(
            "/ext/groups/EMR/g.md", __user__=USER, __metadata__={"chat_id": chat_id}
        )
    )
    out = run(
        _sb().write_file("/ext/groups/EMR/g.md", "x", OTHER, {"chat_id": chat_id})
    )
    assert out == E5_1.format(p="/ext/groups/EMR/g.md")


def test_subagent_child_keeps_its_own_record(sbx):
    """子は子の chat の id で持つ — 親が読んだファイルも、子は読まずに上書きできない。"""
    (sbx["ws"] / "a.md").write_text("a")
    parent = f"test-{uuid.uuid4()}"
    run(
        _sb().read_file(
            "/workspace/a.md", __user__=USER, __metadata__={"chat_id": parent}
        )
    )
    child = {"chat_id": f"test-{uuid.uuid4()}", "agent_session": True}
    out = run(_sb().write_file("/workspace/a.md", "x", USER, child))
    assert out == E5_1.format(p="/workspace/a.md")
    # 子が読んで書いた → 親の記録は古いので、親は読み直しになる
    run(_sb().read_file("/workspace/a.md", __user__=USER, __metadata__=child))
    assert "上書きした" in run(
        _sb().write_file("/workspace/a.md", "child!", USER, child)
    )
    out = run(_sb().write_file("/workspace/a.md", "p", USER, {"chat_id": parent}))
    assert out == E5_2.format(p="/workspace/a.md")


def test_headless_keeps_the_record_only_within_the_request(sbx):
    (sbx["ws"] / "a.md").write_text("a")
    meta: dict = {"chat_id": None}
    tool = _sb()
    run(tool.read_file("/workspace/a.md", __user__=USER, __metadata__=meta))
    # 同じリクエストの tool ループの追撃 (同じ dict)
    assert "上書きした" in run(tool.write_file("/workspace/a.md", "x", USER, meta))
    # 次のリクエスト
    out = run(_sb().write_file("/workspace/a.md", "y", USER, {}))
    assert out == E5_1.format(p="/workspace/a.md")


# ---------------------------------------------------------------------------
# edit_file: 読んだ条件は付けない。自分の読み → 書きの間の変更だけを断る
# ---------------------------------------------------------------------------
def test_edit_file_needs_no_prior_read_and_sends_its_own_version(sbx):
    p = sbx["ws"] / "a.md"
    p.write_text("one\ntwo\n")
    out = run(
        _sb().edit_file(
            "/workspace/a.md", "two", "2", __user__=USER, __metadata__=_chat()
        )
    )
    assert "を置き換えた" in out
    (w,) = _writes(sbx["calls"])
    assert w["expected_size"] == 8 and isinstance(w["expected_mtime"], float)


def test_edit_file_refuses_a_change_between_its_read_and_write(sbx, monkeypatch):
    import nucllei.builtin_tools.sandbox as sb

    p = sbx["ws"] / "a.md"
    p.write_text("one\ntwo\n")
    real = sb.sandbox_post

    async def racing(path, payload, timeout_s):
        if path == "/files/write":
            p.write_text("one\ntwo\nthree\n")
        return await real(path, payload, timeout_s)

    monkeypatch.setattr(sb, "sandbox_post", racing)
    out = run(
        _sb().edit_file(
            "/workspace/a.md", "two", "2", __user__=USER, __metadata__=_chat()
        )
    )
    assert out == E5_3.format(p="/workspace/a.md")
    assert p.read_text() == "one\ntwo\nthree\n"


# ---------------------------------------------------------------------------
# 古い sandbox (版を返さない)
# ---------------------------------------------------------------------------
def test_sandbox_without_versions(sbx):
    (sbx["ws"] / "a.md").write_text("a")
    sbx["drop"].add("mtime")
    meta = _chat()
    tool = _sb()
    run(tool.read_file("/workspace/a.md", __user__=USER, __metadata__=meta))
    out = run(tool.write_file("/workspace/a.md", "x", USER, meta))
    assert out == E5_4
    assert (sbx["ws"] / "a.md").read_text() == "a"
    # 版の無い記録でも、無いファイルを新しく作るのは通す
    (sbx["ws"] / "a.md").unlink()
    out = run(tool.write_file("/workspace/a.md", "x", USER, meta))
    assert out == "/workspace/a.md を新しく作った (UTF-8、1B)。"


# ---------------------------------------------------------------------------
# send_to_workspace の取り込み先・レシピの tool も「読んだ・書いた」に数える
# ---------------------------------------------------------------------------
def test_imported_file_can_be_overwritten(sbx, monkeypatch):
    import nucllei.builtin_tools.sandbox as sb

    src = sbx["ws"].parent / "upload.csv"
    src.write_text("a,b\n")
    rec = type(
        "Rec", (), {"path": str(src), "user_id": "u1", "filename": "upload.csv"}
    )()
    monkeypatch.setattr(sb.Files, "get_file_by_id", lambda fid: rec)
    monkeypatch.setattr(sb.Storage, "get_file", lambda p: p)
    meta = _chat()
    tool = _sb()
    out = run(tool.send_to_workspace(file_id="f1", __user__=USER, __metadata__=meta))
    assert "/workspace/imports/upload.csv" in out
    assert "上書きした" in run(
        tool.write_file("/workspace/imports/upload.csv", "x,y\n", USER, meta)
    )


def _recipe(base: pathlib.Path, name: str, extra: str = "") -> pathlib.Path:
    import datetime as dt

    d = base / "recipes" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "RECIPE.md").write_text(
        f"---\ntitle: {name}\ndescription: 説明\nauthor: 山田\n"
        f"updated: {dt.date.today().isoformat()}\n{extra}---\n\n1. 手順\n",
        encoding="utf-8",
    )
    return d


def test_load_recipe_records_recipe_md_and_required_reads(sbx):
    from nucllei.builtin_tools.recipe_tools import RecipeTools

    (sbx["ws"] / "data").mkdir()
    (sbx["ws"] / "data" / "ref.md").write_text("資料")
    _recipe(sbx["ws"], "集計", 'requires:\n  - read: "/workspace/data/ref.md"\n')
    meta = _chat()
    out = run(
        RecipeTools().load_recipe(
            "/workspace/recipes/集計", __user__=USER, __metadata__=meta, __messages__=[]
        )
    )
    assert "資料" in out
    from nucllei.models.chat_file_state import ChatFileStates

    md = ChatFileStates.get(meta["chat_id"], "u1", "/workspace/recipes/集計/RECIPE.md")
    assert md is not None and md.via == "recipe" and md.mtime is not None
    ref = ChatFileStates.get(meta["chat_id"], "u1", "/workspace/data/ref.md")
    assert ref is not None and ref.via == "read" and ref.size == len("資料".encode())
    assert "上書きした" in run(
        _sb().write_file("/workspace/data/ref.md", "新しい資料", USER, meta)
    )


def test_save_and_edit_recipe_record_what_they_wrote(sbx):
    from nucllei.builtin_tools.recipe_tools import RecipeTools
    from nucllei.models.chat_file_state import ChatFileStates

    meta = _chat()
    run(
        RecipeTools().save_recipe(
            "/workspace/recipes/新",
            title="新",
            description="説明",
            body="1. 手順\n",
            __user__=USER,
            __metadata__=meta,
        )
    )
    md = "/workspace/recipes/新/RECIPE.md"
    first = ChatFileStates.get(meta["chat_id"], "u1", md)
    assert first is not None and first.via == "recipe"
    assert first.size == (sbx["ws"] / "recipes" / "新" / "RECIPE.md").stat().st_size
    run(
        RecipeTools().edit_recipe(
            "/workspace/recipes/新",
            "1. 手順",
            "1. 直した手順",
            __user__=USER,
            __metadata__=meta,
        )
    )
    second = ChatFileStates.get(meta["chat_id"], "u1", md)
    assert second.size == (sbx["ws"] / "recipes" / "新" / "RECIPE.md").stat().st_size
    assert second.size != first.size


def test_save_recipe_from_path_records_copied_attachments(sbx):
    from nucllei.builtin_tools.recipe_tools import RecipeTools

    src = _recipe(sbx["ws"], "元")
    (src / "q.sql").write_text("select 1")
    meta = _chat()
    run(
        RecipeTools().save_recipe(
            "/workspace/recipes/写し",
            from_path="/workspace/recipes/元",
            __user__=USER,
            __metadata__=meta,
        )
    )
    assert "上書きした" in run(
        _sb().write_file("/workspace/recipes/写し/q.sql", "select 2", USER, meta)
    )
