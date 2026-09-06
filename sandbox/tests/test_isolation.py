"""sandbox jail の隔離テスト (権限昇格 / デバイス・ハードウェア・システムの見え方)。

これは稼働中の privileged コンテナに対する統合テストで、service が組み立てる nsjail の設定
(sandbox/README.md「セキュリティモデル」) を jail の *中から* 実際に突いて確認する。
ユニットテストではない。

到達経路: service のポートは host 非公開なので、コンテナ内の Python で
localhost:8000/exec を叩く (`docker exec` 越し)。追加依存なし (docker CLI + stdlib)。
コンテナが無い / 落ちていれば全テスト skip。

コンテナ名は配備側の compose が付けるので env で差し替えられる (既定は現行配備の
`llens-sandbox`)。

実行:
    uv run task sandbox-test
    python3 sandbox/tests/test_isolation.py     # pytest 不要 (末尾ランナー)
    SANDBOX_CONTAINER=<name> uv run task sandbox-test
"""

import json
import os
import subprocess

CONTAINER = os.environ.get("SANDBOX_CONTAINER", "llens-sandbox")
USER_ID = "isolationtest"  # _USER_ID_RE に合う専用テストユーザー

# コンテナ内で /exec を叩く固定クライアント。ユーザーコードは stdin の JSON に載せ、
# この -c 文字列自体は不変にして quoting 事故を避ける。
# 共有トークン (#132) は**コンテナ自身の env から取る** — 配備側がサービスに渡した値が
# そのまま正解なので、テスト側に値を持たせない (ずれようがない)。認証は opt-in なので
# 未設定のコンテナでは header の中身が空になるが、その場合は service 側も認証を掛けない
# ためそのまま通る。
_CLIENT = (
    "import os,sys,urllib.request\n"
    "d=sys.stdin.read()\n"
    "r=urllib.request.urlopen(urllib.request.Request("
    "'http://localhost:8000/exec',data=d.encode(),"
    "headers={'Content-Type':'application/json',"
    "'Authorization':'Bearer '+os.environ.get('SANDBOX_TOKEN','')}),timeout=90)\n"
    "sys.stdout.write(r.read().decode())\n"
)


def run(lang: str, code: str, timeout_s: int = 30) -> dict:
    """jail 内で code を実行し /exec の JSON を返す。lang は "python" | "shell"。"""
    payload = json.dumps(
        {"user_id": USER_ID, "lang": lang, "code": code, "timeout_s": timeout_s}
    )
    p = subprocess.run(
        ["docker", "exec", "-i", CONTAINER, "/opt/app/bin/python", "-c", _CLIENT],
        input=payload,
        capture_output=True,
        text=True,
        timeout=timeout_s + 60,
    )
    if p.returncode != 0:
        raise RuntimeError(
            f"docker exec {CONTAINER} rc={p.returncode}: {p.stderr[:300]}"
        )
    return json.loads(p.stdout)


def out(lang: str, code: str) -> str:
    """成功実行の stdout を返す (exit_code!=0 なら stderr 付きで落とす)。"""
    r = run(lang, code)
    assert r["exit_code"] == 0, f"exit={r['exit_code']} stderr={r['stderr'][:300]!r}"
    return r["stdout"]


def _service_reachable() -> bool:
    try:
        return run("python", "print('ok')")["stdout"].strip() == "ok"
    except Exception:
        return False


AVAILABLE = _service_reachable()
SKIP_REASON = f"sandbox container {CONTAINER!r} not reachable"

try:
    import pytest

    pytestmark = pytest.mark.skipif(not AVAILABLE, reason=SKIP_REASON)
except ImportError:  # __main__ ランナーだけで回すとき
    pytest = None


# ---------------------------------------------------------------------------
# 権限昇格
# ---------------------------------------------------------------------------
def test_runs_as_unprivileged_nobody():
    # jail 内 uid/gid は全ユーザー共通の 65534 (nobody)。root では動かない。
    assert out("python", "import os;print(os.getuid(),os.getgid())").split() == [
        "65534",
        "65534",
    ]


def test_cannot_setuid_root():
    # root へ上がれない (CAP_SETUID を持たない)。setuid(0) は PermissionError。
    assert (
        out(
            "python",
            "import os\ntry:\n os.setuid(0);print('ESCALATED')\n"
            "except PermissionError:\n print('DENIED')",
        ).strip()
        == "DENIED"
    )


def test_system_dirs_read_only():
    # rootfs (/usr /etc /opt/venv) は read-only bind。書けるのは /workspace と /tmp のみ。
    r = run(
        "shell",
        "for d in /usr /etc /opt/venv; do touch $d/pwned 2>&1; done; "
        "ls /usr/pwned /etc/pwned /opt/venv/pwned 2>&1",
    )
    assert r["stdout"].count("Read-only file system") == 3
    assert "No such file" in r["stdout"]  # どのファイルも作られていない


def test_ro_mount_root_is_read_only():
    # 参照用データの置き場 (/mnt、#138): 入れたものは jail から読めるが**書けない**。
    # 何も入れていない配備 (= 既定) では確かめる対象が無いので何もしない。
    listed = run("shell", "ls /mnt 2>/dev/null")["stdout"].split()
    if not listed:
        print("  note: /mnt に何も無い配備なので確認をスキップ (volume 未設定 = 既定)")
        return
    target = f"/mnt/{listed[0]}"
    # touch は dir/file どちらでも書き込み扱い (utimensat) なので RO なら EROFS になる
    r = run("shell", f"[ -e {target} ] && echo VISIBLE; touch {target} 2>&1")
    assert "VISIBLE" in r["stdout"], r["stdout"]
    assert "Read-only file system" in r["stdout"], r["stdout"]


def test_no_host_filesystem_access():
    # 他ユーザーの workspace / イメージ置き場 (ホスト /workspaces) は jail に bind されない。
    r = run("shell", "ls -d /workspaces /workspaces/.images 2>&1")
    assert r["exit_code"] != 0
    assert "No such file or directory" in r["stdout"]


# ---------------------------------------------------------------------------
# デバイス / ハードウェア / システムの見え方
# ---------------------------------------------------------------------------
def test_dev_contains_only_whitelisted_nodes():
    # jail の / は新規 tmpfs。/dev は DEV_BINDS の 5 ノードだけを bind する。
    assert sorted(out("shell", "ls /dev").split()) == [
        "full",
        "null",
        "random",
        "urandom",
        "zero",
    ]


def test_host_block_and_gpu_devices_absent():
    # コンテナは privileged でホストのデバイスを見られるが、jail の /dev はそれを剥がす。
    # ディスク実体・GPU・カーネルメモリのノードは一切見えてはならない。
    probes = [
        "/dev/sda",
        "/dev/loop0",
        "/dev/nvme0n1",
        "/dev/mem",
        "/dev/kmsg",
        "/dev/nvidia0",
        "/dev/dm-0",
    ]
    script = "".join(f"[ -e {d} ] && echo PRESENT {d}\n" for d in probes) + "echo done"
    r = out("shell", script)
    assert "PRESENT" not in r


def test_network_is_unreachable():
    # network namespace は空 (lo も上げない)。外部への到達は構造的に不可。
    r = out(
        "python",
        "import socket\ntry:\n"
        " s=socket.socket();s.settimeout(3);s.connect(('1.1.1.1',80));print('CONNECTED')\n"
        "except OSError as e:\n print('BLOCKED', e.errno)",
    )
    assert r.startswith("BLOCKED")


def test_disk_shows_quota_not_host():
    # df は per-user quota (既定 10GB) を返す。ホストの実 1.8TB は見えない
    # (「なんでも書ける」誤認防止)。ルート tmpfs は 16MB 固定。
    ws_total = int(out("shell", "df -B1 --output=size /workspace | tail -1").strip())
    assert 1 * 1024**3 <= ws_total <= 64 * 1024**3, ws_total  # GB オーダー、TB ではない
    root_total = int(out("shell", "df -B1 --output=size / | tail -1").strip())
    assert root_total == 16 * 1024 * 1024


def test_pid_namespace_hides_host_processes():
    # PID namespace が独立。jail 内からはホスト (uvicorn/nsjail 等) のプロセスが見えず、
    # 自プロセスは pid 1 付近。/proc に並ぶ pid はごく少数。
    line = out(
        "python",
        "import os;pids=[p for p in os.listdir('/proc') if p.isdigit()];"
        "print(os.getpid(),len(pids))",
    )
    my_pid, visible = (int(x) for x in line.split())
    assert my_pid <= 3
    assert visible <= 5


if __name__ == "__main__":
    if not AVAILABLE:
        print(f"SKIP (all): {SKIP_REASON}")
        raise SystemExit(0)
    tests = [
        v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL {t.__name__}: {e!r}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
