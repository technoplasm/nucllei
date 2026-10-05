import os
import secrets
from pathlib import Path

import typer
import uvicorn
from typing import Optional
from typing_extensions import Annotated

app = typer.Typer()

# 秘密鍵の永続先は DATA_DIR(Docker では /app/data volume — 再起動でセッションが飛ばない)。
# env.py の import 前に必要なので、同じ既定(ルート直下 data/)をここでも計算する。
DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).parent.parent / "data"))
KEY_FILE = DATA_DIR / ".webui_secret_key"


def version_callback(value: bool):
    if value:
        from nucllei.env import VERSION

        typer.echo(f"Nucllei version: {VERSION}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        Optional[bool], typer.Option("--version", callback=version_callback)
    ] = None,
):
    """Nucllei CLI."""


def _load_secret_key() -> None:
    """env に WEBUI_SECRET_KEY が無ければ `$DATA_DIR/.webui_secret_key` から入れる(無ければ生成)。

    旧 start.sh の秘密鍵ロジック。**nucllei.env を import する前に呼ぶ** — env.py は
    ENV=dev 以外で鍵が無いと起動を止める(固定鍵のフォールバックは dev 限定、2026-10-03)。
    serve だけでなく、同じ DATA_DIR で動く CLI(create-admin)も同じ鍵で動くべきなので共通化。
    """
    if os.getenv("WEBUI_SECRET_KEY"):
        return
    typer.echo(
        "Loading WEBUI_SECRET_KEY from file, not provided as an environment variable."
    )
    if not KEY_FILE.exists():
        typer.echo(f"Generating a new secret key and saving it to {KEY_FILE}")
        KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        # **secrets を使う**。upstream は random.randbytes(12) を base64 して 16 文字を
        # 書いていたが、random は Mersenne Twister で暗号用途の生成器ではない。
        # 長さも 12 バイト = 96 bit で、HS256 が要求する鍵長 (RFC 7518 3.2: ハッシュ出力
        # と同じ 32 バイト以上) を下回る — PyJWT 2.13 が InsecureKeyLengthWarning で
        # 指すのがこれ。JWT の署名鍵はセッション偽造の可否そのものなので強い方に倒す。
        # 既存の配備は鍵ファイルを持っているのでこの変更の影響を受けない
        # (作り直すと全ユーザのセッションが失効する。回すのは意図した時だけ)
        KEY_FILE.write_text(secrets.token_urlsafe(32))
    typer.echo(f"Loading WEBUI_SECRET_KEY from {KEY_FILE}")
    os.environ["WEBUI_SECRET_KEY"] = KEY_FILE.read_text()


@app.command()
def serve(
    host: str = "0.0.0.0",
    port: int = 8080,
):
    _load_secret_key()

    import nucllei.main  # we need set environment variables before importing main
    from nucllei.env import UVICORN_WORKERS  # Import the workers setting

    uvicorn.run(
        nucllei.main.app,
        host=host,
        port=port,
        forwarded_allow_ips="*",
        workers=UVICORN_WORKERS,
    )


@app.command()
def dev(
    host: str = "0.0.0.0",
    port: int = 8080,
    reload: bool = True,
):
    uvicorn.run(
        "nucllei.main:app",
        host=host,
        port=port,
        reload=reload,
        forwarded_allow_ips="*",
    )


@app.command(name="create-admin")
def create_admin(
    email: Annotated[str, typer.Option(..., help="Administrator email address.")],
    name: Annotated[str, typer.Option(help="Display name.")] = "Admin",
    password: Annotated[
        Optional[str],
        typer.Option(
            help="Password. Omit to be prompted (hidden input, not stored in shell history).",
        ),
    ] = None,
):
    """Create an administrator account from the server (server-side provisioning).

    llens: 公開オンボーディング画面の代わりに、運用者がサーバ上でこのコマンドを
    実行して最初の管理者を作成する。既存 DB でも新規 DB でも動く(import 時に
    マイグレーションが走る)。
    """
    if password is None:
        password = typer.prompt("Password", hide_input=True, confirmation_prompt=True)

    # import 時に alembic(config.run_migrations)マイグレーションが走り、
    # 新規 DB でもスキーマが用意される。重い import は関数内に閉じる(serve と同じ流儀)。
    # 鍵は serve と同じファイルから(ENV=prod のコンテナ内で docker exec しても落ちないように)
    _load_secret_key()
    import nucllei.config  # noqa: F401
    from nucllei.models.auths import Auths
    from nucllei.models.users import Users
    from nucllei.utils.auth import get_password_hash
    from nucllei.utils.misc import validate_email_format

    email = email.lower().strip()

    if not validate_email_format(email):
        typer.echo(f"Invalid email format: {email}", err=True)
        raise typer.Exit(code=1)

    # 下限・複雑さの制限は掛けない(運用判断 — 院内アカウント発行は管理者の裁量)。
    # bcrypt は 72 バイト超を黙って切り詰めるため、上限だけ signup と同じく弾く。
    if len(password.encode("utf-8")) > 72:
        typer.echo("Password must be at most 72 bytes.", err=True)
        raise typer.Exit(code=1)

    if Users.get_user_by_email(email):
        typer.echo(f"A user with email '{email}' already exists.", err=True)
        raise typer.Exit(code=1)

    user = Auths.insert_new_auth(
        email,
        get_password_hash(password),
        name,
        role="admin",
    )

    if not user:
        typer.echo("Failed to create the administrator account.", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Created administrator account: {user.email} (id={user.id})")


@app.command(name="dedup-tool-calls")
def dedup_tool_calls(
    apply: Annotated[
        bool, typer.Option("--apply", help="Write changes (default: dry-run).")
    ] = False,
    database_url: Annotated[
        Optional[str],
        typer.Option(help="Default: $DATABASE_URL, else $DATA_DIR/webui.db."),
    ] = None,
    backup_dir: Annotated[
        Optional[Path], typer.Option(help="Default: $DATA_DIR/backups.")
    ] = None,
):
    """一度きりの掃除: 同じ call id の重複 tool_calls ブロックを chat から除去する(refs #142)。

    詳細と本番での流し方は nucllei/maintenance/dedup_tool_calls.py の docstring。
    nucllei.config を import しない(migration を走らせない)。
    """
    from nucllei.maintenance.dedup_tool_calls import run

    run(database_url, apply, backup_dir)


if __name__ == "__main__":
    app()
