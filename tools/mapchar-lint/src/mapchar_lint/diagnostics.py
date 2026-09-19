"""What a check reports, and the two shapes it is reported in.

Severity is about **what the loader does with the mistake**, not about how bad
it looks. The `.mapchar` reader is deliberately tolerant — unknown keys ignored,
a broken entry dropped with one line in a notice, a bad id degraded to a
pass-through — so a hand-edited project almost never fails to open. It opens
*wrong*, quietly. That is what the scale below is calibrated on:

- ``error`` — the loader will silently drop or misread this. Something the
  author wrote will not be in the project they open.
- ``warning`` — the loader degrades gracefully, but the result is probably not
  what was meant (a row shown outside its folder, a stage read as a pass-through).
- ``info`` — the project is correct and opens as written; the file is merely
  not in the form mapchar itself would write it (a redundant default, an id that
  a re-save would rewrite).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"

    @property
    def rank(self) -> int:
        """Sort order, worst first."""
        return _RANK[self]


_RANK = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}


@dataclass(frozen=True)
class Diagnostic:
    """One finding, addressed to a place in the document.

    ``pointer`` is a JSON Pointer (RFC 6901) at the offending value —
    ``/entries/12/config`` — because that is the one address
    that means the same thing to a person reading the file and to a tool
    editing it, which is the audience here. ``entry`` and ``entry_name`` are
    carried beside it so the text report can say *which* entry without the
    reader counting brackets.

    ``detail`` says what the loader will do about it. That is the whole value of
    a linter over this format: not "this is wrong" but "this will open as
    something else, and here is what".
    """

    code: str
    severity: Severity
    message: str
    pointer: str = ""
    entry: int | None = None
    entry_name: str = ""
    detail: str = ""

    @property
    def sort_key(self) -> tuple:
        # By severity, then by position in the file, so a report reads in the
        # order the entries sit in and the worst news is at the top of each.
        return (
            self.severity.rank,
            self.entry if self.entry is not None else -1,
            self.code,
        )

    def as_dict(self) -> dict:
        data = {
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
            "pointer": self.pointer,
        }
        if self.entry is not None:
            data["entry"] = self.entry
            data["entry_name"] = self.entry_name
        if self.detail:
            data["detail"] = self.detail
        return data


@dataclass
class Report:
    """Everything one project file produced."""

    path: str
    diagnostics: list[Diagnostic] = field(default_factory=list)
    #: Set when the file could not be parsed at all — the entry checks never
    #: ran, so an empty diagnostics list here does not mean a clean project.
    fatal: bool = False
    #: Which id source answered — the shipped snapshot or a live registry.
    id_source: str = ""

    def add(self, diagnostic: Diagnostic) -> None:
        self.diagnostics.append(diagnostic)

    def counts(self) -> dict[str, int]:
        out = {severity.value: 0 for severity in Severity}
        for diagnostic in self.diagnostics:
            out[diagnostic.severity.value] += 1
        return out

    def worst(self) -> Severity | None:
        return min(
            (d.severity for d in self.diagnostics), key=lambda s: s.rank, default=None
        )


# -- rendering -------------------------------------------------------------
_ICON = {Severity.ERROR: "error", Severity.WARNING: "warning", Severity.INFO: "info"}


def render_text(reports: list[Report], *, color: bool = False) -> str:
    """The human report: one block per entry, one line per finding.

    Grouped by entry rather than listed flat because the questions a reader has
    are per entry — *does this block read, does it fit its file* — and a
    flat list of forty lines interleaves five entries' answers.
    """
    paint = _painter(color)
    out: list[str] = []
    for report in reports:
        out.append(paint(report.path, "bold"))
        if report.fatal:
            for diagnostic in report.diagnostics:
                out.append(_line(diagnostic, paint, indent="  "))
            out.append("")
            continue
        if not report.diagnostics:
            out.append(paint("  no problems found", "dim"))
            out.append("")
            continue
        ordered = sorted(report.diagnostics, key=lambda d: d.sort_key)
        # Entry-scoped findings sit under a header naming the entry; the
        # document-level ones have no entry and lead.
        for diagnostic in [d for d in ordered if d.entry is None]:
            out.append(_line(diagnostic, paint, indent="  "))
        by_entry: dict[int, list[Diagnostic]] = {}
        for diagnostic in ordered:
            if diagnostic.entry is not None:
                by_entry.setdefault(diagnostic.entry, []).append(diagnostic)
        for index in sorted(by_entry):
            name = by_entry[index][0].entry_name
            label = f"  entries[{index}]" + (f"  {name}" if name else "")
            out.append(paint(label, "bold"))
            for diagnostic in by_entry[index]:
                out.append(_line(diagnostic, paint, indent="    "))
        out.append("")
        out.append(_summary(report, paint))
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _line(diagnostic: Diagnostic, paint, indent: str) -> str:
    tag = paint(f"{_ICON[diagnostic.severity]:<7}", diagnostic.severity.value)
    code = paint(diagnostic.code, "dim")
    line = f"{indent}{tag} {code}  {diagnostic.message}"
    if diagnostic.pointer:
        line += paint(f"\n{indent}        at {diagnostic.pointer}", "dim")
    if diagnostic.detail:
        line += paint(f"\n{indent}        {diagnostic.detail}", "dim")
    return line


def _summary(report: Report, paint) -> str:
    counts = report.counts()
    parts = [
        paint(
            f"{counts[s.value]} {s.value}{'' if counts[s.value] == 1 else 's'}", s.value
        )
        for s in Severity
        if counts[s.value]
    ]
    return "  " + (", ".join(parts) if parts else paint("clean", "dim"))


_COLORS = {
    "error": "\033[31m",
    "warning": "\033[33m",
    "info": "\033[36m",
    "bold": "\033[1m",
    "dim": "\033[2m",
}


def _painter(color: bool):
    if not color:
        return lambda text, _style: text
    return lambda text, style: f"{_COLORS.get(style, '')}{text}\033[0m"


def render_json(reports: list[Report]) -> str:
    """The machine report — what an agent editing the file reads back.

    Every finding carries its JSON Pointer, so a fix can be applied without
    re-deriving where in the document the complaint lands.
    """
    body = {
        "reports": [
            {
                "path": report.path,
                "fatal": report.fatal,
                "id_source": report.id_source,
                "counts": report.counts(),
                "diagnostics": [
                    d.as_dict()
                    for d in sorted(report.diagnostics, key=lambda d: d.sort_key)
                ],
            }
            for report in reports
        ]
    }
    return json.dumps(body, indent=2, ensure_ascii=False) + "\n"
