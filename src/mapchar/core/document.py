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

    def string_by_index(self, index: int) -> StringRecord | None:
        return next((r for r in self.strings if r.index == index), None)

    def string_at(self, offset: int) -> StringRecord | None:
        """The string whose bytes hold ``offset``."""
        return next((r for r in self.strings if r.start <= offset < r.end), None)

    def string_for_pointer(self, address: int) -> StringRecord | None:
        """The string one of whose pointers occupies ``address``."""
        for rec in self.strings:
            for p in rec.pointers:
                if p.address <= address < p.address + p.size:
                    return rec
        return None
