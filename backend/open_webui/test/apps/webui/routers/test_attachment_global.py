"""グローバル Attachment(#69)の router 回帰。

- upload: グローバル config(ATTACHMENT_FUNCTION_ID)設定時のみ dispatch される
- POST /files/{id}/process: 再添付フック(__event__="attach")。dispatch は owner/admin のみ、
  read 可の非 owner は現状の file を返すだけ、read 不可は 404
- POST /configs/attachment: function_id の保存と実在検証(不正 id は 400)
"""

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user

OK_ATTACHMENT = """
class Attachment:
    def process(self, file, __event__=None):
        return "EXTRACTED:" + str(__event__)
"""

SKIP_ATTACHMENT = """
class Attachment:
    def process(self, file, __event__=None):
        return None
"""


class TestAttachmentGlobal(AbstractPostgresTest):
    BASE_PATH = "/api/v1/files"

    def setup_class(cls):
        super().setup_class()
        from open_webui.models.files import Files
        from open_webui.models.functions import Functions

        cls.files = Files
        cls.functions = Functions

    def teardown_method(self):
        # config テーブルは TRUNCATE 対象外のため、テスト間で必ず未設定に戻す
        from open_webui.main import app

        app.state.config.ATTACHMENT_FUNCTION_ID = ""
        # 削除済み関数の module キャッシュが後続テストに漏れないように
        app.state.FUNCTIONS = {}
        super().teardown_method()

    def _insert_attachment(self, fid, template=OK_ATTACHMENT):
        from open_webui.models.functions import FunctionForm

        function = self.functions.insert_new_function(
            user_id="1",
            type="attachment",
            form_data=FunctionForm(
                id=fid, name=fid, description="test", content=template
            ),
        )
        assert function is not None
        return function

    def _insert_file(self, fid, user_id="1", access_control=None):
        from open_webui.models.files import FileForm

        return self.files.insert_new_file(
            user_id,
            FileForm(
                id=fid,
                filename=f"{fid}.txt",
                path=f"/tmp/{fid}.txt",
                access_control=access_control,
            ),
        )

    def _set_function_id(self, fid):
        from open_webui.main import app

        app.state.config.ATTACHMENT_FUNCTION_ID = fid

    # ---- upload ----

    def test_upload_dispatches_when_configured(self):
        self._insert_attachment("att_up")
        self._set_function_id("att_up")
        with mock_webui_user(id="1"):
            r = self.fast_api_client.post(
                self.create_url("/"),
                files={"file": ("doc.txt", b"hello", "text/plain")},
            )
        assert r.status_code == 200, r.text
        saved = self.files.get_file_by_id(r.json()["id"])
        assert saved.data == {"content": "EXTRACTED:upload", "status": "success"}

    def test_upload_noop_when_unconfigured(self):
        self._insert_attachment("att_idle")
        # config 未設定("")なら何も発火しない
        with mock_webui_user(id="1"):
            r = self.fast_api_client.post(
                self.create_url("/"),
                files={"file": ("doc.txt", b"hello", "text/plain")},
            )
        assert r.status_code == 200, r.text
        saved = self.files.get_file_by_id(r.json()["id"])
        assert saved.data == {}

    # ---- chat.file_upload の enforcement(#69、permissions.md 原則 1/5)----

    def _set_file_upload_permission(self, value):
        from open_webui.main import app

        permissions = app.state.config.USER_PERMISSIONS
        permissions["chat"] = {**permissions.get("chat", {}), "file_upload": value}
        app.state.config.USER_PERMISSIONS = permissions

    def test_upload_forbidden_when_file_upload_permission_off(self):
        self._set_file_upload_permission(False)
        try:
            with mock_webui_user(id="1"):  # 既定 role="user"
                r = self.fast_api_client.post(
                    self.create_url("/"),
                    files={"file": ("doc.txt", b"hello", "text/plain")},
                )
            assert r.status_code == 401, r.text
        finally:
            self._set_file_upload_permission(True)

    def test_upload_allowed_for_admin_even_when_permission_off(self):
        self._set_file_upload_permission(False)
        try:
            with mock_webui_user(id="1", role="admin"):
                r = self.fast_api_client.post(
                    self.create_url("/"),
                    files={"file": ("doc.txt", b"hello", "text/plain")},
                )
            assert r.status_code == 200, r.text
        finally:
            self._set_file_upload_permission(True)

    # ---- POST /files/{id}/process(再添付フック)----

    def test_process_owner_dispatches_with_attach_event(self):
        self._insert_attachment("att_re")
        self._set_function_id("att_re")
        self._insert_file("file_re", user_id="1")
        with mock_webui_user(id="1"):
            r = self.fast_api_client.post(self.create_url("/file_re/process"))
        assert r.status_code == 200, r.text
        assert r.json()["data"] == {"content": "EXTRACTED:attach", "status": "success"}

    def test_process_hook_none_keeps_data(self):
        self._insert_attachment("att_keep", template=SKIP_ATTACHMENT)
        self._set_function_id("att_keep")
        self._insert_file("file_keep", user_id="1")
        self.files.update_file_data_by_id(
            "file_keep", {"content": "already", "status": "success"}
        )
        with mock_webui_user(id="1"):
            r = self.fast_api_client.post(self.create_url("/file_keep/process"))
        assert r.status_code == 200, r.text
        # skip(None)= 再抽出しない判断 → 既存 data 不変
        assert r.json()["data"] == {"content": "already", "status": "success"}

    def test_process_non_owner_reader_does_not_dispatch(self):
        self._insert_attachment("att_ro")
        self._set_function_id("att_ro")
        # 全体公開マーカー付き = user "2" も read 可。だが dispatch は owner/admin のみ。
        self._insert_file(
            "file_ro", user_id="1", access_control={"read": {"public": True}}
        )
        with mock_webui_user(id="2"):
            r = self.fast_api_client.post(self.create_url("/file_ro/process"))
        assert r.status_code == 200, r.text
        assert r.json()["data"] == {}
        assert self.files.get_file_by_id("file_ro").data == {}

    def test_process_without_read_access_is_404(self):
        self._insert_attachment("att_na")
        self._set_function_id("att_na")
        # access_control=None = 非公開(owner のみ)
        self._insert_file("file_na", user_id="1", access_control=None)
        with mock_webui_user(id="2"):
            r = self.fast_api_client.post(self.create_url("/file_na/process"))
        assert r.status_code == 404, r.text
        assert self.files.get_file_by_id("file_na").data == {}


class TestAttachmentConfigFunctionId(AbstractPostgresTest):
    BASE_PATH = "/api/v1/configs"

    def setup_class(cls):
        super().setup_class()
        from open_webui.models.functions import Functions

        cls.functions = Functions

    def teardown_method(self):
        from open_webui.main import app

        app.state.config.ATTACHMENT_FUNCTION_ID = ""
        super().teardown_method()

    def _form(self, function_id):
        return {
            "ATTACHMENT_MAX_SIZE": 25,
            "ATTACHMENT_MAX_COUNT": None,
            "ATTACHMENT_ALLOWED_EXTENSIONS": [],
            "ATTACHMENT_FUNCTION_ID": function_id,
            "ENABLE_GOOGLE_DRIVE_INTEGRATION": False,
            "ENABLE_ONEDRIVE_INTEGRATION": False,
        }

    def test_save_valid_function_id(self):
        from open_webui.models.functions import FunctionForm

        self.functions.insert_new_function(
            user_id="1",
            type="attachment",
            form_data=FunctionForm(
                id="att_cfg", name="att_cfg", description="test", content="x = 1"
            ),
        )
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.post(
                self.create_url("/attachment"), json=self._form("att_cfg")
            )
            assert r.status_code == 200, r.text
            assert r.json()["ATTACHMENT_FUNCTION_ID"] == "att_cfg"

            r = self.fast_api_client.get(self.create_url("/attachment"))
            assert r.json()["ATTACHMENT_FUNCTION_ID"] == "att_cfg"

    def test_invalid_function_id_rejected(self):
        from open_webui.models.functions import FunctionForm

        # 実在しない id は 400
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.post(
                self.create_url("/attachment"), json=self._form("nonexistent")
            )
        assert r.status_code == 400, r.text

        # type が attachment でない実在 id も 400
        self.functions.insert_new_function(
            user_id="1",
            type="filter",
            form_data=FunctionForm(
                id="flt_cfg", name="flt_cfg", description="test", content="x = 1"
            ),
        )
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.post(
                self.create_url("/attachment"), json=self._form("flt_cfg")
            )
        assert r.status_code == 400, r.text

    def test_empty_function_id_clears_setting(self):
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.post(
                self.create_url("/attachment"), json=self._form("")
            )
        assert r.status_code == 200, r.text
        assert r.json()["ATTACHMENT_FUNCTION_ID"] == ""
