"""組み込みツール機構(issue #42)の回帰。

- T1: 起動時 sync で builtin 行ができ、get_tools が repo モジュールから callable を返す
- T2: content 更新・削除・id の横取り(create)は拒否。access_control のみ更新可
- T5: builtin 行にも access_control(4pcg ガード)が効く
"""

import types

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user


def _fake_request():
    return types.SimpleNamespace(
        app=types.SimpleNamespace(state=types.SimpleNamespace(TOOLS={}))
    )


class TestBuiltinTools(AbstractPostgresTest):
    BASE_PATH = "/api/v1/tools"

    def setup_class(cls):
        super().setup_class()
        from nucllei.models.tools import Tools

        cls.Tools = Tools

    def _sync(self):
        from nucllei.builtin_tools import sync_builtin_tools

        sync_builtin_tools()

    def test_sync_creates_builtin_row_and_get_tools_loads_module(self):
        from nucllei.utils.tools import get_tools

        self._sync()

        row = self.Tools.get_tool_by_id("builtin_history")
        assert row is not None
        assert row.meta.builtin is True
        assert row.content == ""
        spec_names = {s["name"] for s in row.specs}
        assert spec_names == {"list_history", "read_history", "grep_history"}

        # get_tools は DB 行 → レジストリのモジュールへ振り向く(content は使わない)
        loaded = get_tools(
            _fake_request(),
            ["builtin_history"],
            types.SimpleNamespace(id="u1", role="user"),
            {"__user__": {"id": "u1"}},
        )
        assert set(loaded.keys()) == {"list_history", "read_history", "grep_history"}
        assert loaded["list_history"]["spec"]["name"] == "list_history"

    def test_sync_is_idempotent_and_refreshes_from_repo(self):
        self._sync()
        # 手で specs を汚しても再 sync で repo から復元される
        self.Tools.update_tool_by_id(
            "builtin_history", {"specs": [], "content": "tampered"}
        )
        self._sync()
        row = self.Tools.get_tool_by_id("builtin_history")
        assert row.content == ""
        assert len(row.specs) == 3

    def test_sync_prunes_builtin_removed_from_repo(self):
        """repo から消えた builtin(`builtin_` prefix だが BUILTIN_TOOLS 未定義)は
        次 sync で DB から削除される。定義済みは残る。"""
        from nucllei.models.tools import ToolForm, ToolMeta

        self._sync()
        # コードから撤去された古い builtin(旧 builtin_view_image 相当)を模す
        self.Tools.insert_new_tool(
            "system",
            ToolForm(
                id="builtin_removed",
                name="Old",
                description="gone from repo",
                content="",
                meta=ToolMeta(builtin=True),
                access_control=None,
            ),
            [],
        )
        assert self.Tools.get_tool_by_id("builtin_removed") is not None

        self._sync()
        assert self.Tools.get_tool_by_id("builtin_removed") is None
        assert self.Tools.get_tool_by_id("builtin_history") is not None

    def test_create_rejects_reserved_prefix(self):
        """`builtin_` prefix は名前空間ごと予約 — 未定義 id でも create 不可。"""
        with mock_webui_user(id="admin1", role="admin"):
            res = self.fast_api_client.post(
                self.create_url("/create"),
                json={
                    "id": "builtin_myown",
                    "name": "n",
                    "description": "d",
                    "content": "class Tools:\n    def x(self):\n        return 'x'",
                },
            )
        assert res.status_code == 400

    def test_update_allows_only_access_control(self):
        self._sync()
        body = {
            "id": "builtin_history",
            "name": "乗っ取り",
            "description": "hacked",
            "content": "class Tools:\n    def pwn(self):\n        return 'x'",
            "meta": {"builtin": False},
            "access_control": {},
        }
        with mock_webui_user(id="admin1", role="admin"):
            res = self.fast_api_client.post(
                self.create_url("/id/builtin_history/update"), json=body
            )
        assert res.status_code == 200

        row = self.Tools.get_tool_by_id("builtin_history")
        # 定義は repo が真実源のまま
        assert row.content == ""
        assert row.name == "History Management"
        assert row.meta.builtin is True
        # access_control だけ反映される
        assert row.access_control == {}

    def test_delete_and_create_are_rejected(self):
        self._sync()
        with mock_webui_user(id="admin1", role="admin"):
            res = self.fast_api_client.delete(
                self.create_url("/id/builtin_history/delete")
            )
        assert res.status_code == 400

        # 予約 id の create も拒否(sync 前の空 DB でも横取りできない)
        self.Tools.delete_tool_by_id("builtin_history")
        with mock_webui_user(id="admin1", role="admin"):
            res = self.fast_api_client.post(
                self.create_url("/create"),
                json={
                    "id": "builtin_history",
                    "name": "偽物",
                    "description": "d",
                    "content": "class Tools:\n    def fake(self):\n        return 'x'",
                },
            )
        assert res.status_code == 400

    def test_access_control_guards_builtin_row(self):
        """4pcg 同型: リクエスト由来 id は builtin 行でも read 検証される(T5)。"""
        from nucllei.utils.tools import get_tools

        self._sync()
        # 私有化(owner="system" のみ) → 一般ユーザーには載らない
        self.Tools.update_tool_by_id("builtin_history", {"access_control": {}})

        denied = get_tools(
            _fake_request(),
            ["builtin_history"],
            types.SimpleNamespace(id="u2", role="user"),
            {"__user__": {"id": "u2"}},
        )
        assert denied == {}

        # admin はバイパス(既存セマンティクス)
        granted = get_tools(
            _fake_request(),
            ["builtin_history"],
            types.SimpleNamespace(id="a1", role="admin"),
            {"__user__": {"id": "a1"}},
        )
        assert "list_history" in granted

    def test_tools_list_endpoint_exposes_specs(self):
        """llens(#49): GET /tools/ が specs(関数一覧)を返す — picker が中身を表示するため。"""
        self._sync()
        with mock_webui_user(id="admin1", role="admin"):
            res = self.fast_api_client.get(self.create_url("/"))
        assert res.status_code == 200
        row = next(t for t in res.json() if t["id"] == "builtin_history")
        spec_names = {s["name"] for s in row["specs"]}
        assert spec_names == {"list_history", "read_history", "grep_history"}


class TestBuiltinHistoryTool(AbstractPostgresTest):
    """履歴管理ツール(builtin_history)の挙動(#42 本実装)。owner-only を固定する。"""

    BASE_PATH = "/api/v1/tools"

    def setup_class(cls):
        super().setup_class()
        from nucllei.models.chats import Chats

        cls.Chats = Chats

    def _new_chat(self, user_id, title, messages):
        """実 OWUI 形の chat を 1 件作る。

        本物のチャットは flat な `messages` リスト(DB の全文検索が走査する)と
        分岐用 `history.messages` マップ(read_history のスレッド復元が使う)の両方を持つ。
        """
        from nucllei.models.chats import ChatForm

        flat = []
        msg_map = {}
        prev = None
        for i, (role, content) in enumerate(messages):
            mid = f"{title}-{i}"
            msg = {
                "id": mid,
                "parentId": prev,
                "childrenIds": [],
                "role": role,
                "content": content,
            }
            flat.append(msg)
            msg_map[mid] = dict(msg)
            if prev:
                msg_map[prev]["childrenIds"] = [mid]
            prev = mid

        return self.Chats.insert_new_chat(
            user_id,
            ChatForm(
                chat={
                    "title": title,
                    "messages": flat,
                    "history": {"messages": msg_map, "currentId": prev},
                }
            ),
        )

    def test_list_history_returns_only_own_chats(self):
        from nucllei.builtin_tools.history import Tools

        self._new_chat("owner", "私のチャット", [("user", "こんにちは")])
        self._new_chat("other", "他人のチャット", [("user", "hi")])

        out = Tools().list_history(__user__={"id": "owner"})
        assert "私のチャット" in out
        assert "他人のチャット" not in out

    def test_read_history_renders_thread(self):
        from nucllei.builtin_tools.history import Tools

        chat = self._new_chat(
            "owner",
            "会話",
            [("user", "天気は?"), ("assistant", "晴れです")],
        )
        out = Tools().read_history(chat.id, __user__={"id": "owner"})
        assert "USER: 天気は?" in out
        assert "ASSISTANT: 晴れです" in out

    def test_read_history_is_owner_only(self):
        from nucllei.builtin_tools.history import Tools

        chat = self._new_chat("owner", "秘密", [("user", "内緒")])
        # 他人が chat_id を知っていても「見つからない」扱い(所有者検証を漏らさない)
        out = Tools().read_history(chat.id, __user__={"id": "intruder"})
        assert "見つかりません" in out
        assert "内緒" not in out

    def test_grep_history_matches_content(self):
        from nucllei.builtin_tools.history import Tools

        self._new_chat("owner", "旅行の計画", [("user", "京都に行きたい")])
        self._new_chat("owner", "買い物", [("user", "牛乳を買う")])

        out = Tools().grep_history("京都", __user__={"id": "owner"})
        assert "旅行の計画" in out
        assert "買い物" not in out

    def test_history_requires_user_context(self):
        import pytest

        from nucllei.builtin_tools.history import Tools

        with pytest.raises(ValueError):
            Tools().list_history(__user__=None)
