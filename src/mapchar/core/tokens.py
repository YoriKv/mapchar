"""Tokens and the text they render to.

``render`` and ``parse_text`` are inverses: a token list renders to text in
the script grammar, and that text parses back to a list of text runs and
codes that the encoder resolves against a table set.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from mapchar.core.table import Entry, EntryKind, OperandSpec


@dataclass(frozen=True)
class Token:
    """One decoded unit. ``entry`` is ``None`` for unmatched data."""

    bits: str
    """The entry's key bits, or the raw chunk for unmatched data."""
    bit_start: int
    bit_end: int
    """Exclusive; covers operands, and skips crossed while reading them."""
    entry: Entry | None = None
    operands: tuple[int, ...] = ()
    table_id: str | None = None
    """The table the entry was matched in; ``raw``/``bits`` for raw frames."""
    fallback: bool = False
    """Fallback bits that closed a frame: consumed, shown as nothing."""

    def encoded_bits(self) -> str:
        """Every bit this token stands for, operands included."""
        if self.entry is None:
            return self.bits
        return self.entry.bits + "".join(
            spec.bits_of(value)
            for spec, value in zip(self.entry.operands, self.operands, strict=False)
        )

    @property
    def is_end(self) -> bool:
        return self.entry is not None and self.entry.kind is EntryKind.END

    @property
    def is_code(self) -> bool:
        return self.entry is None or self.entry.kind is not EntryKind.TEXT

    @property
    def weight(self) -> int:
        return 0 if self.entry is None else self.entry.weight

    def text(self) -> str:
        return render_token(self)


_ESCAPES = {"[": "\\[", "]": "\\]", "\\": "\\\\"}


def escape_text(text: str) -> str:
    """Escape a literal text run for the script grammar."""
    return "".join(_ESCAPES.get(c, c) for c in text)


def render_token(token: Token) -> str:
    entry = token.entry
    if token.fallback:
        return ""
    if entry is None:
        n = len(token.bits)
        if n % 8 == 0:
            return "".join(
                f"[${int(token.bits[i : i + 8], 2):02X}]" for i in range(0, n, 8)
            )
        return f"[%{token.bits}]"
    if entry.kind in (EntryKind.TEXT, EntryKind.END, EntryKind.SWITCH):
        # Table text is already in script form; ``\n`` becomes a line break
        # on dump and is ignored on insert, so dumps re-insert unchanged.
        return entry.text.replace("\\n", "\n")
    if entry.kind is EntryKind.RETURN:
        return ""
    label = entry.text
    if entry.kind is EntryKind.CODE and entry.operands:
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


def operand_values(entry: Entry, words: tuple[str, ...]) -> tuple[int, ...]:
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
