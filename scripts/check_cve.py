#!/usr/bin/env python3
"""配布ホスト (depot) が持っている CVE スキャン結果を手元から見る(issue #24)。

`uv run task cve`。**読み取りのみ**で、depot に何も書かない。

なぜスキャナを手元に持たないか:
    depot 側には既に検査が 2 つある — 関所 (promoter が昇格前に走らせる ClamAV) と
    網 (make scan の事後スキャン)。両者はずれないように同じ `scan.sh` を共有している。
    ここでもう 1 つスキャナを実装すると **3 つ目の判定**ができ、どれかがずれたときに
    「手元では出ないのに配布側で止まる(逆もある)」という一番たちの悪い状態になる。
    なので zot が持っている結果を **そのまま引く**。

なぜ nucllei 側にあるか:
    出てくるのは「依存を上げれば消せるもの」で、その依存を編集するのはこのリポジトリ。
    見るのが llens 側だと「見る → 直す」で repo をまたぐ往復になる。

CI では走らない:
    宛先の配布ホストは院内ネット上にあり、GitHub Actions の runner からは到達できない。
    これは手元で回す道具で、CI 側のゲートは `check_licenses.py` と pytest が持つ。

補足 — ここに出ないもの:
    フロントエンドの npm 依存は出てこない。ビルド後は `/app/build` にバンドルされ、
    `node_modules` もロックファイルも image に残らないため trivy が特定できない
    (実測: nucllei の CVE は OS パッケージと Python だけで npm は 0 件)。
    pnpm-lock.yaml を見る話は #24 の別項。
"""

import json
import os
import subprocess
import sys

DEPOT = os.environ.get("DEPOT_HOST", "llens-depot:13500")
IMAGE = os.environ.get("CVE_IMAGE", "llens/nucllei")
TAG = os.environ.get("CVE_TAG", "latest")
SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"]
# 既定は「上げれば消せるもの」に絞る。全部出すと数百件になって読めない
# (実測 499 件。うち大半は OS パッケージで、上流の base image が直すまで残る)
SHOW = [
    s.strip().upper()
    for s in os.environ.get("CVE_SEVERITY", "CRITICAL,HIGH").split(",")
]
FIXABLE_ONLY = os.environ.get("CVE_ALL") is None


def query(image: str) -> dict:
    """zot の GraphQL に投げる。curl を使うのは、自己署名 CA の信頼が
    docker / oras / git と同じ経路 (システムの CA ストア) で解決されるため。"""
    q = (
        '{ CVEListForImage(image:"%s"){ Tag CVEList{ Id Severity Title '
        "PackageList{ Name PackagePath InstalledVersion FixedVersion } } } }" % image
    )
    proc = subprocess.run(
        [
            "curl",
            "-sS",
            "-m",
            "300",
            "-X",
            "POST",
            "-H",
            "Content-Type: application/json",
            "--data",
            json.dumps({"query": q}),
            f"https://{DEPOT}/v2/_zot/ext/search",
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"depot に届かない ({DEPOT}): {proc.stderr.strip()[:200]}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"応答が JSON でない: {proc.stdout[:200]}")


def fixed_version(pkg: dict) -> str:
    """修正版があるなら返す。**trivy は「無い」を空文字ではなく "Not Specified" で返す**ので、
    素直に真偽値で見ると上流待ちの OS パッケージまで「上げれば直る」側に混ざり、
    絞り込みが意味を失う(実測: CRITICAL 14 件中 12 件が誤って混ざった)。"""
    v = (pkg.get("FixedVersion") or "").strip()
    return "" if v.lower() in ("", "not specified", "unknown", "none") else v


def ecosystem(path: str | None) -> str:
    p = (path or "").lower()
    if "site-packages" in p or "dist-info" in p or ".venv" in p:
        return "Python"
    if "node_modules" in p:
        return "npm"
    return "OS パッケージ"


def main() -> int:
    ref = f"{IMAGE}:{TAG}"
    print(f"depot に問い合わせています: {ref} ({DEPOT})\n")
    try:
        d = query(ref)
    except RuntimeError as e:
        print(f"  {e}")
        print(
            "  (院内ネットから見えるホストです。/etc/hosts の llens-depot と証明書を確認)"
        )
        return 2

    if d.get("errors"):
        msg = d["errors"][0].get("message", "")
        print(f"  スキャンできていない: {msg[:160]}")
        print(
            "  depot 側で CVE DB が要る (配布ホストで make cve-db。詳細は depot の README)"
        )
        return 2

    cves = ((d.get("data") or {}).get("CVEListForImage") or {}).get("CVEList") or []
    if not cves:
        print("  この版に CVE の報告は無い (または、まだ昇格していない)")
        return 0

    rows, counts = [], {}
    for c in cves:
        sev = c.get("Severity", "UNKNOWN")
        for p in c.get("PackageList") or []:
            eco = ecosystem(p.get("PackagePath"))
            counts.setdefault(eco, {}).setdefault(sev, 0)
            counts[eco][sev] += 1
            fixed = fixed_version(p)
            if sev in SHOW and (fixed or not FIXABLE_ONLY):
                rows.append(
                    (
                        SEVERITIES.index(sev) if sev in SEVERITIES else 99,
                        sev,
                        c["Id"],
                        eco,
                        p.get("Name", "?"),
                        p.get("InstalledVersion", "?"),
                        fixed,
                        c.get("Title", ""),
                    )
                )

    print("  由来ごとの内訳:")
    for eco in sorted(counts, key=lambda e: -sum(counts[e].values())):
        b = " / ".join(
            f"{s} {counts[eco][s]}" for s in SEVERITIES if counts[eco].get(s)
        )
        print(f"    {eco:<14} {b}")
    if "npm" not in counts:
        print(
            "    npm            (0 — バンドル済みで image から特定できない。#24 の別項)"
        )

    print(f"\n  {'修正版があるもの' if FIXABLE_ONLY else '一覧'} ({','.join(SHOW)}):")
    if not rows:
        print("    なし — 依存を上げて消せるものは残っていない")
    for _, sev, cid, eco, pkg, cur, fixed, title in sorted(rows):
        arrow = f"→ {fixed}" if fixed else "(修正版なし)"
        print(f"    [{sev:<8}] {cid:<18} {eco:<12} {pkg} {cur} {arrow}")
        if title:
            print(f"                 {title[:96]}")

    print(
        """
  「修正版があるもの」= pyproject.toml / uv.lock を上げれば消せる分。それ以外は
  上流 (base image・配布物) が直すまで残る。**CVE は昇格の条件にしていない**ので、
  ここに件数が出ていても配布は止まらない (depot README「CVE は関所にしていない」)。
  マルウェア検査は別レーンで、こちらは不合格なら実際に配布が止まる。

    全部見る      : CVE_ALL=1 uv run task cve
    深刻度を変える: CVE_SEVERITY=CRITICAL uv run task cve
    版を指定      : CVE_TAG=v20260809-01 uv run task cve
"""
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
