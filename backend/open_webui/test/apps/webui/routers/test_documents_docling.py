"""Documents(Docling 抽出)経路のスモークテスト(issue #9)。

llens プロファイルでは RAG(ベクタ化・埋め込み検索)は使わないが、
「ファイル添付 → Docling 抽出 → 全文注入」の経路はデグレさせない
(fork.md §5: Documents は Docling で文書抽出ができれば十分)。

- CONTENT_EXTRACTION_ENGINE=docling + DOCLING_SERVER_URL で抽出が動くこと
- BYPASS_EMBEDDING_AND_RETRIEVAL(既定 True)でベクタ DB 保存が
  スキップされ、抽出テキストが file.data.content に入ること
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user

EXTRACTED_MARKDOWN = "# 診療報告書\n\n患者テキストの抽出結果です。"


class MockDoclingHandler(BaseHTTPRequestHandler):
    """docling-serve 互換の最小モック(/v1alpha/convert/file)。"""

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        if self.path.rstrip("/") == "/v1alpha/convert/file":
            self.server.received.append(len(raw))
            body = json.dumps({"document": {"md_content": EXTRACTED_MARKDOWN}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()


class TestDoclingExtraction(AbstractPostgresTest):
    BASE_PATH = "/api/v1/files"

    @classmethod
    def setup_class(cls):
        super().setup_class()
        cls.mock_server = ThreadingHTTPServer(("127.0.0.1", 0), MockDoclingHandler)
        cls.mock_server.received = []
        cls.server_thread = threading.Thread(
            target=cls.mock_server.serve_forever, daemon=True
        )
        cls.server_thread.start()

        from open_webui.main import app

        app.state.config.CONTENT_EXTRACTION_ENGINE = "docling"
        app.state.config.DOCLING_SERVER_URL = (
            f"http://127.0.0.1:{cls.mock_server.server_address[1]}"
        )

    @classmethod
    def teardown_class(cls):
        cls.mock_server.shutdown()
        cls.mock_server.server_close()
        super().teardown_class()

    def test_llens_rag_defaults(self):
        """RAG 系フラグの llens 既定値(fresh DB)を固定する。"""
        from open_webui.main import app

        assert app.state.config.BYPASS_EMBEDDING_AND_RETRIEVAL is True
        assert app.state.config.ENABLE_RETRIEVAL_QUERY_GENERATION is False
        assert app.state.config.ENABLE_WEB_SEARCH is False

    def test_docling_extraction_without_vector_db(self):
        """Docling 抽出 → 全文が data.content に入る(ベクタ DB へは保存されない)。

        #52: upload 時のテキスト化は Attachment(案件層 prism のポリシー)に移り、fork 本体は
        builtin Attachment を持たない。ここでは docling 抽出の能力そのものを、抽出の入口である
        `/retrieval/process/file`(extract_file_text 経由)で固定する(issue #9 の回帰)。
        """
        # 1) まずファイルをアップロード(model 無=Attachment 未発火なので raw 保存のみ)
        with mock_webui_user(id="1", role="admin"):
            response = self.fast_api_client.post(
                self.create_url("/"),
                files={
                    "file": (
                        "report.pdf",
                        b"%PDF-1.4 dummy-bytes",
                        "application/pdf",
                    )
                },
            )
        assert response.status_code == 200, response.text
        file_id = response.json()["id"]

        # この時点では抽出は走っていない(Attachment 未割り当て)
        assert len(self.mock_server.received) == 0

        # 2) 抽出の入口 process/file を叩く → docling 抽出 → data.content 保存
        with mock_webui_user(id="1", role="admin"):
            response = self.fast_api_client.post(
                "/api/v1/retrieval/process/file",
                json={"file_id": file_id},
            )
        assert response.status_code == 200, response.text
        data = response.json()

        # Docling モックに変換リクエストが届いている
        assert len(self.mock_server.received) == 1

        # 抽出結果が全文注入用の content に入り、ベクタ DB(collection)へは保存されない
        assert data["content"] == EXTRACTED_MARKDOWN
        assert data["collection_name"] is None

        # 保存済みコンテンツを Files API から取得できる
        with mock_webui_user(id="1", role="admin"):
            response = self.fast_api_client.get(
                self.create_url(f"/{file_id}/data/content")
            )
        assert response.status_code == 200
        assert response.json()["content"] == EXTRACTED_MARKDOWN
