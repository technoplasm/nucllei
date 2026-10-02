"""プロンプト変数の展開(utils/task.py prompt_template)と {{BASE_MODEL_ID}}。

{{BASE_MODEL_ID}}(実際に推論する base model の id)を、preset のシステムプロンプト
(utils/payload.py apply_model_system_prompt_to_body)に差し込めることを固定する。
CORE(OpenAI 互換接続)に乗る経路の回帰。
"""

import re
import types

from nucllei.utils.payload import apply_model_system_prompt_to_body
from nucllei.utils.task import prompt_template, resolve_base_model_id


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
    assert weekday.endswith("day")
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
