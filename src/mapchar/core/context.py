"""The advisory key/value bag one pipeline run carries between stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, NamedTuple

from mapchar.core.notices import Level, Notice

KEY_SOURCE_FILES = "source_files"
"""The files that were joined to make the payload, as :class:`FileSpan`s."""
KEY_SOURCE_OFFSET = "source_offset"
"""Where the container's payload starts inside the file."""
KEY_HEADER_SIZE = "header_size"
"""Bytes before the mapped ROM image: what pointer mappings subtract."""
KEY_SUGGESTED_MAPPING = "suggested_mapping"
"""A mapping id the container thinks fits, for the block dialog to seed."""
KEY_SUGGESTED_TABLE = "suggested_table"
KEY_CONSUMED = "consumed"
"""Bytes a decompressor consumed from its input."""
KEY_COMPLETE = "complete"
"""Whether the decompressor saw its end marker."""
KEY_DECOMPRESS_PARTIAL = "decompress_partial"
"""Set before a *preview* decode: return the prefix decoded so far.

A bounded preview — the decompressed view over a window of the file, a
structure scan that only feeds a decoder a few hundred bytes — routinely runs
out of input mid-structure. A scheme that honours this hands back what it got
and reports ``KEY_COMPLETE`` false instead of raising, so the preview shows a
valid prefix. Unset (the default) is the strict read a load and a save-back
need, where a truncated stream is a failure.
"""


class FileSpan(NamedTuple):
    """One file's contribution to the buffer a container was handed.

    The host publishes these in order under :data:`KEY_SOURCE_FILES`, so a stage
    that cares how its bytes were assembled can find out which file supplied
    which range. ``start`` is the offset into the joined buffer, the coordinate
    space everything downstream already works in.
    """

    path: str
    start: int
    length: int


@dataclass
class PipelineContext:
    values: dict[str, Any] = field(default_factory=dict)
    notices: list[Notice] = field(default_factory=list)

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.values[key] = value

    def note(
        self,
        message: str,
        offset: int | None = None,
        level: Level = Level.WARNING,
        detail: str = "",
        source: str = "",
    ) -> None:
        """Record a non-fatal message. The level is the caller's to choose.

        A stage that had to assume, drop or substitute something warns; one
        stating a fact that costs nothing to ignore informs.
        """
        self.notices.append(Notice(message, level, offset, detail, source))

    def inherit(self) -> PipelineContext:
        """A fresh context seeded with this one's hints, sharing no notices.

        What a nested run is handed: a block's own decode publishes its own
        ``KEY_CONSUMED``, but the header size and suggested mapping its parent's
        container found still apply to it, and pointer mappings need them.
        """
        return PipelineContext(dict(self.values))
