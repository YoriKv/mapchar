"""Running every check over one project file."""

from __future__ import annotations

from mapchar_lint import document
from mapchar_lint.checks import PASSES, crossref
from mapchar_lint.context import Context, read_entry
from mapchar_lint.diagnostics import Report
from mapchar_lint.known import KnownIds, for_project


def lint(path: str, ids: KnownIds, *, check_files: bool = True) -> Report:
    """Every finding for ``path``.

    A parse failure short-circuits: the entry checks would have nothing to walk,
    and reporting "no problems in the entries" about a file that is not JSON is
    worse than saying nothing. :attr:`Report.fatal` marks that case so the
    caller does not read an empty list as a clean bill.
    """
    report = Report(path=path, id_source=ids.source)
    doc, fatal = document.load(path)
    if doc is None:
        report.fatal = True
        report.diagnostics.extend(fatal)
        return report
    ctx = Context(
        doc=doc,
        # The project's own plugins/ folder counts as installed *for this
        # project*, so it is resolved per file rather than once per run.
        ids=for_project(ids, path),
        report=report,
        entries=[read_entry(at, raw) for at, raw in enumerate(doc.entries)],
        check_files=check_files,
    )
    crossref.resolve(ctx)
    for run in PASSES:
        run(ctx)
    return report
