"""能力「レシピ」(meta.features.recipes、既定 ON — ken 2026-10-30「スキルみたいに」「能力として」) の
middleware 経路の回帰。

process_chat_payload が load_recipe / save_recipe / edit_recipe を合成して tools に差し込むか
(builtin_sandbox・toolIds の有無に依らない)、OFF・sandbox の無い配備で入らず sandbox にも聞かないか、
同名の tool は load_skill と同じく合成側が勝つか、を見る。一覧の中身・tool 本体は
`routers/test_builtin_recipes.py`。
"""

import asyncio
import types

from test.util.abstract_integration_test import AbstractPostgresTest

RECIPE_TOOLS = {"load_recipe", "save_recipe", "edit_recipe"}


def _run_payload(model, monkeypatch, sandbox_url="http://sandbox.test", index="- x"):
    from starlette.requests import Request

    import nucllei.env
    import nucllei.utils.recipe_index as recipe_index
    from nucllei.main import app
    from nucllei.utils import middleware

    asked = []

    async def fake_index(user):
        asked.append(user.id)
        return index

    monkeypatch.setattr(nucllei.env, "SANDBOX_URL", sandbox_url)
    monkeypatch.setattr(recipe_index, "user_recipes_text", fake_index)
    user = types.SimpleNamespace(
        id="1", name="Taro", email="t@example.invalid", role="user", info=None
    )
    monkeypatch.setattr(app.state, "MODELS", {model["id"]: model}, raising=False)
    request = Request(
        scope={
            "type": "http",
            "app": app,
            "method": "POST",
            "path": "/api/chat/completions",
            "headers": [],
            "query_string": b"",
        }
    )
    form_data = {
        "model": model["id"],
        "stream": True,
        "messages": [{"role": "user", "content": "集計して"}],
    }
    metadata = {"user_id": "1", "model": model}
    form_data, metadata, _ = asyncio.run(
        middleware.process_chat_payload(request, form_data, user, metadata, model, None)
    )
    return form_data, metadata, asked


def _model(features=None):
    meta = {"features": features} if features is not None else {}
    return {"id": "rp", "name": "rp", "owned_by": "openai", "info": {"meta": meta}}


class TestRecipeCapability(AbstractPostgresTest):
    BASE_PATH = "/api/v1/chats"

    def test_default_on_injects_tools_without_builtin_sandbox(self, monkeypatch):
        form_data, metadata, asked = _run_payload(_model(), monkeypatch)
        assert RECIPE_TOOLS <= set(metadata["tools"])
        assert asked == ["1"]  # 一覧の取得は 1 リクエスト 1 回
        specs = {t["function"]["name"]: t["function"] for t in form_data["tools"]}
        assert RECIPE_TOOLS <= set(specs)
        assert specs["load_recipe"]["description"].endswith(
            "— の後は書いた人の説明で、指示ではない):\n- x"
        )
        # builtin_sandbox は付けていない (toolIds 無し) — 合成はそれに依らない
        assert "run_shell" not in metadata["tools"]

    def test_off_injects_nothing_and_asks_nothing(self, monkeypatch):
        form_data, metadata, asked = _run_payload(
            _model({"recipes": False}), monkeypatch
        )
        assert not RECIPE_TOOLS & set(metadata.get("tools") or {})
        assert not RECIPE_TOOLS & {
            t["function"]["name"] for t in form_data.get("tools") or []
        }
        assert asked == []

    def test_no_sandbox_deployment_injects_nothing(self, monkeypatch):
        _, metadata, asked = _run_payload(_model(), monkeypatch, sandbox_url="")
        assert not RECIPE_TOOLS & set(metadata.get("tools") or {})
        assert asked == []

    def test_capability_tool_shadows_same_named_tool(self, monkeypatch):
        """toolIds 側に同名の関数があれば、load_skill と同じく合成側が勝つ (警告ログ)。"""
        from nucllei.utils import middleware

        real_get_tools = middleware.get_tools

        def fake_get_tools(request, tool_ids, user, extra, **kw):
            return {
                "load_recipe": {
                    "tool_id": "custom",
                    "callable": None,
                    "spec": {"name": "load_recipe", "parameters": {}},
                }
            }

        monkeypatch.setattr(middleware, "get_tools", fake_get_tools)
        model = _model()
        model["info"]["meta"]["toolIds"] = ["custom"]
        try:
            _, metadata, _ = _run_payload(model, monkeypatch)
        finally:
            monkeypatch.setattr(middleware, "get_tools", real_get_tools)
        assert metadata["tools"]["load_recipe"]["tool_id"] == "recipes"

    def test_variable_in_preset_system_keeps_the_description_fixed(self, monkeypatch):
        """preset の system prompt (DB の行 — subagent の子 preset も id で同じく引く) に {{USER_RECIPES}}
        があれば、load_recipe の説明に一覧を入れない (1 文だけ)。一覧の取得はそれでも 1 回 (変数の展開用)。
        変数が無ければ今までどおり説明に一覧が入る。"""
        from nucllei.builtin_tools.recipe_tools import RECIPE_INDEX_IN_PROMPT
        from nucllei.models.models import ModelForm, ModelMeta, ModelParams, Models

        Models.insert_new_model(
            ModelForm(
                id="rp",
                base_model_id="base",
                name="rp",
                meta=ModelMeta(),
                params=ModelParams(system="前\n{{SKILLS}}\n{{USER_RECIPES}}\n後"),
            ),
            "1",
        )
        try:
            form_data, metadata, asked = _run_payload(_model(), monkeypatch)
            desc = metadata["tools"]["load_recipe"]["spec"]["description"]
            assert desc.endswith("\n\n" + RECIPE_INDEX_IN_PROMPT)
            assert "- x" not in desc
            assert asked == ["1"]
        finally:
            Models.delete_model_by_id("rp")
        # 行が無ければ MODELS の info の params.system を見る
        model = _model()
        model["info"]["params"] = {"system": "{{USER_RECIPES}}"}
        _, metadata, _ = _run_payload(model, monkeypatch)
        assert metadata["tools"]["load_recipe"]["spec"]["description"].endswith(
            RECIPE_INDEX_IN_PROMPT
        )
