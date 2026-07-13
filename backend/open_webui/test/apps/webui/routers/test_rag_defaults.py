"""RAG / web 検索系 config の撤去を固定するスモーク(issue #9 / #52 / #51)。

llens プロファイルは RAG(ベクタ化・埋め込み検索)を使わない薄いチャット基盤。
文書抽出は案件層(prism)の Attachment Function が docling を直接叩く方式(#52)、
web 検索も同じ線引きで prism の tool が外部検索システム(SearXNG 等)を直接叩く
(#51、2026-07-13 確定)。fork は検索の config・providers・管理画面を一切持たない。
"""

from test.util.abstract_integration_test import AbstractPostgresTest


class TestRAGDefaults(AbstractPostgresTest):
    BASE_PATH = "/api/v1/retrieval"

    def test_web_search_config_is_gone(self):
        """web 検索系 config が fork に再導入されていないことを固定する(#51)。"""
        from open_webui.main import app

        for name in (
            "ENABLE_WEB_SEARCH",
            "WEB_SEARCH_ENGINE",
            "SEARXNG_QUERY_URL",
            "WEB_LOADER_ENGINE",
        ):
            assert not hasattr(app.state.config, name), name
