"""RAG 系フラグの llens 既定値スモーク(issue #9 / #52)。

llens プロファイルは RAG(ベクタ化・埋め込み検索)を使わない薄いチャット基盤。
文書抽出は案件層(prism)の Attachment Function が docling を直接叩く方式に移行済み
(fork 本体は builtin 抽出経路 / retrieval の process・query endpoint を持たない)。
ここでは残る RAG 系フラグの既定 off を固定する。
"""

from test.util.abstract_integration_test import AbstractPostgresTest


class TestRAGDefaults(AbstractPostgresTest):
    BASE_PATH = "/api/v1/retrieval"

    def test_llens_rag_defaults(self):
        """RAG 系フラグの llens 既定値(fresh DB)を固定する。

        #52: RAG 検索 config(TOP_K/hybrid/relevance/full_context/bypass)+ 取り込み
        経路(process/query/knowledge)は撤去済み。残る RAG 系フラグ
        (query generation / web search)の既定 off を固定する。
        """
        from open_webui.main import app

        assert app.state.config.ENABLE_RETRIEVAL_QUERY_GENERATION is False
        assert app.state.config.ENABLE_WEB_SEARCH is False
