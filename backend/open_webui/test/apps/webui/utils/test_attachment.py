"""Attachment ディスパッチ(#52 プランB)の回帰。

fork 本体は Attachment の「仕組み」だけを持ち、実体は案件層(prism)の authored Function。
契約: `process(file, ...) -> str`(成功=抽出テキスト)/ 失敗=raise(core が
`content=str(e)` `status=failed` を差し込む)。適用はモデルの attachmentIds への完全 opt-in。
"""

import asyncio
from types import SimpleNamespace

from test.util.abstract_integration_test import AbstractPostgresTest

# str を返す(=成功・content)。core が content/status を永続化する。
OK_ATTACHMENT = """
class Attachment:
    def process(self, file, __request__=None, __user__=None):
        return "EXTRACTED:{fid}"
"""

# raise する(=失敗)。core が例外メッセージをそのまま content に、status=failed。
FAIL_ATTACHMENT = """
class Attachment:
    def process(self, file, __request__=None, __user__=None):
        raise Exception("unsupported:{fid}")
"""


def _make_request():
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(FUNCTIONS={})))


class TestAttachmentDispatch(AbstractPostgresTest):
    BASE_PATH = "/api/v1/functions"

    def setup_class(cls):
        super().setup_class()
        from open_webui.models.functions import Functions
        from open_webui.models.files import Files

        cls.functions = Functions
        cls.files = Files

    def _insert_attachment(self, fid, template=OK_ATTACHMENT):
        from open_webui.models.functions import FunctionForm

        function = self.functions.insert_new_function(
            user_id="1",
            type="attachment",
            form_data=FunctionForm(
                id=fid,
                name=fid,
                description="test attachment",
                content=template.replace("{fid}", fid),
            ),
        )
        assert function is not None
        return function

    def _insert_file(self, fid):
        from open_webui.models.files import FileForm

        return self.files.insert_new_file(
            "1",
            FileForm(id=fid, filename=f"{fid}.txt", path=f"/tmp/{fid}.txt"),
        )

    def test_get_attachment_function_ids_opt_in_and_scope(self):
        from open_webui.utils.attachment import get_attachment_function_ids

        self._insert_attachment("att_x")

        # 完全 opt-in: 割り当てられた実在 id だけ。未割り当て/未知は空。
        assert get_attachment_function_ids(None) == []
        assert get_attachment_function_ids([]) == []
        assert get_attachment_function_ids(["att_x"]) == ["att_x"]
        assert get_attachment_function_ids(["nonexistent"]) == []
        assert get_attachment_function_ids(["att_x", "nonexistent"]) == ["att_x"]

    def test_success_persists_content_and_status(self):
        from open_webui.utils.attachment import process_attachment_functions

        self._insert_attachment("att_ok")
        f = self._insert_file("file_ok")
        asyncio.run(
            process_attachment_functions(
                _make_request(), f, user={"id": "1"}, assigned_ids=["att_ok"]
            )
        )
        saved = self.files.get_file_by_id("file_ok")
        assert saved.data == {"content": "EXTRACTED:att_ok", "status": "success"}

    def test_failure_records_message_and_failed_status(self):
        from open_webui.utils.attachment import process_attachment_functions

        self._insert_attachment("att_fail", template=FAIL_ATTACHMENT)
        f = self._insert_file("file_fail")
        asyncio.run(
            process_attachment_functions(
                _make_request(), f, user={"id": "1"}, assigned_ids=["att_fail"]
            )
        )
        saved = self.files.get_file_by_id("file_fail")
        # 例外メッセージがそのまま content に(接頭辞なし)、status=failed
        assert saved.data == {"content": "unsupported:att_fail", "status": "failed"}

    def test_unassigned_attachment_does_not_run(self):
        from open_webui.utils.attachment import process_attachment_functions

        self._insert_attachment("att_off")
        f = self._insert_file("file_off")
        asyncio.run(
            process_attachment_functions(
                _make_request(), f, user={"id": "1"}, assigned_ids=None
            )
        )
        saved = self.files.get_file_by_id("file_off")
        # opt-in: 未割り当ては何も走らず data は空のまま
        assert saved.data == {}
