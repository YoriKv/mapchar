"""Non-fatal messages carried alongside results."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Level(Enum):
    INFO = "info"
    WARNING = "warning"


@dataclass(frozen=True)
class Notice:
    message: str
    level: Level = Level.WARNING
    offset: int | None = None
    """Byte offset the notice is about, when it is about a place in the data."""

    def __str__(self) -> str:
        if self.offset is None:
            return self.message
        return f"${self.offset:X}: {self.message}"
