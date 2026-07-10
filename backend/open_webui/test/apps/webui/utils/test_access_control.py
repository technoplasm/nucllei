"""access control(group 権限)の回帰テスト。

- v0.6.5 既存挙動(OR ベースの group_ids / user_ids)の固定
- llens(#66): ACL は read 一本・OR 合成のみ。旧 group_terms(AND 交差)は撤去済み —
  レガシーデータに残っていても無視される(権限を与えない)ことを固定する。
"""

import time

from test.util.abstract_integration_test import AbstractPostgresTest


class TestAccessControl(AbstractPostgresTest):
    BASE_PATH = "/api/v1/groups"

    def setup_method(self):
        super().setup_method()
        from open_webui.models.groups import GroupForm, Groups
        from open_webui.models.users import Users

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
        from open_webui.models.groups import GroupForm

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
        from open_webui.models.groups import GroupUpdateForm

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
        from open_webui.utils.access_control import has_access

        access_control = {"read": {"group_ids": [self.dept_naika], "user_ids": []}}
        assert has_access("naika-bucho", "read", access_control) is True
        assert has_access("naika-ippan", "read", access_control) is True
        assert has_access("geka-bucho", "read", access_control) is False

    def test_none_access_control_is_public_read(self):
        from open_webui.utils.access_control import has_access

        assert has_access("naika-ippan", "read", None) is True
        assert has_access("naika-ippan", "write", None) is False

    def test_or_across_multiple_groups(self):
        """複数 group 所属は OR 合成 — どれか一つの所属 group が許可されていれば可。"""
        from open_webui.utils.access_control import has_access

        access_control = {"read": {"group_ids": [self.rank_bucho], "user_ids": []}}
        # naika-bucho は dept:内科 と rank:部長 の両方に所属。rank 側だけの許可で read 可
        assert has_access("naika-bucho", "read", access_control) is True
        assert has_access("geka-bucho", "read", access_control) is True
        assert has_access("naika-ippan", "read", access_control) is False

    def test_user_ids_grant(self):
        """user_ids の直接指定でも許可される(group と OR)。"""
        from open_webui.utils.access_control import has_access

        access_control = {"read": {"group_ids": [], "user_ids": ["naika-ippan"]}}
        assert has_access("naika-ippan", "read", access_control) is True
        assert has_access("naika-bucho", "read", access_control) is False

    def test_legacy_group_terms_data_is_ignored(self):
        """#66: 撤去済み group_terms がレガシーデータに残っていても権限を与えない。"""
        from open_webui.utils.access_control import has_access

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
        from open_webui.utils.access_control import get_users_with_access

        access_control = {
            "read": {"group_ids": [self.dept_naika], "user_ids": ["geka-bucho"]}
        }
        users = get_users_with_access("read", access_control)
        assert {u.id for u in users} == {"naika-bucho", "naika-ippan", "geka-bucho"}
