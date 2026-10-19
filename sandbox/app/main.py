"""nucllei sandbox サービス: nsjail で隔離した Python / shell 実行 API。

設計・セキュリティモデル・API 仕様は sandbox/README.md を参照。
"""

import asyncio
import base64
import binascii
import errno
import hmac
import json
import os
import re
import stat
import struct
import subprocess
import time
import uuid
from pathlib import Path
from typing import Literal, NamedTuple, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.datastructures import Headers

WORKSPACES = Path(os.environ.get("SANDBOX_WORKSPACES", "/workspaces"))
IMAGES = WORKSPACES / ".images"
VENV_BIN = "/opt/venv/bin"
JAIL_UID = 65534

# ユーザー単位 quota: 各 workspace は sparse な loopback ext4 (見かけ QUOTA_GB、
# 実消費は書いた分だけ、discard で削除分は解放)。sandbox/README.md 参照。
QUOTA_GB = int(os.environ.get("SANDBOX_QUOTA_GB", "10"))
try:
    QUOTA_OVERRIDES = {
        str(k): int(v)
        for k, v in json.loads(os.environ.get("SANDBOX_QUOTA_OVERRIDES", "{}")).items()
    }
except Exception:
    QUOTA_OVERRIDES = {}

TIMEOUT_DEFAULT_S = int(os.environ.get("SANDBOX_TIMEOUT_DEFAULT_S", "120"))
TIMEOUT_MAX_S = int(os.environ.get("SANDBOX_TIMEOUT_MAX_S", "600"))
MEM_MB = int(os.environ.get("SANDBOX_MEM_MB", "4096"))
# 単一ファイルの rlimit_fsize。ディスク総量の本来の上限は per-user quota (ext4 cap) 側なので、
# ここは quota を跨がない範囲で大きめにし、大きな成果物ファイルを妨げないようにする
FSIZE_MB = int(os.environ.get("SANDBOX_FSIZE_MB", "8192"))
MAX_CONCURRENCY = int(os.environ.get("SANDBOX_MAX_CONCURRENCY", "4"))
STREAM_LIMIT = int(os.environ.get("SANDBOX_STREAM_LIMIT_BYTES", str(512 * 1024)))
# sandbox の上限 `SANDBOX_FILE_MAX_SIZE_MB` (MB の整数、既定 1024): workspace に直接出し入れする
# 1 ファイルの上限。2 つの役目を持つ —
#   - ファイルブラウザのアップロード / ダウンロード (`/files/raw`、ストリーミング) と nucllei の
#     send_to_workspace(url=) の取り込み (同じく `/files/raw`) の上限そのもの
#   - nucllei がリクエストごとに `file_limit_bytes` で渡す上限 (添付の上限 ATTACHMENT_MAX_SIZE_MB —
#     `/files/read` `/files/write` の b64・`/exec` の imports) の**安全上限**:
#     渡された値とこの値の**小さいほう**に従う (`_file_limit`)。b64-in-JSON の経路はファイルサイズの
#     ~2.3 倍が service プロセスの RAM に載るので、真の無制限にはしない
# 値は /healthz (`file_max_size_mb`) と /metrics (`sandbox_file_max_size_bytes`) に出す (nucllei が
# 起動時に読む)。旧名 `SANDBOX_FILE_LIMIT_BYTES` は読まない (起動時に warning — `_startup`)。
FILE_MAX_SIZE_MB = int(os.environ.get("SANDBOX_FILE_MAX_SIZE_MB", "1024"))
FILE_LIMIT = FILE_MAX_SIZE_MB * 1024 * 1024
# 改名前の env。設定されていても値は読み替えない (旧データ互換を持たない方針の延長)
_OLD_FILE_LIMIT_ENV = "SANDBOX_FILE_LIMIT_BYTES"

# stateful run_python の常駐セッション (kernel.py)。TTL は kernel の nsjail time_limit でもある
# ので実効は「セッション開始から」の絶対上限 (idle 回収も同値。README「回収」参照)。
KERNEL_PATH = "/opt/app-src/kernel.py"
SESSION_TTL_S = int(os.environ.get("SANDBOX_SESSION_TTL_S", str(12 * 3600)))
SESSION_MAX = int(os.environ.get("SANDBOX_SESSION_MAX", "64"))
SESSION_GRACE_S = int(os.environ.get("SANDBOX_SESSION_GRACE_S", "5"))

# jail に read-only で見せるホスト (=コンテナ) 側パス
RO_BINDS = ["/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc", "/opt/venv"]

# 参照用データの「置き場」(#138)。**直下に在るものが、同じパスのまま jail へ read-only で
# bind される** — 配備側は compose の volume を `- /host/path/doc:/ext/doc:ro` と足すだけで、
# サービス側の設定 (env) は増やさない。guest 側 = コンテナ側のパスなので `host:guest` の
# 対応表を持たず、複数置いても名前順に並ぶだけで衝突しない。見るのは直下 1 階層だけ
# (「volume 1 本 = 1 エントリ」)。空 (= 既定) なら何も足さない。
# 置き場の名前は `/ext` (2026-10-03、ken 決定。それまでは `/mnt`): nucllei が同じ木を
# `DATA_DIR/ext` から URL `/ext/<rel>` で配信するので、**jail のパスと URL が同じ文字列**
# になる (`/ext/doc-static/X` は jail でも URL でも同じ物)。モデルが読み替えを要らない。
# 全ユーザーに同じものが見える点を含め、詳細は README「参照用データを jail に見せる」。
RO_MOUNT_ROOT = Path("/ext")
# jail 側の置き場のパス (= URL の `/ext`)。コンテナ側は `RO_MOUNT_ROOT` (本番は同じ `/ext`。
# テストは RO_MOUNT_ROOT だけを tmp に差し替える)
JAIL_EXT_ROOT = "/ext"

# 置き場の直下のうち、**リクエストごとに bind する**名前 (#152)。名前 → その下に要る
# セグメント数 (`groups` は `/ext/groups/<グループ名>` を 1 つずつ、`public` は `/ext/public`
# そのもの)。ここに挙げた名前は直下の自動 read-only bind (`_scan_ro_mount_root`) から外す —
# `/ext/groups` を丸ごと bind すると他グループが見えてしまうため。誰に何を rw / ro で見せるかは
# 呼び出し元 (nucllei) がユーザーの権限で決めて `mounts` で渡し、sandbox は ACL を解釈しない
# (パスとして安全か = この形・各セグメントが妥当・経路に symlink が無い、だけを見る)。
REQUEST_MOUNT_ROOTS = {"groups": 1, "public": 0}
# 1 リクエストの `mounts` の上限 (admin は全グループ rw なのでグループ数ぶん来る)
MAX_REQUEST_MOUNTS = 1024

# jail の / は tmpfs で新規なので /dev が無い。最低限のデバイスノードを bind する
# (これが無いと dd if=/dev/zero や /dev/urandom を使うコードが失敗する)
DEV_BINDS = ["/dev/null", "/dev/zero", "/dev/full", "/dev/random", "/dev/urandom"]

# quota 用 loopback で確保しうる loop デバイスのノード数。コンテナには udev が無く、
# loop-control が割り当てた番号のノードが自動生成されないため、起動時に先に mknod する
# (ホストの snapd 等が既に低番号を使うので余裕を持たせる)。
LOOP_NODE_COUNT = int(os.environ.get("SANDBOX_LOOP_NODES", "256"))

# jail 内で BLAS/OMP が使うスレッド数。64 コア機で既定 (=コア数) のままだと
# OpenBLAS がコア数ぶんのバッファを確保して rlimit_as を超え失敗する。加えて
# SGLang と CPU を食い合わないよう小さく固定する。
BLAS_THREADS = os.environ.get("SANDBOX_BLAS_THREADS", "4")

# jail 内に渡す環境変数 (これ以外は渡らない)
JAIL_ENV = {
    "PATH": f"{VENV_BIN}:/usr/local/bin:/usr/bin:/bin",
    "HOME": "/workspace",
    "LANG": "C.UTF-8",
    # jail は JAIL_ENV しか継承しないため TZ を明示的に渡す。無いと datetime/date が UTC に
    # なり日本運用で 9h ずれる (朝方は日付自体がずれる)。既定はコンテナ TZ (compose=Asia/Tokyo)。
    "TZ": os.environ.get("TZ", "Asia/Tokyo"),
    "TMPDIR": "/tmp",
    "MPLBACKEND": "Agg",
    "MPLCONFIGDIR": "/tmp/mpl",
    "MATPLOTLIBRC": "/opt/venv/matplotlibrc",
    # キャッシュ類は揮発の /tmp に逃がす。既定だと HOME=/workspace に fontconfig 等が
    # .cache を書き、永続 workspace (quota 対象) を汚し続ける
    "XDG_CACHE_HOME": "/tmp/.cache",
    "OMP_NUM_THREADS": BLAS_THREADS,
    "OPENBLAS_NUM_THREADS": BLAS_THREADS,
    "MKL_NUM_THREADS": BLAS_THREADS,
    "NUMEXPR_NUM_THREADS": BLAS_THREADS,
    "VECLIB_MAXIMUM_THREADS": BLAS_THREADS,
    # joblib の multiprocessing を無効化 (jail に /dev/shm が無く serial fallback の
    # 警告が毎回出るのを抑止。サンドボックスでは multiprocessing 無効が安全側でもある)
    "JOBLIB_MULTIPROCESSING": "0",
}

_USER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# ---------------------------------------------------------------------------
# API 認証 (呼び出し元と共有する 1 本のトークン。#132)
# ---------------------------------------------------------------------------
# この API は「到達した者が body に書いた user_id をそのまま名乗る」設計なので、到達 =
# 任意ユーザーとしてのコード実行・他ユーザー workspace の破棄と同義。ユーザー間の隔離
# (README「セキュリティモデル」) は user_id が正しいことを前提にしており、その前提を
# 担保するものが「届く経路が狭い」以外に無かった (= 下の認証を有効にしない配備は
# 今もその状態のまま)。
#
# 呼び出し元 (nucllei backend) と `SANDBOX_TOKEN` を env で持ち合い、
# `Authorization: Bearer <token>` で照合する。トークンが保証するのは「呼び出し元が
# nucllei であること」だけで、**user_id の正しさは依然として呼び出し元任せ** —
# nucllei を信頼境界とする、が本 fork の設計判断 (より強い紐付け = 署名付き user_id 等は
# 採らない。nucllei が落ちれば sandbox も同時に落ちる一体の出荷物であり、2 段目の
# 検証は運用コストに見合わない)。
#
# **opt-in** (2026-09-05 に fail-closed から変更、ken 判断): `SANDBOX_TOKEN` を
# **設定すれば認証が有効になり**、未設定なら**認証なしで従来どおり動く**。
# 「キーが無ければ元のまま」= 既存の配備・手元の docker run を token 設定なしで
# 壊さないことを優先した判断。
#   - トレードオフ (事実): 未設定の配備は #132 以前と同じ状態のまま = API に到達できた
#     者が body の `user_id` を名乗るだけで任意ユーザーとしてのコード実行・他ユーザー
#     workspace の破棄までできる。「設定し忘れ」は静かに無防備になる。
#   - そのため**未設定なら起動時に warning を 1 行出す** (`_startup`)。落としはしないが、
#     ログを見れば無認証で上がっていることが分かる状態にはしておく。
TOKEN = os.environ.get("SANDBOX_TOKEN", "").strip()

# **トークンが空かどうかの判定はここ 1 箇所だけ**。middleware の素通し判断も
# `_token_ok` の防御もこのフラグを見る (同じ「空」の意味を 2 箇所に持たせない)。
AUTH_ENABLED = bool(TOKEN)

# **トークンは ASCII 限定**。HTTP ヘッダの値は Starlette が latin-1 でデコードするため、
# 非 ASCII を含むトークンは UTF-8 で送っても受け側で別の文字列になり、**どう送っても
# 一致しない** = 全リクエストが 401 になる。倒れる側は安全だが「設定したのに全部 401」は
# 原因が分からない壊れ方なので、起動時に理由を名指しする (`_log_auth_state`)。
# 起動は止めない — opt-in の方針 (キーが無ければ元のまま) と、compose の crash-loop より
# ログ 1 行のほうが配備時に手が打てるため。推奨値 `openssl rand -hex 32` は常に ASCII。
TOKEN_IS_ASCII = TOKEN.isascii()

# 認証が有効なときに免除するパス。**認証有効なら既定は要認証** (パス単位の allow では
# なく deny がデフォルト) — endpoint を足したときに「書き忘れたら開く」ではなく
# 「書き忘れたら閉じる」側に倒す。
#   - `/healthz`: 配備側 compose の healthcheck が叩く。token を持たせると healthcheck の
#     コマンド行に平文で載り (`docker inspect` に出る)、値がずれただけで unhealthy になる。
#     返すのは真偽値 3 つだけでユーザーデータを含まない。
#   - `/metrics`: 配備側 Prometheus が scrape する (docs/health.md ②)。label なしの集約値
#     のみで user_id を含まない。認証すると scrape 側にも token 配布が要る。
# どちらも「同じ境界に居る監視系が無認証で読める」ことを前提にした設計で、機微は無い。
PUBLIC_PATHS = frozenset({"/healthz", "/metrics"})


def _token_ok(authorization: str) -> bool:
    """`Authorization: Bearer <token>` を定数時間で照合する。

    **認証が有効なときだけ意味を持つ関数**。認証が無効 (TOKEN 未設定) の状態では
    middleware がここに来る前に素通しするので呼ばれないが、呼ばれても「一致」を
    返さない — 空トークン (`Authorization: Bearer `) が正解になる経路を作らないため。
    空判定そのものは `AUTH_ENABLED` の 1 箇所だけが持つ。
    """
    if not AUTH_ENABLED:
        return False
    scheme, _, presented = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return False
    # **トークンは実質 ASCII 限定** (`TOKEN_IS_ASCII` 参照)。ここで bytes に直すのは
    # `hmac.compare_digest` が非 ASCII の str で TypeError を投げるのを避けるためだけで、
    # 非 ASCII トークンを通せるようにする効果は無い — HTTP ヘッダは Starlette が
    # latin-1 でデコードするので、UTF-8 で送られた値はこの時点で別物になっている
    # (`Bearer tokén` → `'Bearer tokÃ©n'`) 。倒れる側は安全 (常に 401)。
    return hmac.compare_digest(presented.strip().encode(), TOKEN.encode())


def _log_auth_state() -> None:
    """起動時に認証の on/off をログ 1 行で残す (`_startup` から呼ぶ)。

    opt-in (#132、2026-09-05 改訂) では未設定でも起動するので、**黙って無防備**に
    ならないよう状態だけは必ずログに出す。無認証だと何ができてしまうかの説明は
    ログに書かない — README「セキュリティモデル」が単一情報源。

    有効なのに**非 ASCII のトークン**という「設定したのに全要求 401」も同じ理由で
    名指しする (`TOKEN_IS_ASCII`)。どちらも起動は止めない。
    """
    if AUTH_ENABLED:
        print("[sandbox] API 認証: 有効 (SANDBOX_TOKEN)", flush=True)
        if not TOKEN_IS_ASCII:
            print(
                "[sandbox] WARNING: SANDBOX_TOKEN に非 ASCII 文字が含まれます。"
                "HTTP ヘッダは latin-1 で解釈されるため一致せず、全要求が 401 になります"
                " — ASCII のトークンにしてください (例: openssl rand -hex 32)",
                flush=True,
            )
    else:
        print(
            "[sandbox] WARNING: API 認証は無効です (SANDBOX_TOKEN 未設定)。"
            "設定すれば全 endpoint で有効になります"
            " — 影響は sandbox/README.md セキュリティモデル",
            flush=True,
        )


class _RequireToken:
    """認証が有効なとき、全リクエストにトークンを要求する ASGI middleware。

    **`AUTH_ENABLED` が False (= `SANDBOX_TOKEN` 未設定) なら丸ごと素通し** —
    「キーが無ければ元のまま」(2026-09-05)。有効なときの免除は PUBLIC_PATHS のみ。

    **素の ASGI で書く** (`@app.middleware("http")` = BaseHTTPMiddleware を使わない):
    あちらは下流に渡す `receive` を差し替えるため、`/exec` `/exec_cell` が lock 待ちの
    間に使う `request.is_disconnected()` (= 切れた要求を実行しない仕組み) の挙動を
    巻き込みうる。ここは scope の header を見て通すか 401 を返すかだけなので、
    receive/send は素通しでよい。

    **守備範囲は HTTP のみ**: 認証が有効なら未知パスも含めて既定 deny なので endpoint を
    足しても書き忘れで開かないが、それは HTTP の話。この API に WebSocket は無い (2026-09-04
    時点で `@app.websocket` はゼロ) — 足すときはここも通るように直すこと。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            AUTH_ENABLED
            and scope["type"] == "http"
            and scope["path"] not in PUBLIC_PATHS
        ):
            auth = Headers(scope=scope).get("authorization", "")
            if not _token_ok(auth):
                # トークン値はログにもレスポンスにも出さない
                # (どこまで一致したかも返さない)
                print(
                    f"[sandbox] 401 {scope.get('method')} {scope['path']}",
                    flush=True,
                )
                res = JSONResponse({"detail": "unauthorized"}, status_code=401)
                await res(scope, receive, send)
                return
        await self.app(scope, receive, send)


app = FastAPI()
app.add_middleware(_RequireToken)


_sem = asyncio.Semaphore(MAX_CONCURRENCY)
_user_locks: dict[str, asyncio.Lock] = {}
# workspace の loop mount は本来張りっぱなしで単調増加する。idle-detach reaper で umount して
# budget を非単調化する (sandbox/docs/health.md の ①)。_ws_registry は「現在 mount 済みの
# workspace の最終利用時刻」。_mount_locks は mount/remount/umount/削除を user 単位で直列化
# (reaper と新規 remount の競合回避 + 初回同時二重 mount の防止)。
_mount_locks: dict[str, asyncio.Lock] = {}
_ws_registry: dict[str, float] = {}  # user_id -> last_used (monotonic)
WS_IDLE_TTL_S = int(os.environ.get("SANDBOX_WS_IDLE_TTL_S", "3600"))
# workspace の保持期限 (日)。0 = 消さない (既定。本番は 40TB NAS なので容量より復帰時の
# 「消えていた」事故を避ける側に倒す — ken 決定 2026-10-03)。>0 なら最終利用から N 日経った
# 未 mount の .img を reaper が unlink する (`reap_stale_workspaces`)。最終利用時刻は
# 再起動を跨いで残す必要があるので、`.img` の隣の `<user_id>.last_used` (image の外 = 未 mount
# でも読める) を利用のたびに touch する。`.img` の mtime は marker が無い場合の一回限りの代替
# (loop 経由の書込で動くかはカーネル任せなので頼らない)。
WS_RETENTION_DAYS = int(os.environ.get("SANDBOX_WORKSPACE_RETENTION_DAYS", "0"))
WS_RETENTION_SWEEP_S = 3600
# `imports/` `exports/` は nucllei Files のキャッシュ (原本は Files 側。#149)。idle 回収で
# umount する直前に、直下のエントリのうち max(mtime, ctime) から N 日過ぎたものを丸ごと消す
# (`_sweep_cache_dirs`)。0 以下 = 消さない。
# 消えたものは `send_to_workspace(file_id)` で戻せる。`memory/` 等の他ディレクトリには触らない。
# 値は `/exec` `/exec_cell` `/files/write` の応答 `cache_ttl_days` でも返す (0 = 消さない) —
# nucllei が取り込みの tool 出力で「YYYY-MM-DD 以降に消える」を知らせるため。
# `exports/` は実行後の自動回収 (チャットへの自動添付) を廃止した (2026-10-19、ken 決定)。
# 新しい workspace には作らず、ファイルブラウザからも消せる (`PROTECTED_DIRS` から外す) が、
# 既存の workspace に残っている写しを期限で消すため掃除の対象 (`CACHE_DIRS`) には残す。
CACHE_TTL_DAYS = int(os.environ.get("SANDBOX_CACHE_TTL_DAYS", "30"))
CACHE_DIRS = ("imports", "exports")
# `trash/` = 消す代わりに移す置き場 (llens の運用も `/files/delete` の /workspace での既定も、
# trash/ の直下へ mv)。期限は同じ `SANDBOX_CACHE_TTL_DAYS`。3 つの置き場とも判定単位は
# **直下の各エントリ**、時刻はその lstat の max(mtime, ctime) (`_sweep_entries`)。
TRASH_DIR = "trash"
# workspace 直下で sandbox が用意し、ファイルブラウザから消させない・改名させないもの
PROTECTED_DIRS = ("imports", TRASH_DIR)
CACHE_TTL_REPORTED = max(CACHE_TTL_DAYS, 0)


# ---------------------------------------------------------------------------
# workspace ヘルパ
# ---------------------------------------------------------------------------
def _run(cmd: list[str], ok=(0,)):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode not in ok:
        raise RuntimeError(
            f"{cmd[0]} rc={p.returncode}: {(p.stderr or p.stdout).strip()[:200]}"
        )
    return p


def _ensure_loop_nodes() -> None:
    """/dev/loop0..N のノードを先に作る。privileged 前提 (mknod, major 7)。
    コンテナ起動時に存在する loop ノードは host が使用中の低番号のみで、mount -o loop が
    新番号を割り当てても対応ノードが無く 'No such file or directory' で失敗するため。"""
    created = 0
    for i in range(LOOP_NODE_COUNT):
        path = f"/dev/loop{i}"
        if os.path.exists(path):
            continue
        try:
            os.mknod(path, stat.S_IFBLK | 0o660, os.makedev(7, i))
            created += 1
        except FileExistsError:
            pass
        except OSError:
            break  # mknod 不可 (非 privileged 等) なら諦める
    if created:
        print(f"[sandbox] created {created} loop device node(s)", flush=True)


def _loop_available() -> bool:
    """loop ドライバが実際に使えるか。/dev/loop-control の open が ENODEV なら
    ドライバ不在 = 全 workspace mount が失敗する状態。典型はホストのカーネル
    パッケージ更新が稼働中カーネルの module ツリーを消したまま再起動していない場合
    (2026-08-10 実障害: node は残るので存在チェックでは検出できない。開いて確かめる)。"""
    try:
        fd = os.open("/dev/loop-control", os.O_RDWR)
        os.close(fd)
        return True
    except OSError:
        return False


def _provision_fresh(img: Path, gb: int) -> None:
    """空の sparse ext4 を atomic に用意する (tmp に mkfs → rename)。
    mkfs 途中で失敗しても本番名の .img には半生成物を残さない (次回 img.exists() が
    True になって壊れた fs を掴む poison を防ぐ)。"""
    tmp = img.parent / (img.name + ".new")
    tmp.unlink(missing_ok=True)
    try:
        with open(tmp, "wb") as f:
            f.truncate(gb * 1024**3)  # sparse: 実ブロックは書込時のみ確保
        _run(
            [
                "mkfs.ext4",
                "-q",
                "-F",
                "-m",
                "0",
                "-O",
                "^has_journal",
                "-E",
                "lazy_itable_init=1,lazy_journal_init=1",
                str(tmp),
            ]
        )
        os.replace(tmp, img)  # 同一 fs 内 atomic rename
    finally:
        tmp.unlink(missing_ok=True)


def _quarantine(img: Path) -> None:
    """破損 .img を退避リネームする (fresh 化の前段)。データは失われるが恒久 500 を回避。"""
    dest = img.parent / f"{img.name}.corrupt-{int(time.time())}"
    try:
        os.replace(img, dest)
        print(
            f"[sandbox] quarantined corrupt image {img.name} -> {dest.name}", flush=True
        )
    except FileNotFoundError:
        pass


def _ensure_workspace(user_id: str) -> Path:
    """ユーザーの workspace を用意して返す (同期)。
    /workspaces/<user_id> に sparse loopback ext4 をマウントする。
    - 初回: quota GB の sparse イメージを作成 + mkfs (journal 無し・lazy init で
      実消費を最小化)。以降マウントするだけ。
    - コンテナ再起動後: イメージは volume に残るので e2fsck して再マウント。
    journal 無し ext4 は停電等 unclean shutdown 後に e2fsck が自動修復不能 (rc>=2) に
    なりうる。その場合・mkfs 失敗の半生成物は fresh 化フォールバックで恒久 500 を避ける。
    ブロッキング処理なので呼び出し側は asyncio.to_thread で回す。"""
    if not _USER_ID_RE.match(user_id):
        raise HTTPException(400, f"invalid user_id: {user_id!r}")
    IMAGES.mkdir(parents=True, exist_ok=True)
    img = IMAGES / f"{user_id}.img"
    ws = WORKSPACES / user_id
    ws.mkdir(parents=True, exist_ok=True)

    if not os.path.ismount(ws):
        gb = QUOTA_OVERRIDES.get(user_id, QUOTA_GB)
        if not img.exists():
            _provision_fresh(img, gb)
        else:
            try:
                _run(["e2fsck", "-p", str(img)], ok=(0, 1))  # 1 = 自動修復した
            except RuntimeError as e:
                # rc>=2 = 自動修復不能 (journal 無し + unclean)。恒久 500 を避け fresh 化。
                print(
                    f"[sandbox] e2fsck unrecoverable for {img.name}: {e}; reprovisioning",
                    flush=True,
                )
                _quarantine(img)
                _provision_fresh(img, gb)
        # discard = jail 内で消したファイルの領域をホスト側へ即返す (実消費を抑える)。
        # mount 失敗 (loop 枯渇 / disk 満杯) はここで RuntimeError → _workspace が 503 に変換。
        _run(["mount", "-o", "loop,discard", str(img), str(ws)])
        os.chown(ws, JAIL_UID, JAIL_UID)

    for d in (ws / "imports", ws / TRASH_DIR, ws / ".exec"):
        if not d.exists():
            _mkdir_owned(str(d))
    return ws


def _mount_lock(user_id: str) -> asyncio.Lock:
    return _mount_locks.setdefault(user_id, asyncio.Lock())


def _last_used_marker(user_id: str) -> Path:
    """最終利用時刻を持つ sidecar。`.img` と同じ置き場 (= image の外) なので未 mount でも読める。"""
    return IMAGES / f"{user_id}.last_used"


def _touch_last_used(user_id: str) -> None:
    """利用のたびに marker の mtime を今にする (無ければ作る)。失敗しても利用は止めない
    (保持期限の判定が image mtime 代替に落ちるだけ)。"""
    try:
        _last_used_marker(user_id).touch()
    except OSError as e:
        print(f"[sandbox] last_used touch failed for {user_id}: {e}", flush=True)


async def _workspace(user_id: str) -> Path:
    # _mount_lock: 初回同時二重 mount と reaper の umount を直列化。SessionManager.get() は
    # SM._lock を保持したままこれを呼ぶので、ロック順は常に SM._lock -> _mount_lock (逆転なし)。
    async with _mount_lock(user_id):
        try:
            ws = await asyncio.to_thread(_ensure_workspace, user_id)
        except RuntimeError as e:
            # loop 枯渇 / disk 満杯 / mount 不能 = 容量系。障害の 500 と区別して 503 で返す。
            raise HTTPException(
                503,
                "sandbox capacity full (同時利用ユーザー数の上限)。しばらく後に再試行してください",
            ) from e
        # lock 内で touch: reap_stale_workspaces が lock を取った時点で必ず最新の時刻を見る
        _touch_last_used(user_id)
    _ws_registry[user_id] = time.monotonic()  # mount 完了後 event loop 上で touch
    return ws


def _safe_path(ws: Path, rel: str) -> Path:
    """workspace 内に閉じたパスへ解決。外に出るパスは拒否。"""
    p = (ws / rel).resolve()
    if p != ws.resolve() and ws.resolve() not in p.parents:
        raise HTTPException(400, f"path escapes workspace: {rel!r}")
    return p


def _user_lock(user_id: str) -> asyncio.Lock:
    if user_id not in _user_locks:
        _user_locks[user_id] = asyncio.Lock()
    return _user_locks[user_id]


# 渡された上限が安全上限を超えていたときの warning を、値が変わったときだけ出すための記録
_file_limit_warned: Optional[int] = None


def _file_limit(requested: Optional[int]) -> int:
    """このリクエストで効く 1 ファイルの上限: 渡された値 (nucllei の ATTACHMENT_MAX_SIZE_MB) と
    sandbox の上限 (FILE_LIMIT) の小さいほう。渡されない (nucllei 側が無制限・`/files/raw` の
    ファイルブラウザと url= の取り込み) なら sandbox の上限。
    渡された値が sandbox の上限を超えていれば warning を 1 行 (値が変わったときだけ)。"""
    global _file_limit_warned
    if requested is None or requested <= 0:
        return FILE_LIMIT
    if requested > FILE_LIMIT and requested != _file_limit_warned:
        _file_limit_warned = requested
        print(
            f"[sandbox] WARNING: 呼び出し元の上限 file_limit_bytes={requested} が sandbox の上限 "
            f"SANDBOX_FILE_MAX_SIZE_MB={FILE_MAX_SIZE_MB} を超えている — sandbox の上限が勝つ "
            "(揃えるなら SANDBOX_FILE_MAX_SIZE_MB を上げる。base64-in-JSON で約 2.3 倍の"
            "メモリを使う)",
            flush=True,
        )
    return min(requested, FILE_LIMIT)


def _too_large(size: int, limit: int) -> HTTPException:
    # 文面 `file too large: N bytes (limit L)` は nucllei が読む (サイズと効いた上限を拾う)
    return HTTPException(413, f"file too large: {size} bytes (limit {limit})")


# ---------------------------------------------------------------------------
# nsjail 実行
# ---------------------------------------------------------------------------
def _scan_ro_mount_root() -> tuple[list[str], list[str]]:
    """`RO_MOUNT_ROOT` 直下を見て (bind するパス, 飛ばしたパス) を名前順で返す (#138)。

    jail を組むたびに見る (`RO_BINDS` の `os.path.exists` と同じ流儀)。中身が変わるのは
    実質コンテナ起動時だけなので、キャッシュを持って実際とずれる余地を作らない。

    飛ばすのは dot 始まりと、**nsjail に渡すと jail の起動ごと失敗するもの** = 全ユーザーの
    全実行が落ちるので警告に回す: 実体を辿れないもの (壊れた symlink) と、名前に ":" を
    含むもの (nsjail の `src:dst` 区切り。dst を相対パスと解釈して落ちる)。名前は配備側が
    自由に付けられる = 外部入力なので、ここで防ぐ。
    """
    try:
        names = sorted(os.listdir(RO_MOUNT_ROOT))
    except OSError:  # 置き場そのものが無い / 読めない = 何も足さない
        return [], []
    binds, skipped = [], []
    for name in names:
        if name.startswith(".") or name in REQUEST_MOUNT_ROOTS:
            # リクエストごとに bind する置き場 (#152) は丸ごとは出さない
            continue
        p = str(RO_MOUNT_ROOT / name)
        ok = ":" not in name and os.path.exists(p)
        (binds if ok else skipped).append(p)
    return binds, skipped


def _log_ro_mounts() -> None:
    """起動時に「jail に何が余分に見えているか」をログ 1 行で残す (`_startup` から呼ぶ)。

    配備側が volume を足すだけで増える口で、サービスの env を見ても分からないため。
    """
    binds, skipped = _scan_ro_mount_root()
    if binds:
        print(f"[sandbox] 追加 read-only bind: {' '.join(binds)}", flush=True)
    for p in skipped:
        print(
            f"[sandbox] WARNING: {p} は bind しません "
            "(実体を辿れない = 壊れた symlink か、名前に ':' を含む)",
            flush=True,
        )
    present = [
        n
        for n in sorted(REQUEST_MOUNT_ROOTS)
        if stat.S_ISDIR(_lstat_mode(RO_MOUNT_ROOT / n))
    ]
    if present:
        print(
            "[sandbox] リクエストごとの bind (呼び出し元が mounts で指定): "
            + " ".join(f"{JAIL_EXT_ROOT}/{n}" for n in present),
            flush=True,
        )


# ---------------------------------------------------------------------------
# リクエストごとの bind (`/ext/groups/<g>` `/ext/public`。#152)
# ---------------------------------------------------------------------------
class MountIn(BaseModel):
    """呼び出し元が決めた 1 つの bind。`path` は jail のパス (`/ext/groups/<g>` か `/ext/public`)、
    `rw` は書けるか。見せないものは渡さない (= jail に存在しない)。"""

    path: str
    rw: bool = False


# パスのセグメントとして拒否する Unicode 一般カテゴリ (nucllei の `utils/ext_acl.py` /
# `utils/group_names.py` と同じ規則): 制御文字・書式文字・サロゲート・行 / 段落区切り
_BAD_SEGMENT_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})


def _safe_segment(seg) -> bool:
    """bind の 1 セグメント (グループ名) として安全か。グループ名はそのままディレクトリ名に
    なるので、ここで弾くのは「パスとして壊れるもの」だけ: 空・前後の空白・`.` 始まり (`.` `..`
    を含む)・`/` `\\` `:` (nsjail の `src:dst` 区切り)・制御 / 書式文字・UTF-8 で 255 バイト超。"""
    import unicodedata

    if not isinstance(seg, str) or not seg or seg != seg.strip():
        return False
    if seg.startswith(".") or any(c in "/\\:" for c in seg):
        return False
    if any(unicodedata.category(c) in _BAD_SEGMENT_CATEGORIES for c in seg):
        return False
    try:
        return len(seg.encode("utf-8")) <= 255
    except UnicodeEncodeError:
        return False


def _validate_mounts(mounts: list[MountIn]) -> list[tuple[str, bool]]:
    """`mounts` を検証して `[(jail のパス, rw)]` (パス順・重複なし) にする。不正は 400。

    受けるのは `REQUEST_MOUNT_ROOTS` の形だけ (`/ext/groups/<g>` と `/ext/public`)。
    `/ext/groups` そのもの・`/ext/doc` 等の自動 bind の名前・深い位置は受けない。
    同じパスが rw と ro で 2 回来たら (呼び出し元の矛盾) 400。"""
    if len(mounts) > MAX_REQUEST_MOUNTS:
        raise HTTPException(400, f"too many mounts (> {MAX_REQUEST_MOUNTS})")
    out: dict[str, bool] = {}
    prefix = JAIL_EXT_ROOT + "/"
    for m in mounts:
        p = m.path
        if not isinstance(p, str) or not p.startswith(prefix):
            raise HTTPException(400, f"invalid mount path: {p!r}")
        segs = p[len(prefix) :].split("/")
        depth = REQUEST_MOUNT_ROOTS.get(segs[0])
        if depth is None or len(segs) != 1 + depth:
            raise HTTPException(400, f"invalid mount path: {p!r}")
        if not all(_safe_segment(s) for s in segs):
            raise HTTPException(400, f"invalid mount path: {p!r}")
        if p in out and out[p] != m.rw:
            raise HTTPException(400, f"conflicting mount: {p!r}")
        out[p] = bool(m.rw)
    return sorted(out.items())


def _lstat_mode(p) -> int:
    try:
        return os.lstat(p).st_mode
    except OSError:
        return 0


def _mount_source(jail_path: str, create: bool) -> Optional[Path]:
    """jail のパス `/ext/<...>` に対応するコンテナ側のディレクトリ (`RO_MOUNT_ROOT/<...>`)。
    **経路のどこにも symlink を許さない** (各セグメントを lstat。置き場の根 `RO_MOUNT_ROOT`
    だけは配備側の物なので辿ってよい)。無ければ None — ただし `create` (rw の bind) なら
    末端の 1 段 (グループのディレクトリ) だけ作って jail のユーザーの物にする (所属グループの
    置き場が初回から書けるように。`/ext/groups` `/ext/public` 自体は作らない = 配備側が置く)。
    blocking。"""
    segs = jail_path[len(JAIL_EXT_ROOT) + 1 :].split("/")
    cur = RO_MOUNT_ROOT
    for i, seg in enumerate(segs):
        cur = cur / seg
        mode = _lstat_mode(cur)
        if mode == 0 and create and i == len(segs) - 1 and i > 0:
            try:
                _mkdir_owned(str(cur))
                print(f"[sandbox] created {jail_path}", flush=True)
            except FileExistsError:
                pass
            except OSError as e:
                print(f"[sandbox] cannot create {jail_path}: {e!r}", flush=True)
                return None
            mode = _lstat_mode(cur)
        if not stat.S_ISDIR(mode):
            # 無い・symlink・ファイル。symlink は辿らない (他グループへ向けた差し替えを bind しない)
            if stat.S_ISLNK(mode):
                print(
                    f"[sandbox] WARNING: {cur} は symlink なので bind しません",
                    flush=True,
                )
            return None
    return cur


def _prepare_mounts(validated: list[tuple[str, bool]]) -> list[tuple[str, str, bool]]:
    """検証済みの `[(jail のパス, rw)]` を nsjail に渡す `[(コンテナ側, jail 側, rw)]` にする。
    実体が無いもの (置き場が無い配備・ro で未作成) は黙って飛ばす — nsjail は存在しない src で
    jail の起動ごと失敗するため。blocking (thread で呼ぶ)。"""
    binds = []
    for jail_path, rw in validated:
        src = _mount_source(jail_path, create=rw)
        if src is not None:
            binds.append((str(src), jail_path, rw))
    return binds


def _mounts_fingerprint(binds: list[tuple[str, str, bool]]) -> str:
    """常駐 kernel が作られたときの jail の見え方の指紋 (jail のパスと rw だけ)。
    違うリクエストが来たら kernel を作り直す (所属・権限が変わったら古い bind を持ち続けない)。"""
    import hashlib

    data = json.dumps([[dst, rw] for _src, dst, rw in binds])
    return hashlib.sha256(data.encode()).hexdigest()[:16]


def _jail_base(ws: Path, wall_s: int, binds=()) -> list[str]:
    """stateless exec と常駐 kernel で共通の nsjail 引数。wall_s は --time_limit
    (プロセスの生存上限。stateless は実行秒、kernel はセッション TTL のバックストップ)。
    `binds` はリクエストごとの bind `[(コンテナ側, jail 側, rw)]` (`_prepare_mounts`)。"""
    cmd = [
        "nsjail",
        "--mode",
        "o",
        "--really_quiet",
        "--hostname",
        "sandbox",
        # user namespace は作らず実 uid 65534(nobody) で動かす。これで bind した
        # workspace (実 65534 所有) が書ける。ユーザー間隔離は mount ns のみ (sandbox/README.md)
        "--disable_clone_newuser",
        # netns 内で lo を上げるには CAP_NET_ADMIN が要るが userns 無しでは持たない。
        # そもそも通信は全遮断が目的なので lo ごと上げない (完全に無ネットワーク)
        "--iface_no_lo",
        "--user",
        str(JAIL_UID),
        "--group",
        str(JAIL_UID),
        "--cwd",
        "/workspace",
        "--time_limit",
        str(wall_s),
        "--rlimit_as",
        str(MEM_MB),
        "--rlimit_fsize",
        str(FSIZE_MB),
        "--rlimit_nproc",
        "128",
        "--rlimit_nofile",
        "256",
        # ルート tmpfs を 16MB に固定。既定 (未指定) だと RAM の半分 (このホストで 756GB)
        # が df に出て「その容量が使える」と誤解される。ルートは元々マウント点を載せる
        # 土台で書き込みには使わない (実書込先は /workspace と /tmp)。明示 mount にすると
        # read-only ではなくなるが 16MB・実行ごと使い捨て・jail 隔離で無害。RO 保証が要る
        # システム系は下の bindmount_ro で別途担保される。bind より前に置く必要がある。
        "--mount",
        "none:/:tmpfs:size=16777216",
        "--bindmount",
        f"{ws}:/workspace",
        "--mount",
        "none:/tmp:tmpfs:size=268435456",
    ]
    for p in RO_BINDS:
        if os.path.exists(p):
            cmd += ["--bindmount_ro", p]
    # ビルド時 fc-cache で焼いた system フォントキャッシュ (/var は非 bind なので個別に)。
    # これで fontconfig は user キャッシュを書かずに済む
    if os.path.exists("/var/cache/fontconfig"):
        cmd += ["--bindmount_ro", "/var/cache/fontconfig"]
    if os.path.exists(KERNEL_PATH):
        cmd += ["--bindmount_ro", KERNEL_PATH]
    # 置き場 (RO_MOUNT_ROOT) に在るものを同じパスのまま read-only で (#138)。
    # nsjail は `src` だけ渡すと dst = src なので、`src:dst` の対応表は要らない
    for p in _scan_ro_mount_root()[0]:
        cmd += ["--bindmount_ro", p]
    # リクエストごとの bind (#152)。src と dst が同じなら src だけ (本番は常にこちら)
    for src, dst, rw in binds:
        cmd += [
            "--bindmount" if rw else "--bindmount_ro",
            src if src == dst else f"{src}:{dst}",
        ]
    for p in DEV_BINDS:
        if os.path.exists(p):
            cmd += ["--bindmount", p]  # rw (デバイスノード)
    for k, v in JAIL_ENV.items():
        cmd += ["--env", f"{k}={v}"]
    return cmd


def _nsjail_cmd(ws: Path, timeout_s: int, argv: list[str], binds=()) -> list[str]:
    # stateless: プロセス = 1 実行。time_limit と rlimit_cpu を実行秒に張る。
    cmd = _jail_base(ws, timeout_s, binds)
    cmd += ["--rlimit_cpu", str(timeout_s)]
    return cmd + ["--"] + argv


def _kernel_nsjail_cmd(ws: Path, binds=()) -> list[str]:
    # stateful: 常駐 kernel。生存上限はセッション TTL (バックストップ)。per-cell の
    # timeout は kernel が SIGALRM で内部処理するので rlimit_cpu は張らない。
    cmd = _jail_base(ws, SESSION_TTL_S, binds)
    return cmd + ["--", f"{VENV_BIN}/python", "-u", KERNEL_PATH]


async def _read_capped(stream: asyncio.StreamReader) -> tuple[bytes, bool]:
    """上限まで保持し、以降は捨てながら EOF まで排水 (メモリ膨張防止)。"""
    buf = bytearray()
    truncated = False
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            return bytes(buf), truncated
        room = STREAM_LIMIT - len(buf)
        if room > 0:
            buf += chunk[:room]
        if len(chunk) > room:
            truncated = True


# ---------------------------------------------------------------------------
# 常駐セッション (stateful run_python)
# ---------------------------------------------------------------------------
class Session:
    """(user_id, chat_id) ごとの常駐 kernel プロセス。framed protocol で通信。"""

    def __init__(self, user_id: str, chat_id: str, proc, mounts_fp: str = ""):
        self.user_id = user_id
        self.chat_id = chat_id
        self.proc = proc
        # 起動時の jail の見え方 (`_mounts_fingerprint`。#152)。違う見え方の要求が来たら作り直す
        self.mounts_fp = mounts_fp
        self.lock = asyncio.Lock()  # 1 セッション = 直列実行
        self.last_used = time.monotonic()
        self.alive = True

    def _send(self, code: str, timeout: float):
        b = json.dumps({"code": code, "timeout": timeout}).encode("utf-8")
        self.proc.stdin.write(struct.pack(">I", len(b)) + b)
        self.proc.stdin.flush()

    def _recv(self) -> Optional[dict]:
        hdr = self.proc.stdout.read(4)
        if len(hdr) < 4:
            return None
        (n,) = struct.unpack(">I", hdr)
        data = b""
        while len(data) < n:
            chunk = self.proc.stdout.read(n - len(data))
            if not chunk:
                return None
            data += chunk
        return json.loads(data)

    def kill(self):
        self.alive = False
        try:
            self.proc.kill()
        except Exception:
            pass


class SessionManager:
    def __init__(self):
        self._sessions: dict[tuple[str, str], Session] = {}
        # reaper / evict が黙って落とした (user, chat) の印。次の get で「前世代あり」=
        # session_reset を返すため。無いと 12h 満了・SESSION_MAX 追い出しが「初回」と
        # 区別できず、モデルは NameError で初めて気付く。drop() は呼び元が自分で
        # session_reset を返すので印は残さない (二重通知になる)。
        self._tombstones: set[tuple[str, str]] = set()
        self._lock = asyncio.Lock()

    def _key(self, user_id, chat_id):
        return (user_id, chat_id)

    async def get(
        self, user_id: str, chat_id: str, binds=(), mounts_fp: str = ""
    ) -> tuple[Session, bool]:
        """既存 or 新規の Session を返す。2 要素目 = 新規作成 (reset) したか。

        `binds` / `mounts_fp` はリクエストごとの bind とその指紋 (#152)。生きている session でも
        指紋が違えば (グループの所属・admin 権限が変わった等) 落として作り直し、reset を返す —
        常駐 kernel は起動時の bind を持ち続けるので、そのままだと外れたグループが見え続ける。"""
        async with self._lock:
            key = self._key(user_id, chat_id)
            sess = self._sessions.get(key)
            alive = sess is not None and sess.alive and sess.proc.poll() is None
            if alive and sess.mounts_fp == mounts_fp:
                return sess, False
            # 死んでいた/初回 → 掃除して作り直し
            if sess is not None:
                sess.kill()
                self._sessions.pop(key, None)
            # 前世代があった = リセット (dict に残っていた死骸、または reaper が残した印)
            was_reset = sess is not None or key in self._tombstones
            self._tombstones.discard(key)
            await self._reap_if_full_locked()
            ws = await _workspace(user_id)
            proc = await asyncio.to_thread(
                lambda: subprocess.Popen(
                    _kernel_nsjail_cmd(ws, binds),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
            )
            sess = Session(user_id, chat_id, proc, mounts_fp)
            self._sessions[key] = sess
            return sess, was_reset

    async def _reap_if_full_locked(self):
        """SESSION_MAX を超えるなら最も長く idle なものを 1 つ落とす。"""
        if len(self._sessions) < SESSION_MAX:
            return
        victim_key = min(self._sessions, key=lambda k: self._sessions[k].last_used)
        self._sessions.pop(victim_key).kill()
        self._tombstones.add(victim_key)

    async def drop(self, sess: Session):
        async with self._lock:
            key = self._key(sess.user_id, sess.chat_id)
            if self._sessions.get(key) is sess:
                self._sessions.pop(key, None)
        sess.kill()

    async def reap_idle(self):
        now = time.monotonic()
        async with self._lock:
            dead = [
                k
                for k, s in self._sessions.items()
                if now - s.last_used > SESSION_TTL_S or s.proc.poll() is not None
            ]
            for k in dead:
                self._sessions.pop(k).kill()
                self._tombstones.add(k)
        return len(dead)


_sessions = SessionManager()


def _has_live_session(user_id: str) -> bool:
    """user に生存中の kernel session があるか。SM._lock は取らない (reaper のロック順逆転を
    避けるため)。await を挟まないので event loop に対して atomic に読める。"""
    for (u, _c), s in _sessions._sessions.items():
        if u == user_id and s.alive and s.proc.poll() is None:
            return True
    return False


def _age_time(st) -> float:
    """期限判定の時刻 = max(mtime, ctime)。mv (rename) は mtime を変えないが ctime は今にする
    (Linux の ext4 / tmpfs で確認済み — `test_retention.py` の `test_rename_updates_ctime`)。
    ctime は利用者が utime で過去にできないので、「古い物を今移した」は新しいと見なされる。"""
    return max(st.st_mtime, st.st_ctime)


def _tree_bytes(name: str, dfd: int) -> int:
    """`dfd` の中のディレクトリ `name` 配下の通常ファイルの合計バイト数 (symlink は辿らない)。"""
    total = 0
    for _root, _dirs, fnames, rfd in os.fwalk(name, dir_fd=dfd, follow_symlinks=False):
        for f in fnames:
            try:
                st = os.stat(f, dir_fd=rfd, follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode):
                total += st.st_size
    return total


def _sweep_entries(d: Path, cutoff: float) -> tuple[int, int]:
    """`d` (`imports/` `exports/` `trash/`) の**直下のエントリ**のうち、自身の lstat の
    max(mtime, ctime) < cutoff のものを丸ごと (ディレクトリなら中身ごと) 消す。中身の時刻は
    見ない (単純さ優先 — ken 決定 2026-10-07)。mv は中のファイルの mtime を変えないので、
    ファイル単位で見ると「2 か月前のファイルを含むフォルダを今日 trash に移した」が即消える。
    直下エントリの ctime は rename で今になるので、移した日から数えられる。
    `d` 自体は残し、`d` が symlink に差し替えられていたら (jail 内から可能) 何もしない
    (O_NOFOLLOW で開く)。中は fd ベースで消す (`shutil.rmtree(dir_fd=)` は symlink を辿らない
    実装、直下の symlink はリンクそのものを unlink)。戻り値は (エントリ数, 通常ファイルのバイト数)。"""
    import shutil

    try:
        dfd = os.open(str(d), _DIR_FLAGS)
    except OSError:
        return 0, 0  # 無い・symlink・ディレクトリでない
    count = size = 0
    try:
        for name in os.listdir(dfd):
            try:
                st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
                if _age_time(st) >= cutoff:
                    continue
                if stat.S_ISDIR(st.st_mode):
                    b = _tree_bytes(name, dfd)
                    shutil.rmtree(name, dir_fd=dfd)
                else:
                    b = st.st_size if stat.S_ISREG(st.st_mode) else 0
                    os.unlink(name, dir_fd=dfd)
                count += 1
                size += b
            except OSError:
                continue  # 消せないものは次回に回す (掃除の失敗で umount を止めない)
    finally:
        os.close(dfd)
    return count, size


def _cutoff(now: Optional[float]) -> Optional[float]:
    if CACHE_TTL_DAYS <= 0:
        return None
    return (time.time() if now is None else now) - CACHE_TTL_DAYS * 86400


def _sweep_cache_dirs(ws: Path, now: Optional[float] = None) -> tuple[int, int]:
    """workspace の `imports/` `exports/` の直下のエントリで `CACHE_TTL_DAYS` 日過ぎたものを
    消す (#149、規則は `_sweep_entries`)。戻り値は (エントリ数, バイト数)。"""
    cutoff = _cutoff(now)
    if cutoff is None:
        return 0, 0
    n = b = 0
    for name in CACHE_DIRS:
        c, s = _sweep_entries(ws / name, cutoff)
        n += c
        b += s
    return n, b


def _sweep_trash(ws: Path, now: Optional[float] = None) -> tuple[int, int]:
    """`trash/` の直下のエントリで `CACHE_TTL_DAYS` 日過ぎたものを消す (規則は `_sweep_entries`。
    名前は見ない)。戻り値は (エントリ数, バイト数)。"""
    cutoff = _cutoff(now)
    if cutoff is None:
        return 0, 0
    return _sweep_entries(ws / TRASH_DIR, cutoff)


async def reap_idle_workspaces() -> int:
    """idle 超過した workspace を umount して loop を解放する (detach は mount の autoclear 任せ)。
    生存 session 中は umount しない (jail が superblock を掴んだまま次アクセスで同一 .img を
    二重 mount → ext4 破損するため。sandbox/docs/health.md C7)。次アクセスで自動再 mount。"""
    freed = 0
    for user in list(_ws_registry.keys()):
        if time.monotonic() - _ws_registry.get(user, 0) <= WS_IDLE_TTL_S:
            continue
        lock = _mount_lock(user)
        if (
            lock.locked() or _user_lock(user).locked()
        ):  # mount/remount or stateless 実行中
            continue
        async with lock:
            if time.monotonic() - _ws_registry.get(user, 0) <= WS_IDLE_TTL_S:
                continue  # ロック待ちの間に touch された
            if _has_live_session(user):
                continue  # hard gate
            ws = WORKSPACES / user
            if not os.path.ismount(ws):
                _ws_registry.pop(user, None)
                continue
            # imports/ exports/ のキャッシュ掃除 (#149)。ここは mount lock 保持・実行中でも
            # 生存 session 中でもない = jail が workspace を触っていないことが確定している
            # trash/ も同じ期限で (直下のエントリ単位)。ログは 1 行にまとめる
            try:
                n, b = await asyncio.to_thread(_sweep_cache_dirs, ws)
                tn, tb = await asyncio.to_thread(_sweep_trash, ws)
                if n or tn:
                    print(
                        f"[sandbox] cache of {user}: removed entries older than "
                        f"{CACHE_TTL_DAYS} days — imports/exports: {n}, {_fmt_size(b)}; "
                        f"trash: {tn}, {_fmt_size(tb)}",
                        flush=True,
                    )
            except Exception as e:
                print(f"[sandbox] cache sweep failed for {user}: {e!r}", flush=True)
            try:
                await asyncio.to_thread(_run, ["umount", str(ws)])
            except RuntimeError as e:
                # EBUSY 等は次ラウンドで再試行 (開いた fd 等)
                if "busy" not in str(e).lower():
                    print(f"[sandbox] ws umount failed for {user}: {e}", flush=True)
                continue
            _ws_registry.pop(user, None)
            # _mount_locks は pop しない: 待機中の coroutine がいると setdefault が別 lock を
            # 作り並行 mount → 二重 mount 破損する。leak は user 数ぶんで極小 (_user_locks と同様)。
            freed += 1
    if freed:
        print(f"[sandbox] reaped {freed} idle workspace(s)", flush=True)
    return freed


def _fmt_size(n: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} TiB"


def _last_used_at(img: Path) -> float:
    """保持期限の基準時刻 (epoch)。marker があればその mtime、無ければ `.img` の mtime
    (marker 導入前からある image の一回限りの代替 — 次の利用で marker が作られる)。"""
    try:
        return _last_used_marker(img.stem).stat().st_mtime
    except FileNotFoundError:
        return img.stat().st_mtime


async def reap_stale_workspaces() -> int:
    """`WS_RETENTION_DAYS` 日使われていない workspace の `.img` を消す (0 = 何もしない)。

    見るのは `IMAGES` 直下の `<user_id>.img` だけ: `.new` `.corrupt-*` `.trash-*` は suffix が
    違うので glob に掛からず、`/workspaces/<user_id>` (mountpoint) や `/ext` には触れない。
    消すのは **未 mount かつ lock が空いている** もののみ — mount 中 / 実行中 / 生存 session 中は
    次回に回す (削除中の remount で同一 .img を掴む TOCTOU は `_mount_lock` で潰す)。
    DELETE /workspace (外部 GC 向け、trash rename) と違い即 unlink する: N 日は配備側が決めた
    「消えてよい」期間なので回復余地は持たない。"""
    if WS_RETENTION_DAYS <= 0:
        return 0
    cutoff = time.time() - WS_RETENTION_DAYS * 86400
    try:
        imgs = sorted(p for p in IMAGES.iterdir() if p.suffix == ".img" and p.is_file())
    except FileNotFoundError:
        return 0
    removed = 0
    for img in imgs:
        user = img.stem
        if not _USER_ID_RE.match(user):
            continue
        try:
            if _last_used_at(img) > cutoff:
                continue
        except FileNotFoundError:
            continue  # 判定中に消えた (DELETE /workspace 等)
        lock = _mount_lock(user)
        if lock.locked() or _user_lock(user).locked():
            print(
                f"[sandbox] workspace of {user} is busy; retention sweep skipped",
                flush=True,
            )
            continue
        async with lock:
            if _has_live_session(user) or os.path.ismount(WORKSPACES / user):
                continue
            try:
                if _last_used_at(img) > cutoff:
                    continue  # lock 待ちの間に使われた
                size = img.stat().st_blocks * 512  # sparse なので実消費
                img.unlink()
            except FileNotFoundError:
                continue
            _last_used_marker(user).unlink(missing_ok=True)
            _ws_registry.pop(user, None)
            try:
                os.rmdir(
                    WORKSPACES / user
                )  # 空の mountpoint だけ。中身があれば触らない
            except OSError:
                pass
            print(
                f"[sandbox] workspace of {user} removed after {WS_RETENTION_DAYS} days "
                f"inactive, {_fmt_size(size)}",
                flush=True,
            )
            removed += 1
    return removed


async def _reaper_loop():
    while True:
        await asyncio.sleep(300)
        try:
            await _sessions.reap_idle()
            await reap_idle_workspaces()
        except Exception as e:
            print(f"[sandbox] reaper error: {e!r}", flush=True)


async def _retention_loop():
    """起動直後に 1 回、以降 1 時間ごとに保持期限の掃除。`WS_RETENTION_DAYS` > 0 のときだけ起動。"""
    while True:
        try:
            await reap_stale_workspaces()
        except Exception as e:
            print(f"[sandbox] retention sweep error: {e!r}", flush=True)
        await asyncio.sleep(WS_RETENTION_SWEEP_S)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
class FileIn(BaseModel):
    name: str
    b64: str


# `mounts` (#152) は全 endpoint 共通: そのユーザーに見せる `/ext/groups/<g>` `/ext/public` と
# rw / ro。無ければ (旧 nucllei) どれも見えない = 従来の jail。
# `file_limit_bytes` はファイルの出入りの上限 (nucllei の ATTACHMENT_MAX_SIZE_MB)。sandbox の上限
# (SANDBOX_FILE_MAX_SIZE_MB) との小さいほうが効く (`_file_limit`)。無ければ sandbox の上限だけ。


class ExecRequest(BaseModel):
    user_id: str
    lang: Literal["python", "shell"]
    code: str
    timeout_s: Optional[int] = None
    imports: list[FileIn] = Field(default_factory=list)
    mounts: list[MountIn] = Field(default_factory=list)
    # `imports` の 1 ファイルの上限 (exports の自動添付は 2026-10-19 に廃止したので、もうそちらには
    # 効かない。今の nucllei は送らない = sandbox の上限だけ)
    file_limit_bytes: Optional[int] = None


class PathRequest(BaseModel):
    user_id: str
    path: str = "."
    mounts: list[MountIn] = Field(default_factory=list)
    file_limit_bytes: Optional[int] = None


class WriteRequest(BaseModel):
    user_id: str
    path: str
    b64: str
    mounts: list[MountIn] = Field(default_factory=list)
    file_limit_bytes: Optional[int] = None
    # ファイルブラウザ (#150) 用。`expected_mtime` (`/files/read` `/files/stat` が返した値) と
    # 今の mtime が違えば 409 (外で変更された)、`exclusive` は既に在れば 409 (新規作成)。
    # `nofollow` は workspace 相対のパスでも経路の symlink を辿らない (`/ext/...` は常に辿らない)
    expected_mtime: Optional[float] = None
    exclusive: bool = False
    nofollow: bool = False


class ReadRequest(PathRequest):
    nofollow: bool = False
    # 範囲読み (nucllei の read_file のページ)。どちらかを渡すと `offset` バイト目から最大 `length`
    # バイト (`length` 省略は末尾まで) だけを返し、上限は**読む量**に掛かる (ファイル全体の大きさで
    # 断らない)。応答の `size` はファイル全体のバイト数、`offset` `length` は実際に返した範囲
    offset: Optional[int] = None
    length: Optional[int] = None


class DeleteRequest(PathRequest):
    # 既定 (False) は workspace の中 (trash/ の外) を trash/ の直下へ移す。True は完全に消す
    permanent: bool = False


class RenameRequest(BaseModel):
    user_id: str
    path: str
    dest: str
    mounts: list[MountIn] = Field(default_factory=list)


class UsageRequest(BaseModel):
    user_id: str


class CellRequest(BaseModel):
    # `file_limit_bytes` は持たない (exports の自動添付の上限だった。2026-10-19 廃止)。古い
    # nucllei が送ってきても pydantic の既定 (extra="ignore") で黙って捨てる = 422 にしない
    user_id: str
    chat_id: str
    code: str
    timeout_s: Optional[int] = None
    mounts: list[MountIn] = Field(default_factory=list)


@app.on_event("startup")
async def _startup():
    _log_auth_state()
    _log_ro_mounts()
    if _OLD_FILE_LIMIT_ENV in os.environ:
        print(
            f"[sandbox] WARNING: {_OLD_FILE_LIMIT_ENV} は廃止 (読まない)。"
            f"SANDBOX_FILE_MAX_SIZE_MB (MB の整数、今の値 {FILE_MAX_SIZE_MB}) を使う",
            flush=True,
        )
    _ensure_loop_nodes()
    if not _loop_available():
        print(
            "[sandbox] WARNING: loop ドライバが使えない (/dev/loop-control open 失敗)。"
            "全 workspace mount が失敗する。ホストで modprobe loop、それが "
            "'Module loop not found' ならカーネル更新後の再起動漏れ — ホストの reboot が必要",
            flush=True,
        )
    asyncio.create_task(_reaper_loop())
    if WS_RETENTION_DAYS > 0:
        print(
            f"[sandbox] workspace retention: {WS_RETENTION_DAYS} 日未使用の workspace を削除する"
            " (SANDBOX_WORKSPACE_RETENTION_DAYS)",
            flush=True,
        )
        asyncio.create_task(_retention_loop())


@app.get("/healthz")
async def healthz():
    # loop 不能は全実行が 5xx になる hard fault なので healthz でも落とす
    # (compose healthcheck は非 200 で unhealthy になり docker ps で見える)
    loop_ok = _loop_available()
    body = {
        "ok": loop_ok,
        "nsjail": os.path.exists("/usr/local/bin/nsjail"),
        "loop": loop_ok,
        # sandbox の上限 (SANDBOX_FILE_MAX_SIZE_MB)。nucllei が起動時に読む (ファイルブラウザの
        # アップロードの事前チェック・url= の取り込み・添付の上限との比較)
        "file_max_size_mb": FILE_MAX_SIZE_MB,
    }
    return JSONResponse(body, status_code=200 if loop_ok else 503)


@app.get("/metrics")
async def metrics():
    """Prometheus text 形式 (依存を足さず手書き)。全て label なし集約 (user_id を出さない)。
    loop_used は /workspaces 直下の mountpoint を数える → ホスト snapd の loop は構造上入らない。"""
    loop_used = 0
    for d in WORKSPACES.iterdir():
        if d.name == ".images":
            continue
        if d.is_dir() and os.path.ismount(d):
            loop_used += 1
    try:
        ws_total = sum(1 for p in IMAGES.iterdir() if p.suffix == ".img")
    except FileNotFoundError:
        ws_total = 0
    st = os.statvfs(WORKSPACES)
    disk_total = st.f_blocks * st.f_frsize
    disk_used = (st.f_blocks - st.f_bfree) * st.f_frsize
    m = [
        (
            "sandbox_loop_used",
            "gauge",
            "現在 mount 済み workspace 数 (= 使用中 loop)",
            loop_used,
        ),
        (
            "sandbox_loop_nodes_total",
            "gauge",
            "loop ノード上限 (SANDBOX_LOOP_NODES)",
            LOOP_NODE_COUNT,
        ),
        (
            "sandbox_workspaces_total",
            "gauge",
            "provision 済み .img 数 (未 mount 含む)",
            ws_total,
        ),
        (
            "sandbox_sessions_active",
            "gauge",
            "生存 kernel session 数",
            len(_sessions._sessions),
        ),
        ("sandbox_disk_used_bytes", "gauge", "/workspaces 使用バイト", disk_used),
        ("sandbox_disk_total_bytes", "gauge", "/workspaces 総バイト", disk_total),
        (
            "sandbox_file_max_size_bytes",
            "gauge",
            "1 ファイルの上限 (SANDBOX_FILE_MAX_SIZE_MB をバイトで)",
            FILE_LIMIT,
        ),
    ]
    lines = []
    for name, typ, help_, val in m:
        lines += [f"# HELP {name} {help_}", f"# TYPE {name} {typ}", f"{name} {val}"]
    return PlainTextResponse("\n".join(lines) + "\n")


async def _abandon_if_client_gone(request: Request):
    """lock 待ちの間に client (nucllei tool) が timeout で切れていたら実行しない。

    tool 側は待ち時間込みで timeout_s+30 秒しか待たず ERROR を返す。ここで確認しないと
    切れた要求が lock 取得後にそのまま走り (結果は誰にも返らない)、モデルの再試行と
    合わせて同じコードが 2 回実行される (書いたファイルの二重生成、kernel なら namespace 副作用)。
    """
    if await request.is_disconnected():
        raise HTTPException(499, "client disconnected while waiting for lock")


@app.post("/exec")
async def exec_code(req: ExecRequest, request: Request):
    timeout_s = max(1, min(req.timeout_s or TIMEOUT_DEFAULT_S, TIMEOUT_MAX_S))
    validated = _validate_mounts(req.mounts)
    limit = _file_limit(req.file_limit_bytes)
    ws = await _workspace(req.user_id)

    async with _user_lock(req.user_id), _sem:
        await _abandon_if_client_gone(request)
        for f in req.imports:
            name = os.path.basename(f.name)
            if not name:
                raise HTTPException(400, f"invalid input name: {f.name!r}")
            try:
                data = base64.b64decode(f.b64)
            except binascii.Error:
                raise HTTPException(400, f"invalid b64 for input: {name}")
            if len(data) > limit:
                raise _too_large(len(data), limit)
            dest = ws / "imports" / name
            try:
                dest.write_bytes(data)
            except OSError as e:
                if _nospace(e):
                    raise HTTPException(507, QUOTA_FULL_DETAIL)
                raise
            os.chown(dest, JAIL_UID, JAIL_UID)

        ext = "py" if req.lang == "python" else "sh"
        script = ws / ".exec" / f"{uuid.uuid4().hex[:8]}.{ext}"
        try:
            script.write_text(req.code, encoding="utf-8")
        except OSError as e:
            if _nospace(e):
                raise HTTPException(507, QUOTA_FULL_DETAIL)
            raise
        os.chown(script, JAIL_UID, JAIL_UID)
        jail_script = f"/workspace/.exec/{script.name}"
        if req.lang == "python":
            argv = [f"{VENV_BIN}/python", "-u", jail_script]
        else:
            argv = ["/bin/bash", jail_script]

        binds = await asyncio.to_thread(_prepare_mounts, validated)
        t0 = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *_nsjail_cmd(ws, timeout_s, argv, binds=binds),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                async with asyncio.timeout(
                    timeout_s + 15
                ):  # 本命は nsjail --time_limit
                    (out, out_trunc), (err, err_trunc) = await asyncio.gather(
                        _read_capped(proc.stdout),
                        _read_capped(proc.stderr),
                    )
                    exit_code = await proc.wait()
            except TimeoutError:
                proc.kill()
                await proc.wait()
                out, out_trunc, err, err_trunc, exit_code = b"", False, b"", False, -9
        finally:
            script.unlink(missing_ok=True)
        duration_s = round(time.monotonic() - t0, 2)

        return {
            "exit_code": exit_code,
            "timed_out": exit_code != 0 and duration_s >= timeout_s,
            "duration_s": duration_s,
            "stdout": out.decode("utf-8", errors="replace"),
            "stderr": err.decode("utf-8", errors="replace"),
            "stdout_truncated": out_trunc,
            "stderr_truncated": err_trunc,
            "cache_ttl_days": CACHE_TTL_REPORTED,
        }


@app.post("/exec_cell")
async def exec_cell(req: CellRequest, request: Request):
    """stateful run_python: (user, chat) の常駐 kernel で永続 namespace 実行。"""
    if not req.chat_id:
        raise HTTPException(400, "chat_id required for stateful exec")
    timeout_s = max(1, min(req.timeout_s or TIMEOUT_DEFAULT_S, TIMEOUT_MAX_S))
    validated = _validate_mounts(req.mounts)
    await _workspace(req.user_id)
    binds = await asyncio.to_thread(_prepare_mounts, validated)
    sess, was_reset = await _sessions.get(
        req.user_id, req.chat_id, binds, _mounts_fingerprint(binds)
    )

    async with sess.lock, _sem:
        await _abandon_if_client_gone(request)
        sess.last_used = time.monotonic()
        t0 = time.monotonic()
        try:
            await asyncio.to_thread(sess._send, req.code, float(timeout_s))
            # kernel は自前 SIGALRM で timeout を処理し即応答する。ここは中断不能な
            # C 演算で kernel が応答しない場合のバックストップ (grace 超過で kill)。
            resp = await asyncio.wait_for(
                asyncio.to_thread(sess._recv), timeout=timeout_s + SESSION_GRACE_S
            )
        except (asyncio.TimeoutError, Exception) as e:
            # 応答なし/パイプ破損 → セッションを落として次回作り直し (namespace 消失)
            await _sessions.drop(sess)
            hard = isinstance(e, asyncio.TimeoutError)
            if not hard:
                # 例外の中身はログだけ — error はモデルに届く文 (常体・内部の repr を出さない。
                # 2026-10-19 llens 監査 (7))
                print(f"[sandbox] exec_cell: kernel I/O failed: {e!r}", flush=True)
            return {
                "stdout": "",
                "error": (
                    "セルが応答せず中断できないので実行環境を強制終了した"
                    " (変数・import は失われた)"
                    if hard
                    else "実行環境との通信に失敗したので実行環境を作り直す"
                    " (変数・import は失われた)"
                ),
                "timed_out": hard,
                "session_reset": True,
                "duration_s": 0,
                "stdout_truncated": False,
                "cache_ttl_days": CACHE_TTL_REPORTED,
            }
        duration_s = round(time.monotonic() - t0, 2)
        sess.last_used = time.monotonic()

        if resp is None:  # kernel が EOF (クラッシュ)
            await _sessions.drop(sess)
            return {
                "stdout": "",
                "error": "実行環境が異常終了した (変数・import は失われた)",
                "timed_out": False,
                "session_reset": True,
                "duration_s": duration_s,
                "stdout_truncated": False,
                "cache_ttl_days": CACHE_TTL_REPORTED,
            }

        return {
            "stdout": resp.get("stdout", ""),
            "error": resp.get("error"),
            "timed_out": resp.get("timed_out", False),
            "session_reset": was_reset,
            "duration_s": duration_s,
            "stdout_truncated": resp.get("truncated", False),
            "cache_ttl_days": CACHE_TTL_REPORTED,
        }


# ---------------------------------------------------------------------------
# /files/* の `/ext/...` (リクエストごとの bind の中。#152)
# ---------------------------------------------------------------------------
# `path` が `/` 始まりなら jail の絶対パス `/ext/groups/<g>/…` `/ext/public/…` として扱い、
# **そのリクエストの `mounts` に在る bind の中**だけを読み書きする (無ければ 404 = 見えない、
# ro に書けば 403)。包含判定の基準はその bind のディレクトリ (= グループのディレクトリ) で、
# **経路上の symlink は 1 つも辿らない** — 各段を `O_NOFOLLOW` 付きで dir fd から開き直す
# (openat 系)。jail 内で作った `/ext/groups/EMR/x -> ../OTHER/secret` のような symlink を
# サービス側 (コンテナの名前空間 = 全グループが在る) で辿って他グループへ抜ける穴を塞ぐため。
# resolve() してから包含判定する方式は判定と open の間に差し替えられる (TOCTOU) ので使わない。
# workspace 相対の `path` (`/` 始まりでない) は従来どおり `_safe_path`。

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _mkdir_owned(name: str, dir_fd: Optional[int] = None) -> None:
    """ディレクトリを 1 段作って jail のユーザー (65534) の物にする。**サービスが新しく作る
    ディレクトリはすべてこれを通す** — root 所有 755 のままだと jail (実 uid 65534) がその中で
    rm / mv できない (llens staging で write_file の親が root になった不具合)。作った段を
    O_NOFOLLOW で開き直して fd に fchown する (作った直後に symlink へ差し替えられても辿らない)。
    mode 0o775 は既存の書き方どおり (/ext の default ACL の mask を絞らない)。既に在れば
    FileExistsError をそのまま投げる (呼び手が「在る」の扱いを決める。既存の所有者は変えない)。"""
    os.mkdir(name, 0o775, dir_fd=dir_fd)
    fd = os.open(name, _DIR_FLAGS, dir_fd=dir_fd)
    try:
        os.fchown(fd, JAIL_UID, JAIL_UID)
    finally:
        os.close(fd)


def _ext_target(path: str, mounts: list[MountIn], write: bool):
    """`/ext/...` を (bind のコンテナ側ディレクトリ, bind の jail パス, その下のセグメント) に。
    blocking。"""
    validated = _validate_mounts(mounts)
    prefix = JAIL_EXT_ROOT + "/"
    if not path.startswith(prefix):
        raise HTTPException(
            400, f"path must be workspace-relative or under /ext: {path!r}"
        )
    segs = path[len(prefix) :].rstrip("/").split("/")
    if any(s in ("", ".", "..") or "\x00" in s for s in segs):
        raise HTTPException(400, f"invalid path: {path!r}")
    best = None
    for jail_path, rw in validated:
        msegs = jail_path[len(prefix) :].split("/")
        if segs[: len(msegs)] == msegs and (best is None or len(msegs) > len(best[1])):
            best = (jail_path, msegs, rw)
    if best is None:
        # bind されていない = jail に存在しない (他グループ・未所属) — 不在と同じ
        raise HTTPException(404, f"not found: {path}")
    jail_path, msegs, rw = best
    if write and not rw:
        raise HTTPException(403, f"read-only: {jail_path}")
    src = _mount_source(jail_path, create=write)
    if src is None:
        raise HTTPException(404, f"not found: {path}")
    return src, jail_path, segs[len(msegs) :]


def _symlink_or_missing(name: str, dir_fd: int, path: str) -> HTTPException:
    """open が失敗した段の言い分け: symlink なら 400、それ以外は 404。"""
    try:
        if stat.S_ISLNK(os.stat(name, dir_fd=dir_fd, follow_symlinks=False).st_mode):
            return HTTPException(400, f"path contains a symlink: {path}")
    except OSError:
        pass
    return HTTPException(404, f"not found: {path}")


def _open_dirs(base: str, segs: list[str], path: str, create: bool = False) -> int:
    """`base` から `segs` を 1 段ずつ O_NOFOLLOW で開き、最後のディレクトリの fd を返す
    (呼び手が close)。`create` なら無い段を作る (jail のユーザーの物にする)。"""
    fd = os.open(base, _DIR_FLAGS)
    try:
        for seg in segs:
            try:
                nfd = os.open(seg, _DIR_FLAGS, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise HTTPException(404, f"not found: {path}")
                try:
                    _mkdir_owned(seg, dir_fd=fd)
                except FileExistsError:
                    pass
                except OSError as e:
                    # 作った直後に symlink 等へ差し替えられた — 下の open が 400/404 に言い分ける
                    if e.errno not in (errno.ELOOP, errno.ENOTDIR):
                        raise
                try:
                    nfd = os.open(seg, _DIR_FLAGS, dir_fd=fd)
                except OSError:
                    raise _symlink_or_missing(seg, fd, path)
            except OSError:
                raise _symlink_or_missing(seg, fd, path)
            os.close(fd)
            fd = nfd
        return fd
    except BaseException:
        os.close(fd)
        raise


def _makedirs_owned(base: Path, d: Path, path: str) -> None:
    """`base` の中の `d` を (無い段を `_mkdir_owned` で作りながら) 用意する。blocking。"""
    os.close(_open_dirs(str(base), list(d.relative_to(base).parts), path, create=True))


def _ext_list(path: str, mounts: list[MountIn]) -> dict:
    src, jail_path, segs = _ext_target(path, mounts, write=False)
    try:
        fd = _open_dirs(str(src), segs[:-1], path) if segs else None
    except HTTPException as e:
        if e.status_code == 404:
            return {"entries": [], "exists": False, "is_dir": False}
        raise
    try:
        if segs:
            leaf = segs[-1]
            try:
                mode = os.stat(leaf, dir_fd=fd, follow_symlinks=False).st_mode
            except FileNotFoundError:
                return {"entries": [], "exists": False, "is_dir": False}
            if stat.S_ISLNK(mode):
                raise HTTPException(400, f"path contains a symlink: {path}")
            if not stat.S_ISDIR(mode):
                return {"entries": [], "exists": True, "is_dir": False}
            top = os.open(leaf, _DIR_FLAGS, dir_fd=fd)
        else:
            top = os.open(str(src), _DIR_FLAGS)
    finally:
        if fd is not None:
            os.close(fd)
    base = jail_path + ("/" + "/".join(segs) if segs else "")
    entries = []
    truncated = False
    try:
        # follow_symlinks=False: ディレクトリ symlink にも降りない。symlink のファイルも出さない
        for dirpath, dirnames, filenames, dfd in os.fwalk(
            ".", dir_fd=top, follow_symlinks=False
        ):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            rel_dir = "" if dirpath == "." else dirpath[2:] + "/"
            for name in sorted(filenames):
                if name.startswith("."):
                    continue
                try:
                    st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
                except OSError:
                    continue
                if not stat.S_ISREG(st.st_mode):
                    continue
                if len(entries) >= LIST_LIMIT:
                    truncated = True
                    break
                entries.append({"path": f"{base}/{rel_dir}{name}", "size": st.st_size})
            if truncated:
                break
    finally:
        os.close(top)
    return {"entries": entries, "exists": True, "is_dir": True, "truncated": truncated}


def _is_ext_path(path: str) -> bool:
    """`/files/*` の `path` が jail の絶対パス (= `/ext/...` の扱い) か。workspace 相対は `/` 無し。"""
    return path.startswith("/")


# ---------------------------------------------------------------------------
# fd で辿るファイル操作 (workspace と `/ext/...` 共通。#150 ファイルブラウザ)
# ---------------------------------------------------------------------------
# 包含判定の基準 (`base` = workspace の root か bind のディレクトリ) から `segs` を 1 段ずつ
# O_NOFOLLOW で開く (`_open_dirs`)。経路のどこにも symlink を許さず、末端の symlink も辿らない
# (削除・改名は symlink そのものに効く)。`/files/ls` `/files/stat` `/files/delete`
# `/files/rename` `/files/mkdir` は常にこれ、`/files/read` `/files/write` は `/ext/...` と
# `nofollow: true` のときこれ (workspace 相対の既定は従来どおり `_safe_path` — tool の挙動を変えない)。

# 容量切れ (workspace の quota = loopback ext4 の満杯)。HTTP 507 で返し、呼び手 (nucllei) が
# 「ファイルブラウザで整理できる」と言い分ける
_NOSPACE_ERRNOS = frozenset({errno.ENOSPC, errno.EDQUOT})
QUOTA_FULL_DETAIL = "workspace quota exceeded (No space left on device)"

# 一覧の 1 ディレクトリあたりの上限 (超えたら `truncated: true`)
LS_LIMIT = 5000
# `/files/list` (再帰・ファイルだけ。list_workspace) の件数の上限。超えたら `truncated: true`
# (追加フィールドなので、これを読まない旧 nucllei とも互換。2026-10-19 llens 監査)
LIST_LIMIT = 500


class _Target(NamedTuple):
    base: str  # 包含判定の基準 (コンテナ側のディレクトリ)
    segs: list  # base から下のセグメント
    path: str  # 要求のパス (エラー文用)
    kind: str  # "workspace" | "ext"
    prefix: str  # 応答のパスの頭 (workspace は ""、ext は bind の jail パス)


def _ws_segs(path: str) -> list[str]:
    """workspace 相対の `path` をセグメントに。`.` と空は落とし、`..`・NUL は 400。"""
    segs = [s for s in path.split("/") if s not in ("", ".")]
    if any(s == ".." or "\x00" in s for s in segs):
        raise HTTPException(400, f"invalid path: {path!r}")
    return segs


async def _fs_target(
    user_id: str, path: str, mounts: list[MountIn], write: bool
) -> _Target:
    if _is_ext_path(path):
        if not _USER_ID_RE.match(user_id):
            raise HTTPException(400, f"invalid user_id: {user_id!r}")
        src, jail_path, segs = await asyncio.to_thread(_ext_target, path, mounts, write)
        return _Target(str(src), segs, path, "ext", jail_path)
    segs = _ws_segs(path)
    ws = await _workspace(user_id)
    return _Target(str(ws), segs, path, "workspace", "")


def _join(t: _Target, segs: list[str]) -> str:
    rel = "/".join(segs)
    if t.kind == "workspace":
        return rel or "."
    return t.prefix + ("/" + rel if rel else "")


def _mtime(st) -> float:
    """上書き競合の比較に使う mtime (秒・float)。JSON → JS → JSON で値が変わらない float で返す
    (ns の int は JS の Number で丸まる)。比較は `_same_mtime`。"""
    return st.st_mtime_ns / 1e9


def _same_mtime(a: float, b: float) -> bool:
    return abs(a - b) < 1e-6


def _kind_of(mode: int) -> str:
    if stat.S_ISDIR(mode):
        return "dir"
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISLNK(mode):
        return "symlink"
    return "other"


def _check_mutable(t: _Target, segs: list[str], what: str) -> None:
    """ファイルブラウザの書き換え (削除・改名・mkdir) で触らせないもの: 置き場の root そのもの、
    `.` 始まり (一覧に出さない内部・隠しファイル)、workspace 直下の `imports/`
    (sandbox が実行のたびに使う置き場。消すと取り込みが落ちる) と `trash/` (消す代わりの移し先)。"""
    if not segs:
        raise HTTPException(400, f"cannot {what} the root: {t.path}")
    if any(s.startswith(".") for s in segs):
        raise HTTPException(400, f"hidden path: {t.path}")
    if t.kind == "workspace" and len(segs) == 1 and segs[0] in PROTECTED_DIRS:
        raise HTTPException(400, f"protected directory: {segs[0]}")


def _nospace(e: OSError) -> bool:
    return e.errno in _NOSPACE_ERRNOS


def _lstat_at(name: str, dfd: int):
    try:
        return os.stat(name, dir_fd=dfd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _open_parent(t: _Target, segs: list[str], create: bool = False):
    """末端の親ディレクトリの fd (呼び手が close)。無ければ None (create なら作る)。"""
    try:
        return _open_dirs(t.base, segs[:-1], t.path, create=create)
    except HTTPException as e:
        if e.status_code == 404 and not create:
            return None
        raise


def _fs_ls(t: _Target) -> dict:
    """1 階層の一覧 (再帰しない)。`.` 始まりは出さない (list_workspace と同じ)。symlink は
    辿らず `type: "symlink"` で出す。"""
    missing = {"entries": [], "exists": False, "is_dir": False, "truncated": False}
    segs = t.segs
    if segs:
        pfd = _open_parent(t, segs)
        if pfd is None:
            return missing
        try:
            st = _lstat_at(segs[-1], pfd)
            if st is None:
                return missing
            if not stat.S_ISDIR(st.st_mode):
                return {
                    "entries": [],
                    "exists": True,
                    "is_dir": False,
                    "truncated": False,
                }
            fd = os.open(segs[-1], _DIR_FLAGS, dir_fd=pfd)
        finally:
            os.close(pfd)
    else:
        fd = os.open(t.base, _DIR_FLAGS)
    entries = []
    truncated = False
    try:
        with os.scandir(fd) as it:
            for de in it:
                if de.name.startswith("."):
                    continue
                if len(entries) >= LS_LIMIT:
                    truncated = True
                    break
                try:
                    st = de.stat(follow_symlinks=False)
                except OSError:
                    continue
                entries.append(
                    {
                        "name": de.name,
                        "path": _join(t, segs + [de.name]),
                        "type": _kind_of(st.st_mode),
                        "size": st.st_size if stat.S_ISREG(st.st_mode) else 0,
                        "mtime": _mtime(st),
                        # imports/ exports/ trash/ の直下は max(mtime, ctime) + N 日で消える
                        # (`_sweep_entries`)。呼び手が残り期限を出すため
                        "ctime": st.st_ctime_ns / 1e9,
                    }
                )
    finally:
        os.close(fd)
    entries.sort(key=lambda e: (e["type"] != "dir", e["name"]))
    return {"entries": entries, "exists": True, "is_dir": True, "truncated": truncated}


def _fs_stat(t: _Target) -> dict:
    segs = t.segs
    if not segs:
        st = os.stat(t.base, follow_symlinks=False)
    else:
        pfd = _open_parent(t, segs)
        if pfd is None:
            return {"exists": False}
        try:
            st = _lstat_at(segs[-1], pfd)
        finally:
            os.close(pfd)
        if st is None:
            return {"exists": False}
    return {
        "exists": True,
        "path": _join(t, segs),
        "type": _kind_of(st.st_mode),
        "size": st.st_size if stat.S_ISREG(st.st_mode) else 0,
        "mtime": _mtime(st),
    }


def _open_regular(t: _Target) -> tuple[int, os.stat_result]:
    """`t` の通常ファイルを経路も末端も symlink を辿らずに開く (fd と fstat。呼び手が close)。"""
    segs = t.segs
    if not segs:
        raise HTTPException(404, f"not a file: {t.path}")
    dfd = _open_dirs(t.base, segs[:-1], t.path)
    try:
        try:
            fd = os.open(
                segs[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dfd
            )
        except OSError:
            raise _symlink_or_missing(segs[-1], dfd, t.path)
    finally:
        os.close(dfd)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise HTTPException(404, f"not a file: {t.path}")
    except BaseException:
        os.close(fd)
        raise
    return fd, st


def _read_body(
    f, st, name: str, limit: int, offset: Optional[int], length: Optional[int]
) -> dict:
    """開いたファイルから `/files/read` の応答を作る。範囲指定 (`offset` / `length`) が無ければ
    全体 (大きさが上限を超えれば 413)、あれば範囲だけ (上限は読む量に掛ける)。"""
    if offset is None and length is None:
        if st.st_size > limit:
            raise _too_large(st.st_size, limit)
        data = f.read()
        return {
            "name": name,
            "size": len(data),
            "b64": base64.b64encode(data).decode(),
            "mtime": _mtime(st),
        }
    off = offset or 0
    if off < 0 or (length is not None and length < 0):
        raise HTTPException(400, "offset / length must be >= 0")
    want = max(0, st.st_size - off) if length is None else length
    if want > limit:
        raise _too_large(want, limit)
    f.seek(off)
    data = f.read(want)
    return {
        "name": name,
        "size": st.st_size,
        "offset": off,
        "length": len(data),
        "b64": base64.b64encode(data).decode(),
        "mtime": _mtime(st),
    }


def _fs_read(
    t: _Target,
    limit: Optional[int] = None,
    offset: Optional[int] = None,
    length: Optional[int] = None,
) -> dict:
    if limit is None:
        limit = FILE_LIMIT
    fd, st = _open_regular(t)
    with os.fdopen(fd, "rb") as f:
        return _read_body(f, st, t.segs[-1], limit, offset, length)


def _conflict(message: str, mtime: Optional[float]) -> HTTPException:
    return HTTPException(409, {"message": message, "mtime": mtime})


def _fs_write(
    t: _Target, data: bytes, expected_mtime: Optional[float], exclusive: bool
) -> dict:
    segs = t.segs
    if not segs:
        raise HTTPException(400, f"is a directory: {t.path}")
    try:
        dfd = _open_dirs(t.base, segs[:-1], t.path, create=True)
        try:
            leaf = segs[-1]
            st = _lstat_at(leaf, dfd)
            if st is not None and stat.S_ISLNK(st.st_mode):
                raise HTTPException(400, f"path contains a symlink: {t.path}")
            if st is not None and stat.S_ISDIR(st.st_mode):
                raise HTTPException(400, f"is a directory: {t.path}")
            if exclusive and st is not None:
                raise _conflict("already exists", _mtime(st))
            if expected_mtime is not None and (
                st is None or not _same_mtime(_mtime(st), expected_mtime)
            ):
                # 開いた後に外 (jail の実行・別のタブ) で変更・削除された
                raise _conflict(
                    "modified" if st is not None else "deleted",
                    _mtime(st) if st is not None else None,
                )
            flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
            flags |= os.O_EXCL if exclusive else os.O_TRUNC
            try:
                fd = os.open(leaf, flags, 0o664, dir_fd=dfd)
            except FileExistsError:
                raise _conflict("already exists", None)
            except IsADirectoryError:
                raise HTTPException(400, f"is a directory: {t.path}")
            except OSError as e:
                if _nospace(e):
                    raise
                raise _symlink_or_missing(leaf, dfd, t.path)
        finally:
            os.close(dfd)
        with os.fdopen(fd, "wb") as f:
            os.fchown(f.fileno(), JAIL_UID, JAIL_UID)
            f.write(data)
            f.flush()
            st = os.fstat(f.fileno())
    except OSError as e:
        if _nospace(e):
            raise HTTPException(507, QUOTA_FULL_DETAIL)
        raise
    return {"size": len(data), "mtime": _mtime(st)}


def _trash_name(name: str, n: int) -> str:
    """trash/ の直下で n 番目に試す名前。1 はそのまま、2 以降は末尾に `-n` (拡張子があれば
    その前: `memo.txt` → `memo-2.txt`、`data` → `data-2`。先頭の `.` は拡張子と見なさない)。"""
    if n == 1:
        return name
    dot = name.rfind(".")
    return f"{name[:dot]}-{n}{name[dot:]}" if dot > 0 else f"{name}-{n}"


TRASH_NAME_TRIES = 1000


def _moves_to_trash(t: _Target, permanent: bool) -> bool:
    """`/files/delete` が消さずに trash/ の直下へ移すか: workspace の中で trash/ の外、
    `permanent` でないとき。trash/ の中と `/ext/...` は完全に消す。"""
    return (
        not permanent
        and t.kind == "workspace"
        and bool(t.segs)
        and t.segs[0] != TRASH_DIR
    )


def _fs_delete(t: _Target, permanent: bool = False) -> dict:
    """削除。workspace の中 (trash/ の外) は既定で trash/ の直下へ移し (`_moves_to_trash`)、
    それ以外と `permanent` は中身ごと完全に消す。symlink はリンクそのものに効く。"""
    import shutil

    segs = t.segs
    _check_mutable(t, segs, "delete")
    dfd = _open_dirs(t.base, segs[:-1], t.path)
    try:
        st = _lstat_at(segs[-1], dfd)
        if st is None:
            raise HTTPException(404, f"not found: {t.path}")
        if _moves_to_trash(t, permanent):
            return _move_to_trash(t, dfd, segs[-1])
        if stat.S_ISDIR(st.st_mode):
            # dir_fd 付きの rmtree は fd ベース (symlink を辿らない版) で消す
            shutil.rmtree(segs[-1], dir_fd=dfd)
        else:
            os.unlink(segs[-1], dir_fd=dfd)  # symlink はリンクそのものを消す
    finally:
        os.close(dfd)
    return {"ok": True, "trashed": False}


def _move_to_trash(t: _Target, dfd: int, leaf: str) -> dict:
    """`dfd` の中の `leaf` を workspace の trash/ の直下へ移す。同名が在れば `_trash_name` の
    番号を進める。trash/ が無ければ作る (jail のユーザーの物。jail で消されていても動く)。
    trash/ が symlink 等に差し替えられていれば辿らない (400)。rename は mv と同じく ctime を
    今にするので、期限 (`_sweep_entries`) は移した時から数える。"""
    try:
        tfd = _open_dirs(t.base, [TRASH_DIR], t.path, create=True)
    except OSError as e:
        if _nospace(e):
            raise HTTPException(507, QUOTA_FULL_DETAIL)
        raise
    try:
        for n in range(1, TRASH_NAME_TRIES + 1):
            name = _trash_name(leaf, n)
            if len(os.fsencode(name)) > 255 or _lstat_at(name, tfd) is not None:
                continue
            try:
                os.rename(leaf, name, src_dir_fd=dfd, dst_dir_fd=tfd)
            except OSError as e:
                if _nospace(e):
                    raise HTTPException(507, QUOTA_FULL_DETAIL)
                raise HTTPException(400, f"cannot move to trash: {t.path}")
            return {"ok": True, "trashed": True, "path": f"{TRASH_DIR}/{name}"}
    finally:
        os.close(tfd)
    raise HTTPException(409, f"no free name in {TRASH_DIR}/ for: {leaf}")


def _fs_rename(t: _Target, d: _Target) -> dict:
    """同じ置き場 (workspace の中・同じ bind の中) での改名・移動。行き先に在れば 409。"""
    if (t.kind, t.base) != (d.kind, d.base):
        raise HTTPException(400, f"cannot move across places: {t.path} -> {d.path}")
    _check_mutable(t, t.segs, "rename")
    _check_mutable(d, d.segs, "rename")
    if d.segs[: len(t.segs)] == t.segs:
        raise HTTPException(400, f"cannot move into itself: {t.path} -> {d.path}")
    sfd = _open_dirs(t.base, t.segs[:-1], t.path)
    try:
        if _lstat_at(t.segs[-1], sfd) is None:
            raise HTTPException(404, f"not found: {t.path}")
        dfd = _open_dirs(d.base, d.segs[:-1], d.path)
        try:
            if _lstat_at(d.segs[-1], dfd) is not None:
                raise _conflict("already exists", None)
            try:
                os.rename(t.segs[-1], d.segs[-1], src_dir_fd=sfd, dst_dir_fd=dfd)
            except OSError as e:
                if _nospace(e):
                    raise HTTPException(507, QUOTA_FULL_DETAIL)
                raise HTTPException(400, f"cannot rename: {t.path} -> {d.path}")
        finally:
            os.close(dfd)
    finally:
        os.close(sfd)
    return {"ok": True, "path": _join(d, d.segs)}


def _fs_mkdir(t: _Target) -> dict:
    segs = t.segs
    _check_mutable(t, segs, "create")
    try:
        dfd = _open_dirs(t.base, segs[:-1], t.path, create=True)
        try:
            try:
                _mkdir_owned(segs[-1], dir_fd=dfd)
            except FileExistsError:
                raise _conflict("already exists", None)
        finally:
            os.close(dfd)
    except OSError as e:
        if _nospace(e):
            raise HTTPException(507, QUOTA_FULL_DETAIL)
        raise
    return {"ok": True, "path": _join(t, segs)}


@app.post("/files/list")
async def files_list(req: PathRequest):
    if _is_ext_path(req.path):
        if not _USER_ID_RE.match(req.user_id):
            raise HTTPException(400, f"invalid user_id: {req.user_id!r}")
        return await asyncio.to_thread(_ext_list, req.path, req.mounts)
    ws = await _workspace(req.user_id)
    base = _safe_path(ws, req.path)
    # `exists` / `is_dir` は「無い」と「空」と「ファイル」を言い分けるため (#145 — 旧版は
    # どれも entries=[] で、tool は「空 (ファイルが無いか、ディレクトリが存在しない)」としか
    # 言えなかった)。追加フィールドなので、これを読まない旧 nucllei とも互換
    if not base.exists():
        return {"entries": [], "exists": False, "is_dir": False}
    if not base.is_dir():
        return {"entries": [], "exists": True, "is_dir": False}
    entries = []
    truncated = False
    for p in sorted(base.rglob("*")):
        rel = p.relative_to(ws)
        # `.` 始まりの要素 (.exec / .cache 等の内部・隠しファイル) は見せない
        if any(part.startswith(".") for part in rel.parts):
            continue
        if p.is_file():
            if len(entries) >= LIST_LIMIT:
                truncated = True
                break
            entries.append({"path": str(rel), "size": p.stat().st_size})
    return {"entries": entries, "exists": True, "is_dir": True, "truncated": truncated}


@app.post("/files/ls")
async def files_ls(req: PathRequest):
    """1 階層の一覧 (#150 ファイルブラウザ)。`/files/list` (再帰・ファイルだけ) と違い、
    ディレクトリも出し、種類・サイズ・mtime を返す。"""
    t = await _fs_target(req.user_id, req.path, req.mounts, write=False)
    res = await asyncio.to_thread(_fs_ls, t)
    # imports/ exports/ trash/ の残り期限 (mtime + N 日) を呼び手が出せるように (#149)
    return {**res, "cache_ttl_days": CACHE_TTL_REPORTED}


@app.post("/files/stat")
async def files_stat(req: PathRequest):
    t = await _fs_target(req.user_id, req.path, req.mounts, write=False)
    return await asyncio.to_thread(_fs_stat, t)


@app.post("/files/read")
async def files_read(req: ReadRequest):
    limit = _file_limit(req.file_limit_bytes)
    if _is_ext_path(req.path) or req.nofollow:
        t = await _fs_target(req.user_id, req.path, req.mounts, write=False)
        return await asyncio.to_thread(_fs_read, t, limit, req.offset, req.length)
    ws = await _workspace(req.user_id)
    p = _safe_path(ws, req.path)
    if not p.is_file():
        raise HTTPException(404, f"not a file: {req.path}")

    def read() -> dict:
        with open(p, "rb") as f:
            st = os.fstat(f.fileno())
            return _read_body(f, st, p.name, limit, req.offset, req.length)

    return await asyncio.to_thread(read)


@app.post("/files/write")
async def files_write(req: WriteRequest):
    try:
        data = base64.b64decode(req.b64)
    except binascii.Error:
        raise HTTPException(400, "invalid b64")
    limit = _file_limit(req.file_limit_bytes)
    if len(data) > limit:
        raise _too_large(len(data), limit)
    if (
        _is_ext_path(req.path)
        or req.nofollow
        or req.exclusive
        or req.expected_mtime is not None
    ):
        t = await _fs_target(req.user_id, req.path, req.mounts, write=True)
        res = await asyncio.to_thread(
            _fs_write, t, data, req.expected_mtime, req.exclusive
        )
        return {"ok": True, **res, "cache_ttl_days": CACHE_TTL_REPORTED}
    ws = await _workspace(req.user_id)
    p = _safe_path(ws, req.path)
    try:
        # 無い親は 1 段ずつ作って jail のユーザーの物にする (`p.parent.mkdir(parents=True)` は
        # root 所有で作り、jail がその中で rm / mv できなかった)。p は resolve 済みで ws の中
        await asyncio.to_thread(_makedirs_owned, ws.resolve(), p.parent, req.path)
        p.write_bytes(data)
    except OSError as e:
        if _nospace(e):
            raise HTTPException(507, QUOTA_FULL_DETAIL)
        raise
    os.chown(p, JAIL_UID, JAIL_UID)
    return {"ok": True, "size": len(data), "cache_ttl_days": CACHE_TTL_REPORTED}


# ---------------------------------------------------------------------------
# 生のバイト列の出し入れ (`/files/raw`) — base64-in-JSON を使わないストリーミングの口
# ---------------------------------------------------------------------------
# ファイルブラウザのアップロード / ダウンロードと、nucllei の send_to_workspace(url=) の取り込み。
# 1 GiB 級を運ぶので本文はチャンクで受け / 返し、メモリに載るのはチャンク分だけ。パラメータは
# query (`user_id` `path` `mounts` = MountIn の配列の JSON、`file_limit_bytes`、PUT は `exclusive`)。
# パスの扱いは `/files/*` の fd 経路と同じ (経路も末端も symlink を辿らない、新しく作る段は 65534 の
# 物、`/ext/...` は mounts の bind の中だけ)。上限は `_file_limit` (渡されなければ sandbox の上限)。
RAW_CHUNK = 1024 * 1024
# 書き込み途中の一時ファイルの名前。`.` 始まりなので一覧に出ない。確定は rename (上書き) か
# link + unlink (`exclusive` — 既に在れば 409 で、競り合っても上書きしない)
_RAW_TMP_PREFIX = ".upload-"


def _parse_mounts(raw: str) -> list[MountIn]:
    try:
        items = json.loads(raw or "[]")
        if not isinstance(items, list):
            raise ValueError("mounts must be a list")
        return [MountIn(**m) for m in items]
    except (ValueError, TypeError) as e:
        raise HTTPException(400, f"invalid mounts: {e}")


def _raw_open_tmp(t: _Target, exclusive: bool) -> tuple[int, int, str]:
    """書き先の親を (無い段は作って) 開き、その中に一時ファイルを作る。(親の fd, 一時ファイルの fd,
    一時ファイルの名前)。先に末端を見て、symlink・ディレクトリは 400、`exclusive` で在れば 409。"""
    segs = t.segs
    if not segs:
        raise HTTPException(400, f"is a directory: {t.path}")
    dfd = _open_dirs(t.base, segs[:-1], t.path, create=True)
    try:
        st = _lstat_at(segs[-1], dfd)
        if st is not None and stat.S_ISLNK(st.st_mode):
            raise HTTPException(400, f"path contains a symlink: {t.path}")
        if st is not None and stat.S_ISDIR(st.st_mode):
            raise HTTPException(400, f"is a directory: {t.path}")
        if exclusive and st is not None:
            raise _conflict("already exists", _mtime(st))
        tmp = f"{_RAW_TMP_PREFIX}{uuid.uuid4().hex}.part"
        fd = os.open(
            tmp,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o664,
            dir_fd=dfd,
        )
        try:
            os.fchown(fd, JAIL_UID, JAIL_UID)
        except BaseException:
            os.close(fd)
            os.unlink(tmp, dir_fd=dfd)
            raise
    except BaseException:
        os.close(dfd)
        raise
    return dfd, fd, tmp


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        view = view[n:]


def _raw_commit(dfd: int, fd: int, tmp: str, leaf: str, exclusive: bool):
    """一時ファイルを `leaf` として確定する。戻り値は確定したファイルの fstat。"""
    st = os.fstat(fd)
    if exclusive:
        try:
            os.link(tmp, leaf, src_dir_fd=dfd, dst_dir_fd=dfd, follow_symlinks=False)
        except FileExistsError:
            raise _conflict("already exists", None)
        os.unlink(tmp, dir_fd=dfd)
        return st
    cur = _lstat_at(leaf, dfd)
    if cur is not None and stat.S_ISDIR(cur.st_mode):
        raise HTTPException(400, f"is a directory: {leaf}")
    # rename は末端が symlink でもリンクそのものを置き換える (向こう側は触らない)
    os.rename(tmp, leaf, src_dir_fd=dfd, dst_dir_fd=dfd)
    return st


@app.put("/files/raw")
async def files_raw_put(
    request: Request,
    user_id: str,
    path: str,
    mounts: str = "[]",
    file_limit_bytes: Optional[int] = None,
    exclusive: bool = False,
):
    """本文 (application/octet-stream) をそのまま `path` に書く。チャンクで受けて一時ファイルに
    書き、全部受けてから rename で確定する — 途中で失敗 (上限超過 413・容量切れ 507・切断) したら
    一時ファイルを消し、元のファイルは変わらない。`exclusive` は既に在れば 409。
    Content-Length が上限を超えていれば本文を読まずに 413。"""
    t = await _fs_target(user_id, path, _parse_mounts(mounts), write=True)
    limit = _file_limit(file_limit_bytes)
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise _too_large(int(declared), limit)
    try:
        dfd, fd, tmp = await asyncio.to_thread(_raw_open_tmp, t, exclusive)
    except OSError as e:
        if _nospace(e):
            raise HTTPException(507, QUOTA_FULL_DETAIL)
        raise
    committed = False
    total = 0
    try:
        try:
            async for chunk in request.stream():
                if not chunk:
                    continue
                total += len(chunk)
                if total > limit:
                    # size は打ち切った時点の受信量 (実サイズの下限)
                    raise _too_large(total, limit)
                await asyncio.to_thread(_write_all, fd, chunk)
            st = await asyncio.to_thread(
                _raw_commit, dfd, fd, tmp, t.segs[-1], exclusive
            )
            committed = True
        except OSError as e:
            if _nospace(e):
                raise HTTPException(507, QUOTA_FULL_DETAIL)
            raise
    finally:
        os.close(fd)
        if not committed:
            try:
                os.unlink(tmp, dir_fd=dfd)
            except OSError:
                pass
        os.close(dfd)
    return {
        "ok": True,
        "path": _join(t, t.segs),
        "size": total,
        "mtime": _mtime(st),
        "cache_ttl_days": CACHE_TTL_REPORTED,
    }


@app.get("/files/raw")
async def files_raw_get(
    user_id: str,
    path: str,
    mounts: str = "[]",
    file_limit_bytes: Optional[int] = None,
):
    """`path` の中身をそのまま返す (StreamingResponse、Content-Length 付き)。開いた時点の大きさ
    までを返す (送っている間に伸びた分は送らない)。上限を超える物は 413。"""
    t = await _fs_target(user_id, path, _parse_mounts(mounts), write=False)
    limit = _file_limit(file_limit_bytes)
    fd, st = await asyncio.to_thread(_open_regular, t)
    size = st.st_size
    if size > limit:
        os.close(fd)
        raise _too_large(size, limit)

    async def body():
        remaining = size
        try:
            while remaining > 0:
                chunk = await asyncio.to_thread(os.read, fd, min(RAW_CHUNK, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk
        finally:
            os.close(fd)

    return StreamingResponse(
        body(),
        media_type="application/octet-stream",
        headers={"Content-Length": str(size), "X-File-Mtime": repr(_mtime(st))},
    )


@app.post("/files/delete")
async def files_delete(req: DeleteRequest):
    """削除。workspace の中 (trash/ の外) は既定で trash/ の直下へ移す (同名は `-2` `-3`…。
    応答 `{ok, trashed: true, path: "trash/<名前>"}`)。`permanent: true`・trash/ の中・`/ext/...`
    はファイル・ディレクトリ (中身ごと) を完全に消す (`{ok, trashed: false}`)。symlink は
    リンクそのものに効く。"""
    t = await _fs_target(req.user_id, req.path, req.mounts, write=True)
    return await asyncio.to_thread(_fs_delete, t, req.permanent)


@app.post("/files/rename")
async def files_rename(req: RenameRequest):
    t = await _fs_target(req.user_id, req.path, req.mounts, write=True)
    d = await _fs_target(req.user_id, req.dest, req.mounts, write=True)
    return await asyncio.to_thread(_fs_rename, t, d)


@app.post("/files/mkdir")
async def files_mkdir(req: PathRequest):
    t = await _fs_target(req.user_id, req.path, req.mounts, write=True)
    return await asyncio.to_thread(_fs_mkdir, t)


@app.post("/files/usage")
async def files_usage(req: UsageRequest):
    """workspace の使用量 (quota = loopback ext4 の容量)。`/ext` は容量上限を持たないので対象外。"""
    ws = await _workspace(req.user_id)
    st = await asyncio.to_thread(os.statvfs, ws)
    return {
        "used_bytes": (st.f_blocks - st.f_bfree) * st.f_frsize,
        "total_bytes": st.f_blocks * st.f_frsize,
        "quota_gb": QUOTA_OVERRIDES.get(req.user_id, QUOTA_GB),
        "cache_ttl_days": CACHE_TTL_REPORTED,
    }


@app.delete("/workspace/{user_id}")
async def delete_workspace(user_id: str):
    """外部 stale-img GC の mechanism (sandbox/docs/health.md ④)。破壊は app のロック下でのみ
    行い、外部スクリプトは fs を直接触らない (mount/remount との TOCTOU 回避)。.img は即 unlink
    せず trash rename (誤削除の回復余地を残す)。app は DB を持たず「誰を消すか」は外部が決める。"""
    if not _USER_ID_RE.match(user_id):
        raise HTTPException(400, f"invalid user_id: {user_id!r}")
    async with _mount_lock(user_id):
        if _has_live_session(user_id):
            return {"deleted": False, "reason": "live_session"}
        if _user_lock(user_id).locked():
            return {"deleted": False, "reason": "busy"}
        ws = WORKSPACES / user_id
        img = IMAGES / f"{user_id}.img"
        if os.path.ismount(ws):
            try:
                await asyncio.to_thread(_run, ["umount", str(ws)])
            except RuntimeError as e:
                if "busy" in str(e).lower():
                    return {"deleted": False, "reason": "ebusy"}
                raise
        _ws_registry.pop(user_id, None)
        _mount_locks.pop(user_id, None)
        trashed = None
        if img.exists():
            dest = img.parent / f"{img.name}.trash-{int(time.time())}"
            os.replace(img, dest)
            trashed = dest.name
        _last_used_marker(user_id).unlink(missing_ok=True)
        return {"deleted": True, "trashed": trashed}
