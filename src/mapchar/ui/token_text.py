"""What a token covers and how it reads on one line.

No Qt: the raw view draws from this, and the pointer tokens built for it
(:mod:`mapchar.ui.pointer_tokens`) read from it too, so neither has to import
the other.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from mapchar.core.tokens import piece_spans
from mapchar.ui import BYTES_PER_ROW

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from mapchar.core.tokens import Token

POINTER_TOKENS = "\x00pointer"
"""The ``table_id`` of a token that is a pointer rather than text: tinted as
one, and hovered for what the row model says of it."""

BREAK_MARK = "↵"
CODE_MARK = "▪"
_EMBEDDED_BREAK = re.compile(r"\[[^\[\]]*\]\n|\n")
_EMBEDDED_CODE = re.compile(r"\[[^\[\]]*\]")


def ellipsize(text: str, limit: int) -> str:
    """``text`` no longer than ``limit``, the last character an ellipsis where
    it had to be cut. Every preview cuts the same way, whatever it previews."""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def token_bytes(token: Token) -> range:
    """The relative bytes a token covers, at least the one it starts in."""
    first = token.bit_start // 8
    return range(first, max(first, (token.bit_end - 1) // 8) + 1)


def row_runs(rels: Iterable[int]) -> Iterator[tuple[int, int]]:
    """Sorted byte offsets as ``(first, last)`` runs, broken at row ends."""
    run: list[int] = []
    for rel in sorted(rels):
        if run and (rel != run[1] + 1 or rel % BYTES_PER_ROW == 0):
            yield run[0], run[1]
            run = []
        run = [run[0] if run else rel, rel]
    if run:
        yield run[0], run[1]


def compact_text(text: str) -> str:
    """Rendered text on one line: a name ending a line as :data:`BREAK_MARK`,
    any other as :data:`CODE_MARK`."""
    return _EMBEDDED_CODE.sub(CODE_MARK, _EMBEDDED_BREAK.sub(BREAK_MARK, text))


def code_spans(text: str) -> list[tuple[int, int]]:
    """Where the codes in script text are, as ``(start, stop)`` offsets.

    The grammar's own lenient walk (:func:`~mapchar.core.tokens.piece_spans`)
    says which pieces are codes, so an escape is read from the start of the
    text rather than guessed at from the one character before a ``[``: in
    ``a\\\\[line]b`` the backslash is itself escaped and the code is real.
    """
    # The highlighter runs this on every keystroke, so text with no bracket at
    # all — most of it — never pays for the walk.
    if "[" not in text:
        return []
    return [(start, stop) for start, stop, is_code in piece_spans(text) if is_code]


def hide_codes(text: str) -> str:
    """Script text with its codes left out and its line breaks kept: what the
    Text tab shows with Show codes off, for text that has no tokens."""
    out: list[str] = []
    at = 0
    for start, stop in code_spans(text):
        out.append(text[at:start])
        at = stop
    out.append(text[at:])
    return "".join(out)


def display_text(token: Token) -> tuple[str, bool]:
    """What the text column shows for a token, and whether it is a label.

    Unmatched data is a dot (the hex column already shows its bytes). A token
    whose whole text is one bracketed name — a code, an end token, a table
    entry written as ``[tile60]`` — shows the name alone, as a label. Text with
    a name inside it keeps its letters: a name ending a line becomes
    :data:`BREAK_MARK` and any other :data:`CODE_MARK`, so ``s[line]`` reads
    ``s↵``; the tooltip has it whole.
    """
    if token.entry is None and not token.fallback:
        return "·", False
    text = token.text()
    bare = text.strip("\n")
    if bare.startswith("[") and bare.endswith("]") and bare.count("[") == 1:
        return bare[1:-1], True
    return compact_text(text), False
