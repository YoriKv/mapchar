"""Translator hand-off files: TSV/CSV and PO, one record per string."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

from mapchar.core.block import StringRecord
from mapchar.core.numbers import format_num, parse_num
from mapchar.project.formats.textfile import BOM, escape, split_lines, unescape

FIELDS = ("id", "address", "original", "translation", "status", "notes")


@dataclass
class Record:
    id: str
    address: int
    original: str
    translation: str
    status: str
    notes: str


def records_for(block_name: str, strings: list[StringRecord]) -> list[Record]:
    return [
        Record(
            f"{block_name}/{s.index}",
            s.start,
            s.original,
            s.current_text() if s.edited else "",
            s.status.value,
            s.notes,
        )
        for s in strings
    ]


# --- TSV / CSV --------------------------------------------------------------


def write_delimited(records: list[Record], delimiter: str = "\t") -> str:
    """The records as TSV or CSV text, ready to write as UTF-8.

    CSV starts with a byte-order mark, which is what spreadsheets need to read
    a UTF-8 file as UTF-8; TSV and PO stay without one. An import accepts
    either, since :func:`~mapchar.project.formats.textfile.split_lines` drops a mark.
    """
    out = io.StringIO()
    if delimiter == "\t":
        out.write("\t".join(FIELDS) + "\n")
        for r in records:
            cells = [
                r.id,
                format_num(r.address),
                escape(r.original, "\t"),
                escape(r.translation, "\t"),
                r.status,
                escape(r.notes, "\t"),
            ]
            out.write("\t".join(cells) + "\n")
        return out.getvalue()
    out.write(BOM)
    writer = csv.writer(out, delimiter=delimiter, lineterminator="\n")
    writer.writerow(FIELDS)
    for r in records:
        writer.writerow(
            [
                r.id,
                format_num(r.address),
                r.original,
                r.translation,
                r.status,
                r.notes,
            ]
        )
    return out.getvalue()


def read_delimited(text: str) -> list[Record]:
    lines = split_lines(text)
    first = lines[0] if lines else ""
    delimiter = "\t" if "\t" in first else ("," if "," in first else ";")
    if delimiter == "\t":
        rows = [line.split("\t") for line in lines if line]
        cell_of = unescape
    else:
        rows = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter))

        def cell_of(s: str) -> str:
            return s

    if not rows:
        return []
    header = [h.strip().lower() for h in rows[0]]
    index = {name: header.index(name) for name in FIELDS if name in header}
    if "id" not in index:
        raise ValueError("no 'id' column")
    records = []
    for row in rows[1:]:
        if not any(row):
            continue
        record = _record_from_row(row, index, cell_of)
        if record is not None:
            records.append(record)
    return records


def _record_from_row(row: list[str], index: dict[str, int], cell_of) -> Record | None:
    def cell(name: str, default: str = "") -> str:
        i = index.get(name)
        return cell_of(row[i]) if i is not None and i < len(row) else default

    addr = cell("address", "0").strip()
    return Record(
        cell("id").strip(),
        parse_num(addr or "0"),
        cell("original"),
        cell("translation"),
        cell("status", "untouched").strip() or "untouched",
        cell("notes"),
    )


# --- PO -------------------------------------------------------------------


DONE_COMMENT = "# done"
"""How a PO file says a string is done: a translator comment, PO having no
flag for it, that survives the tools that keep such comments."""


def _po_quote(text: str) -> str:
    return '"' + escape(text, '"') + '"'


def write_po(records: list[Record], rom: str = "rom") -> str:
    lines = [
        'msgid ""',
        'msgstr ""',
        '"Content-Type: text/plain; charset=UTF-8\\n"',
        "",
    ]
    for r in records:
        if r.notes:
            for note in r.notes.split("\n"):
                lines.append(f"#. {note}")
        lines.append(f"#: {rom}:{format_num(r.address)}")
        if r.status == "review":
            lines.append("#, fuzzy")
        elif r.status == "done":
            lines.append(DONE_COMMENT)
        lines.append(f"msgctxt {_po_quote(r.id)}")
        lines.append(f"msgid {_po_quote(r.original)}")
        lines.append(f"msgstr {_po_quote(r.translation)}")
        lines.append("")
    return "\n".join(lines)


def read_po(text: str) -> list[Record]:
    records: list[Record] = []
    entry: dict[str, str] = {}
    notes: list[str] = []
    fuzzy = done = False
    address = 0
    current: str | None = None

    def flush() -> None:
        nonlocal entry, notes, fuzzy, done, address, current
        if "msgctxt" in entry and entry.get("msgid", "") != "" or entry.get("msgctxt"):
            if fuzzy:
                status = "review"
            elif done:
                status = "done"
            else:
                status = "edited" if entry.get("msgstr") else "untouched"
            records.append(
                Record(
                    entry.get("msgctxt", ""),
                    address,
                    entry.get("msgid", ""),
                    entry.get("msgstr", ""),
                    status,
                    "\n".join(notes),
                )
            )
        entry, notes, fuzzy, done, address, current = {}, [], False, False, 0, None

    for raw in split_lines(text):
        line = raw.strip()
        if not line:
            flush()
            continue
        if line.startswith("#."):
            notes.append(line[2:].strip())
        elif line.startswith("#,") and "fuzzy" in line:
            fuzzy = True
        elif line == DONE_COMMENT:
            done = True
        elif line.startswith("#:"):
            m = re.search(r":\$([0-9A-Fa-f]+)", line)
            if m:
                address = int(m.group(1), 16)
        elif line.startswith("#"):
            continue
        elif line.startswith('"') and current is not None:
            entry[current] = entry.get(current, "") + unescape(line[1:-1])
        else:
            m = re.match(r"^(msgctxt|msgid|msgstr)\s+\"(.*)\"$", line)
            if m:
                current = m.group(1)
                entry[current] = unescape(m.group(2))
    flush()
    return records


# --- applying records -------------------------------------------------------


@dataclass
class ImportReport:
    applied: int = 0
    skipped: list[str] = field(default_factory=list)
    texts: dict[str, dict[int, str]] = field(default_factory=dict)
    """Per block, the text each record's string is to hold, by index."""
    review: dict[str, dict[int, bool]] = field(default_factory=dict)
    """Per block, the strings the records mark for review."""
    done: dict[str, dict[int, bool]] = field(default_factory=dict)
    """Per block, the strings the records mark done."""
    notes: dict[str, dict[int, str]] = field(default_factory=dict)
    """Per block, the notes the records carry."""


def apply_records(
    records: list[Record],
    blocks: dict[str, list[StringRecord]],
    *,
    force: bool = False,
) -> ImportReport:
    """Match records to strings by id; a record whose original drifted is
    skipped. What each string is to say, and its review mark and notes, come
    back in the report — nothing is changed here."""
    report = ImportReport()
    for r in records:
        name, _, idx = r.id.rpartition("/")
        strings = blocks.get(name)
        if strings is None or not idx.isdigit():
            report.skipped.append(f"{r.id}: no such block")
            continue
        rec = next((s for s in strings if s.index == int(idx)), None)
        if rec is None:
            report.skipped.append(f"{r.id}: no such string")
            continue
        if not force and r.original and not rec.matches_original(r.original):
            report.skipped.append(f"{r.id}: original changed")
            continue
        if r.translation:
            report.texts.setdefault(name, {})[rec.index] = r.translation
        if r.status == "review":
            report.review.setdefault(name, {})[rec.index] = True
        elif r.status == "done":
            report.done.setdefault(name, {})[rec.index] = True
        if r.notes:
            report.notes.setdefault(name, {})[rec.index] = r.notes
        report.applied += 1
    return report
