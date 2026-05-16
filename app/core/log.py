import logging
from contextlib import contextmanager
from contextvars import ContextVar
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime
from pathlib import Path
from re import sub

import click

from app.core.config import settings

FMT = "%(prefix)s %(message)s"
TASK_ID: ContextVar[str] = ContextVar("TASK_ID", default="")
TASK_NAME: ContextVar[str] = ContextVar("TASK_NAME", default="")

# 日志级别颜色映射
LEVEL_WITH_COLOR = {
    logging.DEBUG: lambda level_name: click.style(str(level_name), fg="blue"),
    logging.INFO: lambda level_name: click.style(str(level_name), fg="green"),
    logging.WARNING: lambda level_name: click.style(str(level_name), fg="yellow"),
    logging.ERROR: lambda level_name: click.style(str(level_name), fg="red"),
    logging.CRITICAL: lambda level_name: click.style(
        str(level_name), fg="red", bold=True
    ),
}


class CustomFormatter(logging.Formatter):
    """
    自定义日志输出格式

    对 logging.LogRecord 增加一个属性 prefix，level + time
    """

    def __init__(self, file_formatter: bool = False, fmt: str = None) -> None:
        """
        :param file_formatter: 是否为文件格式化器
        """

        self.__file_formatter = file_formatter
        super().__init__(fmt=fmt)

    def format(self, record: logging.LogRecord) -> str:

        if self.__file_formatter:  # 文件中不需要控制字
            record.prefix = f"【{record.levelname}】"
        else:  # 控制台需要控制字
            record.prefix = LEVEL_WITH_COLOR[record.levelno](f"【{record.levelname}】")

        # 最长的 CRITICAL 为 8 个字符，保留 1 个空格作为分隔符
        separator = " " * (9 - len(record.levelname))
        dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        record.prefix += f"{separator}{dt} |"
        return super().format(record)


class TRFileHandler(TimedRotatingFileHandler):
    """
    日期轮换文件处理器
    """

    def __init__(self, log_dir: Path, encoding: str = "utf-8") -> None:
        self.log_dir = log_dir
        super().__init__(
            self.__get_log_filname(),
            when="midnight",
            interval=1,
            backupCount=0,
            encoding=encoding,
        )

    def doRollover(self) -> None:
        """
        在轮换日志文件时，更新日志文件路径
        """

        self.baseFilename = self.__get_log_filname()
        super().doRollover()

    def __get_log_filname(self) -> str:
        """
        根据当前日期生成日志文件路径
        """

        current_date = datetime.now().strftime("%Y-%m-%d")
        return (self.log_dir / f"{current_date}.log").as_posix()


def get_task_log_path(task_id: str, date_str: str | None = None) -> Path:
    safe_task_id = sub(r'[/\\:*?"<>|\x00]+', "_", task_id).strip("_") or "task"
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    return settings.LOG_DIR / "tasks" / safe_task_id / f"{date_str}.log"


class _NoTaskFilter(logging.Filter):
    """总日志过滤器：任务上下文内的日志不写入总日志文件。"""

    def filter(self, record: logging.LogRecord) -> bool:
        return not TASK_ID.get()


class TaskFileHandler(logging.Handler):
    """
    根据当前任务上下文写入单独任务日志。
    """

    def emit(self, record: logging.LogRecord) -> None:
        task_id = TASK_ID.get()
        if not task_id:
            return
        try:
            log_path = get_task_log_path(task_id)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            message = self.format(record)
            with log_path.open("a", encoding="utf-8") as file:
                file.write(message + "\n")
        except Exception:
            self.handleError(record)


class LoggerManager:
    """
    日志管理器
    """

    def __init__(self) -> None:
        """
        初始化 LoggerManager 对象
        """

        self.__logger = logging.getLogger(settings.APP_NAME)
        self.__logger.setLevel(logging.DEBUG)
        self.__logger.propagate = False

        console_formatter = CustomFormatter(
            file_formatter=False,
            fmt=FMT,
        )
        file_formatter = CustomFormatter(
            file_formatter=True,
            fmt=FMT,
        )

        level = logging.DEBUG if settings.DEBUG else logging.INFO

        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        console_handler.setFormatter(console_formatter)
        self.__logger.addHandler(console_handler)

        file_handler = TRFileHandler(log_dir=settings.LOG_DIR, encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(file_formatter)
        file_handler.addFilter(_NoTaskFilter())
        self.__logger.addHandler(file_handler)

        task_file_handler = TaskFileHandler()
        task_file_handler.setLevel(level)
        task_file_handler.setFormatter(file_formatter)
        self.__logger.addHandler(task_file_handler)

    @contextmanager
    def task_context(self, task_id: str, task_name: str = ""):
        """
        为当前任务设置日志上下文。
        """
        task_id_token = TASK_ID.set(task_id)
        task_name_token = TASK_NAME.set(task_name or task_id)
        try:
            yield
        finally:
            TASK_ID.reset(task_id_token)
            TASK_NAME.reset(task_name_token)

    def get_task_log_path(self, task_id: str, date_str: str | None = None) -> Path:
        return get_task_log_path(task_id, date_str)

    def __log(self, method: str, msg: str, *args, **kwargs) -> None:
        """
        获取模块的logger
        :param method: 日志方法
        :param msg: 日志信息
        """
        if hasattr(self.__logger, method):
            getattr(self.__logger, method)(msg, *args, **kwargs)

    def info(self, msg: str, *args, **kwargs) -> None:
        """
        重载info方法
        """
        self.__log("info", msg, *args, **kwargs)

    def debug(self, msg: str, *args, **kwargs) -> None:
        """
        重载debug方法
        """
        self.__log("debug", msg, *args, **kwargs)

    def warning(self, msg: str, *args, **kwargs) -> None:
        """
        重载warning方法
        """
        self.__log("warning", msg, *args, **kwargs)

    def warn(self, msg: str, *args, **kwargs) -> None:
        """
        重载warn方法
        """
        self.__log("warning", msg, *args, **kwargs)

    def error(self, msg: str, *args, **kwargs) -> None:
        """
        重载error方法
        """
        self.__log("error", msg, *args, **kwargs)

    def critical(self, msg: str, *args, **kwargs) -> None:
        """
        重载critical方法
        """
        self.__log("critical", msg, *args, **kwargs)


# 初始化公共日志
logger = LoggerManager()
