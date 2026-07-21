"""builtin view_image の認可 + 画像提示チャネル(#89)の回帰。

- 所有者チェック(#40 と同クラスの IDOR 封鎖): file_id 直指定で他ユーザーの
  ファイルを覗けない。owner チェックは push/実体化より前に効く。
- 成功時は base64 を返さず、__feed_image__(汎用 tool-push)で file_id を push し
  非空の status 文字列を返す(戻り境界に base64 を載せない不変条件)。
"""

import asyncio
import types

from nucllei.builtin_tools import view_image as vi


def _fake_rec(user_id, content_type="image/png"):
    return types.SimpleNamespace(
        user_id=user_id,
        meta={"content_type": content_type},
        path="dummy/path",
        filename="plot.png",
    )


def _call(file_id, user, feed_image=None):
    return asyncio.run(
        vi.Tools().view_image(file_id, __user__=user, __feed_image__=feed_image)
    )


def test_other_users_file_is_rejected(monkeypatch):
    """他人所有の file_id は拒否され、push も base64 も起きない。"""
    monkeypatch.setattr(vi.Files, "get_file_by_id", lambda fid: _fake_rec("owner-a"))
    pushed = []
    out = _call("f1", {"id": "attacker-b"}, feed_image=pushed.append)
    assert "アクセス権が無い" in out
    assert pushed == []
    assert "base64" not in out and not out.startswith("data:")


def test_missing_user_context_is_rejected(monkeypatch):
    """__user__ 未注入でも、所有者付きファイルは覗かせない(null 安全)。"""
    monkeypatch.setattr(vi.Files, "get_file_by_id", lambda fid: _fake_rec("owner-a"))
    out = _call("f1", None)
    assert "アクセス権が無い" in out


def test_non_image_is_rejected(monkeypatch):
    """所有者一致でも非画像は提示しない。"""
    monkeypatch.setattr(
        vi.Files,
        "get_file_by_id",
        lambda fid: _fake_rec("owner-a", content_type="text/plain"),
    )
    pushed = []
    out = _call("f1", {"id": "owner-a"}, feed_image=pushed.append)
    assert "画像ではない" in out
    assert pushed == []


def test_success_pushes_file_id_and_returns_status(monkeypatch):
    """成功時: __feed_image__ に file_id を push し、base64 を含まない status を返す。"""
    monkeypatch.setattr(vi.Files, "get_file_by_id", lambda fid: _fake_rec("owner-a"))
    monkeypatch.setattr(vi, "_read_raw", lambda rec: b"\x89PNG small")
    pushed = []
    out = _call("abc", {"id": "owner-a"}, feed_image=pushed.append)
    assert pushed == ["abc"]
    assert "abc" in out
    assert "base64" not in out and not out.startswith("data:")
    assert isinstance(out, str)
