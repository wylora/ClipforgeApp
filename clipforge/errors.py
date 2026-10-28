"""Иерархия ошибок приложения. Каждая ошибка несёт понятное пользователю сообщение."""
from __future__ import annotations


class ClipForgeError(Exception):
    def __init__(self, message: str, hint: str = "", details: str = ""):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.details = details

    def user_text(self) -> str:
        text = self.message
        if self.hint:
            text += f"\n\nЧто можно сделать: {self.hint}"
        return text


class DependencyError(ClipForgeError): ...
class FFmpegError(ClipForgeError): ...
class DownloadError(ClipForgeError): ...
class TranscriptionError(ClipForgeError): ...
class AnalysisError(ClipForgeError): ...
class InvalidMediaError(ClipForgeError): ...
class DiskSpaceError(ClipForgeError): ...
class ConfigError(ClipForgeError): ...


class NoGoodMomentsError(ClipForgeError):
    """Не найдено достаточно хороших моментов. Это предупреждение, а не сбой."""


class CancelledError(ClipForgeError):
    def __init__(self, message: str = "Операция отменена пользователем", **kw):
        super().__init__(message, **kw)
