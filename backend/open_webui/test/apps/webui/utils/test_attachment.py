"""Attachment ディスパッチ(#52 プランB)の回帰。

fork 本体は Attachment の「仕組み」だけを持ち、実体は案件層(prism)の authored Function。
ここでは DB に登録した実 Attachment を plugin ローダ経由で実行できること、および適用が
モデルの attachmentIds への完全 opt-in であることを固定する(builtin は無い)。
"""

from types import SimpleNamespace

from test.util.abstract_integration_test import AbstractPostgresTest

# file.data に content を書くだけの最小 Attachment(抽出の実体は本来内部関数へ委譲)。
ATTACHMENT_CONTENT_TEMPLATE = """
class Attachment:
    def process(self, file, __request__=None, __user__=None):
        file.data = {"content": "RAN:{fid}"}
        return file
"""


def _make_request():
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(FUNCTIONS={})))


class TestAttachmentDispatch(AbstractPostgresTest):
    BASE_PATH = "/api/v1/functions"

    def setup_class(cls):
        super().setup_class()
        from open_webui.models.functions import Functions

        cls.functions = Functions

    def _insert_attachment(self, fid):
        from open_webui.models.functions import FunctionForm

        function = self.functions.insert_new_function(
            user_id="1",
            type="attachment",
            form_data=FunctionForm(
                id=fid,
                name=fid,
                description="test attachment",
                content=ATTACHMENT_CONTENT_TEMPLATE.replace("{fid}", fid),
            ),
        )
        assert function is not None
        return function

    def test_get_attachment_function_ids_opt_in_and_scope(self):
        from open_webui.utils.attachment import get_attachment_function_ids

        self._insert_attachment("att_x")

        # 完全 opt-in: 割り当てられた実在 id だけ。未割り当て/未知は空。
        assert get_attachment_function_ids(None) == []
        assert get_attachment_function_ids([]) == []
        assert get_attachment_function_ids(["att_x"]) == ["att_x"]
        assert get_attachment_function_ids(["nonexistent"]) == []
        assert get_attachment_function_ids(["att_x", "nonexistent"]) == ["att_x"]

    def test_process_runs_assigned_attachment(self):
        from open_webui.utils.attachment import process_attachment_functions

        self._insert_attachment("att_run")
        file = SimpleNamespace(id="f1", data=None)
        out = process_attachment_functions(
            _make_request(), file, user={"id": "1"}, assigned_ids=["att_run"]
        )
        assert out.data == {"content": "RAN:att_run"}

    def test_unassigned_attachment_does_not_run(self):
        from open_webui.utils.attachment import process_attachment_functions

        self._insert_attachment("att_off")
        file = SimpleNamespace(id="f2", data=None)
        out = process_attachment_functions(
            _make_request(), file, user={"id": "1"}, assigned_ids=None
        )
        # opt-in: 未割り当ては何も走らず data は触られない
        assert out.data is None
