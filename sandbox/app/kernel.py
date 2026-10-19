"""常駐カーネル (jail 内で 1 セッション = 1 プロセスとして常駐)。

sandbox サービスが nsjail 内でこれを起動し、stdin/stdout の framed protocol で
セルを送り込む。永続 globals で実行するので、変数・import はセッション内で残る
(stateful run_python の実体)。設計・検証経緯は sandbox/README.md。

protocol: 4byte big-endian 長さ + JSON。
  req  = {"code": str, "timeout": float}
  resp = {"stdout": str, "error": str|null, "timed_out": bool, "truncated": bool}

per-cell timeout は SIGALRM (setitimer) で内部的に中断する。外部シグナルは nsjail に
吸われて jail ごと落ちるため使わない (sandbox/README.md「割り込み」)。SIGALRM は本物の
シグナルなので time.sleep 等のブロッキング呼び出しも中断できる。中断してもプロセスは
生存し namespace は保たれる。中断不能な C 演算はサービス側が kill にエスカレートする。
"""

import ast
import json
import os
import signal
import struct
import sys
import traceback

STREAM_LIMIT = int(os.environ.get("SANDBOX_STREAM_LIMIT_BYTES", str(512 * 1024)))

# セル出力の fd レベル捕捉先。/tmp は jail 内の 256MB tmpfs (kernel 生存中のみ)。
# 実行ごとに O_TRUNC で作り直し、STREAM_LIMIT まで読んで返す。
CAP_PATH = "/tmp/.cell_output"

_G: dict = {"__name__": "__main__"}


class _CellTimeout(Exception):
    pass


def _on_alarm(signum, frame):
    raise _CellTimeout()


signal.signal(signal.SIGALRM, _on_alarm)


def _format_error() -> str:
    """kernel 自身のフレーム (exec/eval の呼び出し元) を除いたトレースバック文字列。
    先頭の kernel.py フレームを飛ばし、user コード (<cell>) 以降だけ見せる。"""
    etype, evalue, tb = sys.exc_info()
    if isinstance(evalue, SyntaxError):
        return "".join(traceback.format_exception_only(etype, evalue))
    while tb is not None and tb.tb_frame.f_code.co_filename.endswith("kernel.py"):
        tb = tb.tb_next
    return "".join(traceback.format_exception(etype, evalue, tb))


def _read_capped(fd: int) -> tuple[str, bool]:
    """捕捉ファイルを先頭から STREAM_LIMIT まで読む。超過分は捨てて truncated=True。"""
    os.lseek(fd, 0, os.SEEK_SET)
    chunks = []
    remaining = STREAM_LIMIT
    while remaining > 0:
        b = os.read(fd, min(65536, remaining))
        if not b:
            break
        chunks.append(b)
        remaining -= len(b)
    truncated = remaining == 0 and bool(os.read(fd, 1))
    return b"".join(chunks).decode("utf-8", errors="replace"), truncated


def _run(code: str, timeout: float) -> dict:
    err = None
    timed_out = False

    # 最後の式は notebook 風に repr を表示 (ipython なしで「df と打つと中身が出る」を再現)
    try:
        tree = ast.parse(code, "<cell>", "exec")
    except SyntaxError:
        return {
            "stdout": "",
            "error": _format_error(),
            "timed_out": False,
            "truncated": False,
        }
    last_expr = None
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        last_expr = ast.Expression(tree.body.pop().value)
        ast.copy_location(last_expr, last_expr.body)

    # stdout/stderr を fd レベルで捕捉する。Python の print だけでなく os.system /
    # subprocess が起こす子プロセスの出力 (fd 1/2 を継承) も同じファイルに集める。これを
    # しないと子プロセスが元の fd 1 = サービスとの framed protocol パイプに直接書き込み、
    # 通信が壊れてセッションごと落ちる (kernel は -u 起動で無バッファなので出力順も保つ)。
    sys.stdout.flush()
    sys.stderr.flush()
    saved_out, saved_err = os.dup(1), os.dup(2)
    capfd = os.open(CAP_PATH, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o600)
    os.dup2(capfd, 1)
    os.dup2(capfd, 2)

    signal.setitimer(signal.ITIMER_REAL, timeout)
    try:
        exec(compile(tree, "<cell>", "exec"), _G)
        if last_expr is not None:
            val = eval(compile(last_expr, "<cell>", "eval"), _G)
            if val is not None:
                print(repr(val))
    except _CellTimeout:
        timed_out = True
        err = f"CellTimeout: セルが {timeout:g} 秒の上限を超えたので中断した"
    except SystemExit:
        err = "SystemExit (無視した — プロセスは続いている)"
    except BaseException:
        err = _format_error()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        sys.stdout.flush()
        sys.stderr.flush()
        os.dup2(saved_out, 1)
        os.dup2(saved_err, 2)
        os.close(saved_out)
        os.close(saved_err)

    stdout, truncated = _read_capped(capfd)
    os.close(capfd)
    return {
        "stdout": stdout,
        "error": err,
        "timed_out": timed_out,
        "truncated": truncated,
    }


def _read_msg(f):
    hdr = f.read(4)
    if len(hdr) < 4:
        return None
    (n,) = struct.unpack(">I", hdr)
    data = b""
    while len(data) < n:
        chunk = f.read(n - len(data))
        if not chunk:
            return None
        data += chunk
    return json.loads(data)


def _write_msg(f, obj):
    b = json.dumps(obj).encode("utf-8")
    f.write(struct.pack(">I", len(b)))
    f.write(b)
    f.flush()


def main():
    fin = sys.stdin.buffer
    # サービスとの framed protocol を専用 fd に退避する。セル実行中は fd 1 を出力捕捉に
    # 使い回すため、元の stdout (protocol パイプ) を dup で確保しておく (_run 参照)。
    fout = os.fdopen(os.dup(1), "wb", buffering=0)
    while True:
        try:
            msg = _read_msg(fin)
        except KeyboardInterrupt:
            continue  # セル外のシグナルは無視
        if msg is None:
            break
        resp = _run(msg["code"], float(msg.get("timeout", 120)))
        _write_msg(fout, resp)


if __name__ == "__main__":
    main()
