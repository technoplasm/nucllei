from enum import Enum


class MESSAGES(str, Enum):
    DEFAULT = lambda msg="": f"{msg if msg else ''}"
    MODEL_ADDED = lambda model="": f"The model '{model}' has been added successfully."
    MODEL_DELETED = (
        lambda model="": f"The model '{model}' has been deleted successfully."
    )


class WEBHOOK_MESSAGES(str, Enum):
    DEFAULT = lambda msg="": f"{msg if msg else ''}"
    USER_SIGNUP = lambda username="": (
        f"New user signed up: {username}" if username else "New user signed up"
    )


class ERROR_MESSAGES(str, Enum):
    def __str__(self) -> str:
        return super().__str__()

    DEFAULT = lambda err="": "問題が発生しました。" if err == "" else f"エラー: {err}"
    ENV_VAR_NOT_FOUND = "Required environment variable not found. Terminating now."
    CREATE_USER_ERROR = "アカウントの作成中に問題が発生しました。しばらくしてからもう一度お試しください。解決しない場合は管理者に問い合わせてください。"
    EMAIL_MISMATCH = "Uh-oh! This email does not match the email your provider is registered with. Please check your email and try again."
    EMAIL_TAKEN = "このメールアドレスは既に登録されています。既存のアカウントでサインインするか、別のメールアドレスを使ってください。"
    USERNAME_TAKEN = (
        "Uh-oh! This username is already registered. Please choose another username."
    )
    PASSWORD_TOO_LONG = "パスワードが長すぎます。72 バイト未満にしてください (全角文字は 1 文字 3 バイトです)。"
    FILE_EXISTS = "Uh-oh! This file is already registered. Please choose another file."

    ID_TAKEN = "この ID は既に使われています。別の ID を指定してください。"
    MODEL_ID_TAKEN = (
        "このモデル ID は既に使われています。別のモデル ID を指定してください。"
    )

    INVALID_TOKEN = "セッションの有効期限が切れたか、認証情報が無効です。もう一度サインインしてください。"
    INVALID_CRED = "メールアドレスまたはパスワードが正しくありません。入力を確かめて、もう一度サインインしてください。"
    INVALID_EMAIL_FORMAT = "メールアドレスの形式が正しくありません。入力を確かめてください (例: yourname@example.com)。"
    INVALID_PASSWORD = (
        "現在のパスワードが正しくありません。入力を確かめて、もう一度お試しください。"
    )
    INVALID_TRUSTED_HEADER = "認証プロバイダから信頼済みヘッダが届いていません。管理者に問い合わせてください。"

    EXISTING_USERS = "既にユーザーがいるため、認証を無効にできません。WEBUI_AUTH を無効にできるのは、ユーザーが 1 人もいない新規の配備だけです。"

    UNAUTHORIZED = "サインインしていないか、この操作を行う権限がありません。"
    ACCESS_PROHIBITED = (
        "この操作を行う権限がありません。必要な場合は管理者に問い合わせてください。"
    )
    ACTION_PROHIBITED = "この操作はセキュリティ上の理由で制限されています。"

    FILE_NOT_SENT = "FILE_NOT_SENT"
    FILE_NOT_SUPPORTED = "このファイル形式には対応していません。対応している形式のファイルをアップロードしてください。"

    NOT_FOUND = "見つかりませんでした。"
    USER_NOT_FOUND = "ユーザーが見つかりませんでした。"
    API_KEY_NOT_FOUND = "API キーがありません。"
    API_KEY_NOT_ALLOWED = "API キーでの利用は許可されていません。"

    MALICIOUS = "Unusual activities detected, please try again in a few minutes."

    PANDOC_NOT_INSTALLED = "Pandoc is not installed on the server. Please contact your administrator for assistance."
    INCORRECT_FORMAT = (
        lambda err="": f"Invalid format. Please use the correct format{err}"
    )
    RATE_LIMIT_EXCEEDED = "API rate limit exceeded"

    MODEL_NOT_FOUND = lambda name="": f"モデル「{name}」が見つかりません。"
    OPENAI_NOT_FOUND = lambda name="": "OpenAI 互換の接続が見つかりません。"
    OLLAMA_NOT_FOUND = "Ollama に接続できませんでした。"
    CREATE_API_KEY_ERROR = "API キーの作成中に問題が発生しました。しばらくしてからもう一度お試しください。解決しない場合は管理者に問い合わせてください。"
    API_KEY_CREATION_NOT_ALLOWED = "この配備では API キーを作成できません。"

    EMPTY_CONTENT = "ファイルが空です。中身のあるファイルをアップロードしてください。"

    DB_NOT_SQLITE = "この機能は、データベースに SQLite を使っている配備でだけ使えます。"

    INVALID_URL = (
        "Oops! The URL you provided is invalid. Please double-check and try again."
    )

    OLLAMA_API_DISABLED = (
        "The Ollama API is disabled. Please enable it to use this feature."
    )

    FILE_TOO_LARGE = (
        lambda size="": f"ファイルが大きすぎます。{size} 未満のファイルをアップロードしてください。"
    )

    DUPLICATE_CONTENT = (
        "Duplicate content detected. Please provide unique content to proceed."
    )
    FILE_NOT_PROCESSED = "Extracted content is not available for this file. Please ensure that the file is processed before proceeding."


class TASKS(str, Enum):
    def __str__(self) -> str:
        return super().__str__()

    DEFAULT = lambda task="": f"{task if task else 'generation'}"
    TITLE_GENERATION = "title_generation"
    QUERY_GENERATION = "query_generation"
