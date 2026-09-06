#!/usr/bin/env python3
"""リリース tag を切って push する(refs #25)。technoplasm-app `make tag` の移植。

    uv run task release    # 今日の vYYYYMMDD-NN

tag 形式は `vYYYYMMDD-NN` だけ。出荷しない試し焼きが要るときは tag を切らず、
image workflow(`.github/workflows/docker-build.yaml`)を workflow_dispatch で
手動起動する — image tag `dev` が出る。

make tag と同じ流儀:
- ガード(branch 上 / clean tree / origin にブランチあり / 未 push コミットなし)を
  全て満たさないと tag を切らせない。tag の指すコミットは常に push 済みブランチ上にある。
- 採番は既存 tag から自動(日毎 01 始まり)。
- tag を打ったら確認プロンプト(Y/n、既定 = push)。push した時点で CI が
  ghcr.io/technoplasm/nucllei:<tag> と ghcr.io/technoplasm/nucllei-sandbox:<tag> を
  **同じタグで**ビルドする(#114 — 本体とコード実行 sandbox は 1 セットで出す)。
  n なら local に残して終了。
"""

import datetime
import re
import subprocess
import sys


def _run(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout


def preflight() -> None:
    branch = _run("git", "branch", "--show-current").strip()
    if not branch:
        sys.exit("❌ detached HEAD — ブランチ上で実行すること")

    if _run("git", "status", "--porcelain").strip():
        sys.exit("❌ 未コミットの変更がある — commit するか stash してから")

    try:
        _run("git", "rev-parse", "--verify", f"origin/{branch}")
    except subprocess.CalledProcessError:
        sys.exit(f"❌ origin/{branch} が無い — 先に git push -u origin {branch}")

    unpushed = _run("git", "log", f"origin/{branch}..HEAD", "--oneline").strip()
    if unpushed:
        sys.exit(
            "❌ 未 push のコミットがある — 先に git push:\n"
            + "\n".join(f"  {line}" for line in unpushed.splitlines())
        )
    print(f"✅ origin/{branch} と同期済み")


def next_tag() -> str:
    # リリースは日毎 01 始まり(例: v20260716-01)
    prefix, pat, first = f"v{datetime.date.today():%Y%m%d}-", "v[0-9]*", 0

    tags = set(_run("git", "tag", "-l", pat).split())
    try:
        remote = _run("git", "ls-remote", "--tags", "origin", pat)
        tags |= {
            line.split("refs/tags/")[-1]
            for line in remote.splitlines()
            if "refs/tags/" in line and not line.endswith("^{}")
        }
    except subprocess.CalledProcessError:
        print("[warn] origin に届かない — local tag のみで採番", file=sys.stderr)

    series = sorted(t for t in tags if re.fullmatch(re.escape(prefix) + r"\d{2}", t))
    if series:
        print("📋 既存:", " ".join(series))
    nums = [int(t[len(prefix) :]) for t in series]
    return f"{prefix}{max(nums, default=first) + 1:02d}"


def main() -> None:
    preflight()
    tag = next_tag()
    head = _run("git", "rev-parse", "--short", "HEAD").strip()

    subprocess.run(["git", "tag", "-a", tag, "-m", f"Release: {tag}"], check=True)
    print(f"🏷️  tag {tag} を {head} に打った")

    try:
        confirm = input("Push this tag? (Y/n): ").strip()
    except EOFError:
        confirm = "n"
    if confirm.lower() == "n":
        print(f"❌ push 中止(local には残っている。消すなら: git tag -d {tag})")
        sys.exit(1)

    subprocess.run(["git", "push", "origin", tag], check=True)
    print(f"✅ push 済み: {tag} → CI が 2 つの image を同じタグでビルドする(#114):")
    print(f"     ghcr.io/technoplasm/nucllei:{tag}          (本体)")
    print(f"     ghcr.io/technoplasm/nucllei-sandbox:{tag}  (コード実行)")
    print("   どちらも docker-build.yaml の 2 job(同じ gate・同じ APP_VERSION)")
    print("   https://github.com/technoplasm/nucllei/actions")


if __name__ == "__main__":
    main()
