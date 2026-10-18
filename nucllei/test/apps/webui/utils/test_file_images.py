"""内部 file URL 画像の base64 インライン化の回帰テスト(issue #69)。

画像添付は files テーブル経由に統一され、チャット payload の image_url には
"/api/v1/files/{id}/content" 形式の内部 URL が入る。モデル側からこの URL へ
到達・認証できる保証がなく、モデルへ出る直前に inline_internal_image_urls が
data URL へ変換する(utils/file_images.py、process_chat_payload 末尾で発火)。

- 読取権限のある file だけ変換する(非公開 file は他人のリクエストでは変換されない)
- 読めない・不在・storage 欠落の内部 URL は text "(image unavailable)" に置換
  (相対 URL のまま残すとモデル側でリクエストごと落ちる。3 経路同一置換なので
  payload からアクセス不可と不存在を区別できない)
- data URL・外部 URL・文字列 content は触らない
"""

import asyncio
import base64
import os

from test.util.abstract_integration_test import AbstractPostgresTest

PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-image-bytes"
PNG_B64 = base64.b64encode(PNG_BYTES).decode("ascii")

UNAVAILABLE = {"type": "text", "text": "(image unavailable)"}


class TestFileImagesInline(AbstractPostgresTest):
    BASE_PATH = "/api/v1/files"

    def setup_method(self):
        super().setup_method()
        from nucllei.config import UPLOAD_DIR
        from nucllei.models.users import Users
        from nucllei.models.files import Files, FileForm

        self.Users = Users
        self.Files = Files
        self.FileForm = FileForm

        self.alice = Users.insert_new_user(
            id="alice", name="alice", email="alice@h.example", role="user"
        )
        self.bob = Users.insert_new_user(
            id="bob", name="bob", email="bob@h.example", role="user"
        )
        self.admin = Users.insert_new_user(
            id="admin", name="admin", email="admin@h.example", role="admin"
        )

        # alice 所有の画像 file(storage 実体付き・非公開)
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        self.img_path = f"{UPLOAD_DIR}/f-img_x.png"
        with open(self.img_path, "wb") as f:
            f.write(PNG_BYTES)
        Files.insert_new_file(
            "alice",
            FileForm(
                id="f-img",
                filename="x.png",
                path=self.img_path,
                meta={"name": "x.png", "content_type": "image/png", "size": 24},
                data={},
            ),
        )

    def _messages(self, url):
        return [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "これは何の画像?"},
                    {"type": "image_url", "image_url": {"url": url}},
                ],
            }
        ]

    def _convert(self, messages, user):
        from nucllei.utils.file_images import inline_internal_image_urls

        return asyncio.run(inline_internal_image_urls(messages, user))

    def test_cache_reuses_data_url_across_calls(self, monkeypatch):
        """cache を渡すと呼び出しをまたいで data URL を使い回す(tool ループの追撃で
        同じ tool 画像を毎周読み直して base64 化していた — 監査 2026-10-05)。"""
        from nucllei.utils import file_images

        loads = []
        real_load = file_images._load_file

        def counting_load(file_id, user):
            loads.append(file_id)
            return real_load(file_id, user)

        monkeypatch.setattr(file_images, "_load_file", counting_load)

        cache = {}
        url = "/api/v1/files/f-img/content"
        first = asyncio.run(
            file_images.inline_internal_image_urls(
                self._messages(url), self.alice, cache=cache
            )
        )
        second = asyncio.run(
            file_images.inline_internal_image_urls(
                self._messages(url), self.alice, cache=cache
            )
        )
        expected = f"data:image/png;base64,{PNG_B64}"
        assert first[0]["content"][1]["image_url"]["url"] == expected
        assert second[0]["content"][1]["image_url"]["url"] == expected
        assert loads == ["f-img"]

        # cache を渡さなければ従来どおり呼び出しごとに読む
        asyncio.run(
            file_images.inline_internal_image_urls(self._messages(url), self.alice)
        )
        assert loads == ["f-img", "f-img"]

    # ---- 変換される ----

    def test_owner_relative_url_converted(self):
        messages = self._convert(
            self._messages("/api/v1/files/f-img/content"), self.alice
        )
        url = messages[0]["content"][1]["image_url"]["url"]
        assert url == f"data:image/png;base64,{PNG_B64}"
        # text パーツは不変
        assert messages[0]["content"][0] == {"type": "text", "text": "これは何の画像?"}

    def test_absolute_url_converted(self):
        messages = self._convert(
            self._messages("http://localhost:8080/api/v1/files/f-img/content"),
            self.alice,
        )
        url = messages[0]["content"][1]["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")

    def test_url_with_query_converted(self):
        messages = self._convert(
            self._messages("/api/v1/files/f-img/content?attachment=false"), self.alice
        )
        url = messages[0]["content"][1]["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")

    def test_admin_can_convert_foreign_file(self):
        messages = self._convert(
            self._messages("/api/v1/files/f-img/content"), self.admin
        )
        url = messages[0]["content"][1]["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")

    def test_public_marker_file_converted_for_other_user(self):
        self.Files.update_file_access_control_by_id("f-img", {"read": {"public": True}})
        messages = self._convert(
            self._messages("/api/v1/files/f-img/content"), self.bob
        )
        url = messages[0]["content"][1]["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")

    def test_string_image_url_form_converted(self):
        # OpenAI 形式の {"url": ...} でなく素の文字列でも変換(shape は維持)
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": "/api/v1/files/f-img/content"}
                ],
            }
        ]
        messages = self._convert(messages, self.alice)
        assert messages[0]["content"][0]["image_url"] == (
            f"data:image/png;base64,{PNG_B64}"
        )

    def test_image_url_type_from_bytes_not_extension(self):
        # image_url の型は実バイト(magic)で決める — meta.content_type が無く拡張子が .jpg でも、
        # 中身が PNG なら image/png(2026-10-18: 上流は申告と中身の食い違いも受けない)。
        # 拡張子への fallback が効くのは magic を持たない SVG だけ(test_file_images_convert)
        with open(f"{self._upload_dir()}/f-jpg_y.jpg", "wb") as f:
            f.write(PNG_BYTES)
        self.Files.insert_new_file(
            "alice",
            self.FileForm(
                id="f-jpg",
                filename="y.jpg",
                path=f"{self._upload_dir()}/f-jpg_y.jpg",
                meta={},
                data={},
            ),
        )
        messages = self._convert(
            self._messages("/api/v1/files/f-jpg/content"), self.alice
        )
        url = messages[0]["content"][1]["image_url"]["url"]
        assert url == f"data:image/png;base64,{PNG_B64}"

    def _upload_dir(self):
        from nucllei.config import UPLOAD_DIR

        return UPLOAD_DIR

    # ---- 変換できない内部 URL は "(image unavailable)" の text パーツに置換 ----

    def test_unreadable_file_replaced_with_placeholder(self):
        # 非公開 file は他人のリクエストでは data URL 化されない。相対 URL のまま
        # 残すとモデル側でリクエストごと落ちるため text プレースホルダに置換される。
        messages = self._convert(
            self._messages("/api/v1/files/f-img/content"), self.bob
        )
        assert messages[0]["content"][1] == UNAVAILABLE

    def test_missing_file_replaced_with_placeholder(self):
        # 不在 file もアクセス不可と同一の置換(存在有無を payload から区別できない)
        messages = self._convert(
            self._messages("/api/v1/files/no-such/content"), self.alice
        )
        assert messages[0]["content"][1] == UNAVAILABLE

    def test_storage_missing_replaced_with_placeholder(self):
        # DB 行はあるが storage 実体が消えている場合も同一の置換(例外にしない)
        os.remove(self.img_path)
        messages = self._convert(
            self._messages("/api/v1/files/f-img/content"), self.alice
        )
        assert messages[0]["content"][1] == UNAVAILABLE

    def test_deleted_file_replaced_after_owner_delete(self):
        # 再現シナリオ固定: 添付済み画像を /files ページで削除(#94: soft delete)→
        # 以後のターンで履歴中の内部 URL がプレースホルダになり、チャット自体は失敗しない。
        self.Files.soft_delete_file_by_id("f-img", deleted_by="alice")
        messages = self._convert(
            self._messages("/api/v1/files/f-img/content"), self.alice
        )
        assert messages[0]["content"][1] == UNAVAILABLE

    def test_data_url_untouched(self):
        data_url = f"data:image/png;base64,{PNG_B64}"
        messages = self._convert(self._messages(data_url), self.alice)
        assert messages[0]["content"][1]["image_url"]["url"] == data_url

    def test_external_url_unavailable(self, monkeypatch):
        # 絶対 URL は resolver(image_refs)の名前空間 — /ext/ 形でなければ一般の URL として
        # 取りに行き、届かなければ URL のまま残さない(fail closed、#139 / 2026-10-03)。
        # 実ネットワークに出ないよう名前解決を潰す(= 届かない)
        from nucllei.utils import ext_upstreams

        async def no_dns(host):
            return []

        monkeypatch.setattr(ext_upstreams, "lookup_host", no_dns)
        url = "https://example.com/images/cat.png"
        messages = self._convert(self._messages(url), self.alice)
        assert messages[0]["content"][1] == UNAVAILABLE

    def test_plain_string_content_untouched(self):
        messages = [{"role": "user", "content": "/api/v1/files/f-img/content"}]
        assert self._convert(messages, self.alice) == [
            {"role": "user", "content": "/api/v1/files/f-img/content"}
        ]


# ---- 上書きされうる参照(sandbox のパス・/ext/)は cache でも読み直す ----------------


def _ref_messages(url):
    return [
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}}]}
    ]


def _fake_resolver(monkeypatch, contents, reads):
    """resolve_ref の代役: contents[ref] を毎回その時点の中身で返す(上書きを模す)。"""
    from nucllei.utils import file_images
    from nucllei.utils.image_refs import LoadedRef

    async def fake_resolve_ref(ref, user, **kwargs):
        reads.append(ref)
        return LoadedRef(data=contents[ref], content_type="image/png", name=ref), None

    monkeypatch.setattr(file_images, "resolve_ref", fake_resolve_ref)


def _data_url(raw):
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


def test_cache_rereads_overwritten_workspace_path(monkeypatch):
    """同じターンで同じ path を描き直して feed_image し直すと、新しい中身が届く
    (参照文字列で使い回して古い画像を送っていた — llens 2026-10-19)。"""
    from nucllei.utils.file_images import inline_internal_image_urls

    ref = "/workspace/plot.png"
    old, new = PNG_BYTES + b"-old", PNG_BYTES + b"-new"
    contents, reads = {ref: old}, []
    _fake_resolver(monkeypatch, contents, reads)

    cache = {}
    first = asyncio.run(
        inline_internal_image_urls(_ref_messages(ref), None, cache=cache)
    )
    contents[ref] = new
    second = asyncio.run(
        inline_internal_image_urls(_ref_messages(ref), None, cache=cache)
    )
    assert first[0]["content"][0]["image_url"]["url"] == _data_url(old)
    assert second[0]["content"][0]["image_url"]["url"] == _data_url(new)
    assert reads == [ref, ref]


def test_cache_reuses_conversion_for_unchanged_content(monkeypatch):
    """中身が同じなら読み直しても変換(base64・SVG 描画・PDF ラスタ化)はし直さない。
    /ext/ も上書きされうる側。http(s) はターン内で使い回す(読み直さない)。"""
    from nucllei.utils import file_images

    conversions = []
    real = file_images._materialize

    async def counting(loaded, page, label, name):
        conversions.append(name)
        return await real(loaded, page, label, name)

    monkeypatch.setattr(file_images, "_materialize", counting)
    ext, http = "/ext/maps/a.png", "https://example.org/a.png"
    contents, reads = {ext: PNG_BYTES, http: PNG_BYTES}, []
    _fake_resolver(monkeypatch, contents, reads)

    cache = {}
    for _ in range(2):
        for ref in (ext, http):
            asyncio.run(
                file_images.inline_internal_image_urls(
                    _ref_messages(ref), None, cache=cache
                )
            )
    assert reads == [ext, http, ext]
    assert conversions == [ext, http]
