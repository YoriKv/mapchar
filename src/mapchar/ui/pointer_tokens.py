"""Pointers as tokens: what the Hex and Text tabs show while the Table list says
**Pointer**.

The views place, tint, select and hover tokens, so each pointer in view is made
one — covering its own bytes, carrying its text — and nothing else in them has
to know the bytes are not characters.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from mapchar.core.block import BlockConfig, PointerTableSource
from mapchar.core.table import Entry, TokenKind
from mapchar.core.tokens import Token, render
from mapchar.pipeline.view_read import PointerCell, PointerSource
from mapchar.ui.raw_widget import POINTER_TOKENS, compact_text

PREVIEW_CHARS = 80
"""How much of a pointer's string a line of the Text tab shows."""


def view_source(
    config: BlockConfig, block: bool, start: int, end: int
) -> PointerSource:
    """The pointers a view from ``start`` to ``end`` reads: a block's own, or a
    file's read every stride from wherever the view starts."""
    source = config.source
    if block or not isinstance(source, PointerTableSource):
        return source
    return replace(source, start=start, stop=end)


def _token(cell: PointerCell, offset: int, text: str) -> Token:
    """A token over ``cell``'s bytes, in a window from ``offset``, reading
    ``text``."""
    start = (cell.address - offset) * 8
    return Token(
        "",
        start,
        start + cell.size * 8,
        Entry("", TokenKind.TEXT, text),
        table_id=POINTER_TOKENS,
    )


def _target(cell: PointerCell) -> str:
    return "?" if cell.target is None else f"{cell.target:X}"


def preview_reader(
    read: Callable[[int], list[Token]] | None,
) -> Callable[[int | None], str]:
    """A pointer target's string on one line, each read once; empty with
    nothing to read it through."""
    seen: dict[int, str] = {}

    def preview(target: int | None) -> str:
        if read is None or target is None:
            return ""
        if target not in seen:
            seen[target] = compact_text(render(read(target)))
        return seen[target]

    return preview


def hex_tokens(
    cells: list[PointerCell],
    offset: int,
    preview: Callable[[int | None], str],
    show_strings: bool,
) -> tuple[list[Token], dict[int, str]]:
    """The Hex tab's tokens for ``cells``, and each one's hover text.

    A pointer's cells show where it points, or with ``show_strings`` the
    string it reaches; the hover says both.
    """
    tokens, tips = [], {}
    for cell in cells:
        text = preview(cell.target)
        shown = text if show_strings and text else f"→{_target(cell)}"
        token = _token(cell, offset, shown)
        tokens.append(token)
        tip = f"pointer ${cell.value:0{cell.size * 2}X} → {_target(cell)}"
        tips[token.bit_start] = f"{tip}\n{text}" if text else tip
    return tokens, tips


def text_tokens(
    cells: list[PointerCell],
    offset: int,
    preview: Callable[[int | None], str],
    show_strings: bool,
) -> list[Token]:
    """The Text tab's tokens for ``cells``: a line per pointer — its address,
    its value, where it points and, with ``show_strings``, the string there."""
    tokens = []
    for cell in cells:
        line = f"{cell.address:06X}  ${cell.value:0{cell.size * 2}X} → {_target(cell)}"
        if show_strings:
            text = preview(cell.target)
            if len(text) > PREVIEW_CHARS:
                text = text[: PREVIEW_CHARS - 1] + "…"
            line += f"  {text}"
        tokens.append(_token(cell, offset, line + "\n"))
    return tokens
