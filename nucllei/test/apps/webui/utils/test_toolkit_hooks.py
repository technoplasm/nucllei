"""get_tools の toolkit 汎用フック(#140)と meta.features.subagent 撤去 migration の回帰。

- `available(self, request, user, model, metadata) -> bool`: False / 例外でこのターン
  toolkit ごと載せない(read ACL の後に評価)
- `spec_overrides(self, request, user, model, metadata) -> dict[str, dict]`: 関数名 →
  spec の部分パッチを deep-merge(dict は再帰、他は置き換え)。例外なら静的 spec のまま
- フック名は tool 関数として spec 化されない / model は metadata["model"] を優先
- `timeouts` の非正値は「システム側上限なし」として per-entry に載る
- migration e7b3c90d2f14 の meta 変換(migrate_meta)
"""

import importlib.util
import pathlib
import types

from test.util.abstract_integration_test import AbstractPostgresTest

HOOKED_TOOL = '''
class Tools:
    timeouts = {"probe": -1}

    def available(self, request, user, model, metadata):
        meta = (model.get("info") or {}).get("meta") or {}
        if meta.get("explode"):
            raise RuntimeError("boom")
        return bool(meta.get("allow"))

    def spec_overrides(self, request, user, model, metadata):
        if ((model.get("info") or {}).get("meta") or {}).get("explode_overrides"):
            raise RuntimeError("boom")
        return {
            "probe": {
                "description": "patched for " + user.id,
                "parameters": {"properties": {"mode": {"enum": ["a", "b"]}}},
            },
            "no_such_function": {"description": "ignored"},
        }

    async def probe(self, mode: str = "a") -> str:
        """original

        :param mode: the mode
        """
        return mode

    async def other(self) -> str:
        """untouched"""
        return "ok"
'''


def _request():
    return types.SimpleNamespace(
        app=types.SimpleNamespace(state=types.SimpleNamespace(TOOLS={}))
    )


class TestToolkitHooks(AbstractPostgresTest):
    BASE_PATH = "/api/v1/tools"

    def setup_method(self):
        super().setup_method()
        from nucllei.models.tools import ToolForm, Tools
        from nucllei.utils.tools import get_tool_specs

        # specs は sync と同じ get_tool_specs で作る(フック名が spec 化されないことも固定)
        namespace: dict = {}
        exec(HOOKED_TOOL, namespace)
        self.specs = get_tool_specs(namespace["Tools"]())
        Tools.insert_new_tool(
            user_id="1",
            form_data=ToolForm(id="hooked", name="Hooked", content=HOOKED_TOOL),
            specs=self.specs,
        )

    def _load(self, meta, task_model=None, user_id="u1"):
        from nucllei.utils.tools import get_tools

        extra = {
            "__user__": {"id": user_id},
            "__metadata__": {"model": {"id": "parent", "info": {"meta": meta}}},
        }
        if task_model is not None:
            extra["__model__"] = task_model
        return get_tools(
            _request(),
            ["hooked"],
            types.SimpleNamespace(id=user_id, role="user"),
            extra,
        )

    def test_hook_names_are_not_specced(self):
        assert sorted(s["name"] for s in self.specs) == ["other", "probe"]

    def test_available_false_or_raising_hides_toolkit(self):
        assert self._load({}) == {}
        assert self._load({"allow": False}) == {}
        # 例外は fail-closed(載せない)
        assert self._load({"allow": True, "explode": True}) == {}
        assert set(self._load({"allow": True})) == {"probe", "other"}

    def test_hooks_see_parent_preset_not_task_model(self):
        # __model__(タスクモデル)ではなく metadata["model"](親 preset)を見る
        loaded = self._load(
            {"allow": True}, task_model={"id": "task", "info": {"meta": {}}}
        )
        assert set(loaded) == {"probe", "other"}

    def test_spec_overrides_deep_merge(self):
        from nucllei.models.tools import Tools

        loaded = self._load({"allow": True})
        probe = loaded["probe"]["spec"]
        # 置き換え(description)と再帰マージ(enum を足しても type / description は残る)
        assert probe["description"] == "patched for u1"
        mode = probe["parameters"]["properties"]["mode"]
        assert mode["enum"] == ["a", "b"]
        assert mode["type"] == "string"
        assert mode["description"] == "the mode"
        # パッチの無い関数・未知の関数名は影響なし
        assert loaded["other"]["spec"]["description"].strip() == "untouched"
        assert "no_such_function" not in loaded
        # DB の静的 spec は汚れない
        stored = {s["name"]: s for s in Tools.get_tool_by_id("hooked").specs}
        assert "enum" not in stored["probe"]["parameters"]["properties"]["mode"]

    def test_stored_spec_description_equals_turn_spec(self):
        """保存時 spec(get_tool_specs)の description はモデルに渡るもの(フック無しの
        ターン時 spec)と同じ — v0.6.5 は保存時だけ「最後の引数の説明」になっていた
        (2026-10-04)。"""
        from nucllei.models.tools import Tools

        loaded = self._load({"allow": True, "explode_overrides": True})
        stored = {s["name"]: s for s in Tools.get_tool_by_id("hooked").specs}
        for name in ("probe", "other"):
            assert stored[name]["description"] == loaded[name]["spec"]["description"]
        assert stored["probe"]["description"] == "original"
        assert stored["other"]["description"] == "untouched"

    def test_spec_overrides_failure_keeps_static_spec(self):
        loaded = self._load({"allow": True, "explode_overrides": True})
        assert loaded["probe"]["spec"]["description"].strip() == "original"
        assert "enum" not in loaded["probe"]["spec"]["parameters"]["properties"]["mode"]

    def test_non_positive_timeout_is_kept(self):
        loaded = self._load({"allow": True})
        assert loaded["probe"]["timeout"] == -1
        assert "timeout" not in loaded["other"]


# ---- migration e7b3c90d2f14(meta.features.subagent → toolIds の builtin_subagent)----


def _migration():
    versions = pathlib.Path(__file__).resolve().parents[4] / "migrations" / "versions"
    path = next(versions.glob("*_e7b3c90d2f14_*.py"))
    spec = importlib.util.spec_from_file_location("migration_e7b3c90d2f14", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_adds_tool_when_flag_on_or_absent():
    m = _migration()
    # キー不在 = 旧既定 ON
    meta = {"subagentPresetIds": ["w"], "toolIds": ["builtin_history"]}
    assert m.migrate_meta(meta) is True
    assert meta["toolIds"] == ["builtin_history", "builtin_subagent"]

    # 明示 ON: 足して、フラグは消す(空になった features も消す)
    meta = {"subagentPresetIds": ["w"], "features": {"subagent": True}}
    assert m.migrate_meta(meta) is True
    assert meta == {"subagentPresetIds": ["w"], "toolIds": ["builtin_subagent"]}

    # 既に入っていれば重複させない
    meta = {"subagentPresetIds": ["w"], "toolIds": ["builtin_subagent"]}
    assert m.migrate_meta(meta) is False
    assert meta["toolIds"] == ["builtin_subagent"]


def test_migration_respects_flag_off_and_empty_allowlist():
    m = _migration()
    # OFF: 足さない・フラグは消す・他の features は残す・allowlist は残す
    meta = {
        "subagentPresetIds": ["w"],
        "features": {"subagent": False, "skills": False},
    }
    assert m.migrate_meta(meta) is True
    assert meta == {"subagentPresetIds": ["w"], "features": {"skills": False}}

    # allowlist 空(または無し)なら足さない
    meta = {"subagentPresetIds": [], "toolIds": ["x"]}
    assert m.migrate_meta(meta) is False
    assert meta == {"subagentPresetIds": [], "toolIds": ["x"]}
    assert m.migrate_meta({}) is False
