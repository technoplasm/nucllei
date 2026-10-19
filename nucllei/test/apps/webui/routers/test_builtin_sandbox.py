"""builtin_sandbox(配備層 llens のカスタム tool から移設)の回帰。

固定するのは 2 点:

1. **env ゲート** — 他の builtin と違い外部サービスが要るので、向き先 env
   (SANDBOX_URL)が設定された配備でだけ登録される。未設定なら BUILTIN_TOOLS に
   載らず、既存の prune(`builtin_` prefix かつ未定義の行を削除)が DB 行も片付ける。
2. **HTTP は全部モック** — 実 sandbox コンテナに依存せず CI で回る。障害時に例外を
   漏らさず "ERROR: ..." 文字列で返る(builtin-tools メモの T11)ことも含む。

末尾の `send_to_workspace(url=)` 節は、URL の解決が共有 resolver
(`utils/image_refs`)経由であること・値の形による振り分け・失敗理由の言い分けの回帰。
"""

import asyncio
import base64
import contextlib
import importlib
import types

import httpx
import pytest

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.ext_mounts import mount_tree
from test.util.mock_user import mock_webui_user
from test.util.tool_errors import call_text


def _arun(coro):
    """asyncio.run + ToolError(#145)を `.text` に戻す — 文面は全文比較で番をする。"""
    return asyncio.run(call_text(coro))


def _flagged(coro):
    """sink を張って呼び、(モデルに見える本文, 結果の種別) を返す(#145)。種別は raise された
    ToolError の kind、無ければ mark_error_kind の旗(実行時と同じく raise が優先)。"""
    from nucllei.utils import tool_output
    from nucllei.utils.tool_errors import ToolError

    sink = tool_output.ToolOutputSink()

    async def main():
        token = tool_output.current_sink.set(sink)
        try:
            return await coro, sink.error_kind()
        except ToolError as e:
            return e.text, e.kind
        finally:
            tool_output.current_sink.reset(token)

    return asyncio.run(main())


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
    import nucllei.env as env
    from nucllei.builtin_tools import sandbox as mod

    # 読み取りは env.py に集約されている (2026-10-03) ので、差し替えるのは
    # `nucllei.env` の属性。os.environ を触って env.py を reload すると .env が
    # 読み直されて「未設定」が作れない。
    olds = {k: getattr(env, k) for k in ("SANDBOX_URL", "SANDBOX_TOKEN")}

    def _apply(values):
        for key, value in values.items():
            setattr(env, key, "" if value is None else value)
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
                "read_file",
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
                _arun(module._post("/files/list", {"user_id": "u1"}, 30))
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
                }
            }
        )
        out = _arun(
            tool.run_python(
                "print(42)",
                __user__={"id": "u1"},
                __chat_id__="c1",
            )
        )
        path, payload, _ = tool.calls[0]
        assert path == "/exec_cell"
        assert payload["user_id"] == "u1" and payload["chat_id"] == "c1"
        assert payload["timeout_s"] == 120  # valves の既定
        assert "42" in out

    def test_run_python_without_chat_id_points_at_run_python_once(self):
        tool = _sandbox({})
        out = _arun(tool.run_python("x=1", __user__={"id": "u1"}))
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
                }
            }
        )
        out = _arun(
            tool.run_python("while True: pass", __user__={"id": "u1"}, __chat_id__="c1")
        )
        assert (
            "**タイムアウト (セルを中断した。変数・import は残っている)** 次の手: 処理を"
            "分けるか、timeout_s を上げて続ける。" in out
        )

    def test_exec_timeout_names_the_limit_and_the_next_step(self):
        """exec のタイムアウトは効いた秒数 (sandbox の上限で clamp) と上限を言う (A20)。"""
        res = {
            "exit_code": -9,
            "duration_s": 600,
            "stdout": "",
            "stderr": "",
            "timed_out": True,
        }
        for given, n in ((30, 30), (None, 120), (900, 600)):
            tool = _sandbox({"/exec": res})
            out = _arun(
                tool.run_shell("sleep 999", timeout_s=given, __user__={"id": "u1"})
            )
            assert (
                f" **タイムアウト (timeout_s={n} 秒を超えたので強制終了した)** 次の手:"
                " 処理を分けるか、timeout_s を上げて呼び直す (上限 600 秒)。" in out
            ), (given, out)

    def test_failed_code_flags_the_result_without_changing_it(self):
        """#145(ken「sandbox の stderr とかは？実行した python のエラーとか」): 実行した
        コードの失敗 (exit_code != 0 / セルの例外 / タイムアウト) は結果に failure の旗を
        立てる。raise しない — 本文 (stdout・traceback・次の手) は旗なしと 1 文字も同じ。
        exit_code 0 の stderr (warning) は旗を立てない。run_shell の非 0 も failure
        (ken 2026-10-04 — staging で `command not found` (exit 127) が緑だった。grep の
        不一致 = exit 1 も amber の「失敗」になるのは許容)。"""
        run = _flagged

        def exec_res(**over):
            return {
                "exit_code": 0,
                "duration_s": 0.1,
                "stdout": "out\n",
                "stderr": "",
                **over,
            }

        def cell_res(**over):
            return {"stdout": "out\n", "duration_s": 0.1, "exports": [], **over}

        user = {"id": "u1"}
        cases = [
            # (tool 呼び出し, 期待する旗)
            (
                lambda: _sandbox(
                    {"/exec": exec_res(exit_code=1, stderr="Traceback\n")}
                ).run_python_once("1/0", __user__=user),
                "failure",
            ),
            (
                lambda: _sandbox(
                    {"/exec": exec_res(exit_code=-9, timed_out=True)}
                ).run_python_once("while 1: pass", __user__=user),
                "failure",
            ),
            (
                lambda: _sandbox(
                    {"/exec": exec_res(stderr="DeprecationWarning\n")}
                ).run_python_once("import x", __user__=user),
                None,
            ),
            (
                lambda: _sandbox({"/exec": exec_res(exit_code=1)}).run_shell(
                    "grep nothing f", __user__=user
                ),
                "failure",
            ),
            (
                lambda: _sandbox(
                    {
                        "/exec": exec_res(
                            exit_code=127,
                            stdout="",
                            stderr="bash: line 1: this_command_does_not_exist:"
                            " command not found\n",
                        )
                    }
                ).run_shell("this_command_does_not_exist --flag", __user__=user),
                "failure",
            ),
            (
                lambda: _sandbox(
                    {"/exec": exec_res(exit_code=-9, timed_out=True)}
                ).run_shell("sleep 999", __user__=user),
                "failure",
            ),
            (
                lambda: _sandbox({"/exec": exec_res()}).run_shell(
                    "echo out", __user__=user
                ),
                None,
            ),
            (
                lambda: _sandbox(
                    {
                        "/exec_cell": cell_res(
                            error="ZeroDivisionError: division by zero"
                        )
                    }
                ).run_python("1/0", __user__=user, __chat_id__="c1"),
                "failure",
            ),
            (
                lambda: _sandbox({"/exec_cell": cell_res(timed_out=True)}).run_python(
                    "while 1: pass", __user__=user, __chat_id__="c1"
                ),
                "failure",
            ),
            (
                lambda: _sandbox({"/exec_cell": cell_res()}).run_python(
                    "print(1)", __user__=user, __chat_id__="c1"
                ),
                None,
            ),
        ]
        for make, expected in cases:
            flagged_text, kind = run(make())
            assert kind == expected, flagged_text
            # 旗の有無で本文は変わらない(sink 無しで呼んだ結果と同じ)
            assert _arun(make()) == flagged_text
        # traceback と stdout は結果に残る
        text, _ = run(cases[7][0]())
        assert "out" in text and "ZeroDivisionError: division by zero" in text

    def test_run_shell_and_run_python_once_use_exec(self):
        res = {
            "exit_code": 0,
            "duration_s": 0.2,
            "stdout": "hello\n",
            "stderr": "",
        }
        tool = _sandbox({"/exec": res})
        out = _arun(tool.run_shell("echo hello", __user__={"id": "u1"}))
        assert tool.calls[0][0] == "/exec"
        assert tool.calls[0][1]["lang"] == "shell"
        assert "exit_code=0" in out and "hello" in out

        tool = _sandbox({"/exec": res})
        _arun(tool.run_python_once("print('hello')", __user__={"id": "u1"}))
        assert tool.calls[0][1]["lang"] == "python"

    def test_run_shell_accepts_timeout_as_a_string(self):
        """staging (dev-1d3bf0224): モデルが `"timeout_s": "120"` を渡すと `+ 30` が TypeError で
        落ち、接続の失敗として「時間を置いて呼び直す」が返っていた。共通の呼び出し口
        (execute_tool_calls) が注釈 Optional[int] に揃えるので普通に動く。直せない値は
        呼び方の誤り (call) で、sandbox には送らない。"""
        from nucllei.utils.middleware import execute_tool_calls
        from nucllei.utils.tools import get_async_tool_function_and_apply_extra_params

        res = {
            "exit_code": 0,
            "duration_s": 0.2,
            "stdout": "hello\n",
            "stderr": "",
        }
        tool = _sandbox({"/exec": res})
        callable_ = get_async_tool_function_and_apply_extra_params(
            tool.run_shell, {"__user__": {"id": "u1"}}
        )
        tools = {
            "run_shell": {
                "callable": callable_,
                "spec": {
                    "name": "run_shell",
                    "parameters": {"properties": {"command": {}, "timeout_s": {}}},
                },
            }
        }

        def run(arguments):
            call = {
                "id": "functions.run_shell:0",
                "function": {"name": "run_shell", "arguments": arguments},
            }
            return asyncio.run(execute_tool_calls([call], tools))[0]

        result = run('{"command": "echo hello", "timeout_s": "120"}')
        assert "error_kind" not in result, result
        assert "exit_code=0" in result["content"] and "hello" in result["content"]
        assert tool.calls[0][1]["timeout_s"] == 120
        assert tool.calls[0][2] == 150

        tool.calls.clear()
        result = run('{"command": "echo hello", "timeout_s": "abc"}')
        assert tool.calls == []
        assert result["error_kind"] == "call"
        assert result["content"] == (
            "ERROR: ツール run_shell の引数 timeout_s は整数 ('abc' は読めない)。"
            "次の手: timeout_s を整数で渡して呼び直す。"
        )

    def test_internal_errors_are_not_reported_as_sandbox_down(self):
        """nucllei 側の例外 (TypeError 等) は「sandbox で実行できなかった / 時間を置いて
        呼び直す」にしない — 呼び直しても同じところで落ちる。例外の文はモデルに出さない (A17)。
        接続の失敗 (OSError・aiohttp・タイムアウト) の文面は従来どおり。"""
        user = {"id": "u1"}
        internal = (
            "ERROR: sandbox ツールの内部でエラーが起きた (サーバ側の不具合)。次の手: "
            "同じ引数で呼び直さない。ユーザーにツールの不具合で実行できなかったと伝える。"
        )
        for error in (
            TypeError('can only concatenate str (not "int") to str'),
            ValueError("bad json"),
        ):
            tool = _sandbox(error=error)
            for coro in (
                tool.run_shell("x", __user__=user),
                tool.run_python("x", __user__=user, __chat_id__="c1"),
                tool.list_workspace(__user__=user),
            ):
                text, kind = _flagged(coro)
                assert text == internal and kind == "fatal", text

        for error in (ConnectionRefusedError("refused"), asyncio.TimeoutError()):
            tool = _sandbox(error=error)
            text, kind = _flagged(tool.run_shell("x", __user__=user))
            assert text.startswith(
                "ERROR: sandbox で実行できなかった。次の手: 時間を置いて"
            )
            assert kind == "failure"

    def test_missing_user_context_is_refused_everywhere(self):
        tool = _sandbox({})
        for coro in (
            tool.run_python("x", __chat_id__="c1"),
            tool.run_python_once("x"),
            tool.run_shell("x"),
            tool.write_file("/workspace/a.txt", "x"),
            tool.list_workspace(),
            tool.fetch_from_workspace("/workspace/a.txt"),
            tool.send_to_workspace(),
        ):
            assert _arun(coro).startswith("ERROR:")
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
                _arun(tool._post("/files/list", {"user_id": "u1"}, 30))
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
                _arun(tool._post("/files/list", {"user_id": "u1"}, 30))
            finally:
                mod.aiohttp = real

            assert seen["headers"] == {}

    def test_401_error_says_auth_failed_without_the_env_name(self):
        """401 はモデルには「認証に失敗した (サーバの設定の問題)」+ 呼び直さない、だけを言う。
        原因 (両側の SANDBOX_TOKEN 不一致) はログにだけ残す — env 名をモデルに出さない
        (2026-10-04 llens 監査 A18)。"""
        from nucllei.builtin_tools.sandbox import SandboxHTTPError

        tool = _sandbox(
            error=SandboxHTTPError(
                401,
                "認証に失敗した。nucllei と sandbox の "
                "SANDBOX_TOKEN が一致していない "
                "(片側だけ未設定の場合も含む。配備の env を確認)",
            )
        )
        out = _arun(tool.list_workspace(__user__={"id": "u1"}))
        assert out == (
            "ERROR: sandbox との認証に失敗した (サーバの設定の問題)。次の手: 呼び直さない。"
            "ユーザーには sandbox が今使えないと伝え、管理者への連絡を勧める。"
        )
        assert "SANDBOX_TOKEN" not in out
        # 繋がってはいるので「接続できない」とは言わない
        assert "接続できない" not in out
        # 鍵の設定の誤り = サーバが壊れている → fatal(赤。2026-10-04 ken)
        from nucllei.utils.tool_errors import ToolError

        with pytest.raises(ToolError) as e:
            asyncio.run(tool.list_workspace(__user__={"id": "u1"}))
        assert e.value.kind == "fatal"

    def test_http_failure_is_graceful(self):
        """T11: 接続不能・非 200 とも例外を漏らさず "ERROR: ..." で返す。detail はモデルに
        出さない (ログだけ。A17)。実行系は「実行できなかった」、/files は status だけ言う。"""
        from nucllei.builtin_tools.sandbox import SandboxHTTPError

        tool = _sandbox(error=SandboxHTTPError(502, "bad gateway"))
        calls = [
            tool.run_python("x", __user__={"id": "u1"}, __chat_id__="c1"),
            tool.run_python_once("x", __user__={"id": "u1"}),
            tool.run_shell("x", __user__={"id": "u1"}),
            tool.write_file("/workspace/a.txt", "x", __user__={"id": "u1"}),
            tool.list_workspace(__user__={"id": "u1"}),
            tool.fetch_from_workspace("/workspace/a.txt", __user__={"id": "u1"}),
        ]
        outs = [_arun(coro) for coro in calls]
        for out in outs:
            assert out.startswith("ERROR:") and "bad gateway" not in out
            assert "次の手: " in out
        for out in outs[:3]:
            assert out.startswith("ERROR: sandbox で実行できなかった。次の手: "), out
        for out in outs[3:]:
            assert out == (
                "ERROR: sandbox がエラーを返した (HTTP 502)。次の手: 1 回だけ呼び直す。"
                "続くならユーザーに伝える。"
            )

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
        out = _arun(tool.send_to_workspace(file_id="f-mine", __user__={"id": "owner"}))
        assert "/workspace/imports/mine.txt" in out
        assert tool.calls[0][1]["path"] == "imports/mine.txt"

        tool = _sandbox({"/files/write": {"ok": True}})
        out = _arun(tool.send_to_workspace(file_id="f-yours", __user__={"id": "owner"}))
        # 「レコードが無い」と同じ文(存在の有無を漏らさない)。id が UUID の形でないので
        # 受ける形と行き先の規則も前に添わる
        from nucllei.utils.image_refs import FILE_ID_ARG_FORM, REF_ARGS_RULE

        assert out == (
            f"ERROR: file_id f-yours {NO_FILE}。"
            f"次の手: {FILE_ID_ARG_FORM} {REF_ARGS_RULE} {NO_FILE_NEXT}"
        )
        assert tool.calls == []

    def test_write_file_guards_path_and_size(self):
        tool = _sandbox({"/files/write": {"ok": True}})
        user = {"id": "u1"}
        for bad in (
            "../escape",
            "/workspace/../etc/passwd",
            "/etc/passwd",
            "/ext/server/a.txt",
            "/workspacefoo/a.txt",
            "",
            # 相対パスは受けない (/workspace からの相対として救済しない)
            "work/a.txt",
            "./a.txt",
            "https://x.example/y.html",
            # ディレクトリを指すもの (ファイル名が無い)
            "/workspace",
            "/workspace/dir/",
        ):
            out = _arun(tool.write_file(bad, "x", __user__=user))
            assert out.startswith("ERROR:"), bad
            # tool 側で弾いたのであって、接続の問題ではない
            assert "接続できない" not in out, bad
        # read-only の置き場は理由を言う
        assert "read-only" in _arun(
            tool.write_file("/ext/server/a.txt", "x", __user__=user)
        )
        # 形の誤りは全 tool 共通の形 + この tool の path が受ける形。行き先の規則は URL を
        # 渡した (取り違えらしい) ときだけ — 相対パスには付けない (llens staging 2026-10-04)
        from nucllei.utils.image_refs import REF_ARGS_RULE, WORKSPACE_PATH_ARG_FORM

        for bad, rule in (
            ("work/a.txt", ""),
            ("/etc/passwd", ""),
            ("https://x.example/y.html", f" {REF_ARGS_RULE}"),
        ):
            assert _arun(tool.write_file(bad, "x", __user__=user)) == (
                f"ERROR: {bad} は sandbox のパスの形でない。"
                f"次の手: {WORKSPACE_PATH_ARG_FORM}{rule}"
            )
        assert _arun(tool.write_file("", "x", __user__=user)).startswith(
            "ERROR: (空) は sandbox のパスの形でない。"
        )
        assert tool.calls == []

        from nucllei.builtin_tools import sandbox as mod

        big = "a" * (mod.TEXT_MAX_BYTES + 1)
        assert _arun(
            tool.write_file("/workspace/big.txt", big, __user__=user)
        ).startswith("ERROR:")
        assert tool.calls == []

        # exports/ も普通の置き場 — 書くだけで添付しない (自動添付は 2026-10-19 に廃止)
        out = _arun(tool.write_file("/workspace/exports/r.html", "<p>", __user__=user))
        assert out == "/workspace/exports/r.html に書き込んだ (3B)。"

    def test_workspace_paths_are_absolute_in_and_out(self):
        """3 本とも /workspace/ の絶対パスだけを受け、戻り値も絶対。sandbox の wire
        (workspace 相対) は変えない。相対パスは受けない (下の test)。"""
        user = {"id": "u1"}
        png = base64.b64encode(b"x").decode()
        responses = {
            "/files/write": {"ok": True},
            "/files/list": {"entries": [{"path": "imports/a.csv", "size": 2048}]},
            "/files/read": {"name": "a.csv", "size": 1, "b64": png},
        }

        # (渡す path, sandbox に届く相対, 戻り値に出る絶対)
        cases = [
            ("/workspace/imports/a.csv", "imports/a.csv", "/workspace/imports/a.csv"),
            (
                "//workspace//imports/./a.csv",
                "imports/a.csv",
                "/workspace/imports/a.csv",
            ),
            (
                "/workspace/x/../imports/a.csv",
                "imports/a.csv",
                "/workspace/imports/a.csv",
            ),
        ]
        for given, rel, absolute in cases:
            tool = _sandbox(responses)
            out = _arun(tool.write_file(given, "x", __user__=user))
            assert tool.calls[0][0] == "/files/write"
            assert tool.calls[0][1]["path"] == rel, given
            assert f"{absolute} に書き込んだ" in out, given

            tool = _sandbox(responses)
            out = _arun(tool.fetch_from_workspace(given, __user__=user))
            assert tool.calls[0][1]["path"] == rel, given
            assert out.startswith(f"{absolute} ("), given

        # /workspace/x を /workspace/workspace/x に読み替えない (旧不具合)
        tool = _sandbox(responses)
        _arun(tool.write_file("/workspace/x.txt", "x", __user__=user))
        assert tool.calls[0][1]["path"] == "x.txt"

        # 一覧: 既定 (= /workspace) / 絶対のどちらでも、戻り値の各行は絶対パス
        for args, rel in (
            ((), "."),
            (("/workspace",), "."),
            (("/workspace/",), "."),
            (("/workspace/imports",), "imports"),
        ):
            tool = _sandbox(responses)
            out = _arun(tool.list_workspace(*args, __user__=user))
            assert tool.calls[0][1]["path"] == rel, args
            assert out == "- /workspace/imports/a.csv (2.0KB)", args

        tool = _sandbox({"/files/list": {"entries": []}})
        out = _arun(tool.list_workspace("/workspace/sub", __user__=user))
        assert "/workspace/sub は空 (" in out

    def test_list_workspace_tells_missing_file_and_empty_apart(self):
        """#145(ken: staging で存在しないディレクトリの一覧が緑の「空」だった): sandbox の
        `exists` / `is_dir` で「無い」= outcome、「ファイル」= call、「空」= 通常の結果を
        言い分ける。フィールドを返さない旧 sandbox image では言い分けられないので、旧文面の
        まま outcome の旗だけ立てる。"""
        user = {"id": "u1"}
        listed = {"path": "a.csv", "size": 2048}
        cases = [
            (
                {"entries": [], "exists": False, "is_dir": False},
                "ERROR: /workspace/そんなディレクトリは無い は存在しない。"
                "次の手: 親ディレクトリを list_workspace で一覧して、パスを確かめる。",
                "outcome",
            ),
            (
                {"entries": [], "exists": True, "is_dir": False},
                "ERROR: /workspace/そんなディレクトリは無い はファイルで、ディレクトリではない。"
                "次の手: 一覧するならディレクトリを渡す (中身を読むなら read_file、ユーザーに"
                "渡すなら fetch_from_workspace)。",
                "call",
            ),
            (
                {"entries": [], "exists": True, "is_dir": True},
                "/workspace/そんなディレクトリは無い は空 (ファイルが無い)。",
                None,
            ),
            (
                {"entries": [listed], "exists": True, "is_dir": True},
                "- /workspace/a.csv (2.0KB)",
                None,
            ),
            # 旧 sandbox image (exists / is_dir 無し)
            (
                {"entries": []},
                "/workspace/そんなディレクトリは無い は空"
                " (ファイルが無いか、ディレクトリが存在しない)。",
                "outcome",
            ),
            ({"entries": [listed]}, "- /workspace/a.csv (2.0KB)", None),
        ]
        for res, text, kind in cases:
            tool = _sandbox({"/files/list": res})
            got = _flagged(
                tool.list_workspace(
                    "/workspace/そんなディレクトリは無い", __user__=user
                )
            )
            assert got == (text, kind), res

    def test_send_to_workspace_flags_nothing_imported(self, tmp_path):
        """#145: 添付一覧からの取り込みで 1 件も取り込めなかった結果は、文面はそのままで旗。
        filename で指した添付が無い = outcome / 何も指さず添付も無い = call /
        対象はあったが全部失敗 = failure / 1 件でも取り込めたら旗なし。"""
        from nucllei.models.files import FileForm

        src = tmp_path / "a.csv"
        src.write_text("x,y", encoding="utf-8")
        self.Files.insert_new_file(
            "u1",
            FileForm(
                id="f-a",
                filename="a.csv",
                path=str(src),
                data={},
                meta={"name": "a.csv", "content_type": "text/csv", "size": 3},
            ),
        )
        user = {"id": "u1"}
        files = [
            {"id": "f-a", "name": "a.csv"},
            {"id": "f-gone", "name": "gone.csv"},
        ]

        tool = _sandbox({"/files/write": {"ok": True}})
        assert _flagged(
            tool.send_to_workspace(filename="zzz", __user__=user, __files__=files)
        ) == ("取り込み対象の添付ファイルが無かった。", "outcome")
        assert _flagged(tool.send_to_workspace(__user__=user, __files__=[])) == (
            "取り込み対象の添付ファイルが無かった。",
            "call",
        )
        text, kind = _flagged(
            tool.send_to_workspace(filename="gone", __user__=user, __files__=files)
        )
        assert kind == "failure" and "gone.csv: レコードが無い" in text
        text, kind = _flagged(tool.send_to_workspace(__user__=user, __files__=files))
        assert kind is None
        assert "/workspace/imports/a.csv" in text and "gone.csv" in text

    def test_relative_paths_are_refused(self):
        """path の形は絶対パス 1 つ。相対を「/workspace からの相対」として通さない
        (`https://x/y.html` が `/workspace/https:/x/y.html` になる、を作らない)。
        エラーは値によらず同じ形 (全 tool 共通) + その tool の path が受ける形の固定文。"""
        from nucllei.utils.image_refs import (
            PATH_ARG_FORM,
            REF_ARGS_RULE,
            WORKSPACE_PATH_ARG_FORM,
        )

        user = {"id": "u1"}
        tool = _sandbox({})
        for bad in ("imports/a.csv", "./imports/a.csv", ".", "https://x.example/y"):
            # 行き先の規則は URL (取り違えらしい) のときだけ
            rule = f" {REF_ARGS_RULE}" if bad.startswith("https://") else ""
            for coro, form in (
                (tool.write_file(bad, "x", __user__=user), WORKSPACE_PATH_ARG_FORM),
                (tool.list_workspace(bad, __user__=user), WORKSPACE_PATH_ARG_FORM),
                (tool.fetch_from_workspace(bad, __user__=user), PATH_ARG_FORM),
            ):
                assert _arun(coro) == (
                    f"ERROR: {bad} は sandbox のパスの形でない。次の手: {form}{rule}"
                ), bad
        assert tool.calls == []

    def test_send_to_workspace_result_feeds_list_and_fetch(self):
        """send_to_workspace が返す絶対パスを、そのまま list / fetch に渡せる。"""
        user = {"id": "u1"}
        given = "/workspace/imports/mine.txt"
        tool = _sandbox(
            {
                "/files/list": {"entries": [{"path": "imports/mine.txt", "size": 6}]},
                "/files/read": {
                    "name": "mine.txt",
                    "size": 6,
                    "b64": base64.b64encode(b"abcdef").decode(),
                },
            }
        )
        listed = _arun(tool.list_workspace("/workspace/imports", __user__=user))
        assert f"- {given} (" in listed
        out = _arun(tool.fetch_from_workspace(given, __user__=user))
        assert out.startswith(f"{given} (") and "file_id=" in out
        # 画像・音声・動画でない添付はファイル一覧にだけ載る — 本文で示すリンクを渡す
        assert "本文で示すなら [mine.txt](/api/v1/files/" in out
        assert "画面には既に表示されている" not in out
        assert [c[1]["path"] for c in tool.calls] == ["imports", "imports/mine.txt"]

    def test_fetch_records_the_source_path(self):
        """fetch_from_workspace で添付した Files の meta.source に由来の sandbox の絶対パスを残す
        (添付 dict の meta には載せない — 画面・モデルには出さない)。"""
        import re

        user = {"id": "u-src"}
        tool = _sandbox(
            {
                "/files/read": {
                    "name": "a.csv",
                    "size": 3,
                    "b64": base64.b64encode(b"x,y").decode(),
                }
            }
        )
        emitted = []

        async def emitter(event):
            emitted.append(event)

        out = _arun(
            tool.fetch_from_workspace(
                "/workspace/out/a.csv", __user__=user, __event_emitter__=emitter
            )
        )
        fid = re.search(r"file_id=([0-9a-f-]+)", out).group(1)
        rec = self.Files.get_file_by_id(fid)
        assert rec.meta["source"] == "/workspace/out/a.csv"
        assert rec.meta["name"] == "a.csv" and rec.meta["size"] == 3
        assert "source" not in emitted[0]["data"]["files"][0]["file"]["meta"]
        # 戻り値は表示されないファイル = 本文で示すリンクの書き方
        assert "次の手: 本文で示すなら [a.csv](/api/v1/files/" in out

    def test_fetch_refuses_in_subagent_session(self):
        """サブエージェントの子ターン (metadata の agent_session — utils/subagent が立てる) では
        添付しない: 子の chat はユーザーに見えない。sandbox にも触らない。"""
        tool = _sandbox({"/files/read": {"name": "a.csv", "size": 1, "b64": "eA=="}})
        for path in ("/workspace/out/a.csv", "/ext/groups/g/a.pdf", "/ext/doc/x.md"):
            text, kind = _flagged(
                tool.fetch_from_workspace(
                    path,
                    __user__={"id": "u1"},
                    __chat_id__="child",
                    __metadata__={"chat_id": "child", "agent_session": True},
                )
            )
            assert kind == "call"
            assert text == (
                "ERROR: ここはサブエージェントの会話で、添付してもユーザーには見えない。"
                "次の手: 書いたファイルの絶対パスを主エージェントへの結果に書く。"
                "渡すのは主エージェント。"
            )
        assert tool.calls == []
        # 親 (通常のターン) はそのまま添付する
        out = _arun(
            tool.fetch_from_workspace(
                "/workspace/out/a.csv",
                __user__={"id": "u1"},
                __metadata__={"chat_id": "parent"},
            )
        )
        assert "file_id=" in out

    def test_list_and_fetch_refuse_paths_outside_workspace(self):
        tool = _sandbox({})
        user = {"id": "u1"}
        for bad in ("/etc", "../..", "/workspace/../etc", "/workspacefoo"):
            for coro in (
                tool.list_workspace(bad, __user__=user),
                tool.fetch_from_workspace(bad, __user__=user),
            ):
                out = _arun(coro)
                assert out.startswith(
                    f"ERROR: {bad} は sandbox のパスの形でない。"
                ), bad
        # /ext/ は sandbox の /files/* が扱えない — 使える手段を案内して断る
        out = _arun(tool.list_workspace("/ext/server42", __user__=user))
        assert out.startswith("ERROR:") and "run_shell" in out and "ls" in out
        # `/ext/…` はパスがそのまま URL — 見せるならそのパスでリンクを書く (固定文)
        assert "そのパスをそのまま本文のリンクに書く" in out
        assert "fetch_from_workspace" not in out
        # fetch_from_workspace は /ext/ を受ける (sandbox の /files/* は使わず、nucllei 側の
        # 同じ置き場 静的 mount から読む — 末尾の「fetch_from_workspace(path) の /ext/…」節)。無ければ不在
        out = _arun(tool.fetch_from_workspace("/ext/server42/a.png", __user__=user))
        assert out.startswith("ERROR:") and "このサーバの /ext に無い" in out
        assert tool.calls == []

    def test_file_errors_say_what_went_wrong(self):
        """400 = パスの問題、404 = 無い、413 = 大きい、それ以外の例外 = 接続の問題。"""
        from nucllei.builtin_tools.sandbox import SandboxHTTPError

        user = {"id": "u1"}

        def outs(error):
            tool = _sandbox(error=error)
            return [
                _arun(tool.write_file("/workspace/a.txt", "x", __user__=user)),
                _arun(tool.list_workspace("/workspace/a", __user__=user)),
                _arun(tool.fetch_from_workspace("/workspace/a.txt", __user__=user)),
            ]

        # sandbox 側の _safe_path が弾いた (symlink 越しの脱出など、tool からは見えない分)
        # detail はログだけ (A17)
        for out in outs(SandboxHTTPError(400, "path escapes workspace: 'a.txt'")):
            assert out.startswith("ERROR:") and "path escapes workspace" not in out
            assert "は使えない path (sandbox が受け付けなかった)" in out
            assert "接続できない" not in out

        out = outs(SandboxHTTPError(404, "not a file: a.txt"))[2]
        assert "/workspace/a.txt というファイルは無い" in out
        assert "期限で消えた" not in out

        # #149: imports/ exports/ は期限で消えるキャッシュ — 無いときは戻し方を添える
        for path in ("/workspace/imports/a.csv", "/workspace/exports/b.png"):
            tool = _sandbox(error=SandboxHTTPError(404, "not a file"))
            out = _arun(tool.fetch_from_workspace(path, __user__=user))
            assert f"{path} というファイルは無い" in out
            assert "期限で消えた可能性" in out and "send_to_workspace(file_id)" in out
        tool = _sandbox({"/files/list": {"entries": [], "exists": False}})
        out = _arun(tool.list_workspace("/workspace/imports", __user__=user))
        assert "期限で消えた可能性" in out
        # 名前が似ているだけの別の場所には付けない
        tool = _sandbox(error=SandboxHTTPError(404, "not a file"))
        out = _arun(tool.fetch_from_workspace("/workspace/imports2/a", __user__=user))
        assert "期限で消えた" not in out

        out = outs(SandboxHTTPError(413, "file too large: 9 bytes (limit 5)"))[2]
        assert "添付できる上限 5 B" in out and "接続できない" not in out

        # 403 = read-only の置き場 (#152)。代わりの書き先は勧めない (どこに書くかは利用者が
        # 決める)。「管理者だけ」は /ext/public のときだけ (/ext/doc 等は管理者も ro)
        from nucllei.builtin_tools.sandbox import _files_error

        for path, admin_only in (
            ("/workspace/a.txt", False),
            ("/ext/doc/a.md", False),
            ("/ext/public/a.md", True),
            ("/ext/publicx/a.md", False),
        ):
            out = _files_error(SandboxHTTPError(403, "read-only"), path).text
            assert out.startswith(f"ERROR: {path} は read-only"), out
            assert ("書けるのは管理者だけ" in out) is admin_only, out
            assert "別の置き場に書くかはユーザーが決める" in out
            assert "/workspace/` か" not in out and "/ext/groups/<" not in out

        for out in outs(ConnectionRefusedError("refused")):
            assert out.startswith("ERROR: sandbox に接続できない")

    def test_toolkit_note_tells_imports_expire(self):
        """#149: imports/ の期限と戻し方は toolkit 共通注記 (クラス docstring) に 1 回だけ。
        exports/ は 2026-10-19 に廃止 — 置き場として案内しない。渡すのは fetch_from_workspace。"""
        from nucllei.builtin_tools.sandbox import Tools

        import re

        note = re.sub(r"\s*\n\s*", "", Tools.__doc__)  # docstring の折り返しを繋ぐ
        assert "imports/" in note and "30 日" in note
        assert "exports" not in note and "自動" not in note
        assert "fetch_from_workspace を呼ぶ" in note
        # 管理者の例外 (ext_acl: admin は全グループ・/ext/public が rw)
        assert "管理者はすべてのグループの置き場と" in note
        assert "`/ext/public/` に書ける" in note
        assert "send_to_workspace(file_id)" in note
        # 消すときは rm せず trash/ の直下へ移す (同じ規則で消える)
        assert "/workspace/trash/" in note and "直下" in note

    def test_import_tells_cache_expiry(self, tmp_path):
        """#149: sandbox が応答で返す `cache_ttl_days` から「今日 + N 日 以降に消える」を
        取り込みの結果に 1 行添える。キー欠落 (古い sandbox)・0 では添えない。"""
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo

        from nucllei.models.files import FileForm

        def day(n):
            return datetime.now(ZoneInfo("Asia/Tokyo")).date() + timedelta(days=n)

        src = tmp_path / "exp.txt"
        src.write_text("中身", encoding="utf-8")
        self.Files.insert_new_file(
            "u-exp",
            FileForm(
                id="f-exp",
                filename="exp.txt",
                path=str(src),
                data={},
                meta={"name": "exp.txt", "content_type": "text/plain", "size": 6},
            ),
        )
        user = {"id": "u-exp"}
        tool = _sandbox({"/files/write": {"ok": True, "cache_ttl_days": 30}})
        out = _arun(tool.send_to_workspace(file_id="f-exp", __user__=user))
        assert out == (
            "取り込み結果:\n- exp.txt (6B) → /workspace/imports/exp.txt\n"
            f"(/workspace/imports/ の写しは {day(30)} 以降に消える。"
            "消えたら send_to_workspace で取り込み直す)"
        )
        # 添付一覧からの一括取り込みも同じ 1 行
        tool = _sandbox({"/files/write": {"ok": True, "cache_ttl_days": 3}})
        out = _arun(
            tool.send_to_workspace(
                __user__=user, __files__=[{"id": "f-exp", "name": "exp.txt"}]
            )
        )
        assert out.endswith(
            f"写しは {day(3)} 以降に消える。消えたら send_to_workspace で取り込み直す)"
        )
        for res in ({"ok": True}, {"ok": True, "cache_ttl_days": 0}):
            tool = _sandbox({"/files/write": res})
            out = _arun(tool.send_to_workspace(file_id="f-exp", __user__=user))
            assert "消える" not in out and "/workspace/imports/exp.txt" in out


# =============================================================================
# send_to_workspace(url=) の契約
#
# URL の解決は共有 resolver(`utils/image_refs.resolve_ref`)一本 — 許可範囲・redirect の
# 扱い・失敗理由が配備層の fetch_url / feed_image と同じになる(独自の self fetch /
# LAN fetch は撤去した。許可先の外の URL は取りに行かない)。ここで固定するのは
# tool 側の持ち分: 引数名どおりの解釈(url は URL、file_id は file の id — 値の形を見て
# 別の引数に読み替えない)、RefError の言い分け、「解決先が sandbox から既に見えている物は
# コピーしない」、取り込み先と戻り値の絶対パス。
#
# DB を触らないので AbstractPostgresTest には載せない(file_id へ吸収する経路だけ
# Files / Storage を差し替える)。ネットワークは ext_upstreams の共有 client を
# MockTransport に差し替えて切り離す。
# =============================================================================

# 一般の URL のホスト(DNS は `_self_env` で差し替える。実ネットワークに出ない)
INTRA = "http://10.1.2.3"
FILE_UUID = "0b54ed0c-6f0b-4b5c-9d0e-3f6f3f0a1c11"
# url が受ける形の固定文(値を調べて出し分けない。llens の tool と共有する公開定数)
from nucllei.utils.image_refs import (  # noqa: E402
    FILE_ID_ARG_FORM,
    PATH_ARG_FORM,
    REF_ARGS_RULE,
    URL_ARG_FORM,
)

NO_FILE = "のレコードが無い、またはアクセス権が無い (どちらなのかは区別できない)"
NO_FILE_NEXT = (
    "list_chat_files か grep_chat_files が使えるなら、このチャットの file_id を確かめて渡し直す。"
    "ユーザーには「このファイルは開けなかった」と伝え、存在しないとは言い切らない。"
)


class _UrlEnv:
    """1 テスト分の道具立て: tool + 偽の上流(MockTransport)+ 偽 _post + 記録。"""

    def __init__(self, monkeypatch, handler=None):
        from nucllei.builtin_tools.sandbox import Tools
        from nucllei.utils import ext_upstreams

        self.monkeypatch = monkeypatch
        self.tools = Tools()
        self.gets: list[str] = []  # 上流へ出た URL
        self.sent_headers: list[dict] = []
        self.writes: list[dict] = []

        def transport(request):
            self.gets.append(str(request.url))
            self.sent_headers.append(dict(request.headers))
            if handler is None:
                return httpx.Response(200, content=b"data")
            return handler(request)

        monkeypatch.setattr(
            ext_upstreams,
            "_client",
            httpx.AsyncClient(
                transport=httpx.MockTransport(transport), follow_redirects=False
            ),
        )

        async def fake_post(path, payload, timeout_s):
            self.writes.append({"path": path, "payload": payload})
            return {}

        self.tools._post = fake_post

        # url= の取り込みは `/files/raw` へストリーミング(`sandbox_put_raw`)。本文を集めて
        # `/files/write` と同じ形 (path / b64) で記録する — 取り込み先・中身の検査を共通にする
        from nucllei.builtin_tools import sandbox as mod

        self.raw_response: dict = {}
        self.raw_error: Exception | None = None

        async def fake_put_raw(params, body, content_length=None):
            data = b"".join([c async for c in body])
            self.writes.append(
                {
                    "path": "/files/raw",
                    "payload": {
                        "path": params["path"],
                        "b64": base64.b64encode(data).decode(),
                        "params": params,
                        "content_length": content_length,
                    },
                }
            )
            if self.raw_error is not None:
                raise self.raw_error
            return self.raw_response

        monkeypatch.setattr(mod, "sandbox_put_raw", fake_put_raw)

    def mount_root(self, root):

        mount_tree(self.monkeypatch, root)

    def files(self, tmp_path, records: dict):
        """Files / Storage を差し替える。records = {file_id: (owner, filename, bytes)}。"""
        from nucllei.builtin_tools import sandbox as mod

        recs = {}
        for fid, (owner, filename, data) in records.items():
            src = tmp_path / f"{fid}_{filename}"
            src.write_bytes(data)
            recs[fid] = types.SimpleNamespace(
                user_id=owner, filename=filename, path=str(src)
            )
        self.monkeypatch.setattr(
            mod.Files, "get_file_by_id", staticmethod(lambda fid: recs.get(fid))
        )
        self.monkeypatch.setattr(mod.Storage, "get_file", staticmethod(lambda p: p))

    def run(self, **kwargs):
        return _arun(self.tools.send_to_workspace(__user__={"id": "u1"}, **kwargs))


def test_url_exclusive_with_file_id_and_filename(monkeypatch):
    env = _UrlEnv(monkeypatch)
    for extra in ({"file_id": "some-uuid"}, {"filename": "x"}):
        result = env.run(url="http://his-info.test/docs/a.pdf", **extra)
        assert result.startswith("ERROR") and "同時に指定できない" in result, extra


def test_spec_has_no_request_param_and_no_lan_valve():
    """cookie 転送も CIDR ガードも無い(resolver が境界を持つ)ことを形で固定する。"""
    import inspect

    from nucllei.builtin_tools import sandbox as mod

    assert (
        "__request__" not in inspect.signature(mod.Tools.send_to_workspace).parameters
    )
    assert "url_blocked_cidrs" not in mod.Tools.Valves.model_fields
    for gone in ("_fetch_self", "_fetch_lan", "_http_get", "_blocked_ip_error"):
        assert not hasattr(mod.Tools, gone), gone
    assert not hasattr(mod, "SELF_BASE_URL")


# --- resolver 経由の取り込み(一般の URL / /ext の橋渡し)--------------------------


def test_general_url_is_fetched_and_copied(monkeypatch):
    # 一般の URL は直接取りに行く(接続先は固定済みのアドレス)。cookie / 認証ヘッダは送らない
    env = _UrlEnv(monkeypatch, lambda r: httpx.Response(200, content=b"%PDF-fake"))
    _self_env(monkeypatch)
    result = env.run(url=f"{INTRA}/static/docs/10-14.pdf?rev=2#page=3")
    assert env.gets == [f"{INTRA}/static/docs/10-14.pdf?rev=2"]
    assert "cookie" not in env.sent_headers[0]
    assert "authorization" not in env.sent_headers[0]
    assert env.writes[0]["payload"]["path"] == "imports/10-14.pdf"
    assert base64.b64decode(env.writes[0]["payload"]["b64"]) == b"%PDF-fake"
    # 戻り値は絶対パス(そのまま run_python / list_workspace / fetch_from_workspace へ)
    assert not result.startswith("ERROR")
    assert "→ /workspace/imports/10-14.pdf" in result


def test_url_import_tells_cache_expiry(monkeypatch):
    # #149: url= の取り込みも `/files/write` の応答 `cache_ttl_days` から期限を 1 行添える
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    env = _UrlEnv(monkeypatch, lambda r: httpx.Response(200, content=b"%PDF-fake"))
    _self_env(monkeypatch)

    env.raw_response = {"ok": True, "cache_ttl_days": 30}
    result = env.run(url=f"{INTRA}/static/docs/10-14.pdf")
    day = datetime.now(ZoneInfo("Asia/Tokyo")).date() + timedelta(days=30)
    assert result.endswith(
        f"→ /workspace/imports/10-14.pdf\n(/workspace/imports/ の写しは {day} 以降に消える。"
        "消えたら send_to_workspace で取り込み直す)"
    )


def test_ext_url_is_read_from_the_mount_without_connecting(monkeypatch, tmp_path):
    # path が /ext/{rest} の URL は host を問わず EXT_MOUNTS の静的 mount の {rest} をローカル読み。
    # 与えられた host にも自オリジンにも接続しない。実在すればコピーせず sandbox のパス
    # (= 相対 URL と同じ文字列、decode 済み)を返す
    root = _cage_mount(tmp_path)
    env = _UrlEnv(monkeypatch)
    env.mount_root(root)
    for url in (
        "https://elsewhere.example/ext/cage/static/%E5%9B%B3/a.png?rev=2",
        "HTTPS://llens.test/ext/cage/static/%E5%9B%B3/a.png",
        "/ext/cage/static/%E5%9B%B3/a.png",
    ):
        result = env.run(url=url)
        assert "取り込み不要" in result and "/ext/cage/static/図/a.png" in result, url
        assert str(root) not in result, url  # nucllei 側の実体のパスは見せない
    assert env.gets == [] and env.writes == []


# --- 許可先の外 / 失敗理由の言い分け -------------------------------------------


def _self_env(monkeypatch, addresses=("10.106.0.5", "10.106.0.1"), dns=None):
    """自分自身の発見(実行時)と DNS を差し替える。"""
    import ipaddress

    from nucllei.utils import ext_upstreams

    found = frozenset(ipaddress.ip_address(a) for a in addresses)
    monkeypatch.setattr(ext_upstreams, "discover_self_addresses", lambda: found)
    ext_upstreams.reset_self_addresses()

    async def fake_lookup(host):
        return list((dns or {}).get(host, []))

    monkeypatch.setattr(ext_upstreams, "lookup_host", fake_lookup)


def test_server_itself_is_not_fetched(monkeypatch):
    """挙動の番兵: 一般の URL で取りに行かないのは **このサーバ自身(と同居するサービス)**
    だけ(loopback・自分のアドレス・既定ゲートウェイ・host.docker.internal)。固定文で
    「このサーバ自身」と言い、リンク切れとは言わない。繋ぎもしない。"""
    env = _UrlEnv(monkeypatch)
    _self_env(monkeypatch, dns={"host.docker.internal": ["10.106.0.1"]})
    for u in (
        "http://127.0.0.1:8000/files/write",
        "http://[::1]:3000/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.106.0.5:13303/",
        "http://10.106.0.1:13303/",
        "http://host.docker.internal:13303/",
    ):
        result = env.run(url=u)
        assert result.startswith("ERROR") and "このサーバ自身" in result, u
        # url が受ける形を固定文で言う。リンク切れとは言わない
        assert result.endswith(URL_ARG_FORM), u
        assert "リンク切れ" not in result, u
    assert env.gets == [] and env.writes == []


def test_unmounted_ext_path_is_not_found(monkeypatch, tmp_path):
    # 静的 mount に無い /ext/… は存在しない(表に無い name か、静的 mount に無い物 — 2026-10-05)
    env = _UrlEnv(monkeypatch)
    env.mount_root(_cage_mount(tmp_path))
    for url in ("https://llens.test/ext/nosuch/a.png", "/ext/nosuch/a.png", "/ext/"):
        result = env.run(url=url)
        assert result.startswith("ERROR") and "存在しない" in result, url
    assert env.gets == [] and env.writes == []


def test_unknown_upstream_wording_follows_reason(monkeypatch):
    """`unknown_upstream` の内訳は resolver の `RefError.reason`(解決結果)で言い分ける。
    tool は値を自前でパースし直さない。reason は server_self だけ(2026-10-03)。"""
    env = _UrlEnv(monkeypatch)
    _self_env(monkeypatch)
    itself = env.run(url="http://127.0.0.1/a.pdf")
    assert (
        "このサーバ自身 (または同居するサービス) を指しているので取りに行かない"
        in itself
    )
    assert itself.startswith("ERROR") and itself.endswith(URL_ARG_FORM)
    assert env.gets == [] and env.writes == []


def test_general_url_is_imported_from_anywhere(monkeypatch):
    """登録無しの URL(閉域の本番は IP リテラル。インターネットのホストも同じ)をそのまま
    取り込める。接続先は固定済みのアドレス、Host は元のまま。redirect は辿る。"""

    def upstream(request):
        if request.url.path == "/old":
            return httpx.Response(302, headers={"location": "http://10.1.2.4/new.pdf"})
        return httpx.Response(
            200, content=b"%PDF-1", headers={"content-type": "application/pdf"}
        )

    env = _UrlEnv(monkeypatch, upstream)
    _self_env(monkeypatch, dns={"example.com": ["93.184.216.34"]})
    result = env.run(url="http://10.1.2.3/his-info/%E6%A7%98%E5%BC%8F.pdf")
    assert not result.startswith("ERROR"), result
    assert env.gets == ["http://10.1.2.3/his-info/%E6%A7%98%E5%BC%8F.pdf"]
    assert env.sent_headers[0]["host"] == "10.1.2.3"
    assert env.writes[-1]["payload"]["path"].endswith("imports/様式.pdf")
    result = env.run(url="http://10.1.2.3/old")
    assert not result.startswith("ERROR"), result
    assert env.gets[-2:] == ["http://10.1.2.3/old", "http://10.1.2.4/new.pdf"]
    assert env.writes[-1]["payload"]["path"].endswith("imports/new.pdf")
    result = env.run(url="https://example.com/a.pdf")
    assert not result.startswith("ERROR"), result
    assert env.gets[-1] == "https://93.184.216.34/a.pdf"
    assert env.sent_headers[-1]["host"] == "example.com"


def test_general_url_wording_follows_reason(monkeypatch):
    """辿れなかった redirect(自分自身へ・回数超過)・引けない名前も reason で言い分ける。"""

    def upstream(request):
        path = request.url.path
        if path == "/leave":
            return httpx.Response(302, headers={"location": "http://127.0.0.1:3000/x"})
        if path.startswith("/loop"):
            n = int(path[len("/loop") :] or 0)
            return httpx.Response(302, headers={"location": f"/loop{n + 1}"})
        return httpx.Response(200, content=b"ok")

    env = _UrlEnv(monkeypatch, upstream)
    _self_env(monkeypatch, dns={"intra.test": ["10.5.5.5"]})
    to_self = env.run(url="http://10.1.2.3/leave")
    assert "転送先が このサーバ自身" in to_self and "302" in to_self
    assert env.gets == ["http://10.1.2.3/leave"]
    loop = env.run(url="http://10.1.2.3/loop")
    assert "4 回以上続いた" in loop and "[ラベル](http://10.1.2.3/loop)" in loop
    assert env.gets.count("http://10.1.2.3/loop") == 1 and len(env.gets) == 1 + 4
    unresolved = env.run(url="http://nosuch.test/x")
    assert (
        "ホスト名を解決できない" in unresolved
        and "IP アドレスの URL が分かっているならそれで呼び直す" in unresolved
    )
    named = env.run(url="http://intra.test/x")
    assert not named.startswith("ERROR")
    assert (
        env.gets[-1] == "http://10.5.5.5/x"
        and env.sent_headers[-1]["host"] == "intra.test"
    )
    assert env.writes[-1]["payload"]["path"].endswith("imports/x")


def _set_sandbox_limit_mb(monkeypatch, mb):
    """sandbox の上限 (`/healthz` の file_max_size_mb を読めた値) を差し替える。url= はこれに従う。"""
    from nucllei.utils import file_limit

    monkeypatch.setattr(file_limit, "_sandbox_limit", mb * 1024 * 1024)
    monkeypatch.setattr(file_limit, "_warned", None)


def _set_limit_mb(monkeypatch, mb):
    """RC `ATTACHMENT_MAX_SIZE_MB` (ファイルの出入りの唯一の上限。0 = 無制限) を差し替える。
    app.state.config はこの RuntimeConfig をそのまま包むので、管理画面の変更と同じ効き方。"""
    from nucllei import config

    monkeypatch.setattr(config.ATTACHMENT_MAX_SIZE_MB, "value", mb)


def test_too_large_reports_actual_size_and_limit(monkeypatch):
    big = b"x" * (3 * 1024 * 1024)
    env = _UrlEnv(
        monkeypatch,
        lambda r: httpx.Response(
            200, content=big, headers={"content-length": str(len(big))}
        ),
    )
    _self_env(monkeypatch)
    _set_sandbox_limit_mb(monkeypatch, 1)
    result = env.run(url=f"{INTRA}/big.bin")
    assert result.startswith("ERROR")
    # 実サイズ(Content-Length)と上限(sandbox の上限)の両方。変えるのは配備。リンク切れではないと言う
    assert "3.0MB" in result and "上限 1.0MB" in result
    assert "sandbox の配備の設定" in result and "最大アップロードサイズ" not in result
    assert "少なくとも" not in result
    assert "リンク切れではない" in result
    assert env.writes == []


def test_too_large_without_content_length_says_at_least(monkeypatch):
    async def chunks():
        for _ in range(40):
            yield b"x" * 65536

    env = _UrlEnv(monkeypatch, lambda r: httpx.Response(200, content=chunks()))
    _self_env(monkeypatch)
    _set_sandbox_limit_mb(monkeypatch, 1)
    result = env.run(url=f"{INTRA}/stream.bin")
    assert result.startswith("ERROR")
    assert "少なくとも" in result and "実サイズは不明" in result
    assert env.writes == []


def test_sandbox_limit_is_the_resolver_limit(monkeypatch):
    # url= は file_id を作らない経路 — 上限は sandbox の上限 (SANDBOX_FILE_MAX_SIZE_MB。/healthz から
    # 読めていなければ既定 1024 MB)。添付の上限 (RC ATTACHMENT_MAX_SIZE_MB) には従わない。
    # 中身は一時ファイル (sink) に流す
    seen = {}

    async def fake_resolve(url, *, max_bytes=None, user=None, sink=None):
        from nucllei.utils.image_refs import RefError

        seen.update(url=url, max_bytes=max_bytes, sink=sink is not None)
        return None, RefError("not_found", "x")

    from nucllei.utils import file_limit, image_refs

    # url 引数の入口は resolve_url(保存済み文字列用の resolve_ref は通さない)
    monkeypatch.setattr(image_refs, "resolve_url", fake_resolve)
    monkeypatch.setattr(file_limit, "_sandbox_limit", None)
    env = _UrlEnv(monkeypatch)
    _set_limit_mb(monkeypatch, 3)
    env.run(url="https://llens.test/ext/cage/a.bin")
    assert seen == {
        "url": "https://llens.test/ext/cage/a.bin",
        "max_bytes": 1024 * 1024 * 1024,
        "sink": True,
    }
    _set_sandbox_limit_mb(monkeypatch, 100)
    env.run(url="https://llens.test/ext/cage/a.bin")
    assert seen["max_bytes"] == 100 * 1024 * 1024


def test_url_import_streams_through_a_temp_file(monkeypatch):
    # 上流の本文は resolver が一時ファイルに書き (メモリに貯めない)、sandbox の /files/raw へ
    # チャンクで送る (Content-Length 付き、base64-in-JSON の /files/write は使わない)
    async def chunks():
        for i in range(8):
            yield bytes([i]) * 300_000

    env = _UrlEnv(monkeypatch, lambda r: httpx.Response(200, content=chunks()))
    _self_env(monkeypatch)
    from nucllei.utils import image_refs

    real = image_refs._read_response
    seen = {}

    async def spy(response, label, filename, limit, sink=None):
        loaded, err = await real(response, label, filename, limit, sink)
        seen.update(sink=sink is not None, kept=len(loaded.data), size=loaded.size)
        return loaded, err

    monkeypatch.setattr(image_refs, "_read_response", spy)
    result = env.run(url=f"{INTRA}/big.bin")
    assert not result.startswith("ERROR"), result
    # 手元に残るのは型の判定に使う先頭だけ
    assert seen == {"sink": True, "kept": 64, "size": 2_400_000}
    [w] = env.writes
    assert w["path"] == "/files/raw"
    assert w["payload"]["path"] == "imports/big.bin"
    assert w["payload"]["content_length"] == 2_400_000
    assert base64.b64decode(w["payload"]["b64"]) == b"".join(
        bytes([i]) * 300_000 for i in range(8)
    )
    # 添付の上限は渡さない (sandbox は自分の上限で見る)
    assert "file_limit_bytes" not in w["payload"]["params"]


def test_url_import_sandbox_errors(monkeypatch):
    from nucllei.builtin_tools.sandbox import SandboxHTTPError

    env = _UrlEnv(monkeypatch, lambda r: httpx.Response(200, content=b"data"))
    _self_env(monkeypatch)
    env.raw_error = SandboxHTTPError(
        413, '{"detail":"file too large: 4 bytes (limit 3)"}'
    )
    out = env.run(url=f"{INTRA}/a.bin")
    assert out.startswith("ERROR") and "取り込みの上限" in out
    assert "sandbox の配備の設定" in out
    env.raw_error = SandboxHTTPError(507, "workspace quota exceeded")
    out = env.run(url=f"{INTRA}/a.bin")
    assert out.startswith("ERROR") and "容量 (quota) が一杯" in out


def test_valve_url_max_mb_is_gone():
    # 上限は RC の 1 つ — valve には無い(DB の保存値は merge_valves_update が落とす)
    from nucllei.builtin_tools.sandbox import Tools
    from nucllei.utils.valves import merge_valves_update

    assert "url_max_mb" not in Tools.Valves.model_fields
    assert merge_valves_update({"url_max_mb": 50, "debug": True}, {}, Tools.Valves) == {
        "debug": True
    }


def test_failure_reasons_are_told_apart(monkeypatch):
    cases = [
        (httpx.Response(404), ("存在しない", "リンク切れ")),
        # Location の無い 3xx は辿れない redirect
        (httpx.Response(302), ("redirect", "302")),
        (httpx.Response(401), ("上流が 401",)),
        (httpx.Response(503), ("上流が 503",)),
    ]
    for response, expected in cases:
        env = _UrlEnv(monkeypatch, lambda r, response=response: response)
        _self_env(monkeypatch)
        result = env.run(url=f"{INTRA}/a.png")
        assert result.startswith("ERROR"), response
        for word in expected:
            assert word in result, (response, result)
        if response.status_code != 404:
            assert "リンク切れ" not in result, result
        assert len(env.gets) == 1
        assert env.writes == []


def test_unreachable_upstream(monkeypatch):
    def boom(request):
        raise httpx.ConnectError("refused")

    env = _UrlEnv(monkeypatch, boom)
    _self_env(monkeypatch)
    result = env.run(url=f"{INTRA}/a.png")
    assert result.startswith("ERROR") and "届かない" in result
    assert "リンク切れ" not in result


def test_traversal_and_malformed_url(monkeypatch, tmp_path):
    env = _UrlEnv(monkeypatch)
    env.mount_root(_cage_mount(tmp_path))
    result = env.run(url="https://llens.test/ext/cage/../secret")
    assert result.startswith("ERROR") and "範囲の外" in result
    result = env.run(url="http://[::1/ext/cage/a.png")
    assert result.startswith("ERROR")
    assert env.gets == [] and env.writes == []


# --- sandbox から既に見えている物はコピーしない --------------------------------


def _cage_mount(tmp_path):
    """tmp の木を `EXT_MOUNTS` の静的 mount に(= /ext の配信元 = sandbox の /ext と同じ組)。"""
    root = tmp_path / "data" / "ext"
    (root / "cage" / "static" / "図").mkdir(parents=True)
    (root / "cage" / "static" / "図" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\nx")
    return root


def test_mounted_file_returns_path_without_copy(monkeypatch, tmp_path):
    """`/ext/{rest}` の実体 = 静的 mount の `{rest}` = sandbox の `/ext/{rest}` と同じ物(#138)。
    URL → sandbox のパス(同じ文字列)の橋渡しを tool がし、コピーは作らない。"""
    root = _cage_mount(tmp_path)
    env = _UrlEnv(monkeypatch)
    env.mount_root(root)
    # サイズ上限は掛からない(コピーしないので)
    _set_limit_mb(monkeypatch, 0.000001)
    result = env.run(url="https://llens.test/ext/cage/static/%E5%9B%B3/a.png")
    assert not result.startswith("ERROR")
    assert "取り込み不要" in result
    assert "/ext/cage/static/図/a.png" in result and str(root) not in result
    assert "run_python / run_shell" in result
    assert env.writes == [] and env.gets == []


def test_any_mounted_path_is_bridged(monkeypatch, tmp_path):
    # 静的 mount に見えている物は全部(cage に限らない)。fragment も query も付いてよい
    root = _cage_mount(tmp_path)
    (root / "shared").mkdir()
    (root / "shared" / "s.pdf").write_bytes(b"%PDF-1")
    env = _UrlEnv(monkeypatch)
    env.mount_root(root)
    result = env.run(url="http://any.host/ext/shared/s.pdf?v=1#page=1")
    assert "取り込み不要" in result and "/ext/shared/s.pdf" in result
    assert env.writes == [] and env.gets == []


def test_missing_mounted_file_is_not_found(monkeypatch, tmp_path):
    # 無いファイルを「既に見えている」と言わない
    root = _cage_mount(tmp_path)
    env = _UrlEnv(monkeypatch)
    env.mount_root(root)
    result = env.run(url="https://llens.test/ext/cage/static/nope.png")
    assert result.startswith("ERROR") and "存在しない" in result
    assert env.writes == []


def test_symlink_escaping_mount_is_not_visible(monkeypatch, tmp_path):
    # 静的 mount の外を指す symlink — パスも返さず、読みもしない
    root = _cage_mount(tmp_path)
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"x")
    (root / "cage" / "static" / "escape.png").symlink_to(outside)
    env = _UrlEnv(monkeypatch)
    env.mount_root(root)
    result = env.run(url="https://llens.test/ext/cage/static/escape.png")
    assert result.startswith("ERROR") and "取り込み不要" not in result
    assert env.writes == []


# --- 同一サーバの相対 URL --------------------------------------------------------


def test_relative_ext_url_under_mount(monkeypatch, tmp_path):
    # 相対 `/ext/{rest}` は「このサーバの URL」= 絶対形と同じ 静的 mount で解決する。
    # 解決先が 静的 mount 配下の実ファイル → コピーせずパスを返す(値の形ではなく解決結果)
    root = _cage_mount(tmp_path)
    env = _UrlEnv(monkeypatch)
    env.mount_root(root)
    result = env.run(url="/ext/cage/static/%E5%9B%B3/a.png")
    assert "取り込み不要" in result and "/ext/cage/static/図/a.png" in result
    assert env.writes == [] and env.gets == []


def test_relative_ext_url_stays_closed(monkeypatch, tmp_path):
    root = _cage_mount(tmp_path)
    env = _UrlEnv(monkeypatch)
    env.mount_root(root)
    result = env.run(url="/ext/nosuch/a.png")
    assert result.startswith("ERROR") and "存在しない" in result
    result = env.run(url="/ext/cage/static/nope.png")
    assert result.startswith("ERROR") and "存在しない" in result
    result = env.run(url="/ext/cage/../secret")
    assert result.startswith("ERROR") and "範囲の外" in result
    assert env.gets == [] and env.writes == []


def test_files_content_url_is_routed_to_the_file(monkeypatch, tmp_path):
    """`/api/v1/files/{id}/content`(このサーバのファイル配信口。相対、または絶対 = host
    不問)は URL のルーティングとして file に当たる。HTTP では取りに行かない = 所有者
    チェックは file_id 経路のものがそのまま効く。"""
    for given in (
        f"/api/v1/files/{FILE_UUID}/content",
        f"https://llens.test/api/v1/files/{FILE_UUID}/content",
        # fragment は `#page=N` 以外も無視して同じ file に当たる(file_id_of_url の規則)
        f"/api/v1/files/{FILE_UUID}/content#page=2",
        f"/api/v1/files/{FILE_UUID}/content#section",
    ):
        env = _UrlEnv(monkeypatch)
        env.files(tmp_path, {FILE_UUID: ("u1", "report.xlsx", b"xlsx")})
        result = env.run(url=given)
        assert "→ /workspace/imports/report.xlsx" in result, given
        assert env.writes[0]["payload"]["path"] == "imports/report.xlsx"
        assert env.gets == []

    env = _UrlEnv(monkeypatch)
    env.files(tmp_path, {FILE_UUID: ("someone-else", "secret.xlsx", b"x")})
    given = f"/api/v1/files/{FILE_UUID}/content"
    result = env.run(url=given)
    # url= 経由は形の誤りではないので、file_id の固定文は添えない (確かめ方・伝え方は同じ)
    assert result == f"ERROR: {given} {NO_FILE}。次の手: {NO_FILE_NEXT}"
    assert env.writes == []


# --- 引数は名前で解釈が決まる(値の形を見て別の引数に読み替えない)--------------


def test_url_is_only_ever_resolved_as_a_url(monkeypatch, tmp_path):
    """url= に URL でない値(UUID / sandbox のパス / サーバに無い経路 / スキーム無し)が
    来ても、file_id や path として救済しない。エラーは値によらず同じ文 + 固定の案内
    (行き先の規則はスキームの無い値 = 取り違えらしいときだけ。llens staging 2026-10-04)。"""
    env = _UrlEnv(monkeypatch)
    env.files(tmp_path, {FILE_UUID: ("u1", "report.xlsx", b"xlsx")})
    results = {}
    for bad in (
        FILE_UUID,
        "/workspace/x",
        "/mnt/cage/x.pdf",
        f"/api/v1/files/{FILE_UUID}",
        "/etc/passwd",
        "ext/cage/x.png",
        "his-info.test/docs/a.pdf",
    ):
        result = env.run(url=bad)
        # 全 tool 共通の形: 値 + 固定文(url が受ける形)+ 行き先の規則(1 文)
        assert result == (
            f"ERROR: {bad} は URL として解決できない。次の手: {URL_ARG_FORM} {REF_ARGS_RULE}"
        ), bad
        results[bad] = result.replace(bad, "<value>")
    # 値を調べて行き先を出し分けていない
    assert len(set(results.values())) == 1
    # スキーム付き (URL の種類で形が崩れているだけ) には行き先の規則を付けない
    bad = "ftp://10.0.0.5/a.pdf"
    assert env.run(url=bad) == (
        f"ERROR: {bad} は URL として解決できない。次の手: {URL_ARG_FORM}"
    )
    assert env.gets == [] and env.writes == []


def test_file_id_is_only_ever_a_file_id(monkeypatch, tmp_path):
    """file_id= に URL や sandbox のパスが来ても url / path として処理しない
    (レコードが無い、で答える)。"""
    env = _UrlEnv(monkeypatch)
    env.files(tmp_path, {FILE_UUID: ("u1", "report.xlsx", b"xlsx")})
    for bad in (
        "https://llens.test/ext/cage/static/a.pdf",
        "/ext/cage/static/a.pdf",
        f"https://llens.test/api/v1/files/{FILE_UUID}/content",
        f"/api/v1/files/{FILE_UUID}/content",
        "/workspace/x",
    ):
        result = env.run(file_id=bad)
        # UUID の形でない (取り違えらしい) ので、受ける形と行き先の規則も添える
        assert result == (
            f"ERROR: file_id {bad} {NO_FILE}。"
            f"次の手: {FILE_ID_ARG_FORM} {REF_ARGS_RULE} {NO_FILE_NEXT}"
        ), bad
    assert env.gets == [] and env.writes == []
    # 正しい file_id は従来どおり
    result = env.run(file_id=FILE_UUID)
    assert "→ /workspace/imports/report.xlsx" in result


def test_no_value_sniffing_helpers_left():
    from nucllei.builtin_tools import sandbox as mod

    for gone in (
        "_classify_source",
        "_already_visible",
        "_URL_ACCEPTS",
        "_ERR_RELATIVE_EXT",
        "_UUID_PATTERN",
        "_FILES_URL_PATH_PATTERN",
    ):
        assert not hasattr(mod, gone), gone


# =============================================================================
# fetch_from_workspace(path) の /ext/…(sandbox のパス → 利用者に見せられる形)
#
# send_to_workspace(url=) の「URL → sandbox で開けるパス」の逆向き。値の形ではなく
# 解決結果で分かれる: 実体が nucllei 側の 静的 mount にも実在すれば(= /ext で配信されている)URL を
# 返し(コピーも添付もしない)、無ければ添付もできない(読めない)。2026-10-03 までの
# EXT_FILE_SCOPES(配信範囲の外は添付もしない)は設定ごと撤去。
# =============================================================================


class _MountEnv(_UrlEnv):
    """_UrlEnv + tmp の RO root + Files 登録の記録(DB / Storage は触らない)。"""

    def __init__(self, monkeypatch, tmp_path):
        super().__init__(monkeypatch)
        from nucllei.builtin_tools import sandbox as mod

        self.root = _cage_mount(tmp_path)
        (self.root / "cage" / "static" / "表 1.csv").write_text("a,b\n1,2\n")
        (self.root / "cage" / "doc").mkdir()
        (self.root / "cage" / "doc" / "本文.md").write_text("# x")
        (self.root / "shared").mkdir()
        (self.root / "shared" / "案内 図.png").write_bytes(b"\x89PNG\r\n\x1a\nshared")
        (self.root / "shared" / "memo.txt").write_bytes(b"hello")
        outside = tmp_path / "outside.txt"
        outside.write_text("secret")
        (self.root / "shared" / "escape.txt").symlink_to(outside)
        self.mount_root(self.root)
        # tool 側の「/ext 配下か」は jail 側の形(`/ext/…`)で見る — 差し替え不要

        self.registered: list[tuple] = []
        self.displayed: list[str] = []

        def fake_register(user_id, name, data, source=None):
            self.registered.append((user_id, name, data))
            content_type = mod.mimetypes.guess_type(name)[0] or "x/y"
            return mod._attachment_dict("fid-1", name, content_type, len(data))

        monkeypatch.setattr(mod, "_register_bytes_file", fake_register)
        monkeypatch.setattr(mod, "display", self.displayed.append)
        self.emitted: list[dict] = []

    def fetch(self, path):
        async def emit(event):
            self.emitted.append(event)

        return _arun(
            self.tools.fetch_from_workspace(
                path, __user__={"id": "u1"}, __event_emitter__=emit
            )
        )


def test_fetch_mounted_file_served_by_ext_returns_url(monkeypatch, tmp_path):
    env = _MountEnv(monkeypatch, tmp_path)

    out = env.fetch("/ext/cage/static/図/a.png")
    url = "/ext/cage/static/%E5%9B%B3/a.png"
    assert not out.startswith("ERROR") and "添付不要" in out
    # 画像は画像記法で、URL は相対のまま
    assert f"![a.png]({url})" in out
    # Files には何も登録しない・添付しない・sandbox も叩かない
    assert env.registered == [] and env.emitted == [] and env.displayed == []
    assert env.writes == []

    # 画像以外はリンク。空白を含む名前も markdown のリンク先に置ける形
    out = env.fetch("/ext/cage/static/表 1.csv")
    assert "[表 1.csv](/ext/cage/static/%E8%A1%A8%201.csv)" in out
    # cage に限らず、静的 mount に見えている物はゲートが配信している = URL を返す
    out = env.fetch("/ext/cage/doc/本文.md")
    assert "(/ext/cage/doc/%E6%9C%AC%E6%96%87.md)" in out
    out = env.fetch("/ext/shared/案内 図.png")
    assert "![案内 図.png](/ext/shared/%E6%A1%88%E5%86%85%20%E5%9B%B3.png)" in out
    out = env.fetch("/ext/shared/memo.txt")
    assert "[memo.txt](/ext/shared/memo.txt)" in out
    assert env.registered == [] and env.emitted == [] and env.displayed == []

    # 返した URL は send_to_workspace(url=) に渡すと同じパスに戻る(往復)
    back = env.run(url=url)
    assert "取り込み不要" in back and "/ext/cage/static/図/a.png" in back


def test_fetch_mounted_file_not_in_nucllei_mount_is_unreadable(monkeypatch, tmp_path):
    """nucllei 側の 静的 mount に無い物(sandbox にだけ bind した置き場)は URL も無く、読めも
    しない — 配備は配信したくない物を nucllei 側に置かないことで渡さない。添付の経路
    (`_attach`)は 静的 mount にあって URL にできない物のためだけに残る(実質 resolver が
    not_found で断る)。"""
    env = _MountEnv(monkeypatch, tmp_path)
    for missing in (
        "/ext/shared/nope.png",
        "/ext/elsewhere/x.txt",
    ):
        out = env.fetch(missing)
        # 本当に無いか、配備の mount のずれ (sandbox にだけ mount)。「渡さない」とも
        # 「/workspace にコピーして渡す」とも言わない (ken 2026-10-04: /ext は URL で見せる
        # だけ — 同じ組を nucllei と sandbox に mount する)
        assert out == (
            f"ERROR: {missing} はこのサーバの /ext に無い (存在しないか、sandbox にだけ"
            " mount されている)。次の手: 中身が要るなら sandbox で読んで使う。"
        ), missing
        assert "渡さない" not in out and "コピー" not in out, missing
        # URL(markdown のリンク / 画像記法)も file_id も出ない(パスの echo は出る)
        assert "file_id=" not in out and "](/ext/" not in out, missing
        assert "添付不要" not in out, missing
    assert env.registered == [] and env.emitted == [] and env.displayed == []
    # sandbox の /files/read は使わない(nucllei 側の同じ置き場を見る)
    assert env.writes == []


def test_fetch_mounted_file_failures_are_told_apart(monkeypatch, tmp_path):
    env = _MountEnv(monkeypatch, tmp_path)

    # 不在 / ディレクトリ / mount の外へ出る symlink → 読まない(同じ文面 = fail closed)
    for bad in (
        "/ext/shared/nope.txt",
        "/ext/shared",
        "/ext",
        "/ext/shared/escape.txt",
        "/ext/cage/static/nope.png",
        # path は fs のパスとして読む(URL と同じ文字列でも percent-decode しない)
        "/ext/cage/static/%E5%9B%B3/a.png",
    ):
        out = env.fetch(bad)
        # `/ext` 自体は sandbox のパスの形 (`/ext/…`) でない — 理由を付けない「読めない」
        want = "を読めない。" if bad == "/ext" else "はこのサーバの /ext に無い"
        assert out.startswith("ERROR:") and want in out, bad
        assert "大きすぎ" not in out and "上限" not in out, bad

    # `..` は畳んでから解く。mount の外へ出れば sandbox のパスの形でない
    out = env.fetch("/ext/shared/../../outside.txt")
    assert out.startswith("ERROR:") and out.endswith(f"次の手: {PATH_ARG_FORM}")
    # `..` を畳めば /ext の中 = 配信されている(URL)
    out = env.fetch("/ext/shared/../shared/memo.txt")
    assert "添付不要" in out and "(/ext/shared/memo.txt)" in out

    # 配信されている物は読まないので上限に掛からない
    monkeypatch.setattr("nucllei.utils.image_refs.FETCH_MAX_BYTES", 3)
    env.emitted.clear()
    assert "添付不要" in env.fetch("/ext/cage/static/図/a.png")
    assert "添付不要" in env.fetch("/ext/shared/memo.txt")
    assert env.registered == [] and env.emitted == []


def test_no_withheld_concept_left():
    """2026-10-03: 配信範囲(EXT_FILE_SCOPES)と「利用者に配信しない範囲」の断りは撤去。"""
    import inspect

    from nucllei.builtin_tools import sandbox as mod
    from nucllei.utils import image_refs

    assert not hasattr(image_refs, "mounted_path_withheld")
    assert "利用者に配信しない範囲" not in inspect.getsource(mod)


def test_fetch_path_is_only_ever_a_sandbox_path(monkeypatch, tmp_path):
    """path に URL / file_id が来ても読み替えない — sandbox のパスとして解いて失敗する
    (`/ext/…` は sandbox のパスでもあるので別: 上の told_apart の percent-encoded の例)。"""
    env = _MountEnv(monkeypatch, tmp_path)
    for given, rule in (
        ("https://llens.test/ext/cage/static/%E5%9B%B3/a.png", f" {REF_ARGS_RULE}"),
        (f"/api/v1/files/{FILE_UUID}/content", f" {REF_ARGS_RULE}"),
        (FILE_UUID, f" {REF_ARGS_RULE}"),
        # 2026-10-03 までの置き場。もう特別扱いしない (パスの形の崩れ = 規則は付けない)
        ("/mnt/cage/static/図/a.png", ""),
    ):
        out = env.fetch(given)
        assert out == (
            f"ERROR: {given} は sandbox のパスの形でない。次の手: {PATH_ARG_FORM}{rule}"
        ), given
    assert env.registered == [] and env.writes == []


def test_fetch_from_workspace_spec_mentions_both_outcomes():
    """モデルに見える説明(docstring の :param より前)と引数。引数は path だけ。
    2026-10-19: ユーザーへ渡す経路はこれ一本 (exports/ の自動添付は廃止)。`/ext/…` は
    パスがそのまま URL なので呼ばずにリンクを書いてよい (呼べば配信されているリンクを返す)。"""
    from nucllei.builtin_tools.sandbox import Tools
    from nucllei.utils.tools import get_tool_specs

    description = Tools.fetch_from_workspace.__doc__.split(":param")[0]
    assert "ユーザーに渡すときに使う" in description and "添付" in description
    assert (
        "/ext/" in description
        and "呼ばずにそのパスで本文にリンクを書いてよい" in description
    )
    assert "exports" not in description
    spec = next(
        s for s in get_tool_specs(Tools()) if s["name"] == "fetch_from_workspace"
    )
    assert list(spec["parameters"]["properties"]) == ["path"]
    assert "/ext/" in spec["parameters"]["properties"]["path"]["description"]
    # 橋渡しで返る URL は percent-encode される(本文の URL と別表記になりうる)
    assert "別表記" in description


def test_reference_params_are_described_in_fixed_words():
    """モデルに見える spec: 参照の引数(url / path / file_id)の説明が、どの builtin でも
    同じ言葉(固定文の定数と同じ形の列挙)で、1 行に収まって spec に入っている
    (spec ビルダは :param の継続行を落とす)。"""
    from nucllei.builtin_tools.attachment_text import Tools as AttachmentTools
    from nucllei.builtin_tools.sandbox import Tools
    from nucllei.utils.tools import get_tool_specs

    def described(tools, name, arg):
        spec = next(s for s in get_tool_specs(tools) if s["name"] == name)
        return spec["parameters"]["properties"][arg]["description"]

    url_form = (
        "スキーム付きの絶対 URL (`http(s)://…`) か、同一サーバの `/ext/…`・"
        "`/api/v1/files/{id}/content`。"
    )
    workspace_form = "sandbox の絶対パス (`/workspace/…`)。"
    path_form = "sandbox の絶対パス (`/workspace/…` `/ext/…`)。"
    file_id_form = "添付・生成物の id"

    sandbox = Tools()
    assert url_form in described(sandbox, "send_to_workspace", "url")
    assert file_id_form in described(sandbox, "send_to_workspace", "file_id")
    assert workspace_form in described(sandbox, "write_file", "path")
    assert workspace_form in described(sandbox, "list_workspace", "path")
    assert workspace_form in described(sandbox, "read_file", "path")
    assert path_form in described(sandbox, "fetch_from_workspace", "path")
    assert file_id_form in described(AttachmentTools(), "read_attachment", "file_id")
    # 「相対も通る」は消えている
    for name in ("write_file", "read_file", "list_workspace", "fetch_from_workspace"):
        assert "相対" not in described(sandbox, name, "path"), name
