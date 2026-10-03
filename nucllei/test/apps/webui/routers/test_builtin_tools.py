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
        # 戻りは素の list(直列化は本体が行う — #99)
        assert [c["title"] for c in out] == ["私のチャット"]

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
        assert [c["title"] for c in out] == ["旅行の計画"]
        # 一致箇所の抜粋 (発言者 + 前後の文脈) が付く — grep_doc と同じく何がヒットしたか見せる
        assert out[0]["matches"] == [{"role": "user", "excerpt": "京都に行きたい"}]

    def test_grep_history_excludes_the_current_chat(self):
        """検索を頼んだ発言 (「『京都』で検索して」) 自身がヒットしないよう、進行中の
        チャット (__chat_id__) は結果から外す。"""
        from nucllei.builtin_tools.history import Tools

        self._new_chat("owner", "旅行の計画", [("user", "京都に行きたい")])
        current = self._new_chat(
            "owner", "新しいチャット", [("user", "「京都」でチャット履歴を検索して")]
        )
        out = Tools().grep_history(
            "京都", __user__={"id": "owner"}, __chat_id__=current.id
        )
        assert [c["title"] for c in out] == ["旅行の計画"]
        # __chat_id__ 無し (ツール外からの呼び出し) では従来どおり両方
        out = Tools().grep_history("京都", __user__={"id": "owner"})
        assert len(out) == 2

    def test_grep_history_excerpts_are_bounded_and_fold_width_case(self):
        """抜粋は前後 60 文字で切り (… を付ける)、1 チャット最大 3 件。照合は NFKC + casefold
        (全角半角・大小文字を無視) で、長さが変わらないときは原文のまま見せる。"""
        from nucllei.builtin_tools.history import Tools

        long = "あ" * 100 + "Report" + "い" * 100
        self._new_chat(
            "owner",
            "長い",
            [("user", long), ("assistant", "report 1\nreport 2\nreport 3\nreport 4")],
        )
        out = Tools().grep_history("report", __user__={"id": "owner"})
        assert len(out) == 1 and len(out[0]["matches"]) == 3
        first = out[0]["matches"][0]
        assert first["role"] == "user" and first["excerpt"].startswith("…")
        assert "Report" in first["excerpt"] and first["excerpt"].endswith("…")
        assert len(first["excerpt"]) <= 60 * 2 + len("Report") + 2
        assert out[0]["matches"][1]["role"] == "assistant"
        assert "report 1 report 2" in out[0]["matches"][1]["excerpt"]

    def test_history_requires_user_context(self):
        import pytest

        from nucllei.builtin_tools.history import Tools

        with pytest.raises(ValueError):
            Tools().list_history(__user__=None)

    def test_result_carries_json_render_kind(self):
        """builtin tool の結果に render {kind:"json"} が付く(#99)。

        以前は自前で json.dumps した str を返していたため、backend は実型から
        ディスクリプタを付けられず、フロントは text 描画(ハイライト・ヘッダ・
        コピー無し)に落ちていた。cage の grep_doc(Code("json") 宣言あり)と
        見え方が食い違う原因だったので、実型を素直に返す形へ戻した。
        """
        import asyncio

        from nucllei.builtin_tools.history import Tools
        from nucllei.utils.middleware import execute_tool_calls

        self._new_chat("owner", "旅行の計画", [("user", "京都に行きたい")])

        tool = Tools()

        async def _call_list_history(**kwargs):
            return tool.list_history(__user__={"id": "owner"})

        tools = {
            "list_history": {
                "callable": _call_list_history,
                "spec": {"name": "list_history", "parameters": {"properties": {}}},
            }
        }
        call = {
            "id": "functions.list_history:0",
            "function": {"name": "list_history", "arguments": "{}"},
        }
        result = asyncio.run(execute_tool_calls([call], tools))[0]

        assert result["render"] == {"kind": "json"}
        # モデルに渡る文字列は非エスケープ・indent なし(#99)
        assert "\\u" not in result["content"]
        assert "\n" not in result["content"]
        assert json.loads(result["content"])[0]["title"] == "旅行の計画"

    def test_read_history_declares_markdown_render(self):
        """read_history は "# {title}\n\n{body}" のマークダウン成形を返すので、
        戻り型 Markdown を宣言して md としてレンダリングさせる(JSON ではない)。"""
        from nucllei.builtin_tools.history import Tools
        from nucllei.utils.tool_render import extract_render_from_function

        render = extract_render_from_function(Tools().read_history)
        assert render == {"result": {"kind": "markdown"}}

        # 一覧・検索は宣言なし = 実型(list)から backend が kind:json を付ける側
        assert extract_render_from_function(Tools().list_history) == {}
        assert extract_render_from_function(Tools().grep_history) == {}


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
        items = out["files"]
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
        assert [i["file_id"] for i in out["files"]] == ["f-mine"]
        assert "secret.xlsx" not in json.dumps(out, ensure_ascii=False)

    def test_deleted_own_file_is_marked(self):
        from nucllei.builtin_tools.chat_files import Tools

        self._new_file("owner", "f-del", "old.txt", deleted=True)
        chat = self._new_chat("owner", [[{"type": "file", "id": "f-del"}]])
        out = Tools().list_chat_files(__user__={"id": "owner"}, __chat_id__=chat.id)
        items = out["files"]
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
        assert [i["name"] for i in out["files"]] == ["Report.PDF"]

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
        # 切り詰めは散文ではなく total として構造に載る(#99)
        assert out["total"] == 3
        assert [i["file_id"] for i in out["files"]] == ["f-2"]

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
        assert out["total"] == 3
        assert [i["file_id"] for i in out["files"]] == ["f-1", "f-2"]

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
        assert [i["file_id"] for i in out["files"]] == ["f-meta"]

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
        assert [i["file_id"] for i in out["files"]] == ["f-top", "f-msg"]

    def test_purged_file_lists_without_name_and_grep_skips_it(self):
        """purge(#94)後は filename=None・meta が {size} に縮退する別形状。
        list は name:null + deleted:true で出し、grep は落ちずに非マッチ。"""
        from nucllei.builtin_tools.chat_files import Tools

        self._new_file("owner", "f-purged", "gone.txt", deleted=True)
        self.Files.purge_file_by_id("f-purged", "owner")
        chat = self._new_chat("owner", [[{"type": "file", "id": "f-purged"}]])

        out = Tools().list_chat_files(__user__={"id": "owner"}, __chat_id__=chat.id)
        items = out["files"]
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
        assert out["total"] == 3 and len(out["files"]) == 1

        out = Tools().list_chat_files(
            limit="2", __user__={"id": "owner"}, __chat_id__=chat.id
        )
        assert out["total"] == 3 and len(out["files"]) == 2

        # 変換不能値は既定 20 へフォールバック(3 件なので全件出る)
        out = Tools().list_chat_files(
            limit="abc", __user__={"id": "owner"}, __chat_id__=chat.id
        )
        assert len(out["files"]) == 3


class TestAttachmentText(AbstractPostgresTest):
    """builtin_attachment_text(#113)— file_id → 抽出テキストの読み口。

    owner-only fail-closed / data.content 優先 / text 系 raw フォールバック /
    offset 窓(尻尾が黙って落ちない)を固定する。
    """

    BASE_PATH = "/api/v1/tools"

    def setup_class(cls):
        super().setup_class()
        from nucllei.models.files import Files

        cls.Files = Files

    def _new_file(
        self,
        user_id,
        file_id,
        name,
        content=None,
        content_type="text/plain",
        path="",
    ):
        from nucllei.models.files import FileForm

        self.Files.insert_new_file(
            user_id,
            FileForm(
                id=file_id,
                filename=name,
                path=path,
                data={"content": content} if content is not None else {},
                meta={"name": name, "content_type": content_type, "size": 1},
            ),
        )

    def test_reads_extracted_content_with_offset_window(self):
        from nucllei.builtin_tools.attachment_text import Tools

        self._new_file(
            "owner",
            "doc-1",
            "doc.pdf",
            content="あ" * 10,
            content_type="application/pdf",
        )

        out = Tools().read_attachment("doc-1", max_chars=4, __user__={"id": "owner"})
        assert out["ok"] is True
        assert out["source"] == "extracted"
        assert out["text"] == "ああああ"
        assert out["total_chars"] == 10
        assert out["next_offset"] == 4

        # 続き → 最終窓では next_offset が消える(終端の合図)
        out = Tools().read_attachment(
            "doc-1", offset=8, max_chars=4, __user__={"id": "owner"}
        )
        assert out["text"] == "ああ"
        assert "next_offset" not in out

    def test_owner_check_is_fail_closed(self):
        from nucllei.builtin_tools.attachment_text import Tools

        self._new_file("owner", "doc-2", "doc.txt", content="secret")
        # 他人は拒否
        denied = Tools().read_attachment("doc-2", __user__={"id": "intruder"})
        assert denied["ok"] is False and "アクセス権" in denied["error"]
        # 「レコードが無い」と「所有者でない」は同じ文(存在の有無を漏らさない)。
        # 文面は全 tool 共通の形 + file_id が受ける形の固定文
        from nucllei.utils.image_refs import FILE_ID_ARG_FORM

        missing = Tools().read_attachment("no-such-id", __user__={"id": "intruder"})
        for out, fid in ((denied, "doc-2"), (missing, "no-such-id")):
            assert out == {
                "ok": False,
                "error": f"file_id {fid} のレコードが無い、またはアクセス権が無い。"
                f"{FILE_ID_ARG_FORM}",
            }
        # owner が falsy な行も拒否(fail-closed — files.user_id は nullable カラム)
        self._new_file("owner", "doc-orphan", "orphan.txt", content="x")
        from nucllei.internal.db import get_db
        from nucllei.models.files import File

        with get_db() as db:
            db.query(File).filter_by(id="doc-orphan").update({"user_id": None})
            db.commit()
        denied = Tools().read_attachment("doc-orphan", __user__={"id": "owner"})
        assert denied["ok"] is False
        # ユーザー文脈なしも拒否
        denied = Tools().read_attachment("doc-2", __user__=None)
        assert denied["ok"] is False

    def test_raw_text_fallback_and_binary_refusal(self, tmp_path):
        from nucllei.builtin_tools.attachment_text import Tools

        # 抽出なし + text 系 content_type → Storage raw を decode
        raw = tmp_path / "note.txt"
        raw.write_text("生テキスト", encoding="utf-8")
        self._new_file("owner", "raw-1", "note.txt", path=str(raw))
        out = Tools().read_attachment("raw-1", __user__={"id": "owner"})
        assert out["ok"] is True and out["source"] == "raw"
        assert out["text"] == "生テキスト"

        # 抽出なし + binary → 読まず、「抽出テキストが無い」事実だけを返す。種類を
        # 決めつけた誘導(feed_image)はしない — 音声でも同じ文
        outs = {}
        for fid, name, content_type in (
            ("img-1", "scan.png", "image/png"),
            ("aud-1", "rec.webm", "audio/webm"),
        ):
            self._new_file("owner", fid, name, content_type=content_type)
            out = Tools().read_attachment(fid, __user__={"id": "owner"})
            assert out["ok"] is False and "抽出テキストが無い" in out["error"]
            assert "feed_image" not in out["error"]
            outs[fid] = out["error"].replace(fid, "<id>").replace(content_type, "<ct>")
        assert len(set(outs.values())) == 1

    def test_sync_registers_builtin_row(self):
        from nucllei.builtin_tools import sync_builtin_tools
        from nucllei.models.tools import Tools as ToolsTable

        sync_builtin_tools()
        row = ToolsTable.get_tool_by_id("builtin_attachment_text")
        assert row is not None
        assert [s["name"] for s in row.specs] == ["read_attachment"]
