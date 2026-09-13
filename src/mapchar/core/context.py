"""The advisory key/value bag one pipeline run carries between stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mapchar.core.notices import Notice

KEY_SOURCE_FILES = "source_files"
"""The paths that were joined to make the payload."""
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


@dataclass
class PipelineContext:
    values: dict[str, Any] = field(default_factory=dict)
    notices: list[Notice] = field(default_factory=list)

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.values[key] = value

    def note(self, message: str, offset: int | None = None) -> None:
        self.notices.append(Notice(message, offset=offset))
