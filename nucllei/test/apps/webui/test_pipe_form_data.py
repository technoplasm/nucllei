"""pipe 経路(generate_function_chat_completion)は呼び手の form_data を書き換えない。

middleware の tool ループは初回に渡した form_data を追撃・計測でも使う。旧実装は
pipe 経路が model を base_model_id へ、messages を preset system 込みへ、params を
preset 値へ in-place で書き換えていたため、pipe preset の追撃が base pipe 宛てになり
preset の params(temperature 等)が 2 回目以降の呼び出しで落ちていた
(監査 2026-10-05: 1 回目 temperature=0.3 → 追撃 None)。
"""

import asyncio
import types

from nucllei import functions


class _Pipe:
    def __init__(self):
        self.bodies = []

    def pipe(self, body: dict):
        self.bodies.append(body)
        return "ok"


def test_pipe_preset_does_not_mutate_caller_form_data(monkeypatch):
    module = _Pipe()
    model_info = types.SimpleNamespace(
        id="pipepreset",
        base_model_id="basepipe",
        params=types.SimpleNamespace(
            model_dump=lambda: {"system": "【PRESET-SYS】", "temperature": 0.3}
        ),
    )
    monkeypatch.setattr(
        functions.Models,
        "get_model_by_id",
        lambda model_id: model_info if model_id == "pipepreset" else None,
    )
    monkeypatch.setattr(
        functions, "get_function_module_by_id", lambda request, pipe_id: module
    )
    monkeypatch.setattr(functions, "get_tools", lambda *args, **kwargs: {})

    user = types.SimpleNamespace(
        id="1", name="u", email="u@example.invalid", role="user", info=None
    )
    form_data = {
        "model": "pipepreset",
        "stream": False,
        "messages": [{"role": "user", "content": "q"}],
        "metadata": {"chat_id": None, "message_id": None},
    }
    snapshot = {
        **form_data,
        "messages": [dict(m) for m in form_data["messages"]],
        "metadata": dict(form_data["metadata"]),
    }

    for _ in range(2):  # 初回 + 追撃(同じ form_data で 2 回)
        asyncio.run(
            functions.generate_function_chat_completion(
                types.SimpleNamespace(), form_data, user=user, models={}
            )
        )

    assert form_data == snapshot
    # 2 回とも preset 経由(base pipe 宛て・preset の params / system 1 枚)
    assert len(module.bodies) == 2
    for body in module.bodies:
        assert body["model"] == "basepipe"
        assert body["temperature"] == 0.3
        systems = [m for m in body["messages"] if m["role"] == "system"]
        assert [m["content"] for m in systems] == ["【PRESET-SYS】"]


def test_pipe_user_has_no_user_valves(monkeypatch):
    """UserValves は撤去済み — pipe の __user__ に "valves" は入らない(呼び手の
    __user__ に混ざっていても落とす)。UserValves クラスがあっても読まない。"""
    from pydantic import BaseModel

    seen = []

    class _UserValvesPipe:
        class UserValves(BaseModel):
            secret: str = "default"

        def pipe(self, body: dict, __user__: dict):
            seen.append(__user__)
            return "ok"

    monkeypatch.setattr(functions.Models, "get_model_by_id", lambda model_id: None)
    monkeypatch.setattr(
        functions,
        "get_function_module_by_id",
        lambda request, pipe_id: _UserValvesPipe(),
    )
    monkeypatch.setattr(functions, "get_tools", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        functions.Functions,
        "get_user_valves_by_id_and_user_id",
        lambda *a: (_ for _ in ()).throw(AssertionError("UserValves を読んだ")),
        raising=False,
    )
    user = types.SimpleNamespace(id="1", name="u", email="u@x", role="user")
    asyncio.run(
        functions.generate_function_chat_completion(
            types.SimpleNamespace(),
            {"model": "p", "stream": False, "messages": [], "metadata": {}},
            user=user,
        )
    )
    assert len(seen) == 1
    assert "valves" not in seen[0]
    assert seen[0]["id"] == "1"
