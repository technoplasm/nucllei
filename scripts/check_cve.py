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

npm 側は別経路で見る:
    フロントの依存だけは depot に聞いても出てこない。ビルド後は `/app/build` に
    バンドルされ、`node_modules` もロックファイルも image に残らないので trivy が
    特定できないため(実測: image 側の CVE は OS パッケージと Python だけ)。
    **見えないのではなく image に実体が無いだけ**で、`pnpm-lock.yaml` はここにある。
    なので npm は `pnpm audit` で直接見て、同じ表に並べる。

    見られなかったときは **0 件と言わずに「見ていない」と言う**。ここを黙って 0 に
    すると、実際には CRITICAL があるのに「無い」と読める(実際そうなっていた)。

CI では depot 側が落ちる:
    配布ホストは院内ネット上にあり GitHub Actions の runner からは到達できない。
    npm 側は CI でも走る(pnpm audit はネットワークだけあればよい)。
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

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


def pnpm_cmd() -> tuple[list[str] | None, str]:
    """pnpm audit の起動方法を決める。手元に pnpm があればそれ、無ければ docker で
    .node-version / packageManager と同じ版を使う。どちらも無ければ None を返す —
    **黙って 0 件にしない**ため、呼び出し側で「見ていない」と出す。"""
    pm = "pnpm@11.3.0"
    try:
        pm = json.loads((ROOT / "package.json").read_text()).get("packageManager") or pm
    except Exception:
        pass
    if shutil.which("pnpm"):
        return ["pnpm"], "pnpm (手元)"
    if shutil.which("docker"):
        major = "24"
        try:
            major = re.match(r"\d+", (ROOT / ".node-version").read_text().strip())[0]
        except Exception:
            pass
        return (
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{ROOT}:/w",
                "-w",
                "/w",
                "-u",
                f"{os.getuid()}:{os.getgid()}",
                "-e",
                "HOME=/tmp/h",
                "-e",
                "npm_config_cache=/tmp/h/npm",
                "-e",
                "npm_config_store_dir=/tmp/h/store",
                f"node:{major}-alpine",
                "npx",
                "--yes",
                pm,
            ],
            f"docker node:{major}-alpine ({pm})",
        )
    return None, ""


def npm_audit() -> tuple[list[tuple], str | None]:
    """本番依存 (実際にバンドルされる分) だけを見る。devDependencies は配布物に載らない。
    戻りは (行, 見られなかった理由)。理由が入っていたら **件数を出さない**。"""
    cmd, how = pnpm_cmd()
    if cmd is None:
        return [], "pnpm も docker も無い"
    proc = subprocess.run(
        cmd + ["audit", "--prod", "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "CI": "true"},
    )
    # audit は脆弱性が有ると非 0 で終わる。JSON が取れているかで成否を見る
    try:
        advisories = (json.loads(proc.stdout) or {}).get("advisories") or {}
    except json.JSONDecodeError:
        return (
            [],
            f"{how} で実行できない ({(proc.stderr or proc.stdout).strip()[:120]})",
        )
    rows = []
    for a in advisories.values():
        sev = (a.get("severity") or "unknown").upper()
        patched = (a.get("patched_versions") or "").strip()
        # npm 側は「修正版なし」を <0.0.0 で表す
        fixed = "" if patched in ("", "<0.0.0") else patched
        if sev in SHOW and (fixed or not FIXABLE_ONLY):
            paths = (a.get("findings") or [{}])[0].get("paths") or []
            direct = bool(paths) and paths[0].count(">") <= 1
            rows.append(
                (
                    SEVERITIES.index(sev) if sev in SEVERITIES else 99,
                    sev,
                    a.get("github_advisory_id")
                    or a.get("cves", [""])[0]
                    or a.get("id", ""),
                    "npm" + ("(直接)" if direct else ""),
                    a.get("module_name", "?"),
                    a.get("vulnerable_versions", "?"),
                    fixed,
                    a.get("title", ""),
                )
            )
    return rows, None


def ecosystem(path: str | None) -> str:
    p = (path or "").lower()
    if "site-packages" in p or "dist-info" in p or ".venv" in p:
        return "Python"
    if "node_modules" in p:
        return "npm"
    return "OS パッケージ"


def main() -> int:
    ref = f"{IMAGE}:{TAG}"
    print(f"[1/2] image を depot に問い合わせ: {ref} ({DEPOT})")
    depot_err = None
    cves = []
    try:
        d = query(ref)
        if d.get("errors"):
            depot_err = d["errors"][0].get("message", "")[:120]
        else:
            cves = ((d.get("data") or {}).get("CVEListForImage") or {}).get(
                "CVEList"
            ) or []
    except RuntimeError as e:
        depot_err = str(e)

    print("[2/2] npm (本番依存) を pnpm audit で確認")
    npm_rows, npm_err = npm_audit()
    print()

    if depot_err:
        print(f"  image 側を見られなかった: {depot_err}")
        print(
            "  (配布ホストは院内ネット上。CI や外からは届かない。CVE DB は make cve-db)"
        )

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

    rows += npm_rows

    print("  由来ごとの内訳:")
    for eco in sorted(counts, key=lambda e: -sum(counts[e].values())):
        b = " / ".join(
            f"{s} {counts[eco][s]}" for s in SEVERITIES if counts[eco].get(s)
        )
        print(f"    {eco:<14} {b}")
    if depot_err:
        print("    (image 側は見ていない — 上の理由。0 件という意味ではない)")
    # **「見ていない」と「0 件」を混ぜない**。ここを黙って 0 にしていたせいで、
    # 実際には CRITICAL 2 件あった npm 側を「無い」と読める状態になっていた
    if npm_err:
        print(f"    npm            見ていない ({npm_err}) — 0 件という意味ではない")
    else:
        npm_c = {}
        for r in npm_rows:
            npm_c[r[1]] = npm_c.get(r[1], 0) + 1
        b = " / ".join(f"{s} {npm_c[s]}" for s in SEVERITIES if npm_c.get(s))
        print(f"    npm (本番依存)  {b or 'なし'}  ※修正版があるものだけ数えている")

    print(f"\n  {'修正版があるもの' if FIXABLE_ONLY else '一覧'} ({','.join(SHOW)}):")
    if not rows:
        print("    なし — 依存を上げて消せるものは残っていない")
    for _, sev, cid, eco, pkg, cur, fixed, title in sorted(rows):
        arrow = f"→ {fixed}" if fixed else "(修正版なし)"
        print(f"    [{sev:<8}] {cid:<20} {eco:<12} {pkg} {cur} {arrow}")
        if title:
            print(f"                   {title[:94]}")

    print("""
  「修正版があるもの」= pyproject.toml / uv.lock / package.json を上げれば消せる分。
  それ以外は上流 (base image・親パッケージ) が直すまで残る。**CVE は昇格の条件に
  していない**ので、件数が出ていても配布は止まらない (depot README「CVE は関所に
  していない」)。マルウェア検査は別レーンで、そちらは不合格なら実際に配布が止まる。

  npm は **本番依存 (--prod) だけ**を見る。devDependencies はバンドルに載らない。

    全部見る      : CVE_ALL=1 uv run task cve
    深刻度を変える: CVE_SEVERITY=CRITICAL uv run task cve
    版を指定      : CVE_TAG=v20260809-01 uv run task cve
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
