"""What an import will do, read off its report before anything is applied.

Both importers plan before they change anything —
:func:`~mapchar.project.formats.script.apply_script` and
:func:`~mapchar.project.formats.translator.apply_records` return what each
string is to say and alter nothing — so the plan can be shown and confirmed.
This module turns either report into one shape the dialog draws, and
:class:`ImportSummary` is Qt-free so what the dialog will say is testable
without one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mapchar.project.formats.script import Script, ScriptImportReport
from mapchar.project.formats.translator import ImportReport

EDIT = "edit"
NEW_BLOCK = "new block"


@dataclass
class BlockPlan:
    """One block the file reaches, and what the import does to it."""

    name: str
    strings: int
    action: str = EDIT


@dataclass
class ImportSummary:
    """The whole of what an import will do: which blocks, how many strings of
    each, and what it cannot place."""

    kind: str
    """What the file is, in the words the dialog heads itself with."""
    blocks: list[BlockPlan] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    forceable: bool = False
    """Whether **Force** would take any of the skipped records anyway: true
    only for the translator formats, which are the only ones that compare
    originals."""

    @property
    def strings(self) -> int:
        return sum(b.strings for b in self.blocks)

    @property
    def nothing_to_do(self) -> bool:
        return not self.blocks


def summarise_script(script: Script, report: ScriptImportReport) -> ImportSummary:
    """A native script's plan. A block the project lacks is counted from the
    script rather than the report: ``apply_script`` places nothing in a block
    that does not exist yet, and the import creates it and plans again."""
    new = {name for name, _ in report.new_blocks}
    blocks = [
        BlockPlan(sb.name, len(sb.strings), NEW_BLOCK if sb.name in new else EDIT)
        for sb in script.blocks
        if sb.name in new or sb.name in report.texts
    ]
    return ImportSummary("Native script", blocks, list(report.notices))


def summarise_records(kind: str, report: ImportReport) -> ImportSummary:
    """A translator file's plan. Every skipped record is a candidate for
    **Force**, which is why the summary is rebuilt rather than filtered when it
    is switched on.

    A block's count is every string the records reach, not only the ones whose
    text changes: a record carrying nothing but a status or a note still lands
    on its string."""
    touched: dict[str, set[int]] = {}
    for by_block in (report.texts, report.review, report.done, report.notes):
        for name, by_index in by_block.items():
            touched.setdefault(name, set()).update(by_index)
    blocks = [BlockPlan(name, len(ix)) for name, ix in sorted(touched.items())]
    return ImportSummary(kind, blocks, list(report.skipped), forceable=True)
