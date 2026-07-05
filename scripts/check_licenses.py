#!/usr/bin/env python3
"""依存パッケージの強コピーレフト(GPL/AGPL/SSPL/NC)検出(issue #2)。

インストール済み Python 環境(uv venv)を走査し、強コピーレフトの
ライセンスを検出したら exit 1 で失敗する。CI から `uv run` で実行する。
許容: LGPL(弱コピーレフト)、および ALLOWLIST に明記したパッケージ。

再監査の記録は _memos/license-audit.md。
"""

import importlib.metadata as md
import re
import sys

# 誤検知・審査済みの例外(名前: 理由)
ALLOWLIST = {
    "pandas": "BSD-3-Clause(ライセンス本文中の 'R' で誤検知)",
}

PATTERN = re.compile(r"GPL|AGPL|SSPL|NonCommercial|CC-BY-NC|EUPL", re.I)
WEAK = re.compile(r"LGPL", re.I)


def main() -> int:
    flagged = []
    for dist in md.distributions():
        name = dist.metadata["Name"]
        if name in ALLOWLIST:
            continue
        # 判定ソースの優先順: License-Expression(PEP 639)→ classifier →
        # License フィールド(短い識別子の場合のみ。長文はライセンス全文や
        # 同梱コードの notice を含み誤検知するため使わない)
        expression = dist.metadata.get("License-Expression") or ""
        classifiers = " ".join(
            c
            for c in (dist.metadata.get_all("Classifier") or [])
            if c.startswith("License")
        )
        license_field = dist.metadata.get("License") or ""
        if len(license_field) > 64:
            license_field = ""
        license_text = " ".join([expression, classifiers, license_field]).strip()

        # LGPL 表記を除去してから強コピーレフトを判定する
        stripped = WEAK.sub("", license_text)
        if PATTERN.search(stripped):
            flagged.append((name, license_text[:160]))

    if flagged:
        print("強コピーレフトの依存を検出しました(_memos/license-audit.md 参照):")
        for name, license_text in sorted(flagged):
            print(f"  {name}: {license_text}")
        return 1

    print("OK: 強コピーレフト(GPL/AGPL/SSPL/NC)の依存はありません")
    return 0


if __name__ == "__main__":
    sys.exit(main())
