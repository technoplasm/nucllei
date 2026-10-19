"""プロンプト変数の展開(utils/task.py prompt_template)と {{BASE_MODEL_ID}} /
{{USER_ROLE}} / {{USER_GROUPS}}。

{{BASE_MODEL_ID}}(実際に推論する base model の id)を、preset のシステムプロンプト
(utils/payload.py apply_model_system_prompt_to_body)に差し込めることを固定する。
CORE(OpenAI 互換接続)に乗る経路の回帰。

{{USER_ROLE}} は role の値そのまま、{{USER_GROUPS}} は所属グループ名(casefold → 元の名前
の順)を ", " でつないだもの。グループ照会は変数があるときだけ・1 呼び出し 1 回。
"""

import re
import types

import pytest

from nucllei.utils.payload import apply_model_system_prompt_to_body
from nucllei.utils.task import (
    prompt_template,
    resolve_base_model_id,
    title_generation_template,
    user_group_names,
)
from test.util.abstract_integration_test import AbstractPostgresTest


def test_base_model_id_is_expanded():
    out = prompt_template(
        "{{BASE_MODEL_ID}} / {{BASE_MODEL_ID}}", base_model_id="qwen3.8-flash-next"
    )
    assert out == "qwen3.8-flash-next / qwen3.8-flash-next"


def test_base_model_id_falls_back_to_unknown():
    # モデル情報の無い呼び出しでも変数を生のまま残さない({{USER_NAME}} と同じ流儀)
    assert prompt_template("{{BASE_MODEL_ID}}") == "Unknown"


def test_unknown_variables_are_left_as_is():
    # 存在しない変数は展開せず文字のまま残す
    assert prompt_template("{{MODEL_NAME}}", base_model_id="b") == "{{MODEL_NAME}}"


def test_existing_variables_unchanged():
    out = prompt_template(
        "{{CURRENT_DATE}}|{{CURRENT_TIME}}|{{CURRENT_DATETIME}}|{{CURRENT_WEEKDAY}}"
        "|{{USER_NAME}}|{{USER_LOCATION}}",
        "Taro",  # 位置引数(execution_resume の呼び方)
        "Sapporo",
    )
    date, time, datetime_, weekday, name, location = out.split("|")
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", date)
    assert re.fullmatch(r"\d{2}:\d{2}:\d{2} [AP]M", time)
    assert datetime_.startswith(date)
    # 曜日は日本語で固定(ロケールに依らない — 2026-10-19 llens 監査)
    assert weekday in (
        "月曜日",
        "火曜日",
        "水曜日",
        "木曜日",
        "金曜日",
        "土曜日",
        "日曜日",
    )
    assert (name, location) == ("Taro", "Sapporo")

    assert prompt_template("{{USER_NAME}}|{{USER_LOCATION}}") == "Unknown|Unknown"


def test_resolve_base_model_id_preset():
    model = {
        "id": "llens",
        "name": "LLENS",
        "info": {"id": "llens", "base_model_id": "qwen3.8-flash-next"},
    }
    assert resolve_base_model_id(model) == "qwen3.8-flash-next"


def test_resolve_base_model_id_bare_model():
    # base を持たない素のモデル: そのモデル自身の id
    assert resolve_base_model_id({"id": "raw-model", "name": "Raw"}) == "raw-model"


def test_resolve_base_model_id_without_model():
    # 取れない → None(prompt_template が Unknown に落とす)
    for model in (None, {}, {"info": {"meta": {}}}):
        assert resolve_base_model_id(model) is None


def _system_of(form_data):
    return form_data["messages"][0]["content"]


SYSTEM = "base={{BASE_MODEL_ID}} user={{USER_NAME}}"


def test_system_prompt_preset():
    user = types.SimpleNamespace(name="Taro", info=None)
    model_info = types.SimpleNamespace(
        id="llens", name="LLENS", base_model_id="qwen3.8-flash-next"
    )
    form_data = apply_model_system_prompt_to_body(
        {"system": SYSTEM},
        {"messages": [{"role": "user", "content": "{{BASE_MODEL_ID}}"}]},
        None,
        user,
        model_info,
    )
    assert form_data["messages"][0]["role"] == "system"
    assert _system_of(form_data) == "base=qwen3.8-flash-next user=Taro"
    # 利用者の本文は展開しない
    assert form_data["messages"][1] == {"role": "user", "content": "{{BASE_MODEL_ID}}"}


def test_system_prompt_bare_model():
    model_info = types.SimpleNamespace(id="raw-model", name="Raw", base_model_id=None)
    form_data = apply_model_system_prompt_to_body(
        {"system": SYSTEM}, {"messages": []}, None, None, model_info
    )
    assert _system_of(form_data) == "base=raw-model user=Unknown"


def test_system_prompt_without_model_info():
    form_data = apply_model_system_prompt_to_body(
        {"system": SYSTEM}, {"messages": []}, None, None
    )
    assert _system_of(form_data) == "base=Unknown user=Unknown"


# ---- {{USER_ROLE}} / {{USER_GROUPS}} ----


def _group(name, user_ids):
    return types.SimpleNamespace(id=f"g-{name}", name=name, user_ids=user_ids)


@pytest.fixture
def fake_groups(monkeypatch):
    """Groups.get_groups_by_member_id を差し替え、呼び出しを数える。

    実物と同じく LIKE 相当の粗い結果(所属していないグループも混ざりうる)を返す。
    """
    from nucllei.models.groups import Groups

    state = {"groups": [], "calls": []}

    def fake(user_id):
        state["calls"].append(user_id)
        return list(state["groups"])

    monkeypatch.setattr(Groups, "get_groups_by_member_id", fake)
    return state


def test_user_role_is_expanded():
    assert prompt_template("[{{USER_ROLE}}]", user_role="admin") == "[admin]"
    assert prompt_template("[{{USER_ROLE}}]", user_role="user") == "[user]"
    # user 不明は {{USER_NAME}} と同じく Unknown
    assert prompt_template("[{{USER_ROLE}}]") == "[Unknown]"


def test_user_groups_sorted_and_joined(fake_groups):
    fake_groups["groups"] = [
        _group("病理", ["u1"]),
        _group("b-team", ["u1"]),
        _group("Lab", ["u1"]),
        _group("lab", ["u1"]),
        _group("Alpha", ["u1"]),
    ]
    out = prompt_template("[{{USER_GROUPS}}]", user_id="u1")
    # casefold で比べ、同値なら元の文字列(大文字が先)— DB の返し順に依らない
    assert out == "[Alpha, b-team, Lab, lab, 病理]"
    fake_groups["groups"].reverse()
    assert prompt_template("[{{USER_GROUPS}}]", user_id="u1") == out


def test_user_groups_rechecks_membership(fake_groups):
    # LIKE の誤ヒット(user_ids に "u1" を含まない)は入れない
    fake_groups["groups"] = [_group("mine", ["u1"]), _group("other", ["u10"])]
    assert prompt_template("{{USER_GROUPS}}", user_id="u1") == "mine"


def test_user_groups_empty(fake_groups):
    assert prompt_template("[{{USER_GROUPS}}]", user_id="u1") == "[]"
    # user 不明は照会せず空
    assert prompt_template("[{{USER_GROUPS}}]") == "[]"
    assert fake_groups["calls"] == ["u1"]


def test_user_groups_queried_once_and_only_when_present(fake_groups):
    fake_groups["groups"] = [_group("g", ["u1"])]
    prompt_template("{{USER_NAME}} {{USER_ROLE}}", user_id="u1", user_role="user")
    assert fake_groups["calls"] == []
    out = prompt_template("{{USER_GROUPS}} / {{USER_GROUPS}}", user_id="u1")
    assert out == "g / g"
    assert fake_groups["calls"] == ["u1"]


def test_system_prompt_role_and_groups(fake_groups):
    fake_groups["groups"] = [_group("b", ["u1"]), _group("a", ["u1"])]
    user = types.SimpleNamespace(id="u1", name="Taro", role="admin", info=None)
    form_data = apply_model_system_prompt_to_body(
        {"system": "{{USER_NAME}} {{USER_ROLE}} [{{USER_GROUPS}}]"},
        {"messages": []},
        None,
        user,
    )
    assert _system_of(form_data) == "Taro admin [a, b]"
    assert fake_groups["calls"] == ["u1"]


def test_title_generation_template_role_and_groups(fake_groups):
    fake_groups["groups"] = [_group("g", ["u1"])]
    user = types.SimpleNamespace(id="u1", name="Taro", role="user", info=None)
    out = title_generation_template(
        "{{USER_NAME}}/{{USER_ROLE}}/{{USER_GROUPS}}", [], user
    )
    assert out == "Taro/user/g"


class TestUserGroupsDb(AbstractPostgresTest):
    """実 DB の Groups で: admin でも所属しているグループだけ・名前順。"""

    BASE_PATH = "/api/v1/groups"

    def _group(self, name, user_ids):
        from nucllei.models.groups import GroupForm, GroupUpdateForm, Groups

        group = Groups.insert_new_group("1", GroupForm(name=name, description=""))
        Groups.update_group_by_id(
            group.id,
            GroupUpdateForm(name=name, description="", user_ids=user_ids),
        )

    def test_member_groups_only_even_for_admin(self):
        self._group("zeta", ["admin1", "u2"])
        self._group("Alpha", ["admin1"])
        self._group("others", ["u2"])
        # "admin1" を部分文字列に含む別 id(LIKE の誤ヒット候補)
        self._group("prefix", ["xadmin1x"])

        assert user_group_names("admin1") == ["Alpha", "zeta"]
        assert user_group_names("u2") == ["others", "zeta"]
        assert user_group_names("nobody") == []

        admin = types.SimpleNamespace(id="admin1", name="A", role="admin", info=None)
        form_data = apply_model_system_prompt_to_body(
            {"system": "{{USER_ROLE}}: {{USER_GROUPS}}"}, {"messages": []}, None, admin
        )
        assert _system_of(form_data) == "admin: Alpha, zeta"
