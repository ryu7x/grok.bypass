from xylogger import Logger, Spinner, ProgressBar, Table, Panel, Timer
from xylogger import LogContext
from xylogger.utils import retry, bold, colorize
from contextlib import contextmanager


class Log:
    _logger = Logger(name="Grok")

    @staticmethod
    def Success(message: str) -> None:
        Log._logger.success(message)

    @staticmethod
    def Error(message: str) -> None:
        Log._logger.error(message)

    @staticmethod
    def Info(message: str) -> None:
        Log._logger.info(message)

    @staticmethod
    def Warning(message: str) -> None:
        Log._logger.warning(message)

    @staticmethod
    def Debug(message: str) -> None:
        Log._logger.debug(message)

    @staticmethod
    def Critical(message: str) -> None:
        Log._logger.critical(message)

    @staticmethod
    def Section(title: str, start_rgb: tuple = (88, 101, 242), end_rgb: tuple = (114, 137, 218)) -> None:
        Log._logger.section(title, start_rgb=start_rgb, end_rgb=end_rgb)

    @staticmethod
    def Gradient(text: str, start_rgb: tuple = (88, 101, 242), end_rgb: tuple = (114, 137, 218)) -> None:
        Log._logger.gradient_print(text, start_rgb, end_rgb)

    @staticmethod
    def Divider() -> None:
        Log._logger.divider()

    @staticmethod
    def Rule(title: str, style: str = "─") -> None:
        Log._logger.rule(title, style=style)

    @staticmethod
    def Blank(count: int = 1) -> None:
        Log._logger.blank(count)

    @classmethod
    def Spinner(cls, message: str, style: str = "dots2") -> Spinner:
        return Spinner(message=message, style=style)

    @classmethod
    def Progress(cls, total: int, description: str = "Progress", style: str = "blocks") -> ProgressBar:
        return ProgressBar(
            total=total,
            description=description,
            style=style,
            show_percentage=True,
            show_count=True,
            show_eta=True
        )

    @classmethod
    def Table(cls, headers: list, style: str = "rounded") -> Table:
        return Table(headers=headers, style=style, padding=1)

    @classmethod
    def Panel(cls, content, title: str = None, style: str = "rounded") -> Panel:
        return Panel(content=content, title=title, style=style, padding=1)

    @classmethod
    def Timer(cls, name: str) -> Timer:
        return Timer(name)

    @classmethod
    def Context(cls, context_name: str) -> LogContext:
        return LogContext(cls._logger, context_name)

    @staticmethod
    def Bold(text: str) -> str:
        return bold(text)

    @staticmethod
    def Retry(max_attempts: int = 3, delay: float = 1.0):
        return retry(max_attempts=max_attempts, delay=delay)