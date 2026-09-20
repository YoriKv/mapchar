"""Tokens and the text they render to.

``render`` and ``parse_text`` are inverses: a token list renders to text in
the script grammar, and that text parses back to a list of text runs and
codes that the encoder resolves against a table set. ``piece_spans`` reads
the same grammar leniently, keeping the offsets a text being edited needs.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from mapchar.core.table import OperandSpec, TableEntry, TokenKind


@dataclass(frozen=True)
class Token:
    """One decoded unit. ``entry`` is ``None`` for unmatched data."""

    bits: str
    """The entry's key bits, or the raw chunk for unmatched data."""
    bit_start: int
    bit_end: int
    """Exclusive; covers operands, and skips crossed while reading them."""
    entry: TableEntry | None = None
    operands: tuple[int, ...] = ()
    table_id: str | None = None
    """The table the entry was matched in; ``raw``/``bits`` for raw frames."""
    fallback: bool = False
    """Bits a frame consumed silently: the fallback bits that closed it, or
    the count that opened it. Shown as nothing."""
    newline: bool = False
    """A line code — the block's, or an entry with the *newline* effect: a line
    break follows it wherever it is shown, and it counts as a line."""
    page: bool = False
    """An entry with the *page* effect: the text box ends after it, so a line
    break follows it wherever it is shown too."""

    def encoded_bits(self) -> str:
        """Every bit this token stands for, operands included."""
        if self.entry is None:
            return self.bits
        return bits_for(self.entry, self.operands)

    @property
    def is_end(self) -> bool:
        return self.entry is not None and self.entry.kind is TokenKind.END

    @property
    def is_code(self) -> bool:
        return self.entry is None or self.entry.kind is not TokenKind.TEXT

    @property
    def weight(self) -> int:
        return 0 if self.entry is None else self.entry.weight

    @property
    def pascal_weight(self) -> int:
        """Weight for a Pascal length count, where unmatched data counts as one."""
        return 1 if self.entry is None else self.weight

    def text(self) -> str:
        return render_token(self)


def bits_for(entry: TableEntry, values: Iterable[int]) -> str:
    """An entry's bits with its operand ``values`` packed after them."""
    return entry.bits + "".join(
        spec.bits_of(value) for spec, value in zip(entry.operands, values, strict=False)
    )


_ESCAPES = {"[": "\\[", "]": "\\]", "\\": "\\\\"}


def escape_text(text: str) -> str:
    """Escape a literal text run for the script grammar."""
    return "".join(_ESCAPES.get(c, c) for c in text)


def render_token(token: Token) -> str:
    text = _render(token)
    if (token.newline or token.page) and not text.endswith("\n"):
        return text + "\n"
    return text


def _render(token: Token) -> str:
    entry = token.entry
    if token.fallback:
        return ""
    if entry is None:
        n = len(token.bits)
        whole = n - n % 8
        out = "".join(
            f"[${int(token.bits[i : i + 8], 2):02X}]" for i in range(0, whole, 8)
        )
        # A tail of fewer than eight bits is shown as bits.
        return out + (f"[%{token.bits[whole:]}]" if n % 8 else "")
    if entry.kind in (TokenKind.TEXT, TokenKind.END, TokenKind.SWITCH):
        # Table text is already in script form; ``\n`` becomes a line break
        # on dump and is ignored on insert, so dumps re-insert unchanged.
        return entry.text.replace("\\n", "\n")
    if entry.kind is TokenKind.RETURN:
        return ""
    label = entry.text
    if entry.kind is TokenKind.CODE and entry.operands:
        words = [
            spec.render(value)
            for spec, value in zip(entry.operands, token.operands, strict=False)
        ]
        # An operand cut short by the end of the data is left out; the raw
        # bytes that were there follow as their own unmatched tokens.
        return f"[{label} {' '.join(words)}]" if words else f"[{label}]"
    return f"[{label}]"


def render(tokens: list[Token]) -> str:
    return "".join(render_token(t) for t in tokens)


@dataclass(frozen=True)
class TextRun:
    text: str


@dataclass(frozen=True)
class CodeRef:
    """A bracketed item in script text, not yet resolved against a table."""

    label: str
    words: tuple[str, ...] = ()

    @property
    def is_raw_byte(self) -> bool:
        return self.label.startswith("$")

    @property
    def is_raw_bits(self) -> bool:
        return self.label.startswith("%")

    def raw_bits(self) -> str:
        if self.is_raw_byte:
            return format(int(self.label[1:], 16), f"0{(len(self.label) - 1) * 4}b")
        return self.label[1:]


_RAW_LABEL = re.compile(r"\$[0-9A-Fa-f]{2}|%[01]+")


def plain_text(script_form: str) -> str:
    """The literal characters of a TEXT entry: escapes resolved, ``\\n`` dropped."""
    return "".join(
        item.text for item in parse_text(script_form) if isinstance(item, TextRun)
    )


def parse_text(text: str) -> list[TextRun | CodeRef]:
    """Split script text into literal runs and codes. Raises ValueError."""
    items: list[TextRun | CodeRef] = []
    buf: list[str] = []
    i, n = 0, len(text)

    def flush() -> None:
        if buf:
            items.append(TextRun("".join(buf)))
            buf.clear()

    while i < n:
        c = text[i]
        if c == "\\":
            if i + 1 >= n:
                raise ValueError("dangling backslash")
            nxt = text[i + 1]
            if nxt == "n":
                # A line break escape inside a script is never text.
                i += 2
                continue
            buf.append(nxt)
            i += 2
        elif c == "[":
            close = text.find("]", i + 1)
            if close < 0:
                raise ValueError(f"unclosed '[' at {i}")
            inner = text[i + 1 : close].strip()
            if not inner:
                raise ValueError(f"empty code at {i}")
            flush()
            words = inner.split()
            label = words[0]
            if _RAW_LABEL.fullmatch(label) and len(words) == 1:
                items.append(CodeRef(label))
            else:
                items.append(CodeRef(label, tuple(words[1:])))
            i = close + 1
        elif c == "]":
            raise ValueError(f"stray ']' at {i}")
        elif c == "\n":
            # Line breaks in scripts separate lines, never tokens.
            i += 1
        else:
            buf.append(c)
            i += 1
    flush()
    return items


def piece_spans(text: str) -> list[tuple[int, int, bool]]:
    """``text`` split into the pieces the grammar makes, each as
    ``(start, stop, is a code)`` in character offsets.

    The lenient walk, for text a person is typing or a surface that has to
    keep its offsets: a code is one piece however many characters it spells,
    an escape is one piece, and an unclosed ``[`` runs to the next ``[`` or to
    the end — mid-typing that is exactly the one piece being spelled. Only a
    closed ``[...]`` pair is a code, so a surface that tints or hides codes
    reads that flag instead of guessing from the characters around a bracket.
    :func:`parse_text` is the strict reading, which the encoder needs.
    """
    spans: list[tuple[int, int, bool]] = []
    at, total = 0, len(text)
    while at < total:
        if text[at] == "\\" and at + 1 < total:
            spans.append((at, at + 2, False))
            at += 2
            continue
        if text[at] == "[":
            close = text.find("]", at + 1)
            nested = text.find("[", at + 1)
            closed = close >= 0 and (nested < 0 or close < nested)
            stop = close + 1 if closed else (total if nested < 0 else nested)
            spans.append((at, stop, closed))
            at = stop
            continue
        spans.append((at, at + 1, False))
        at += 1
    return spans


def operand_values(entry: TableEntry, words: tuple[str, ...]) -> tuple[int, ...]:
    """Parse the rendered operand words of a code back to values."""
    values: list[int] = []
    pos = 0
    for spec in entry.operands:
        take = words[pos : pos + spec.words]
        if len(take) < spec.words:
            raise ValueError(f"[{entry.text}] needs more operands")
        values.append(_parse_operand(spec, take))
        pos += spec.words
    if pos != len(words):
        raise ValueError(f"[{entry.text}] has too many operands")
    return tuple(values)


def _parse_operand(spec: OperandSpec, words: tuple[str, ...]) -> int:
    if spec.kind == "bytes":
        value = 0
        for w in words:
            value = (value << 8) | spec.parse_value(w)
        return value
    return spec.parse_value(words[0])
