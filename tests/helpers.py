"""Qt-free helpers shared by test modules: the table bodies and test ROMs the
tests read, the extract-edit-insert round trip, and the abcde dump comparison.
"""

from __future__ import annotations

import os
import re

from mapchar.core.table import Table, TableSet
from mapchar.pipeline.extract import extract
from mapchar.pipeline.insert import apply_splices, layout_block
from mapchar.plugins.charsets import apply_charset
from mapchar.plugins.registry import default_registry
from mapchar.project.formats.legacy import load_table_text
from mapchar.project.formats.table_native import HEADER, parse_native

ABC_TABLE = "@table main\n41=A\n42=B\n43=C\n/00=[end]\nFE=[line]\\n\n"
"""Three letters, an end token and a line code: the body most tests read."""

ASCII_TABLE = "@table main\n@charset ascii\n/00=[end]\n"
"""The ASCII charset with an end token, for tests whose data is plain text."""


def native_files(body: str) -> list[str]:
    """Native table bodies as the files they stand for, header added: each
    ``@table`` line starts another file."""
    return [
        HEADER + "\n" + part
        for part in re.split(r"(?m)^(?=@table )", body)
        if part.strip()
    ]


def tables_from(body: str) -> dict[str, Table]:
    """Parse native table text into ``{id: Table}``, a file per ``@table``."""
    tables = [parse_native(text).table for text in native_files(body)]
    registry = default_registry()
    for t in tables:
        apply_charset(t, registry)
    return {t.id: t for t in tables}


def table_set(body: str, start: str | None = None) -> TableSet:
    tables = tables_from(body)
    first = start or next(iter(tables))
    return TableSet.build(tables[first], tables)


def texts(strings) -> list[str]:
    """The original text of every string of an extraction, document or list."""
    return [s.original_text() for s in getattr(strings, "strings", strings)]


def pointer_rom(targets, body: str, *, at: int = 0x10, tail: int = 8) -> bytes:
    """A ROM of little-endian 16-bit pointers to ``targets`` at 0 and the hex
    bytes ``body`` at ``at``, padded with ``$FF`` to ``at`` and by ``tail``."""
    table = b"".join(t.to_bytes(2, "little") for t in targets)
    return table + b"\xff" * (at - len(table)) + bytes.fromhex(body) + b"\xff" * tail


def relayout(data: bytes, cfg, ts, edits: dict[int, str], registry=None, room=None):
    """Extract ``data``, translate ``{index: text}``, lay the block out again.

    ``room`` is what the block remembers giving up to an earlier shortening.
    Returns the layout result and the spliced bytes, ``None`` when the layout
    refused the edits.
    """
    ex = extract(data, cfg, ts, registry)
    for i, text in edits.items():
        ex.strings[i].replacement = text
    res = layout_block(data, cfg, ts, ex.strings, registry, room)
    return res, (apply_splices(data, res.splices) if res.ok else None)


def translated(data: bytes, cfg, ts, edits: dict[int, str], registry=None):
    """Strings whose bytes hold ``{index: text}``, with the originals they had
    before: what a block looks like after the edits landed. Returns the strings
    and the bytes."""
    res, out = relayout(data, cfg, ts, edits, registry)
    assert res.ok, res.problems
    before = extract(data, cfg, ts, registry).strings
    after = extract(out, cfg, ts, registry).strings
    for a, b in zip(after, before, strict=True):
        a.original = b.original
        a.refresh_status()
    return after, out


def load_abcde_tables(
    folder, registry, *, assert_clean: bool = False
) -> dict[str, Table]:
    """Every ``.tbl`` in ``folder`` read as abcde tables, keyed by table id."""
    tables: dict[str, Table] = {}
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".tbl"):
            continue
        with open(os.path.join(folder, name), encoding="utf-8") as f:
            tf = load_table_text(f.read(), name, "abcde")
        if assert_clean:
            assert not tf.notices, (name, tf.notices)
        for t in tf.tables:
            apply_charset(t, registry)
            tables[t.id] = t
    return tables


def cartographer_blocks(text: str) -> list[tuple[str, str]]:
    """``(block name, body)`` of an abcde Cartographer dump, in dump order.

    Comments are dropped except the ``//POINTER`` and ``//Block Range`` lines
    that mark where a string begins; :func:`normalise_dump` drops those.
    """
    blocks: list[tuple[str, str]] = []
    name: str | None = None
    body: list[str] = []
    for line in text.split("\n"):
        m = re.match(r"^//BLOCK #\d+ NAME:\t\t(.*)$", line)
        if m:
            if name is not None:
                blocks.append((name, "\n".join(body)))
            name, body = m.group(1), []
        elif name is None or line.startswith("#"):
            continue
        elif not line.startswith("//") or line.startswith(
            ("//POINTER", "//Block Range")
        ):
            body.append(line)
    if name is not None:
        blocks.append((name, "\n".join(body)))
    return blocks


def normalise_dump(text: str) -> str:
    """An abcde dump in mapchar's notation: the string markers dropped, raw
    bytes as ``[$xx]`` and the spaces of a label as underscores."""
    text = re.sub(r"//POINTER[^\n]*\n|//Block Range[^\n]*\n", "", text)
    text = re.sub(r"<\$([0-9A-Fa-f]{2})>", r"[$\1]", text)
    return re.sub(
        r"\[([^\]$%][^\]]*)\]",
        lambda m: "[" + re.sub(r"\s+", "_", m.group(1)) + "]",
        text,
    )
