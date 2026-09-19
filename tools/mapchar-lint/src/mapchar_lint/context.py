"""What every check is handed: the document, the id source, and somewhere to
report to — plus the per-entry reading each check would otherwise redo.

:class:`EntryView` holds both what the file *says* and what the loader will
*make of it* — ``raw_kind`` beside ``kind``, the drop causes beside the entry —
because almost every finding here is the gap between the two.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field

from mapchar_lint.config import ConfigReading, read_config
from mapchar_lint.diagnostics import Diagnostic, Report, Severity
from mapchar_lint.document import ProjectDocument
from mapchar_lint.known import KnownIds
from mapchar_lint.reading import Cause, drop_causes
from mapchar_lint.schema import CHILD_KINDS, KINDS


@dataclass
class EntryView:
    """One entry, as written and as it will be read."""

    index: int
    raw: dict
    #: The ``kind`` in the file, whatever it is.
    raw_kind: object = None
    #: The kind the reader makes of it; None when it makes none.
    kind: str | None = None
    name: str = ""
    #: Why the reader drops the entry; empty when it reads.
    causes: list = field(default_factory=list)
    #: A block's own configuration line, read; None for every other entry.
    config: ConfigReading | None = None
    #: Set by the reference checks: a row whose parent does not load is
    #: dropped with it, so it is as absent as one that did not read.
    orphaned: bool = False

    @property
    def pointer(self) -> str:
        return f"/entries/{self.index}"

    def at(self, *keys: object) -> str:
        """A JSON Pointer into this entry — ``at("session", "offset")``."""
        return "/".join([self.pointer, *(_escape(key) for key in keys)])

    @property
    def dropped(self) -> bool:
        """Whether the entry is absent from the loaded project."""
        return bool(self.causes) or self.orphaned

    @property
    def is_child(self) -> bool:
        return self.kind in CHILD_KINDS


def read_entry(index: int, raw: object) -> EntryView:
    """``raw`` read the way the loader reads it, mistakes preserved."""
    causes: list[Cause] = drop_causes(raw)
    if not isinstance(raw, dict):
        return EntryView(index=index, raw={}, causes=causes)
    raw_kind = raw.get("kind")
    kind = str(raw_kind) if str(raw_kind) in KINDS else None
    name = raw.get("name")
    path = raw.get("path")
    label = str(name) if name is not None and str(name) else ""
    if not label and isinstance(path, str) and path:
        label = path.replace("\\", "/").rsplit("/", 1)[-1]
    config = None
    if kind == "block" and raw.get("config"):
        config = read_config(raw["config"])
    return EntryView(
        index=index,
        raw=raw,
        raw_kind=raw_kind,
        kind=kind,
        name=label,
        causes=causes,
        config=config,
    )


def region_size(doc: ProjectDocument, view: EntryView) -> int | None:
    """The joined size of everything ``view``'s bytes come from, or None.

    Addresses are into the **join** of ``path`` and ``extra_paths``, so a file
    read from several ROM chips has to be measured whole. None when any part of
    it is missing — the reference checks report that, and a partial total would
    be a lie.
    """
    path = view.raw.get("path")
    if not (isinstance(path, str) and path):
        return None
    total = doc.size_of(path)
    if total is None:
        return None
    for item in view.raw.get("extra_paths") or ():
        if not (isinstance(item, str) and item):
            return None
        size = doc.size_of(item)
        if size is None:
            return None
        total += size
    return total


def parent_of(entries: list, view: EntryView) -> EntryView | None:
    """The entry a child's ``parent`` names, when it names one at all.

    Only an ``int`` is read as an index — a boolean too, being one in Python.
    """
    index = view.raw.get("parent")
    if not isinstance(index, int) or not 0 <= index < len(entries):
        return None
    return entries[index]


def _escape(key: object) -> str:
    """One JSON Pointer reference token (RFC 6901)."""
    return str(key).replace("~", "~0").replace("/", "~1")


@dataclass
class Context:
    """Shared state for one file's run."""

    doc: ProjectDocument
    ids: KnownIds
    report: Report
    entries: list = field(default_factory=list)
    #: Set from the CLI — the file checks are the only ones that touch the disk,
    #: and a project whose ROMs are elsewhere should still be lintable.
    check_files: bool = True

    def emit(
        self,
        code: str,
        severity: Severity,
        message: str,
        *,
        pointer: str = "",
        entry: EntryView | None = None,
        detail: str = "",
    ) -> None:
        self.report.add(
            Diagnostic(
                code=code,
                severity=severity,
                message=message,
                pointer=pointer,
                entry=entry.index if entry is not None else None,
                entry_name=entry.name if entry is not None else "",
                detail=detail,
            )
        )

    def error(self, code, message, **kwargs) -> None:
        self.emit(code, Severity.ERROR, message, **kwargs)

    def warn(self, code, message, **kwargs) -> None:
        self.emit(code, Severity.WARNING, message, **kwargs)

    def info(self, code, message, **kwargs) -> None:
        self.emit(code, Severity.INFO, message, **kwargs)

    def unknown_id(
        self,
        stage: str,
        plugin_id: str,
        what: str,
        *,
        code: str,
        pointer: str,
        entry: EntryView,
        consequence: str,
    ) -> None:
        """Report an id the source does not know — as missing when the source
        can see every plugin, as "not a built-in" when it cannot, and as
        possibly the project's own when its folder holds code plugins."""
        elsewhere = self.ids.stage_of(plugin_id)
        close = difflib.get_close_matches(
            plugin_id,
            sorted(
                self.ids.plugins.get(stage, set()) | self.ids.local.get(stage, set())
            ),
            n=1,
        )
        hint = (
            f" — that is a {elsewhere} id"
            if elsewhere
            else f" — did you mean {close[0]!r}?"
            if close
            else ""
        )
        if self.ids.authoritative and stage not in self.ids.opaque:
            self.error(
                f"E{code}",
                f"{what} {plugin_id!r} is not installed{hint}",
                pointer=pointer,
                entry=entry,
                detail=consequence,
            )
            return
        maybe = (
            "the project's plugins/ folder holds code plugins, which may provide it"
            if stage in self.ids.opaque
            else f"this is checked against the {self.ids.source}; re-run with --live "
            "to see your own plugins"
        )
        self.warn(
            f"W{code}",
            f"{what} {plugin_id!r} is not one of mapchar's built-in ids{hint}",
            pointer=pointer,
            entry=entry,
            detail=f"{consequence} Unless it is your own plugin: {maybe}.",
        )
