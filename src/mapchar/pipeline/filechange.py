"""What a file holds right now, and the difference between two states of it.

The write side's memory: a write is a byte transform over whole files, so undo
and redo are the run of bytes that differed rather than a copy of either
version, and every check against the disk is made at the moment it is made.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

__all__ = [
    "FileChange",
    "current_bytes",
    "edit_runs",
    "existing_bytes",
    "on_disk",
    "replay",
]

_CHUNK = 4096
"""How many bytes :func:`edit_runs` compares at a time before looking closer."""


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


def on_disk(paths: tuple[str, ...]) -> bytes:
    """What the files hold right now, end to end — the shape a load's ``raw``
    has, so the two can be compared."""
    return b"".join(current_bytes(p) for p in paths)


def edit_runs(before: bytes, after: bytes) -> tuple[tuple[int, bytes], ...]:
    """The runs of bytes in which ``after`` differs from ``before``, each as
    ``(offset, bytes)`` in ``after``, in order.

    What a buffer's edits are, apart from the bytes they were made over, so
    they can be laid over other bytes (:func:`replay`). Whole chunks are
    compared first, since a buffer of megabytes with a handful of edits is the
    usual case. Bytes ``after`` has past ``before``'s end are one run; bytes
    it lacks are not a run at all, there being nothing to lay over.
    """
    runs: list[tuple[int, bytes]] = []
    limit = min(len(before), len(after))
    start: int | None = None
    at = 0
    while at < limit:
        end = min(at + _CHUNK, limit)
        if before[at:end] == after[at:end]:
            if start is not None:
                runs.append((start, after[start:at]))
                start = None
        else:
            for i in range(at, end):
                if before[i] != after[i]:
                    if start is None:
                        start = i
                elif start is not None:
                    runs.append((start, after[start:i]))
                    start = None
        at = end
    if len(after) > limit:
        # The tail joins a run still open at the end, so each run is one splice.
        start = limit if start is None else start
        runs.append((start, after[start:]))
    elif start is not None:
        runs.append((start, after[start:limit]))
    return tuple(runs)


def replay(runs: tuple[tuple[int, bytes], ...], base: bytes) -> bytes:
    """``base`` with ``runs`` laid over it, each at its offset.

    The runs win wherever they overlap what ``base`` holds — they are the
    edits, and ``base`` is what the disk says now. One that reaches past the
    end lengthens the result, padded with ``$FF`` up to it when ``base`` is
    shorter than the buffer the run was made in.
    """
    if not runs:
        return base
    out = bytearray(base)
    for offset, chunk in runs:
        if offset > len(out):
            out.extend(b"\xff" * (offset - len(out)))
        out[offset : offset + len(chunk)] = chunk
    return bytes(out)


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
