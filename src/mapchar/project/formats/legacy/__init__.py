"""Readers for the romjuice, Cartographer, Atlas and abcde table dialects.

One module per dialect, each following its own tool's parsing rules
(``docs/table-dialects.md`` and the tool docs) and converting to the native
model, recording what the conversion changed as notices. Only abcde is written
back out, beside the Atlas exporter that needs it
(:func:`~mapchar.project.formats.legacy.abcde.write_abcde_table`).

Detection and dispatch live here, with the helpers every dialect shares:
:func:`legacy_text` for text whose line breaks are real characters,
:func:`_even_key` for a whole-byte hex key and :func:`_add_or_note` for adding
an entry without letting one bad line stop the file.
"""

from __future__ import annotations

import re

from mapchar.core.bits import hex_to_bits
from mapchar.core.errors import TableError
from mapchar.core.notices import Level, Notice
from mapchar.core.table import Entry, Table
from mapchar.project.formats.table_native import (
    TableFile,
    is_native,
    parse_native,
    sanitize_label,
)
from mapchar.project.formats.textfile import split_lines

DIALECTS = ("native", "abcde", "cartographer", "atlas", "romjuice")

_HEXKEY = re.compile(r"^[0-9A-Fa-f]+$")


def detect_dialect(text: str) -> tuple[str, bool]:
    """Guess the dialect; the flag says whether the guess is certain."""
    if is_native(text):
        return "native", True
    lines = [ln.rstrip() for ln in split_lines(text)]
    for line in lines:
        if (
            re.match(r"^@[^<>=]+$", line)
            or re.match(r"^/?!/?[0-9A-Fa-f%]+(<-?\d+>)?=", line)
            and "," in line
        ):
            return "abcde", True
        if line.startswith("%") or line.startswith("#"):
            return "abcde", True
    for line in lines:
        if re.match(r"^![0-9A-Fa-f]+$", line) or re.match(r"^@[0-9A-Fa-f]+=\S+,", line):
            return "romjuice", True
        if re.match(r"^\$[0-9A-Fa-f]+=\d+$", line):
            return "romjuice", True
    for line in lines:
        if re.match(r"^\$[0-9A-Fa-f]+=[^,]+,\d+$", line):
            return "cartographer", True
    for line in lines:
        if re.match(r"^\*[0-9A-Fa-f]+", line) or re.match(r"^/[^0-9A-Fa-f]", line):
            return "atlas", True
    return "cartographer", False


def load_table_text(
    text: str, path: str | None = None, dialect: str | None = None
) -> TableFile:
    """Load a table file in the given dialect, or the detected one."""
    # Imported here rather than at the top: every reader reads the helpers above
    # out of this module, and `project.tables` reads `load_table_text` out of it.
    from mapchar.project.formats.legacy.abcde import read_abcde
    from mapchar.project.formats.legacy.atlas import read_atlas
    from mapchar.project.formats.legacy.cartographer import read_cartographer
    from mapchar.project.formats.legacy.romjuice import read_romjuice
    from mapchar.project.tables import table_id_for

    if dialect is None:
        dialect, _ = detect_dialect(text)
    if dialect not in DIALECTS:
        raise TableError(f"unknown dialect {dialect!r}", path)
    default_id = table_id_for(path)
    if dialect == "native":
        return parse_native(text, path, default_id)
    readers = {
        "abcde": read_abcde,
        "cartographer": read_cartographer,
        "atlas": read_atlas,
        "romjuice": read_romjuice,
    }
    result = readers[dialect](text, path, default_id)
    result.dialect = dialect
    return result


_CODE_IN_TEXT = re.compile(r"\[([^\[\]]+)\]")


def legacy_text(raw: str) -> str:
    """Native script form of legacy text whose line breaks are real characters.

    Bracketed code notation such as ``[END]`` or ``[cardinal #]`` is kept as
    a code (whitespace inside becoming ``_``), inside text too; any other
    bracket is escaped so it stays literal text.
    """
    body = raw.replace("\\", "\\\\")
    body = body.replace("\n", "\\n")
    out = []
    pos = 0
    for m in _CODE_IN_TEXT.finditer(body):
        inner = m.group(1)
        if inner[0] in "$%" or "\\" in inner:
            continue
        out.append(body[pos : m.start()].replace("[", "\\[").replace("]", "\\]"))
        out.append(f"[{sanitize_label(inner)}]")
        pos = m.end()
    out.append(body[pos:].replace("[", "\\[").replace("]", "\\]"))
    return "".join(out)


def _even_key(key: str, path: str | None, n: int) -> str:
    """A whole-byte hex key as bits; anything else is an error."""
    if not _HEXKEY.match(key) or len(key) % 2:
        raise TableError(f"key {key!r} must be whole bytes", path, n)
    return hex_to_bits(key)


def _add_or_note(table: Table, entry: Entry, n: int, notices: list[Notice]) -> None:
    """Add ``entry``, or say in a notice why it could not be added.

    A legacy file is somebody else's output: one line the native model refuses
    costs that line and not the file.
    """
    if entry.bits in table.entries:
        notices.append(Notice(f"line {n}: duplicate key, first entry wins", Level.INFO))
        return
    try:
        table.add(entry)
    except TableError as exc:
        notices.append(Notice(f"line {n}: {exc.message}; entry dropped", Level.INFO))
