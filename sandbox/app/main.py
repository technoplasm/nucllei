"""nucllei sandbox サービス: nsjail で隔離した Python / shell 実行 API。

設計・セキュリティモデル・API 仕様は sandbox/README.md を参照。
"""

import asyncio
import base64
import binascii
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
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
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
# tool の成果物回収 (/files/read = fetch_from_workspace / exports 自動添付) の単一ファイル上限。
# nucllei 側のユーザーアップロード制限とは独立 (別レイヤ)。read は base64 で JSON に載せる実装
# のため、ファイルサイズの ~2.3 倍が service プロセスの RAM に一時的に載る → 真の無制限には
# しない (巨大ファイルで OOM しうる)。恒久値は compose の SANDBOX_FILE_LIMIT_BYTES。GB 級を
# 恒常的に出すなら base64-in-JSON をやめて raw ストリーム化する必要がある (現状は未対応)。
FILE_LIMIT = int(os.environ.get("SANDBOX_FILE_LIMIT_BYTES", str(25 * 1024 * 1024)))

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
# になる (`/ext/cage/static/X` は jail でも URL でも同じ物)。モデルが読み替えを要らない。
# 全ユーザーに同じものが見える点を含め、詳細は README「参照用データを jail に見せる」。
RO_MOUNT_ROOT = Path("/ext")

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

    for d in (ws / "imports", ws / "exports", ws / ".exec"):
        if not d.exists():
            d.mkdir(parents=True)
            os.chown(d, JAIL_UID, JAIL_UID)
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


def _snapshot_exports(ws: Path) -> dict[str, tuple[int, int]]:
    out = ws / "exports"
    snap = {}
    for p in out.rglob("*"):
        if p.is_file():
            st = p.stat()
            snap[str(p.relative_to(out))] = (st.st_mtime_ns, st.st_size)
    return snap


def _collect_exports(ws: Path, before: dict) -> list[dict]:
    """exports/ の実行前後差分 (新規 + 更新) を b64 で返す。"""
    results = []
    for rel, sig in sorted(_snapshot_exports(ws).items()):
        if before.get(rel) == sig:
            continue
        p = ws / "exports" / rel
        size = sig[1]
        entry = {"name": rel, "size": size, "truncated": size > FILE_LIMIT}
        if not entry["truncated"]:
            entry["b64"] = base64.b64encode(p.read_bytes()).decode()
        results.append(entry)
    return results


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
        if name.startswith("."):
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


def _jail_base(ws: Path, wall_s: int) -> list[str]:
    """stateless exec と常駐 kernel で共通の nsjail 引数。wall_s は --time_limit
    (プロセスの生存上限。stateless は実行秒、kernel はセッション TTL のバックストップ)。"""
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
    for p in DEV_BINDS:
        if os.path.exists(p):
            cmd += ["--bindmount", p]  # rw (デバイスノード)
    for k, v in JAIL_ENV.items():
        cmd += ["--env", f"{k}={v}"]
    return cmd


def _nsjail_cmd(ws: Path, timeout_s: int, argv: list[str]) -> list[str]:
    # stateless: プロセス = 1 実行。time_limit と rlimit_cpu を実行秒に張る。
    cmd = _jail_base(ws, timeout_s)
    cmd += ["--rlimit_cpu", str(timeout_s)]
    return cmd + ["--"] + argv


def _kernel_nsjail_cmd(ws: Path) -> list[str]:
    # stateful: 常駐 kernel。生存上限はセッション TTL (バックストップ)。per-cell の
    # timeout は kernel が SIGALRM で内部処理するので rlimit_cpu は張らない。
    cmd = _jail_base(ws, SESSION_TTL_S)
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

    def __init__(self, user_id: str, chat_id: str, proc):
        self.user_id = user_id
        self.chat_id = chat_id
        self.proc = proc
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

    async def get(self, user_id: str, chat_id: str) -> tuple[Session, bool]:
        """既存 or 新規の Session を返す。2 要素目 = 新規作成 (reset) したか。"""
        async with self._lock:
            key = self._key(user_id, chat_id)
            sess = self._sessions.get(key)
            if sess is not None and sess.alive and sess.proc.poll() is None:
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
                    _kernel_nsjail_cmd(ws),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
            )
            sess = Session(user_id, chat_id, proc)
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


class ExecRequest(BaseModel):
    user_id: str
    lang: Literal["python", "shell"]
    code: str
    timeout_s: Optional[int] = None
    imports: list[FileIn] = Field(default_factory=list)


class PathRequest(BaseModel):
    user_id: str
    path: str = "."


class WriteRequest(BaseModel):
    user_id: str
    path: str
    b64: str


class CellRequest(BaseModel):
    user_id: str
    chat_id: str
    code: str
    timeout_s: Optional[int] = None


@app.on_event("startup")
async def _startup():
    _log_auth_state()
    _log_ro_mounts()
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
    ]
    lines = []
    for name, typ, help_, val in m:
        lines += [f"# HELP {name} {help_}", f"# TYPE {name} {typ}", f"{name} {val}"]
    return PlainTextResponse("\n".join(lines) + "\n")


async def _abandon_if_client_gone(request: Request):
    """lock 待ちの間に client (nucllei tool) が timeout で切れていたら実行しない。

    tool 側は待ち時間込みで timeout_s+30 秒しか待たず ERROR を返す。ここで確認しないと
    切れた要求が lock 取得後にそのまま走り (結果は誰にも返らない)、モデルの再試行と
    合わせて同じコードが 2 回実行される (exports の二重生成、kernel なら namespace 副作用)。
    """
    if await request.is_disconnected():
        raise HTTPException(499, "client disconnected while waiting for lock")


@app.post("/exec")
async def exec_code(req: ExecRequest, request: Request):
    timeout_s = max(1, min(req.timeout_s or TIMEOUT_DEFAULT_S, TIMEOUT_MAX_S))
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
            dest = ws / "imports" / name
            dest.write_bytes(data)
            os.chown(dest, JAIL_UID, JAIL_UID)

        ext = "py" if req.lang == "python" else "sh"
        script = ws / ".exec" / f"{uuid.uuid4().hex[:8]}.{ext}"
        script.write_text(req.code, encoding="utf-8")
        os.chown(script, JAIL_UID, JAIL_UID)
        jail_script = f"/workspace/.exec/{script.name}"
        if req.lang == "python":
            argv = [f"{VENV_BIN}/python", "-u", jail_script]
        else:
            argv = ["/bin/bash", jail_script]

        before = _snapshot_exports(ws)
        t0 = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *_nsjail_cmd(ws, timeout_s, argv),
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
            "exports": _collect_exports(ws, before),
        }


@app.post("/exec_cell")
async def exec_cell(req: CellRequest, request: Request):
    """stateful run_python: (user, chat) の常駐 kernel で永続 namespace 実行。"""
    if not req.chat_id:
        raise HTTPException(400, "chat_id required for stateful exec")
    timeout_s = max(1, min(req.timeout_s or TIMEOUT_DEFAULT_S, TIMEOUT_MAX_S))
    ws = await _workspace(req.user_id)
    sess, was_reset = await _sessions.get(req.user_id, req.chat_id)

    async with sess.lock, _sem:
        await _abandon_if_client_gone(request)
        sess.last_used = time.monotonic()
        before = _snapshot_exports(ws)
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
            return {
                "stdout": "",
                "error": (
                    "セルが応答せず中断不能だったため実行環境を再起動しました "
                    "(変数・import は失われました)"
                    if hard
                    else f"実行環境との通信に失敗しました: {e!r}"
                ),
                "timed_out": hard,
                "session_reset": True,
                "duration_s": 0,
                "stdout_truncated": False,
                "exports": _collect_exports(ws, before),
            }
        duration_s = round(time.monotonic() - t0, 2)
        sess.last_used = time.monotonic()

        if resp is None:  # kernel が EOF (クラッシュ)
            await _sessions.drop(sess)
            return {
                "stdout": "",
                "error": "実行環境が異常終了しました (変数・import は失われました)",
                "timed_out": False,
                "session_reset": True,
                "duration_s": duration_s,
                "stdout_truncated": False,
                "exports": _collect_exports(ws, before),
            }

        return {
            "stdout": resp.get("stdout", ""),
            "error": resp.get("error"),
            "timed_out": resp.get("timed_out", False),
            "session_reset": was_reset,
            "duration_s": duration_s,
            "stdout_truncated": resp.get("truncated", False),
            "exports": _collect_exports(ws, before),
        }


@app.post("/files/list")
async def files_list(req: PathRequest):
    ws = await _workspace(req.user_id)
    base = _safe_path(ws, req.path)
    if not base.exists():
        return {"entries": []}
    entries = []
    for p in sorted(base.rglob("*")):
        rel = p.relative_to(ws)
        # `.` 始まりの要素 (.exec / .cache 等の内部・隠しファイル) は見せない
        if any(part.startswith(".") for part in rel.parts):
            continue
        if p.is_file():
            entries.append({"path": str(rel), "size": p.stat().st_size})
        if len(entries) >= 500:
            break
    return {"entries": entries}


@app.post("/files/read")
async def files_read(req: PathRequest):
    ws = await _workspace(req.user_id)
    p = _safe_path(ws, req.path)
    if not p.is_file():
        raise HTTPException(404, f"not a file: {req.path}")
    size = p.stat().st_size
    if size > FILE_LIMIT:
        raise HTTPException(413, f"file too large: {size} bytes (limit {FILE_LIMIT})")
    return {
        "name": p.name,
        "size": size,
        "b64": base64.b64encode(p.read_bytes()).decode(),
    }


@app.post("/files/write")
async def files_write(req: WriteRequest):
    ws = await _workspace(req.user_id)
    p = _safe_path(ws, req.path)
    try:
        data = base64.b64decode(req.b64)
    except binascii.Error:
        raise HTTPException(400, "invalid b64")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    os.chown(p, JAIL_UID, JAIL_UID)
    return {"ok": True, "size": len(data)}


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
