"""pipe / action function の列挙と pipe のストリーム終端の回帰(2026-10-05)。

- 読み込めない pipe / action が 1 つあると /api/models 全体が落ちていた → 個別に飛ばす
- utils/models の内部関数 get_function_module_by_id が値を返さず、action の
  `actions`(複数 action の展開)が効かなかった
- pipe のストリームが型によって `[DONE]` を出さない・出しても空行区切りが無かった
"""

import asyncio
import types

from nucllei import functions
from nucllei.utils import models as models_utils


def _fn(id, type="pipe", name=None):
    return types.SimpleNamespace(
        id=id, name=name or id, type=type, created_at=0, description=f"{id} desc"
    )


def test_broken_pipe_is_skipped_not_fatal(monkeypatch):
    monkeypatch.setattr(
        functions.Functions,
        "get_functions_by_type",
        lambda type: [_fn("broken"), _fn("good")],
    )

    def load(request, pipe_id):
        if pipe_id == "broken":
            raise SyntaxError("bad pipe source")
        return types.SimpleNamespace(pipe=lambda body: "x")

    monkeypatch.setattr(functions, "get_function_module_by_id", load)
    result = asyncio.run(functions.get_function_models(types.SimpleNamespace()))
    assert [m["id"] for m in result] == ["good"]


def test_manifold_sub_pipe_without_id_is_skipped(monkeypatch):
    monkeypatch.setattr(
        functions.Functions, "get_functions_by_type", lambda type: [_fn("mani")]
    )
    module = types.SimpleNamespace(pipes=[{"id": "a", "name": "A"}, {"name": "no id"}])
    monkeypatch.setattr(
        functions, "get_function_module_by_id", lambda request, pipe_id: module
    )
    result = asyncio.run(functions.get_function_models(types.SimpleNamespace()))
    assert [m["id"] for m in result] == ["mani.a"]


def _run_get_all_models(monkeypatch, modules):
    request = types.SimpleNamespace(
        app=types.SimpleNamespace(state=types.SimpleNamespace(FUNCTIONS={}))
    )

    async def base_models(request, user=None):
        return [{"id": "base", "name": "base", "owned_by": "openai"}]

    custom = types.SimpleNamespace(
        id="preset",
        name="preset",
        base_model_id="base",
        is_active=True,
        created_at=0,
        meta=types.SimpleNamespace(
            model_dump=lambda: {"actionIds": list(modules.keys())}
        ),
        model_dump=lambda: {"id": "preset"},
    )
    monkeypatch.setattr(models_utils, "get_all_base_models", base_models)
    monkeypatch.setattr(models_utils.Models, "get_all_models", lambda: [custom])
    monkeypatch.setattr(
        models_utils.Functions,
        "get_functions_by_type",
        lambda type: [_fn(i, "action") for i in modules],
    )
    monkeypatch.setattr(
        models_utils.Functions, "get_function_by_id", lambda i: _fn(i, "action")
    )

    def load(function_id):
        module = modules[function_id]
        if isinstance(module, Exception):
            raise module
        return module, "action", {}

    monkeypatch.setattr(models_utils, "load_function_module_by_id", load)
    models = asyncio.run(models_utils.get_all_models(request))
    return {m["id"]: m for m in models}


def test_action_module_with_multiple_actions_is_expanded(monkeypatch):
    multi = types.SimpleNamespace(
        actions=[{"id": "one", "name": "One"}, {"id": "two", "name": "Two"}]
    )
    models = _run_get_all_models(monkeypatch, {"multi": multi})
    assert sorted(a["id"] for a in models["preset"]["actions"]) == [
        "multi.one",
        "multi.two",
    ]


def test_broken_action_is_skipped_not_fatal(monkeypatch):
    single = types.SimpleNamespace()
    models = _run_get_all_models(
        monkeypatch, {"broken": SyntaxError("bad action"), "single": single}
    )
    assert [a["id"] for a in models["preset"]["actions"]] == ["single"]


# ---- pipe のストリーム終端 ------------------------------------------------------


class _AsyncPipe:
    async def pipe(self, body: dict):
        for t in ["あ", "い"]:
            yield t


class _SyncPipe:
    def pipe(self, body: dict):
        for t in ["あ", "い"]:
            yield t


class _StrPipe:
    def pipe(self, body: dict):
        return "あい"


class _DictPipe:
    def pipe(self, body: dict):
        return {
            "id": "x",
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {"content": "う"}}],
        }


class _DonePipe:
    async def pipe(self, body: dict):
        yield "data: [DONE]"


def _stream_of(monkeypatch, module):
    monkeypatch.setattr(functions.Models, "get_model_by_id", lambda model_id: None)
    monkeypatch.setattr(
        functions, "get_function_module_by_id", lambda request, pipe_id: module
    )
    monkeypatch.setattr(functions, "get_tools", lambda *args, **kwargs: {})
    user = types.SimpleNamespace(id="1", name="u", email="u@x", role="admin")

    async def run():
        response = await functions.generate_function_chat_completion(
            types.SimpleNamespace(),
            {"model": "p", "stream": True, "messages": [], "metadata": {}},
            user=user,
        )
        raw = ""
        async for chunk in response.body_iterator:
            raw += chunk.decode() if isinstance(chunk, bytes) else chunk
        return raw

    return asyncio.run(run())


def test_pipe_stream_always_ends_with_done(monkeypatch):
    for module in (_AsyncPipe(), _SyncPipe(), _StrPipe(), _DictPipe()):
        raw = _stream_of(monkeypatch, module)
        assert raw.endswith("data: [DONE]\n\n"), (type(module).__name__, raw)
        assert raw.count("data: [DONE]") == 1
        # 全行が空行区切りの SSE(前の行と繋がらない)
        frames = [f for f in raw.split("\n\n") if f]
        assert all(f.startswith("data: ") for f in frames), raw
        assert '"finish_reason": "stop"' in raw


def test_pipe_own_done_is_not_doubled(monkeypatch):
    raw = _stream_of(monkeypatch, _DonePipe())
    assert raw.count("data: [DONE]") == 1
