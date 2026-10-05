"""テスト用: `EXT_MOUNTS`(`/ext/<name>` の対応表、2026-10-05)を tmp の木から組む。"""

from pathlib import Path


def mount_tree(monkeypatch, root: Path, **extra: str) -> dict[str, str]:
    """`root` 直下の各ディレクトリ(ディレクトリ symlink を含む)を同名の **静的** mount にする
    (`root/cage/...` → `cage=<root>/cage`)。`root` が無ければ空。`extra` は追加の行
    (`cage="http://cage.test"` で proxy 等)。呼んだ時点の木で決まる — 後から足した
    トップレベルのディレクトリは入らない(本番の env と同じく起動時に固定)。"""
    from nucllei import env

    mounts: dict[str, str] = {}
    if root.is_dir():
        for child in sorted(root.iterdir()):
            if child.is_dir():
                mounts[child.name] = str(child)
    mounts.update(extra)
    monkeypatch.setattr(env, "EXT_MOUNTS", mounts)
    return mounts
