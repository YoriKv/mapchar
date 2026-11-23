"""How much text the project holds and how much of it is translated.

Counted off each string's **original**, the text the project keeps for every
block, so a block the session has not opened counts as much as one it has, and
a translated string counts the source words it covered — how a translator's
work is measured.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from mapchar.core.block import Status
from mapchar.core.tokens import TextRun, parse_text
from mapchar.project.entry import Entry, EntryKind
from mapchar.project.workspace import Workspace


@dataclass(frozen=True)
class Tally:
    """Strings, and the words and lines of text they hold."""

    strings: int = 0
    words: int = 0
    lines: int = 0

    def __add__(self, other: Tally) -> Tally:
        return Tally(
            self.strings + other.strings,
            self.words + other.words,
            self.lines + other.lines,
        )


def _literal(line: str) -> str:
    """A line's text with its codes taken out, each leaving a space behind."""
    try:
        items = parse_text(line)
    except ValueError:
        return line
    return " ".join(item.text for item in items if isinstance(item, TextRun))


def text_tally(text: str | None) -> Tally:
    """One string's tally: its words, and its lines that hold any text.

    A word is a run between spaces with a letter or a digit in it; a code
    separates words and is none, and a line of nothing but codes is no line.
    A string with no original kept (an old project) counts as a string alone.
    """
    words = lines = 0
    for line in (text or "").split("\n"):
        chunks = _literal(line).split()
        words += sum(any(c.isalnum() for c in w) for w in chunks)
        lines += bool(chunks)
    return Tally(1, words, lines)


@dataclass
class BlockStatistics:
    """One block's tallies, by the status of its strings."""

    file: str
    block: str
    by_status: dict[Status, Tally] = field(default_factory=dict)

    def of(self, *statuses: Status) -> Tally:
        return sum((self.by_status.get(s, Tally()) for s in statuses), Tally())

    @property
    def total(self) -> Tally:
        return self.of(*Status)

    @property
    def translated(self) -> Tally:
        """Every string that is no longer its original: edited, for review, done."""
        return self.of(Status.EDITED, Status.REVIEW, Status.DONE)

    def add(self, status: Status, tally: Tally) -> None:
        self.by_status[status] = self.by_status.get(status, Tally()) + tally


def _strings(entry: Entry) -> Iterator[tuple[Status, str | None]]:
    """The block's strings as status and original: off its records once read,
    else off the state the project keeps for it."""
    if entry.doc is not None:
        for rec in entry.doc.strings:
            yield rec.status, rec.original
    elif entry.pending_strings:
        for st in entry.pending_strings.values():
            yield st.status, st.original


def block_statistics(entry: Entry) -> BlockStatistics:
    stats = BlockStatistics(entry.parent.name if entry.parent else "", entry.name)
    for status, original in _strings(entry):
        stats.add(status, text_tally(original))
    return stats


def project_statistics(workspace: Workspace) -> list[BlockStatistics]:
    """Every block's statistics, in the order the project lists them."""
    return [block_statistics(e) for e in workspace.of_kind(EntryKind.BLOCK)]


def summed(blocks: Iterable[BlockStatistics]) -> BlockStatistics:
    """The project's statistics: every block's added together."""
    total = BlockStatistics("", "")
    for block in blocks:
        for status, tally in block.by_status.items():
            total.add(status, tally)
    return total


__all__ = [
    "BlockStatistics",
    "Tally",
    "block_statistics",
    "project_statistics",
    "summed",
    "text_tally",
]
