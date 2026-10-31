"""admin から外した人をグループの `writers` からも外す回帰テスト。

グループの編集画面は admin の行の「書き込み」を外せない印で出し、そのまま
`data.ext.writers` に保存する。admin の間は role で全グループに書けるので見えないが、
降格すると保存された印だけが残って書けてしまう。role を変える経路はすべて
`Users.update_user_role_by_id` を通り、admin から外れたら全グループの `writers` から外す
(所属はそのまま。書かせるなら管理者が改めて印を付ける)。
"""

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user_strict


def _make_group(name, user_ids, writers):
    from nucllei.models.groups import GroupExtForm, GroupForm, GroupUpdateForm, Groups

    g = Groups.insert_new_group("first", GroupForm(name=name, description=""))
    Groups.update_group_by_id(
        g.id,
        GroupUpdateForm(
            name=name,
            description="",
            user_ids=user_ids,
            ext=GroupExtForm(writers=writers),
        ),
    )
    return g.id


def _group(group_id):
    from nucllei.models.groups import Groups

    return Groups.get_group_by_id(group_id)


class TestAdminDemotionDropsWriters(AbstractPostgresTest):
    BASE_PATH = "/api/v1/users"

    def setup_method(self):
        super().setup_method()
        from nucllei.models.users import Users

        # 最初のユーザーは role を変えられない (users.update_user_role) ので別に置く
        for uid, role in (
            ("first", "admin"),
            ("acting", "admin"),
            ("admin2", "admin"),
            ("writer1", "user"),
        ):
            Users.insert_new_user(
                id=uid,
                name=uid,
                email=f"{uid}@nucllei.local",
                profile_image_url="/user.png",
                role=role,
            )
        # 同じ秒に作ると created_at が並ぶので、最初のユーザーを確定させる
        Users.update_user_by_id("first", {"created_at": 1})
        self.chart = _make_group("CHART", ["writer1", "admin2"], ["admin2", "writer1"])
        self.other = _make_group("OTHER", ["admin2"], ["admin2"])
        self.plain = _make_group("PLAIN", ["writer1"], ["writer1"])

    def _set_role(self, user_id, role):
        with mock_webui_user_strict(id="acting", role="admin"):
            return self.fast_api_client.post(
                self.create_url("/update/role"), json={"id": user_id, "role": role}
            )

    def test_demotion_drops_writers_keeps_membership(self):
        from nucllei.utils.ext_acl import ext_access, group_writers

        admin2 = {"id": "admin2", "role": "admin"}
        assert ext_access(admin2, "groups/CHART/a.md") == "rw"

        response = self._set_role("admin2", "user")
        assert response.status_code == 200, response.text
        assert response.json()["role"] == "user"

        chart, other = _group(self.chart), _group(self.other)
        assert group_writers(chart) == ["writer1"]
        assert group_writers(other) == []
        assert chart.data["ext"] == {"writers": ["writer1"]}
        # 所属は残る (読むだけになる)
        assert "admin2" in chart.user_ids and "admin2" in other.user_ids
        demoted = {"id": "admin2", "role": "user"}
        assert ext_access(demoted, "groups/CHART/a.md") == "ro"
        assert ext_access(demoted, "groups/OTHER/a.md") == "ro"
        # 他の人の writers は触らない
        assert group_writers(_group(self.plain)) == ["writer1"]

    def test_every_non_admin_role_counts_as_demotion(self):
        from nucllei.utils.ext_acl import group_writers

        for role in ("pending", "disabled"):
            from nucllei.models.groups import GroupExtForm, GroupUpdateForm, Groups
            from nucllei.models.users import Users

            Users.update_user_role_by_id("admin2", "admin")
            Groups.update_group_by_id(
                self.other,
                GroupUpdateForm(
                    name="OTHER", description="", ext=GroupExtForm(writers=["admin2"])
                ),
            )
            response = self._set_role("admin2", role)
            assert response.status_code == 200, response.text
            assert group_writers(_group(self.other)) == [], role

    def test_promotion_back_does_not_restore(self):
        from nucllei.utils.ext_acl import ext_access, group_writers

        assert self._set_role("admin2", "user").status_code == 200
        assert self._set_role("admin2", "admin").status_code == 200
        assert group_writers(_group(self.chart)) == ["writer1"]
        # admin の間は role で書ける
        assert ext_access({"id": "admin2", "role": "admin"}, "groups/CHART/a") == "rw"

    def test_non_admin_role_changes_keep_writers(self):
        from nucllei.utils.ext_acl import group_writers

        # admin からの降格でなければ writers は触らない
        assert self._set_role("writer1", "admin").status_code == 200
        assert group_writers(_group(self.plain)) == ["writer1"]
        assert self._set_role("admin2", "admin").status_code == 200
        assert group_writers(_group(self.chart)) == ["admin2", "writer1"]

    def test_model_path_used_by_oauth(self):
        """OAuth の role 管理 (utils/oauth.py) も `Users.update_user_role_by_id` を呼ぶ。"""
        from nucllei.models.users import Users
        from nucllei.utils.ext_acl import group_writers

        user = Users.update_user_role_by_id("admin2", "user")
        assert user is not None and user.role == "user"
        assert group_writers(_group(self.chart)) == ["writer1"]
        assert group_writers(_group(self.other)) == []

    def test_requires_admin(self):
        with mock_webui_user_strict(id="writer1", role="user"):
            response = self.fast_api_client.post(
                self.create_url("/update/role"), json={"id": "admin2", "role": "user"}
            )
        assert response.status_code in (401, 403)
        from nucllei.utils.ext_acl import group_writers

        assert group_writers(_group(self.chart)) == ["admin2", "writer1"]
