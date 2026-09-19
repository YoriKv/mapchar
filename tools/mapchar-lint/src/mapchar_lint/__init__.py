"""Static checks for hand-edited ``.mapchar`` project files.

A mapchar project loads *tolerantly*: an entry that does not read is dropped
with one line in a notice, a key nothing reads is ignored, a configuration word
that is not a setting is passed over, a plugin id nothing answers to becomes a
pass-through. Every one of those is the right behaviour for a loader — a
project that will not open is worse than one that opens degraded — and most of
them are silent.

This package reports what a load would silently change, by checking the
document rather than the parse result. See :mod:`mapchar_lint.schema` for why it
restates the schema instead of importing mapchar's own reader.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
