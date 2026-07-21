"""view_image の所有者チェック回帰(IDOR 封鎖)。

file_id 直指定で他ユーザーのファイルを覗けないこと(#40 と同クラスの authz)。
owner チェックは content_type / Storage 読取より前に効き、他人のファイルでは
画素(base64 data URL)を一切返さないことを固定する。
"""

import asyncio
import types

from nucllei.tools import view_image as vi


def _fake_rec(user_id, content_type="image/png"):
    return types.SimpleNamespace(
        user_id=user_id,
        meta={"content_type": content_type},
        path="dummy/path",
        filename="x.png",
    )


def _call(file_id, user):
    return asyncio.run(vi.Tools().view_image(file_id, __user__=user))


def test_other_users_file_is_rejected(monkeypatch):
    """他人所有の file_id は拒否され、base64 も data URL も返さない。"""
    monkeypatch.setattr(vi.Files, "get_file_by_id", lambda fid: _fake_rec("owner-a"))
    out = _call("f1", {"id": "attacker-b"})
    assert "アクセス権が無い" in out
    assert "base64" not in out
    assert not out.startswith("data:")


def test_missing_user_context_is_rejected(monkeypatch):
    """__user__ 未注入でも、所有者付きファイルは覗かせない(null 安全)。"""
    monkeypatch.setattr(vi.Files, "get_file_by_id", lambda fid: _fake_rec("owner-a"))
    out = _call("f1", None)
    assert "アクセス権が無い" in out


def test_owner_passes_owner_gate(monkeypatch):
    """所有者一致なら owner ゲートを通過し、次の判定(非画像)へ進む。"""
    monkeypatch.setattr(
        vi.Files,
        "get_file_by_id",
        lambda fid: _fake_rec("owner-a", content_type="text/plain"),
    )
    out = _call("f1", {"id": "owner-a"})
    assert "アクセス権が無い" not in out
    assert "画像ではない" in out
