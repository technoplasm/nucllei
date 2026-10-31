"""`/` でレシピを呼ぶ (utils/recipe_commands) と、`/` の補完が読む一覧 (GET /api/v1/recipes/) の回帰。

名前の決め方 (入力欄の src/lib/utils/slash-commands.ts と同じ規則 — FE は slash-commands.test.ts):
- skill が先 (`/name` で発火する skill があれば、同じ名前のレシピはどの置き場でも呼ばない)
- レシピ同士は狭い置き場が勝つ: 個人 → グループ (グループ名の順) → 全体。勝った物が壊れていれば呼ばない
- 名前は空白までの 1 語 (空白のあるレシピは `/` では呼べない)

sandbox は本物のアプリ (`sandbox/app/main.py`) を TestClient で動かし、load_recipe は process_chat_payload が
このターンの tools に載せた本物を通す (読める範囲・判定は load_recipe のまま)。
"""

import asyncio
import datetime as dt
import importlib.util
import json
import os
import pathlib
import sys
import types

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user

_SANDBOX_MAIN = (
    pathlib.Path(__file__).resolve().parents[4] / "sandbox" / "app" / "main.py"
)
TODAY = dt.date.today().isoformat()
MEMBERS = {"u1": ["EMR", "a-team"], "a1": []}


def _md(title="集計", description="月次の集計", body="1. 集める\n", extra=""):
    return (
        f"---\ntitle: {title}\ndescription: {description}\nauthor: 山田\n"
        f"updated: {TODAY}\n{extra}---\n\n{body}"
    )


def _put(base: pathlib.Path, name: str, md: str):
    d = base / "recipes" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "RECIPE.md").write_text(md, encoding="utf-8")
    return d


@pytest.fixture
def sbx(monkeypatch, tmp_path):
    """本物の sandbox を tmp に立て、nucllei の sandbox_post をそこへ繋ぐ。"""
    monkeypatch.setenv("SANDBOX_WORKSPACES", str(tmp_path / "workspaces"))
    monkeypatch.delenv("SANDBOX_TOKEN", raising=False)
    spec = importlib.util.spec_from_file_location(
        "sandbox_main_for_recipe_commands", _SANDBOX_MAIN
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    ws_root = tmp_path / "workspaces"

    async def fake_workspace(user_id):
        ws = ws_root / user_id
        ws.mkdir(parents=True, exist_ok=True)
        return ws

    monkeypatch.setattr(mod, "_workspace", fake_workspace)
    ext = tmp_path / "ext"
    for g in ("EMR", "a-team", "OTHER"):
        (ext / "groups" / g).mkdir(parents=True)
    (ext / "public").mkdir()
    monkeypatch.setattr(mod, "RO_MOUNT_ROOT", ext)
    monkeypatch.setattr(os, "chown", lambda *a, **k: None)
    monkeypatch.setattr(os, "fchown", lambda *a, **k: None)
    client = TestClient(mod.app)

    import nucllei.builtin_tools.sandbox as sb
    import nucllei.env
    import nucllei.utils.ext_acl as ext_acl
    import nucllei.utils.task as task

    calls = []

    async def fake_post(path, payload, timeout_s):
        calls.append(path)
        r = client.post(path, json=payload)
        if r.status_code != 200:
            raise sb.SandboxHTTPError(r.status_code, r.text)
        return r.json()

    monkeypatch.setattr(sb, "sandbox_post", fake_post)
    monkeypatch.setattr(nucllei.env, "SANDBOX_URL", "http://sandbox.test")
    monkeypatch.setattr(
        ext_acl,
        "member_group_access",
        lambda uid: {g: "rw" for g in MEMBERS.get(uid, [])},
    )
    monkeypatch.setattr(ext_acl, "all_group_names", lambda: ["EMR", "a-team", "OTHER"])
    monkeypatch.setattr(task, "user_group_names", lambda uid: MEMBERS.get(uid, []))
    task.reset_prompt_fetches()
    return {"ws": ws_root / "u1", "ext": ext, "calls": calls}


def _request():
    from nucllei.main import app

    return Request(
        scope={
            "type": "http",
            "app": app,
            "method": "POST",
            "path": "/api/chat/completions",
            "headers": [],
            "query_string": b"",
        }
    )


def _model(*skill_ids, skills=True, recipes=True):
    features = {}
    if not skills:
        features["skills"] = False
    if not recipes:
        features["recipes"] = False
    return {
        "id": "rcpreset",
        "name": "rcpreset",
        "owned_by": "openai",
        "info": {"meta": {"skillIds": list(skill_ids), "features": features}},
    }


def _calls(messages, name):
    return [
        (call["id"], json.loads(call["function"]["arguments"]))
        for message in messages
        for call in message.get("tool_calls") or []
        if call["function"]["name"] == name
    ]


def _tool_content(messages, call_id):
    return next(
        m["content"]
        for m in messages
        if m.get("role") == "tool" and m.get("tool_call_id") == call_id
    )


# ---------------------------------------------------------------------------
# 純関数: 名前の取り出しと、同じ名前のどれが使われるか
# ---------------------------------------------------------------------------


def test_command_token():
    from nucllei.utils.recipe_commands import command_token

    assert command_token("/集計 今月分") == "集計"
    assert command_token("/集計") == "集計"
    assert command_token("/集計\n2 行目") == "集計"
    assert command_token("/集計　全角の空白") == "集計"
    assert command_token("/foo.bar baz") == "foo.bar"
    # NFD で打たれても NFC で比べる
    assert command_token("/" + "ガ") == "ガ"
    assert command_token("今月 /集計") is None
    assert command_token("1 行目\n/集計") is None
    assert command_token("/") is None
    assert command_token("/ 集計") is None
    assert command_token(None) is None


def _r(name, place, group="", broken=()):
    return {"name": name, "place": place, "group": group, "broken": list(broken)}


def test_resolve_narrower_place_wins():
    from nucllei.utils.recipe_commands import resolve_recipe_command

    recipes = [
        _r("集計", "public"),
        _r("集計", "group", "EMR"),
        _r("集計", "personal"),
        _r("抽出", "public"),
        _r("抽出", "group", "病理"),
        _r("抽出", "group", "EMR"),
        _r("共通", "public"),
    ]
    assert resolve_recipe_command(recipes, "集計")["place"] == "personal"
    winner = resolve_recipe_command(recipes, "抽出")
    assert (winner["place"], winner["group"]) == ("group", "EMR")
    assert resolve_recipe_command(recipes, "共通")["place"] == "public"
    assert resolve_recipe_command(recipes, "無い") is None
    # 完全一致だけ (前方一致・大文字小文字の違いは別の名前)
    assert resolve_recipe_command(recipes, "集") is None
    assert resolve_recipe_command([_r("Foo", "personal")], "foo") is None


def test_resolve_groups_in_name_order_casefolded():
    from nucllei.utils.recipe_commands import resolve_recipe_command

    recipes = [_r("x", "group", "b"), _r("x", "group", "B2"), _r("x", "group", "A")]
    assert resolve_recipe_command(recipes, "x")["group"] == "A"
    recipes = [_r("x", "group", "b"), _r("x", "group", "B2")]
    assert resolve_recipe_command(recipes, "x")["group"] == "b"


def test_resolve_broken_winner_holds_the_name():
    """狭い置き場のレシピが壊れていれば何も呼ばない (広い置き場の同じ名前に回さない)。"""
    from nucllei.utils.recipe_commands import resolve_recipe_command

    recipes = [
        _r("集計", "personal", broken=["RECIPE.md が無い"]),
        _r("集計", "public"),
    ]
    assert resolve_recipe_command(recipes, "集計") is None
    recipes = [_r("集計", "personal"), _r("集計", "public", broken=["x"])]
    assert resolve_recipe_command(recipes, "集計")["place"] == "personal"


# ---------------------------------------------------------------------------
# process_chat_payload を通す (本物の sandbox・load_recipe・skill の行)
# ---------------------------------------------------------------------------


class TestRecipeCommand(AbstractPostgresTest):
    BASE_PATH = "/api/v1/recipes"

    def setup_method(self):
        super().setup_method()
        self.user = types.SimpleNamespace(
            id="u1", name="山田", email="y@example.invalid", role="user", info=None
        )

    def _skill(self, skill_id, owner="u1", access_control=None, **kwargs):
        from nucllei.models.skills import SkillForm, Skills

        skill = Skills.insert_new_skill(
            owner,
            SkillForm(
                id=skill_id,
                name=skill_id,
                description=f"{skill_id} の説明",
                content=f"【{skill_id} の手順書】",
                access_control=access_control,
                **kwargs,
            ),
        )
        assert skill is not None
        return skill

    def _payload(self, messages, model, files=None):
        from nucllei.main import app
        from nucllei.utils import middleware
        from nucllei.utils.task import reset_prompt_fetches

        reset_prompt_fetches()
        saved = getattr(app.state, "MODELS", None)
        app.state.MODELS = {model["id"]: model}
        try:
            form_data = {"model": model["id"], "stream": True, "messages": messages}
            if files:
                form_data["files"] = files
            form_data, metadata, _ = asyncio.run(
                middleware.process_chat_payload(
                    _request(),
                    form_data,
                    self.user,
                    {"user_id": self.user.id, "model": model},
                    model,
                    None,
                )
            )
            return form_data, metadata
        finally:
            app.state.MODELS = saved

    def _next_turn(self, first_messages, metadata, reply="わかりました"):
        """1 ターン目の応答を保存して、FE が次のリクエストで送る形にした履歴 (+ 次の発言は呼び手が足す)。"""
        from nucllei.utils.content_blocks import (
            convert_content_blocks_to_messages,
            snapshot_content_blocks,
        )
        from nucllei.utils.middleware import activation_seed_blocks

        blocks = activation_seed_blocks(metadata)
        blocks.append({"type": "text", "content": reply})
        saved = snapshot_content_blocks(blocks)
        assert all("in_payload" not in block for block in saved)
        return [
            *[m for m in first_messages if m.get("role") == "user"],
            *convert_content_blocks_to_messages(saved),
        ]

    # ── 発火 ─────────────────────────────────────────────────

    def test_fires_load_recipe_after_current_message(self, sbx):
        _put(sbx["ws"], "集計", _md(body="1. 今月分を集める\n"))
        messages = [{"role": "user", "content": "/集計 今月分で"}]
        form_data, metadata = self._payload(messages, _model())
        out = form_data["messages"]
        roles = [m["role"] for m in out]
        user_at = roles.index("user")
        assert roles[user_at : user_at + 3] == ["user", "assistant", "tool"]
        # 利用者の文は生のまま (残りがその発言)
        assert "/集計 今月分で" in json.dumps(out[user_at], ensure_ascii=False)
        ((call_id, args),) = _calls(out, "load_recipe")
        assert args == {"path": "/workspace/recipes/集計"}
        assert call_id == "recipe:0"
        content = _tool_content(out, call_id)
        assert '<recipe path="/workspace/recipes/集計"' in content
        assert "1. 今月分を集める" in content
        # 応答の先頭に残す往復 (同じ結果)
        from nucllei.utils.middleware import activation_seed_blocks

        (block,) = activation_seed_blocks(metadata)
        assert block["in_payload"] is True
        assert [c["function"]["name"] for c in block["content"]] == ["load_recipe"]
        assert block["results"][0]["tool_call_id"] == call_id
        assert block["results"][0]["content"] == content
        # カードの木 (モデルには渡らない)
        assert block["results"][0]["chain"]["body"].startswith("1. 今月分を集める")
        # load_recipe はこのターンの tool にある
        assert "load_recipe" in metadata["tools"]

    def test_second_turn_does_not_fire_again_and_keeps_result(self, sbx):
        _put(sbx["ws"], "集計", _md())
        first = [{"role": "user", "content": "/集計 今月分で"}]
        form_data, metadata = self._payload(first, _model())
        history = self._next_turn(form_data["messages"], metadata)
        assert len(_calls(history, "load_recipe")) == 1
        history.append({"role": "user", "content": "表にして"})
        form_data2, metadata2 = self._payload(history, _model())
        assert len(_calls(form_data2["messages"], "load_recipe")) == 1
        assert not metadata2.get("recipe_activations")
        # 同じ発言の `/集計` が今の発言のまま来ても (応答が後ろにある) 発火し直さない
        resent = self._next_turn(form_data["messages"], metadata)
        form_data3, metadata3 = self._payload(resent, _model())
        assert len(_calls(form_data3["messages"], "load_recipe")) == 1
        assert not metadata3.get("recipe_activations")

    def test_regenerate_fires_again(self, sbx):
        """応答を作り直す (応答の無い履歴) と、今の発言の `/name` でまた発火する。"""
        _put(sbx["ws"], "集計", _md())
        messages = [{"role": "user", "content": "/集計"}]
        self._payload(list(messages), _model())
        form_data, metadata = self._payload(list(messages), _model())
        assert len(_calls(form_data["messages"], "load_recipe")) == 1
        assert metadata["recipe_activations"]

    def test_only_current_message_fires(self, sbx):
        _put(sbx["ws"], "集計", _md())
        messages = [
            {"role": "user", "content": "/集計"},
            {"role": "assistant", "content": "はい"},
            {"role": "user", "content": "続けて"},
        ]
        form_data, metadata = self._payload(messages, _model())
        assert _calls(form_data["messages"], "load_recipe") == []
        assert not metadata.get("recipe_activations")

    def test_narrower_place_wins_and_group_order(self, sbx):
        _put(sbx["ext"] / "public", "集計", _md(body="全体の本文\n"))
        _put(sbx["ext"] / "groups" / "EMR", "集計", _md(body="EMR の本文\n"))
        _put(sbx["ext"] / "groups" / "a-team", "集計", _md(body="a-team の本文\n"))
        form_data, _ = self._payload([{"role": "user", "content": "/集計"}], _model())
        ((_, args),) = _calls(form_data["messages"], "load_recipe")
        assert args["path"] == "/ext/groups/a-team/recipes/集計"
        _put(sbx["ws"], "集計", _md(body="個人の本文\n"))
        form_data, _ = self._payload([{"role": "user", "content": "/集計"}], _model())
        ((call_id, args),) = _calls(form_data["messages"], "load_recipe")
        assert args["path"] == "/workspace/recipes/集計"
        assert "個人の本文" in _tool_content(form_data["messages"], call_id)

    def test_broken_winner_fires_nothing(self, sbx):
        _put(sbx["ws"], "集計", "壊れている\n")
        _put(sbx["ext"] / "public", "集計", _md())
        form_data, metadata = self._payload(
            [{"role": "user", "content": "/集計"}], _model()
        )
        assert _calls(form_data["messages"], "load_recipe") == []
        assert not metadata.get("recipe_activations")

    def test_warned_recipe_fires(self, sbx):
        _put(
            sbx["ws"],
            "集計",
            _md(extra="requires:\n  - read: /ext/public/無い.md\n"),
        )
        form_data, _ = self._payload([{"role": "user", "content": "/集計"}], _model())
        ((call_id, _),) = _calls(form_data["messages"], "load_recipe")
        assert 'status="warning"' in _tool_content(form_data["messages"], call_id)

    def test_skill_wins_over_same_named_recipe(self, sbx):
        _put(sbx["ws"], "summary", _md())
        _put(sbx["ext"] / "public", "summary", _md())
        self._skill("summary")
        form_data, metadata = self._payload(
            [{"role": "user", "content": "/summary 今月"}], _model("summary")
        )
        assert _calls(form_data["messages"], "load_recipe") == []
        assert [a["id"] for a in metadata["skill_activations"]] == ["summary"]

    def test_skill_already_loaded_still_wins(self, sbx):
        """skill が読み込み済み (発火しない) でも、同じ `/name` のレシピは呼ばない。"""
        _put(sbx["ws"], "summary", _md())
        self._skill("summary")
        first = [{"role": "user", "content": "/summary"}]
        form_data, metadata = self._payload(first, _model("summary"))
        history = self._next_turn(form_data["messages"], metadata)
        history.append({"role": "user", "content": "/summary もう一度"})
        form_data2, metadata2 = self._payload(history, _model("summary"))
        assert _calls(form_data2["messages"], "load_recipe") == []
        assert not metadata2.get("skill_activations")

    def test_skill_prefix_match_shadows_recipe(self, sbx):
        """`/foo.bar` は skill foo を発火させる (skill の `/command` は行頭の英数字・_・- の並び) —
        レシピ foo.bar は呼ばない。"""
        _put(sbx["ws"], "foo.bar", _md())
        self._skill("foo")
        form_data, metadata = self._payload(
            [{"role": "user", "content": "/foo.bar"}], _model("foo")
        )
        assert _calls(form_data["messages"], "load_recipe") == []
        assert [a["id"] for a in metadata["skill_activations"]] == ["foo"]

    def test_recipe_fires_when_skill_not_invocable(self, sbx):
        """skill がこのモデルに紐づいていない・見えない・機能「スキル」OFF なら、レシピが呼ばれる。"""
        _put(sbx["ws"], "summary", _md())
        self._skill("summary")
        self._skill("hidden", owner="someone-else", access_control={})
        _put(sbx["ws"], "hidden", _md())
        for model, text in (
            (_model(), "/summary"),
            (_model("summary", skills=False), "/summary"),
            (_model("hidden"), "/hidden"),
        ):
            form_data, metadata = self._payload(
                [{"role": "user", "content": text}], model
            )
            assert len(_calls(form_data["messages"], "load_recipe")) == 1, (model, text)
            assert not metadata.get("skill_activations")

    def test_recipes_off_or_no_sandbox_does_nothing(self, sbx, monkeypatch):
        import nucllei.env

        _put(sbx["ws"], "集計", _md())
        form_data, metadata = self._payload(
            [{"role": "user", "content": "/集計"}], _model(recipes=False)
        )
        assert _calls(form_data["messages"], "load_recipe") == []
        assert sbx["calls"] == []
        monkeypatch.setattr(nucllei.env, "SANDBOX_URL", "")
        form_data, metadata = self._payload(
            [{"role": "user", "content": "/集計"}], _model()
        )
        assert _calls(form_data["messages"], "load_recipe") == []
        assert sbx["calls"] == []

    def test_listing_fetched_once_per_request(self, sbx):
        """`/` の発火と load_recipe の説明の一覧は 1 回の問い合わせで済む。"""
        _put(sbx["ws"], "集計", _md())
        self._payload([{"role": "user", "content": "/集計"}], _model())
        assert sbx["calls"].count("/recipes/index") == 1

    def test_not_a_member_group_and_space_names(self, sbx):
        _put(sbx["ext"] / "groups" / "OTHER", "秘密", _md())
        _put(sbx["ws"], "月次 集計", _md())
        for text in ("/秘密", "/月次 集計", "/月次"):
            form_data, _ = self._payload([{"role": "user", "content": text}], _model())
            assert _calls(form_data["messages"], "load_recipe") == [], text

    def test_coexists_with_conversation_preamble(self, sbx, monkeypatch):
        """会話の前置き (params.context) は最初の発言の先頭に入り、レシピの往復はその発言の直後のまま。"""
        from nucllei.utils import conversation_context, middleware

        _put(sbx["ws"], "集計", _md())
        for mod in (conversation_context, middleware):
            monkeypatch.setattr(
                mod, "preset_context_template", lambda model: "前置き\n{{USER_RECIPES}}"
            )
        form_data, _ = self._payload(
            [{"role": "user", "content": "/集計 今月分"}], _model()
        )
        out = form_data["messages"]
        first_user = next(m for m in out if m["role"] == "user")
        text = json.dumps(first_user, ensure_ascii=False)
        assert "前置き" in text and "/workspace/recipes/集計" in text
        assert text.index("前置き") < text.index("/集計 今月分")
        i = out.index(first_user)
        assert [m["role"] for m in out[i : i + 3]] == ["user", "assistant", "tool"]
        assert sbx["calls"].count("/recipes/index") == 1

    # ── 一覧の API ───────────────────────────────────────────

    def test_api_lists_visible_recipes(self, sbx):
        _put(sbx["ws"], "集計", _md(title="月次の集計", description="件数を表に"))
        _put(sbx["ext"] / "groups" / "EMR", "抽出", _md(title="抽出"))
        _put(sbx["ext"] / "groups" / "OTHER", "秘密", _md())
        _put(sbx["ext"] / "public", "壊れ", "---\ntitle: x\n")
        _put(
            sbx["ext"] / "public",
            "共通",
            _md(extra="requires:\n  - read: /ext/public/無い.md\n"),
        )
        with mock_webui_user(id="u1", role="user", name="山田"):
            res = self.fast_api_client.get(self.create_url("/"))
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["available"] is True and body["error"] is None
        assert body["skill_ids"] == []
        rows = [
            (r["place"], r["group"], r["name"], r["title"], r["description"])
            for r in body["recipes"]
        ]
        assert rows == [
            ("personal", "", "集計", "月次の集計", "件数を表に"),
            ("group", "EMR", "抽出", "抽出", "月次の集計"),
            ("public", "", "共通", "集計", "月次の集計"),
            ("public", "", "壊れ", "", ""),
        ]
        by_name = {r["name"]: r for r in body["recipes"]}
        assert by_name["集計"]["folder"] == "/workspace/recipes/集計"
        assert by_name["壊れ"]["broken"] and not by_name["壊れ"]["warnings"]
        assert by_name["共通"]["warnings"] == ["requires の 1 番目の read が無い"]
        assert not by_name["集計"]["broken"]

    def test_api_without_sandbox(self, monkeypatch):
        import nucllei.env

        monkeypatch.setattr(nucllei.env, "SANDBOX_URL", "")
        with mock_webui_user(id="u1", role="user", name="山田"):
            res = self.fast_api_client.get(self.create_url("/"))
        assert res.json() == {
            "available": False,
            "error": None,
            "recipes": [],
            "skill_ids": [],
        }

    def test_api_unreachable_sandbox(self, sbx, monkeypatch):
        import nucllei.builtin_tools.sandbox as sb

        async def down(path, payload, timeout_s):
            raise ConnectionError("down")

        monkeypatch.setattr(sb, "sandbox_post", down)
        with mock_webui_user(id="u1", role="user", name="山田"):
            res = self.fast_api_client.get(self.create_url("/"))
        assert res.json()["error"] == "unreachable"
        assert res.json()["recipes"] == []

    def test_api_skill_ids_are_the_readable_ones_even_for_admins(self, monkeypatch):
        """灰色の判定 (skill が先) は `/` で発火する skill で見る — 管理者でも他人の非公開 skill は入らない。"""
        import nucllei.env

        monkeypatch.setattr(nucllei.env, "SANDBOX_URL", "")
        self._skill("mine", owner="a1")
        self._skill("public_one", owner="someone")
        self._skill("private_one", owner="someone", access_control={})
        with mock_webui_user(id="a1", role="admin", name="管理"):
            res = self.fast_api_client.get(self.create_url("/"))
        assert sorted(res.json()["skill_ids"]) == ["mine", "public_one"]

    # ── 自動ロード・inlet との組み合わせ ─────────────────────

    def _file(self, file_id, content_type, owner="u1"):
        from nucllei.models.files import FileForm, Files

        assert Files.insert_new_file(
            owner,
            FileForm(
                id=file_id,
                filename=file_id,
                path=f"/nonexistent/{file_id}",
                meta={"name": file_id, "content_type": content_type},
                data={},
            ),
        )
        return {"type": "file", "id": file_id}

    def test_with_auto_loaded_skill_order_matches_saved_block(self, sbx):
        """同じ発言で添付の自動ロードとレシピが発火したとき、messages の並び (skill → レシピ) と応答に残す
        ブロックの並びが同じ — 次のリクエストの履歴はモデルが見た並びのまま。"""
        from nucllei.utils.middleware import activation_seed_blocks

        _put(sbx["ws"], "集計", _md())
        self._skill("audio", auto_load_content_types=["audio/"])
        first = [{"role": "user", "content": "/集計 これ聞いて"}]
        form_data, metadata = self._payload(
            first, _model("audio"), files=[self._file("rec", "audio/webm")]
        )
        out = form_data["messages"]
        i = next(n for n, m in enumerate(out) if m["role"] == "user")
        seen = [
            (
                m["role"],
                (m.get("tool_calls") or [{}])[0].get("function", {}).get("name"),
            )
            for m in out[i + 1 : i + 5]
        ]
        assert seen == [
            ("assistant", "load_skill"),
            ("tool", None),
            ("assistant", "load_recipe"),
            ("tool", None),
        ]
        (block,) = activation_seed_blocks(metadata)
        assert [c["function"]["name"] for c in block["content"]] == [
            "load_skill",
            "load_recipe",
        ]
        assert [r["tool_call_id"] for r in block["results"]] == [
            out[i + 2]["tool_call_id"],
            out[i + 4]["tool_call_id"],
        ]
        assert block["results"][1]["content"] == out[i + 4]["content"]

    def test_inlet_rebuilding_messages_still_gets_the_result(self, sbx, monkeypatch):
        """inlet が messages を作り直しても (JSON で丸ごと置き換え)、結果はモデルに渡る往復に入る。"""
        from nucllei.utils import middleware

        _put(sbx["ws"], "集計", _md(body="本文はここ\n"))

        async def rebuild(request, payload, user, models):
            return json.loads(json.dumps(payload))

        monkeypatch.setattr(middleware, "process_pipeline_inlet_filter", rebuild)
        form_data, metadata = self._payload(
            [{"role": "user", "content": "/集計"}], _model()
        )
        ((call_id, _),) = _calls(form_data["messages"], "load_recipe")
        assert "本文はここ" in _tool_content(form_data["messages"], call_id)
        assert metadata["recipe_activations"][0]["result"]["content"] == (
            _tool_content(form_data["messages"], call_id)
        )

    def test_inlet_dropping_the_exchange_stores_nothing(self, sbx, monkeypatch):
        """inlet が往復を落とせば、実行も応答への保存もしない (モデルが見ていない往復を残さない)。"""
        from nucllei.utils import middleware

        _put(sbx["ws"], "集計", _md())

        async def drop(request, payload, user, models):
            payload["messages"] = [
                m for m in payload["messages"] if m["role"] == "user"
            ]
            return payload

        monkeypatch.setattr(middleware, "process_pipeline_inlet_filter", drop)
        form_data, metadata = self._payload(
            [{"role": "user", "content": "/集計"}], _model()
        )
        assert _calls(form_data["messages"], "load_recipe") == []
        assert not metadata.get("recipe_activations")
        assert middleware.activation_seed_blocks(metadata) == []
