import json
import logging
import sys
from typing import TYPE_CHECKING

from loguru import logger

from nucllei.env import GLOBAL_LOG_LEVEL


if TYPE_CHECKING:
    from loguru import Record


def stdout_format(record: "Record") -> str:
    """
    Generates a formatted string for log records that are output to the console. This format includes a timestamp, log level, source location (module, function, and line), the log message, and any extra data (serialized as JSON).

    Parameters:
    record (Record): A Loguru record that contains logging details including time, level, name, function, line, message, and any extra context.
    Returns:
    str: A formatted log string intended for stdout.
    """
    record["extra"]["extra_json"] = json.dumps(record["extra"])
    return (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level> - {extra[extra_json]}"
        "\n{exception}"
    )


class InterceptHandler(logging.Handler):
    """
    Intercepts log records from Python's standard logging module
    and redirects them to Loguru's logger.
    """

    def emit(self, record):
        """
        Called by the standard logging module for each log event.
        It transforms the standard `LogRecord` into a format compatible with Loguru
        and passes it to Loguru's logger.
        """
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = sys._getframe(6), 6
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def start_logger():
    """
    Initializes and configures Loguru's logger: a console (stdout) handler and
    rerouting of Python's standard logging (uvicorn 含む) through Loguru.

    llens(#31): upstream の監査ログハンドラ(audit.log ファイル出力)は機構ごと撤去済み。
    nucllei 自体の操作は監査対象ではない — カルテ DB 等へ到達する tool の監査は
    tool 側(prism 層)が自前で記録する。
    """
    logger.remove()

    logger.add(
        sys.stdout,
        level=GLOBAL_LOG_LEVEL,
        format=stdout_format,
    )

    logging.basicConfig(
        handlers=[InterceptHandler()], level=GLOBAL_LOG_LEVEL, force=True
    )
    for uvicorn_logger_name in ["uvicorn", "uvicorn.error"]:
        uvicorn_logger = logging.getLogger(uvicorn_logger_name)
        uvicorn_logger.setLevel(GLOBAL_LOG_LEVEL)
        uvicorn_logger.handlers = []
    for uvicorn_logger_name in ["uvicorn.access"]:
        uvicorn_logger = logging.getLogger(uvicorn_logger_name)
        uvicorn_logger.setLevel(GLOBAL_LOG_LEVEL)
        uvicorn_logger.handlers = [InterceptHandler()]

    logger.info(f"GLOBAL_LOG_LEVEL: {GLOBAL_LOG_LEVEL}")
