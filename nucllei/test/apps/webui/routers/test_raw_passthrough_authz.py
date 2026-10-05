"""生パススルー(/openai/chat/completions・/ollama/api/chat)の認可の回帰(2026-10-05)。

両ルートは `bypass_filter` をクエリ引数として露出しており、一般ユーザーが
`?bypass_filter=true` を付けるだけで preset の ACL(access_control={} = 非公開)と
admin 専用の生モデル制限を素通りできた(403 → 200、上流へ送信まで到達)。
内部から True で呼ぶ箇所は無かったので、引数ごと撤去した。

続けて同日、生パススルー endpoint 自体を admin 限定にした(filter を通らないので
large_file_gate 等の方針用 filter を迂回できる — TestRawPassthroughAdminOnly)。
TestRawPassthroughAuthz は role ゲートを素通りする mock_webui_user のままにしてあり、
admin 限定の内側に残る preset ACL / 生モデル制限の回帰として持つ。
"""

import inspect

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user

BODY_MESSAGES = [{"role": "user", "content": "hi"}]


class TestRawPassthroughAuthz(AbstractPostgresTest):
    BASE_PATH = "/openai"

    def setup_method(self):
        super().setup_method()
        from nucllei.models.models import ModelForm, ModelMeta, ModelParams, Models

        Models.insert_new_model(
            ModelForm(
                id="private-preset",
                base_model_id="base",
                name="private-preset",
                meta=ModelMeta(description="x"),
                params=ModelParams(system="【PRESET-SYS】"),
                access_control={},
            ),
            "1",
        )

    def _post(self, path, model, role="user"):
        with mock_webui_user(id="2", role=role):
            return self.fast_api_client.post(
                path,
                json={"model": model, "messages": BODY_MESSAGES, "stream": False},
            )

    def test_openai_private_preset_is_403_even_with_bypass_query(self):
        for path in [
            "/openai/chat/completions",
            "/openai/chat/completions?bypass_filter=true",
        ]:
            r = self._post(path, "private-preset")
            assert r.status_code == 403, (path, r.text)

    def test_openai_raw_model_is_admin_only_even_with_bypass_query(self):
        for path in [
            "/openai/chat/completions",
            "/openai/chat/completions?bypass_filter=true",
        ]:
            r = self._post(path, "raw-base-model")
            assert r.status_code == 403, (path, r.text)

    def test_ollama_private_preset_is_403_even_with_bypass_query(self):
        for path in ["/ollama/api/chat", "/ollama/api/chat?bypass_filter=true"]:
            r = self._post(path, "private-preset")
            assert r.status_code == 403, (path, r.text)

    def test_ollama_raw_model_is_admin_only_even_with_bypass_query(self):
        for path in ["/ollama/api/chat", "/ollama/api/chat?bypass_filter=true"]:
            r = self._post(path, "raw-base-model")
            assert r.status_code == 403, (path, r.text)


def test_bypass_filter_parameter_is_gone():
    from nucllei.routers import ollama, openai
    from nucllei.utils import chat

    for fn in (
        openai.generate_chat_completion,
        ollama.generate_chat_completion,
        chat.generate_chat_completion,
    ):
        assert "bypass_filter" not in inspect.signature(fn).parameters, fn


# 2026-10-05 (refs #24): 生パススルーは filter を通らない(large_file_gate 等の方針用
# filter を迂回できる)ので、endpoint としては admin 限定にした(ken 決定)。
# role ゲートの実物を通すため mock_webui_user_strict を使う。
RAW_PASSTHROUGH_POSTS = [
    "/openai/chat/completions",
    "/openai/completions",  # catch-all proxy
    "/openai/embeddings",  # catch-all proxy
    "/ollama/api/chat",
    "/ollama/api/chat/0",
    "/ollama/api/generate",
    "/ollama/api/generate/0",
    "/ollama/v1/completions",
    "/ollama/v1/completions/0",
    "/ollama/v1/chat/completions",
    "/ollama/v1/chat/completions/0",
]


class TestRawPassthroughAdminOnly(AbstractPostgresTest):
    BASE_PATH = "/openai"

    def _post(self, path, role):
        from test.util.mock_user import mock_webui_user_strict

        with mock_webui_user_strict(id="2", role=role):
            return self.fast_api_client.post(
                path,
                json={
                    "model": "raw-base-model",
                    "prompt": "hi",
                    "messages": BODY_MESSAGES,
                    "stream": False,
                },
            )

    def test_user_is_rejected(self):
        for path in RAW_PASSTHROUGH_POSTS:
            r = self._post(path, "user")
            assert r.status_code == 401, (path, r.status_code, r.text)

    def test_admin_passes_the_role_gate(self):
        # 上流は無い(.invalid)ので応答自体は 4xx/5xx になりうるが、role ゲートでは止まらない。
        # catch-all proxy が既定の api.openai.com へ出ないよう接続先を差し替える。
        from nucllei.constants import ERROR_MESSAGES
        from nucllei.main import app

        saved = app.state.config.OPENAI_API_BASE_URLS
        app.state.config.OPENAI_API_BASE_URLS = ["http://upstream.invalid/v1"]
        try:
            for path in RAW_PASSTHROUGH_POSTS:
                r = self._post(path, "admin")
                assert ERROR_MESSAGES.ACCESS_PROHIBITED not in r.text, (
                    path,
                    r.status_code,
                    r.text,
                )
        finally:
            app.state.config.OPENAI_API_BASE_URLS = saved

    def test_model_listing_stays_open_to_users(self):
        from test.util.mock_user import mock_webui_user_strict

        with mock_webui_user_strict(id="2", role="user"):
            for path in ["/openai/models", "/ollama/api/tags"]:
                r = self.fast_api_client.get(path)
                assert r.status_code == 200, (path, r.status_code, r.text)
