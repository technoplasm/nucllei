"""sandbox のファイルブラウザ(#150)`/api/v1/sandbox/files/*` の回帰。CORE: group 権限・認証。

sandbox サービスは偽物(`builtin_tools.sandbox.sandbox_post` を差し替え)。固定すること:

- 開ける場所は本人の `/workspace` と、ACL(`utils/ext_acl`)で見える `/ext/public`・
  `/ext/groups/<g>` だけ。見えないグループ・`/ext/doc` 等は sandbox を呼ばずに 404
- ro の場所への書き換えは sandbox を呼ばずに 403(sandbox にも ro の bind として渡る)
- `.` 始まり・traversal・相対パスは 400
- sandbox への payload(本人の user_id・ACL の mounts・workspace は相対パス・nofollow)
- 平文の判定(UTF-8・NUL 無し・テキストの上限内)、上書き競合 409 の中継、テキストの保存の上限 413、
  容量切れ 507 の中継、ダウンロードは常に添付
- アップロード / ダウンロードは sandbox の `/files/raw` へストリーミングで中継(チャンクで送り、
  全体を bytes にしない)。上限は sandbox の上限(読めていれば送る前に 413)、権限 `chat.file_upload`
  が無ければ 403
- SANDBOX_URL の無い配備では全部 404
- sandbox tool の容量切れの文面: 自分では消さず、ユーザーに伝えて画面の「ワークスペース」→
  「サンドボックス」タブで整理してもらう (2026-10-19 llens 監査 (1) — 旧文は rm を勧めていた)
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

        # `/files/raw`(ストリーミング)。PUT は届いたチャンクをそのまま記録する
        self.raw_puts = []
        self.raw_put_response = {"ok": True, "size": 0, "mtime": 1.0}
        self.raw_get = (None, b"")

        async def fake_put_raw(params, body, content_length=None):
            chunks = [c async for c in body]
            self.raw_puts.append(
                {"params": params, "chunks": chunks, "content_length": content_length}
            )
            res = self.raw_put_response
            if callable(res):
                return res(params)
            return {**res, "size": sum(map(len, chunks))}

        async def fake_get_raw(params):
            self.calls.append(("/files/raw", params))
            res = self.raw_get
            if callable(res):
                res = res(params)
            length, data = res

            async def chunks():
                for i in range(0, len(data), 4):
                    yield data[i : i + 4]

            return length, chunks()

        monkeypatch.setattr(sandbox, "sandbox_put_raw", fake_put_raw)
        monkeypatch.setattr(sandbox, "sandbox_get_raw", fake_get_raw)

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
        body = r.json()
        from nucllei.builtin_tools import sandbox

        assert body["text_limit_bytes"] == sandbox.TEXT_MAX_BYTES == 1024 * 1024
        assert body["can_upload"] is True
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
                            "ctime": 4.0,
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
        # ctime は中継し、返さない古い sandbox では None
        assert [e["ctime"] for e in body["entries"]] == [None, 4.0]
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
        # テキストの上限超えは中身を読まずに too_large
        from nucllei.builtin_tools import sandbox

        fake.responses["/files/stat"] = {
            "exists": True,
            "type": "file",
            "size": sandbox.TEXT_MAX_BYTES + 1,
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
        # テキストの上限(SANDBOX_TEXT_MAX_SIZE_MB)。detail に上限の値
        from nucllei.builtin_tools import sandbox

        monkeypatch.setattr(sandbox, "TEXT_MAX_BYTES", 4)
        n = len(fake.calls)
        r = self._post(
            MEMBER, "/write", {"path": "/workspace/a.md", "content": "12345"}
        )
        assert r.status_code == 413 and len(fake.calls) == n
        assert r.json()["detail"]["limit"] == 4 and r.json()["detail"]["size"] == 5
        # sandbox に繋がらない → 500(502 は画面のバックエンド不達バナーに使うので返さない)
        fake.responses["/files/write"] = lambda _p: (_ for _ in ()).throw(
            ConnectionError("down")
        )
        r = self._post(MEMBER, "/write", {"path": "/workspace/a.md", "content": "x"})
        assert r.status_code == 500

    def _upload(self, user, dir, name, data, overwrite=None):
        form = {"dir": dir}
        if overwrite is not None:
            form["overwrite"] = "true" if overwrite else "false"
        with mock_webui_user(**user):
            return self.fast_api_client.post(
                self.create_url("/upload"),
                data=form,
                files={"file": (name, data, "application/octet-stream")},
            )

    def test_upload(self, monkeypatch):
        fake = FakeSandbox(monkeypatch)
        up = self._upload
        r = up(MEMBER, "/workspace/data", "a.csv", b"x,y")
        assert r.status_code == 200, r.text
        assert r.json()["path"] == "/workspace/data/a.csv" and r.json()["size"] == 3
        put = fake.raw_puts[-1]
        assert put["params"] == {
            "user_id": "member1",
            "path": "data/a.csv",
            "exclusive": "true",
        }
        assert put["content_length"] == 3 and b"".join(put["chunks"]) == b"x,y"
        # base64-in-JSON の /files/write は使わない
        assert [c for c in fake.calls if c[0] == "/files/write"] == []
        up(MEMBER, "/workspace", "a.csv", b"x", overwrite=True)
        assert fake.raw_puts[-1]["params"]["exclusive"] == "false"
        assert fake.raw_puts[-1]["params"]["path"] == "a.csv"
        # /ext は jail の絶対パスで、mounts はその場所を覆う bind だけ
        up(MEMBER, "/ext/groups/EMR/sub", "b.csv", b"1")
        params = fake.raw_puts[-1]["params"]
        assert params["path"] == "/ext/groups/EMR/sub/b.csv"
        assert json.loads(params["mounts"]) == [{"path": "/ext/groups/EMR", "rw": True}]
        n = len(fake.raw_puts)
        assert up(MEMBER, "/workspace", ".bashrc", b"x").status_code == 400
        assert up(MEMBER, "/ext/public", "a.csv", b"x").status_code == 403
        assert up(MEMBER, "/ext/groups/OTHER", "a.csv", b"x").status_code == 404
        assert len(fake.raw_puts) == n
        # 既に在る(409)・容量切れ(507)は中継、sandbox の 413 は上限の値ごと
        from nucllei.builtin_tools.sandbox import SandboxHTTPError

        def fail(status, detail):
            def raise_(_params):
                raise SandboxHTTPError(status, json.dumps({"detail": detail}))

            return raise_

        fake.raw_put_response = fail(409, {"message": "already exists", "mtime": 1.0})
        assert up(MEMBER, "/workspace", "a.csv", b"x").status_code == 409
        fake.raw_put_response = fail(507, "workspace quota exceeded")
        assert up(MEMBER, "/workspace", "a.csv", b"x").status_code == 507
        fake.raw_put_response = fail(413, "file too large: 9 bytes (limit 8)")
        r = up(MEMBER, "/workspace", "a.csv", b"x" * 9)
        assert r.status_code == 413
        assert r.json()["detail"]["limit"] == 8 and r.json()["detail"]["size"] == 9

    def test_upload_streams_in_chunks(self, monkeypatch):
        # 大きめのファイルは spool からチャンクで送る — 全体を 1 つの bytes にしない
        from nucllei.builtin_tools import sandbox

        fake = FakeSandbox(monkeypatch)
        monkeypatch.setattr(sandbox, "RAW_CHUNK", 64 * 1024)
        data = bytes(range(256)) * (3 * 4096 + 7)  # 約 3 MB
        r = self._upload(MEMBER, "/workspace", "big.bin", data)
        assert r.status_code == 200, r.text
        put = fake.raw_puts[-1]
        assert put["content_length"] == len(data)
        assert len(put["chunks"]) > 40
        assert max(map(len, put["chunks"])) <= 64 * 1024
        assert b"".join(put["chunks"]) == data
        # 構造: アップロード・ダウンロードの経路に全体を読む呼び出し・base64 が無い
        import inspect

        from nucllei.routers import sandbox_files

        for fn in (sandbox_files.upload_file, sandbox_files.download_file):
            src = inspect.getsource(fn)
            assert "file.read()" not in src and "b64" not in src, fn

    def test_upload_limit_and_permission(self, monkeypatch):
        from nucllei.utils import file_limit

        fake = FakeSandbox(monkeypatch)
        # sandbox の上限を読めていれば送る前に 413(画面にも upload_limit_bytes で出す)
        monkeypatch.setattr(file_limit, "_sandbox_limit", 2)
        assert self._get(MEMBER, "/places").json()["upload_limit_bytes"] == 2
        r = self._upload(MEMBER, "/workspace", "a.bin", b"xyz")
        assert r.status_code == 413 and fake.raw_puts == []
        assert r.json()["detail"]["limit"] == 2
        assert self._upload(MEMBER, "/workspace", "a.bin", b"xy").status_code == 200
        monkeypatch.setattr(file_limit, "_sandbox_limit", None)
        assert self._get(MEMBER, "/places").json()["upload_limit_bytes"] is None

        # 権限 chat.file_upload が無ければ 403(admin は常に可)。新規作成・保存は対象外
        perms = self.fast_api_client.app.state.config.USER_PERMISSIONS
        original = perms
        perms = {**perms, "chat": {**perms.get("chat", {}), "file_upload": False}}
        self.fast_api_client.app.state.config.USER_PERMISSIONS = perms
        try:
            n = len(fake.raw_puts)
            r = self._upload(MEMBER, "/workspace", "a.bin", b"x")
            assert r.status_code == 403 and len(fake.raw_puts) == n
            assert self._get(MEMBER, "/places").json()["can_upload"] is False
            assert self._upload(ADMIN, "/workspace", "a.bin", b"x").status_code == 200
            assert self._get(ADMIN, "/places").json()["can_upload"] is True
            fake.responses["/files/write"] = {"ok": True, "mtime": 2.0}
            r = self._post(
                MEMBER,
                "/write",
                {"path": "/workspace/new.md", "content": "x", "exclusive": True},
            )
            assert r.status_code == 200
        finally:
            self.fast_api_client.app.state.config.USER_PERMISSIONS = original

    def test_download_is_attachment(self, monkeypatch):
        fake = FakeSandbox(monkeypatch)
        fake.raw_get = (10, b"<h1>hello!")
        r = self._get(MEMBER, "/download", path="/ext/groups/EMR/表 1.html")
        assert r.status_code == 200 and r.content == b"<h1>hello!"
        assert r.headers["content-length"] == "10"
        assert r.headers["content-type"] == "application/octet-stream"
        assert r.headers["content-disposition"].startswith("attachment;")
        assert "%E8%A1%A8%201.html" in r.headers["content-disposition"]
        assert r.headers["x-content-type-options"] == "nosniff"
        path, params = fake.calls[-1]
        assert path == "/files/raw" and params["path"] == "/ext/groups/EMR/表 1.html"
        assert json.loads(params["mounts"]) == [{"path": "/ext/groups/EMR", "rw": True}]
        fake.raw_get = (2, b"ws")
        r = self._get(MEMBER, "/download", path="/workspace/a/b.txt")
        assert r.content == b"ws" and fake.calls[-1][1] == {
            "user_id": "member1",
            "path": "a/b.txt",
        }
        r = self._get(MEMBER, "/download", path="/ext/groups/OTHER/secret.md")
        assert r.status_code == 404
        # sandbox の 404・413 は中継
        from nucllei.builtin_tools.sandbox import SandboxHTTPError

        def missing(_params):
            raise SandboxHTTPError(404, json.dumps({"detail": "not found: x"}))

        fake.raw_get = missing
        assert self._get(MEMBER, "/download", path="/workspace/x").status_code == 404

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
        # 古い sandbox (`trashed` を返さない = 常に完全削除)
        assert self._post(MEMBER, "/delete", {"path": "/workspace/a.md"}).json() == {
            "ok": True,
            "trashed": False,
        }
        assert fake.calls[-1][1]["path"] == "a.md"
        assert fake.calls[-1][1]["permanent"] is False
        # /workspace の削除は sandbox が trash/ の直下へ移す — 移した先を jail のパスで返す
        fake.responses["/files/delete"] = {
            "ok": True,
            "trashed": True,
            "path": "trash/a-2.md",
        }
        r = self._post(MEMBER, "/delete", {"path": "/workspace/a.md"})
        assert r.json() == {
            "ok": True,
            "trashed": True,
            "path": "/workspace/trash/a-2.md",
        }
        # permanent はそのまま渡す (trash の中で消すときに画面が送る)
        fake.responses["/files/delete"] = {"ok": True, "trashed": False}
        r = self._post(
            MEMBER, "/delete", {"path": "/workspace/trash/a.md", "permanent": True}
        )
        assert r.json() == {"ok": True, "trashed": False}
        assert (fake.calls[-1][1]["path"], fake.calls[-1][1]["permanent"]) == (
            "trash/a.md",
            True,
        )
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


# 容量切れの次の手 (507 の全経路で同じ文)。画面の名前は ja-JP の表記 (サイドバー「ワークスペース」、
# タブ「サンドボックス」)
QUOTA_NEXT = (
    "次の手: 自分では消さない。ユーザーに容量が一杯だと伝え、整理するか聞く"
    " (ゴミ箱へ移しただけでは空かず、ゴミ箱を空にして初めて空く)。"
    "ユーザーは画面の「ワークスペース」→「サンドボックス」タブで使用量を見て整理できる。"
)


def _import_fixture(monkeypatch, tmp_path):
    """send_to_workspace(file_id / 添付) が読む Files・Storage を偽物にする (DB に依存しない)。"""
    import types

    from nucllei.builtin_tools import sandbox

    src = tmp_path / "a.csv"
    src.write_text("x", encoding="utf-8")
    rec = types.SimpleNamespace(path=str(src), user_id="u1", filename="a.csv")
    monkeypatch.setattr(
        sandbox.Files, "get_file_by_id", lambda fid: rec if fid == "f1" else None
    )
    monkeypatch.setattr(sandbox.Storage, "get_file", lambda path: path)


@pytest.mark.parametrize("status", [507])
def test_tool_quota_full_points_to_file_browser(status, monkeypatch, tmp_path):
    """507 の全経路 (実行・write_file・file_id の取り込み・添付一覧の取り込み) が同じ次の手を
    言う。rm は勧めない。添付一覧の報告では「次の手:」は末尾に 1 回だけ (行の中に書かない)。
    url= の取り込みは test_builtin_sandbox の test_url_import_sandbox_errors。"""
    from nucllei.builtin_tools.sandbox import SandboxHTTPError

    _import_fixture(monkeypatch, tmp_path)
    err = SandboxHTTPError(status, "workspace quota exceeded")
    tool = _tool({"/files/write": err, "/exec": err})
    user = {"id": "u1"}
    out = _text(tool.write_file("/workspace/a.txt", "x", __user__=user))
    assert out == (
        "ERROR: /workspace/a.txt に書けない (workspace の容量 (quota) が一杯)。"
        + QUOTA_NEXT
    )
    out = _text(tool.run_shell("ls", __user__=user))
    assert (
        out == "ERROR: workspace の容量 (quota) が一杯で実行できなかった。" + QUOTA_NEXT
    )
    out = _text(tool.send_to_workspace(file_id="f1", __user__=user))
    assert out == (
        "ERROR: file_id f1 を取り込めない: workspace の容量 (quota) が一杯。"
        + QUOTA_NEXT
    )
    out = _text(
        tool.send_to_workspace(__user__=user, __files__=[{"id": "f1", "name": "a.csv"}])
    )
    assert out == (
        "取り込み結果:\n- a.csv: workspace の容量 (quota) が一杯 (取り込まなかった)\n"
        + QUOTA_NEXT
    )
    for text in (out,):
        assert text.count("次の手:") == 1 and " rm" not in text


def test_tool_exec_enospc_in_stderr_adds_note():
    res = {
        "exit_code": 1,
        "duration_s": 0.1,
        "stdout": "",
        "stderr": "OSError: [Errno 28] No space left on device",
    }
    tool = _tool({"/exec": res})
    out = _text(tool.run_shell("dd", __user__={"id": "u1"}))
    assert out.endswith(
        "※ workspace の容量 (quota) が一杯の可能性がある。" + QUOTA_NEXT
    ), out
    tool = _tool({"/exec": {**res, "stderr": "boom"}})
    out = _text(tool.run_shell("x", __user__={"id": "u1"}))
    assert "容量" not in out


def test_raw_client_streams_both_ways(monkeypatch):
    """`sandbox_put_raw` / `sandbox_get_raw` を本物の HTTP で: PUT は Content-Length 付きで
    チャンクのまま送り、トークンを付ける。GET は Content-Length を返しチャンクで読める。
    sandbox の 413 は SandboxHTTPError。"""
    from aiohttp import web

    from nucllei.builtin_tools import sandbox

    got = {}

    async def put(request):
        got["headers"] = dict(request.headers)
        got["query"] = dict(request.query)
        body = b""
        async for chunk in request.content.iter_chunked(1 << 20):
            body += chunk
        got["body"] = body
        if request.query.get("path") == "big":
            return web.json_response(
                {"detail": "file too large: 9 bytes (limit 8)"}, status=413
            )
        return web.json_response({"ok": True, "size": len(body), "mtime": 1.5})

    async def get(request):
        data = b"z" * (3 * 1024 * 1024 + 5)
        return web.Response(body=data, content_type="application/octet-stream")

    async def main():
        app = web.Application()
        app.router.add_put("/files/raw", put)
        app.router.add_get("/files/raw", get)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        monkeypatch.setattr(sandbox, "SANDBOX_URL", f"http://127.0.0.1:{port}")
        monkeypatch.setattr(sandbox, "SANDBOX_TOKEN", "tok")
        try:

            async def body():
                for i in range(3):
                    yield bytes([i]) * 1000

            params = sandbox.raw_params(
                "u1",
                "/ext/groups/EMR/a",
                [{"path": "/ext/groups/EMR", "rw": True}],
                exclusive=True,
            )
            res = await sandbox.sandbox_put_raw(params, body(), content_length=3000)
            assert res == {"ok": True, "size": 3000, "mtime": 1.5}
            assert got["headers"]["Content-Length"] == "3000"
            assert got["headers"]["Authorization"] == "Bearer tok"
            assert got["query"]["exclusive"] == "true"
            assert json.loads(got["query"]["mounts"]) == [
                {"path": "/ext/groups/EMR", "rw": True}
            ]
            assert got["body"] == b"\x00" * 1000 + b"\x01" * 1000 + b"\x02" * 1000
            with pytest.raises(sandbox.SandboxHTTPError) as e:
                await sandbox.sandbox_put_raw(
                    sandbox.raw_params("u1", "big"), body(), content_length=3000
                )
            assert e.value.status == 413 and "limit 8" in e.value.detail

            length, chunks = await sandbox.sandbox_get_raw(
                sandbox.raw_params("u1", "x")
            )
            sizes = [len(c) async for c in chunks]
            assert length == 3 * 1024 * 1024 + 5 == sum(sizes)
            assert max(sizes) <= sandbox.RAW_CHUNK
        finally:
            await runner.cleanup()

    asyncio.run(main())
