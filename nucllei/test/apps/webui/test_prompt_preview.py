"""preset / skill の編集画面のプレビュー。

- POST /api/v1/models/preview: 編集中 (未保存) の preset で、新しいチャットの最初のターンにモデルへ
  渡る tools と system prompt と会話の前置き (最初の発言の先頭に入るバイト列) を、リクエストした本人として
  返す。admin だけ (preset の編集と同じ)
- POST /api/v1/skills/preview: 編集中の skill の本文を本人として展開し、before / after を書いた順に
  並べる (実行しない)。skill を編集できる人だけ
- tools は実際のリクエスト (process_chat_payload) と同じ関数を通るので、spec が一致する
- 利用者ごとに変わる部分の印 (変数の scope・tool の varies)
"""

import asyncio
import types

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user, mock_webui_user_strict

TAKEN = "1"


def _model_form(system: str, meta=None, model_id="pv", context=None):
    return {
        "id": model_id,
        "base_model_id": "base-x",
        "name": "pv",
        "meta": meta or {},
        "params": {"system": system, **({"context": context} if context else {})},
    }


def _fake_recipes(monkeypatch, index="- `/workspace/recipes/集計` — 月次"):
    import nucllei.env
    import nucllei.utils.recipe_index as recipe_index

    asked = []

    async def fake_index(user):
        asked.append(getattr(user, "id", None))
        return index

    monkeypatch.setattr(nucllei.env, "SANDBOX_URL", "http://sandbox.test")
    monkeypatch.setattr(recipe_index, "user_recipes_text", fake_index)
    return asked


def _insert_skill(skill_id="pvskill", description="集計の手順"):
    from nucllei.models.skills import SkillForm, Skills

    return Skills.insert_new_skill(
        TAKEN,
        SkillForm(
            id=skill_id,
            name=skill_id,
            description=description,
            content="本文",
            access_control=None,
        ),
    )


# ブラウザが送りうる英語の曜日など — サーバの値を上書きしてはいけない
BROWSER_VARIABLES = {
    "{{CURRENT_DATE}}": "1999-01-01",
    "{{CURRENT_TIME}}": "11:59:59 PM",
    "{{CURRENT_DATETIME}}": "1999-01-01 11:59:59 PM",
    "{{CURRENT_WEEKDAY}}": "Friday",
    "{{USER_NAME}}": "Mallory",
    "{{USER_LOCATION}}": "Nowhere",
    "{{CURRENT_TIMEZONE}}": "Pacific/Kiritimati",
    "{{USER_LANGUAGE}}": "ja-JP",
}
WEEKDAYS_JA = ("月曜日", "火曜日", "水曜日", "木曜日", "金曜日", "土曜日", "日曜日")


def _now_in(tz_name):
    """(日付, 曜日) — 呼ぶ前後で日付が変わっても通るよう、呼び出しの前後 2 回分を返す。"""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now = datetime.now(ZoneInfo(tz_name) if tz_name else None)
    return now.strftime("%Y-%m-%d"), WEEKDAYS_JA[now.weekday()]


def _chat_tools(model: dict, monkeypatch, messages=None, variables=None):
    """実際のリクエスト (process_chat_payload) がモデルに渡す tools (messages を渡せばその結果も)。"""
    from starlette.requests import Request

    from nucllei.main import app
    from nucllei.utils import middleware

    user = types.SimpleNamespace(
        id=TAKEN, name="Taro", email="t@example.invalid", role="admin", info=None
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
        "messages": messages or [{"role": "user", "content": "こんにちは"}],
    }
    metadata = {
        "user_id": TAKEN,
        "chat_id": "c1",
        "message_id": "m1",
        "model": model,
        "variables": variables,
    }

    async def run():
        out, _, _ = await middleware.process_chat_payload(
            request, form_data, user, metadata, model, None
        )
        return out

    out = asyncio.run(run())
    if messages is not None:
        return out
    return [t["function"] for t in out.get("tools") or []]


class TestModelPreview(AbstractPostgresTest):
    BASE_PATH = "/api/v1/models"

    def _preview(self, body, **user):
        with mock_webui_user(**{"id": TAKEN, "role": "admin", "name": "Taro", **user}):
            return self.fast_api_client.post(self.create_url("/preview"), json=body)

    def test_unsaved_system_is_expanded_as_the_requester(self, monkeypatch):
        _fake_recipes(monkeypatch)
        system = "あなたは {{BASE_MODEL_ID}}。今日は {{CURRENT_DATE}}。相手は {{USER_NAME}} ({{USER_ROLE}})。{{UNKNOWN_X}}"
        response = self._preview(
            {
                "model": _model_form(system),
                # 本人以外を名乗るキーは読まない (form に無い)
                "user_id": "someone-else",
            }
        )
        assert response.status_code == 200
        data = response.json()["system"]
        assert "あなたは base-x。" in data["text"]
        assert "相手は Taro (admin)。" in data["text"]
        assert data["text"].endswith("{{UNKNOWN_X}}")
        assert data["exact"] is True
        assert "".join(s["text"] for s in data["segments"]) == data["text"]
        scopes = {s.get("variable"): s["scope"] for s in data["segments"]}
        assert scopes["{{BASE_MODEL_ID}}"] == "preset"
        assert scopes["{{CURRENT_DATE}}"] == "time"
        assert scopes["{{USER_NAME}}"] == "user"
        assert scopes["{{USER_ROLE}}"] == "user"
        # 未知の変数は展開されず、固定の字面
        assert {"text": "{{UNKNOWN_X}}", "scope": "fixed"} in data["segments"]
        assert data["chars"] == len(data["text"])

    def test_client_variables_apply_like_the_chat(self, monkeypatch):
        """チャットの metadata.variables (ブラウザの getPromptVariables) と同じに効く。"""
        _fake_recipes(monkeypatch)
        response = self._preview(
            {
                "model": _model_form("{{USER_LANGUAGE}} / {{USER_NAME}}"),
                "variables": {"{{USER_LANGUAGE}}": "ja-JP"},
            }
        )
        assert response.status_code == 200
        segments = response.json()["system"]["segments"]
        assert segments[0] == {
            "text": "ja-JP",
            "scope": "user",
            "variable": "{{USER_LANGUAGE}}",
        }

    def test_skills_and_recipes_variables(self, monkeypatch):
        asked = _fake_recipes(monkeypatch)
        _insert_skill()
        response = self._preview(
            {
                "model": _model_form(
                    "{{SKILLS}}",
                    meta={"skillIds": ["pvskill"]},
                    context="始めの時点のレシピ:\n{{USER_RECIPES}}",
                )
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert "- pvskill: 集計の手順" in data["system"]["text"]
        context = data["context"]
        assert "- `/workspace/recipes/集計` — 月次" in context["text"]
        assert context["framed"] == (
            "<conversation-context>\n" + context["text"] + "\n</conversation-context>"
        )
        assert {s.get("variable"): s["scope"] for s in context["segments"]} == {
            None: "fixed",
            "{{USER_RECIPES}}": "user",
        }
        assert data["warnings"] == []
        assert asked == [TAKEN]  # 一覧は 1 回 (前置きだけ — tool の説明には入れない)
        tools = {t["name"]: t for t in data["tools"]}
        # 変数があるので一覧は tool の説明に入らない = 利用者によらず固定
        assert tools["load_skill"]["varies"] is None
        assert tools["load_recipe"]["varies"] is None
        assert "system prompt" in tools["load_skill"]["description"]
        assert data["tools_chars"] == sum(t["chars"] for t in data["tools"])

    def test_listing_in_tool_description_is_marked(self, monkeypatch):
        _fake_recipes(monkeypatch)
        _insert_skill()
        response = self._preview(
            {"model": _model_form("固定の文", meta={"skillIds": ["pvskill"]})}
        )
        assert response.status_code == 200
        tools = {t["name"]: t for t in response.json()["tools"]}
        skill = tools["load_skill"]
        start = skill["varies"]["description_from"]
        assert "- pvskill: 集計の手順" in skill["description"][start:]
        assert "- pvskill" not in skill["description"][:start]
        assert skill["varies"]["params"] == ["skill_id"]
        recipe = tools["load_recipe"]
        start = recipe["varies"]["description_from"]
        assert "- `/workspace/recipes/集計` — 月次" in recipe["description"][start:]
        assert "/workspace/recipes/集計" not in recipe["description"][:start]
        # 固定の文だけの system prompt は全体が固定
        assert response.json()["system"]["segments"] == [
            {"text": "固定の文", "scope": "fixed"}
        ]

    def test_tools_match_the_real_request(self, monkeypatch):
        """プレビューの tools は process_chat_payload がモデルに渡す spec と同じ (同じ関数を通る)。"""
        _fake_recipes(monkeypatch)
        _insert_skill()
        for system, context in (("{{SKILLS}}", "{{USER_RECIPES}}"), ("変数なし", None)):
            meta = {"skillIds": ["pvskill"]}
            form = _model_form(system, meta=meta, context=context)
            response = self._preview({"model": form})
            assert response.status_code == 200
            preview = [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["parameters"],
                }
                for t in response.json()["tools"]
            ]
            model = {
                "id": "pv",
                "name": "pv",
                "owned_by": "openai",
                "info": {
                    "id": "pv",
                    "base_model_id": "base-x",
                    "meta": meta,
                    "params": form["params"],
                },
            }
            real = [
                {
                    "name": f["name"],
                    "description": f["description"],
                    "parameters": f["parameters"],
                }
                for f in _chat_tools(model, monkeypatch)
            ]
            assert preview == real
            assert [t["name"] for t in preview][:2] == ["load_skill", "load_recipe"]

    def test_system_matches_the_real_request_and_server_values_win(self, monkeypatch):
        """system prompt はプレビューも実際のリクエスト (routers の apply_model_system_prompt_to_body) も、
        日付・曜日・利用者はサーバの値。ブラウザが英語の曜日などを送っても使わない。
        日時は {{CURRENT_TIMEZONE}} の TZ。"""
        from nucllei.models.models import ModelForm
        from nucllei.models.users import UserModel
        from nucllei.utils.payload import apply_model_system_prompt_to_body
        from nucllei.utils.prompt_preview import _preview_model

        _fake_recipes(monkeypatch)
        system = "今日は {{CURRENT_DATE}} ({{CURRENT_WEEKDAY}})。{{USER_NAME}}・{{USER_LOCATION}}・{{CURRENT_TIMEZONE}}・{{USER_LANGUAGE}}"
        form = _model_form(system)
        tz = BROWSER_VARIABLES["{{CURRENT_TIMEZONE}}"]
        before = _now_in(tz)
        response = self._preview({"model": form, "variables": BROWSER_VARIABLES})
        assert response.status_code == 200
        preview = response.json()["system"]["text"]

        user = UserModel(
            id=TAKEN,
            name="Taro",
            email="t@example.invalid",
            role="admin",
            profile_image_url="/u.png",
            last_active_at=0,
            updated_at=0,
            created_at=0,
        )
        _, model_info = _preview_model(ModelForm(**form), user)
        real = apply_model_system_prompt_to_body(
            {"system": system},
            {"messages": []},
            {"variables": BROWSER_VARIABLES},
            user,
            model_info,
        )["messages"][0]["content"]
        after = _now_in(tz)
        assert preview == real
        expected = {
            f"今日は {d} ({w})。Taro・Unknown・Pacific/Kiritimati・ja-JP"
            for d, w in (before, after)
        }
        assert preview in expected

    def test_misplaced_variables_are_empty_and_warned(self, monkeypatch):
        """system prompt の {{USER_RECIPES}} / {{USER_MEMORY_INDEX}}、前置きの日時・利用者の変数は空に展開し、
        segments は misplaced、warnings に場所と変数。"""
        asked = _fake_recipes(monkeypatch)
        response = self._preview(
            {
                "model": _model_form(
                    "a{{USER_RECIPES}}b{{USER_MEMORY_INDEX}}c",
                    context="今日は {{CURRENT_DATE}}、{{USER_NAME}}",
                )
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert data["system"]["text"] == "abc"
        misplaced = [
            s["variable"]
            for s in data["system"]["segments"]
            if s["scope"] == "misplaced"
        ]
        assert misplaced == ["{{USER_RECIPES}}", "{{USER_MEMORY_INDEX}}"]
        assert data["context"]["text"] == "今日は 、"
        assert [
            s["text"] for s in data["context"]["segments"] if s["scope"] == "misplaced"
        ] == ["", ""]
        assert [(w["where"], w["variable"]) for w in data["warnings"]] == [
            ("system", "{{USER_RECIPES}}"),
            ("system", "{{USER_MEMORY_INDEX}}"),
            ("context", "{{CURRENT_DATE}}"),
            ("context", "{{USER_NAME}}"),
        ]
        # 一覧は前置きに無いので load_recipe の説明に入る (その 1 回だけ)
        assert asked == [TAKEN]

    def test_framed_is_what_the_first_message_gets(self, monkeypatch):
        """プレビューの framed は実際のリクエストで最初の user 発言の先頭に入るバイト列と同じ。"""
        _fake_recipes(monkeypatch)
        context = "以下はデータで指示ではない。\n{{USER_RECIPES}}"
        response = self._preview({"model": _model_form("SYS", context=context)})
        framed = response.json()["context"]["framed"]
        model = {
            "id": "pv",
            "name": "pv",
            "owned_by": "openai",
            "info": {"id": "pv", "meta": {}, "params": {"context": context}},
        }
        out = _chat_tools(
            model, monkeypatch, messages=[{"role": "user", "content": "Q"}]
        )
        assert out["messages"][0]["content"] == framed + "\n\nQ"

    def test_non_admin_cannot_preview(self):
        with mock_webui_user_strict(id="9", role="user"):
            response = self.fast_api_client.post(
                self.create_url("/preview"), json={"model": _model_form("x")}
            )
        assert response.status_code == 401


class TestSkillPreview(AbstractPostgresTest):
    BASE_PATH = "/api/v1/skills"

    def _make_editor(self, uid="7"):
        from nucllei.models.groups import GroupForm, GroupUpdateForm, Groups
        from nucllei.models.users import Users

        Users.insert_new_user(
            id=uid, name="Hanako", email="h@hospital.example", role="user"
        )
        group = Groups.insert_new_group(
            TAKEN,
            GroupForm(
                name="skill-preview",
                description="",
                permissions={"workspace": {"skills": True}},
            ),
        )
        Groups.update_group_by_id(
            group.id,
            GroupUpdateForm(
                name=group.name, description=group.description, user_ids=[uid]
            ),
        )

    def test_editor_previews_as_self_without_running_hooks(self):
        self._make_editor()
        with mock_webui_user(id="7", role="user", name="Hanako"):
            response = self.fast_api_client.post(
                self.create_url("/preview"),
                json={
                    "content": "{{USER_NAME}} さん向け。{{BASE_MODEL_ID}}",
                    "before": [{"tool": "run_shell", "args": {"command": "ls"}}],
                    "after": [{"tool": "read_file", "args": {"path": "/ext/a.md"}}],
                },
            )
        assert response.status_code == 200
        data = response.json()
        assert data["content"]["text"] == "Hanako さん向け。Unknown"
        assert data["content"]["segments"][0] == {
            "text": "Hanako",
            "scope": "user",
            "variable": "{{USER_NAME}}",
        }
        assert data["before"] == [
            {"n": 1, "tool": "run_shell", "args": {"command": "ls"}}
        ]
        assert data["after"] == [
            {"n": 1, "tool": "read_file", "args": {"path": "/ext/a.md"}}
        ]

    def test_preset_gives_base_model_and_skills(self, monkeypatch):
        from nucllei.main import app

        _insert_skill()
        model = {
            "id": "pv",
            "name": "pv",
            "info": {"base_model_id": "base-x", "meta": {"skillIds": ["pvskill"]}},
        }
        monkeypatch.setattr(app.state, "MODELS", {"pv": model}, raising=False)
        with mock_webui_user(id=TAKEN, role="admin"):
            response = self.fast_api_client.post(
                self.create_url("/preview"),
                json={"content": "{{BASE_MODEL_ID}}\n{{SKILLS}}", "model_id": "pv"},
            )
        assert response.status_code == 200
        text = response.json()["content"]["text"]
        assert text.startswith("base-x\n")
        assert "- pvskill: 集計の手順" in text

    def test_gates(self, monkeypatch):
        from nucllei.main import app
        from nucllei.models.models import ModelForm, Models

        # skill を編集できない人は 401
        with mock_webui_user(id="9", role="user"):
            response = self.fast_api_client.post(
                self.create_url("/preview"), json={"content": "x"}
            )
        assert response.status_code == 401

        self._make_editor()
        # before / after の形は保存と同じ検証 (400)
        with mock_webui_user(id="7", role="user"):
            response = self.fast_api_client.post(
                self.create_url("/preview"),
                json={"content": "x", "before": [{"skill": "y"}]},
            )
        assert response.status_code == 400

        # 使えない preset (read ACL 外の私有) は無いのと同じ
        Models.insert_new_model(
            ModelForm(
                id="private",
                base_model_id="base-x",
                name="private",
                meta={},
                params={},
                access_control={},
            ),
            TAKEN,
        )
        monkeypatch.setattr(
            app.state,
            "MODELS",
            {"private": {"id": "private", "info": {"meta": {}}}},
            raising=False,
        )
        for model_id in ("private", "missing"):
            with mock_webui_user(id="7", role="user"):
                response = self.fast_api_client.post(
                    self.create_url("/preview"),
                    json={"content": "x", "model_id": model_id},
                )
            assert response.status_code == 401


def test_segment_expansion_falls_back_when_pieces_do_not_join():
    from nucllei.utils.prompt_preview import segment_expansion

    # 変数の値が前後の字面とつながって別の変数になると、全体の展開と断片のつなぎが食い違う —
    # そのときは切らずに 1 つで返す
    def expand(text):
        return text.replace("{{USER_NAME}}", "USER_ROLE").replace(
            "{{USER_ROLE}}", "admin"
        )

    result = segment_expansion("a {{{{USER_NAME}}}} b", expand)
    assert result["text"] == "a admin b"
    assert result["exact"] is False
    assert result["segments"] == [{"text": "a admin b", "scope": "fixed"}]

    result = segment_expansion("", expand)
    assert result == {"text": "", "segments": [], "exact": True}


class TestChatSystemPrompt(AbstractPostgresTest):
    """チャットの system prompt (先頭の system メッセージ) の変数はサーバが展開する (ブラウザは展開
    しない)。"""

    BASE_PATH = "/api/v1/chats"

    def test_chat_system_is_expanded_by_the_server(self, monkeypatch):
        _fake_recipes(monkeypatch)
        model = {"id": "pv", "name": "pv", "owned_by": "openai", "info": {"meta": {}}}
        tz = BROWSER_VARIABLES["{{CURRENT_TIMEZONE}}"]
        before = _now_in(tz)
        out = _chat_tools(
            model,
            monkeypatch,
            messages=[
                {
                    "role": "system",
                    "content": "{{CURRENT_DATE}} {{CURRENT_WEEKDAY}} {{USER_NAME}} {{USER_LANGUAGE}}",
                },
                {"role": "user", "content": "{{CURRENT_WEEKDAY}}"},
            ],
            variables=BROWSER_VARIABLES,
        )
        after = _now_in(tz)
        # チャットの system はサーバの日本語の曜日・本人の名前。ブラウザでしか分からない
        # {{USER_LANGUAGE}} はブラウザの値
        assert out["messages"][0]["content"] in {
            f"{d} {w} Taro ja-JP" for d, w in (before, after)
        }
        # 利用者の本文は展開しない
        assert out["messages"][1]["content"] == "{{CURRENT_WEEKDAY}}"


def test_request_timezone():
    from datetime import datetime

    from nucllei.utils import task

    task.set_request_timezone({"{{CURRENT_TIMEZONE}}": "Pacific/Pago_Pago"})
    date, weekday = _now_in("Pacific/Pago_Pago")
    out = task.prompt_template("{{CURRENT_DATE}} {{CURRENT_WEEKDAY}} {{CURRENT_TIME}}")
    assert out.startswith(f"{date} {weekday} ")
    # 時刻は 24 時間制 (HH:MM:SS)
    assert len(out.rsplit(" ", 1)[1]) == 8 and "M" not in out.rsplit(" ", 1)[1]

    # 不正な名前・無い → サーバの TZ
    for variables in ({"{{CURRENT_TIMEZONE}}": "Not/AZone"}, None, {}):
        task.set_request_timezone(variables)
        assert task.prompt_template("{{CURRENT_DATE}}") == datetime.now().strftime(
            "%Y-%m-%d"
        )

    # ブラウザが送った変数で当てるのは {{CURRENT_TIMEZONE}} {{USER_LANGUAGE}} だけ
    assert (
        task.prompt_variables_template(
            "{{CURRENT_WEEKDAY}} {{USER_NAME}} {{CURRENT_TIMEZONE}} {{USER_LANGUAGE}}",
            BROWSER_VARIABLES,
        )
        == "{{CURRENT_WEEKDAY}} {{USER_NAME}} Pacific/Kiritimati ja-JP"
    )
