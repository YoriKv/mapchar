"""How far the translation has got: the block in hand, and the project."""

from __future__ import annotations

from collections.abc import Iterable

from mapchar.core.block import Status
from mapchar.core.document import Document
from mapchar.project.workspace import EntryKind, Workspace


def _counts(statuses: Iterable[Status]) -> tuple[int, int, int]:
    """How many of ``statuses`` are touched, done, and how many there are."""
    statuses = list(statuses)
    touched = sum(s is not Status.UNTOUCHED for s in statuses)
    done = sum(s is Status.DONE for s in statuses)
    return touched, done, len(statuses)


def progress_text(workspace: Workspace, doc: Document) -> str:
    """How far ``doc``'s block and the whole project are: strings whose bytes
    no longer say the original, over all of them.

    A block the session has not read has no records to count, so the state the
    project keeps for it is counted instead — a project's progress that left
    out every block not yet opened would climb as they were.
    """
    touched, done, total = _counts(rec.status for rec in doc.strings)
    all_touched, all_done, all_total = 0, 0, 0
    for e in workspace.of_kind(EntryKind.BLOCK):
        if e.doc is not None:
            a, d, t = _counts(rec.status for rec in e.doc.strings)
        elif e.pending_strings:
            a, d, t = _counts(st.status for st in e.pending_strings.values())
        else:
            continue
        all_touched += a
        all_done += d
        all_total += t

    def pct(d: int, t: int) -> str:
        return f"{d} / {t} ({100 * d // t}%)" if t else "0 / 0"

    text = f"translated {pct(touched, total)}"
    if done:
        text += f", done {done}"
    if all_total != total:
        text += f" · project {pct(all_touched, all_total)}"
        if all_done:
            text += f", done {all_done}"
    return text
