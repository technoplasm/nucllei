#!/usr/bin/env python3
"""static/licenses.json を生成する(設定モーダル License タブの依存クレジット表示用)。

- frontend: `pnpm licenses list --prod --json`(pnpm store から。ネットワーク不要)
- backend: .venv の importlib.metadata(uv sync 済み環境から。ネットワーク不要)

再生成: `uv run task licenses`(依存を増減したリリース前に回して結果をコミットする)

注意: backend は「その時の venv に入っているもの」を数えるため、**素の `uv sync`(extra なし)の
venv で回すこと**。`--extra local-ml` 等が入った環境で回すと optional 依存
(torch / nvidia-cuda 系など)が混入する(2026-07-17 に実際に混入していて除去した)。
"""

import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "static" / "licenses.json"


def frontend_packages(captured: Path | None = None):
    # captured を渡すと pnpm を起動せず、その JSON を使う。**image ビルド時に使う** —
    # node_modules があるのは frontend ステージだけで、backend ステージには pnpm が無い。
    # frontend ステージで `pnpm licenses list` の出力だけを持ち越し、ここで合流させる
    if captured is not None:
        data = json.loads(captured.read_text())
    else:
        out = subprocess.run(
            ["pnpm", "licenses", "list", "--prod", "--json"],
            capture_output=True,
            text=True,
            check=True,
            cwd=ROOT,
        ).stdout
        data = json.loads(out)

    packages = []
    for license_name, items in data.items():
        for item in items:
            versions = item.get("versions") or []
            packages.append(
                {
                    "name": item["name"],
                    "version": versions[-1] if versions else "",
                    "license": license_name,
                }
            )
    return sorted(packages, key=lambda p: p["name"].lower())


def backend_packages():
    import importlib.metadata as md

    packages = []
    for dist in md.distributions():
        meta = dist.metadata
        name = meta.get("Name") or ""
        if not name:
            continue
        # package 化(console script `nucllei`)で自分自身も venv に入る — 依存ではないので除外
        if name == "nucllei":
            continue

        # License-Expression(PEP 639)→ License → classifier の順で拾う。
        # 旧式パッケージは License にライセンス本文を丸ごと入れていることがあるので
        # 長すぎる値は classifier に落とす
        license_name = meta.get("License-Expression") or meta.get("License") or ""
        if not license_name or "\n" in license_name or len(license_name) > 60:
            classifiers = [
                c.split("::")[-1].strip()
                for c in (meta.get_all("Classifier") or [])
                if c.startswith("License")
            ]
            license_name = (
                " / ".join(classifiers)
                or license_name.strip().split("\n")[0][:60]
                or "Unknown"
            )

        packages.append(
            {"name": name, "version": dist.version, "license": license_name}
        )

    # 同名重複(editable 等)を除いてソート
    seen = set()
    unique = []
    for p in sorted(packages, key=lambda p: p["name"].lower()):
        if p["name"].lower() in seen:
            continue
        seen.add(p["name"].lower())
        unique.append(p)
    return unique


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--frontend-json",
        type=Path,
        help="pnpm licenses list --prod --json の出力。指定すると pnpm を起動しない",
    )
    ap.add_argument("--out", type=Path, default=OUT, help=f"出力先 (既定 {OUT})")
    args = ap.parse_args()

    data = {
        "frontend": frontend_packages(args.frontend_json),
        "backend": backend_packages(),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
    print(
        f"wrote {args.out} (frontend {len(data['frontend'])} / backend {len(data['backend'])})"
    )


if __name__ == "__main__":
    main()
