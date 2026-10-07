"""valves/update の部分更新 (utils/valves.merge_valves_update)。"""

import pytest
from pydantic import BaseModel, ValidationError

from nucllei.utils.valves import merge_valves_update


class Valves(BaseModel):
    url_max_mb: int = 50
    binary_hint: str = "既定"
    image_hint: str = "既定"


def test_keys_not_sent_keep_stored_values():
    # hint だけを送っても、以前に入れた url_max_mb は既定に戻らない
    stored = {"url_max_mb": 100}
    assert merge_valves_update(stored, {"binary_hint": "変換して"}, Valves) == {
        "url_max_mb": 100,
        "binary_hint": "変換して",
    }


def test_null_resets_to_default():
    stored = {"url_max_mb": 100, "binary_hint": "変換して"}
    assert merge_valves_update(stored, {"url_max_mb": None}, Valves) == {
        "binary_hint": "変換して"
    }


def test_full_form_from_ui_replaces_everything():
    # 管理画面は全フィールドを送る (未設定 = null) — 従来どおり全体の置き換え
    stored = {"url_max_mb": 100, "binary_hint": "変換して"}
    form = {"url_max_mb": None, "binary_hint": None, "image_hint": "見る"}
    assert merge_valves_update(stored, form, Valves) == {"image_hint": "見る"}


def test_stale_keys_are_dropped():
    assert merge_valves_update({"removed_valve": 1}, {}, Valves) == {}


def test_empty_stored():
    assert merge_valves_update(None, {"url_max_mb": 10}, Valves) == {"url_max_mb": 10}


def test_invalid_value_raises():
    with pytest.raises(ValidationError):
        merge_valves_update({}, {"url_max_mb": "abc"}, Valves)
