from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user


class TestModels(AbstractPostgresTest):
    BASE_PATH = "/api/v1/models"

    def setup_class(cls):
        super().setup_class()
        from nucllei.models.models import Model

        cls.models = Model

    def test_models(self):
        with mock_webui_user(id="2", role="admin"):
            response = self.fast_api_client.get(self.create_url("/"))
        assert response.status_code == 200
        assert len(response.json()) == 0

        with mock_webui_user(id="2", role="admin"):
            response = self.fast_api_client.post(
                self.create_url("/create"),
                json={
                    "id": "my-model",
                    "base_model_id": "base-model-id",
                    "name": "Hello World",
                    "meta": {
                        "profile_image_url": "/static/favicon.png",
                        "description": "description",
                        "capabilities": None,
                        "model_config": {},
                    },
                    "params": {},
                },
            )
        assert response.status_code == 200

        with mock_webui_user(id="2", role="admin"):
            response = self.fast_api_client.get(self.create_url("/"))
        assert response.status_code == 200
        assert len(response.json()) == 1

        with mock_webui_user(id="2", role="admin"):
            response = self.fast_api_client.get(
                # モデル ID に '/' を含められるよう path param ではなくクエリで単体取得する API
                self.create_url("/model", query_params={"id": "my-model"})
            )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "my-model"
        assert data["name"] == "Hello World"

        with mock_webui_user(id="2", role="admin"):
            response = self.fast_api_client.delete(
                self.create_url("/model/delete?id=my-model")
            )
        assert response.status_code == 200

        with mock_webui_user(id="2", role="admin"):
            response = self.fast_api_client.get(self.create_url("/"))
        assert response.status_code == 200
        assert len(response.json()) == 0

    def test_preset_writes_refresh_the_chat_models_cache(self, monkeypatch=None):
        """preset の作成・更新・削除のあと app.state.MODELS (チャット経路が読む) を作り直す。
        以前は GET /api/models を誰かが叩くまで古い preset のまま動いた (llens staging 2026-10-04)。"""
        import pytest

        from nucllei.routers import models as models_router

        calls = []

        async def fake_get_all_models(request, user=None):
            calls.append(user.id if user else None)
            return []

        mp = pytest.MonkeyPatch()
        import nucllei.utils.models as utils_models

        mp.setattr(utils_models, "get_all_models", fake_get_all_models)
        try:
            body = {
                "id": "cache-model",
                "base_model_id": "base-model-id",
                "name": "Cache",
                "meta": {"skillIds": []},
                "params": {},
            }
            with mock_webui_user(id="2", role="admin"):
                assert (
                    self.fast_api_client.post(
                        self.create_url("/create"), json=body
                    ).status_code
                    == 200
                )
            body["meta"] = {"skillIds": ["new-skill"]}
            with mock_webui_user(id="2", role="admin"):
                assert (
                    self.fast_api_client.post(
                        self.create_url("/model/update?id=cache-model"), json=body
                    ).status_code
                    == 200
                )
            with mock_webui_user(id="2", role="admin"):
                assert (
                    self.fast_api_client.delete(
                        self.create_url("/model/delete?id=cache-model")
                    ).status_code
                    == 200
                )
            assert len(calls) == 3
            assert models_router._refresh_models_cache is not None
        finally:
            mp.undo()
