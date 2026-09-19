"""Which table ids a table entry gives the project.

A block names its table by id (``table=main``), and the id comes from three
places in turn: the entry's own ``table`` key, the ``@table`` line of a native
file (``@name`` lines of an abcde one, which can hold several tables), and
otherwise the file's name. Reading the file for that line is the one place the
linter opens a table — only its header lines, never its entries, which are the
table editor's business and not the project's.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from mapchar_lint.document import ProjectDocument

#: ``mapchar.core.table.ID_PATTERN``.
ID_PATTERN = re.compile(r"[\w.-]+")
_NATIVE_HEADER = re.compile(r"^@mapchar\s+table\b")
_NATIVE_TABLE = re.compile(r"^@table\s+(\S+)\s*$")
#: An abcde table header (``legacy.abcde``: ``^@([^<>]+)$``).
_ABCDE_HEADER = re.compile(r"^@([^<>]+)$")


@dataclass
class TableIds:
    """The ids one table entry provides, and whether they could be read."""

    ids: list = field(default_factory=list)
    #: False when the entry names a file that is not there or does not read:
    #: its ids are then unknown, so a block naming one cannot be judged.
    known: bool = True


def sanitize_id(text: str) -> str:
    """``projectfile``'s ``sanitize_id``: what ``ID_PATTERN`` rejects becomes ``_``."""
    return re.sub(r"[^\w.-]", "_", text)


def stem_id(path: str) -> str:
    """``tables.table_id_for``: the id a file without a ``@table`` line gives."""
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    if "." in name:
        name = name.rsplit(".", 1)[0]
    return sanitize_id(name) or "table"


def table_ids(doc: ProjectDocument, raw: dict) -> TableIds:
    """The ids ``raw`` — a table entry — provides."""
    path = raw.get("path")
    override = raw.get("table")
    if not (isinstance(path, str) and path):
        name = raw.get("name")
        wanted = str(override or sanitize_id(str(name if name is not None else "")))
        # ``Table(id)`` refuses an id outside the pattern, and the reader then
        # names the table "table" (``projectfile._entry_from``).
        return TableIds([wanted if ID_PATTERN.fullmatch(wanted) else "table"])
    text = doc.text_of(path)
    if text is None:
        return TableIds([str(override)] if override else [], known=bool(override))
    ids = _file_ids(text, path, raw.get("dialect"))
    if override:
        ids[0] = str(override)
    return TableIds(ids)


def _file_ids(text: str, path: str, dialect: object) -> list:
    lines = [line.rstrip() for line in text.splitlines()]
    content = [line for line in lines if line and not line.startswith("#")]
    native = bool(content) and bool(_NATIVE_HEADER.match(content[0]))
    if dialect == "native" or (dialect is None and native):
        for line in content:
            m = _NATIVE_TABLE.match(line)
            if m:
                return [m.group(1)]
        return [stem_id(path)]
    if dialect in (None, "abcde"):
        headers = [
            sanitize_id(m.group(1))
            for line in lines
            if (m := _ABCDE_HEADER.match(line))
        ]
        if headers:
            return headers
    return [stem_id(path)]
