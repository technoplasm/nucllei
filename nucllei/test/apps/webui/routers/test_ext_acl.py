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
    """メンバー全員に書き込みを許したグループ (管理画面でメンバーに入れたときの既定と同じ)。"""
    from nucllei.models.groups import GroupExtForm, GroupForm, GroupUpdateForm, Groups

    def make(name, user_ids):
        g = Groups.insert_new_group("admin1", GroupForm(name=name, description=""))
        Groups.update_group_by_id(
            g.id,
            GroupUpdateForm(
                name=name,
                description="",
                user_ids=user_ids,
                ext=GroupExtForm(writers=user_ids),
            ),
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
        from nucllei.models.groups import GroupExtForm, GroupUpdateForm, Groups
        from nucllei.utils.ext_acl import ext_access

        g = next(g for g in Groups.get_groups() if g.name == "OTHER")
        assert ext_access(MEMBER, "groups/OTHER/x") == "none"
        Groups.update_group_by_id(
            g.id,
            GroupUpdateForm(name="OTHER", description="", user_ids=["member1"]),
        )
        # 書き込みを許すまでは読むだけ
        assert ext_access(MEMBER, "groups/OTHER/x") == "ro"
        Groups.update_group_by_id(
            g.id,
            GroupUpdateForm(
                name="OTHER",
                description="",
                ext=GroupExtForm(writers=["member1"]),
            ),
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

    def test_exec_and_cell_send_user_groups_and_role(self):
        """jail の env NUCLLEI_USER_GROUPS / NUCLLEI_USER_ROLE の元。
        所属は {{USER_GROUPS}} と同じ順で、#151 より前の不正な名前 (rank:部長) は落とす。
        管理者でも所属分だけ (全グループではない)。所属なしは []。"""
        res = {"exit_code": 0, "duration_s": 0.1, "stdout": "", "stderr": ""}
        tool = _tool({"/exec": res, "/exec_cell": {**res, "error": None}})
        _text(tool.run_shell("ls", __user__=MEMBER))
        _text(tool.run_python_once("1", __user__=MEMBER))
        _text(tool.run_python("1", __user__=MEMBER, __chat_id__="c1"))
        _text(tool.run_shell("ls", __user__=ADMIN))
        _text(tool.run_shell("ls", __user__=OUTSIDER))
        _text(tool.run_shell("ls", __user__={"id": "member1", "role": "disabled"}))
        member = {"user_groups": ["EMR", "内科外来"], "user_role": "user"}
        for path, payload in tool.calls[:3]:
            got = {k: payload.get(k) for k in ("user_groups", "user_role")}
            assert got == member, (path, payload)
        assert [p for p, _ in tool.calls[:3]] == ["/exec", "/exec", "/exec_cell"]
        assert tool.calls[3][1]["user_groups"] == [] and (
            tool.calls[3][1]["user_role"] == "admin"
        )
        assert tool.calls[4][1]["user_groups"] == []
        # sandbox の知らないロールは送らない (400 で実行ごと落とさない)
        assert "user_role" not in tool.calls[5][1]

    def test_write_file(self):
        tool = _tool({"/files/write": {"ok": True, "size": 2}})
        out = _text(
            tool.write_file("/ext/groups/EMR/recipes/r.md", "hi", __user__=MEMBER)
        )
        assert out.startswith("/ext/groups/EMR/recipes/r.md を新しく作った"), out
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
        # ACL の外の /ext (/ext/doc 等) は管理者も書けない。書ける場所の案内に管理者の権限も書く (監査 (11))
        out = _text(tool.write_file("/ext/doc/x.md", "x", __user__=MEMBER))
        assert out == (
            "ERROR: /ext/doc/x.md は read-only の参照データ。書き込めるのは `/workspace/` 配下と、"
            "所属グループの `/ext/groups/<グループ名>/` だけ (グループの置き場は管理者が書き込みを許した"
            "メンバーだけ。管理者はすべてのグループの置き場と `/ext/public/` にも書ける)。"
        )
        assert tool.calls == []
        out = _text(tool.write_file("/ext/groups/EMR/../OTHER/x", "x", __user__=MEMBER))
        assert "は無い" in out and tool.calls == []

        tool = _tool({"/files/write": {"ok": True}})
        out = _text(tool.write_file("/ext/public/x.md", "x", __user__=ADMIN))
        assert "を新しく作った" in out

    def test_edit_file(self):
        """edit_file は write_file と同じ ACL — 書けない置き場は sandbox に問い合わせず
        断り、書ける置き場は mounts 付きで stat → 読み (nofollow) → 書き戻す。"""
        body = "- 手順 1\n".encode("utf-8")
        responses = {
            "/files/stat": {"exists": True, "type": "file", "size": len(body)},
            "/files/read": {"name": "r.md", "b64": base64.b64encode(body).decode()},
            "/files/write": {"ok": True},
        }
        tool = _tool(responses)
        out = _text(
            tool.edit_file(
                "/ext/groups/EMR/recipes/r.md", "手順 1", "手順 2", __user__=MEMBER
            )
        )
        assert out.startswith("/ext/groups/EMR/recipes/r.md の 1 行目を置き換えた"), out
        assert [c[0] for c in tool.calls] == [
            "/files/stat",
            "/files/read",
            "/files/write",
        ]
        for _, payload in tool.calls:
            assert payload["path"] == "/ext/groups/EMR/recipes/r.md"
            assert {"path": "/ext/groups/EMR", "rw": True} in payload["mounts"]
        assert tool.calls[1][1]["nofollow"] is True
        written = base64.b64decode(tool.calls[2][1]["b64"]).decode("utf-8")
        assert written == "- 手順 2\n"

        # 見えない置き場は「無い」、public は一般ユーザーには ro、置き場そのものはディレクトリ
        tool = _tool({})
        for path in (
            "/ext/groups/OTHER/x.md",
            "/ext/groups/EMR/../OTHER/x.md",
        ):
            out = _text(tool.edit_file(path, "a", "b", __user__=MEMBER))
            assert "は無い" in out, out
        out = _text(tool.edit_file("/ext/public/x.md", "a", "b", __user__=MEMBER))
        assert "read-only" in out and "書けるのは管理者だけ" in out
        out = _text(tool.edit_file("/ext/groups/EMR", "a", "b", __user__=MEMBER))
        assert "ディレクトリ" in out
        out = _text(tool.edit_file("/ext/doc/x.md", "a", "b", __user__=MEMBER))
        assert out == _text(tool.write_file("/ext/doc/x.md", "a", __user__=MEMBER))
        assert tool.calls == []

        # 管理者は /ext/public も直せる
        tool = _tool(responses)
        out = _text(tool.edit_file("/ext/public/x.md", "手順 1", "x", __user__=ADMIN))
        assert "置き換えた" in out
        assert {"path": "/ext/public", "rw": True} in tool.calls[2][1]["mounts"]

    def test_remove_file(self):
        """remove_file は write_file と同じ ACL — 書けない置き場は sandbox に問い合わせず
        断り、書ける置き場は mounts 付きで `/files/trash` に渡す (移す先は置き場の trash/)。"""
        tool = _tool(
            {
                "/files/trash": {
                    "ok": True,
                    "trashed": True,
                    "path": "/ext/groups/EMR/trash/r.md",
                    "cache_ttl_days": 30,
                }
            }
        )
        out = _text(tool.remove_file("/ext/groups/EMR/recipes/r.md", __user__=MEMBER))
        assert out.startswith(
            "/ext/groups/EMR/recipes/r.md を /ext/groups/EMR/trash/r.md へ移した。"
        ), out
        assert "mv /ext/groups/EMR/trash/r.md /ext/groups/EMR/recipes/r.md" in out
        [(ep, payload)] = tool.calls
        assert ep == "/files/trash"
        assert payload["path"] == "/ext/groups/EMR/recipes/r.md"
        assert {"path": "/ext/groups/EMR", "rw": True} in payload["mounts"]

        # フォルダも渡せる
        tool = _tool(
            {"/files/trash": {"ok": True, "path": "/ext/groups/EMR/trash/recipes"}}
        )
        out = _text(tool.remove_file("/ext/groups/EMR/recipes/", __user__=MEMBER))
        assert (
            "へ移した" in out and tool.calls[0][1]["path"] == "/ext/groups/EMR/recipes"
        )

        # 見えない置き場は「無い」、public は一般ユーザーには ro、置き場そのもの・その trash/ は断る
        tool = _tool({})
        for path in ("/ext/groups/OTHER/x.md", "/ext/groups/EMR/../OTHER/x.md"):
            out = _text(tool.remove_file(path, __user__=MEMBER))
            assert "は無い" in out, out
        out = _text(tool.remove_file("/ext/public/x.md", __user__=MEMBER))
        assert "read-only" in out and "書けるのは管理者だけ" in out
        out = _text(tool.remove_file("/ext/groups/EMR", __user__=MEMBER))
        assert "置き場そのもので、remove_file では移せない" in out
        out = _text(tool.remove_file("/ext/groups", __user__=MEMBER))
        assert "グループの置き場の一覧" in out
        out = _text(tool.remove_file("/ext/groups/EMR/trash/r.md", __user__=MEMBER))
        assert "ゴミ箱の中にある" in out
        out = _text(tool.remove_file("/ext/groups/EMR/trash", __user__=MEMBER))
        assert "ゴミ箱そのもの" in out
        out = _text(tool.remove_file("/ext/doc/x.md", __user__=MEMBER))
        assert out == _text(tool.write_file("/ext/doc/x.md", "a", __user__=MEMBER))
        assert tool.calls == []

        # 管理者は /ext/public も移せる (/ext/public/trash/ へ)
        tool = _tool({"/files/trash": {"ok": True, "path": "/ext/public/trash/x.md"}})
        out = _text(tool.remove_file("/ext/public/x.md", __user__=ADMIN))
        assert out.startswith("/ext/public/x.md を /ext/public/trash/x.md へ移した。")
        assert {"path": "/ext/public", "rw": True} in tool.calls[0][1]["mounts"]

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
        # 件数の上限で打ち切った (sandbox の truncated。2026-10-19 llens 監査) — read-only の注記の後
        tool = _tool(
            {
                "/files/list": {
                    "entries": [{"path": "/ext/public/a.md", "size": 3}],
                    "exists": True,
                    "is_dir": True,
                    "truncated": True,
                }
            }
        )
        out = _text(tool.list_workspace("/ext/public", __user__=OUTSIDER))
        assert out == (
            "- /ext/public/a.md (3B)\n(/ext/public (read-only — 書けるのは管理者だけ))\n"
            "1 件で打ち切った。次の手: 下の階層のディレクトリを path に渡して、絞って一覧する。"
        )
        tool = _tool({})
        out = _text(tool.list_workspace("/ext/groups/OTHER", __user__=MEMBER))
        assert "は無い" in out and tool.calls == []

    def test_list_workspace_marks_groups_admin_is_not_in(self):
        # 管理者には全グループが見えて書ける — 所属していないものはそう添える
        from nucllei.models.groups import GroupUpdateForm, Groups

        g = next(x for x in Groups.get_groups() if x.name == "EMR")
        Groups.update_group_by_id(
            g.id, GroupUpdateForm(name="EMR", description="", user_ids=["admin1"])
        )
        note = "所属していない (管理者なので見えている)"
        out = _text(_tool({}).list_workspace("/ext/groups", __user__=ADMIN))
        lines = out.splitlines()
        assert "- /ext/groups/EMR/ (グループの共有の置き場。書ける)" in lines
        assert f"- /ext/groups/OTHER/ (グループの共有の置き場。書ける。{note})" in lines
        assert (
            f"- /ext/groups/内科外来/ (グループの共有の置き場。書ける。{note})" in lines
        )
        # 所属している一般ユーザーには添えない
        out = _text(_tool({}).list_workspace("/ext/groups", __user__=MEMBER))
        assert note not in out

        listing = {
            "/files/list": {
                "entries": [{"path": "/ext/groups/OTHER/a.md", "size": 3}],
                "exists": True,
                "is_dir": True,
            }
        }
        out = _text(_tool(listing).list_workspace("/ext/groups/OTHER", __user__=ADMIN))
        assert (
            out
            == f"- /ext/groups/OTHER/a.md (3B)\n(/ext/groups/OTHER (OTHER に{note}))"
        )
        out = _text(
            _tool(
                {"/files/list": {"entries": [], "exists": True, "is_dir": True}}
            ).list_workspace("/ext/groups/OTHER/sub", __user__=ADMIN)
        )
        assert out == f"/ext/groups/OTHER/sub は空 (ファイルが無い) (OTHER に{note})。"
        listing["/files/list"]["entries"] = [
            {"path": "/ext/groups/EMR/a.md", "size": 3}
        ]
        out = _text(_tool(listing).list_workspace("/ext/groups/EMR", __user__=ADMIN))
        assert out == "- /ext/groups/EMR/a.md (3B)"

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


# ---------------------------------------------------------------------------
# グループの書き込み: data.ext = {"writers": […]}
# ---------------------------------------------------------------------------
WRITER = {"id": "writer1", "role": "user"}
READER = {"id": "reader1", "role": "user"}
LISTED_ADMIN = {"id": "admin2", "role": "admin"}

RO_WHY = "グループ「CHART」の置き場に書けるのは、管理者が書き込みを許したメンバーだけ"


def _group(name):
    from nucllei.models.groups import Groups

    return next(g for g in Groups.get_groups() if g.name == name)


def _set_ext(name, ext, user_ids=None):
    """管理画面の保存と同じ経路 (GroupUpdateForm の ext)。"""
    from nucllei.models.groups import GroupExtForm, GroupUpdateForm, Groups

    g = _group(name)
    return Groups.update_group_by_id(
        g.id,
        GroupUpdateForm(
            name=name,
            description="",
            user_ids=user_ids,
            ext=None if ext is None else GroupExtForm(**ext),
        ),
    )


def _set_data(name, data):
    """DB を直接書く (保存の正規化を通らない形)。"""
    from nucllei.internal.db import get_db
    from nucllei.models.groups import Group

    g = _group(name)
    with get_db() as db:
        db.query(Group).filter_by(id=g.id).update({"data": data})
        db.commit()


def _make_chart():
    """CHART: writer1 / reader1 / admin2 がメンバーで、書けるのは writer1 だけ。"""
    from nucllei.models.groups import GroupForm, Groups

    Groups.insert_new_group("admin1", GroupForm(name="CHART", description=""))
    _set_ext(
        "CHART",
        {"writers": ["writer1"]},
        user_ids=["writer1", "reader1", "admin2"],
    )


class TestGroupWriters(AbstractPostgresTest):
    BASE_PATH = "/api/v1/groups"

    def setup_method(self):
        super().setup_method()
        _make_groups()
        _make_chart()

    def test_only_writers_can_write(self):
        from nucllei.utils.ext_acl import ext_access

        # writers のメンバーだけ rw、他のメンバーは ro、admin は role で rw
        assert ext_access(WRITER, "groups/CHART/a.md") == "rw"
        assert ext_access(READER, "groups/CHART/a.md") == "ro"
        assert ext_access(READER, "groups/CHART") == "ro"
        assert ext_access(LISTED_ADMIN, "groups/CHART/a.md") == "rw"
        assert ext_access(ADMIN, "groups/CHART/a.md") == "rw"
        assert ext_access(OUTSIDER, "groups/CHART/a.md") == "none"
        assert ext_access("reader1", "groups/CHART/a.md") == "ro"
        # 全員を writers にすればメンバー全員 rw
        _set_ext("CHART", {"writers": ["writer1", "reader1", "admin2"]})
        assert ext_access(READER, "groups/CHART/a.md") == "rw"
        assert ext_access(WRITER, "groups/CHART/a.md") == "rw"
        # 空にすればメンバー全員 ro (admin は rw のまま)
        _set_ext("CHART", {"writers": []})
        assert ext_access(WRITER, "groups/CHART/a.md") == "ro"
        assert ext_access(READER, "groups/CHART/a.md") == "ro"
        assert ext_access(LISTED_ADMIN, "groups/CHART/a.md") == "rw"

    def test_missing_ext_or_writers_means_read_only(self):
        from nucllei.utils.ext_acl import ext_access, sandbox_ext_mounts

        for data in (
            None,
            {},
            {"other": 1},
            {"ext": None},
            {"ext": "writer1"},
            {"ext": {}},
            {"ext": {"writers": None}},
            {"ext": {"writers": "writer1"}},
            # write は読まない (writers だけで決まる)
            {"ext": {"write": "members"}},
            {"ext": {"write": "members", "writers": []}},
        ):
            _set_data("CHART", data)
            assert ext_access(WRITER, "groups/CHART/a.md") == "ro", data
            assert ext_access(READER, "groups/CHART/a.md") == "ro", data
            assert ext_access(LISTED_ADMIN, "groups/CHART/a.md") == "rw", data
            assert {"path": "/ext/groups/CHART", "rw": False} in sandbox_ext_mounts(
                WRITER
            ), data

    def test_non_member_in_writers_grants_nothing(self):
        """保存の正規化をすり抜けた (DB を直接触った) writers でも、判定は所属を確かめる。"""
        from nucllei.utils.ext_acl import ext_access, sandbox_ext_mounts

        _set_data("CHART", {"ext": {"writers": ["writer1", "outsider1", "member1"]}})
        assert ext_access(OUTSIDER, "groups/CHART/a.md") == "none"
        assert ext_access(MEMBER, "groups/CHART/a.md") == "none"
        assert {"path": "/ext/groups/CHART", "rw": True} not in sandbox_ext_mounts(
            OUTSIDER
        )
        assert all("CHART" not in m["path"] for m in sandbox_ext_mounts(MEMBER))
        assert ext_access(WRITER, "groups/CHART/a.md") == "rw"

    def test_writers_are_normalized_on_save(self):
        from nucllei.utils.ext_acl import ext_access

        # writers は保存のたびに user_ids との積 (順序は保つ・重複なし)
        g = _set_ext(
            "CHART",
            {"writers": ["reader1", "outsider1", "reader1", "writer1"]},
        )
        assert g.data["ext"] == {"writers": ["reader1", "writer1"]}
        # メンバーから外すと writers からも落ちる (ext を送らない更新 = OAuth の同期と同じ形でも)
        g = _set_ext("CHART", None, user_ids=["writer1", "admin2"])
        assert g.data["ext"] == {"writers": ["writer1"]}
        # 入れ直しても書き込みは戻らない
        g = _set_ext("CHART", None, user_ids=["writer1", "admin2", "reader1"])
        assert g.data["ext"] == {"writers": ["writer1"]}
        assert ext_access(READER, "groups/CHART/x") == "ro"
        # ext を送らずに足したメンバーは読むだけ・既存の writers は残る
        g = _set_ext("CHART", None, user_ids=["writer1", "admin2", "reader1", "new1"])
        assert g.data["ext"] == {"writers": ["writer1"]}
        assert ext_access("new1", "groups/CHART/x") == "ro"
        assert ext_access(WRITER, "groups/CHART/x") == "rw"

    def test_members_added_to_group_without_ext_are_read_only(self):
        """ext の無いグループに ext を送らずにメンバーを足しても、誰も書けない (ext も作らない)。"""
        from nucllei.models.groups import GroupForm, GroupUpdateForm, Groups
        from nucllei.utils.ext_acl import ext_access

        g = Groups.insert_new_group("admin1", GroupForm(name="NEW", description=""))
        Groups.update_group_by_id(
            g.id, GroupUpdateForm(name="NEW", description="", user_ids=["writer1"])
        )
        assert Groups.get_group_by_id(g.id).data is None
        assert ext_access(WRITER, "groups/NEW/a") == "ro"

    def test_normalize_group_data(self):
        from nucllei.utils.ext_acl import normalize_group_data

        ids = ["a", "b"]
        # write とその他の ext のキーは落とす
        assert normalize_group_data(
            {"ext": {"write": "members", "writers": ["a", "x", 1, "a"], "k": 1}}, ids
        ) == {"ext": {"writers": ["a"]}}
        assert normalize_group_data({"ext": "members", "k": 1}, ids) == {
            "ext": {"writers": []},
            "k": 1,
        }
        assert normalize_group_data({"ext": {"writers": "a"}}, ids) == {
            "ext": {"writers": []}
        }
        assert normalize_group_data({"ext": None}, ids) == {"ext": {"writers": []}}
        assert normalize_group_data({"k": 1}, ids) == {"k": 1}
        assert normalize_group_data(None, ids) is None

    def test_update_without_ext_keeps_the_setting_and_other_data(self):
        """ext を送らない更新 (名前・権限だけ・OAuth の同期) は data を消さない。改名でも writers は
        user_id で持つので残る。"""
        from nucllei.models.groups import GroupUpdateForm, Groups
        from nucllei.utils.ext_acl import ext_access

        g = _group("CHART")
        _set_data("CHART", {**g.data, "other": {"k": 1}})
        Groups.update_group_by_id(
            g.id, GroupUpdateForm(name="CHART", description="", permissions={})
        )
        assert _group("CHART").data == {
            "ext": {"writers": ["writer1"]},
            "other": {"k": 1},
        }
        Groups.update_group_by_id(g.id, GroupUpdateForm(name="CHART2", description=""))
        g2 = Groups.get_group_by_id(g.id)
        assert g2.name == "CHART2" and g2.data["ext"]["writers"] == ["writer1"]
        assert ext_access(WRITER, "groups/CHART2/a") == "rw"
        assert ext_access(READER, "groups/CHART2/a") == "ro"
        # ext を送った更新でも、ext 以外のキーは残る
        _set_ext("CHART2", {"writers": ["reader1"]})
        assert _group("CHART2").data == {
            "ext": {"writers": ["reader1"]},
            "other": {"k": 1},
        }

    def test_removing_user_from_all_groups_drops_writer(self):
        from nucllei.models.groups import Groups

        assert Groups.remove_user_from_all_groups("writer1")
        g = _group("CHART")
        assert "writer1" not in g.user_ids
        assert g.data["ext"]["writers"] == []

    def test_sandbox_mounts_bind_group_read_only(self):
        from nucllei.utils.ext_acl import sandbox_ext_mounts

        assert {"path": "/ext/groups/CHART", "rw": False} in sandbox_ext_mounts(READER)
        assert {"path": "/ext/groups/CHART", "rw": True} in sandbox_ext_mounts(WRITER)
        assert {"path": "/ext/groups/CHART", "rw": True} in sandbox_ext_mounts(
            LISTED_ADMIN
        )
        # 実行要求の mounts も同じ (jail に ro で bind される — sandbox は rw=False を --bindmount_ro に)
        res = {"exit_code": 0, "duration_s": 0.1, "stdout": "", "stderr": ""}
        tool = _tool({"/exec": res, "/exec_cell": {**res, "error": None}})
        _text(tool.run_shell("ls", __user__=READER))
        _text(tool.run_python("1", __user__=READER, __chat_id__="c1"))
        for _, payload in tool.calls:
            assert {"path": "/ext/groups/CHART", "rw": False} in payload["mounts"]
        # 所属 (NUCLLEI_USER_GROUPS) は書き込みの指定によらない
        assert tool.calls[0][1]["user_groups"] == ["CHART"]

    def test_write_tools_refuse_non_writer(self):
        want = (
            f"ERROR: /ext/groups/CHART/a.md はこの利用者には読み取り専用 ({RO_WHY})。"
            "次の手: この置き場には書けないとユーザーに伝える (書けるようにするのは管理者。"
            "別の置き場に書くかはユーザーが決める)。"
        )
        tool = _tool({})
        assert (
            _text(tool.write_file("/ext/groups/CHART/a.md", "x", __user__=READER))
            == want
        )
        assert (
            _text(tool.edit_file("/ext/groups/CHART/a.md", "a", "b", __user__=READER))
            == want
        )
        assert (
            _text(tool.remove_file("/ext/groups/CHART/a.md", __user__=READER)) == want
        )
        assert tool.calls == []
        # 書ける人は通る (mounts は rw)
        tool = _tool({"/files/write": {"ok": True}})
        out = _text(tool.write_file("/ext/groups/CHART/a.md", "x", __user__=WRITER))
        assert "を新しく作った" in out
        assert {"path": "/ext/groups/CHART", "rw": True} in tool.calls[0][1]["mounts"]
        # sandbox の 403 (ro の bind) も同じ文
        from nucllei.builtin_tools.sandbox import SandboxHTTPError, _files_error

        e = _files_error(SandboxHTTPError(403, "read-only"), "/ext/groups/CHART/a.md")
        assert (
            f"{e.fact}"
            == f"/ext/groups/CHART/a.md はこの利用者には読み取り専用 ({RO_WHY})"
        )

    def test_reader_can_still_read(self):
        body = "x".encode()
        tool = _tool(
            {
                "/files/stat": {"exists": True, "type": "file", "size": len(body)},
                "/files/read": {"name": "a.md", "b64": base64.b64encode(body).decode()},
            }
        )
        out = _text(tool.read_file("/ext/groups/CHART/a.md", __user__=READER))
        assert out.text.endswith("x")
        assert {"path": "/ext/groups/CHART", "rw": False} in tool.calls[1][1]["mounts"]

    def test_list_workspace_marks_read_only_groups(self):
        out = _text(_tool({}).list_workspace("/ext/groups", __user__=READER))
        assert out == (
            f"- /ext/groups/CHART/ (グループの共有の置き場。読み取り専用 — {RO_WHY})"
        )
        out = _text(_tool({}).list_workspace("/ext/groups", __user__=WRITER))
        assert out == "- /ext/groups/CHART/ (グループの共有の置き場。書ける)"
        # 管理者は所属していても指定に関係なく書ける
        out = _text(_tool({}).list_workspace("/ext/groups", __user__=LISTED_ADMIN))
        assert (
            "- /ext/groups/CHART/ (グループの共有の置き場。書ける)" in out.splitlines()
        )
        listing = {
            "/files/list": {
                "entries": [{"path": "/ext/groups/CHART/a.md", "size": 3}],
                "exists": True,
                "is_dir": True,
            }
        }
        out = _text(_tool(listing).list_workspace("/ext/groups/CHART", __user__=READER))
        assert out == (
            f"- /ext/groups/CHART/a.md (3B)\n(/ext/groups/CHART (読み取り専用 — {RO_WHY}))"
        )
        out = _text(_tool(listing).list_workspace("/ext/groups/CHART", __user__=WRITER))
        assert out == "- /ext/groups/CHART/a.md (3B)"
        # メンバー全員が writers のグループ
        out = _text(_tool({}).list_workspace("/ext/groups", __user__=MEMBER))
        assert out.splitlines() == [
            "- /ext/groups/EMR/ (グループの共有の置き場。書ける)",
            "- /ext/groups/内科外来/ (グループの共有の置き場。書ける)",
        ]

    def test_toolkit_note_is_the_same_for_everyone(self):
        """共通注記 (tool の spec) は利用者によらない — rw / ro の別は list_workspace の結果が言う
        (prefix cache を割らない)。"""
        from nucllei.builtin_tools.sandbox import Tools

        doc = " ".join(Tools.__doc__.split())
        assert (
            "(書けるのは書き込みを許されたメンバーだけ。list_workspace で分かる)" in doc
        )
        assert "書ける)" not in doc.split("`/ext/public/` は")[0]

    # ---- API (admin だけ) -------------------------------------------------------
    def test_api_update_and_response_shape(self):
        from test.util.mock_user import mock_webui_user_strict

        g = _group("EMR")
        before = _group("EMR").data
        body = {
            "name": "EMR",
            "description": "",
            "user_ids": ["member1"],
            "ext": {"writers": ["member1", "ghost"]},
        }
        with mock_webui_user_strict(id="member1", role="user"):
            r = self.fast_api_client.post(
                self.create_url(f"/id/{g.id}/update"), json=body
            )
        assert r.status_code == 401, r.text
        assert _group("EMR").data == before

        from nucllei.models.users import Users

        for uid in ("member1", "reader1"):
            Users.insert_new_user(uid, uid, f"{uid}@x.local", role="user")
        body["user_ids"] = ["member1", "reader1", "ghost"]
        body["ext"]["writers"] = ["member1", "ghost", "outsider1"]
        with mock_webui_user_strict(id="admin1", role="admin"):
            r = self.fast_api_client.post(
                self.create_url(f"/id/{g.id}/update"), json=body
            )
            assert r.status_code == 200, r.text
            # user_ids は実在のユーザーに絞られ、writers はその積
            got = r.json()
            assert sorted(got["user_ids"]) == ["member1", "reader1"]
            assert got["data"] == {"ext": {"writers": ["member1"]}}
            from nucllei.utils.ext_acl import ext_access

            assert ext_access("reader1", "groups/EMR/x") == "ro"
            assert ext_access("member1", "groups/EMR/x") == "rw"
            # 一覧 (GET /) も同じ形で返す (画面はここから読む)
            r = self.fast_api_client.get(self.create_url("/"))
            emr = next(x for x in r.json() if x["name"] == "EMR")
            assert emr["data"]["ext"] == {"writers": ["member1"]}
            # ext は writers だけ (他のキー・writers の欠けは 422 で、何も変えない)
            for bad_ext in (
                {"write": "members", "writers": ["member1", "reader1"]},
                {"write": "listed"},
                {},
                {"writers": "member1"},
            ):
                r = self.fast_api_client.post(
                    self.create_url(f"/id/{g.id}/update"),
                    json={**body, "ext": bad_ext},
                )
                assert r.status_code == 422, bad_ext
            assert _group("EMR").data == {"ext": {"writers": ["member1"]}}

    def test_api_members_added_without_ext_are_read_only(self):
        """ext を送らずに user_ids でメンバーを足す API 呼び出し: 足した人は読むだけ、既存の writers は残る。"""
        from test.util.mock_user import mock_webui_user_strict
        from nucllei.models.users import Users
        from nucllei.utils.ext_acl import ext_access

        for uid in ("member1", "new1"):
            Users.insert_new_user(uid, uid, f"{uid}@x.local", role="user")
        g = _group("EMR")
        with mock_webui_user_strict(id="admin1", role="admin"):
            r = self.fast_api_client.post(
                self.create_url(f"/id/{g.id}/update"),
                json={
                    "name": "EMR",
                    "description": "",
                    "user_ids": ["member1", "new1"],
                },
            )
        assert r.status_code == 200, r.text
        assert r.json()["data"] == {"ext": {"writers": ["member1"]}}
        assert ext_access("member1", "groups/EMR/x") == "rw"
        assert ext_access("new1", "groups/EMR/x") == "ro"

    # ---- OAuth のグループ同期 ----------------------------------------------------
    def _oauth_sync(self, user_id, groups):
        from types import SimpleNamespace

        from nucllei.utils import oauth

        mgr = oauth.OAuthManager.__new__(oauth.OAuthManager)
        cfg = SimpleNamespace(OAUTH_GROUPS_CLAIM="groups")
        orig = oauth.auth_manager_config
        oauth.auth_manager_config = cfg
        try:
            mgr.update_user_groups(
                SimpleNamespace(id=user_id), {"groups": groups}, default_permissions={}
            )
        finally:
            oauth.auth_manager_config = orig

    def test_oauth_sync_adds_members_read_only(self):
        from nucllei.utils.ext_acl import ext_access

        # 同期で入ったメンバーは読むだけ。既存の writers は残る
        self._oauth_sync("new1", ["CHART"])
        g = _group("CHART")
        assert "new1" in g.user_ids
        assert g.data["ext"] == {"writers": ["writer1"]}
        assert ext_access("new1", "groups/CHART/a") == "ro"
        assert ext_access(WRITER, "groups/CHART/a") == "rw"

    def test_oauth_sync_readded_writer_comes_back_read_only(self):
        from nucllei.utils.ext_acl import ext_access

        # 外れたら writers からも落ち、入れ直しても読むだけ
        self._oauth_sync("writer1", ["EMR"])
        g = _group("CHART")
        assert "writer1" not in g.user_ids
        assert g.data["ext"] == {"writers": []}
        assert ext_access(WRITER, "groups/CHART/a") == "none"
        self._oauth_sync("writer1", ["CHART"])
        g = _group("CHART")
        assert "writer1" in g.user_ids
        assert g.data["ext"] == {"writers": []}
        assert ext_access(WRITER, "groups/CHART/a") == "ro"


# ---------------------------------------------------------------------------
# migration 31221e5f4d38 (既存グループの data.ext を {"writers": …} に揃える)
# ---------------------------------------------------------------------------
def _writers_migration():
    import importlib.util
    import pathlib

    versions = pathlib.Path(__file__).resolve().parents[4] / "migrations" / "versions"
    path = next(versions.glob("*_31221e5f4d38_*.py"))
    spec = importlib.util.spec_from_file_location("migration_31221e5f4d38", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "data, user_ids, want",
    [
        # ext の無いグループ (メンバー全員が書けた) — 全員を writers に
        (None, ["a", "b"], {"ext": {"writers": ["a", "b"]}}),
        ({}, ["a"], {"ext": {"writers": ["a"]}}),
        ({"other": 1}, ["a"], {"other": 1, "ext": {"writers": ["a"]}}),
        ({"ext": None}, ["a"], {"ext": {"writers": ["a"]}}),
        ({"ext": "x"}, ["a"], {"ext": {"writers": ["a"]}}),
        ({"ext": {}}, ["a"], {"ext": {"writers": ["a"]}}),
        # members (メンバー全員が書けた) — 保存されていた writers は見ない
        (
            {"ext": {"write": "members", "writers": []}, "k": 1},
            ["a", "b"],
            {"ext": {"writers": ["a", "b"]}, "k": 1},
        ),
        (
            {"ext": {"write": None, "writers": ["a"]}},
            ["a", "b"],
            {"ext": {"writers": ["a", "b"]}},
        ),
        # listed (指定されたメンバーだけ書けた) — writers ∩ user_ids
        (
            {"ext": {"write": "listed", "writers": ["b", "ghost", "b", 3]}},
            ["a", "b"],
            {"ext": {"writers": ["b"]}},
        ),
        ({"ext": {"write": "listed", "writers": []}}, ["a"], {"ext": {"writers": []}}),
        ({"ext": {"write": "listed"}}, ["a"], {"ext": {"writers": []}}),
        # 知らない値は listed と同じ扱いだった
        (
            {"ext": {"write": "everyone", "writers": ["a"]}},
            ["a", "b"],
            {"ext": {"writers": ["a"]}},
        ),
        # user_ids の欠け・重複
        (None, None, {"ext": {"writers": []}}),
        (None, ["a", "a", 1, ""], {"ext": {"writers": ["a"]}}),
        ("junk", ["a"], {"ext": {"writers": ["a"]}}),
    ],
)
def test_migration_writers_keep_who_could_write(data, user_ids, want):
    m = _writers_migration()
    assert m.migrate_data(data, user_ids) == want


def test_migration_upgrade_rewrites_every_group():
    """sqlite に migration 直前の group 表を作って upgrade を当てる (JSON は文字列で持つ)。"""
    import json

    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    m = _writers_migration()
    rows = [
        ("g1", None, ["a", "b"]),
        ("g2", {"ext": {"write": "members", "writers": []}}, ["a"]),
        ("g3", {"ext": {"write": "listed", "writers": ["b", "x"]}, "k": 1}, ["a", "b"]),
        ("g4", None, None),
    ]
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                'CREATE TABLE "group" (id TEXT PRIMARY KEY, data JSON, user_ids JSON)'
            )
        )
        for gid, data, ids in rows:
            conn.execute(
                sa.text(
                    'INSERT INTO "group" (id, data, user_ids) VALUES (:id, :d, :u)'
                ),
                {
                    "id": gid,
                    "d": None if data is None else json.dumps(data),
                    "u": None if ids is None else json.dumps(ids),
                },
            )
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            m.upgrade()
        got = {
            gid: json.loads(d)
            for gid, d in conn.execute(sa.text('SELECT id, data FROM "group"')).all()
        }
    assert got == {
        "g1": {"ext": {"writers": ["a", "b"]}},
        "g2": {"ext": {"writers": ["a"]}},
        "g3": {"ext": {"writers": ["b"]}, "k": 1},
        "g4": {"ext": {"writers": []}},
    }


class TestWritersMigrationOnPostgres(AbstractPostgresTest):
    """実 DB (PostgreSQL の JSON 列) で upgrade を当て、判定が前と同じになること。"""

    BASE_PATH = "/api/v1/groups"

    def test_upgrade_keeps_who_can_write(self):
        from alembic.migration import MigrationContext
        from alembic.operations import Operations

        from nucllei.internal.db import engine
        from nucllei.models.groups import GroupForm, Groups
        from nucllei.utils.ext_acl import ext_access

        def make(name, ids, data):
            g = Groups.insert_new_group("admin1", GroupForm(name=name, description=""))
            _set_data(name, data)
            from nucllei.internal.db import get_db
            from nucllei.models.groups import Group

            with get_db() as db:
                db.query(Group).filter_by(id=g.id).update({"user_ids": ids})
                db.commit()

        make("PLAIN", ["u1", "u2"], None)
        make("MEMBERS", ["u1", "u2"], {"ext": {"write": "members", "writers": []}})
        make(
            "LISTED", ["u1", "u2"], {"ext": {"write": "listed", "writers": ["u2", "x"]}}
        )
        with engine.begin() as conn:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                _writers_migration().upgrade()
        assert _group("PLAIN").data == {"ext": {"writers": ["u1", "u2"]}}
        assert _group("MEMBERS").data == {"ext": {"writers": ["u1", "u2"]}}
        assert _group("LISTED").data == {"ext": {"writers": ["u2"]}}
        assert ext_access("u1", "groups/PLAIN/a") == "rw"
        assert ext_access("u2", "groups/MEMBERS/a") == "rw"
        assert ext_access("u1", "groups/LISTED/a") == "ro"
        assert ext_access("u2", "groups/LISTED/a") == "rw"


@pytest.mark.parametrize(
    "data, want",
    [
        (
            {"ext": {"writers": ["a", "b"]}, "k": 1},
            {"ext": {"write": "listed", "writers": ["a", "b"]}, "k": 1},
        ),
        ({"ext": {"writers": []}}, {"ext": {"write": "listed", "writers": []}}),
        # ext の無いグループ (誰も書けない) も listed の空にする — ひとつ前のコードの「無い = 全員」にしない
        (None, {"ext": {"write": "listed", "writers": []}}),
        ({"k": 1}, {"k": 1, "ext": {"write": "listed", "writers": []}}),
        ({"ext": {"writers": "a"}}, {"ext": {"write": "listed", "writers": []}}),
    ],
)
def test_migration_downgrade_keeps_who_can_write(data, want):
    m = _writers_migration()
    assert m.migrate_down_data(data) == want
    # 上げ直すと元の writers (メンバーとの積)
    if want["ext"]["writers"]:
        assert m.migrate_data(want, ["a", "b"])["ext"] == {"writers": ["a", "b"]}
