"""What the window calls a source kind and a string type, in one table.

The Reading bar's Source and Ends-at pickers are the authority — they are where
a reading is set — and everything else that has to name one of these reads it
from here: the block bar's label, the Scan window's Strings column, the Files
panel's lower-case phrases. One table, so the same block cannot describe itself
two ways in one window (``docs/ui.md``: words, never class names).

Keyed by class rather than by a picker's data string, so any layer holding a
:class:`~mapchar.core.block.BlockConfig` can name what it holds. Qt-free.
"""

from __future__ import annotations

from mapchar.core.block import (
    EndToken,
    FixedLength,
    Lines,
    NestedPointerSource,
    NextPointer,
    Pascal,
    PointerListSource,
    PointerTableSource,
    RangeSource,
)

SOURCE_NAMES: dict[type, str] = {
    RangeSource: "Range",
    PointerTableSource: "Pointer table",
    PointerListSource: "Pointer list",
    NestedPointerSource: "Nested tables",
}
"""Where a block's strings come from, as the Source picker names it."""

STRING_TYPE_NAMES: dict[type, str] = {
    EndToken: "End token",
    FixedLength: "Fixed length",
    Pascal: "Length prefix",
    NextPointer: "Next pointer",
    Lines: "Lines",
}
"""How a string ends, as the Ends-at picker names it."""

__all__ = ["SOURCE_NAMES", "STRING_TYPE_NAMES"]
