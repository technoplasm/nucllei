"""Attachment ディスパッチ(#52 プランB / #69 グローバル化)の回帰。

fork 本体は Attachment の「仕組み」だけを持ち、実体は案件層(prism)の authored Function。
契約: `process(file, ...) -> str`(成功=抽出テキスト)/ 失敗=raise(core が
`content=str(e)` `status=failed` を差し込む)/ skip=None(何も永続化しない)。
適用は admin がグローバル config(ATTACHMENT_FUNCTION_ID)に選んだ単一フック(#69)。
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

# None を返す(=skip)。この file は扱わない → core は何も永続化しない(未抽出/none)。
SKIP_ATTACHMENT = """
class Attachment:
    def process(self, file, __request__=None, __user__=None):
        return None
"""

# __event__ を宣言する(=発火文脈 upload/attach が注入される)。
EVENT_ATTACHMENT = """
class Attachment:
    def process(self, file, __event__=None):
        return "EVENT:" + str(__event__)
"""


def _make_request(function_id=""):
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                FUNCTIONS={},
                config=SimpleNamespace(ATTACHMENT_FUNCTION_ID=function_id),
            )
        )
    )


class TestAttachmentDispatch(AbstractPostgresTest):
    BASE_PATH = "/api/v1/functions"

    def setup_class(cls):
        super().setup_class()
        from nucllei.models.functions import Functions
        from nucllei.models.files import Files

        cls.functions = Functions
        cls.files = Files

    def _insert_attachment(self, fid, template=OK_ATTACHMENT, type="attachment"):
        from nucllei.models.functions import FunctionForm

        function = self.functions.insert_new_function(
            user_id="1",
            type=type,
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
        from nucllei.models.files import FileForm

        return self.files.insert_new_file(
            "1",
            FileForm(id=fid, filename=f"{fid}.txt", path=f"/tmp/{fid}.txt"),
        )

    def test_get_attachment_function_id_resolution(self):
        from nucllei.utils.attachment import get_attachment_function_id

        self._insert_attachment("att_x")
        self._insert_attachment("flt_x", type="filter")

        # 未設定("")/実在しない id/type 違いは None(何も発火しない)
        assert get_attachment_function_id(_make_request("")) is None
        assert get_attachment_function_id(_make_request("nonexistent")) is None
        assert get_attachment_function_id(_make_request("flt_x")) is None
        assert get_attachment_function_id(_make_request("att_x")) == "att_x"

    def test_success_persists_content_and_status(self):
        from nucllei.utils.attachment import process_attachment_functions

        self._insert_attachment("att_ok")
        f = self._insert_file("file_ok")
        asyncio.run(
            process_attachment_functions(_make_request("att_ok"), f, user={"id": "1"})
        )
        saved = self.files.get_file_by_id("file_ok")
        assert saved.data == {"content": "EXTRACTED:att_ok", "status": "success"}

    def test_failure_records_message_and_failed_status(self):
        from nucllei.utils.attachment import process_attachment_functions

        self._insert_attachment("att_fail", template=FAIL_ATTACHMENT)
        f = self._insert_file("file_fail")
        asyncio.run(
            process_attachment_functions(_make_request("att_fail"), f, user={"id": "1"})
        )
        saved = self.files.get_file_by_id("file_fail")
        # 例外メッセージがそのまま content に(接頭辞なし)、status=failed
        assert saved.data == {"content": "unsupported:att_fail", "status": "failed"}

    def test_skip_returns_none_leaves_file_untouched(self):
        from nucllei.utils.attachment import process_attachment_functions

        # 設定済みだが None(skip) を返す → 未設定と同じく data は空のまま。
        self._insert_attachment("att_skip", template=SKIP_ATTACHMENT)
        f = self._insert_file("file_skip")
        asyncio.run(
            process_attachment_functions(_make_request("att_skip"), f, user={"id": "1"})
        )
        saved = self.files.get_file_by_id("file_skip")
        assert saved.data == {}

    def test_unconfigured_attachment_does_not_run(self):
        from nucllei.utils.attachment import process_attachment_functions

        self._insert_attachment("att_off")
        f = self._insert_file("file_off")
        asyncio.run(
            process_attachment_functions(_make_request(""), f, user={"id": "1"})
        )
        saved = self.files.get_file_by_id("file_off")
        # グローバル config 未設定なら何も走らず data は空のまま
        assert saved.data == {}

    def test_event_injected_when_declared(self):
        from nucllei.utils.attachment import process_attachment_functions

        # __event__ を宣言したフックには発火文脈(upload/attach)が注入される
        self._insert_attachment("att_ev", template=EVENT_ATTACHMENT)

        f = self._insert_file("file_ev_up")
        asyncio.run(
            process_attachment_functions(
                _make_request("att_ev"), f, user={"id": "1"}, event="upload"
            )
        )
        assert self.files.get_file_by_id("file_ev_up").data["content"] == "EVENT:upload"

        f = self._insert_file("file_ev_at")
        asyncio.run(
            process_attachment_functions(
                _make_request("att_ev"), f, user={"id": "1"}, event="attach"
            )
        )
        assert self.files.get_file_by_id("file_ev_at").data["content"] == "EVENT:attach"

    def test_event_not_required_by_legacy_hooks(self):
        from nucllei.utils.attachment import process_attachment_functions

        # __event__ 未宣言の既存フックは attach でも従来どおり動く
        self._insert_attachment("att_legacy")
        f = self._insert_file("file_legacy")
        asyncio.run(
            process_attachment_functions(
                _make_request("att_legacy"), f, user={"id": "1"}, event="attach"
            )
        )
        saved = self.files.get_file_by_id("file_legacy")
        assert saved.data == {"content": "EXTRACTED:att_legacy", "status": "success"}
