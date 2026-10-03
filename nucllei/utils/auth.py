import logging
import time
import uuid
import jwt


from datetime import datetime, timedelta, timezone
from pytz import UTC
from typing import Optional, Union

from nucllei.models.users import Users

from nucllei.constants import ERROR_MESSAGES
from nucllei.env import (
    WEBUI_SECRET_KEY,
    WEBUI_AUTH_COOKIE_SAME_SITE,
    WEBUI_AUTH_COOKIE_SECURE,
    SRC_LOG_LEVELS,
)
from nucllei.utils.misc import parse_duration

from fastapi import BackgroundTasks, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext

logging.getLogger("passlib").setLevel(logging.ERROR)

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["OAUTH"])

SESSION_SECRET = WEBUI_SECRET_KEY
ALGORITHM = "HS256"

##############
# Auth Utils
##############


# llens(#28): get_license_data / override_static を撤去 — 起動時に
# api.openwebui.com へ同期 POST する upstream のライセンス照会は外部ネットワーク
# 必須機能を持たない方針(CLAUDE.md)に反し、リモート応答で static ファイルを上書きする経路も不要。

bearer_security = HTTPBearer(auto_error=False)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password, hashed_password):
    return (
        pwd_context.verify(plain_password, hashed_password) if hashed_password else None
    )


def get_password_hash(password):
    return pwd_context.hash(password)


def create_token(data: dict, expires_delta: Union[timedelta, None] = None) -> str:
    payload = data.copy()

    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
        payload.update({"exp": expire})

    encoded_jwt = jwt.encode(payload, SESSION_SECRET, algorithm=ALGORITHM)
    return encoded_jwt


def decode_token(token: str) -> Optional[dict]:
    try:
        decoded = jwt.decode(token, SESSION_SECRET, algorithms=[ALGORITHM])
        return decoded
    except Exception:
        return None


def extract_token_from_auth_header(auth_header: str):
    return auth_header[len("Bearer ") :]


def create_api_key():
    key = str(uuid.uuid4()).replace("-", "")
    return f"sk-{key}"


def get_http_authorization_cred(auth_header: Optional[str]):
    if not auth_header:
        return None
    try:
        scheme, credentials = auth_header.split(" ")
        return HTTPAuthorizationCredentials(scheme=scheme, credentials=credentials)
    except Exception:
        return None


####################################
# Idle timeout(スライディング期限)
####################################
#
# llens: `JWT_EXPIRES_IN` の意味は「発行からの絶対期限」ではなく
# **「無操作でこの時間が経ったら切れる」** = アイドルタイムアウトである。
# 共有 PC(北大病院の使い回し端末)の離席対策 — `_memos/auth-profile.md` 参照。
#
# 延命の条件は 2 つだけ:
#   1. **人の操作を伴うリクエスト**であること。FE(`src/lib/apis/fetch.ts`)が
#      最後の延命以降にキー入力・クリック等があった場合にだけ
#      `X-Nucllei-Activity` を付ける。チャット画面の 5 秒ポーリングのような
#      機械的なリクエストにはヘッダが付かないので、**タブを開いたまま離席しても
#      延命されない**(将来ポーリングが増えても自動的に安全側になる —
#      endpoint の除外リストを持たないのが要点)。
#   2. 現在の token が**寿命の半分を過ぎている**こと。毎リクエスト発行しない。
#
# `JWT_EXPIRES_IN` が `-1` / `0`(無期限)のときは何もしない(既定は現状維持)。
# **cookie セッション限定** — API キー(`sk-`)経路は延命しない。
#
# 実際の `Set-Cookie` は `nucllei/main.py` の HTTP middleware が
# `apply_session_renewal()` で行う。依存関数の sub-response に set_cookie しても、
# endpoint が Response を直接返す場合(chat completion の StreamingResponse 等)は
# FastAPI がその header を捨てるため(fastapi/routing.py)、依存関数だけでは
# 「メッセージ送信」という最も人間的な操作で延命が落ちる。

SESSION_ACTIVITY_HEADER = "X-Nucllei-Activity"


def session_idle_timeout_seconds(jwt_expires_in) -> Optional[int]:
    """`JWT_EXPIRES_IN` をアイドルタイムアウトの秒数に直す。無期限なら None。

    `/api/config` が FE へ露出する値でもある(FE 側タイマの TTL)。
    """
    try:
        expires_delta = parse_duration(str(jwt_expires_in))
    except Exception:
        # 設定値が壊れている — 無期限として扱う(延命もタイマも動かさない)
        return None
    return int(expires_delta.total_seconds()) if expires_delta else None


def mark_session_renewal(request: Request, user_id: str, token_data: dict) -> None:
    """人の操作を伴う cookie セッションのリクエストで、必要なら token を再発行する。

    発行した token は `request.state` に積むだけ。cookie への反映は
    `apply_session_renewal()`(main.py の middleware)が行う。
    """
    if not request.headers.get(SESSION_ACTIVITY_HEADER):
        return

    lifetime = session_idle_timeout_seconds(request.app.state.config.JWT_EXPIRES_IN)
    if not lifetime:
        # `-1` / `0` = 無期限。アイドルタイムアウトも無い
        return

    expires_delta = timedelta(seconds=lifetime)
    exp = token_data.get("exp")
    if exp is not None and (exp - time.time()) > lifetime / 2:
        # まだ寿命の半分以上残っている — 発行しない
        return
    # exp が無い token(無期限だった頃に発行されたもの)は、ここで初めて
    # 期限付きに切り替える = 設定変更が次の操作から効く

    request.state.renewed_session_token = create_token(
        data={"id": user_id}, expires_delta=expires_delta
    )
    request.state.renewed_session_expires_at = int(time.time() + lifetime)


def apply_session_renewal(request: Request, response: Response) -> None:
    """`mark_session_renewal()` が積んだ token を cookie に書く。

    cookie 属性は `routers/auths.py` の発行箇所と同一でなければならない
    (80/443 併用の配備で `samesite` / `secure` がずれると壊れる)。
    """
    token = getattr(request.state, "renewed_session_token", None)
    if not token:
        return

    response.set_cookie(
        key="token",
        value=token,
        expires=datetime.fromtimestamp(
            request.state.renewed_session_expires_at, timezone.utc
        ),
        httponly=True,
        samesite=WEBUI_AUTH_COOKIE_SAME_SITE,
        secure=WEBUI_AUTH_COOKIE_SECURE,
    )


def get_session_expires_at(request: Request) -> Optional[int]:
    """現在のセッション token の有効期限(epoch 秒)。無期限 / 不明なら None。"""
    renewed = getattr(request.state, "renewed_session_expires_at", None)
    if renewed is not None:
        return renewed

    cred = getattr(request.state, "token", None)
    token = cred.credentials if cred else request.cookies.get("token")
    if not token or token.startswith("sk-"):
        return None

    data = decode_token(token)
    exp = data.get("exp") if data else None
    return int(exp) if exp is not None else None


def get_current_user(
    request: Request,
    background_tasks: BackgroundTasks,
    auth_token: HTTPAuthorizationCredentials = Depends(bearer_security),
):
    token = None

    if auth_token is not None:
        token = auth_token.credentials

    if token is None and "token" in request.cookies:
        token = request.cookies.get("token")

    if token is None:
        raise HTTPException(status_code=403, detail="Not authenticated")

    # auth by api key
    if token.startswith("sk-"):
        if not request.state.enable_api_key:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.API_KEY_NOT_ALLOWED
            )

        if request.app.state.config.ENABLE_API_KEY_ENDPOINT_RESTRICTIONS:
            # llens(GHSA-57q6-fvp4-pqmm / CVE-2026-45339): 空要素を除外する。
            # API_KEY_ALLOWED_ENDPOINTS は既定 "" で、素朴に split(",") すると [""] となり
            # `path.startswith("" + "/")` = 全 path に一致 → 制限が「全許可」に無効化される
            # (末尾カンマ等の空要素でも同様)。空リストなら下の any() は False となり全拒否。
            allowed_paths = [
                path.strip()
                for path in str(
                    request.app.state.config.API_KEY_ALLOWED_ENDPOINTS
                ).split(",")
                if path.strip()
            ]

            # Check if the request path matches any allowed endpoint.
            if not any(
                request.url.path == allowed
                or request.url.path.startswith(allowed + "/")
                for allowed in allowed_paths
            ):
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.API_KEY_NOT_ALLOWED
                )

        return get_current_user_by_api_key(token)

    # auth by jwt token
    try:
        data = decode_token(token)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    if data is not None and "id" in data:
        user = Users.get_user_by_id(data["id"])
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ERROR_MESSAGES.INVALID_TOKEN,
            )
        else:
            # Refresh the user's last active timestamp asynchronously
            # to prevent blocking the request
            if background_tasks:
                background_tasks.add_task(Users.update_user_last_active_by_id, user.id)

            # llens: アイドルタイムアウト(スライディング期限)の延命判定。
            # ここに来るのは cookie / Bearer の JWT セッションのみ
            # (API キーは上で return 済み)。
            mark_session_renewal(request, user.id, data)
        return user
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )


def get_current_user_by_api_key(api_key: str):
    user = Users.get_user_by_api_key(api_key)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.INVALID_TOKEN,
        )
    else:
        Users.update_user_last_active_by_id(user.id)

    return user


def get_verified_user(user=Depends(get_current_user)):
    if user.role not in {"user", "admin"}:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )
    return user


def get_admin_user(user=Depends(get_current_user)):
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )
    return user
