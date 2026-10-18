import uuid

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user


class TestChats(AbstractPostgresTest):
    BASE_PATH = "/api/v1/chats"

    def setup_class(cls):
        super().setup_class()

    def setup_method(self):
        super().setup_method()
        from nucllei.models.chats import ChatForm, Chats

        self.chats = Chats
        self.chats.insert_new_chat(
            "2",
            ChatForm(
                **{
                    "chat": {
                        "name": "chat1",
                        "description": "chat1 description",
                        "tags": ["tag1", "tag2"],
                        "history": {"currentId": "1", "messages": []},
                    }
                }
            ),
        )

    def test_get_session_user_chat_list(self):
        with mock_webui_user(id="2"):
            response = self.fast_api_client.get(self.create_url("/"))
        assert response.status_code == 200
        first_chat = response.json()[0]
        assert first_chat["id"] is not None
        assert first_chat["title"] == "New Chat"
        assert first_chat["created_at"] is not None
        assert first_chat["updated_at"] is not None

    def test_delete_all_user_chats(self):
        # llens(#31): soft delete — 行は残り(監査証憑)、ユーザー向け一覧からは消える
        with mock_webui_user(id="2"):
            response = self.fast_api_client.delete(self.create_url("/"))
        assert response.status_code == 200

        remaining = self.chats.get_chats()
        assert len(remaining) == 1
        assert remaining[0].deleted_at is not None

        with mock_webui_user(id="2"):
            response = self.fast_api_client.get(self.create_url("/"))
        assert response.status_code == 200
        assert response.json() == []

    def test_get_user_chat_list_by_user_id(self):
        with mock_webui_user(id="3"):
            response = self.fast_api_client.get(self.create_url("/list/user/2"))
        assert response.status_code == 200
        first_chat = response.json()[0]
        assert first_chat["id"] is not None
        assert first_chat["title"] == "New Chat"
        assert first_chat["created_at"] is not None
        assert first_chat["updated_at"] is not None

    def test_create_new_chat(self):
        with mock_webui_user(id="2"):
            response = self.fast_api_client.post(
                self.create_url("/new"),
                json={
                    "chat": {
                        "name": "chat2",
                        "description": "chat2 description",
                        "tags": ["tag1", "tag2"],
                    }
                },
            )
        assert response.status_code == 200
        data = response.json()
        assert data["archived"] is False
        assert data["chat"] == {
            "name": "chat2",
            "description": "chat2 description",
            "tags": ["tag1", "tag2"],
        }
        assert data["user_id"] == "2"
        assert data["id"] is not None
        assert data["share_id"] is None
        assert data["title"] == "New Chat"
        assert data["updated_at"] is not None
        assert data["created_at"] is not None
        assert len(self.chats.get_chats()) == 2

    def test_get_user_chats(self):
        self.test_get_session_user_chat_list()

    def test_get_user_archived_chats(self):
        self.chats.archive_all_chats_by_user_id("2")
        from nucllei.internal.db import Session

        Session.commit()
        with mock_webui_user(id="2"):
            response = self.fast_api_client.get(self.create_url("/all/archived"))
        assert response.status_code == 200
        first_chat = response.json()[0]
        assert first_chat["id"] is not None
        assert first_chat["title"] == "New Chat"
        assert first_chat["created_at"] is not None
        assert first_chat["updated_at"] is not None

    def test_get_all_user_chats_in_db(self):
        with mock_webui_user(id="4"):
            response = self.fast_api_client.get(self.create_url("/all/db"))
        assert response.status_code == 200
        assert len(response.json()) == 1

    def test_get_archived_session_user_chat_list(self):
        self.test_get_user_archived_chats()

    def test_archive_all_chats(self):
        with mock_webui_user(id="2"):
            response = self.fast_api_client.post(self.create_url("/archive/all"))
        assert response.status_code == 200
        assert len(self.chats.get_archived_chats_by_user_id("2")) == 1

    def test_get_shared_chat_by_id(self):
        chat_id = self.chats.get_chats()[0].id
        self.chats.update_chat_share_id_by_id(chat_id, chat_id)
        with mock_webui_user(id="2"):
            response = self.fast_api_client.get(self.create_url(f"/share/{chat_id}"))
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == chat_id
        assert data["chat"] == {
            "name": "chat1",
            "description": "chat1 description",
            "tags": ["tag1", "tag2"],
            "history": {"currentId": "1", "messages": []},
        }
        assert data["id"] == chat_id
        assert data["share_id"] == chat_id
        assert data["title"] == "New Chat"

    def test_get_chat_by_id(self):
        chat_id = self.chats.get_chats()[0].id
        with mock_webui_user(id="2"):
            response = self.fast_api_client.get(self.create_url(f"/{chat_id}"))
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == chat_id
        assert data["chat"] == {
            "name": "chat1",
            "description": "chat1 description",
            "tags": ["tag1", "tag2"],
            "history": {"currentId": "1", "messages": []},
        }
        assert data["share_id"] is None
        assert data["title"] == "New Chat"
        assert data["user_id"] == "2"

    def test_update_chat_by_id(self):
        chat_id = self.chats.get_chats()[0].id
        with mock_webui_user(id="2"):
            response = self.fast_api_client.post(
                self.create_url(f"/{chat_id}"),
                json={
                    "chat": {
                        "name": "chat2",
                        "description": "chat2 description",
                        "tags": ["tag2", "tag4"],
                        "title": "Just another title",
                    }
                },
            )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == chat_id
        assert data["chat"] == {
            "name": "chat2",
            "title": "Just another title",
            "description": "chat2 description",
            "tags": ["tag2", "tag4"],
            "history": {"currentId": "1", "messages": []},
        }
        assert data["share_id"] is None
        assert data["title"] == "Just another title"
        assert data["user_id"] == "2"

    def test_delete_chat_by_id(self):
        # llens(#31): soft delete — 行は残り(deleted_at)、本人の取得・一覧からは消える
        chat_id = self.chats.get_chats()[0].id
        with mock_webui_user(id="2"):
            response = self.fast_api_client.delete(self.create_url(f"/{chat_id}"))
        assert response.status_code == 200
        assert response.json() is True

        chat = self.chats.get_chat_by_id(chat_id)
        assert chat is not None
        assert chat.deleted_at is not None

        with mock_webui_user(id="2"):
            response = self.fast_api_client.get(self.create_url(f"/{chat_id}"))
        assert response.status_code == 401

        with mock_webui_user(id="2"):
            response = self.fast_api_client.get(self.create_url("/"))
        assert response.json() == []

    def test_delete_chat_unshares_it(self):
        # llens(#31): 削除で share_id が外れ、共有コピー行も消える(/s/ が開けなくなる)
        chat_id = self.chats.get_chats()[0].id
        share_id = str(uuid.uuid4())
        self.chats.update_chat_share_id_by_id(chat_id, share_id)
        with mock_webui_user(id="2"):
            response = self.fast_api_client.delete(self.create_url(f"/{chat_id}"))
        assert response.status_code == 200

        chat = self.chats.get_chat_by_id(chat_id)
        assert chat.share_id is None
        assert self.chats.get_chat_by_share_id(share_id) is None

    def test_deleted_chat_admin_only_view_via_share_endpoint(self):
        # llens(#19): 削除済みチャットは admin だけが /share/{chat_id} の直参照で
        # 閲覧できる(deleted_at で「削除済み」を明示)。非 admin には存在しない扱い。
        chat_id = self.chats.get_chats()[0].id
        with mock_webui_user(id="2"):
            response = self.fast_api_client.delete(self.create_url(f"/{chat_id}"))
        assert response.status_code == 200

        with mock_webui_user(id="3", role="admin"):
            response = self.fast_api_client.get(self.create_url(f"/share/{chat_id}"))
        assert response.status_code == 200
        assert response.json()["deleted_at"] is not None

        with mock_webui_user(id="4", role="user"):
            response = self.fast_api_client.get(self.create_url(f"/share/{chat_id}"))
        assert response.status_code == 401

    def test_deleted_chat_not_found_via_get_chat(self):
        # llens(#19): /chats/{id}(/c/ の裏)は admin フォールバックでも削除済みを返さない
        chat_id = self.chats.get_chats()[0].id
        with mock_webui_user(id="2"):
            response = self.fast_api_client.delete(self.create_url(f"/{chat_id}"))
        assert response.status_code == 200

        with mock_webui_user(id="3", role="admin"):
            response = self.fast_api_client.get(self.create_url(f"/{chat_id}"))
        assert response.status_code == 401

    def test_clone_chat_by_id(self):
        chat_id = self.chats.get_chats()[0].id
        with mock_webui_user(id="2"):
            response = self.fast_api_client.post(
                self.create_url(f"/{chat_id}/clone"), json={}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] != chat_id
        assert data["chat"] == {
            "branchPointMessageId": "1",
            "description": "chat1 description",
            "history": {"currentId": "1", "messages": []},
            "name": "chat1",
            "originalChatId": chat_id,
            "tags": ["tag1", "tag2"],
            "title": "Clone of New Chat",
        }
        assert data["share_id"] is None
        assert data["title"] == "Clone of New Chat"
        assert data["user_id"] == "2"

    def test_archive_chat_by_id(self):
        chat_id = self.chats.get_chats()[0].id
        with mock_webui_user(id="2"):
            response = self.fast_api_client.post(self.create_url(f"/{chat_id}/archive"))
        assert response.status_code == 200

        chat = self.chats.get_chat_by_id(chat_id)
        assert chat.archived is True

    def test_share_chat_by_id(self):
        chat_id = self.chats.get_chats()[0].id
        with mock_webui_user(id="2"):
            response = self.fast_api_client.post(self.create_url(f"/{chat_id}/share"))
        assert response.status_code == 200

        chat = self.chats.get_chat_by_id(chat_id)
        assert chat.share_id is not None

    def test_delete_shared_chat_by_id(self):
        chat_id = self.chats.get_chats()[0].id
        share_id = str(uuid.uuid4())
        self.chats.update_chat_share_id_by_id(chat_id, share_id)
        with mock_webui_user(id="2"):
            response = self.fast_api_client.delete(self.create_url(f"/{chat_id}/share"))
        assert response.status_code

        chat = self.chats.get_chat_by_id(chat_id)
        assert chat.share_id is None


class TestChatAppendOnlyHistory(AbstractPostgresTest):
    """llens(#31): メッセージ履歴の append-only ガード。

    監査証憑の保全のため、POST /{id} の全量更新で「既存メッセージの消失」と
    「content の非前方一致な書き換え」を 400 で拒否する。追記・ブランチ追加・
    メタ情報(title 等)の変更は通す。
    """

    BASE_PATH = "/api/v1/chats"

    def setup_method(self):
        super().setup_method()
        from nucllei.models.chats import ChatForm, Chats

        self.chats = Chats
        self.history = {
            "currentId": "m2",
            "messages": {
                "m1": {
                    "id": "m1",
                    "parentId": None,
                    "childrenIds": ["m2"],
                    "role": "user",
                    "content": "hello",
                },
                "m2": {
                    "id": "m2",
                    "parentId": "m1",
                    "childrenIds": [],
                    "role": "assistant",
                    "content": "hi there",
                },
            },
        }
        chat = self.chats.insert_new_chat(
            "2", ChatForm(**{"chat": {"title": "t", "history": self.history}})
        )
        self.chat_id = chat.id

    def _update(self, history: dict, extra: dict | None = None):
        with mock_webui_user(id="2"):
            return self.fast_api_client.post(
                self.create_url(f"/{self.chat_id}"),
                json={"chat": {"history": history, **(extra or {})}},
            )

    def _history(self, mutate) -> dict:
        import copy

        history = copy.deepcopy(self.history)
        mutate(history)
        return history

    def test_removing_message_is_rejected(self):
        def mutate(h):
            del h["messages"]["m2"]
            h["messages"]["m1"]["childrenIds"] = []
            h["currentId"] = "m1"

        response = self._update(self._history(mutate))
        assert response.status_code == 400
        # DB は無傷
        chat = self.chats.get_chat_by_id(self.chat_id)
        assert set(chat.chat["history"]["messages"].keys()) == {"m1", "m2"}

    def test_rewriting_content_is_rejected(self):
        def mutate(h):
            h["messages"]["m1"]["content"] = "HELLO (rewritten)"

        response = self._update(self._history(mutate))
        assert response.status_code == 400
        chat = self.chats.get_chat_by_id(self.chat_id)
        assert chat.chat["history"]["messages"]["m1"]["content"] == "hello"

    def test_appending_content_is_allowed(self):
        # ストリーミング終端の保存等: 旧 content が前方一致で保存される追記は通す
        def mutate(h):
            h["messages"]["m2"]["content"] = "hi there, how can I help?"

        response = self._update(self._history(mutate))
        assert response.status_code == 200

    def test_adding_branch_is_allowed(self):
        # 「編集して送信」= 兄弟ブランチの追加(既存メッセージは無傷)
        def mutate(h):
            h["messages"]["m3"] = {
                "id": "m3",
                "parentId": "m1",
                "childrenIds": [],
                "role": "assistant",
                "content": "alternative answer",
            }
            h["messages"]["m1"]["childrenIds"] = ["m2", "m3"]
            h["currentId"] = "m3"

        response = self._update(self._history(mutate))
        assert response.status_code == 200

    def test_metadata_only_update_is_allowed(self):
        response = self._update(self._history(lambda h: None), {"title": "renamed"})
        assert response.status_code == 200
        assert response.json()["chat"]["title"] == "renamed"

    def test_message_overwrite_endpoint_is_removed(self):
        # llens(#31): POST /{id}/messages/{message_id}(無痕跡上書き API)は撤去済み
        with mock_webui_user(id="2"):
            response = self.fast_api_client.post(
                self.create_url(f"/{self.chat_id}/messages/m1"),
                json={"content": "overwritten"},
            )
        assert response.status_code in (404, 405)


class TestChatHistoryValidation(AbstractPostgresTest):
    """llens: 保存 / 作成 / インポートで history の木構造を検証する(DoS 対策)。

    parentId が循環した履歴を一度保存させると、履歴を辿る get_message_list が
    無限ループしてイベントループごとサーバー全体がハングする。受け付け時点で
    「parentId 実在・循環なし・currentId 実在」を満たさなければ 400 にする。
    """

    BASE_PATH = "/api/v1/chats"

    def _history(self):
        return {
            "currentId": "m2",
            "messages": {
                "m1": {"id": "m1", "parentId": None, "childrenIds": ["m2"]},
                "m2": {"id": "m2", "parentId": "m1", "childrenIds": []},
            },
        }

    def setup_method(self):
        super().setup_method()
        from nucllei.models.chats import ChatForm, Chats

        self.chats = Chats
        chat = self.chats.insert_new_chat(
            "2", ChatForm(**{"chat": {"title": "t", "history": self._history()}})
        )
        self.chat_id = chat.id

    def _post(self, path, history, **extra):
        with mock_webui_user(id="2"):
            return self.fast_api_client.post(
                self.create_url(path),
                json={"chat": {"title": "t", "history": history}, **extra},
            )

    def _cycle(self):
        h = self._history()
        h["messages"]["m1"]["parentId"] = "m2"  # m1 <-> m2
        return h

    def _dangling_parent(self):
        h = self._history()
        h["messages"]["m2"]["parentId"] = "ghost"
        return h

    def _missing_current(self):
        h = self._history()
        h["currentId"] = "ghost"
        return h

    def _self_parent(self):
        h = self._history()
        h["messages"]["m1"]["parentId"] = "m1"
        return h

    def test_update_rejects_parent_cycle(self):
        # append-only ガード(content 不変)は循環を通してしまう — 木構造の検証で 400
        response = self._post(f"/{self.chat_id}", self._cycle())
        assert response.status_code == 400
        stored = self.chats.get_chat_by_id(self.chat_id).chat["history"]
        assert stored["messages"]["m1"]["parentId"] is None

    def test_update_rejects_self_parent(self):
        assert self._post(f"/{self.chat_id}", self._self_parent()).status_code == 400

    def test_update_rejects_dangling_parent(self):
        response = self._post(f"/{self.chat_id}", self._dangling_parent())
        assert response.status_code == 400

    def test_update_rejects_missing_current_id(self):
        response = self._post(f"/{self.chat_id}", self._missing_current())
        assert response.status_code == 400

    def test_update_accepts_valid_history(self):
        assert self._post(f"/{self.chat_id}", self._history()).status_code == 200

    def test_create_and_import_reject_invalid_history(self):
        before = len(self.chats.get_chats())
        for bad in (self._cycle(), self._dangling_parent(), self._missing_current()):
            assert self._post("/new", bad).status_code == 400
            assert self._post("/import", bad, meta={}, pinned=False).status_code == 400
        assert len(self.chats.get_chats()) == before

    def test_create_and_import_accept_valid_history(self):
        assert self._post("/new", self._history()).status_code == 200
        response = self._post("/import", self._history(), meta={}, pinned=False)
        assert response.status_code == 200


class TestChatSaveCas(AbstractPostgresTest):
    """llens(#110 敵対検証 3): FE 保存経路(POST /{id})の CAS 化。

    保存の read と guarded UPDATE の間に配達 driver の notice 追記
    (append_delivery_turn)が挟まっても、無ガード上書きで notice が無言で
    消えない: CAS 負け → 再読 → append-only 検証を新鮮な chat で再評価し、
    通れば再試行・通らなければ 400(FE の save-400 リカバリに乗る)。
    あわせて CAS の version の厳密単調増加(敵対検証 2 — 同秒 ABA 防止)と、updated_at が
    実時刻を超えないこと(旧 max(now, 旧値+1) の未来ずれ、2026-10-19)も縛る。
    """

    BASE_PATH = "/api/v1/chats"

    def setup_method(self):
        super().setup_method()
        from nucllei.models.chats import ChatForm, Chats

        self.chats = Chats
        self.history = {
            "currentId": "m2",
            "messages": {
                "m1": {
                    "id": "m1",
                    "parentId": None,
                    "childrenIds": ["m2"],
                    "role": "user",
                    "content": "hello",
                },
                "m2": {
                    "id": "m2",
                    "parentId": "m1",
                    "childrenIds": [],
                    "role": "assistant",
                    "content": "hi there",
                },
            },
        }
        chat = self.chats.insert_new_chat(
            "2", ChatForm(**{"chat": {"title": "t", "history": self.history}})
        )
        self.chat_id = chat.id

    def _interleave_driver_append(self, monkeypatch):
        """router の read と 1 回目の guarded UPDATE の間に driver の notice
        追記が挟まった状況を再現する。"""
        original = self.chats.update_chat_by_id_guarded
        state = {"first": True}

        def racing_guarded(id, chat, expected_version):
            if state["first"]:
                state["first"] = False
                status, _ = self.chats.append_delivery_turn(
                    id,
                    {
                        "role": "user",
                        "content": "notice",
                        "meta": {"system_notice": "task"},
                    },
                    {"role": "assistant", "content": ""},
                    [],
                )
                assert status == "ok"
            return original(id, chat, expected_version)

        monkeypatch.setattr(self.chats, "update_chat_by_id_guarded", racing_guarded)

    def _notice_ids(self):
        chat = self.chats.get_chat_by_id(self.chat_id)
        return [
            mid
            for mid, m in chat.chat["history"]["messages"].items()
            if (m.get("meta") or {}).get("system_notice")
        ]

    def test_save_with_stale_history_gets_400_and_notice_survives(self, monkeypatch):
        """FE の古い全量 history での保存は CAS 負け → 再検証で append-only 違反
        (notice 消失に相当)= 400。driver の notice は履歴に残る。"""
        import copy

        self._interleave_driver_append(monkeypatch)
        with mock_webui_user(id="2"):
            response = self.fast_api_client.post(
                self.create_url(f"/{self.chat_id}"),
                json={"chat": {"history": copy.deepcopy(self.history)}},
            )
        assert response.status_code == 400
        assert len(self._notice_ids()) == 1  # notice は消えない

    def test_save_without_history_retries_and_keeps_notice(self, monkeypatch):
        """history を含まない保存(タイトル等)は CAS 負け後の再読 + 再試行で
        成功し、driver の notice はそのまま残る。"""
        self._interleave_driver_append(monkeypatch)
        with mock_webui_user(id="2"):
            response = self.fast_api_client.post(
                self.create_url(f"/{self.chat_id}"),
                json={"chat": {"title": "renamed"}},
            )
        assert response.status_code == 200, response.text
        chat = self.chats.get_chat_by_id(self.chat_id)
        assert chat.title == "renamed"
        assert len(self._notice_ids()) == 1  # notice は消えない

    def test_save_without_interleaving_succeeds_first_try(self):
        """介在が無ければ CAS は 1 回で通る(既存挙動の保存)。"""
        with mock_webui_user(id="2"):
            response = self.fast_api_client.post(
                self.create_url(f"/{self.chat_id}"),
                json={"chat": {"title": "plain"}},
            )
        assert response.status_code == 200
        assert self.chats.get_chat_by_id(self.chat_id).title == "plain"

    def test_version_is_strictly_monotonic_within_a_second(self):
        """chat 行への連続書き込みは同秒でも version が必ず前進する
        (敵対検証 2 — CAS のバージョンが秒粒度で ABA しない)。"""
        chat = self.chats.get_chat_by_id(self.chat_id)
        versions = [chat.version]

        updated = self.chats.update_chat_by_id(self.chat_id, dict(chat.chat))
        versions.append(updated.version)
        updated = self.chats.update_chat_by_id(self.chat_id, dict(chat.chat))
        versions.append(updated.version)
        updated = self.chats.toggle_chat_pinned_by_id(self.chat_id)
        versions.append(updated.version)
        updated = self.chats.toggle_chat_archive_by_id(self.chat_id)
        versions.append(updated.version)
        assert self.chats.record_usage_by_id_and_message_id(
            self.chat_id, "m2", {"prompt_tokens": 1, "completion_tokens": 1}
        )
        versions.append(self.chats.get_chat_by_id(self.chat_id).version)
        status, updated = self.chats.update_chat_by_id_guarded(
            self.chat_id, dict(chat.chat), versions[-1]
        )
        assert status == "ok"
        versions.append(updated.version)

        assert versions == list(range(versions[0], versions[0] + len(versions)))

    def test_updated_at_never_runs_ahead_of_wall_clock(self):
        """1 秒の間に何度書いても updated_at は実時刻を超えない — 旧実装は max(now,
        旧値+1) で進め、長いターンの chat が実時刻より先へずれて一覧の上に居座った
        (staging で約 2 時間先、2026-10-19)。"""
        import time

        chat = self.chats.get_chat_by_id(self.chat_id)
        for _ in range(20):
            self.chats.update_chat_by_id(self.chat_id, dict(chat.chat))
            self.chats.record_usage_by_id_and_message_id(
                self.chat_id, "m2", {"prompt_tokens": 1, "completion_tokens": 1}
            )
        fresh = self.chats.get_chat_by_id(self.chat_id)
        assert fresh.updated_at <= int(time.time())
        assert fresh.version >= chat.version + 40

    def test_guarded_update_conflicts_on_same_second_write(self):
        """読んだ後の介在書き込みが同秒(updated_at が同じ値)でも、guarded UPDATE は
        conflict になる(version で比較 — 敵対検証 2)。"""
        chat = self.chats.get_chat_by_id(self.chat_id)
        intervening = self.chats.update_chat_by_id(self.chat_id, dict(chat.chat))
        if intervening.updated_at != chat.updated_at:
            # 秒の境目をまたいだ — 同秒の条件を作り直す
            chat = intervening
            intervening = self.chats.update_chat_by_id(self.chat_id, dict(chat.chat))
        status, updated = self.chats.update_chat_by_id_guarded(
            self.chat_id, {**chat.chat, "title": "stale"}, chat.version
        )
        assert status == "conflict"
        assert updated is None
        assert self.chats.get_chat_by_id(self.chat_id).title != "stale"


# ---- migration ac4c52211a8e(chat.version と未来の updated_at の丸め)----


def _chat_version_migration():
    import importlib.util
    import pathlib

    versions = pathlib.Path(__file__).resolve().parents[4] / "migrations" / "versions"
    path = next(versions.glob("*_ac4c52211a8e_*.py"))
    spec = importlib.util.spec_from_file_location("migration_ac4c52211a8e", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_adds_version_and_clamps_future_updated_at():
    """sqlite に migration 直前の chat 表を作って upgrade を当てる: version は 0 で
    埋まり、実時刻より未来の updated_at だけが migration 時点の時刻に丸まる。"""
    import time

    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    now = int(time.time())
    rows = {"past": now - 3600, "future": now + 7200}
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            sa.text("CREATE TABLE chat (id TEXT PRIMARY KEY, updated_at BIGINT)")
        )
        for cid, updated_at in rows.items():
            conn.execute(
                sa.text("INSERT INTO chat (id, updated_at) VALUES (:id, :u)"),
                {"id": cid, "u": updated_at},
            )
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            _chat_version_migration().upgrade()
        result = {
            r.id: (r.updated_at, r.version)
            for r in conn.execute(sa.text("SELECT id, updated_at, version FROM chat"))
        }
    assert result["past"] == (now - 3600, 0)
    assert now <= result["future"][0] <= int(time.time())
    assert result["future"][1] == 0
