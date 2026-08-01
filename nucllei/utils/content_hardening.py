"""ブラウザ配信レスポンスの実行防止ハードニング(共有)。

同一オリジンでユーザーのブラウザへ中継・配信するレスポンスに適用する。
アクティブ型(HTML/SVG/JS)が上流・保存領域から来ても、ブラウザで
スクリプト実行させないよう添付ダウンロード扱いにし、MIME スニッフィングを
無効化する。/cache(CacheStaticFiles、GHSA-m8f9-9whg-f4xr)と /ext プロキシ
(#97 — 上流の content-type を透過するため同型の穴)が同じ定義を共有する。
"""

# ブラウザで実行され得る content-type(base 部分、小文字)。
ACTIVE_TYPES = ("text/html", "application/xhtml+xml", "image/svg+xml")


def active_content_headers(content_type: str | None) -> dict[str, str]:
    """content-type から付与すべき防御ヘッダを返す。

    - X-Content-Type-Options: nosniff は常時。
    - アクティブ型 / javascript は Content-Disposition: attachment(inline 実行を止める)。
      音声・画像等のメディアは inline のまま。
    """
    headers = {"X-Content-Type-Options": "nosniff"}
    base = (content_type or "").split(";")[0].strip().lower()
    if base in ACTIVE_TYPES or "javascript" in base:
        headers["Content-Disposition"] = "attachment"
    return headers
