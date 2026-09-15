"""What a file holds right now, and the difference between two states of it.

The write side's memory: a write is a byte transform over whole files, so undo
and redo are the run of bytes that differed rather than a copy of either
version, and every check against the disk is made at the moment it is made.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

__all__ = ["FileChange", "current_bytes", "existing_bytes"]


def current_bytes(path: str) -> bytes:
    """A *destination's* current bytes, or ``b""`` when it is not there yet.

    Write-side only. A missing **source** is a hard failure that the load's
    error funnel reports; a missing destination is a file about to be created.
    """
    if not os.path.exists(path):
        return b""
    with open(path, "rb") as f:
        return f.read()


def existing_bytes(paths: tuple[str, ...]) -> dict[str, bytes]:
    """What each destination holds right now, keyed by path."""
    return {p: current_bytes(p) for p in paths}


@dataclass(frozen=True)
class FileChange:
    """The bytes one write changed in one file, and enough to put either side back.

    Only the run that differs is held — a write changes one block's region of a
    ROM — with the file's size on each side, since a single file may grow or
    shrink. :meth:`apply` moves the file from ``before`` to ``after``, and
    :meth:`flipped` is the same change the other way, so an undo and a redo are
    one operation over a pair rather than two.
    """

    path: str
    offset: int
    before: bytes
    after: bytes
    before_size: int
    after_size: int

    @classmethod
    def between(cls, path: str, before: bytes, after: bytes) -> FileChange | None:
        """The change from ``before`` to ``after``; ``None`` when they are equal."""
        if before == after:
            return None
        start = 0
        limit = min(len(before), len(after))
        while start < limit and before[start] == after[start]:
            start += 1
        end = 0
        limit -= start
        while end < limit and before[-1 - end] == after[-1 - end]:
            end += 1
        return cls(
            path,
            start,
            before[start : len(before) - end],
            after[start : len(after) - end],
            len(before),
            len(after),
        )

    def flipped(self) -> FileChange:
        return FileChange(
            self.path,
            self.offset,
            self.after,
            self.before,
            self.after_size,
            self.before_size,
        )

    def _holds(self, data: bytes, chunk: bytes, size: int) -> bool:
        if len(data) != size:
            return False
        return data[self.offset : self.offset + len(chunk)] == chunk

    def holds_after(self) -> bool:
        return self._holds(current_bytes(self.path), self.after, self.after_size)

    def holds_before(self) -> bool:
        return self._holds(current_bytes(self.path), self.before, self.before_size)

    def apply(self) -> bool:
        """Put ``after`` in the file: ``True`` once it holds it.

        The file is read **now**, as a write reads it, and only a file whose
        run still holds ``before`` is touched — one changed there since, by
        another program or by hand, holds neither side and is left as it is,
        which is the ``False``. Bytes outside the run are the file's own
        business, as they are to the write.
        """
        data = current_bytes(self.path)
        if self._holds(data, self.after, self.after_size):
            return True
        if not self._holds(data, self.before, self.before_size):
            return False
        end = self.offset + len(self.before)
        with open(self.path, "wb") as f:
            f.write(data[: self.offset] + self.after + data[end:])
        return True
