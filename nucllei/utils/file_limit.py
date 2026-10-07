"""1 ファイルの出入りの上限 — 情報源は RC `ATTACHMENT_MAX_SIZE_MB` の 1 つ (ken 決定、llens 依頼)。

従う経路: チャットへのアップロード (`routers/files.py`)、`send_to_workspace` の file_id / url、
exports の自動添付、`fetch_from_workspace`、`feed_image(path=)` の取り出し。管理画面 (ワークスペース > 設定 > ファイルの
「最大アップロードサイズ」) で変えるとすぐ効く — 値はリクエストのたびにここで読む (キャッシュしない)。

揃えないもの: write_file / read_file とファイルブラウザのテキスト編集 (`SANDBOX_WRITE_FILE_LIMIT_BYTES`、
context に載せるテキストの上限)、quota、**ファイルブラウザのアップロード・ダウンロード**
(`routers/sandbox_files.py`。1 GB 級の動画などを直接 sandbox に入れる口 — 上限は sandbox の安全上限と
quota にする方針で、base64-in-JSON をやめるストリーミング化と一緒に別作業。ken 決定 2026-10-07)。

sandbox は nucllei の設定を読めないので、該当リクエスト (`/exec` `/exec_cell` `/files/read`
`/files/write`) に `file_limit_bytes` で渡す (`sandbox_limit_fields`)。sandbox は渡された値と
自分の安全上限 (env `SANDBOX_FILE_LIMIT_BYTES`、OOM 防止。既定 1 GiB) の小さいほうに従う。
安全上限は sandbox の `/healthz` から起動時に読み (`probe_sandbox_safety_limit`)、こちらの上限の
方が大きい (無制限を含む) なら warning を出す (`warn_if_over_sandbox_limit`)。
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

# sandbox の安全上限の既定 (sandbox/app/main.py の `SANDBOX_FILE_LIMIT_BYTES` の既定と同じ)。
# `/healthz` から実際の値を読めるまでの仮の値 — nucllei 自身がメモリに載せる経路
# (URL の取得・Storage の読み出し) を無制限のときにも有限に保つためだけに使う
SANDBOX_SAFETY_DEFAULT = 1024 * _MIB

_sandbox_safety: Optional[int] = (
    None  # sandbox の /healthz の file_limit_bytes (読めたら)
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


def transfer_cap(limit_bytes: Optional[int]) -> int:
    """nucllei 自身がメモリに載せる経路の上限。無制限なら sandbox の安全上限 (読めていなければ既定)。"""
    if limit_bytes is not None:
        return limit_bytes
    return _sandbox_safety or SANDBOX_SAFETY_DEFAULT


def sandbox_safety_limit() -> Optional[int]:
    """`/healthz` から読めた sandbox の安全上限 (まだ / 古い sandbox なら None)。"""
    return _sandbox_safety


def warn_if_over_sandbox_limit(limit_bytes: Optional[int]) -> None:
    """こちらの上限が sandbox の安全上限より大きい (無制限を含む) なら warning を 1 行。
    組 (上限, 安全上限) が変わったときだけ出す (毎リクエストで溢れさせない)。"""
    global _warned
    safety = _sandbox_safety
    if safety is None:
        return
    over = limit_bytes is None or limit_bytes > safety
    pair = (limit_bytes, safety)
    if not over or pair == _warned:
        return
    _warned = pair
    log.warning(
        f"[file_limit] ATTACHMENT_MAX_SIZE_MB ({format_limit(limit_bytes)}) は sandbox の安全上限 "
        f"SANDBOX_FILE_LIMIT_BYTES ({format_limit(safety)}) より大きい — sandbox を通る経路 "
        "(send_to_workspace・exports の自動添付・fetch_from_workspace・"
        "feed_image(path=)) では sandbox の安全上限が勝つ。揃えるなら sandbox の "
        "SANDBOX_FILE_LIMIT_BYTES を上げる (base64-in-JSON で約 2.3 倍のメモリを使う)"
    )


def set_sandbox_safety_limit(value: Optional[int]) -> None:
    """安全上限を記録して、今の上限と比べる (probe とテストの口)。"""
    global _sandbox_safety
    _sandbox_safety = value if isinstance(value, int) and value > 0 else None
    warn_if_over_sandbox_limit(attachment_limit_bytes())


async def probe_sandbox_safety_limit(
    attempts: int = 20, interval_s: float = 15.0
) -> Optional[int]:
    """sandbox の `/healthz` から安全上限 (`file_limit_bytes`) を読む。起動時に background で 1 回。

    sandbox は nucllei と同時に立ち上がるので、繋がるまで `interval_s` ごとに `attempts` 回まで
    やり直す。キーが無い (古い sandbox) なら黙って諦める。SANDBOX_URL の無い配備では何もしない。
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
                    # loop 不能の 503 でも body は返る (安全上限は読める)
                    body = await resp.json(content_type=None)
            value = body.get("file_limit_bytes") if isinstance(body, dict) else None
            set_sandbox_safety_limit(value)
            return _sandbox_safety
        except Exception as e:
            if i == attempts - 1:
                log.info(f"[file_limit] sandbox /healthz unreachable: {e!r}")
                return None
            await asyncio.sleep(interval_s)
    return None
