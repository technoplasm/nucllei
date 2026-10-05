"""access control(group 権限)の回帰テスト。

- v0.6.5 既存挙動(OR ベースの group_ids / user_ids)の固定
- llens(#66): ACL は read 一本・OR 合成のみ。旧 group_terms(AND 交差)は撤去済み —
  レガシーデータに残っていても無視される(権限を与えない)ことを固定する。
"""

from test.util.abstract_integration_test import AbstractPostgresTest


class TestAccessControl(AbstractPostgresTest):
    BASE_PATH = "/api/v1/groups"

    def setup_method(self):
        super().setup_method()
        from nucllei.models.groups import Groups
        from nucllei.models.users import Users

        self.groups = Groups
        self.users = Users

        # 二軸の group(診療科 × 職位)
        self.dept_naika = self._make_group("dept:内科")
        self.dept_geka = self._make_group("dept:外科")
        self.rank_bucho = self._make_group("rank:部長")
        self.rank_ippan = self._make_group("rank:一般")

        # ユーザー: 内科部長 / 内科一般 / 外科部長
        self._make_user("naika-bucho")
        self._make_user("naika-ippan")
        self._make_user("geka-bucho")
        self._set_members(self.dept_naika, ["naika-bucho", "naika-ippan"])
        self._set_members(self.dept_geka, ["geka-bucho"])
        self._set_members(self.rank_bucho, ["naika-bucho", "geka-bucho"])
        self._set_members(self.rank_ippan, ["naika-ippan"])

    def _make_group(self, name):
        from nucllei.models.groups import GroupForm

        group = self.groups.insert_new_group("1", GroupForm(name=name, description=""))
        assert group is not None
        return group.id

    def _make_user(self, user_id):
        self.users.insert_new_user(
            id=user_id,
            name=user_id,
            email=f"{user_id}@hospital.example",
            role="user",
        )

    def _set_members(self, group_id, user_ids):
        from nucllei.models.groups import GroupUpdateForm

        group = self.groups.get_group_by_id(group_id)
        self.groups.update_group_by_id(
            group_id,
            GroupUpdateForm(
                name=group.name,
                description=group.description,
                user_ids=user_ids,
            ),
        )

    def test_or_semantics_unchanged(self):
        """v0.6.5 既存の OR 判定(いずれかの group に所属で許可)を固定。"""
        from nucllei.utils.access_control import has_access

        access_control = {"read": {"group_ids": [self.dept_naika], "user_ids": []}}
        assert has_access("naika-bucho", "read", access_control) is True
        assert has_access("naika-ippan", "read", access_control) is True
        assert has_access("geka-bucho", "read", access_control) is False

    def test_none_access_control_is_public_read(self):
        from nucllei.utils.access_control import has_access

        assert has_access("naika-ippan", "read", None) is True
        assert has_access("naika-ippan", "write", None) is False

    def test_or_across_multiple_groups(self):
        """複数 group 所属は OR 合成 — どれか一つの所属 group が許可されていれば可。"""
        from nucllei.utils.access_control import has_access

        access_control = {"read": {"group_ids": [self.rank_bucho], "user_ids": []}}
        # naika-bucho は dept:内科 と rank:部長 の両方に所属。rank 側だけの許可で read 可
        assert has_access("naika-bucho", "read", access_control) is True
        assert has_access("geka-bucho", "read", access_control) is True
        assert has_access("naika-ippan", "read", access_control) is False

    def test_user_ids_grant(self):
        """user_ids の直接指定でも許可される(group と OR)。"""
        from nucllei.utils.access_control import has_access

        access_control = {"read": {"group_ids": [], "user_ids": ["naika-ippan"]}}
        assert has_access("naika-ippan", "read", access_control) is True
        assert has_access("naika-bucho", "read", access_control) is False

    def test_legacy_group_terms_data_is_ignored(self):
        """#66: 撤去済み group_terms がレガシーデータに残っていても権限を与えない。"""
        from nucllei.utils.access_control import has_access

        access_control = {
            "read": {
                "group_ids": [],
                "user_ids": [],
                "group_terms": [[self.dept_naika, self.rank_bucho]],
            }
        }
        assert has_access("naika-bucho", "read", access_control) is False

    def test_get_users_with_access_or_composition(self):
        """get_users_with_access は user_ids と group 所属者の和集合。"""
        from nucllei.utils.access_control import get_users_with_access

        access_control = {
            "read": {"group_ids": [self.dept_naika], "user_ids": ["geka-bucho"]}
        }
        users = get_users_with_access("read", access_control)
        assert {u.id for u in users} == {"naika-bucho", "naika-ippan", "geka-bucho"}


class TestNullGroupPermissions(AbstractPostgresTest):
    """group の permissions が null でもメンバーの API が 500 にならない。

    insert_new_group は form の None を exclude して保存するため、permissions を
    省略して作った group は DB 上 null になっていた。権限合成(get_permissions /
    has_permission)が null.items() で落ち、メンバーの session 取得等が全て 500。
    読み側は null を {} として扱い、作成・更新時に {} へ正規化する。
    """

    BASE_PATH = "/api/v1/auths"

    def setup_method(self):
        super().setup_method()
        from nucllei.models.groups import Groups, GroupForm, GroupUpdateForm

        self.groups = Groups
        group = Groups.insert_new_group("1", GroupForm(name="g", description=""))
        self.group_id = group.id
        Groups.update_group_by_id(
            group.id, GroupUpdateForm(name="g", description="", user_ids=["member"])
        )

    def _force_null_permissions(self):
        # 正規化前に作られた行を再現(ORM 経由で直接 null を書く)
        from nucllei.internal.db import get_db
        from nucllei.models.groups import Group

        with get_db() as db:
            db.query(Group).filter_by(id=self.group_id).update({"permissions": None})
            db.commit()
        assert self.groups.get_group_by_id(self.group_id).permissions is None

    def test_insert_without_permissions_stores_empty_dict(self):
        assert self.groups.get_group_by_id(self.group_id).permissions == {}

    def test_update_normalizes_existing_null(self):
        from nucllei.models.groups import GroupUpdateForm

        self._force_null_permissions()
        self.groups.update_group_by_id(
            self.group_id, GroupUpdateForm(name="g2", description="")
        )
        group = self.groups.get_group_by_id(self.group_id)
        assert group.permissions == {}
        assert group.user_ids == ["member"]

    def test_permission_helpers_tolerate_null(self):
        from nucllei.utils.access_control import get_permissions, has_permission

        self._force_null_permissions()
        defaults = {"chat": {"file_upload": True}}
        assert get_permissions("member", defaults) == defaults
        assert has_permission("member", "chat.file_upload", defaults) is True
        assert has_permission("member", "chat.nonexistent", defaults) is False

    def test_member_session_endpoint_not_500(self):
        from test.util.mock_user import mock_webui_user

        self._force_null_permissions()
        with mock_webui_user(id="member"):
            response = self.fast_api_client.get(self.create_url(""))
        assert response.status_code == 200
