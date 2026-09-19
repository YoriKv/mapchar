"""The check passes, in the order they run.

Order matters only for readability — every pass reports independently, and
none consumes another's findings. It is arranged outside-in: the document, then
each entry's own shape, then what the entry points at (the disk, the registry,
the other entries), then the records nested inside it.

Which rows the reader drops for want of a file is settled before any of them
runs (:func:`crossref.resolve`), since every pass asks whether an entry loads.
"""

from __future__ import annotations

from mapchar_lint.checks import (
    blocks,
    crossref,
    entries,
    files,
    ids,
    table_entries,
    toplevel,
)

PASSES = (
    toplevel.check,
    entries.check,
    files.check,
    ids.check,
    crossref.check,
    blocks.check,
    table_entries.check,
)

__all__ = ["PASSES"]
