"""生パススルー(/openai/chat/completions・/ollama/api/chat)の認可の回帰(2026-10-05)。

両ルートは `bypass_filter` をクエリ引数として露出しており、一般ユーザーが
`?bypass_filter=true` を付けるだけで preset の ACL(access_control={} = 非公開)と
admin 専用の生モデル制限を素通りできた(403 → 200、上流へ送信まで到達)。
内部から True で呼ぶ箇所は無かったので、引数ごと撤去した。
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
