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


def preflight() -> None:
    """technoplasm-app の `make tag` と同じガード。満たさなければ tag を切らせない。

    tag push は tag の指すコミット以下の objects しか運ばない — branch ref が
    origin に無いままだと、GitHub 上で「どのブランチにも属さない tag」になり
    workflow 一覧・履歴の見え方が壊れる。必ず branch を push してから tag。
    """
    branch = _run("git", "branch", "--show-current").strip()
    if not branch:
        sys.exit("❌ detached HEAD — ブランチ上で実行すること")

    if _run("git", "status", "--porcelain").strip():
        sys.exit(
            "❌ 未コミットの変更がある — tag は HEAD のコミットに打たれる。"
            "commit するか stash してから"
        )

    try:
        _run("git", "rev-parse", "--verify", f"origin/{branch}")
    except subprocess.CalledProcessError:
        sys.exit(f"❌ origin/{branch} が無い — 先に git push -u origin {branch}")

    unpushed = _run("git", "log", f"origin/{branch}..HEAD", "--oneline").strip()
    if unpushed:
        sys.exit(
            "❌ 未 push のコミットがある — 先に git push してから tag を切る:\n"
            + "\n".join(f"  {line}" for line in unpushed.splitlines())
        )


def main() -> None:
    ap = argparse.ArgumentParser(description="リリース/プレ tag を切る")
    ap.add_argument("kind", choices=["pre", "release"])
    ap.add_argument(
        "--push", action="store_true", help="tag を origin へ push(CI 発火)"
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="採番とガードの確認だけ(tag を打たない)"
    )
    args = ap.parse_args()

    preflight()
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
