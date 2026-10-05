"""get_tools のプラグイン機構の回帰(upstream リリースノート照合 2026-10-05)。

- __user__ は toolkit ごとに束縛され、UserValves は注入しない(撤去済み — 他 toolkit /
  filter / pipe の分が混ざっていた)
- 関数名の衝突は先勝ち(tool_ids 順)で、残した側・捨てた側をログに出す
- tool server(server:N)も実行時に一覧と同じ read ACL を確認する
- OpenAPI tool server: 任意の requestBody は省ける / path・query 引数を body に重ねない
"""

import asyncio
import logging
import types

from test.util.abstract_integration_test import AbstractPostgresTest

UV_A = '''
from pydantic import BaseModel


class Tools:
    class UserValves(BaseModel):
        secret: str = "A-default"

    async def tool_a(self, __user__=None) -> str:
        """A"""
        return repr(__user__.get("valves"))
'''

UV_B = '''
from pydantic import BaseModel


class Tools:
    class UserValves(BaseModel):
        secret: str = "B-default"

    async def tool_b(self, __user__=None) -> str:
        """B"""
        return repr(__user__.get("valves"))
'''

UV_C = '''
class Tools:
    async def tool_c(self, __user__=None, __id__=None) -> str:
        """C (UserValves 無し)"""
        return repr((__user__.get("valves"), __id__))
'''

DUP_1 = '''
class Tools:
    async def same(self) -> str:
        """first"""
        return "dup_1"
'''

DUP_2 = '''
class Tools:
    async def same(self) -> str:
        """second"""
        return "dup_2"
'''


def _request(connections=(), servers=()):
    return types.SimpleNamespace(
        app=types.SimpleNamespace(
            state=types.SimpleNamespace(
                TOOLS={},
                TOOL_SERVERS=list(servers),
                config=types.SimpleNamespace(TOOL_SERVER_CONNECTIONS=list(connections)),
            )
        ),
        state=types.SimpleNamespace(),
    )


def _user(uid="u1", role="user"):
    return types.SimpleNamespace(id=uid, role=role)


class TestGetToolsIsolation(AbstractPostgresTest):
    BASE_PATH = "/api/v1/tools"

    def _insert(self, tool_id, content):
        from nucllei.models.tools import ToolForm, Tools
        from nucllei.utils.tools import get_tool_specs

        namespace: dict = {}
        exec(content, namespace)
        Tools.insert_new_tool(
            user_id="u1",
            form_data=ToolForm(id=tool_id, name=tool_id, content=content),
            specs=get_tool_specs(namespace["Tools"]()),
        )

    def test_user_is_bound_per_toolkit_without_valves(self):
        """toolkit ごとに別の __user__ を束縛し、UserValves は注入しない(撤去済み)。

        旧実装は呼び手の __user__ に UserValves を書き込み、partial がその参照を
        持ったままだったため、全 toolkit が最後に読んだ toolkit(ここでは B)の
        UserValves を見ていた。pipe / filter 由来の "valves" キーも落とす。
        """
        from nucllei.utils.tools import get_tools

        for tid, src in (("uv_a", UV_A), ("uv_b", UV_B), ("uv_c", UV_C)):
            self._insert(tid, src)

        shared_user = {"id": "u1", "valves": "from-pipe"}
        extra = {"__user__": shared_user}
        loaded = get_tools(_request(), ["uv_a", "uv_b", "uv_c"], _user(), extra)

        async def call(name):
            return await loaded[name]["callable"]()

        assert asyncio.run(call("tool_a")) == "None"
        assert asyncio.run(call("tool_b")) == "None"
        assert asyncio.run(call("tool_c")) == "(None, 'uv_c')"
        # 呼び手の dict / extra_params は書き換えない
        assert shared_user == {"id": "u1", "valves": "from-pipe"}
        assert "__id__" not in extra

    def test_name_collision_first_wins_and_is_logged(self, caplog):
        from nucllei.utils.tools import get_tools

        self._insert("dup_1", DUP_1)
        self._insert("dup_2", DUP_2)

        for order, winner, loser in (
            (["dup_1", "dup_2"], "dup_1", "dup_2"),
            (["dup_2", "dup_1"], "dup_2", "dup_1"),
        ):
            caplog.clear()
            with caplog.at_level(logging.WARNING, logger="nucllei.utils.tools"):
                loaded = get_tools(_request(), order, _user(), {"__user__": {}})
            assert loaded["same"]["tool_id"] == winner
            assert f"keeping {winner}.same" in caplog.text
            assert f"discarding {loser}.same" in caplog.text

        # 同じ id の重複指定は自分自身との衝突にしない
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="nucllei.utils.tools"):
            loaded = get_tools(_request(), ["dup_1", "dup_1"], _user(), {})
        assert loaded["same"]["tool_id"] == "dup_1"
        assert "collision" not in caplog.text

    def test_tool_server_execution_enforces_read_acl(self):
        """server:N も一覧(GET /tools)と同じ read ACL で実行可否を決める。"""
        from nucllei.utils.tools import get_tools

        spec = {
            "name": "srv_op",
            "description": "d",
            "parameters": {"type": "object", "properties": {}, "required": []},
        }
        connections = [
            # 0: u-ok だけ read 可
            {
                "url": "http://srv0",
                "config": {"access_control": {"read": {"user_ids": ["u-ok"]}}},
            },
            # 1: access_control=None(全員)
            {"url": "http://srv1", "config": {"access_control": None}},
        ]
        servers = [
            {"idx": 0, "url": "http://srv0", "openapi": {}, "specs": [spec]},
            {"idx": 1, "url": "http://srv1", "openapi": {}, "specs": [spec]},
        ]
        request = _request(connections, servers)

        assert get_tools(request, ["server:0"], _user("u-ng"), {}) == {}
        assert "srv_op" in get_tools(request, ["server:0"], _user("u-ok"), {})
        assert "srv_op" in get_tools(request, ["server:0"], _user("adm", "admin"), {})
        assert "srv_op" in get_tools(request, ["server:1"], _user("u-ng"), {})
        # サーバー由来の id(skill)は呼び手が認可済み
        assert "srv_op" in get_tools(
            request, ["server:0"], _user("u-ng"), {}, enforce_access_control=False
        )
        # 範囲外・未取得の idx はターンを落とさずスキップ
        assert get_tools(request, ["server:9", "server:x"], _user("u-ok"), {}) == {}


class _FakeResponse:
    status = 200

    async def json(self):
        return {"ok": True}

    async def text(self):
        return ""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeSession:
    calls: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def __getattr__(self, method):
        def request(url, **kwargs):
            _FakeSession.calls.append((method, url, kwargs))
            return _FakeResponse()

        return request


def _openapi(required_body):
    body = {
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {"note": {"type": "string"}},
                }
            }
        }
    }
    if required_body is not None:
        body["required"] = required_body
    return {
        "paths": {
            "/items/{item_id}": {
                "post": {
                    "operationId": "touch",
                    "parameters": [
                        {"name": "item_id", "in": "path", "required": True},
                        {"name": "dry", "in": "query"},
                    ],
                    "requestBody": body,
                }
            }
        }
    }


def _execute(monkeypatch, openapi, params):
    from nucllei.utils import tools as tools_module

    _FakeSession.calls = []
    monkeypatch.setattr(tools_module.aiohttp, "ClientSession", _FakeSession)
    result = asyncio.run(
        tools_module.execute_tool_server(
            token="",
            url="http://srv",
            name="touch",
            params=params,
            server_data={"openapi": openapi},
        )
    )
    return result, _FakeSession.calls


def test_optional_request_body_can_be_omitted(monkeypatch):
    """任意の requestBody は引数ゼロでも呼べる(旧実装は例外で失敗していた)。"""
    for required in (None, False):
        result, calls = _execute(monkeypatch, _openapi(required), {"item_id": "7"})
        assert result == {"ok": True}
        method, url, kwargs = calls[0]
        assert url == "http://srv/items/7"
        assert kwargs["json"] is None


def test_request_body_excludes_path_and_query(monkeypatch):
    result, calls = _execute(
        monkeypatch,
        _openapi(None),
        {"item_id": "7", "dry": "1", "note": "hi"},
    )
    assert result == {"ok": True}
    method, url, kwargs = calls[0]
    assert url == "http://srv/items/7?dry=1"
    assert kwargs["json"] == {"note": "hi"}


def test_required_empty_body_is_sent_as_object(monkeypatch):
    result, calls = _execute(monkeypatch, _openapi(True), {"item_id": "7"})
    assert result == {"ok": True}
    assert calls[0][2]["json"] == {}
