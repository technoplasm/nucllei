"""1 ファイルの上限は 2 つ(`utils/file_limit` の表、ken 決定)。

固定すること:
- 添付の上限 = RC `ATTACHMENT_MAX_SIZE_MB`。呼ぶたびに RC から読む(管理画面で変えるとすぐ効く)。
  0 / 空 = 無制限 = None
- 添付の上限の経路: send_to_workspace(file_id=)、exports の自動添付、fetch_from_workspace。
  sandbox への該当リクエスト(`/exec` `/exec_cell` `/files/read` `/files/write`)に
  `file_limit_bytes` で渡る。無制限なら渡さない(sandbox の上限だけ)。超過の文は上限の値と
  「管理者の設定 (最大アップロードサイズ)」を言う
- sandbox の上限 = sandbox の `/healthz` の `file_max_size_mb`(読めるまでは既定 1024 MB)。
  feed_image(path=) はこれと FETCH_MAX_BYTES の小さいほう(send_to_workspace(url=) とファイルブラウザ
  は test_builtin_sandbox / test_sandbox_files)
- 添付の上限が sandbox の上限より大きい(無制限を含む)と warning(組が変わったときだけ)
- 改名した env の旧名(SANDBOX_WRITE_FILE_LIMIT_BYTES)は読まず、warning で新名を案内する
"""

import asyncio
import base64
import logging
import types

import pytest

from test.util.tool_errors import call_text

MIB = 1024 * 1024


@pytest.fixture
def limit_mb(monkeypatch):
    """RC を差し替える関数を返す。app.state.config はこの RuntimeConfig を包むだけなので、
    管理画面の変更と同じ効き方。安全上限の記録も毎回まっさらにする。"""
    from nucllei import config
    from nucllei.utils import file_limit

    monkeypatch.setattr(file_limit, "_sandbox_limit", None)
    monkeypatch.setattr(file_limit, "_warned", None)

    def set_mb(value):
        monkeypatch.setattr(config.ATTACHMENT_MAX_SIZE_MB, "value", value)

    return set_mb


def _run(coro):
    return asyncio.run(call_text(coro))


# --- 上限関数 ------------------------------------------------------------------------


def test_limit_reads_rc_each_time(limit_mb):
    from nucllei.utils.file_limit import attachment_limit_bytes

    limit_mb(25)
    assert attachment_limit_bytes() == 25 * MIB
    limit_mb(3)
    assert attachment_limit_bytes(user={"id": "u"}) == 3 * MIB
    for unlimited in (0, None, ""):
        limit_mb(unlimited)
        assert attachment_limit_bytes() is None


def test_limit_reads_app_config_through_request(limit_mb):
    from nucllei.utils.file_limit import attachment_limit_bytes

    cfg = types.SimpleNamespace(ATTACHMENT_MAX_SIZE_MB=7)
    app = types.SimpleNamespace(state=types.SimpleNamespace(config=cfg))
    assert attachment_limit_bytes(types.SimpleNamespace(app=app)) == 7 * MIB
    assert attachment_limit_bytes(app) == 7 * MIB
    # config の無い request(tool のテスト用の偽物等)は module の RC
    limit_mb(2)
    bare = types.SimpleNamespace(
        app=types.SimpleNamespace(state=types.SimpleNamespace())
    )
    assert attachment_limit_bytes(bare) == 2 * MIB


def test_sandbox_fields_and_format(limit_mb):
    from nucllei.utils.file_limit import format_limit, sandbox_limit_fields

    assert sandbox_limit_fields(None) == {}
    assert sandbox_limit_fields(5 * MIB) == {"file_limit_bytes": 5 * MIB}
    assert format_limit(25 * MIB) == "25 MB"
    assert format_limit(None) == "無制限"


def test_warns_when_over_sandbox_limit_once_per_pair(limit_mb, caplog):
    from nucllei.utils import file_limit

    limit_mb(25)
    with caplog.at_level(logging.WARNING, logger="nucllei.utils.file_limit"):
        file_limit.set_sandbox_file_limit(10 * MIB)
        assert caplog.text.count("sandbox の上限が勝つ") == 1
        assert "25 MB" in caplog.text and "10 MB" in caplog.text
        # 同じ組では繰り返さない(毎リクエストで溢れさせない)
        file_limit.sandbox_limit_fields(25 * MIB)
        file_limit.sandbox_limit_fields(25 * MIB)
        assert caplog.text.count("sandbox の上限が勝つ") == 1
        # 無制限も「大きい」
        file_limit.sandbox_limit_fields(None)
        assert caplog.text.count("sandbox の上限が勝つ") == 2
        # sandbox の上限以下なら出さない
        file_limit.sandbox_limit_fields(5 * MIB)
        assert caplog.text.count("sandbox の上限が勝つ") == 2


def test_no_warning_without_safety_or_under_it(limit_mb, caplog):
    from nucllei.utils import file_limit

    limit_mb(25)
    with caplog.at_level(logging.WARNING, logger="nucllei.utils.file_limit"):
        file_limit.sandbox_limit_fields(None)  # sandbox の上限が未取得
        file_limit.set_sandbox_file_limit(1024 * MIB)
    assert "sandbox の上限が勝つ" not in caplog.text


# --- sandbox tool の各経路 ------------------------------------------------------------


class _Env:
    """sandbox tool + 偽 _post(endpoint → 応答 dict か例外)+ 記録。"""

    def __init__(self, monkeypatch, responses=None):
        from nucllei.builtin_tools import sandbox as mod

        self.mod = mod
        self.tools = mod.Tools()
        self.calls: list[tuple[str, dict]] = []
        responses = responses or {}

        async def fake_post(path, payload, timeout_s):
            self.calls.append((path, payload))
            r = responses.get(path, {})
            if isinstance(r, Exception):
                raise r
            return r

        self.tools._post = fake_post
        monkeypatch.setattr(mod, "_ext_mounts", lambda user: [])
        self.registered: list[tuple[str, bytes]] = []

        def fake_register(user_id, name, data):
            self.registered.append((name, data))
            return {
                "id": "fid",
                "file": {"meta": {"content_type": "application/octet-stream"}},
            }

        monkeypatch.setattr(mod, "_register_bytes_file", fake_register)

        async def no_persist(*a, **k):
            return None

        self.tools._persist_and_emit_files = no_persist

    def payload(self, path):
        return [p for (e, p) in self.calls if e == path]


USER = {"id": "u1", "role": "user"}


def test_exec_passes_limit_and_unlimited_omits_it(monkeypatch, limit_mb):
    env = _Env(monkeypatch, {"/exec": {"exit_code": 0, "duration_s": 0.1}})
    limit_mb(4)
    _run(env.tools.run_shell(command="true", __user__=USER))
    assert env.payload("/exec")[-1]["file_limit_bytes"] == 4 * MIB
    # 変えるとすぐ効く
    limit_mb(0)
    _run(env.tools.run_shell(command="true", __user__=USER))
    assert "file_limit_bytes" not in env.payload("/exec")[-1]


def test_exec_cell_passes_limit(monkeypatch, limit_mb):
    env = _Env(monkeypatch, {"/exec_cell": {"stdout": "", "duration_s": 0}})
    limit_mb(6)
    _run(env.tools.run_python(code="1", __user__=USER, __chat_id__="c1"))
    assert env.payload("/exec_cell")[-1]["file_limit_bytes"] == 6 * MIB


def test_exports_over_limit_say_limit_and_admin(monkeypatch, limit_mb):
    res = {
        "exit_code": 0,
        "duration_s": 0.1,
        "exports": [
            {"name": "big.bin", "size": 9 * MIB, "truncated": True, "limit": 4 * MIB},
            {"name": "ok.txt", "size": 2, "b64": base64.b64encode(b"hi").decode()},
        ],
    }
    env = _Env(monkeypatch, {"/exec": res})
    limit_mb(4)
    out = _run(env.tools.run_shell(command="true", __user__=USER))
    assert "big.bin" in out and "添付の上限 4 MB" in out
    assert "最大アップロードサイズ" in out
    assert [n for n, _ in env.registered] == ["ok.txt"]


def test_send_file_id_over_limit_is_not_read(monkeypatch, limit_mb, tmp_path):
    env = _Env(monkeypatch)
    src = tmp_path / "big.bin"
    src.write_bytes(b"x" * (2 * MIB))
    rec = types.SimpleNamespace(user_id="u1", filename="big.bin", path=str(src))
    monkeypatch.setattr(env.mod.Files, "get_file_by_id", staticmethod(lambda fid: rec))
    monkeypatch.setattr(env.mod.Storage, "get_file", staticmethod(lambda p: p))
    limit_mb(1)
    out = _run(env.tools.send_to_workspace(file_id="f1", __user__=USER))
    assert out.startswith("ERROR")
    assert "取り込みの上限 1 MB" in out and "最大アップロードサイズ" in out
    assert env.payload("/files/write") == []
    # 上限を上げるとすぐ通り、sandbox にも上限が渡る
    limit_mb(5)
    out = _run(env.tools.send_to_workspace(file_id="f1", __user__=USER))
    assert not out.startswith("ERROR"), out
    assert env.payload("/files/write")[-1]["file_limit_bytes"] == 5 * MIB


def test_send_file_id_sandbox_413_reports_effective_limit(
    monkeypatch, limit_mb, tmp_path
):
    from nucllei.builtin_tools.sandbox import SandboxHTTPError

    err = SandboxHTTPError(413, '{"detail":"file too large: 2048 bytes (limit 1024)"}')
    env = _Env(monkeypatch, {"/files/write": err})
    src = tmp_path / "a.bin"
    src.write_bytes(b"x" * 2048)
    rec = types.SimpleNamespace(user_id="u1", filename="a.bin", path=str(src))
    monkeypatch.setattr(env.mod.Files, "get_file_by_id", staticmethod(lambda fid: rec))
    monkeypatch.setattr(env.mod.Storage, "get_file", staticmethod(lambda p: p))
    limit_mb(25)
    out = _run(env.tools.send_to_workspace(file_id="f1", __user__=USER))
    assert out.startswith("ERROR") and "取り込みの上限" in out and "2.0KB" in out


def test_fetch_passes_limit_and_413_says_limit(monkeypatch, limit_mb):
    from nucllei.builtin_tools.sandbox import SandboxHTTPError

    ok = {"name": "a.txt", "b64": base64.b64encode(b"hi").decode()}
    env = _Env(monkeypatch, {"/files/read": ok})
    limit_mb(8)
    out = _run(env.tools.fetch_from_workspace(path="/workspace/a.txt", __user__=USER))
    assert not out.startswith("ERROR"), out
    assert env.payload("/files/read")[-1]["file_limit_bytes"] == 8 * MIB

    err = SandboxHTTPError(
        413, f'{{"detail":"file too large: {9 * MIB} bytes (limit {8 * MIB})"}}'
    )
    env = _Env(monkeypatch, {"/files/read": err})
    out = _run(env.tools.fetch_from_workspace(path="/workspace/big.bin", __user__=USER))
    assert out.startswith("ERROR")
    assert "9.0MB" in out and "上限 8 MB" in out and "最大アップロードサイズ" in out
    assert "ファイルは存在する" in out


# --- feed_image(path=) / resolver ------------------------------------------------------


def test_feed_image_path_uses_sandbox_limit_capped_by_fetch(monkeypatch, limit_mb):
    # file_id を作らない (参照を積むだけ) ので添付の上限ではなく sandbox の上限。送信直前に
    # FETCH_MAX_BYTES で読み直すので、その小さいほう
    from nucllei.builtin_tools import feed_image as mod
    from nucllei.env import FETCH_MAX_BYTES
    from nucllei.utils import file_limit
    from nucllei.utils.image_refs import RefError

    seen = {}

    async def fake_path(path, user, *, max_bytes=None):
        seen["max_bytes"] = max_bytes
        return None, RefError("too_large", "x", size=3 * MIB, limit=max_bytes)

    monkeypatch.setattr(mod, "resolve_path", fake_path)
    tools = mod.Tools()

    def call():
        return _run(
            tools.feed_image(
                path="/workspace/a.png", __user__=USER, __feed_image__=lambda r: None
            )
        )

    limit_mb(1)  # 添付の上限は効かない
    out = call()
    assert seen["max_bytes"] == min(1024 * MIB, FETCH_MAX_BYTES)
    assert out.startswith("ERROR") and "sandbox の配備の設定" in out
    assert "最大アップロードサイズ" not in out
    file_limit.set_sandbox_file_limit(2 * MIB)
    call()
    assert seen["max_bytes"] == 2 * MIB


def test_renamed_env_only_warns(caplog):
    from nucllei.utils import file_limit

    with caplog.at_level(logging.WARNING, logger="nucllei.utils.file_limit"):
        assert file_limit.warn_renamed_env({}) == []
        assert "廃止" not in caplog.text
        found = file_limit.warn_renamed_env({"SANDBOX_WRITE_FILE_LIMIT_BYTES": "5"})
    assert found == ["SANDBOX_WRITE_FILE_LIMIT_BYTES"]
    assert (
        "SANDBOX_WRITE_FILE_LIMIT_BYTES は廃止 (読まない)。SANDBOX_TEXT_MAX_SIZE_MB"
        in caplog.text
    )


def test_probe_reads_file_max_size_mb(monkeypatch, limit_mb):
    from aiohttp import web

    from nucllei.builtin_tools import sandbox
    from nucllei.utils import file_limit

    async def main(body):
        app = web.Application()

        async def healthz(request):
            return web.json_response(body)

        app.router.add_get("/healthz", healthz)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        monkeypatch.setattr(sandbox, "SANDBOX_URL", f"http://127.0.0.1:{port}")
        try:
            return await file_limit.probe_sandbox_file_limit(attempts=1)
        finally:
            await runner.cleanup()

    limit_mb(25)
    assert asyncio.run(main({"ok": True, "file_max_size_mb": 3})) == 3 * MIB
    assert file_limit.sandbox_file_limit_bytes() == 3 * MIB
    assert file_limit.sandbox_file_limit_probed() == 3 * MIB
    # キーの無い sandbox は既定のまま
    assert asyncio.run(main({"ok": True})) is None
    assert file_limit.sandbox_file_limit_probed() is None
    assert file_limit.sandbox_file_limit_bytes() == 1024 * MIB


def test_workspace_read_passes_limit_to_sandbox(monkeypatch):
    from nucllei.builtin_tools import sandbox
    from nucllei.utils import image_refs

    sent = {}

    async def fake_post(path, payload, timeout_s):
        sent.update(payload)
        return {"name": "a.png", "b64": base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()}

    monkeypatch.setattr(sandbox, "SANDBOX_URL", "http://sandbox.test")
    monkeypatch.setattr(sandbox, "sandbox_post", fake_post)
    loaded, err = asyncio.run(
        image_refs.resolve_path("/workspace/a.png", "u1", max_bytes=3 * MIB)
    )
    assert err is None and loaded is not None
    assert sent["file_limit_bytes"] == 3 * MIB
