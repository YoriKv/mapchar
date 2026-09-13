"""Non-fatal messages carried alongside results."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Level(Enum):
    INFO = "info"
    WARNING = "warning"


@dataclass(frozen=True)
class Notice:
    """One thing a stage wants the user to know without failing.

    ``message`` is the single line shown in a list — write it so it still makes
    sense with no context, since that is how it will be read. ``detail`` is the
    fuller explanation the UI shows as a tooltip; keep it to a few short lines.
    ``source`` names what produced it — usually a plugin id — so a notice stays
    attributable once several stages have contributed to one run.
    """

    message: str
    level: Level = Level.WARNING
    offset: int | None = None
    """Byte offset the notice is about, when it is about a place in the data."""
    detail: str = ""
    source: str = ""

    @property
    def is_warning(self) -> bool:
        return self.level is Level.WARNING

    def __str__(self) -> str:
        where = f"${self.offset:X}: " if self.offset is not None else ""
        text = f"{where}{self.message}"
        return f"{self.source}: {text}" if self.source else text


def notice_lines(notice: Notice, prefix: str = "") -> list[str]:
    """``notice`` as the lines that show it: the message, then its detail.

    The detail belongs *under* the line it explains, indented, rather than
    beside it — so one notice with more to say reads as one notice rather than
    as two. Notices with nothing more to say are a single line, as before.
    """
    return [prefix + str(notice)] + [
        f"    {line}" for line in notice.detail.splitlines()
    ]
