"""GHSA-m8f9-9whg-f4xr(audio transcription 拡張子 → /cache polyglot XSS)の回帰。

- 保存側: transcription は content-type から安全な音声拡張子を決め、ユーザー提供の
  filename 拡張子(.html 等)を保存名に使わない。
- 配信側: 無認証の /cache はアクティブ型(HTML/SVG/JS)を添付ダウンロード扱いにし
  nosniff を付ける。音声・画像などのメディアは inline のまま。
"""

from unittest.mock import patch

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user


class TestCacheSecurity(AbstractPostgresTest):
    BASE_PATH = "/api/v1/audio"

    def test_transcription_saves_safe_extension(self):
        """content-type=audio/wav を詐称した .html アップロードでも保存拡張子は wav。"""
        with (
            mock_webui_user(id="1"),
            patch("nucllei.routers.audio.compress_audio", side_effect=lambda p: p),
            patch("nucllei.routers.audio.transcribe", return_value={"text": "ok"}),
        ):
            response = self.fast_api_client.post(
                self.create_url("/transcriptions"),
                files={
                    "file": (
                        "evil.html",
                        b"RIFF0000WAVE<script>alert(1)</script>",
                        "audio/wav",
                    )
                },
            )
        assert response.status_code == 200
        filename = response.json()["filename"]
        assert filename.endswith(".wav")
        assert not filename.endswith(".html")

    def test_transcription_accepts_safari_mp4(self):
        """Safari の MediaRecorder は audio/mp4 しか作れない(#56 レビュー P5)。

        許可リストに無いと 400 になる。受理した上で、保存拡張子は
        ユーザー提供の filename ではなく content-type から決まる(GHSA の方針)。
        """
        with (
            mock_webui_user(id="1"),
            patch("nucllei.routers.audio.compress_audio", side_effect=lambda p: p),
            patch("nucllei.routers.audio.transcribe", return_value={"text": "ok"}),
        ):
            response = self.fast_api_client.post(
                self.create_url("/transcriptions"),
                files={
                    "file": (
                        "evil.html",
                        b"\x00\x00\x00\x20ftypM4A ",
                        "audio/mp4",
                    )
                },
            )
        assert response.status_code == 200
        filename = response.json()["filename"]
        assert filename.endswith(".mp4")
        assert not filename.endswith(".html")

    def test_cache_active_content_forced_attachment(self):
        """/cache 上の HTML は添付ダウンロード扱い + nosniff(ブラウザで実行させない)。"""
        from nucllei.config import CACHE_DIR

        d = CACHE_DIR / "audio" / "transcriptions"
        d.mkdir(parents=True, exist_ok=True)
        html_file = d / "xss_probe.html"
        html_file.write_text("<script>alert(1)</script>")
        try:
            r = self.fast_api_client.get("/cache/audio/transcriptions/xss_probe.html")
            assert r.status_code == 200
            assert r.headers.get("content-disposition") == "attachment"
            assert r.headers.get("x-content-type-options") == "nosniff"
        finally:
            html_file.unlink(missing_ok=True)

    def test_cache_media_served_inline(self):
        """メディア(画像)は inline のまま(添付にしない)。nosniff は付く。"""
        from nucllei.config import CACHE_DIR

        d = CACHE_DIR / "audio" / "transcriptions"
        d.mkdir(parents=True, exist_ok=True)
        png_file = d / "probe.png"
        png_file.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\x00")
        try:
            r = self.fast_api_client.get("/cache/audio/transcriptions/probe.png")
            assert r.status_code == 200
            # メディアは添付強制しない(inline 表示を壊さない)
            assert "content-disposition" not in {k.lower() for k in r.headers}
            assert r.headers.get("x-content-type-options") == "nosniff"
        finally:
            png_file.unlink(missing_ok=True)
