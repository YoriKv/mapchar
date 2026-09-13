"""Exception types shared by every layer."""

from __future__ import annotations


class MapcharError(Exception):
    """Base of every error mapchar raises on purpose."""


class TableError(MapcharError):
    """A table file could not be loaded. Carries the file and line."""

    def __init__(self, message: str, path: str | None = None, line: int | None = None):
        self.message = message
        self.path = path
        self.line = line
        super().__init__(self.format())

    def format(self) -> str:
        where = self.path or "<table>"
        if self.line is not None:
            where = f"{where}:{self.line}"
        return f"{where}: {self.message}"


class ScriptError(MapcharError):
    """A script file could not be parsed."""

    def __init__(self, message: str, path: str | None = None, line: int | None = None):
        self.message = message
        self.path = path
        self.line = line
        where = path or "<script>"
        if line is not None:
            where = f"{where}:{line}"
        super().__init__(f"{where}: {message}")


class EncodeError(MapcharError):
    """Text could not be encoded, or did not decode back to itself."""

    def __init__(self, message: str, position: int | None = None, context: str = ""):
        self.position = position
        self.context = context
        super().__init__(message)
