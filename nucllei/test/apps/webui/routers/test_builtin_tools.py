"""組み込みツール機構(issue #42)の回帰。

- T1: 起動時 sync で builtin 行ができ、get_tools が repo モジュールから callable を返す
- T2: content 更新・削除・id の横取り(create)は拒否。access_control のみ更新可
- T5: builtin 行にも access_control(4pcg ガード)が効く
"""

import json
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


class TestBuiltinChatFilesTool(AbstractPostgresTest):
    """チャット添付一覧ツール(builtin_chat_files)の挙動。

    現在チャット限定 + owner-only を固定する(ユーザー Files 全件の列挙経路を
    作らない設計 — 他人 file_id の紛れ込みはメタデータも見せない)。
    """

    BASE_PATH = "/api/v1/tools"

    def setup_class(cls):
        super().setup_class()
        from nucllei.models.chats import Chats
        from nucllei.models.files import Files

        cls.Chats = Chats
        cls.Files = Files

    def _new_file(self, user_id, file_id, name, size=1024, deleted=False):
        from nucllei.models.files import FileForm

        self.Files.insert_new_file(
            user_id,
            FileForm(
                id=file_id,
                filename=name,
                path=f"/tmp/{file_id}",
                data={},
                meta={"name": name, "content_type": "text/plain", "size": size},
            ),
        )
        if deleted:
            self.Files.soft_delete_file_by_id(file_id, user_id)

    def _new_chat(self, user_id, message_files, top_files=None):
        """message_files: メッセージごとの files 配列(添付エントリの実 OWUI 形)。"""
        from nucllei.models.chats import ChatForm

        msg_map = {}
        prev = None
        for i, files in enumerate(message_files):
            mid = f"m{i}"
            msg_map[mid] = {
                "id": mid,
                "parentId": prev,
                "childrenIds": [],
                "role": "user" if i % 2 == 0 else "assistant",
                "content": f"msg {i}",
                "files": files,
            }
            if prev:
                msg_map[prev]["childrenIds"] = [mid]
            prev = mid
        return self.Chats.insert_new_chat(
            user_id,
            ChatForm(
                chat={
                    "title": "添付テスト",
                    "files": list(top_files or []),
                    "messages": [],
                    "history": {"messages": msg_map, "currentId": prev},
                }
            ),
        )

    def test_sync_creates_chat_files_row(self):
        from nucllei.builtin_tools import sync_builtin_tools
        from nucllei.models.tools import Tools as ToolsTable

        sync_builtin_tools()
        row = ToolsTable.get_tool_by_id("builtin_chat_files")
        assert row is not None
        assert row.meta.builtin is True
        assert {s["name"] for s in row.specs} == {"list_chat_files", "grep_chat_files"}

    def test_list_collects_uploads_and_tool_attachments_in_order(self):
        from nucllei.builtin_tools.chat_files import Tools

        self._new_file("owner", "f-up", "report.pdf", size=2048)
        self._new_file("owner", "f-exp", "result.csv")
        self._new_file("owner", "f-img", "plot.png")
        chat = self._new_chat(
            "owner",
            [
                # ユーザーアップロード(file 内側 id 形)+ 旧 base64 直入れ image(id 無し)
                [
                    {"type": "file", "file": {"id": "f-up"}},
                    {"type": "image", "url": "data:image/png;base64,xx"},
                ],
                # tool 添付(トップレベル id 形)+ 画像添付 + 重複
                [
                    {"type": "file", "id": "f-exp"},
                    {"type": "image", "id": "f-img"},
                    {"type": "file", "id": "f-up"},
                ],
            ],
        )
        out = Tools().list_chat_files(__user__={"id": "owner"}, __chat_id__=chat.id)
        items = json.loads(out)
        assert [i["file_id"] for i in items] == ["f-up", "f-exp", "f-img"]
        assert items[0]["name"] == "report.pdf"
        assert items[0]["size"] == "2.0KB"
        assert items[0]["content_type"] == "text/plain"

    def test_list_is_owner_only(self):
        from nucllei.builtin_tools.chat_files import Tools

        self._new_file("owner", "f-own", "mine.txt")
        chat = self._new_chat("owner", [[{"type": "file", "id": "f-own"}]])
        out = Tools().list_chat_files(__user__={"id": "intruder"}, __chat_id__=chat.id)
        assert "見つかりません" in out
        assert "mine.txt" not in out

    def test_foreign_file_id_planted_in_own_chat_is_hidden(self):
        """chat JSON はクライアント制御(#40 と同じ前提)— 他人 file_id を紛れ込ませても
        read 権が無ければメタデータも出さない。"""
        from nucllei.builtin_tools.chat_files import Tools

        self._new_file("victim", "f-secret", "secret.xlsx")
        self._new_file("owner", "f-mine", "mine.txt")
        chat = self._new_chat(
            "owner",
            [[{"type": "file", "id": "f-secret"}, {"type": "file", "id": "f-mine"}]],
        )
        out = Tools().list_chat_files(
            __user__={"id": "owner", "role": "user"}, __chat_id__=chat.id
        )
        assert "f-secret" not in out
        assert "secret.xlsx" not in out
        assert "f-mine" in out

    def test_deleted_own_file_is_marked(self):
        from nucllei.builtin_tools.chat_files import Tools

        self._new_file("owner", "f-del", "old.txt", deleted=True)
        chat = self._new_chat("owner", [[{"type": "file", "id": "f-del"}]])
        out = Tools().list_chat_files(__user__={"id": "owner"}, __chat_id__=chat.id)
        items = json.loads(out)
        assert items[0]["file_id"] == "f-del"
        assert items[0]["deleted"] is True

    def test_grep_matches_filename_case_insensitive(self):
        from nucllei.builtin_tools.chat_files import Tools

        self._new_file("owner", "f-rep", "Report.PDF")
        self._new_file("owner", "f-csv", "data.csv")
        chat = self._new_chat(
            "owner",
            [[{"type": "file", "id": "f-rep"}, {"type": "file", "id": "f-csv"}]],
        )
        out = Tools().grep_chat_files(
            "report", __user__={"id": "owner"}, __chat_id__=chat.id
        )
        assert "Report.PDF" in out
        assert "data.csv" not in out

        miss = Tools().grep_chat_files(
            "zzz", __user__={"id": "owner"}, __chat_id__=chat.id
        )
        assert "見つかりませんでした" in miss

    def test_limit_keeps_newest_and_reports_total(self):
        from nucllei.builtin_tools.chat_files import Tools

        for i in range(3):
            self._new_file("owner", f"f-{i}", f"file{i}.txt")
        chat = self._new_chat(
            "owner", [[{"type": "file", "id": f"f-{i}"}] for i in range(3)]
        )
        out = Tools().list_chat_files(
            limit=1, __user__={"id": "owner"}, __chat_id__=chat.id
        )
        assert "添付 3 件中、新しい 1 件" in out
        assert "f-2" in out
        assert "f-0" not in out

    def test_limit_clamped_by_valves(self):
        from nucllei.builtin_tools.chat_files import Tools

        for i in range(3):
            self._new_file("owner", f"f-{i}", f"file{i}.txt")
        chat = self._new_chat(
            "owner", [[{"type": "file", "id": f"f-{i}"}] for i in range(3)]
        )
        tool = Tools()
        tool.valves = tool.Valves(max_limit=2)
        out = tool.list_chat_files(
            limit=50, __user__={"id": "owner"}, __chat_id__=chat.id
        )
        assert "添付 3 件中、新しい 2 件" in out
        assert "f-0" not in out

    def test_requires_chat_context_with_metadata_fallback(self):
        from nucllei.builtin_tools.chat_files import Tools

        out = Tools().list_chat_files(__user__={"id": "owner"})
        assert "チャット文脈" in out

        # __chat_id__ 未注入経路でも __metadata__.chat_id で解決できる
        self._new_file("owner", "f-meta", "meta.txt")
        chat = self._new_chat("owner", [[{"type": "file", "id": "f-meta"}]])
        out = Tools().list_chat_files(
            __user__={"id": "owner"}, __metadata__={"chat_id": chat.id}
        )
        assert "f-meta" in out

    def test_empty_chat_returns_message(self):
        from nucllei.builtin_tools.chat_files import Tools

        chat = self._new_chat("owner", [[]])
        out = Tools().list_chat_files(__user__={"id": "owner"}, __chat_id__=chat.id)
        assert "添付ファイルはありません" in out

    def test_top_level_chat_files_are_collected(self):
        """走査は routers/chats.get_file_ids_from_chat の同形複製 — トップレベル
        chat.files 側の半分もここで固定する(ドリフト検知がテストの役割)。"""
        from nucllei.builtin_tools.chat_files import Tools

        self._new_file("owner", "f-top", "top.txt")
        self._new_file("owner", "f-msg", "msg.txt")
        chat = self._new_chat(
            "owner",
            [[{"type": "file", "id": "f-msg"}]],
            top_files=[{"type": "file", "id": "f-top"}],
        )
        out = Tools().list_chat_files(__user__={"id": "owner"}, __chat_id__=chat.id)
        items = json.loads(out)
        assert [i["file_id"] for i in items] == ["f-top", "f-msg"]

    def test_purged_file_lists_without_name_and_grep_skips_it(self):
        """purge(#94)後は filename=None・meta が {size} に縮退する別形状。
        list は name:null + deleted:true で出し、grep は落ちずに非マッチ。"""
        from nucllei.builtin_tools.chat_files import Tools

        self._new_file("owner", "f-purged", "gone.txt", deleted=True)
        self.Files.purge_file_by_id("f-purged", "owner")
        chat = self._new_chat("owner", [[{"type": "file", "id": "f-purged"}]])

        out = Tools().list_chat_files(__user__={"id": "owner"}, __chat_id__=chat.id)
        items = json.loads(out)
        assert items[0]["file_id"] == "f-purged"
        assert items[0]["name"] is None
        assert items[0]["deleted"] is True

        miss = Tools().grep_chat_files(
            "gone", __user__={"id": "owner"}, __chat_id__=chat.id
        )
        assert "見つかりませんでした" in miss

    def test_limit_tolerates_non_int_values(self):
        """tool 引数は実行時型検証を通らない — モデル由来の 5.0 / "5" / ゴミ文字列でも落ちない。"""
        from nucllei.builtin_tools.chat_files import Tools

        for i in range(3):
            self._new_file("owner", f"f-{i}", f"file{i}.txt")
        chat = self._new_chat(
            "owner", [[{"type": "file", "id": f"f-{i}"}] for i in range(3)]
        )
        out = Tools().list_chat_files(
            limit=1.0, __user__={"id": "owner"}, __chat_id__=chat.id
        )
        assert "添付 3 件中、新しい 1 件" in out

        out = Tools().list_chat_files(
            limit="2", __user__={"id": "owner"}, __chat_id__=chat.id
        )
        assert "添付 3 件中、新しい 2 件" in out

        # 変換不能値は既定 20 へフォールバック(3 件なので全件出る)
        out = Tools().list_chat_files(
            limit="abc", __user__={"id": "owner"}, __chat_id__=chat.id
        )
        assert len(json.loads(out)) == 3
