"""グループ名の一意性と安全な文字の制約(#151)の回帰。CORE: group 権限。

グループ名は `/ext/groups/<グループ名>/` のディレクトリ名になる(#152)ため、

- 正規化(NFKC → 前後の空白除去 → casefold)した名前の重複は 409
- パスとして危険な名前は 400
- 検証は作成時・改名時だけ(名前を変えない更新は #151 以前の名前でも通す)
- DB の unique index(name_key)は model を直接叩いても効き、None に化けない
"""

import pytest

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user, mock_webui_user_strict

from nucllei.utils.group_names import (
    GroupNameError,
    normalize_group_name_key,
    validate_group_name,
)

# ---- 検証関数(DB 不要)----


@pytest.mark.parametrize(
    "a,b",
    [
        ("Naika", "naika"),
        ("ＮＡＩＫＡ", "naika"),  # 全角
        ("  内科  ", "内科"),
        ("　内科　", "内科"),  # 全角空白
        ("ﾅｲｶ", "ナイカ"),  # 半角カナ
        ("Straße", "STRASSE"),  # casefold
    ],
)
def test_normalize_key_equates(a, b):
    assert normalize_group_name_key(a) == normalize_group_name_key(b)


@pytest.mark.parametrize(
    "name",
    [
        "",
        "   ",
        "a/b",
        "a\\b",
        "a:b",
        "a\x00b",
        "a\nb",
        "a\tb",
        "a\x7fb",
        "a​b",  # ゼロ幅スペース (Cf)
        "a‮b",  # RLO (Cf)
        "a﻿b",  # BOM (Cf)
        "a b",  # 行区切り (Zl)
        "a\ud800b",  # サロゲート (Cs)
        ".",
        "..",
        ".hidden",
        " .hidden",  # 前後空白を落とした後で先頭 .
        "a" * 256,
        "あ" * 86,  # 258 バイト
    ],
)
def test_validate_rejects(name):
    with pytest.raises(GroupNameError):
        validate_group_name(name)


@pytest.mark.parametrize(
    "name,stored",
    [
        ("内科", "内科"),
        ("  rank 部長  ", "rank 部長"),
        ("a.b", "a.b"),
        ("a..", "a.."),
        ("rank：部長", "rank：部長"),  # 全角コロンはファイル名として無害
        ("a" * 255, "a" * 255),
        ("あ" * 85, "あ" * 85),  # 255 バイト
    ],
)
def test_validate_accepts_and_strips(name, stored):
    assert validate_group_name(name) == stored


# ---- API ----


class TestGroupNameApi(AbstractPostgresTest):
    BASE_PATH = "/api/v1/groups"

    def _create(self, name):
        with mock_webui_user(id="1", role="admin"):
            return self.fast_api_client.post(
                self.create_url("/create"), json={"name": name, "description": ""}
            )

    def _update(self, gid, name, **extra):
        with mock_webui_user(id="1", role="admin"):
            return self.fast_api_client.post(
                self.create_url(f"/id/{gid}/update"),
                json={"name": name, "description": "", **extra},
            )

    def test_create_stores_name_as_entered_but_stripped(self):
        r = self._create("  Naika 内科  ")
        assert r.status_code == 200, r.text
        assert r.json()["name"] == "Naika 内科"

    def test_create_duplicate_normalized_is_409(self):
        assert self._create("Naika").status_code == 200
        for dup in ("Naika", "naika", "ＮＡＩＫＡ", " naika "):
            r = self._create(dup)
            assert r.status_code == 409, (dup, r.text)
            assert "同じ名前" in r.json()["detail"]

    @pytest.mark.parametrize(
        "name", ["a/b", "a\\b", "a:b", "..", ".x", "", "a​b", "a" * 256]
    )
    def test_create_dangerous_name_is_400(self, name):
        r = self._create(name)
        assert r.status_code == 400, (name, r.text)
        assert isinstance(r.json()["detail"], str) and r.json()["detail"]

    def test_rename_to_existing_is_409(self):
        self._create("Geka")
        gid = self._create("Naika").json()["id"]
        r = self._update(gid, "GEKA")
        assert r.status_code == 409, r.text

    def test_rename_self_case_change_ok(self):
        gid = self._create("Naika").json()["id"]
        r = self._update(gid, "NAIKA")
        assert r.status_code == 200, r.text
        assert r.json()["name"] == "NAIKA"
        # 旧名の key は解放されていない(同じ key のまま)— 別グループで取れない
        assert self._create("naika").status_code == 409

    def test_rename_frees_old_key(self):
        gid = self._create("Naika").json()["id"]
        assert self._update(gid, "Geka").status_code == 200
        assert self._create("naika").status_code == 200

    def test_rename_to_dangerous_is_400(self):
        gid = self._create("Naika").json()["id"]
        r = self._update(gid, "a/b")
        assert r.status_code == 400, r.text

    def test_update_without_rename_skips_validation(self):
        # #151 以前に作られた不正な名前でもメンバー・権限の編集はできる
        from nucllei.models.groups import GroupForm, Groups

        legacy = Groups.insert_new_group(
            "1", GroupForm(name="rank:部長", description="")
        )
        r = self._update(legacy.id, "rank:部長", permissions={"chat": {"edit": False}})
        assert r.status_code == 200, r.text
        assert r.json()["permissions"] == {"chat": {"edit": False}}

    def test_update_missing_group_is_404(self):
        r = self._update("no-such-id", "x")
        assert r.status_code == 404

    def test_non_admin_cannot_create_or_rename(self):
        gid = self._create("Naika").json()["id"]
        with mock_webui_user_strict(id="9", role="user"):
            assert (
                self.fast_api_client.post(
                    self.create_url("/create"), json={"name": "x", "description": ""}
                ).status_code
                == 401
            )
            assert (
                self.fast_api_client.post(
                    self.create_url(f"/id/{gid}/update"),
                    json={"name": "y", "description": ""},
                ).status_code
                == 401
            )


class TestGroupNameKeyConstraint(AbstractPostgresTest):
    """router の事前チェックを経ない経路でも unique index が効き、None に化けない。"""

    BASE_PATH = "/api/v1/groups"

    def test_model_insert_duplicate_raises_conflict(self):
        from nucllei.models.groups import GroupForm, Groups
        from nucllei.utils.group_names import GroupNameConflictError

        g = Groups.insert_new_group("1", GroupForm(name="Naika", description=""))
        assert g is not None
        with pytest.raises(GroupNameConflictError):
            Groups.insert_new_group("1", GroupForm(name="ｎａｉｋａ", description=""))
        # セッションが壊れていない(後続の操作が通る)
        assert Groups.insert_new_group("1", GroupForm(name="Geka", description=""))

    def test_model_update_duplicate_raises_conflict(self):
        from nucllei.models.groups import GroupForm, GroupUpdateForm, Groups
        from nucllei.utils.group_names import GroupNameConflictError

        Groups.insert_new_group("1", GroupForm(name="Naika", description=""))
        g = Groups.insert_new_group("1", GroupForm(name="Geka", description=""))
        with pytest.raises(GroupNameConflictError):
            Groups.update_group_by_id(
                g.id, GroupUpdateForm(name="NAIKA", description="")
            )
        assert Groups.get_group_by_id(g.id).name == "Geka"

    def test_race_after_precheck_is_409(self, monkeypatch):
        # 事前チェックをすり抜けた(並行作成)場合も DB 制約違反が 409 になる
        from nucllei.models.groups import GroupForm, Groups

        Groups.insert_new_group("1", GroupForm(name="Naika", description=""))
        monkeypatch.setattr(Groups, "get_group_by_name_key", lambda key: None)
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.post(
                self.create_url("/create"), json={"name": "naika", "description": ""}
            )
        assert r.status_code == 409, r.text


# ---- migration a5c8e2f17b49(既存行の name_key の埋め方)----


def _migration():
    import importlib.util
    import pathlib

    versions = pathlib.Path(__file__).resolve().parents[4] / "migrations" / "versions"
    path = next(versions.glob("*_a5c8e2f17b49_*.py"))
    spec = importlib.util.spec_from_file_location("migration_a5c8e2f17b49", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_upgrade(rows):
    """sqlite に migration 直前の group 表を作って upgrade を当て、name_key を返す。"""
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    m = _migration()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text('CREATE TABLE "group" (id TEXT PRIMARY KEY, name TEXT)'))
        for gid, name in rows:
            conn.execute(
                sa.text('INSERT INTO "group" (id, name) VALUES (:id, :name)'),
                {"id": gid, "name": name},
            )
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            m.upgrade()
        return dict(conn.execute(sa.text('SELECT id, name_key FROM "group"')).all())


def test_migration_backfills_name_key():
    keys = _run_upgrade([("1", "Naika"), ("2", " ＧＥＫＡ "), ("3", None)])
    assert keys == {"1": "naika", "2": "geka", "3": None}
    # アプリ側と同じ式
    m = _migration()
    for s in ("Naika", " ＧＥＫＡ ", "ﾅｲｶ", "Straße"):
        assert m.name_key(s) == normalize_group_name_key(s)


def test_migration_fails_on_existing_duplicates():
    # 重複検出はしない(ken 判断)— unique index の作成で落ちるのは許容
    import sqlalchemy as sa

    with pytest.raises(sa.exc.IntegrityError):
        _run_upgrade([("1", "Naika"), ("2", "naika")])
