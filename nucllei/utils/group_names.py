"""グループ名の正規化と検証(#151)。

グループ名は `/ext/groups/<グループ名>/` のディレクトリ名(と nsjail の bind 指定)に
そのまま使われる(#152)ため、作成時・改名時に

- パスとして危険な名前を拒否する(`validate_group_name` → `GroupNameError` = 400)
- 正規化した名前(`normalize_group_name_key`)の重複を拒否する
  (`group.name_key` の unique index + router の事前チェック → `GroupNameConflictError` = 409)

正規化は重複判定にだけ使い、保存する名前は入力のまま(前後の空白だけ落とす)。
#152 はここを import して使う — 検証規則を 2 か所に書かない。
"""

import unicodedata

# ext4 のファイル名上限は 255 バイト。ディレクトリ名 = グループ名そのままなので
# UTF-8 のバイト数で縛る(日本語なら 1 文字 3 バイト ≒ 85 文字)。
MAX_GROUP_NAME_BYTES = 255

# 名前に含めてはいけない文字(パス区切り・nsjail の bind 指定の区切り)
FORBIDDEN_GROUP_NAME_CHARS = frozenset("/\\:")

# 拒否する Unicode 一般カテゴリ:
# Cc = 制御文字(NUL・改行・タブ・DEL・C1)、Cf = 書式文字(ゼロ幅・書字方向制御・BOM 等)、
# Cs = サロゲート(UTF-8 に符号化できない)、Zl / Zp = 行・段落区切り
FORBIDDEN_GROUP_NAME_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})


class GroupNameError(ValueError):
    """グループ名がディレクトリ名として不正(HTTP 400)。message は利用者向けの日本語。"""


class GroupNameConflictError(Exception):
    """正規化した名前が既存グループと重複(HTTP 409)。"""


GROUP_NAME_CONFLICT_MESSAGE = (
    "同じ名前のグループが既にあります"
    "(大文字・小文字、全角・半角の違いは同じ名前とみなします)。"
)


def normalize_group_name_key(name: str) -> str:
    """重複判定用のキー: NFKC → 前後の空白除去 → casefold。"""
    return unicodedata.normalize("NFKC", name).strip().casefold()


def validate_group_name(name: str) -> str:
    """グループ名を検証し、保存する名前(前後の空白を落としたもの)を返す。

    不正なら `GroupNameError` を投げる。重複の判定はしない(DB を見るのは呼び出し側)。
    """
    if not isinstance(name, str):
        raise GroupNameError("グループ名は文字列で指定してください。")

    name = name.strip()
    if not name:
        raise GroupNameError("グループ名を入力してください。")

    for ch in name:
        if ch in FORBIDDEN_GROUP_NAME_CHARS:
            raise GroupNameError(
                "グループ名に「/」「\\」「:」は使えません"
                "(グループ別ディレクトリの名前になるため)。"
            )
        if unicodedata.category(ch) in FORBIDDEN_GROUP_NAME_CATEGORIES:
            raise GroupNameError(
                f"グループ名に制御文字・書式文字(U+{ord(ch):04X})は使えません。"
            )

    if name.startswith("."):
        # `.` と `..` もここで落ちる
        raise GroupNameError("グループ名を「.」で始めることはできません。")

    if len(name.encode("utf-8")) > MAX_GROUP_NAME_BYTES:
        raise GroupNameError(
            f"グループ名が長すぎます(UTF-8 で {MAX_GROUP_NAME_BYTES} バイトまで、"
            "日本語なら約 85 文字)。"
        )

    if not normalize_group_name_key(name):
        raise GroupNameError("グループ名を入力してください。")

    return name
