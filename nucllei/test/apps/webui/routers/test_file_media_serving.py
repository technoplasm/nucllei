"""メディアファイル配信の Content-Disposition(issue #87)。

チャットの音声・動画埋め込み(生 HTML `<audio>`/`<video>`)は `/api/v1/files/{id}/content` を
src に張る。attachment 配信だと Firefox がメディア要素での再生を拒否するため、
audio/* と video/* は inline で配信する。保存導線は `?attachment=true` が担う。
"""

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user


class TestMediaFileServing(AbstractPostgresTest):
    BASE_PATH = "/api/v1/files"

    def _upload(self, name: str, content_type: str) -> str:
        with mock_webui_user(id="1", role="admin"):
            up = self.fast_api_client.post(
                self.create_url("", query_params={"process": "false"}),
                files={"file": (name, b"\xff\xfb\x90dummy", content_type)},
            )
        assert up.status_code == 200, up.text
        return up.json()["id"]

    def test_audio_content_is_inline(self):
        file_id = self._upload("voice.mp3", "audio/mpeg")
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.get(self.create_url(f"/{file_id}/content"))
        assert r.status_code == 200
        assert r.headers.get("content-disposition", "").startswith("inline")

    def test_video_content_is_inline(self):
        file_id = self._upload("clip.mp4", "video/mp4")
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.get(self.create_url(f"/{file_id}/content"))
        assert r.status_code == 200
        assert r.headers.get("content-disposition", "").startswith("inline")

    def test_audio_attachment_query_still_downloads(self):
        file_id = self._upload("voice.mp3", "audio/mpeg")
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.get(
                self.create_url(
                    f"/{file_id}/content", query_params={"attachment": "true"}
                )
            )
        assert r.status_code == 200
        assert r.headers.get("content-disposition", "").startswith("attachment")

    def test_other_binary_stays_attachment(self):
        file_id = self._upload("blob.bin", "application/octet-stream")
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.get(self.create_url(f"/{file_id}/content"))
        assert r.status_code == 200
        assert r.headers.get("content-disposition", "").startswith("attachment")
