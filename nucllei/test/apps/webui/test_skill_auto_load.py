"""添付による skill の自動ロード(入口 3)の回帰テスト。

skill が `auto_load_content_types`(content_type の前方一致リスト)を宣言していて、
会話に条件に合う添付があれば、`/command` と同じ合成 load_skill exchange が注入される
(utils/skills.py apply_skill_auto_load。設計は _memos/skill-design.md「入口 3」)。

カバー範囲:
- 条件の宣言: API で保存でき、正規化される(小文字化・空要素と重複の除去)
- 発火する / 条件に合わなければしない(content_type は DB の値で判定)
- 認可は `/command` と同じ AND(機能有効 ∧ モデル紐づき ∧ read 可視)+ 読めない添付は無視
- 後のターン(添付の無い発言)でも再導出される / カード(current)は添付の付いたターンだけ
- 二重発火しない(`/command`・モデル自律 load_skill が既にあれば足さない / 添付複数でも 1 回)
- `/command`・load_skill との併用(位置と tool_ids の再導出)
- 画像(user メッセージの image_url パート)は付いたメッセージの直後
"""

import json
import types

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user


def _model_with_skills(*skill_ids, features=None):
    meta = {"skillIds": list(skill_ids)}
    if features is not None:
        meta["features"] = features
    return {"info": {"meta": meta}}


def _file_entry(file_id):
    """チャット wire のリクエスト直下 `files` の 1 件(FE が送る形の最小)。"""
    return {"type": "file", "id": file_id}


def _load_skill_call(skill_id, call_id="functions.load_skill:0"):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": "load_skill",
                    "arguments": json.dumps({"skill_id": skill_id}),
                },
            }
        ],
    }


def _called_skill_ids(messages):
    return [
        json.loads(call["function"]["arguments"])["skill_id"]
        for message in messages
        for call in message.get("tool_calls") or []
    ]


class TestSkillAutoLoad(AbstractPostgresTest):
    BASE_PATH = "/api/v1/skills"

    def setup_method(self):
        super().setup_method()
        self.user = types.SimpleNamespace(id="1", name="Taro", role="user")

    def _skill(self, skill_id="audio", content_types=("audio/",), **kwargs):
        from nucllei.models.skills import SkillForm, Skills

        skill = Skills.insert_new_skill(
            kwargs.pop("owner", "1"),
            SkillForm(
                id=skill_id,
                name=skill_id,
                content=kwargs.pop("content", "音声が添付されています"),
                auto_load_content_types=list(content_types),
                access_control=kwargs.pop("access_control", None),
                **kwargs,
            ),
        )
        assert skill is not None
        return skill

    def _file(self, file_id, content_type, owner="1"):
        from nucllei.models.files import FileForm, Files

        file = Files.insert_new_file(
            owner,
            FileForm(
                id=file_id,
                filename=file_id,
                path=f"/nonexistent/{file_id}",
                meta={"name": file_id, "content_type": content_type},
                data={},
            ),
        )
        assert file is not None
        return _file_entry(file_id)

    def _apply(self, form_data, model, metadata=None, user=None):
        from nucllei.utils.skills import apply_skill_auto_load

        return apply_skill_auto_load(form_data, user or self.user, model, metadata)

    # ── 条件の宣言 ──────────────────────────────────────────────

    def test_auto_load_content_types_are_stored_and_normalized(self):
        with mock_webui_user(id="2", role="admin"):
            response = self.fast_api_client.post(
                self.create_url("/create"),
                json={
                    "id": "audio",
                    "name": "音声",
                    "content": "本文",
                    "auto_load_content_types": [" Audio/ ", "", "audio/", "video/"],
                },
            )
        assert response.status_code == 200
        assert response.json()["auto_load_content_types"] == ["audio/", "video/"]

        # 省略時は空(自動ロードしない)
        with mock_webui_user(id="2", role="admin"):
            response = self.fast_api_client.post(
                self.create_url("/create"),
                json={"id": "plain", "name": "p", "content": "本文"},
            )
        assert response.json()["auto_load_content_types"] == []

        # 更新で差し替わる
        with mock_webui_user(id="2", role="admin"):
            response = self.fast_api_client.post(
                self.create_url("/id/audio/update"),
                json={
                    "id": "audio",
                    "name": "音声",
                    "content": "本文",
                    "auto_load_content_types": ["image/"],
                },
            )
        assert response.status_code == 200
        assert response.json()["auto_load_content_types"] == ["image/"]

    # ── 発火 ────────────────────────────────────────────────────

    def test_fires_on_matching_attachment(self):
        skill = self._skill(tool_ids=["audio-tools"], content="{{USER_NAME}} の音声")
        # Chrome は audio/webm、Safari は audio/mp4 — 拡張子ではなく content_type の前方一致
        files = [self._file("rec", "audio/webm;codecs=opus")]

        form_data = {
            "messages": [{"role": "user", "content": "これ聞いて"}],
            "files": files,
        }
        result, activations = self._apply(form_data, _model_with_skills(skill.id))

        msgs = result["messages"]
        assert [m["role"] for m in msgs] == ["user", "assistant", "tool"]
        assert msgs[0]["content"] == "これ聞いて"  # 生のまま
        call = msgs[1]["tool_calls"][0]
        assert call["id"] == "skill:audio:auto"
        assert call["function"]["name"] == "load_skill"
        assert json.loads(call["function"]["arguments"]) == {"skill_id": "audio"}
        assert msgs[2]["tool_call_id"] == call["id"]
        assert json.loads(msgs[2]["content"]) == {"result": "Taro の音声", "tools": []}

        # 発火情報は `/command` と同じ形(tool_ids の取り込み・result の書き戻しに使う)
        assert len(activations) == 1
        assert activations[0]["id"] == "audio"
        assert activations[0]["tool_ids"] == ["audio-tools"]
        assert activations[0]["result_message"] is msgs[2]

    def test_does_not_fire_without_matching_attachment(self):
        skill = self._skill()
        model = _model_with_skills(skill.id)
        messages = [{"role": "user", "content": "見て"}]

        # 添付なし
        result, activations = self._apply({"messages": list(messages)}, model)
        assert activations == [] and len(result["messages"]) == 1

        # 条件に合わない添付(PDF)だけ
        form_data = {
            "messages": list(messages),
            "files": [self._file("doc", "application/pdf")],
        }
        result, activations = self._apply(form_data, model)
        assert activations == [] and len(result["messages"]) == 1

        # 画像だけ(image_url パート)でも audio/ の skill は発火しない
        self._file("img", "image/png")
        form_data = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "見て"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "/api/v1/files/img/content"},
                        },
                    ],
                }
            ]
        }
        result, activations = self._apply(form_data, model)
        assert activations == [] and len(result["messages"]) == 1

    def test_content_type_comes_from_db_not_from_client(self):
        """クライアント申告の meta は信用しない(DB の content_type で判定)。"""
        skill = self._skill()
        entry = self._file("doc", "application/pdf")
        entry["file"] = {"meta": {"content_type": "audio/webm"}}
        entry["name"] = "rec.webm"

        form_data = {"messages": [{"role": "user", "content": "x"}], "files": [entry]}
        _, activations = self._apply(form_data, _model_with_skills(skill.id))
        assert activations == []

        # DB に無い file id も無視(エラーにしない)
        form_data = {
            "messages": [{"role": "user", "content": "x"}],
            "files": [_file_entry("ghost")],
        }
        _, activations = self._apply(form_data, _model_with_skills(skill.id))
        assert activations == []

    # ── 認可 ────────────────────────────────────────────────────

    def test_authorization_is_the_same_and_as_slash_command(self):
        skill = self._skill()
        files = [self._file("rec", "audio/webm")]

        def fire(model, user=None):
            form_data = {
                "messages": [{"role": "user", "content": "x"}],
                "files": list(files),
            }
            _, activations = self._apply(form_data, model, user=user)
            return [a["id"] for a in activations]

        assert fire(_model_with_skills(skill.id)) == ["audio"]
        # 機能「スキル」OFF
        assert fire(_model_with_skills(skill.id, features={"skills": False})) == []
        # モデルに紐づいていない
        assert fire(_model_with_skills("other")) == []
        assert fire({"info": {"meta": {}}}) == []

    def test_invisible_skill_and_unreadable_file_do_not_fire(self):
        # 私有 skill(owner=9)は user 1 から不可視
        private = self._skill("private-audio", owner="9", access_control={})
        files = [self._file("rec", "audio/webm")]
        form_data = {"messages": [{"role": "user", "content": "x"}], "files": files}
        _, activations = self._apply(form_data, _model_with_skills(private.id))
        assert activations == []

        # 他人の file(読めない)は条件の判定に使わない
        public = self._skill("public-audio")
        theirs = [self._file("theirs", "audio/webm", owner="9")]
        form_data = {"messages": [{"role": "user", "content": "x"}], "files": theirs}
        _, activations = self._apply(form_data, _model_with_skills(public.id))
        assert activations == []

    # ── 持続 ────────────────────────────────────────────────────

    def test_persists_on_later_turns(self):
        """添付はチャットの一覧として毎リクエスト届く — 添付の無い発言のターンでも再導出される。"""
        skill = self._skill(tool_ids=["audio-tools"])
        files = [self._file("rec", "audio/webm")]

        form_data = {
            "messages": [
                {"role": "user", "content": "これ聞いて"},
                {"role": "assistant", "content": "聞きました"},
                {"role": "user", "content": "もう一度読み直して"},
            ],
            "files": files,
        }
        result, activations = self._apply(form_data, _model_with_skills(skill.id))

        # 位置は最初の user メッセージの直後で固定(ターンが進んでも動かない)
        assert [m["role"] for m in result["messages"]] == [
            "user",
            "assistant",
            "tool",
            "assistant",
            "user",
        ]
        assert activations[0]["tool_ids"] == ["audio-tools"]
        # chat 文脈なし(headless)ではカードの seed 対象にしない
        assert activations[0]["current"] is False

    def test_card_is_seeded_only_on_the_turn_the_attachment_was_added(self):
        """current(UI カードの seed)は「いまの user メッセージに付いた添付」で決める。"""
        from nucllei.models.chats import ChatForm, Chats

        skill = self._skill()
        entry = self._file("rec", "audio/webm")
        chat = Chats.insert_new_chat(
            "1",
            ChatForm(
                chat={
                    "title": "t",
                    "history": {
                        "currentId": "a2",
                        "messages": {
                            "u1": {"id": "u1", "role": "user", "parentId": None},
                            "a1": {"id": "a1", "role": "assistant", "parentId": "u1"},
                            "u2": {
                                "id": "u2",
                                "role": "user",
                                "parentId": "a1",
                                "files": [entry],
                            },
                            "a2": {"id": "a2", "role": "assistant", "parentId": "u2"},
                            "u3": {"id": "u3", "role": "user", "parentId": "a2"},
                            "a3": {"id": "a3", "role": "assistant", "parentId": "u3"},
                        },
                    },
                }
            ),
        )
        model = _model_with_skills(skill.id)

        def current(message_id, n_turns):
            messages = []
            for i in range(n_turns):
                messages.append({"role": "user", "content": f"発言 {i}"})
                if i < n_turns - 1:
                    messages.append({"role": "assistant", "content": "応答"})
            _, activations = self._apply(
                {"messages": messages, "files": [entry]},
                model,
                metadata={"chat_id": chat.id, "message_id": message_id},
            )
            return activations[0]["current"]

        # 2 ターン目(録音を添付した発言への応答 a2)= カードを出す
        assert current("a2", 2) is True
        # 3 ターン目(添付の無い発言への応答 a3)= 発火は続くがカードは出さない
        assert current("a3", 3) is False

    # ── 二重発火 ────────────────────────────────────────────────

    def test_fires_once_for_multiple_matching_attachments(self):
        skill = self._skill()
        files = [self._file("rec1", "audio/webm"), self._file("rec2", "audio/mp4")]
        form_data = {"messages": [{"role": "user", "content": "x"}], "files": files}
        result, activations = self._apply(form_data, _model_with_skills(skill.id))
        assert len(activations) == 1
        assert _called_skill_ids(result["messages"]) == ["audio"]

    def test_does_not_fire_when_already_loaded(self):
        from nucllei.utils.skills import apply_skill_command

        skill = self._skill(tool_ids=["audio-tools"])
        model = _model_with_skills(skill.id)
        files = [self._file("rec", "audio/webm")]

        # `/command` で同じ skill が発火済み(同じリクエストで再導出される)
        form_data = {
            "messages": [{"role": "user", "content": "/audio これ聞いて"}],
            "files": list(files),
        }
        form_data, commands = apply_skill_command(form_data, self.user, model)
        result, activations = self._apply(form_data, model)
        assert len(commands) == 1 and activations == []
        assert _called_skill_ids(result["messages"]) == ["audio"]

        # モデル自律の load_skill が履歴にある(tool_calls 形)
        form_data = {
            "messages": [
                {"role": "user", "content": "これ聞いて"},
                _load_skill_call("audio"),
                {"role": "tool", "tool_call_id": "functions.load_skill:0"},
                {"role": "assistant", "content": "聞きました"},
                {"role": "user", "content": "続けて"},
            ],
            "files": list(files),
        }
        result, activations = self._apply(form_data, model)
        assert activations == []
        assert len(result["messages"]) == 5

    # ── 併用 ────────────────────────────────────────────────────

    def test_combines_with_slash_command_and_load_skill(self):
        from nucllei.utils.skills import apply_skill_command, replay_skill_tool_ids

        audio = self._skill(tool_ids=["audio-tools"])
        notes = self._skill("notes", content_types=(), tool_ids=["notes-tools"])
        doc = self._skill("doc", content_types=(), tool_ids=["doc-tools"])
        model = _model_with_skills(notes.id, doc.id, audio.id)

        form_data = {
            "messages": [
                {"role": "user", "content": "/notes 議事録にして"},
                _load_skill_call("doc"),
                {"role": "tool", "tool_call_id": "functions.load_skill:0"},
                {"role": "assistant", "content": "はい"},
                {"role": "user", "content": "続けて"},
            ],
            "files": [self._file("rec", "audio/webm")],
        }
        form_data, commands = apply_skill_command(form_data, self.user, model)
        result, activations = self._apply(form_data, model)

        # 同じ user メッセージの `/command` exchange の後ろに自動ロード分が並ぶ
        assert [m["role"] for m in result["messages"]] == [
            "user",
            "assistant",  # /notes(合成)
            "tool",
            "assistant",  # audio(自動ロード)
            "tool",
            "assistant",  # doc(モデル自律)
            "tool",
            "assistant",
            "user",
        ]
        assert _called_skill_ids(result["messages"]) == ["notes", "audio", "doc"]
        assert [a["id"] for a in commands] == ["notes"]
        assert [a["id"] for a in activations] == ["audio"]
        # モデル自律分の tool は履歴から再導出される(自動ロードを足しても変わらない)
        assert "doc-tools" in replay_skill_tool_ids(result, self.user, model)

    def test_multiple_skills_fire_in_model_order(self):
        self._skill("b-audio")
        self._skill("a-audio")
        self._skill("images", content_types=("image/",))
        files = [self._file("rec", "audio/webm")]
        form_data = {"messages": [{"role": "user", "content": "x"}], "files": files}
        # 順序は meta.skillIds の順(決定的)。条件に合わない images は発火しない
        result, activations = self._apply(
            form_data, _model_with_skills("b-audio", "images", "a-audio")
        )
        assert [a["id"] for a in activations] == ["b-audio", "a-audio"]
        assert _called_skill_ids(result["messages"]) == ["b-audio", "a-audio"]

    # ── 画像(user メッセージの image_url パート)────────────────

    def test_image_part_fires_right_after_its_message(self):
        skill = self._skill("images", content_types=("image/",))
        self._file("img", "image/png")
        model = _model_with_skills(skill.id)

        def image_message(url, text="見て"):
            return {
                "role": "user",
                "content": [
                    {"type": "text", "text": text},
                    {"type": "image_url", "image_url": {"url": url}},
                ],
            }

        form_data = {
            "messages": [
                {"role": "user", "content": "こんにちは"},
                {"role": "assistant", "content": "はい"},
                image_message("/api/v1/files/img/content"),
            ]
        }
        result, activations = self._apply(form_data, model)
        assert [m["role"] for m in result["messages"]] == [
            "user",
            "assistant",
            "user",
            "assistant",
            "tool",
        ]
        # 画像はメッセージに載って来るので、付いたターンが payload だけで分かる
        assert activations[0]["current"] is True

        # data URL(headless API クライアント)は MIME で判定
        form_data = {"messages": [image_message("data:image/jpeg;base64,AAAA")]}
        _, activations = self._apply(form_data, model)
        assert [a["id"] for a in activations] == ["images"]

        # tool 結果の画像を運ぶ user メッセージ(tool の直後)は利用者の添付ではない
        form_data = {
            "messages": [
                {"role": "user", "content": "グラフを描いて"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "functions.run_python:0",
                            "type": "function",
                            "function": {"name": "run_python", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "functions.run_python:0"},
                image_message("/api/v1/files/img/content", "(直前の tool 結果の画像)"),
                {"role": "assistant", "content": "描きました"},
                {"role": "user", "content": "ありがとう"},
            ]
        }
        _, activations = self._apply(form_data, model)
        assert activations == []
