"""参照用データの read-only bind (`RO_MOUNT_ROOT` 直下) の回帰 (#138)。

何を固定しているかは各テスト関数のコメントを見ること。`test_session_manager.py` と同じく
main.py を直接 import する (コンテナ不要)。

実行:
    uv run task sandbox-unit
"""

import importlib.util
import pathlib
import sys

_MAIN = pathlib.Path(__file__).resolve().parents[1] / "app" / "main.py"


def _load_main(monkeypatch, tmp_path, mount_root):
    monkeypatch.setenv("SANDBOX_WORKSPACES", str(tmp_path / "workspaces"))
    spec = importlib.util.spec_from_file_location("sandbox_main_ro_mounts_test", _MAIN)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    # 置き場は env ではなく定数 (設定を増やさない)。テストは属性ごと差し替える
    monkeypatch.setattr(mod, "RO_MOUNT_ROOT", pathlib.Path(mount_root))
    return mod


def _cmd(mod) -> list[str]:
    return mod._jail_base(pathlib.Path("/workspaces/u"), 60)


def _ro_binds(mod, mount_root) -> list[str]:
    """`_jail_base` が組む引数から、置き場由来の read-only bind だけを順序どおり拾う。"""
    cmd = _cmd(mod)
    return [
        cmd[i + 1]
        for i, a in enumerate(cmd[:-1])
        if a == "--bindmount_ro" and cmd[i + 1].startswith(str(mount_root))
    ]


def _without_root_binds(cmd, mount_root) -> list[str]:
    """置き場由来の `--bindmount_ro <p>` の対だけを取り除いた引数列。"""
    out, i = [], 0
    while i < len(cmd):
        if (
            cmd[i] == "--bindmount_ro"
            and i + 1 < len(cmd)
            and cmd[i + 1].startswith(str(mount_root))
        ):
            i += 2
            continue
        out.append(cmd[i])
        i += 1
    return out


def _all_binds(mod) -> list[tuple[str, str]]:
    cmd = mod._jail_base(pathlib.Path("/workspaces/u"), 60)
    return [
        (a, cmd[i + 1])
        for i, a in enumerate(cmd[:-1])
        if a in ("--bindmount", "--bindmount_ro")
    ]


# ---------------------------------------------------------------------------
# 置き場が空 = 従来どおり
# ---------------------------------------------------------------------------
def test_missing_mount_root_changes_nothing(monkeypatch, tmp_path):
    # 置き場そのものが無い配備 (= listdir が失敗する) でも jail は普通に組める
    absent = tmp_path / "no-such-dir"
    mod = _load_main(monkeypatch, tmp_path, absent)
    assert mod._scan_ro_mount_root() == ([], [])
    assert _ro_binds(mod, absent) == []


def test_empty_and_missing_mount_root_give_identical_argv(monkeypatch, tmp_path):
    # 「置き場が空なら挙動不変」= 不在のときと **argv が完全一致**すること。
    # 置き場由来の引数を prefix で数えるだけだと、`/mnt` を無条件に足す退行を見逃す
    root = tmp_path / "mnt"
    root.mkdir()
    mod = _load_main(monkeypatch, tmp_path, root)
    empty = _cmd(mod)
    monkeypatch.setattr(mod, "RO_MOUNT_ROOT", tmp_path / "no-such-dir")
    assert empty == _cmd(mod)
    assert not any(str(root) in a for a in empty)


def test_entries_only_add_ro_bind_pairs(monkeypatch, tmp_path):
    # 置いたことで増えるのは `--bindmount_ro <path>` の対だけ (他の引数を動かさない)
    root = tmp_path / "mnt"
    (root / "doc").mkdir(parents=True)
    mod = _load_main(monkeypatch, tmp_path, root)
    with_entry = _cmd(mod)
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(mod, "RO_MOUNT_ROOT", empty)
    assert _without_root_binds(with_entry, root) == _cmd(mod)


# ---------------------------------------------------------------------------
# 置いたものがそのまま出る
# ---------------------------------------------------------------------------
def test_entries_are_bound_at_the_same_path(monkeypatch, tmp_path):
    root = tmp_path / "mnt"
    (root / "doc").mkdir(parents=True)
    (root / "static").mkdir()
    mod = _load_main(monkeypatch, tmp_path, root)
    # guest 側 = コンテナ側と同じパス。名前順で並ぶ (複数あっても衝突しない)
    assert _ro_binds(mod, root) == [f"{root}/doc", f"{root}/static"]


def test_single_file_entry_is_bound(monkeypatch, tmp_path):
    # 置くのはディレクトリとは限らない (ファイル 1 本の volume も同じ扱い)
    root = tmp_path / "mnt"
    root.mkdir()
    (root / "corpus.sqlite").write_text("x")
    mod = _load_main(monkeypatch, tmp_path, root)
    assert _ro_binds(mod, root) == [f"{root}/corpus.sqlite"]


def test_entries_are_read_only_never_rw(monkeypatch, tmp_path):
    # **契約**: この口は read-only 専用。rw (`--bindmount`) で足す経路を作らない
    root = tmp_path / "mnt"
    (root / "doc").mkdir(parents=True)
    mod = _load_main(monkeypatch, tmp_path, root)
    rw = [p for flag, p in _all_binds(mod) if flag == "--bindmount"]
    assert not any(p.startswith(str(root)) for p in rw), rw


def test_only_one_level_deep(monkeypatch, tmp_path):
    # 孫は個別に足さない (「volume 1 本 = 1 エントリ」)。中身は bind した親の下に見える
    root = tmp_path / "mnt"
    (root / "doc" / "guideline").mkdir(parents=True)
    mod = _load_main(monkeypatch, tmp_path, root)
    assert _ro_binds(mod, root) == [f"{root}/doc"]


# ---------------------------------------------------------------------------
# 飛ばすもの
# ---------------------------------------------------------------------------
def test_dotfiles_are_skipped(monkeypatch, tmp_path):
    root = tmp_path / "mnt"
    (root / "doc").mkdir(parents=True)
    (root / ".DS_Store").write_text("junk")
    mod = _load_main(monkeypatch, tmp_path, root)
    assert _ro_binds(mod, root) == [f"{root}/doc"]


def test_name_with_colon_is_skipped(monkeypatch, tmp_path):
    # ":" は nsjail の `src:dst` 区切り。そのまま渡すと dst を相対パスと解釈して
    # **jail の起動ごと失敗する** = 全ユーザーの全実行が落ちる。名前は配備側が自由に
    # 付けられる (= 外部入力) ので、壊れた symlink と同じく飛ばす
    root = tmp_path / "mnt"
    (root / "a:b").mkdir(parents=True)
    (root / "doc").mkdir()
    mod = _load_main(monkeypatch, tmp_path, root)
    binds, skipped = mod._scan_ro_mount_root()
    assert binds == [f"{root}/doc"]
    assert skipped == [f"{root}/a:b"]


def test_broken_symlink_is_skipped_with_warning(monkeypatch, tmp_path, capsys):
    # 辿れないパスを nsjail に渡すと jail の起動ごと失敗する = 全ユーザーの全実行が落ちる。
    # 飛ばしたうえで、起動ログに名指しで警告を出す
    root = tmp_path / "mnt"
    (root / "doc").mkdir(parents=True)
    (root / "dangling").symlink_to(tmp_path / "gone")
    mod = _load_main(monkeypatch, tmp_path, root)

    binds, skipped = mod._scan_ro_mount_root()
    assert binds == [f"{root}/doc"]
    assert skipped == [f"{root}/dangling"]
    assert _ro_binds(mod, root) == [f"{root}/doc"]

    mod._log_ro_mounts()
    log = capsys.readouterr().out
    assert f"{root}/doc" in log
    assert "WARNING" in log and f"{root}/dangling" in log
