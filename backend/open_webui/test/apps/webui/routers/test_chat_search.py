"""会話検索(タイトル + 本文)の回帰テスト(issue #12)。

v0.6.5 の `Chats.get_chats_by_user_id_and_search_text`(models/chats.py)を
API 境界(GET /api/v1/chats/search)で固定する。KEEP 判断
(_memos/feature-triage.md「会話検索: 必要・維持。デグレさせない」)の担保。

検索仕様: タイトルは ilike、本文は chat JSON トップレベルの messages[].content
を LIKE(PostgreSQL は json_array_elements)。archived は既定で除外。
"""

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user


class TestChatSearch(AbstractPostgresTest):
    BASE_PATH = "/api/v1/chats"

    def setup_method(self):
        super().setup_method()
        from open_webui.models.chats import ChatForm, Chats

        self.chats = Chats

        def make(user_id, title, contents, archived=False):
            chat = self.chats.insert_new_chat(
                user_id,
                ChatForm(
                    **{
                        "chat": {
                            "title": title,
                            "messages": [
                                {"role": "user", "content": c} for c in contents
                            ],
                            "history": {"currentId": None, "messages": {}},
                        }
                    }
                ),
            )
            if archived:
                self.chats.toggle_chat_archive_by_id(chat.id)
            return chat

        self.own_title_hit = make("2", "検査結果の相談", ["よろしくお願いします"])
        self.own_body_hit = make("2", "New Chat", ["肺炎の既往があります", "了解です"])
        self.own_miss = make("2", "雑談", ["天気の話"])
        self.own_archived = make(
            "2", "検査予約(過去)", ["アーカイブ済み"], archived=True
        )
        self.other_user = make("999", "検査データ", ["他人のチャット"])

    def _search(self, text):
        with mock_webui_user(id="2"):
            response = self.fast_api_client.get(self.create_url(f"/search?text={text}"))
        assert response.status_code == 200
        return {c["id"] for c in response.json()}

    def test_title_match(self):
        ids = self._search("検査結果")
        assert self.own_title_hit.id in ids
        assert self.own_miss.id not in ids

    def test_message_content_match(self):
        ids = self._search("肺炎")
        assert ids == {self.own_body_hit.id}

    def test_case_insensitive_title_match(self):
        from open_webui.models.chats import ChatForm

        chat = self.chats.insert_new_chat(
            "2",
            ChatForm(
                **{
                    "chat": {
                        "title": "CT Scan Review",
                        "messages": [{"role": "user", "content": "read this"}],
                        "history": {"currentId": None, "messages": {}},
                    }
                }
            ),
        )
        assert chat.id in self._search("ct scan")

    def test_other_users_chats_are_invisible(self):
        # 他ユーザーのチャットはタイトルが一致しても出ない(user_id で絞る)
        ids = self._search("検査")
        assert self.other_user.id not in ids

    def test_archived_excluded_by_default(self):
        ids = self._search("検査")
        assert self.own_archived.id not in ids
        assert self.own_title_hit.id in ids

    def test_no_match_returns_empty(self):
        assert self._search("存在しない語句xyz") == set()
