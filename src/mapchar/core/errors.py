"""Exception types shared by every layer."""

from __future__ import annotations


class MapcharError(Exception):
    """Base of every error mapchar raises on purpose."""


class LocatedError(MapcharError):
    """An error in a file, formatted ``path:line: message``.

    ``placeholder`` stands in for a file that was not named.
    """

    placeholder = "<file>"

    def __init__(self, message: str, path: str | None = None, line: int | None = None):
        self.message = message
        self.path = path
        self.line = line
        super().__init__(self.format())

    def format(self) -> str:
        where = self.path or self.placeholder
        if self.line is not None:
            where = f"{where}:{self.line}"
        return f"{where}: {self.message}"


class TableError(LocatedError):
    """A table file could not be loaded. Carries the file and line."""

    placeholder = "<table>"


class ScriptError(LocatedError):
    """A script file could not be parsed."""

    placeholder = "<script>"


class EncodeError(MapcharError):
    """Text could not be encoded, or did not decode back to itself."""

    def __init__(self, message: str, position: int | None = None, context: str = ""):
        self.position = position
        self.context = context
        super().__init__(message)
