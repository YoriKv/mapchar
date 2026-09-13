"""Translator hand-off files: TSV/CSV and PO, one record per string."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass

from mapchar.core.block import Status, StringRecord

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
            s.original_text(),
            s.translation or "",
            s.status.value,
            s.notes,
        )
        for s in strings
    ]


# --- TSV / CSV --------------------------------------------------------------


def write_delimited(records: list[Record], delimiter: str = "\t") -> str:
    out = io.StringIO()
    if delimiter == "\t":
        out.write("\t".join(FIELDS) + "\n")
        for r in records:
            cells = [
                r.id,
                f"${r.address:X}",
                _tsv_escape(r.original),
                _tsv_escape(r.translation),
                r.status,
                _tsv_escape(r.notes),
            ]
            out.write("\t".join(cells) + "\n")
        return out.getvalue()
    writer = csv.writer(out, delimiter=delimiter, lineterminator="\n")
    writer.writerow(FIELDS)
    for r in records:
        writer.writerow(
            [r.id, f"${r.address:X}", r.original, r.translation, r.status, r.notes]
        )
    return out.getvalue()


def _tsv_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n")


def _tsv_unescape(text: str) -> str:
    return re.sub(
        r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), text
    )


def read_delimited(text: str) -> list[Record]:
    text = text.lstrip("\ufeff")
    first = text.split("\n", 1)[0]
    delimiter = "\t" if "\t" in first else ("," if "," in first else ";")
    if delimiter == "\t":
        rows = [
            line.split("\t") for line in text.replace("\r\n", "\n").split("\n") if line
        ]
        unescape = _tsv_unescape
    else:
        rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))

        def unescape(s: str) -> str:
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
        record = _record_from_row(row, index, unescape)
        if record is not None:
            records.append(record)
    return records


def _record_from_row(row: list[str], index: dict[str, int], unescape) -> Record | None:
    def cell(name: str, default: str = "") -> str:
        i = index.get(name)
        return unescape(row[i]) if i is not None and i < len(row) else default

    addr = cell("address", "0").strip()
    return Record(
        cell("id").strip(),
        int(addr[1:], 16) if addr.startswith("$") else int(addr or "0"),
        cell("original"),
        cell("translation"),
        cell("status", "untouched").strip() or "untouched",
        cell("notes"),
    )


# --- PO -------------------------------------------------------------------


def _po_quote(text: str) -> str:
    body = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{body}"'


def _po_unquote(text: str) -> str:
    return re.sub(
        r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), text
    )


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
        lines.append(f"#: {rom}:${r.address:X}")
        if r.status == "review":
            lines.append("#, fuzzy")
        lines.append(f"msgctxt {_po_quote(r.id)}")
        lines.append(f"msgid {_po_quote(r.original)}")
        lines.append(f"msgstr {_po_quote(r.translation)}")
        lines.append("")
    return "\n".join(lines)


def read_po(text: str) -> list[Record]:
    records: list[Record] = []
    entry: dict[str, str] = {}
    notes: list[str] = []
    fuzzy = False
    address = 0
    current: str | None = None

    def flush() -> None:
        nonlocal entry, notes, fuzzy, address, current
        if "msgctxt" in entry and entry.get("msgid", "") != "" or entry.get("msgctxt"):
            records.append(
                Record(
                    entry.get("msgctxt", ""),
                    address,
                    entry.get("msgid", ""),
                    entry.get("msgstr", ""),
                    "review"
                    if fuzzy
                    else ("edited" if entry.get("msgstr") else "untouched"),
                    "\n".join(notes),
                )
            )
        entry, notes, fuzzy, address, current = {}, [], False, 0, None

    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line:
            flush()
            continue
        if line.startswith("#."):
            notes.append(line[2:].strip())
        elif line.startswith("#,") and "fuzzy" in line:
            fuzzy = True
        elif line.startswith("#:"):
            m = re.search(r":\$([0-9A-Fa-f]+)", line)
            if m:
                address = int(m.group(1), 16)
        elif line.startswith("#"):
            continue
        elif line.startswith('"') and current is not None:
            entry[current] = entry.get(current, "") + _po_unquote(line[1:-1])
        else:
            m = re.match(r"^(msgctxt|msgid|msgstr)\s+\"(.*)\"$", line)
            if m:
                current = m.group(1)
                entry[current] = _po_unquote(m.group(2))
    flush()
    return records


# --- applying records -------------------------------------------------------


@dataclass
class ImportReport:
    applied: int = 0
    skipped: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.skipped is None:
            self.skipped = []


def apply_records(
    records: list[Record],
    blocks: dict[str, list[StringRecord]],
    *,
    force: bool = False,
) -> ImportReport:
    """Set translations by id; a record whose original drifted is skipped."""
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
        if (
            not force
            and r.original
            and r.original.replace("\n", "") != rec.original_text().replace("\n", "")
        ):
            report.skipped.append(f"{r.id}: original changed")
            continue
        translation = r.translation or None
        if translation is not None and translation != rec.translation:
            rec.translation = translation
            rec.status = Status.EDITED
        if r.status == "review":
            rec.status = Status.REVIEW
        if r.notes:
            rec.notes = r.notes
        report.applied += 1
    return report
