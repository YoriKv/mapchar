"""What a file holds right now, and the difference between two states of it.

The write side's memory: a write is a byte transform over whole files, so undo
and redo are the run of bytes that differed rather than a copy of either
version, and every check against the disk is made at the moment it is made.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field

__all__ = [
    "DiskState",
    "FileChange",
    "Merge",
    "current_bytes",
    "existing_bytes",
    "fingerprint",
    "merge",
]

_CHUNK = 4096
"""How many bytes :func:`_runs` compares at a time before looking closer."""


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


def _runs(before: bytes, after: bytes) -> tuple[tuple[int, bytes], ...]:
    """The runs of bytes in which ``after`` differs from ``before``, each as
    ``(offset, bytes)`` in ``after``, in order.

    Whole chunks are compared first, since a buffer of megabytes with a
    handful of edits is the usual case. Bytes ``after`` has past ``before``'s
    end are one run; bytes it lacks are not a run at all, there being nothing
    to lay over.
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


@dataclass(frozen=True)
class Merge:
    """What :func:`merge` produced, and how much of the edits it carried."""

    data: bytes
    kept: int
    """Bytes the edits changed that were laid onto the new contents."""
    conflicts: int
    """Of those, the bytes the disk changed too: the edit won there, and these
    are the places worth a look."""
    dropped: int
    """Edited bytes past the end of the new contents, which had nowhere to
    land: the file shrank under an edit near its end."""


def merge(base: bytes, local: bytes, disk: bytes) -> Merge:
    """``disk`` with every byte ``local`` changed since ``base`` laid over it.

    The three-way merge a reload runs: ``base`` is the buffer as it was read
    or last written here, ``local`` the same buffer with the edits in it, and
    ``disk`` what the file decodes to now. Wherever ``local`` differs from
    ``base`` the edit wins — carrying it is the whole point — and everywhere
    else the new contents stand. A byte both sides changed is a conflict the
    edit still wins, since a silent revert of the user's own work is the one
    outcome a reload exists to prevent, and it is counted so it can be said.
    An edit past the end of ``disk`` — the file shrank under it, or the edit
    lengthened the buffer — has nowhere to land, and is dropped and counted.
    """
    runs = _runs(base, local)
    if not runs:
        return Merge(disk, 0, 0, 0)
    out = bytearray(disk)
    kept = conflicts = dropped = 0
    for offset, chunk in runs:
        room = max(0, min(len(chunk), len(out) - offset))
        dropped += len(chunk) - room
        if not room:
            continue
        for i in range(room):
            at = offset + i
            was = base[at] if at < len(base) else None
            if out[at] != was and out[at] != chunk[i]:
                conflicts += 1
        out[offset : offset + room] = chunk[:room]
        kept += room
    return Merge(bytes(out), kept, conflicts, dropped)


Fingerprint = tuple[int, bytes]
"""A file's modification time and a digest of its bytes."""


def _digest(data: bytes) -> bytes:
    return hashlib.blake2b(data, digest_size=16).digest()


def fingerprint(path: str) -> Fingerprint | None:
    """``(mtime_ns, digest)`` for ``path``, or ``None`` while it cannot be read:
    mid-rename, or gone."""
    try:
        mtime = os.stat(path).st_mtime_ns
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    return (mtime, _digest(data))


def _key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


@dataclass
class DiskState:
    """What each open file looked like the last time this program read or
    wrote it, so a look at the disk can say whether someone else changed it.

    The time is the cheap question and the digest the sure one: a file whose
    time has not moved is not read, and one whose time moved but whose bytes
    digest the same — a touch, a rewrite of the same bytes — is noted as seen
    and reported as nothing. A digest rather than the bytes, since the
    document keeps those already, and rather than the size, which a rewrite
    of a ROM never changes.
    """

    _seen: dict[str, Fingerprint] = field(default_factory=dict)

    def record(self, paths: tuple[str, ...]) -> None:
        """Take ``paths`` as they are now: read, written or declined here."""
        for path in paths:
            now = fingerprint(path)
            if now is None:
                self._seen.pop(_key(path), None)
            else:
                self._seen[_key(path)] = now

    def forget(self, paths: tuple[str, ...]) -> None:
        for path in paths:
            self._seen.pop(_key(path), None)

    def retain(self, paths: tuple[str, ...]) -> None:
        """Stop knowing everything but ``paths``: the files still open."""
        keep = {_key(p) for p in paths}
        for key in list(self._seen):
            if key not in keep:
                del self._seen[key]

    def changed(self, path: str) -> bool:
        """Whether ``path`` holds other bytes than when it was last recorded.

        A path never recorded, or one that cannot be read right now, is not a
        change: the first has nothing to have gone stale, and the second is
        mid-rename or deleted, and neither is something to reload.
        """
        key = _key(path)
        seen = self._seen.get(key)
        if seen is None:
            return False
        try:
            mtime = os.stat(path).st_mtime_ns
        except OSError:
            return False
        if mtime == seen[0]:
            return False
        now = fingerprint(path)
        if now is None:
            return False
        if now[1] == seen[1]:
            self._seen[key] = now
            return False
        return True


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
