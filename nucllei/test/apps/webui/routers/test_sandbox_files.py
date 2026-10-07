"""sandbox のファイルブラウザ(#150)`/api/v1/sandbox/files/*` の回帰。CORE: group 権限・認証。

sandbox サービスは偽物(`builtin_tools.sandbox.sandbox_post` を差し替え)。固定すること:

- 開ける場所は本人の `/workspace` と、ACL(`utils/ext_acl`)で見える `/ext/public`・
  `/ext/groups/<g>` だけ。見えないグループ・`/ext/doc` 等は sandbox を呼ばずに 404
- ro の場所への書き換えは sandbox を呼ばずに 403(sandbox にも ro の bind として渡る)
- `.` 始まり・traversal・相対パスは 400
- sandbox への payload(本人の user_id・ACL の mounts・workspace は相対パス・nofollow)
- 平文の判定(UTF-8・NUL 無し・上限内)、上書き競合 409 の中継、書き込み上限 413、
  容量切れ 507 の中継、ダウンロードは常に添付
- SANDBOX_URL の無い配備では全部 404
- sandbox tool の容量切れの文面に「ファイルブラウザで整理できる」
"""

import asyncio
import base64
import json

import pytest

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user
from test.util.tool_errors import call_text

MEMBER = {"id": "member1", "role": "user"}
OUTSIDER = {"id": "outsider1", "role": "user"}
ADMIN = {"id": "admin1", "role": "admin"}


def _make_groups():
    from nucllei.models.groups import GroupForm, GroupUpdateForm, Groups

    for name, ids in (("EMR", ["member1"]), ("OTHER", ["other1"])):
        g = Groups.insert_new_group("admin1", GroupForm(name=name, description=""))
        Groups.update_group_by_id(
            g.id, GroupUpdateForm(name=name, description="", user_ids=ids)
        )


class FakeSandbox:
    """`sandbox_post` の偽物。endpoint → 応答(dict か、例外を返す callable)。"""

    def __init__(self, monkeypatch, responses=None, url="http://sandbox.test"):
        from nucllei.builtin_tools import sandbox

        self.calls = []
        self.responses = responses or {}
        monkeypatch.setattr(sandbox, "SANDBOX_URL", url)

        async def fake_post(path, payload, timeout_s):
            self.calls.append((path, payload))
            res = self.responses.get(path, {})
            if callable(res):
                return res(payload)
            return res

        monkeypatch.setattr(sandbox, "sandbox_post", fake_post)

    def error(self, status, detail):
        from nucllei.builtin_tools.sandbox import SandboxHTTPError

        def raise_(_payload):
            raise SandboxHTTPError(status, json.dumps({"detail": detail}))

        return raise_


class TestSandboxFiles(AbstractPostgresTest):
    BASE_PATH = "/api/v1/sandbox/files"

    def setup_method(self):
        super().setup_method()
        _make_groups()

    def _post(self, user, path, body):
        with mock_webui_user(**user):
            return self.fast_api_client.post(self.create_url(path), json=body)

    def _get(self, user, ep, **params):
        with mock_webui_user(**user):
            return self.fast_api_client.get(self.create_url(ep), params=params)

    # -----------------------------------------------------------------
    def test_disabled_without_sandbox_url(self, monkeypatch):
        from nucllei.routers import sandbox_files

        fake = FakeSandbox(monkeypatch, url="")
        assert sandbox_files.enabled() is False
        assert self._get(MEMBER, "/places").status_code == 404
        r = self._post(MEMBER, "/list", {"path": "/workspace"})
        assert r.status_code == 404 and fake.calls == []

    def test_places_follow_acl(self, monkeypatch):
        FakeSandbox(monkeypatch)
        r = self._get(MEMBER, "/places")
        assert r.status_code == 200, r.text
        assert r.json()["places"] == [
            {"path": "/workspace", "kind": "workspace", "rw": True},
            {"path": "/ext/public", "kind": "public", "rw": False},
            {"path": "/ext/groups/EMR", "kind": "group", "rw": True},
        ]
        paths = [p["path"] for p in self._get(OUTSIDER, "/places").json()["places"]]
        assert paths == ["/workspace", "/ext/public"]
        places = self._get(ADMIN, "/places").json()["places"]
        assert {"path": "/ext/public", "kind": "public", "rw": True} in places
        assert {"path": "/ext/groups/OTHER", "kind": "group", "rw": True} in places

    def test_list_workspace(self, monkeypatch):
        fake = FakeSandbox(
            monkeypatch,
            {
                "/files/ls": {
                    "entries": [
                        {
                            "name": "data",
                            "path": "data",
                            "type": "dir",
                            "size": 0,
                            "mtime": 1.0,
                        },
                        {
                            "name": "a.md",
                            "path": "a.md",
                            "type": "file",
                            "size": 3,
                            "mtime": 2.5,
                        },
                    ],
                    "exists": True,
                    "is_dir": True,
                    "truncated": False,
                    "cache_ttl_days": 30,
                }
            },
        )
        r = self._post(MEMBER, "/list", {"path": "/workspace/"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["path"] == "/workspace" and body["rw"] is True
        assert [e["path"] for e in body["entries"]] == [
            "/workspace/data",
            "/workspace/a.md",
        ]
        assert body["cache_ttl_days"] == 30
        path, payload = fake.calls[0]
        assert path == "/files/ls" and payload["path"] == "."
        assert payload["user_id"] == "member1"
        assert payload["mounts"] == [
            {"path": "/ext/public", "rw": False},
            {"path": "/ext/groups/EMR", "rw": True},
        ]
        self._post(MEMBER, "/list", {"path": "/workspace/data"})
        assert fake.calls[1][1]["path"] == "data"

    def test_list_ext_keeps_absolute_paths(self, monkeypatch):
        fake = FakeSandbox(
            monkeypatch,
            {
                "/files/ls": {
                    "entries": [
                        {
                            "name": "r.md",
                            "path": "/ext/groups/EMR/r.md",
                            "type": "file",
                            "size": 1,
                            "mtime": 1.0,
                        }
                    ],
                    "exists": True,
                    "is_dir": True,
                }
            },
        )
        body = self._post(MEMBER, "/list", {"path": "/ext/groups/EMR"}).json()
        assert body["rw"] is True
        assert body["entries"][0]["path"] == "/ext/groups/EMR/r.md"
        assert fake.calls[0][1]["path"] == "/ext/groups/EMR"
        body = self._post(MEMBER, "/list", {"path": "/ext/public"}).json()
        assert body["rw"] is False

    def test_invisible_and_invalid_paths(self, monkeypatch):
        fake = FakeSandbox(monkeypatch)
        for user, path, want in [
            (MEMBER, "/ext/groups/OTHER", 404),
            (MEMBER, "/ext/groups/OTHER/secret.md", 404),
            (OUTSIDER, "/ext/groups/EMR", 404),
            (MEMBER, "/ext/groups", 404),
            (MEMBER, "/ext/doc", 404),
            (MEMBER, "/ext", 404),
            (MEMBER, "/etc/passwd", 404),
            (MEMBER, "/workspace/../x", 400),
            (MEMBER, "/workspace/.exec", 400),
            (MEMBER, "/ext/groups/EMR/../OTHER", 400),
            (MEMBER, "workspace/a", 400),
        ]:
            r = self._post(user, "/list", {"path": path})
            assert r.status_code == want, (user, path, r.status_code)
        assert fake.calls == []

    def test_readonly_places_reject_writes(self, monkeypatch):
        fake = FakeSandbox(
            monkeypatch,
            {"/files/write": {"ok": True, "mtime": 3.0}, "/files/delete": {"ok": True}},
        )
        for ep, body in [
            ("/write", {"path": "/ext/public/x.md", "content": "x"}),
            ("/delete", {"path": "/ext/public/x.md"}),
            ("/mkdir", {"path": "/ext/public/d"}),
            ("/rename", {"path": "/ext/public/a", "dest": "/ext/public/b"}),
            ("/rename", {"path": "/workspace/a", "dest": "/ext/public/b"}),
        ]:
            r = self._post(MEMBER, ep, body)
            assert r.status_code == 403, (ep, r.text)
        assert fake.calls == []
        # admin は public に書ける(sandbox にも rw の bind で渡る)
        r = self._post(ADMIN, "/write", {"path": "/ext/public/x.md", "content": "x"})
        assert r.status_code == 200, r.text
        assert {"path": "/ext/public", "rw": True} in fake.calls[0][1]["mounts"]

    def test_read_text_and_binary(self, monkeypatch):
        content = {"data": b"# hello\n"}

        def read(_payload):
            return {
                "name": "a.md",
                "size": len(content["data"]),
                "b64": base64.b64encode(content["data"]).decode(),
                "mtime": 5.5,
            }

        fake = FakeSandbox(
            monkeypatch,
            {
                "/files/stat": {
                    "exists": True,
                    "type": "file",
                    "size": 8,
                    "mtime": 5.5,
                },
                "/files/read": read,
            },
        )
        r = self._post(MEMBER, "/read", {"path": "/workspace/a.md"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["text"] == "# hello\n" and body["mtime"] == 5.5 and body["rw"]
        assert fake.calls[-1] == (
            "/files/read",
            {
                "user_id": "member1",
                "mounts": fake.calls[-1][1]["mounts"],
                "path": "a.md",
                "nofollow": True,
            },
        )
        for data in (b"a\x00b", b"\xff\xfe\x00", "日本語".encode("shift_jis")):
            content["data"] = data
            body = self._post(MEMBER, "/read", {"path": "/workspace/a.md"}).json()
            assert body["text"] is None and body["reason"] == "binary", data
        # 上限超えは中身を読まずに too_large
        from nucllei.routers import sandbox_files

        fake.responses["/files/stat"] = {
            "exists": True,
            "type": "file",
            "size": sandbox_files.TEXT_LIMIT + 1,
            "mtime": 1.0,
        }
        n = len(fake.calls)
        body = self._post(MEMBER, "/read", {"path": "/workspace/big.txt"}).json()
        assert body["text"] is None and body["reason"] == "too_large"
        assert [c[0] for c in fake.calls[n:]] == ["/files/stat"]
        # ディレクトリ・無い物は 404
        fake.responses["/files/stat"] = {"exists": True, "type": "dir"}
        assert self._post(MEMBER, "/read", {"path": "/workspace/d"}).status_code == 404
        fake.responses["/files/stat"] = {"exists": False}
        assert self._post(MEMBER, "/read", {"path": "/workspace/x"}).status_code == 404

    def test_write_conflict_and_limits(self, monkeypatch):
        fake = FakeSandbox(monkeypatch, {"/files/write": {"ok": True, "mtime": 9.0}})
        r = self._post(
            MEMBER,
            "/write",
            {"path": "/workspace/a.md", "content": "あ", "expected_mtime": 5.5},
        )
        assert r.status_code == 200, r.text
        assert r.json() == {
            "ok": True,
            "path": "/workspace/a.md",
            "size": 3,
            "mtime": 9.0,
        }
        payload = fake.calls[0][1]
        assert payload["path"] == "a.md" and payload["expected_mtime"] == 5.5
        assert payload["nofollow"] is True and payload["exclusive"] is False
        assert base64.b64decode(payload["b64"]).decode() == "あ"
        # 外で変更された → 409 を detail ごと中継
        fake.responses["/files/write"] = fake.error(
            409, {"message": "modified", "mtime": 7.0}
        )
        r = self._post(
            MEMBER,
            "/write",
            {"path": "/workspace/a.md", "content": "x", "expected_mtime": 5.5},
        )
        assert r.status_code == 409
        assert r.json()["detail"] == {"message": "modified", "mtime": 7.0}
        # 容量切れ → 507
        fake.responses["/files/write"] = fake.error(507, "workspace quota exceeded")
        r = self._post(MEMBER, "/write", {"path": "/workspace/a.md", "content": "x"})
        assert r.status_code == 507
        # 上限(SANDBOX_WRITE_FILE_LIMIT_BYTES)
        from nucllei.builtin_tools import sandbox

        monkeypatch.setattr(sandbox, "WRITE_FILE_LIMIT", 4)
        n = len(fake.calls)
        r = self._post(
            MEMBER, "/write", {"path": "/workspace/a.md", "content": "12345"}
        )
        assert r.status_code == 413 and len(fake.calls) == n
        # sandbox に繋がらない → 500(502 は画面のバックエンド不達バナーに使うので返さない)
        fake.responses["/files/write"] = lambda _p: (_ for _ in ()).throw(
            ConnectionError("down")
        )
        r = self._post(MEMBER, "/write", {"path": "/workspace/a.md", "content": "x"})
        assert r.status_code == 500

    def test_upload(self, monkeypatch):
        fake = FakeSandbox(monkeypatch, {"/files/write": {"ok": True, "mtime": 1.0}})

        def up(user, dir, name, data, overwrite=None):
            form = {"dir": dir}
            if overwrite is not None:
                form["overwrite"] = "true" if overwrite else "false"
            with mock_webui_user(**user):
                return self.fast_api_client.post(
                    self.create_url("/upload"),
                    data=form,
                    files={"file": (name, data, "application/octet-stream")},
                )

        r = up(MEMBER, "/workspace/data", "a.csv", b"x,y")
        assert r.status_code == 200, r.text
        assert r.json()["path"] == "/workspace/data/a.csv"
        payload = fake.calls[-1][1]
        assert payload["path"] == "data/a.csv" and payload["exclusive"] is True
        up(MEMBER, "/workspace", "a.csv", b"x", overwrite=True)
        assert fake.calls[-1][1]["exclusive"] is False
        assert fake.calls[-1][1]["path"] == "a.csv"
        n = len(fake.calls)
        assert up(MEMBER, "/workspace", ".bashrc", b"x").status_code == 400
        assert up(MEMBER, "/ext/public", "a.csv", b"x").status_code == 403
        assert up(MEMBER, "/ext/groups/OTHER", "a.csv", b"x").status_code == 404
        from nucllei.builtin_tools import sandbox

        monkeypatch.setattr(sandbox, "WRITE_FILE_LIMIT", 2)
        assert up(MEMBER, "/workspace", "a.csv", b"xyz").status_code == 413
        assert len(fake.calls) == n
        # 既に在る → 409 を中継
        monkeypatch.setattr(sandbox, "WRITE_FILE_LIMIT", 1024)
        fake.responses["/files/write"] = fake.error(
            409, {"message": "already exists", "mtime": 1.0}
        )
        assert up(MEMBER, "/workspace", "a.csv", b"x").status_code == 409

    def test_download_is_attachment(self, monkeypatch):
        FakeSandbox(
            monkeypatch,
            {
                "/files/read": {
                    "name": "x.html",
                    "size": 4,
                    "b64": base64.b64encode(b"<h1>").decode(),
                    "mtime": 1.0,
                }
            },
        )
        r = self._get(MEMBER, "/download", path="/ext/groups/EMR/表 1.html")
        assert r.status_code == 200 and r.content == b"<h1>"
        assert r.headers["content-type"] == "application/octet-stream"
        assert r.headers["content-disposition"].startswith("attachment;")
        assert "%E8%A1%A8%201.html" in r.headers["content-disposition"]
        assert r.headers["x-content-type-options"] == "nosniff"
        r = self._get(MEMBER, "/download", path="/ext/groups/OTHER/secret.md")
        assert r.status_code == 404

    def test_delete_rename_mkdir_usage(self, monkeypatch):
        fake = FakeSandbox(
            monkeypatch,
            {
                "/files/delete": {"ok": True},
                "/files/rename": {"ok": True, "path": "data/b.md"},
                "/files/mkdir": {"ok": True, "path": "d"},
                "/files/usage": {
                    "used_bytes": 2,
                    "total_bytes": 10,
                    "quota_gb": 10,
                    "cache_ttl_days": 30,
                },
            },
        )
        assert self._post(MEMBER, "/delete", {"path": "/workspace/a.md"}).json() == {
            "ok": True
        }
        assert fake.calls[-1][1]["path"] == "a.md"
        r = self._post(
            MEMBER,
            "/rename",
            {"path": "/workspace/a.md", "dest": "/workspace/data/b.md"},
        )
        assert r.json() == {"ok": True, "path": "/workspace/data/b.md"}
        assert (fake.calls[-1][1]["path"], fake.calls[-1][1]["dest"]) == (
            "a.md",
            "data/b.md",
        )
        n = len(fake.calls)
        r = self._post(
            MEMBER,
            "/rename",
            {"path": "/ext/groups/EMR/a", "dest": "/ext/groups/OTHER/a"},
        )
        assert r.status_code == 404 and len(fake.calls) == n
        r = self._post(MEMBER, "/mkdir", {"path": "/workspace/d"})
        assert r.json() == {"ok": True, "path": "/workspace/d"}
        r = self._get(MEMBER, "/usage")
        assert r.json() == {"used_bytes": 2, "total_bytes": 10, "quota_gb": 10}
        assert fake.calls[-1] == (
            "/files/usage",
            {"user_id": "member1", "mounts": fake.calls[-1][1]["mounts"]},
        )
        # sandbox の 400(保護されたディレクトリ等)はそのまま
        fake.responses["/files/delete"] = fake.error(400, "protected directory")
        r = self._post(MEMBER, "/delete", {"path": "/workspace/imports"})
        assert r.status_code == 400 and r.json()["detail"] == "protected directory"


# ---------------------------------------------------------------------------
# sandbox tool の容量切れの文面
# ---------------------------------------------------------------------------
def _tool(responses):
    from nucllei.builtin_tools.sandbox import Tools

    tool = Tools()
    tool.calls = []

    async def _post(path, payload, timeout_s):
        tool.calls.append((path, payload))
        res = responses[path]
        if isinstance(res, Exception):
            raise res
        return res

    tool._post = _post
    return tool


def _text(coro):
    return asyncio.run(call_text(coro))


@pytest.mark.parametrize("status", [507])
def test_tool_quota_full_points_to_file_browser(status):
    from nucllei.builtin_tools.sandbox import SandboxHTTPError

    err = SandboxHTTPError(status, "workspace quota exceeded")
    tool = _tool({"/files/write": err, "/exec": err})
    out = _text(tool.write_file("/workspace/a.txt", "x", __user__={"id": "u1"}))
    assert "容量" in out and "ファイルブラウザ" in out, out
    out = _text(tool.run_shell("ls", __user__={"id": "u1"}))
    assert "容量" in out and "ファイルブラウザ" in out, out


def test_tool_exec_enospc_in_stderr_adds_note():
    res = {
        "exit_code": 1,
        "duration_s": 0.1,
        "stdout": "",
        "stderr": "OSError: [Errno 28] No space left on device",
        "exports": [],
    }
    tool = _tool({"/exec": res})
    out = _text(tool.run_shell("dd", __user__={"id": "u1"}))
    assert "ファイルブラウザ" in out, out
    tool = _tool({"/exec": {**res, "stderr": "boom"}})
    out = _text(tool.run_shell("x", __user__={"id": "u1"}))
    assert "ファイルブラウザ" not in out
