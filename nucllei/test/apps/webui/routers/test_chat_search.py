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
        from nucllei.models.chats import ChatForm, Chats

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
        from nucllei.models.chats import ChatForm

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


class TestChatSearchNul(AbstractPostgresTest):
    """NUL(U+0000)を含むメッセージがあってもチャット検索が 500 にならない。

    Postgres の json 型は \\u0000 を含む文書を保存できるが、検索の
    `message->>'content'`(text 変換)で例外になり検索全体が落ちていた。
    保存時に NUL を落とし(NulFreeJSON)、既に入っている行も検索側で避ける。
    """

    BASE_PATH = "/api/v1/chats"

    def _insert(self, title, content):
        from nucllei.models.chats import ChatForm, Chats

        return Chats.insert_new_chat(
            "2",
            ChatForm(
                chat={
                    "title": title,
                    "messages": [{"role": "user", "content": content}],
                    "history": {"currentId": None, "messages": {}},
                }
            ),
        )

    def _search(self, text):
        with mock_webui_user(id="2"):
            response = self.fast_api_client.get(self.create_url(f"/search?text={text}"))
        assert response.status_code == 200, response.text
        return {c["id"] for c in response.json()}

    def test_nul_is_stripped_on_save(self):
        from nucllei.models.chats import Chats

        chat = self._insert("タイ\x00トル", "肺\x00炎の既往")
        stored = Chats.get_chat_by_id(chat.id)
        assert stored.title == "タイトル"
        assert stored.chat["messages"][0]["content"] == "肺炎の既往"
        assert chat.id in self._search("肺炎")

    def test_search_survives_nul_already_in_db(self):
        import json

        from sqlalchemy import text

        from nucllei.internal.db import get_db

        poisoned = self._insert("毒入り", "placeholder")
        normal = self._insert("普通", "肺炎の相談")
        # 保存時の除去を経ない既存行を再現(生 SQL で \u0000 入りの json を書く)
        raw = json.dumps(
            {
                "title": "毒入り",
                "messages": [{"role": "user", "content": "肺炎\u0000です"}],
            }
        )
        assert "\\u0000" in raw
        with get_db() as db:
            db.execute(
                text("UPDATE chat SET chat = CAST(:j AS json) WHERE id = :id"),
                {"j": raw, "id": poisoned.id},
            )
            db.commit()

        ids = self._search("肺炎")
        assert normal.id in ids
        assert poisoned.id in ids  # JSON 表現のまま照合して拾える
        assert self._search("存在しない語句xyz") == set()
