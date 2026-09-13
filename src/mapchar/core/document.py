"""The interpreted, mutable model of one open entry."""

from __future__ import annotations

from dataclasses import dataclass, field

from mapchar.core.block import StringRecord
from mapchar.core.context import PipelineContext
from mapchar.core.notices import Notice
from mapchar.core.table import TableSet


@dataclass
class Document:
    data: bytes
    """The decompressed payload: what views decode and edits change."""
    ctx: PipelineContext
    writable: bool
    raw: bytes = b""
    """The file bytes before the container, kept for write-back."""
    missing_plugins: list[str] = field(default_factory=list)
    table_set: TableSet | None = None
    strings: list[StringRecord] = field(default_factory=list)
    notices: list[Notice] = field(default_factory=list)
    extraction_key: tuple | None = None
    """What the strings were extracted from, to skip a repeat."""

    @property
    def size(self) -> int:
        return len(self.data)
