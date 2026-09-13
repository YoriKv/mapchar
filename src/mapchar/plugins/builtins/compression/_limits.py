"""The output caps every decoder here honours.

A decoder is handed the rest of the file, not the structure: the raw view
refreshes from the caret and a Scan re-runs the decoder at every offset. So each
one bounds what a single read may produce — an unbounded PackBits read expands
128x, an LZ one eight times over — and reports the bytes it consumed as the end
of the last **complete** op, never a position past the buffer.

``KEY_DECOMPRESS_PARTIAL`` on the context says a short buffer is expected rather
than corrupt, which is what
:class:`~mapchar.plugins.base.PartialDecompression` takes off it: the prefix
decoded so far comes back instead of an error. Without it a truncated stream
*is* an error, which is what lets the UI tell "a structure continues past the
window" from "this is not a structure". A scheme with no end to find reports
``KEY_COMPLETE`` false always and never raises for a short read.
"""

from __future__ import annotations

MAX_OUT = 0x100000
"""Output bytes any one decode may produce — a memory guard, not a format limit.

1 MiB is far past any text bank a game holds, while capping what an unbounded
read expands to. Reaching it is a short read: a scheme that finds its own end
has not found it, and one that does not was never going to.
"""

MAX_BANK = 0x10000
"""The tighter cap the schemes whose own fields reach one 64 KiB bank use."""
