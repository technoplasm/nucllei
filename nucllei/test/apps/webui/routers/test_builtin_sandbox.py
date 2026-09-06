"""builtin_sandbox(配備層 llens のカスタム tool から移設)の回帰。

固定するのは 2 点:

1. **env ゲート** — 他の builtin と違い外部サービスが要るので、向き先 env
   (SANDBOX_URL)が設定された配備でだけ登録される。未設定なら BUILTIN_TOOLS に
   載らず、既存の prune(`builtin_` prefix かつ未定義の行を削除)が DB 行も片付ける。
2. **HTTP は全部モック** — 実 sandbox コンテナに依存せず CI で回る。障害時に例外を
   漏らさず "ERROR: ..." 文字列で返る(builtin-tools メモの T11)ことも含む。

末尾の `send_to_workspace(url=)` 節は、移設元 prism-hu/llens にあった
`nucllei/tests/test_send_to_workspace_url.py` を引き取ったもの(SSRF ガードの回帰)。
"""

import asyncio
import base64
import contextlib
import importlib
import os
import types

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user


def _fake_request():
    return types.SimpleNamespace(
        app=types.SimpleNamespace(state=types.SimpleNamespace(TOOLS={}))
    )


@contextlib.contextmanager
def sandbox_env(url, token="test-token"):
    """SANDBOX_URL / SANDBOX_TOKEN を差し替えて builtin_tools を読み直す(None で未設定)。

    ゲートは import 時に決まる(Valves の default に env を焼くため — default_factory
    だと管理画面の spec から default が落ちる)。テストからは module reload で
    やり直す。reload は module オブジェクトを作り直さないので、他モジュールが
    掴んだ `is_builtin_tool_id` 等の関数も新しい BUILTIN_TOOLS を見る。

    **登録ゲートは `SANDBOX_URL` ただ一つ** — token (#132) は opt-in なので条件に
    入らない (2026-09-05)。既定で token を入れてあるのは「認証ありの配備」を既定の
    前提にしておくためで、token だけを動かすテストは明示的に渡す。
    """
    import nucllei.builtin_tools as pkg
    from nucllei.builtin_tools import sandbox as mod

    olds = {k: os.environ.get(k) for k in ("SANDBOX_URL", "SANDBOX_TOKEN")}

    def _apply(values):
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        importlib.reload(mod)
        importlib.reload(pkg)

    _apply({"SANDBOX_URL": url, "SANDBOX_TOKEN": token})
    try:
        yield pkg
    finally:
        _apply(olds)


def _sandbox(responses=None, error=None):
    """sandbox HTTP をモックした Tools。_post の呼び出しを calls に記録する。

    実コンテナに依存させないため差し替えるのは _post 1 点だけ — 認可・引数検証・
    整形はすべて本物のコードを通す。
    """
    from nucllei.builtin_tools.sandbox import Tools

    tool = Tools()
    tool.calls = []

    async def _post(path, payload, timeout_s):
        tool.calls.append((path, payload, timeout_s))
        if error is not None:
            raise error
        return (responses or {})[path]

    tool._post = _post
    return tool


class TestSandboxBuiltinRegistration(AbstractPostgresTest):
    """env ゲートと Tools テーブルへの反映。"""

    BASE_PATH = "/api/v1/tools"

    def setup_class(cls):
        super().setup_class()
        from nucllei.models.tools import Tools

        cls.Tools = Tools

    def test_not_registered_without_env(self):
        with sandbox_env(None) as pkg:
            assert "builtin_sandbox" not in pkg.BUILTIN_TOOLS
            assert pkg.is_builtin_tool_id("builtin_sandbox") is False
            pkg.sync_builtin_tools()
            assert self.Tools.get_tool_by_id("builtin_sandbox") is None
            # 他の builtin は env に関係なく居る(ゲートは sandbox だけ)
            assert self.Tools.get_tool_by_id("builtin_history") is not None

    def test_registered_with_env_and_pruned_when_removed(self):
        with sandbox_env("http://sandbox.test:8000") as pkg:
            assert "builtin_sandbox" in pkg.BUILTIN_TOOLS
            pkg.sync_builtin_tools()
            row = self.Tools.get_tool_by_id("builtin_sandbox")
            assert row is not None
            assert row.meta.builtin is True
            assert row.content == ""
            assert {s["name"] for s in row.specs} == {
                "run_python",
                "run_python_once",
                "run_shell",
                "send_to_workspace",
                "write_file",
                "list_workspace",
                "fetch_from_workspace",
            }

        # env を外すと既存の prune 経路(prefix ありで BUILTIN_TOOLS 未定義)が
        # そのまま行を消す。access_control 設定が失われるのは承知の上の帰結。
        with sandbox_env(None) as pkg:
            pkg.sync_builtin_tools()
            assert self.Tools.get_tool_by_id("builtin_sandbox") is None

    def test_registered_without_token(self):
        """#132 (2026-09-05 改訂): トークン無しでも URL があれば生やす。

        sandbox 側の認証は opt-in で、トークン未設定の sandbox は認証なしで応じる
        — 「一覧に見えているのに呼ぶと必ず 401」にはならないので、ゲートに入れない。
        設定漏れに気付けるよう sync 時に warning だけ出す。
        """
        with sandbox_env("http://sandbox.test:8000", token=None) as pkg:
            assert "builtin_sandbox" in pkg.BUILTIN_TOOLS
            assert pkg._SANDBOX_TOKEN_MISSING is True  # sync 時に warning を出す印
            pkg.sync_builtin_tools()
            assert self.Tools.get_tool_by_id("builtin_sandbox") is not None

        # URL が無いのは「sandbox の居ない配備」— 非登録。トークンの有無に依らず
        # 設定漏れの警告も出さない
        with sandbox_env(None, token="test-token") as pkg:
            assert "builtin_sandbox" not in pkg.BUILTIN_TOOLS
            assert pkg._SANDBOX_TOKEN_MISSING is False
            pkg.sync_builtin_tools()
            assert self.Tools.get_tool_by_id("builtin_sandbox") is None

    def test_reserved_prefix_blocks_user_created_id(self):
        """env 未設定で未登録でも、`builtin_` prefix の予約はユーザー作成を拒む。"""
        with sandbox_env(None):
            with mock_webui_user(id="admin1", role="admin"):
                res = self.fast_api_client.post(
                    self.create_url("/create"),
                    json={
                        "id": "builtin_sandbox",
                        "name": "偽 sandbox",
                        "description": "d",
                        "content": "class Tools:\n    def run_python(self):\n        return 'x'",
                    },
                )
            assert res.status_code == 400

    def test_function_names_do_not_collide_with_other_builtins(self):
        """同名関数は先勝ち discard(#18-3 の教訓)— builtin 同士でぶつけない。"""
        with sandbox_env("http://sandbox.test:8000") as pkg:
            seen = {}
            from nucllei.utils.tools import get_tool_specs

            for tool_id, entry in pkg.BUILTIN_TOOLS.items():
                for spec in get_tool_specs(entry["module"].Tools()):
                    assert (
                        spec["name"] not in seen
                    ), f"{spec['name']} が {seen.get(spec['name'])} と {tool_id} で衝突"
                    seen[spec["name"]] = tool_id

    def test_valves_are_editable_from_the_tools_page(self):
        """Valves は builtin でも一覧 UI から編集できる(#16 / 70743aa68)。

        一覧の has_valves は router がモジュールキャッシュ経由の hasattr で埋め、
        builtin は load_builtin_tool へ振り向く — spec / update とも同じ経路。
        """
        with sandbox_env("http://sandbox.test:8000") as pkg:
            pkg.sync_builtin_tools()

            with mock_webui_user(id="admin1", role="admin"):
                listed = self.fast_api_client.get(self.create_url("/list"))
                assert listed.status_code == 200
                row = next(t for t in listed.json() if t["id"] == "builtin_sandbox")
                assert row["has_valves"] is True

                spec = self.fast_api_client.get(
                    self.create_url("/id/builtin_sandbox/valves/spec")
                )
                assert spec.status_code == 200
                props = spec.json()["properties"]
                # 向き先は env 専用 — valve として露出しない (2026-09-05)
                assert "sandbox_url" not in props

                upd = self.fast_api_client.post(
                    self.create_url("/id/builtin_sandbox/valves/update"),
                    json={"default_timeout_s": 30},
                )
                assert upd.status_code == 200
                assert upd.json()["default_timeout_s"] == 30

            # 保存した valves は get_tools のロード経路で実インスタンスへ適用される
            from nucllei.utils.tools import get_tools

            req = _fake_request()
            loaded = get_tools(
                req,
                ["builtin_sandbox"],
                types.SimpleNamespace(id="a1", role="admin"),
                {"__user__": {"id": "a1"}},
            )
            assert "run_python" in loaded
            module = req.app.state.TOOLS["builtin_sandbox"]
            assert module.valves.default_timeout_s == 30

    def test_destination_is_env_only_and_unreachable_from_db(self):
        """向き先は env `SANDBOX_URL` のみ。DB からは変えられない (2026-09-05)。

        `sandbox_url` valve を廃止した理由の回帰。valve だった頃は、admin 権限を
        取った者が宛先を外部に向けて env の `SANDBOX_TOKEN` を
        `Authorization: Bearer` ごと回収できた。ここで固定するのは 3 点:

        1. `valves/update` に `sandbox_url` を送っても保存されない (未知キーは無視)
        2. **旧配備の DB に `sandbox_url` 行が残っていても** ValidationError にならず
           ロードできる (pydantic v2 の既定 extra='ignore'。旧データ互換コードは
           書かない方針なので「落ちないこと」だけを担保する)
        3. `_post` の宛先は env の値のまま
        """
        with sandbox_env("http://sandbox.test:8000") as pkg:
            pkg.sync_builtin_tools()

            with mock_webui_user(id="admin1", role="admin"):
                upd = self.fast_api_client.post(
                    self.create_url("/id/builtin_sandbox/valves/update"),
                    json={"sandbox_url": "http://evil.test:9000"},
                )
                assert upd.status_code == 200
                assert "sandbox_url" not in upd.json()

            # 旧配備の残骸を直接 DB へ書き戻す (router を通さない = 移行前の状態)
            from nucllei.models.tools import Tools as ToolsTable

            ToolsTable.update_tool_valves_by_id(
                "builtin_sandbox",
                {"sandbox_url": "http://evil.test:9000", "default_timeout_s": 45},
            )

            from nucllei.utils.tools import get_tools

            req = _fake_request()
            loaded = get_tools(
                req,
                ["builtin_sandbox"],
                types.SimpleNamespace(id="a1", role="admin"),
                {"__user__": {"id": "a1"}},
            )
            assert "run_python" in loaded  # ValidationError で落ちない
            module = req.app.state.TOOLS["builtin_sandbox"]
            assert not hasattr(module.valves, "sandbox_url")
            assert module.valves.default_timeout_s == 45

            # 実際の宛先は env の値 (module は load_builtin_tool が返す Tools インスタンス)
            seen = {}

            class FakeResp:
                status = 200

                async def json(self):
                    return {"entries": []}

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *a):
                    return False

            class FakeSession:
                def __init__(self, timeout=None, headers=None):
                    pass

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *a):
                    return False

                def post(self, url, json=None):
                    seen["url"] = url
                    return FakeResp()

            import nucllei.builtin_tools.sandbox as mod

            real = mod.aiohttp
            mod.aiohttp = types.SimpleNamespace(
                ClientSession=FakeSession, ClientTimeout=lambda total: total
            )
            try:
                asyncio.run(module._post("/files/list", {"user_id": "u1"}, 30))
            finally:
                mod.aiohttp = real
            assert seen["url"] == "http://sandbox.test:8000/files/list"

    def test_code_arg_render_is_declared(self):
        """run_python の code 引数は Code("python") 宣言を持つ(#20 型駆動レンダリング)。"""
        with sandbox_env("http://sandbox.test:8000") as pkg:
            from nucllei.utils.tool_render import extract_render_from_function

            tool = pkg.BUILTIN_TOOLS["builtin_sandbox"]["module"].Tools()
            assert extract_render_from_function(tool.run_python) == {
                "args": {"code": {"kind": "code", "language": "python"}}
            }
            assert extract_render_from_function(tool.run_shell) == {
                "args": {"command": {"kind": "code", "language": "bash"}}
            }


class TestSandboxToolBehaviour(AbstractPostgresTest):
    """tool 本体の挙動。sandbox HTTP は全部モック(実コンテナに依存しない)。"""

    BASE_PATH = "/api/v1/tools"

    def setup_class(cls):
        super().setup_class()
        from nucllei.models.files import Files

        cls.Files = Files

    def test_run_python_posts_exec_cell_with_user_and_chat(self):
        tool = _sandbox(
            {
                "/exec_cell": {
                    "stdout": "42\n",
                    "error": None,
                    "timed_out": False,
                    "duration_s": 0.1,
                    "exports": [],
                }
            }
        )
        out = asyncio.run(
            tool.run_python(
                "print(42)",
                __user__={"id": "u1"},
                __chat_id__="c1",
                __message_id__="m1",
            )
        )
        path, payload, _ = tool.calls[0]
        assert path == "/exec_cell"
        assert payload["user_id"] == "u1" and payload["chat_id"] == "c1"
        assert payload["timeout_s"] == 120  # valves の既定
        assert "42" in out

    def test_run_python_without_chat_id_points_at_run_python_once(self):
        tool = _sandbox({})
        out = asyncio.run(tool.run_python("x=1", __user__={"id": "u1"}))
        assert out.startswith("ERROR:")
        assert "run_python_once" in out
        assert tool.calls == []  # HTTP を打たずに手前で止まる

    def test_timeout_result_says_state_is_kept(self):
        tool = _sandbox(
            {
                "/exec_cell": {
                    "stdout": "",
                    "error": None,
                    "timed_out": True,
                    "duration_s": 120,
                    "exports": [],
                }
            }
        )
        out = asyncio.run(
            tool.run_python("while True: pass", __user__={"id": "u1"}, __chat_id__="c1")
        )
        assert "タイムアウト" in out and "変数・import は保持" in out

    def test_run_shell_and_run_python_once_use_exec(self):
        res = {
            "exit_code": 0,
            "duration_s": 0.2,
            "stdout": "hello\n",
            "stderr": "",
            "exports": [],
        }
        tool = _sandbox({"/exec": res})
        out = asyncio.run(tool.run_shell("echo hello", __user__={"id": "u1"}))
        assert tool.calls[0][0] == "/exec"
        assert tool.calls[0][1]["lang"] == "shell"
        assert "exit_code=0" in out and "hello" in out

        tool = _sandbox({"/exec": res})
        asyncio.run(tool.run_python_once("print('hello')", __user__={"id": "u1"}))
        assert tool.calls[0][1]["lang"] == "python"

    def test_missing_user_context_is_refused_everywhere(self):
        tool = _sandbox({})
        for coro in (
            tool.run_python("x", __chat_id__="c1"),
            tool.run_python_once("x"),
            tool.run_shell("x"),
            tool.write_file("a.txt", "x"),
            tool.list_workspace(),
            tool.fetch_from_workspace("a.txt"),
            tool.send_to_workspace(),
        ):
            assert asyncio.run(coro).startswith("ERROR:")
        assert tool.calls == []

    def test_post_sends_bearer_token(self):
        """#132: トークンがあれば Authorization: Bearer が付く(唯一の HTTP 出口 = _post)。

        他のテストは _post ごと差し替えるので、ここだけ本物の _post を通し
        aiohttp を偽物にしてヘッダを見る。トークン未設定ならヘッダ自体が無いことも
        併せて固定する (opt-in、2026-09-05)。
        """
        with sandbox_env("http://sandbox.test:8000", token="tok-abc"):
            from nucllei.builtin_tools.sandbox import Tools

            seen = {}

            class FakeResp:
                status = 200

                async def json(self):
                    return {"entries": []}

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *a):
                    return False

            class FakeSession:
                def __init__(self, timeout=None, headers=None):
                    seen["headers"] = dict(headers or {})

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *a):
                    return False

                def post(self, url, json=None):
                    seen["url"] = url
                    return FakeResp()

            tool = Tools()
            fake_aiohttp = types.SimpleNamespace(
                ClientSession=FakeSession, ClientTimeout=lambda total: total
            )
            import nucllei.builtin_tools.sandbox as mod

            real = mod.aiohttp
            mod.aiohttp = fake_aiohttp
            try:
                asyncio.run(tool._post("/files/list", {"user_id": "u1"}, 30))
            finally:
                mod.aiohttp = real

            assert seen["headers"] == {"Authorization": "Bearer tok-abc"}
            assert seen["url"] == "http://sandbox.test:8000/files/list"

        # トークン未設定なら header 自体を付けない (空の `Bearer ` を送らない)
        with sandbox_env("http://sandbox.test:8000", token=None):
            import nucllei.builtin_tools.sandbox as mod

            tool = mod.Tools()
            real = mod.aiohttp
            mod.aiohttp = fake_aiohttp
            try:
                asyncio.run(tool._post("/files/list", {"user_id": "u1"}, 30))
            finally:
                mod.aiohttp = real

            assert seen["headers"] == {}

    def test_401_error_names_the_token_mismatch(self):
        """401 は原因(両側の SANDBOX_TOKEN 不一致)を言う。トークン値は出さない。"""
        tool = _sandbox(
            error=RuntimeError(
                "sandbox HTTP 401: 認証に失敗しました。nucllei と sandbox の "
                "SANDBOX_TOKEN が一致していません "
                "(片側だけ未設定の場合も含む。配備の env を確認)"
            )
        )
        out = asyncio.run(tool.list_workspace(__user__={"id": "u1"}))
        assert out.startswith("ERROR:") and "SANDBOX_TOKEN" in out

    def test_http_failure_is_graceful(self):
        """T11: 接続不能・非 200 とも例外を漏らさず "ERROR: ..." で返す。"""
        tool = _sandbox(error=RuntimeError("sandbox HTTP 502: bad gateway"))
        calls = [
            tool.run_python("x", __user__={"id": "u1"}, __chat_id__="c1"),
            tool.run_python_once("x", __user__={"id": "u1"}),
            tool.run_shell("x", __user__={"id": "u1"}),
            tool.write_file("a.txt", "x", __user__={"id": "u1"}),
            tool.list_workspace(__user__={"id": "u1"}),
            tool.fetch_from_workspace("a.txt", __user__={"id": "u1"}),
        ]
        for coro in calls:
            out = asyncio.run(coro)
            assert out.startswith("ERROR:") and "502" in out

    def test_exports_are_registered_attached_and_displayed(self):
        """exports/ の差分 → Files 登録 + chat:message:files emit + 画像は display。"""
        from nucllei.utils import tool_output
        from nucllei.utils.tool_output import ToolOutputSink

        png = base64.b64encode(b"\x89PNG fake").decode()
        tool = _sandbox(
            {
                "/exec": {
                    "exit_code": 0,
                    "duration_s": 0.1,
                    "stdout": "",
                    "stderr": "",
                    "exports": [
                        {"name": "plot.png", "size": 9, "b64": png},
                        # 転送上限超過は登録せず報告のみ
                        {"name": "huge.bin", "size": 10**9, "truncated": True},
                    ],
                }
            }
        )

        emitted = []

        async def emitter(event):
            emitted.append(event)

        sink = ToolOutputSink()
        token = tool_output.current_sink.set(sink)
        try:
            out = asyncio.run(
                tool.run_python_once(
                    "…",
                    __user__={"id": "u1"},
                    __event_emitter__=emitter,
                    __chat_id__="c1",
                    __message_id__="m1",
                )
            )
        finally:
            tool_output.current_sink.reset(token)

        assert "plot.png" in out and "file_id=" in out
        assert "転送上限" in out and "huge.bin" in out

        assert emitted[0]["type"] == "chat:message:files"
        files = emitted[0]["data"]["files"]
        assert [f["name"] for f in files] == ["plot.png"]
        assert self.Files.get_file_by_id(files[0]["id"]) is not None

        # 画像は人間の目(display)へ、上限超過の警告も同じチャネルで出す
        shown = [d["content"] for d in sink.displays()]
        assert any(c.startswith("![plot.png](/api/v1/files/") for c in shown)
        assert any("転送上限" in c for c in shown)

    def test_send_to_workspace_is_owner_only(self, tmp_path):
        from nucllei.models.files import FileForm

        src = tmp_path / "mine.txt"
        src.write_text("中身", encoding="utf-8")
        for uid, fid, name in (
            ("owner", "f-mine", "mine.txt"),
            ("other", "f-yours", "yours.txt"),
        ):
            self.Files.insert_new_file(
                uid,
                FileForm(
                    id=fid,
                    filename=name,
                    path=str(src),
                    data={},
                    meta={"name": name, "content_type": "text/plain", "size": 6},
                ),
            )

        tool = _sandbox({"/files/write": {"ok": True}})
        out = asyncio.run(
            tool.send_to_workspace(file_id="f-mine", __user__={"id": "owner"})
        )
        assert "/workspace/imports/mine.txt" in out
        assert tool.calls[0][1]["path"] == "imports/mine.txt"

        tool = _sandbox({"/files/write": {"ok": True}})
        out = asyncio.run(
            tool.send_to_workspace(file_id="f-yours", __user__={"id": "owner"})
        )
        assert "アクセス権が無い" in out
        assert tool.calls == []

    def test_write_file_guards_path_and_size(self):
        tool = _sandbox({"/files/write": {"ok": True}})
        assert asyncio.run(
            tool.write_file("../escape", "x", __user__={"id": "u1"})
        ).startswith("ERROR:")
        assert asyncio.run(tool.write_file("", "x", __user__={"id": "u1"})).startswith(
            "ERROR:"
        )

        from nucllei.builtin_tools import sandbox as mod

        big = "a" * (mod.WRITE_FILE_LIMIT + 1)
        assert asyncio.run(
            tool.write_file("big.txt", big, __user__={"id": "u1"})
        ).startswith("ERROR:")
        assert tool.calls == []

        out = asyncio.run(
            tool.write_file("exports/r.html", "<p>", __user__={"id": "u1"})
        )
        assert "書き込みました" in out
        # exports/ 直書きは自動添付されない旨をモデルに伝える
        assert "fetch_from_workspace" in out

    def test_list_workspace_formats_entries(self):
        tool = _sandbox({"/files/list": {"entries": [{"path": "a.csv", "size": 2048}]}})
        out = asyncio.run(tool.list_workspace(__user__={"id": "u1"}))
        assert "- a.csv (2.0KB)" in out

        tool = _sandbox({"/files/list": {"entries": []}})
        assert "空です" in asyncio.run(
            tool.list_workspace("sub", __user__={"id": "u1"})
        )


# =============================================================================
# send_to_workspace(url=) の契約(SSRF ガードの回帰)
#
# 移設元 prism-hu/llens の `nucllei/tests/test_send_to_workspace_url.py` を
# 引き取ったもの(tool 本体と一緒に来るべきテスト)。あちらは配備層 tool として
# nucllei の venv の外でも単体実行できるよう pydantic/aiohttp の stub 差し込み +
# ファイルパス直読み + 自前ランナーという作りだったが、builtin になった今その
# 前提は消えたので、普通に import する pytest の流儀へ書き直してある。
#
# DB を触らないので AbstractPostgresTest には載せない(url 経路は Files を見ない)。
# ネットワークは _http_get / _post の差し替えで切り離す — 宛先ガードの
# getaddrinfo は literal IP を使うので実解決は起きない。
# =============================================================================

# _http_get が上限超過(_TooLarge)を投げる、と応答表で示すための番兵
_TOO_LARGE = object()


class _FakeRequest:
    def __init__(self, headers):
        self.headers = headers


class _UrlEnv:
    """1 テスト分の道具立て: tool + 偽 _http_get / _post + 記録。"""

    def __init__(self, responses=None):
        from nucllei.builtin_tools.sandbox import Tools, _TooLarge

        self.tools = Tools()
        self.gets: list[tuple] = []  # (target, 送信 headers)
        self.writes: list[dict] = []
        self._responses = list(responses or [])

        async def fake_http_get(target, max_bytes, headers=None):
            self.gets.append((target, headers or {}))
            status, resp_headers, data = self._responses.pop(0)
            if data is _TOO_LARGE:
                raise _TooLarge()
            return status, resp_headers, data

        async def fake_post(path, payload, timeout_s):
            self.writes.append({"path": path, "payload": payload})
            return {}

        self.tools._http_get = fake_http_get
        self.tools._post = fake_post

    def run(self, __request__=None, **kwargs):
        return asyncio.run(
            self.tools.send_to_workspace(
                __user__={"id": "u1"}, __request__=__request__, **kwargs
            )
        )


def test_url_exclusive_with_file_id_and_filename():
    env = _UrlEnv()
    for extra in ({"file_id": "some-uuid"}, {"filename": "x"}):
        result = env.run(url="http://10.0.0.5/a.pdf", **extra)
        assert result.startswith("ERROR") and "同時指定" in result, extra


@contextlib.contextmanager
def _patched(mapping: dict, key, value):
    mapping[key] = value
    try:
        yield
    finally:
        mapping.pop(key, None)


def test_url_rejects_non_url():
    env = _UrlEnv()
    result = env.run(url="ext/cage/x.png")
    assert result.startswith("ERROR") and env.gets == []


# --- "/" 始まり = self fetch --------------------------------------------------


def test_self_fetch_forwards_auth_headers():
    env = _UrlEnv(responses=[(200, {}, b"%PDF-fake")])
    result = env.run(
        url="https://llens.test/ext/cage/static/kansen_manual/docs/10-14.pdf",
        __request__=_FakeRequest({"cookie": "token=abc", "authorization": "Bearer x"}),
    )
    assert env.gets == [
        (
            "http://127.0.0.1:8080/ext/cage/static/kansen_manual/docs/10-14.pdf",
            {"cookie": "token=abc", "authorization": "Bearer x"},
        )
    ]
    assert env.writes[0]["payload"]["path"] == "imports/10-14.pdf"
    assert base64.b64decode(env.writes[0]["payload"]["b64"]) == b"%PDF-fake"
    assert not result.startswith("ERROR")


def test_self_fetch_files_url_uses_content_disposition_name():
    env = _UrlEnv(
        responses=[
            (200, {"content-disposition": 'attachment; filename="report.xlsx"'}, b"x"),
        ]
    )
    result = env.run(
        url="/api/v1/files/1111-2222/content",
        __request__=_FakeRequest({"cookie": "token=abc"}),
    )
    assert env.gets[0][0] == "http://127.0.0.1:8080/api/v1/files/1111-2222/content"
    assert env.writes[0]["payload"]["path"] == "imports/report.xlsx"
    assert not result.startswith("ERROR")


def test_self_fetch_absolute_ext_url_any_host():
    # path が /ext/{name}/... の完全 URL は host を問わず自分自身への self fetch
    # (認証ヘッダ付き)。与えられた host には接続しない(#139 の名前空間規則)
    env = _UrlEnv(responses=[(200, {}, b"%PDF-fake")])
    result = env.run(
        url="https://elsewhere.example/ext/cage/static/x.pdf?rev=2",
        __request__=_FakeRequest({"cookie": "token=abc"}),
    )
    assert env.gets == [
        (
            "http://127.0.0.1:8080/ext/cage/static/x.pdf?rev=2",
            {"cookie": "token=abc"},
        )
    ]
    assert env.writes[0]["payload"]["path"] == "imports/x.pdf"
    assert not result.startswith("ERROR")


def test_self_fetch_without_request_sends_no_headers():
    env = _UrlEnv(responses=[(200, {}, b"x")])
    env.run(url="https://llens.test/ext/cage/a.png")
    assert env.gets == [("http://127.0.0.1:8080/ext/cage/a.png", {})]


def test_self_fetch_auth_failure_surfaces_status():
    env = _UrlEnv(responses=[(401, {}, b"")])
    result = env.run(url="https://llens.test/ext/cage/a.png")
    assert result.startswith("ERROR") and "401" in result
    assert env.writes == []


def test_relative_ext_path_rejected():
    # 名前空間は image_refs と同じ: "/" 始まりは sandbox の fs、/ext/ は URL(絶対で渡す)
    env = _UrlEnv(responses=[(200, {}, b"x")])
    result = env.run(url="/ext/cage/a.png")
    assert result.startswith("ERROR") and "絶対 URL" in result
    assert env.gets == [] and env.writes == []


def test_sandbox_paths_need_no_import():
    env = _UrlEnv(responses=[(200, {}, b"x")])
    for u in ("/workspace/x", "/mnt/cage/x.pdf"):
        result = env.run(url=u)
        assert result.startswith("ERROR") and "取り込み不要" in result, u
    assert env.gets == [] and env.writes == []


def test_other_relative_paths_rejected():
    env = _UrlEnv(responses=[(200, {}, b"x")])
    result = env.run(url="/etc/passwd")
    assert result.startswith("ERROR") and env.gets == []


def test_public_base_url_goes_through_self_ext():
    # EXT_PUBLIC_BASES 配下の原本 URL は原本 host へ直接行かず /ext/{name}/{rel} の self fetch
    from nucllei.env import EXT_PUBLIC_BASES

    env = _UrlEnv(responses=[(200, {}, b"%PDF-fake")])
    with _patched(EXT_PUBLIC_BASES, "his", "http://his-info.test/docs"):
        result = env.run(
            url="http://his-info.test/docs/a/b.pdf?rev=2#page=3",
            __request__=_FakeRequest({"cookie": "token=abc"}),
        )
    assert env.gets == [
        ("http://127.0.0.1:8080/ext/his/a/b.pdf?rev=2", {"cookie": "token=abc"})
    ]
    assert not result.startswith("ERROR")


def test_malformed_absolute_url_is_error():
    env = _UrlEnv(responses=[(200, {}, b"x")])
    result = env.run(url="http://[::1/ext/cage/a.png")
    assert result.startswith("ERROR") and env.gets == []


def test_self_fetch_does_not_follow_redirects():
    env = _UrlEnv(responses=[(302, {"location": "http://10.0.0.5/x"}, b"")])
    result = env.run(
        url="https://llens.test/ext/cage/a.png",
        __request__=_FakeRequest({"cookie": "token=abc"}),
    )
    assert result.startswith("ERROR") and "redirect" in result
    assert len(env.gets) == 1  # 追っていない


# --- 完全 URL = LAN fetch -----------------------------------------------------


def test_lan_blocked_destinations():
    for bad in (
        "http://127.0.0.1:8000/files/write",
        "http://172.17.0.5/metrics",
        "http://169.254.1.1/x",
        # ガード貫通の回帰 (レビュー指摘): IPv4-mapped IPv6 と unspecified は
        # いずれも自ホストに落ちる
        "http://[::ffff:127.0.0.1]:8000/files/write",
        "http://0.0.0.0:8080/x",
        "http://[::]:8080/x",
    ):
        env = _UrlEnv()
        result = env.run(url=bad)
        assert result.startswith("ERROR") and "拒否レンジ" in result, bad
        assert env.gets == [], bad


def test_lan_rejects_non_http_scheme():
    env = _UrlEnv()
    result = env.run(url="ftp://10.0.0.5/a.pdf")
    assert result.startswith("ERROR") and "http" in result


def test_lan_accepts_uppercase_scheme():
    env = _UrlEnv(responses=[(200, {}, b"x")])
    result = env.run(url="HTTP://10.0.0.5/a.pdf")
    assert not result.startswith("ERROR") and len(env.gets) == 1


def test_lan_ok_writes_with_basename_and_no_auth_headers():
    env = _UrlEnv(responses=[(200, {}, b"data")])
    result = env.run(
        url="http://10.0.0.5/docs/manual.pdf",
        __request__=_FakeRequest({"cookie": "token=abc"}),
    )
    # 認証ヘッダは外へ持ち出さない
    assert env.gets == [("http://10.0.0.5/docs/manual.pdf", {})]
    assert env.writes[0]["payload"]["path"] == "imports/manual.pdf"
    assert not result.startswith("ERROR")


def test_lan_name_from_content_disposition():
    env = _UrlEnv(
        responses=[
            (200, {"content-disposition": 'attachment; filename="report.xlsx"'}, b"x"),
        ]
    )
    env.run(url="http://10.0.0.5/download?id=3")
    assert env.writes[0]["payload"]["path"] == "imports/report.xlsx"


def test_lan_name_fallback_when_no_basename():
    env = _UrlEnv(responses=[(200, {}, b"x")])
    env.run(url="http://10.0.0.5/")
    assert env.writes[0]["payload"]["path"] == "imports/download"


def test_lan_follows_redirect_with_recheck():
    env = _UrlEnv(
        responses=[
            (302, {"location": "http://10.0.0.6/real.pdf"}, b""),
            (200, {}, b"pdf"),
        ]
    )
    result = env.run(url="http://10.0.0.5/alias")
    assert [g[0] for g in env.gets] == [
        "http://10.0.0.5/alias",
        "http://10.0.0.6/real.pdf",
    ]
    assert env.writes[0]["payload"]["path"] == "imports/real.pdf"
    assert not result.startswith("ERROR")


def test_lan_redirect_to_blocked_is_rejected():
    env = _UrlEnv(responses=[(302, {"location": "http://127.0.0.1:8000/x"}, b"")])
    result = env.run(url="http://10.0.0.5/alias")
    assert result.startswith("ERROR") and "拒否レンジ" in result
    assert env.writes == []


def test_lan_too_many_redirects():
    env = _UrlEnv(responses=[(302, {"location": "http://10.0.0.5/loop"}, b"")] * 4)
    result = env.run(url="http://10.0.0.5/loop")
    assert result.startswith("ERROR") and "redirect" in result


def test_lan_size_cap():
    env = _UrlEnv(responses=[(200, {}, _TOO_LARGE)])
    result = env.run(url="http://10.0.0.5/big.iso")
    assert result.startswith("ERROR") and "大きすぎる" in result


def test_lan_error_status():
    env = _UrlEnv(responses=[(404, {}, b"")])
    result = env.run(url="http://10.0.0.5/missing.pdf")
    assert result.startswith("ERROR") and "404" in result
