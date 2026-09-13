"""Apply a parsed native script to a project's blocks."""

from __future__ import annotations

from dataclasses import dataclass, field

from mapchar.core.block import BlockConfig, Status, StringRecord
from mapchar.project.formats.script import Script


@dataclass
class ScriptImportReport:
    applied: int = 0
    notices: list[str] = field(default_factory=list)
    new_blocks: list[tuple[str, BlockConfig]] = field(default_factory=list)
    """Blocks the script carries that the project lacks, with their config."""


def apply_script(
    script: Script, blocks: dict[str, list[StringRecord]]
) -> ScriptImportReport:
    report = ScriptImportReport()
    for sb in script.blocks:
        strings = blocks.get(sb.name)
        if strings is None:
            if sb.config is not None:
                report.new_blocks.append((sb.name, sb.config))
            else:
                report.notices.append(
                    f"{sb.name}: not in the project and no configuration"
                )
            continue
        by_index = {s.index: s for s in strings}
        for ss in sb.strings:
            rec = by_index.get(ss.index)
            if rec is None:
                report.notices.append(f"{sb.name}/{ss.index}: no such string")
                continue
            if (ss.start, ss.end) != (rec.start, rec.end):
                report.notices.append(
                    f"{sb.name}/{ss.index}: script says ${ss.start:X}-${ss.end:X}, "
                    f"project has ${rec.start:X}-${rec.end:X}"
                )
            original = rec.original_text().replace("\n", "")
            if ss.text.replace("\n", "") == original:
                if rec.translation is not None:
                    rec.translation = None
                    rec.status = Status.UNTOUCHED
            else:
                rec.translation = ss.text
                rec.status = Status.EDITED
            report.applied += 1
    return report
