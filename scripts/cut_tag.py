#!/usr/bin/env python3
"""リリース/プレ tag を切る(refs #25)。git tag = ghcr image tag の 1:1。

tag 形式は 2 つだけ(env.py の _git_describe / CI のトリガと一致):
  リリース: vYYYYMMDD-NN(切った時点で本番 — 日付は常に今日)
  プレ    : pre-NN(日付なしの通し番号)

採番は local + origin の既存 tag から次番号を自動計算。tag は lightweight
(pre-00 と同型 — describe は --tags で拾う)。push した時点で CI が
ghcr.io/technoplasm/nucllei:<tag> をビルドする。

使い方:
    uv run task tag pre            # 次の pre-NN を HEAD に打つ(push はしない)
    uv run task tag release        # 今日の vYYYYMMDD-NN を HEAD に打つ
    uv run task tag pre --push     # 打った上で origin へ push(= CI 発火)
    uv run task tag pre --dry-run  # 採番の確認だけ
"""

import argparse
import datetime
import re
import subprocess
import sys


def _run(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout


def existing_tags(pattern: str) -> set:
    """local と origin の両方から pattern に合う tag 名を集める(採番の取りこぼし防止)。"""
    tags = set(_run("git", "tag", "-l", pattern).split())
    try:
        remote = _run("git", "ls-remote", "--tags", "origin", pattern)
        tags |= {
            line.split("refs/tags/")[-1]
            for line in remote.splitlines()
            if "refs/tags/" in line and not line.endswith("^{}")
        }
    except subprocess.CalledProcessError:
        print("[warn] origin に届かない — local tag のみで採番する", file=sys.stderr)
    return tags


def next_tag(kind: str) -> str:
    if kind == "pre":
        # pre は 00 始まり(pre-00 が実在する慣例)
        prefix, pat, first = "pre-", "pre-*", -1
    else:
        # リリースは 01 始まり(例: v20260716-01)
        prefix, pat, first = f"v{datetime.date.today():%Y%m%d}-", "v[0-9]*", 0
    nums = [
        int(m.group(1))
        for t in existing_tags(pat)
        if (m := re.fullmatch(re.escape(prefix) + r"(\d{2})", t))
    ]
    return f"{prefix}{max(nums, default=first) + 1:02d}"


def main() -> None:
    ap = argparse.ArgumentParser(description="リリース/プレ tag を切る")
    ap.add_argument("kind", choices=["pre", "release"])
    ap.add_argument(
        "--push", action="store_true", help="tag を origin へ push(CI 発火)"
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="採番の確認だけ(tag を打たない)"
    )
    args = ap.parse_args()

    if _run("git", "status", "--porcelain").strip():
        print(
            "[warn] working tree が汚れている(tag は HEAD のコミットに打たれる — "
            "未コミット分は入らない)",
            file=sys.stderr,
        )

    tag = next_tag(args.kind)
    head = _run("git", "rev-parse", "--short", "HEAD").strip()

    if args.dry_run:
        print(f"[dry-run] 次の tag: {tag} (HEAD {head})")
        return

    subprocess.run(["git", "tag", tag], check=True)
    print(f"tag {tag} を {head} に打った")

    if args.push:
        subprocess.run(["git", "push", "origin", tag], check=True)
        print(f"push 済み → CI が ghcr.io/technoplasm/nucllei:{tag} をビルドする")
    else:
        print(f"push は手動で: git push origin {tag}")
        print(
            "(BE のバージョン反映は BE 再起動、FE は vite 再起動/touch vite.config.ts)"
        )


if __name__ == "__main__":
    main()
