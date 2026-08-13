"""agent session(kind="agent" の一級チャット — issue #107)の router / model 層回帰。

- HTTP 経由の書き込み(update / delete / pin / archive / share)は 400 で拒否。
  clone だけは通す(= 「介入したいときは fork」— 生成物は kind="user"・親リンク無し)。
- ユーザー向け一覧(サイドバー・検索・archived)から agent session を除外する。
  admin 一覧は除外せず、kind フィルタで絞れる。
- GET /{id}/children は所有者のみ。deleted 済みの子は返さない。
- 親(user chat)削除で子・孫が cascade soft delete される。
"""

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user


class TestAgentSessions(AbstractPostgresTest):
    BASE_PATH = "/api/v1/chats"

    def setup_class(cls):
        super().setup_class()
        from nucllei.models.chats import Chats

        cls.chats = Chats

    def _insert_chat(self, user_id="2", title="chat", **kwargs):
        from nucllei.models.chats import ChatForm

        chat = self.chats.insert_new_chat(
            user_id,
            ChatForm(
                chat={"title": title, "history": {"currentId": None, "messages": {}}}
            ),
            **kwargs,
        )
        assert chat is not None
        return chat

    def _insert_agent_chat(self, user_id="2", parent=None, title="agent", depth=1):
        return self._insert_chat(
            user_id,
            title=title,
            kind="agent",
            parent_chat_id=parent.id if parent else None,
            parent_message_id="pm-1",
            depth=depth,
        )

    # ---- HTTP 書き込みガード ----

    def test_agent_chat_write_operations_rejected(self):
        parent = self._insert_chat(title="parent")
        agent = self._insert_agent_chat(parent=parent)

        with mock_webui_user(id="2"):
            # update(M3)
            r = self.fast_api_client.post(
                self.create_url(f"/{agent.id}"),
                json={"chat": {"title": "hijacked"}},
            )
            assert r.status_code == 400, r.text
            # delete(M4 — 親削除の cascade でのみ消える)
            r = self.fast_api_client.delete(self.create_url(f"/{agent.id}"))
            assert r.status_code == 400, r.text
            # pin(M6)
            r = self.fast_api_client.post(self.create_url(f"/{agent.id}/pin"))
            assert r.status_code == 400, r.text
            # archive(M7)
            r = self.fast_api_client.post(self.create_url(f"/{agent.id}/archive"))
            assert r.status_code == 400, r.text
            # share(M9 — /s/ 公開の拒否)
            r = self.fast_api_client.post(self.create_url(f"/{agent.id}/share"))
            assert r.status_code == 400, r.text

        # DB は無傷(soft delete も archive も share も起きていない)
        refreshed = self.chats.get_chat_by_id(agent.id)
        assert refreshed.title == "agent"
        assert refreshed.deleted_at is None
        assert refreshed.archived is False
        assert refreshed.share_id is None

    def test_admin_cannot_directly_delete_agent_chat(self):
        parent = self._insert_chat(title="parent")
        agent = self._insert_agent_chat(parent=parent)

        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.delete(self.create_url(f"/{agent.id}"))
        assert r.status_code == 400, r.text
        assert self.chats.get_chat_by_id(agent.id).deleted_at is None

    def test_agent_chat_readable_and_clonable(self):
        # 閲覧と clone(M11)はガードしない — clone 生成物は kind=user・親リンク無し
        parent = self._insert_chat(title="parent")
        agent = self._insert_agent_chat(parent=parent)

        with mock_webui_user(id="2"):
            r = self.fast_api_client.get(self.create_url(f"/{agent.id}"))
        assert r.status_code == 200, r.text

        with mock_webui_user(id="2"):
            r = self.fast_api_client.post(
                self.create_url(f"/{agent.id}/clone"), json={}
            )
        assert r.status_code == 200, r.text
        clone_id = r.json()["id"]
        assert clone_id != agent.id

        clone = self.chats.get_chat_by_id(clone_id)
        assert clone.kind == "user"
        assert clone.parent_chat_id is None
        assert clone.parent_message_id is None
        assert clone.depth == 0

    # ---- ユーザー向け一覧からの除外 ----

    def test_agent_chats_excluded_from_user_lists(self):
        parent = self._insert_chat(title="findme parent")
        self._insert_agent_chat(parent=parent, title="findme agent")

        with mock_webui_user(id="2"):
            # サイドバー一覧(L1)
            r = self.fast_api_client.get(self.create_url("/"))
            assert {c["id"] for c in r.json()} == {parent.id}
            # 検索(L3 — title 一致でも agent は出ない)
            r = self.fast_api_client.get(self.create_url("/search?text=findme"))
            assert {c["id"] for c in r.json()} == {parent.id}
            # 全件(L4)
            r = self.fast_api_client.get(self.create_url("/all"))
            assert {c["id"] for c in r.json()} == {parent.id}

    def test_agent_chats_excluded_from_archived_lists(self):
        parent = self._insert_chat(title="parent")
        agent = self._insert_agent_chat(parent=parent)
        # model 層には archive ガードは無い(HTTP 経由のみ拒否)。仮に archived が
        # 立っても archived 一覧(L6/L7)には出ないことを固定する。
        self.chats.toggle_chat_archive_by_id(agent.id)
        self.chats.toggle_chat_archive_by_id(parent.id)

        with mock_webui_user(id="2"):
            r = self.fast_api_client.get(self.create_url("/archived"))
            assert {c["id"] for c in r.json()} == {parent.id}
            r = self.fast_api_client.get(self.create_url("/all/archived"))
            assert {c["id"] for c in r.json()} == {parent.id}

    def test_archive_all_skips_agent_chats(self):
        parent = self._insert_chat(title="parent")
        agent = self._insert_agent_chat(parent=parent)

        with mock_webui_user(id="2"):
            r = self.fast_api_client.post(self.create_url("/archive/all"))
        assert r.status_code == 200

        assert self.chats.get_chat_by_id(parent.id).archived is True
        assert self.chats.get_chat_by_id(agent.id).archived is False

    # ---- admin 一覧の kind フィルタ ----

    def test_admin_list_kind_filter(self):
        parent = self._insert_chat(title="user chat")
        agent = self._insert_agent_chat(parent=parent, title="agent chat")

        with mock_webui_user(id="1", role="admin"):
            # 既定は両方(監査系は除外しない)。行に kind が載る
            r = self.fast_api_client.get(self.create_url("/admin/list"))
            body = r.json()
            assert body["total"] == 2
            kinds = {i["title"]: i["kind"] for i in body["items"]}
            assert kinds == {"user chat": "user", "agent chat": "agent"}

            r = self.fast_api_client.get(
                self.create_url("/admin/list", {"kind": "agent"})
            )
            body = r.json()
            assert body["total"] == 1
            assert body["items"][0]["id"] == agent.id

            r = self.fast_api_client.get(
                self.create_url("/admin/list", {"kind": "user"})
            )
            body = r.json()
            assert body["total"] == 1
            assert body["items"][0]["id"] == parent.id

            r = self.fast_api_client.get(
                self.create_url("/admin/list", {"kind": "bogus"})
            )
            assert r.status_code == 400, r.text

    # ---- 子一覧 API ----

    def test_children_endpoint_owner_only(self):
        parent = self._insert_chat(title="parent")
        child_a = self._insert_agent_chat(parent=parent, title="child a")
        child_b = self._insert_agent_chat(parent=parent, title="child b")
        removed = self._insert_agent_chat(parent=parent, title="removed")
        # model 層の soft delete(cascade 経路と同じ)— deleted は children に出ない
        assert self.chats.delete_chat_by_id(removed.id)

        with mock_webui_user(id="2"):
            r = self.fast_api_client.get(self.create_url(f"/{parent.id}/children"))
        assert r.status_code == 200, r.text
        children = r.json()
        assert {c["id"] for c in children} == {child_a.id, child_b.id}
        for c in children:
            assert set(c.keys()) == {
                "id",
                "title",
                "kind",
                "updated_at",
                "created_at",
                "parent_message_id",
            }
            assert c["kind"] == "agent"
            assert c["parent_message_id"] == "pm-1"

        # 所有者以外は 401(admin でも他人チャットの children は見せない)
        with mock_webui_user(id="3", role="user"):
            r = self.fast_api_client.get(self.create_url(f"/{parent.id}/children"))
        assert r.status_code == 401
        with mock_webui_user(id="3", role="admin"):
            r = self.fast_api_client.get(self.create_url(f"/{parent.id}/children"))
        assert r.status_code == 401

    # ---- cascade delete ----

    def test_parent_delete_cascades_to_descendants(self):
        parent = self._insert_chat(title="parent")
        child = self._insert_agent_chat(parent=parent, title="child", depth=1)
        grandchild = self._insert_chat(
            "2",
            title="grandchild",
            kind="agent",
            parent_chat_id=child.id,
            parent_message_id="cm-1",
            depth=2,
        )
        # 孫まで BFS で収集される(層の順は親に近い方から)
        assert self.chats.get_descendant_chat_ids(parent.id) == [
            child.id,
            grandchild.id,
        ]

        with mock_webui_user(id="2"):
            r = self.fast_api_client.delete(self.create_url(f"/{parent.id}"))
        assert r.status_code == 200
        assert r.json() is True

        for chat_id in (parent.id, child.id, grandchild.id):
            assert self.chats.get_chat_by_id(chat_id).deleted_at is not None

        # 削除後は子孫収集からも消える(deleted 除外)
        assert self.chats.get_descendant_chat_ids(parent.id) == []

    def test_cascade_does_not_touch_other_users_chats(self):
        # delete_chat_by_id_and_user_id の user_id 述語: 他人の chat id を parent に
        # 偽装しても巻き添えにならない(子孫は spawn 時に同一 user_id が前提)
        parent = self._insert_chat(user_id="2", title="parent")
        foreign = self._insert_chat(
            user_id="9",
            title="foreign",
            kind="agent",
            parent_chat_id=parent.id,
            parent_message_id="pm-1",
            depth=1,
        )

        with mock_webui_user(id="2"):
            r = self.fast_api_client.delete(self.create_url(f"/{parent.id}"))
        assert r.status_code == 200

        assert self.chats.get_chat_by_id(parent.id).deleted_at is not None
        assert self.chats.get_chat_by_id(foreign.id).deleted_at is None

    def test_delete_all_user_chats_includes_agent_sessions(self):
        parent = self._insert_chat(title="parent")
        agent = self._insert_agent_chat(parent=parent)

        with mock_webui_user(id="2"):
            r = self.fast_api_client.delete(self.create_url("/"))
        assert r.status_code == 200

        # user_id 一括の soft delete に agent session も自然に含まれる
        assert self.chats.get_chat_by_id(parent.id).deleted_at is not None
        assert self.chats.get_chat_by_id(agent.id).deleted_at is not None
