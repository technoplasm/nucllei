"""1 ファイルの上限は 2 つ — どの出入り口がどちらに従うかを固定する (ken 決定、llens 依頼)。

| 出入り口 | 従う上限 |
|---|---|
| チャットへのアップロード (`routers/files.py`) | 添付の上限 |
| `send_to_workspace(file_id=)` (添付一覧からの取り込みも) | 添付の上限 |
| `fetch_from_workspace` | 添付の上限 |
| ファイルブラウザのアップロード / ダウンロード (`routers/sandbox_files.py`) | sandbox の上限 |
| `send_to_workspace(url=)` | sandbox の上限 |
| `feed_image(path=)` | sandbox の上限 (と `FETCH_MAX_BYTES` の小さいほう) |

- **添付の上限** = RC `ATTACHMENT_MAX_SIZE_MB` (`attachment_limit_bytes`)。file_id を作る・file_id から
  運ぶ経路。管理画面 (ワークスペース > 設定 > ファイルの「最大アップロードサイズ」) で変えるとすぐ効く —
  値はリクエストのたびにここで読む (キャッシュしない)。sandbox を通る経路 (`/files/read`
  `/files/write`) には `file_limit_bytes` で渡す (`sandbox_limit_fields`)。sandbox は
  渡された値と自分の上限の小さいほうに従う
- **sandbox の上限** = sandbox の env `SANDBOX_FILE_MAX_SIZE_MB` (既定 1024、全員共通)。workspace に
  直接出し入れする経路 (file_id を作らない)。sandbox が持つ値で、nucllei は起動時に `/healthz` から
  読む (`probe_sandbox_file_limit`。読めるまでは既定 1024 MB を仮に使う)。運ぶのは sandbox の
  `/files/raw` (ストリーミング) — `feed_image(path=)` だけは中身をメモリに載せて画像・PDF として
  検証し、送信直前にもう一度 resolver (`FETCH_MAX_BYTES`) で読むので、その小さいほう
- 添付の上限が sandbox の上限より大きい (無制限を含む) なら warning (`warn_if_over_sandbox_limit`)

揃えないもの: write_file の本文とファイルブラウザのテキスト編集 (env `SANDBOX_TEXT_MAX_SIZE_MB`)、
quota。read_file は範囲読みでファイル全体の大きさを問わない。
"""

import asyncio
import logging
from typing import Any, Optional

from nucllei.env import SRC_LOG_LEVELS

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])

_MIB = 1024 * 1024

# 上限を変える場所 (エラー文に添える。管理画面の表記どおり)
ADMIN_SETTING = "ワークスペース > 設定 > ファイルの「最大アップロードサイズ」"

# sandbox の上限の既定 (sandbox/app/main.py の `SANDBOX_FILE_MAX_SIZE_MB` の既定と同じ)。
# `/healthz` から実際の値を読めるまでの仮の値
SANDBOX_FILE_DEFAULT = 1024 * _MIB

_sandbox_limit: Optional[int] = (
    None  # sandbox の /healthz の file_max_size_mb をバイトで (読めたら)
)
_warned: Optional[tuple] = None  # 最後に warning を出した (上限, 安全上限)


def _config_value(request_or_app) -> Any:
    """RC の値。`request_or_app` は Request / FastAPI app / None (None なら module の RC を直接)。

    app.state.config は module の RuntimeConfig をそのまま包む (`AppConfig`) ので、どちらで
    読んでも同じ値 — tool (background の続きのターンでは request が無い) は None で呼ぶ。
    """
    if request_or_app is not None:
        app = getattr(request_or_app, "app", request_or_app)
        try:
            return app.state.config.ATTACHMENT_MAX_SIZE_MB
        except (AttributeError, KeyError):
            pass
    from nucllei.config import ATTACHMENT_MAX_SIZE_MB

    return ATTACHMENT_MAX_SIZE_MB.value


def attachment_limit_bytes(request_or_app=None, user=None) -> Optional[int]:
    """1 ファイルの上限 (バイト)。0 / 空 = 無制限 = None。

    `user` は今は使わない — ユーザー / グループごとの上限 (#147) を入れるときにここで見る
    (呼び手は今から user を渡しておく)。
    """
    raw = _config_value(request_or_app)
    try:
        mb = float(raw) if raw not in (None, "") else 0.0
    except (TypeError, ValueError):
        log.warning(f"ATTACHMENT_MAX_SIZE_MB is not a number ({raw!r}); unlimited")
        return None
    if mb <= 0:
        return None
    return int(mb * _MIB)


def format_limit(limit_bytes: Optional[int]) -> str:
    """上限の表示 (MB)。設定は MB の整数なので、ふつうは「25 MB」の形になる。"""
    if limit_bytes is None:
        return "無制限"
    if limit_bytes % _MIB == 0:
        return f"{limit_bytes // _MIB} MB"
    if limit_bytes >= _MIB:
        return f"{limit_bytes / _MIB:.1f} MB"
    if limit_bytes >= 1024:
        return f"{limit_bytes / 1024:.1f} KB"
    return f"{limit_bytes} B"


def sandbox_limit_fields(limit_bytes: Optional[int]) -> dict:
    """sandbox へのリクエストに混ぜるフィールド。無制限なら何も渡さない (= sandbox の安全上限だけ)。"""
    warn_if_over_sandbox_limit(limit_bytes)
    return {} if limit_bytes is None else {"file_limit_bytes": int(limit_bytes)}


def sandbox_file_limit_bytes() -> int:
    """sandbox の上限 (バイト)。`/healthz` から読めていなければ既定 (1024 MB)。"""
    return _sandbox_limit or SANDBOX_FILE_DEFAULT


def sandbox_file_limit_probed() -> Optional[int]:
    """`/healthz` から読めた sandbox の上限 (まだ読めていなければ None)。画面の事前チェック用 —
    推測の値を出さない (読めていなければ sandbox の 413 で知る)。"""
    return _sandbox_limit


def transfer_cap(limit_bytes: Optional[int]) -> int:
    """添付の上限で nucllei 自身がメモリに載せる経路の上限。無制限なら sandbox の上限。"""
    if limit_bytes is not None:
        return limit_bytes
    return sandbox_file_limit_bytes()


def warn_if_over_sandbox_limit(limit_bytes: Optional[int]) -> None:
    """添付の上限が sandbox の上限より大きい (無制限を含む) なら warning を 1 行。
    組 (上限, sandbox の上限) が変わったときだけ出す (毎リクエストで溢れさせない)。"""
    global _warned
    cap = _sandbox_limit
    if cap is None:
        return
    over = limit_bytes is None or limit_bytes > cap
    pair = (limit_bytes, cap)
    if not over or pair == _warned:
        return
    _warned = pair
    log.warning(
        f"[file_limit] ATTACHMENT_MAX_SIZE_MB ({format_limit(limit_bytes)}) は sandbox の上限 "
        f"SANDBOX_FILE_MAX_SIZE_MB ({format_limit(cap)}) より大きい — sandbox を通る経路 "
        "(send_to_workspace(file_id=)・fetch_from_workspace) では "
        "sandbox の上限が勝つ。揃えるなら sandbox の SANDBOX_FILE_MAX_SIZE_MB を上げる"
        " (これらの経路は base64-in-JSON で約 2.3 倍のメモリを使う)"
    )


def set_sandbox_file_limit(value: Optional[int]) -> None:
    """sandbox の上限 (バイト) を記録して、今の添付の上限と比べる (probe とテストの口)。"""
    global _sandbox_limit
    _sandbox_limit = value if isinstance(value, int) and value > 0 else None
    warn_if_over_sandbox_limit(attachment_limit_bytes())


async def probe_sandbox_file_limit(
    attempts: int = 20, interval_s: float = 15.0
) -> Optional[int]:
    """sandbox の `/healthz` から上限 (`file_max_size_mb`) を読む。起動時に background で 1 回。

    sandbox は nucllei と同時に立ち上がるので、繋がるまで `interval_s` ごとに `attempts` 回まで
    やり直す。キーが無い (古い sandbox) なら黙って諦める (既定 1024 MB のまま)。SANDBOX_URL の
    無い配備では何もしない。
    """
    import aiohttp

    from nucllei.builtin_tools import sandbox

    if not sandbox.SANDBOX_URL:
        return None
    url = sandbox.SANDBOX_URL.rstrip("/") + "/healthz"
    for i in range(attempts):
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=5)
            ) as session:
                async with session.get(url) as resp:
                    # loop 不能の 503 でも body は返る (上限は読める)
                    body = await resp.json(content_type=None)
            mb = body.get("file_max_size_mb") if isinstance(body, dict) else None
            set_sandbox_file_limit(mb * _MIB if isinstance(mb, int) else None)
            return _sandbox_limit
        except Exception as e:
            if i == attempts - 1:
                log.info(f"[file_limit] sandbox /healthz unreachable: {e!r}")
                return None
            await asyncio.sleep(interval_s)
    return None


# 改名した env: 旧名 → 新名 (旧名は読まない。設定されていたら起動時に warning を 1 行)
RENAMED_ENV = {"SANDBOX_WRITE_FILE_LIMIT_BYTES": "SANDBOX_TEXT_MAX_SIZE_MB"}


def warn_renamed_env(environ=None) -> list[str]:
    """旧名の env が設定されていたら新名を案内する warning を出す (main.py の lifespan)。
    値は読み替えない。出した旧名を返す (テスト用)。"""
    import os

    environ = os.environ if environ is None else environ
    found = [old for old in RENAMED_ENV if old in environ]
    for old in found:
        log.warning(
            f"[file_limit] env {old} は廃止 (読まない)。{RENAMED_ENV[old]} (MB の整数) を使う"
        )
    return found
