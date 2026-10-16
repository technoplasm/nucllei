"""`/ext` のグループ別 ACL(#152)の回帰。CORE: group 権限・認証。

判定は `utils/ext_acl` 1 か所で、使う側の全員が同じ答えを出すことを固定する:

- ACL そのもの(`ext_access` / `ext_visible_groups` / `sandbox_ext_mounts`)— メンバー判定は
  実 DB の Groups。#151 より前の名前(`:` 入り等)は bind も配信もしない
- `/ext` ゲート(`serve_ext`)— 非メンバーは不在と同じ 404、admin は素通し、`/ext/groups` 直下は
  404、グループ・public の応答は `Cache-Control: private, no-cache`、グループの中の symlink で
  他グループへ出られない
- resolver(`resolve_url` / `resolve_path` / `mounted_path_for_url` / `url_for_mounted_path`)—
  user を渡さなければ ACL の置き場は見えない(fail closed)
- sandbox tool — `mounts` の payload、write_file / list_workspace / fetch_from_workspace の可否
"""

import asyncio
import base64

import pytest

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user
from test.util.tool_errors import call_text

MEMBER = {"id": "member1", "role": "user"}
OUTSIDER = {"id": "outsider1", "role": "user"}
ADMIN = {"id": "admin1", "role": "admin"}


def _make_groups():
    from nucllei.models.groups import GroupForm, GroupUpdateForm, Groups

    def make(name, user_ids):
        g = Groups.insert_new_group("admin1", GroupForm(name=name, description=""))
        Groups.update_group_by_id(
            g.id, GroupUpdateForm(name=name, description="", user_ids=user_ids)
        )
        return g

    make("EMR", ["member1"])
    make("内科外来", ["member1", "outsider9"])
    make("OTHER", ["other1"])
    # #151 より前に作られうる、ディレクトリ名にできない名前(model 直叩きなら入る)
    make("rank:部長", ["member1"])


def _tree(monkeypatch, tmp_path):
    """nucllei 側の静的 mount: groups / public / doc。"""
    from nucllei import env

    root = tmp_path / "ext"
    for d in (
        "groups/EMR/recipes",
        "groups/OTHER",
        "groups/内科外来",
        "public",
        "doc",
    ):
        (root / d).mkdir(parents=True)
    (root / "groups/EMR/recipes/a.md").write_text("emr")
    (root / "groups/EMR/fig.png").write_bytes(b"\x89PNG\r\n\x1a\nemr")
    (root / "groups/OTHER/secret.md").write_text("other-secret")
    (root / "groups/内科外来/n.md").write_text("naika")
    (root / "public/p.md").write_text("pub")
    (root / "doc/d.md").write_text("doc")
    # jail の中で作れる「他グループへの symlink」
    (root / "groups/EMR/leak.md").symlink_to(root / "groups/OTHER/secret.md")
    (root / "groups/EMR/rel.md").symlink_to("../OTHER/secret.md")
    (root / "groups/EMR/dir").symlink_to(root / "groups/OTHER")
    monkeypatch.setattr(
        env,
        "EXT_MOUNTS",
        {
            "groups": str(root / "groups"),
            "public": str(root / "public"),
            "doc": str(root / "doc"),
        },
    )
    return root


# ---------------------------------------------------------------------------
# ACL 本体
# ---------------------------------------------------------------------------
class TestExtAcl(AbstractPostgresTest):
    BASE_PATH = "/ext"

    def setup_method(self):
        super().setup_method()
        _make_groups()

    def test_access_matrix(self):
        from nucllei.utils.ext_acl import ext_access

        cases = [
            # (user, rel, want)
            (MEMBER, "groups/EMR/a.md", "rw"),
            (MEMBER, "groups/EMR", "rw"),
            (MEMBER, "groups/内科外来/x", "rw"),
            (MEMBER, "groups/OTHER/secret.md", "none"),
            (MEMBER, "groups/NOPE/x", "none"),
            (MEMBER, "groups/rank:部長/x", "none"),  # 名前がパスにできない
            (MEMBER, "groups", "none"),  # 直下の一覧は ext_visible_groups
            (MEMBER, "groups/", "none"),
            (MEMBER, "groups/EMR/../OTHER/x", "none"),
            (MEMBER, "public/p.md", "ro"),
            (MEMBER, "public", "ro"),
            (MEMBER, "doc/d.md", "ro"),
            (OUTSIDER, "groups/EMR/a.md", "none"),
            (ADMIN, "groups/OTHER/secret.md", "rw"),
            (ADMIN, "groups/NOPE/x", "rw"),
            (ADMIN, "groups/rank:部長/x", "none"),
            (ADMIN, "public/p.md", "rw"),
            (ADMIN, "doc/d.md", "ro"),
            # user 不明: ACL の置き場は見えない、それ以外は従来どおり
            (None, "groups/EMR/a.md", "none"),
            (None, "public/p.md", "none"),
            (None, "doc/d.md", "ro"),
            # id だけ(role 不明)= 一般ユーザー
            ("member1", "groups/EMR/a.md", "rw"),
            ("member1", "public/p.md", "ro"),
        ]
        for user, rel, want in cases:
            assert ext_access(user, rel) == want, (user, rel)

    def test_user_model_is_accepted(self):
        from nucllei.models.users import UserModel
        from nucllei.utils.ext_acl import ext_access

        u = UserModel(
            id="admin1",
            name="a",
            email="a@x",
            role="admin",
            profile_image_url="/x.png",
            last_active_at=0,
            updated_at=0,
            created_at=0,
        )
        assert ext_access(u, "groups/OTHER/x") == "rw"

    def test_visible_groups_and_sandbox_mounts(self):
        from nucllei.utils.ext_acl import ext_visible_groups, sandbox_ext_mounts

        assert ext_visible_groups(MEMBER) == ["EMR", "内科外来"]
        assert ext_visible_groups(OUTSIDER) == []
        assert ext_visible_groups(ADMIN) == ["EMR", "OTHER", "内科外来"]
        assert ext_visible_groups(None) == []

        assert sandbox_ext_mounts(MEMBER) == [
            {"path": "/ext/public", "rw": False},
            {"path": "/ext/groups/EMR", "rw": True},
            {"path": "/ext/groups/内科外来", "rw": True},
        ]
        assert sandbox_ext_mounts(OUTSIDER) == [{"path": "/ext/public", "rw": False}]
        assert sandbox_ext_mounts(ADMIN) == [
            {"path": "/ext/public", "rw": True},
            {"path": "/ext/groups/EMR", "rw": True},
            {"path": "/ext/groups/OTHER", "rw": True},
            {"path": "/ext/groups/内科外来", "rw": True},
        ]
        assert sandbox_ext_mounts(None) == []

    def test_membership_change_is_seen_immediately(self):
        from nucllei.models.groups import GroupUpdateForm, Groups
        from nucllei.utils.ext_acl import ext_access

        g = next(g for g in Groups.get_groups() if g.name == "OTHER")
        assert ext_access(MEMBER, "groups/OTHER/x") == "none"
        Groups.update_group_by_id(
            g.id,
            GroupUpdateForm(name="OTHER", description="", user_ids=["member1"]),
        )
        assert ext_access(MEMBER, "groups/OTHER/x") == "rw"
        Groups.update_group_by_id(
            g.id, GroupUpdateForm(name="OTHER", description="", user_ids=[])
        )
        assert ext_access(MEMBER, "groups/OTHER/x") == "none"


# ---------------------------------------------------------------------------
# /ext ゲート
# ---------------------------------------------------------------------------
class TestServeExtAcl(AbstractPostgresTest):
    BASE_PATH = "/ext"

    def setup_method(self):
        super().setup_method()
        _make_groups()

    def _get(self, user, path):
        with mock_webui_user(**user):
            return self.fast_api_client.get(path)

    def test_member_outsider_admin(self, monkeypatch, tmp_path):
        _tree(monkeypatch, tmp_path)
        r = self._get(MEMBER, "/ext/groups/EMR/recipes/a.md")
        assert r.status_code == 200 and r.text == "emr"
        assert r.headers["cache-control"] == "private, no-cache"
        # 日本語のグループ名(percent-encoded)
        r = self._get(MEMBER, "/ext/groups/%E5%86%85%E7%A7%91%E5%A4%96%E6%9D%A5/n.md")
        assert r.status_code == 200 and r.text == "naika"

        # 非メンバー: 在るファイルも無いファイルも同じ 404(存在を漏らさない)
        a = self._get(OUTSIDER, "/ext/groups/EMR/recipes/a.md")
        b = self._get(OUTSIDER, "/ext/groups/EMR/recipes/nope.md")
        assert a.status_code == b.status_code == 404 and a.json() == b.json()
        assert self._get(MEMBER, "/ext/groups/OTHER/secret.md").status_code == 404

        # admin は素通し
        r = self._get(ADMIN, "/ext/groups/OTHER/secret.md")
        assert r.status_code == 200 and r.text == "other-secret"

    def test_groups_root_listing_is_404(self, monkeypatch, tmp_path):
        _tree(monkeypatch, tmp_path)
        for user in (MEMBER, ADMIN):
            for p in (
                "/ext/groups",
                "/ext/groups/",
                "/ext/groups/EMR",
                "/ext/groups/EMR/",
            ):
                assert self._get(user, p).status_code == 404, (user, p)

    def test_public_and_others(self, monkeypatch, tmp_path):
        _tree(monkeypatch, tmp_path)
        r = self._get(OUTSIDER, "/ext/public/p.md")
        assert (
            r.status_code == 200 and r.headers["cache-control"] == "private, no-cache"
        )
        r = self._get(OUTSIDER, "/ext/doc/d.md")
        assert r.status_code == 200 and "cache-control" not in r.headers

    def test_symlink_inside_group_cannot_reach_other_group(self, monkeypatch, tmp_path):
        """jail でメンバーが作った `/ext/groups/EMR/leak.md -> …/OTHER/secret.md` を、ゲートが
        辿って他グループを配信しない(閉じ込めはグループのディレクトリ)。"""
        _tree(monkeypatch, tmp_path)
        for p in (
            "/ext/groups/EMR/leak.md",
            "/ext/groups/EMR/rel.md",
            "/ext/groups/EMR/dir/secret.md",
        ):
            r = self._get(MEMBER, p)
            assert r.status_code == 404 and "other-secret" not in r.text, p
            # admin でも、EMR の住所で OTHER の中身は出ない(住所と実体が一致しない)
            assert self._get(ADMIN, p).status_code == 404, p

    def test_group_dir_symlink_is_not_served(self, monkeypatch, tmp_path):
        root = _tree(monkeypatch, tmp_path)
        (root / "groups/内科外来").rename(root / "groups/old")
        (root / "groups/内科外来").symlink_to(root / "groups/OTHER")
        r = self._get(MEMBER, "/ext/groups/内科外来/secret.md")
        assert r.status_code == 404

    def test_proxy_mount_is_gated_too(self, monkeypatch, tmp_path):
        """groups を proxy にした配備でも ACL は同じ(パスで判定してから中継する)。"""
        import httpx
        from nucllei import env
        from nucllei.utils import ext_upstreams

        def handler(request):
            return httpx.Response(
                200, text="up", headers={"cache-control": "max-age=60"}
            )

        monkeypatch.setattr(
            ext_upstreams,
            "_client",
            httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        monkeypatch.setattr(env, "EXT_MOUNTS", {"groups": "http://up.test/groups"})
        assert self._get(OUTSIDER, "/ext/groups/EMR/a.md").status_code == 404
        r = self._get(MEMBER, "/ext/groups/EMR/a.md")
        assert (
            r.status_code == 200 and r.headers["cache-control"] == "private, no-cache"
        )


# ---------------------------------------------------------------------------
# resolver
# ---------------------------------------------------------------------------
class TestResolverAcl(AbstractPostgresTest):
    BASE_PATH = "/ext"

    def setup_method(self):
        super().setup_method()
        _make_groups()

    def test_resolve_url_and_path(self, monkeypatch, tmp_path):
        from nucllei.utils.image_refs import resolve_path, resolve_ref, resolve_url

        _tree(monkeypatch, tmp_path)
        url = "/ext/groups/EMR/recipes/a.md"
        loaded, err = asyncio.run(resolve_url(url, user=MEMBER))
        assert err is None and loaded.data == b"emr"
        # 絶対 URL(host 不問)も同じ
        loaded, _ = asyncio.run(
            resolve_url("https://h/ext/groups/EMR/recipes/a.md", user=MEMBER)
        )
        assert loaded.data == b"emr"
        for user in (OUTSIDER, None):
            loaded, err = asyncio.run(resolve_url(url, user=user))
            assert loaded is None and err.code == "not_found", user
        loaded, err = asyncio.run(
            resolve_url(url)
        )  # user を渡さない呼び手 = fail closed
        assert loaded is None and err.code == "not_found"
        # 非 ACL の置き場は従来どおり user 無しで読める
        loaded, _ = asyncio.run(resolve_url("/ext/doc/d.md"))
        assert loaded.data == b"doc"

        loaded, err = asyncio.run(resolve_path(url, MEMBER))
        assert loaded.data == b"emr"
        loaded, err = asyncio.run(resolve_path(url, "member1"))  # id 文字列
        assert loaded.data == b"emr"
        loaded, err = asyncio.run(resolve_path(url, OUTSIDER))
        assert loaded is None and err.code == "not_found"
        loaded, err = asyncio.run(resolve_path("/ext/groups/EMR/leak.md", MEMBER))
        assert loaded is None and err.code == "not_found"

        # 保存済み参照(送信直前 inline)も送信者の ACL
        loaded, _ = asyncio.run(resolve_ref(url, MEMBER))
        assert loaded.data == b"emr"
        loaded, err = asyncio.run(resolve_ref(url, OUTSIDER))
        assert loaded is None

        # public は全員 ro で読める
        loaded, _ = asyncio.run(resolve_url("/ext/public/p.md", user=OUTSIDER))
        assert loaded.data == b"pub"

    def test_bridges(self, monkeypatch, tmp_path):
        from nucllei.utils.image_refs import mounted_path_for_url, url_for_mounted_path

        _tree(monkeypatch, tmp_path)
        p = "/ext/groups/EMR/recipes/a.md"
        assert asyncio.run(mounted_path_for_url(p, MEMBER)) == p
        assert asyncio.run(mounted_path_for_url(p, OUTSIDER)) is None
        assert asyncio.run(mounted_path_for_url(p)) is None
        assert asyncio.run(url_for_mounted_path(p, MEMBER)) == p
        assert asyncio.run(url_for_mounted_path(p, OUTSIDER)) is None
        assert (
            asyncio.run(url_for_mounted_path("/ext/groups/EMR/leak.md", MEMBER)) is None
        )
        assert (
            asyncio.run(url_for_mounted_path("/ext/public/p.md", OUTSIDER))
            == "/ext/public/p.md"
        )

    def test_redirect_into_other_group_is_not_followed(self, monkeypatch, tmp_path):
        """一般の URL の redirect の先が `/ext/groups/OTHER/…` でも、user の ACL で止まる。"""
        import httpx
        from nucllei.utils import ext_upstreams, image_refs

        _tree(monkeypatch, tmp_path)

        def handler(request):
            return httpx.Response(
                302, headers={"location": "/ext/groups/OTHER/secret.md"}
            )

        monkeypatch.setattr(
            ext_upstreams,
            "_client",
            httpx.AsyncClient(
                transport=httpx.MockTransport(handler), follow_redirects=False
            ),
        )

        async def pin(host):
            return "203.0.113.5", ""

        monkeypatch.setattr(image_refs, "pin_address", pin)
        loaded, err = asyncio.run(
            image_refs.resolve_url("http://intra.test/x", user=MEMBER)
        )
        assert loaded is None and err.code == "not_found"
        loaded, err = asyncio.run(
            image_refs.resolve_url("http://intra.test/x", user=ADMIN)
        )
        assert loaded is not None and loaded.data == b"other-secret"


# ---------------------------------------------------------------------------
# sandbox tool
# ---------------------------------------------------------------------------
def _tool(responses):
    from nucllei.builtin_tools.sandbox import Tools

    tool = Tools()
    tool.calls = []

    async def _post(path, payload, timeout_s):
        tool.calls.append((path, payload))
        return responses[path]

    tool._post = _post
    return tool


def _text(coro):
    return asyncio.run(call_text(coro))


class TestSandboxToolAcl(AbstractPostgresTest):
    BASE_PATH = "/ext"

    def setup_method(self):
        super().setup_method()
        _make_groups()

    def test_exec_and_cell_send_mounts(self):
        res = {
            "exit_code": 0,
            "duration_s": 0.1,
            "stdout": "",
            "stderr": "",
            "exports": [],
        }
        tool = _tool({"/exec": res, "/exec_cell": {**res, "error": None}})
        _text(tool.run_shell("ls", __user__=MEMBER))
        _text(tool.run_python("1", __user__=ADMIN, __chat_id__="c1"))
        assert tool.calls[0][1]["mounts"] == [
            {"path": "/ext/public", "rw": False},
            {"path": "/ext/groups/EMR", "rw": True},
            {"path": "/ext/groups/内科外来", "rw": True},
        ]
        assert {"path": "/ext/groups/OTHER", "rw": True} in tool.calls[1][1]["mounts"]
        assert {"path": "/ext/public", "rw": True} in tool.calls[1][1]["mounts"]

    def test_write_file(self):
        tool = _tool({"/files/write": {"ok": True, "size": 2}})
        out = _text(
            tool.write_file("/ext/groups/EMR/recipes/r.md", "hi", __user__=MEMBER)
        )
        assert out.startswith("/ext/groups/EMR/recipes/r.md に書き込んだ"), out
        path, payload = tool.calls[0]
        assert (
            path == "/files/write" and payload["path"] == "/ext/groups/EMR/recipes/r.md"
        )
        assert payload["mounts"][1] == {"path": "/ext/groups/EMR", "rw": True}

        tool = _tool({})
        out = _text(tool.write_file("/ext/groups/OTHER/x.md", "x", __user__=MEMBER))
        assert "は無い" in out and tool.calls == []
        out = _text(tool.write_file("/ext/public/x.md", "x", __user__=MEMBER))
        assert "read-only" in out and tool.calls == []
        # /ext/public は管理者なら書ける。代わりの書き先は勧めない (利用者が決める)
        assert "書けるのは管理者だけ" in out
        assert (
            "別の置き場に書くかはユーザーが決める" in out and "/ext/groups/<" not in out
        )
        out = _text(tool.write_file("/ext/groups/EMR", "x", __user__=MEMBER))
        assert "ディレクトリ" in out and tool.calls == []
        out = _text(tool.write_file("/ext/groups/EMR/../OTHER/x", "x", __user__=MEMBER))
        assert "は無い" in out and tool.calls == []

        tool = _tool({"/files/write": {"ok": True}})
        out = _text(tool.write_file("/ext/public/x.md", "x", __user__=ADMIN))
        assert "書き込んだ" in out

    def test_read_file(self):
        """#153: read_file も同じ ACL — 見えない置き場は「無い」(sandbox に問い合わせない)、
        public は一般ユーザーでも読める (ro)、読むときは mounts + nofollow。"""
        body = "手順".encode("utf-8")
        responses = {
            "/files/stat": {"exists": True, "type": "file", "size": len(body)},
            "/files/read": {"name": "r.md", "b64": base64.b64encode(body).decode()},
        }
        tool = _tool(responses)
        out = _text(tool.read_file("/ext/groups/EMR/recipes/r.md", __user__=MEMBER))
        assert out.kind == "markdown" and out.text.endswith("\n\n手順")
        assert [c[0] for c in tool.calls] == ["/files/stat", "/files/read"]
        stat_payload, read_payload = tool.calls[0][1], tool.calls[1][1]
        assert (
            stat_payload["path"]
            == read_payload["path"]
            == "/ext/groups/EMR/recipes/r.md"
        )
        assert {"path": "/ext/groups/EMR", "rw": True} in read_payload["mounts"]
        assert read_payload["nofollow"] is True

        tool = _tool(responses)
        out = _text(tool.read_file("/ext/public/p.md", __user__=OUTSIDER))
        assert out.text.endswith("手順")
        assert tool.calls[1][1]["mounts"] == [{"path": "/ext/public", "rw": False}]

        tool = _tool({})
        for path in (
            "/ext/groups/OTHER/secret.md",
            "/ext/groups/EMR/../OTHER/secret.md",
        ):
            out = _text(tool.read_file(path, __user__=MEMBER))
            assert out.startswith("ERROR: /ext/groups/OTHER/secret.md は無い"), out
        out = _text(tool.read_file("/ext/groups/OTHER", __user__=MEMBER))
        assert "は無い" in out
        out = _text(tool.read_file("/ext/groups", __user__=MEMBER))
        assert "グループの置き場の一覧" in out
        out = _text(tool.read_file("/ext/groups/EMR/", __user__=MEMBER))
        assert "ディレクトリ" in out
        assert tool.calls == []

        # 経路の symlink は sandbox が 400 で断る — write_file と同じ文
        from nucllei.builtin_tools.sandbox import SandboxHTTPError

        tool = _tool({})

        async def _post(path, payload, timeout_s):
            raise SandboxHTTPError(400, "path contains a symlink")

        tool._post = _post
        out = _text(tool.read_file("/ext/groups/EMR/dir/secret.md", __user__=MEMBER))
        assert out == (
            "ERROR: /ext/groups/EMR/dir/secret.md は使えない path (経路に symlink を含む等で"
            " sandbox が受け付けなかった)。次の手: symlink を経由しない実際のパスで渡す。"
        )

    def test_list_workspace(self):
        tool = _tool({})
        out = _text(tool.list_workspace("/ext/groups", __user__=MEMBER))
        assert "/ext/groups/EMR/" in out and "内科外来" in out and "OTHER" not in out
        out = _text(tool.list_workspace("/ext/groups", __user__=OUTSIDER))
        assert "無い" in out

        tool = _tool(
            {
                "/files/list": {
                    "entries": [{"path": "/ext/groups/EMR/a.md", "size": 3}],
                    "exists": True,
                    "is_dir": True,
                }
            }
        )
        out = _text(tool.list_workspace("/ext/groups/EMR", __user__=MEMBER))
        assert out == "- /ext/groups/EMR/a.md (3B)"
        assert tool.calls[0][1]["path"] == "/ext/groups/EMR"
        tool = _tool({})
        out = _text(tool.list_workspace("/ext/groups/OTHER", __user__=MEMBER))
        assert "は無い" in out and tool.calls == []

    def test_fetch_from_workspace(self, monkeypatch, tmp_path):
        _tree(monkeypatch, tmp_path)
        tool = _tool({})
        out = _text(
            tool.fetch_from_workspace("/ext/groups/EMR/recipes/a.md", __user__=MEMBER)
        )
        assert "添付不要" in out and "(/ext/groups/EMR/recipes/a.md)" in out
        out = _text(
            tool.fetch_from_workspace("/ext/groups/OTHER/secret.md", __user__=MEMBER)
        )
        assert "は無い" in out and "other-secret" not in out and tool.calls == []

    def test_fetch_falls_back_to_sandbox_when_not_served(self, monkeypatch):
        from nucllei import env

        monkeypatch.setattr(env, "EXT_MOUNTS", {})
        tool = _tool(
            {
                "/files/read": {
                    "name": "a.csv",
                    "size": 3,
                    "b64": base64.b64encode(b"x,y").decode(),
                }
            }
        )
        monkeypatch.setattr(
            tool,
            "_attach",
            lambda abs_path, name, data, *a: asyncio.sleep(
                0, f"attached {name} {data!r}"
            ),
        )
        out = _text(tool.fetch_from_workspace("/ext/groups/EMR/a.csv", __user__=MEMBER))
        assert out == "attached a.csv b'x,y'"
        assert tool.calls[0][0] == "/files/read"
        assert tool.calls[0][1]["path"] == "/ext/groups/EMR/a.csv"

    def test_send_to_workspace_url_respects_acl(self, monkeypatch, tmp_path):
        _tree(monkeypatch, tmp_path)
        tool = _tool({})
        out = _text(
            tool.send_to_workspace(url="/ext/groups/EMR/recipes/a.md", __user__=MEMBER)
        )
        assert "取り込み不要" in out and "/ext/groups/EMR/recipes/a.md" in out
        out = _text(
            tool.send_to_workspace(url="/ext/groups/OTHER/secret.md", __user__=MEMBER)
        )
        assert out.startswith("ERROR") and tool.calls == []


@pytest.mark.parametrize(
    "name,ok",
    [
        ("EMR", True),
        ("内科外来", True),
        ("内科 外来", False),  # 空白はグループ名に使えない
        ("内科　外来", False),
        ("a,b", False),  # カンマも(USER_GROUPS の区切り)
        ("a，b", False),
        ("rank:部長", False),
        (".hidden", False),
        ("..", False),
        ("a/b", False),
        ("a\\b", False),
        (" EMR", False),
        ("a​b", False),
        ("", False),
        (None, False),
    ],
)
def test_group_dir_name(name, ok):
    from nucllei.utils.ext_acl import is_group_dir_name

    assert is_group_dir_name(name) is ok
