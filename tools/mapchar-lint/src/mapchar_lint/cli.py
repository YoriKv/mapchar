"""``mapchar-lint`` — the command line.

Exit status is what a script or a pre-commit hook reads: **1 for anything at or
above the failure threshold, 0 otherwise**, with 2 kept for the linter's own
failures (no such file, bad arguments) so "the project is broken" and "the run
is broken" are never the same answer.

The threshold defaults to ``error`` rather than to "any finding" because the
info level exists to describe files that are *correct* — a redundant default, an
id a re-save would rewrite. Failing a build on those would make the level
useless.
"""

from __future__ import annotations

import argparse
import sys
from glob import glob
from os.path import exists, isdir, join

from mapchar_lint.diagnostics import Severity, render_json, render_text
from mapchar_lint.known import known_ids
from mapchar_lint.linter import lint

_LEVELS = {"error": Severity.ERROR, "warning": Severity.WARNING, "info": Severity.INFO}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mapchar-lint",
        description="Check .mapchar project files for configuration and structural "
        "errors.",
        epilog="A mapchar project loads tolerantly by design: an entry that does not "
        "read is dropped, a configuration word that is not a setting is passed over, "
        "an unknown plugin id becomes a pass-through. This reports what a load would "
        "silently change.",
    )
    parser.add_argument(
        "paths",
        nargs="+",
        metavar="PROJECT",
        help="one or more .mapchar files, or directories to search for them",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="text for a person (default), json for a tool — every finding carries a "
        "JSON Pointer at the value it is about",
    )
    parser.add_argument(
        "--min-severity",
        choices=tuple(_LEVELS),
        default="info",
        help="hide findings below this level (default: info, i.e. show everything)",
    )
    parser.add_argument(
        "--fail-on",
        choices=(*_LEVELS, "never"),
        default="error",
        help="exit 1 when something at or above this level is found (default: error)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="resolve plugin ids against the installed mapchar — your own plugin "
        "presets included — instead of the snapshot that ships with this tool",
    )
    parser.add_argument(
        "--no-files",
        action="store_true",
        help="skip every check that touches the disk, for a project whose data files "
        "are not here",
    )
    parser.add_argument(
        "--select",
        metavar="CODES",
        help="only report these codes, comma-separated; a bare letter selects a level "
        "(E, W, I) and a prefix selects a family (E3, W52)",
    )
    parser.add_argument(
        "--ignore",
        metavar="CODES",
        help="suppress these codes, matched the same way as --select",
    )
    parser.add_argument(
        "--no-color", action="store_true", help="never colorize the text report"
    )
    return parser


def main(argv: list | None = None) -> int:
    args = build_parser().parse_args(argv)
    # A path that is not there is the *run* being wrong, not the project — so it
    # is answered before anything is linted, and with the status reserved for
    # that. Otherwise a typo'd filename is indistinguishable from a project full
    # of errors, which is the one thing the exit codes exist to separate.
    missing = [path for path in args.paths if not exists(path)]
    if missing:
        for path in missing:
            print(f"no such file or directory: {path}", file=sys.stderr)
        return 2
    targets = _targets(args.paths)
    if not targets:
        print("no .mapchar files found", file=sys.stderr)
        return 2

    ids = known_ids(prefer_live=args.live)
    reports = [lint(path, ids, check_files=not args.no_files) for path in targets]
    for report in reports:
        report.diagnostics = _filter(report.diagnostics, args)

    color = not args.no_color and sys.stdout.isatty()
    if args.format == "json":
        sys.stdout.write(render_json(reports))
    else:
        sys.stdout.write(render_text(reports, color=color))
        _note_id_source(reports, ids)
    return _status(reports, args.fail_on)


def _targets(paths: list) -> list:
    """Expand directories into the project files they hold.

    Sorted so a run over a directory reports in a stable order — the output is
    diffed and pasted into issues, and a filesystem's own order is neither.
    """
    out = []
    for path in paths:
        if isdir(path):
            out.extend(sorted(glob(join(path, "**", "*.mapchar"), recursive=True)))
        else:
            out.append(path)
    return out


def _filter(diagnostics: list, args) -> list:
    minimum = _LEVELS[args.min_severity]
    select = _codes(args.select)
    ignore = _codes(args.ignore)
    kept = []
    for diagnostic in diagnostics:
        if diagnostic.severity.rank > minimum.rank:
            continue
        if select and not _matches(diagnostic.code, select):
            continue
        if ignore and _matches(diagnostic.code, ignore):
            continue
        kept.append(diagnostic)
    return kept


def _codes(raw: str | None) -> tuple:
    if not raw:
        return ()
    return tuple(item.strip().upper() for item in raw.split(",") if item.strip())


def _matches(code: str, patterns: tuple) -> bool:
    # Prefix matching, so `E3` selects the whole file-reference family and `E`
    # the whole severity. Codes are allocated in families for exactly this.
    return any(code.startswith(pattern) for pattern in patterns)


def _note_id_source(reports: list, ids) -> None:
    """Say which registry answered, but only when it changed a finding.

    An unconditional footer on every clean run is noise; a footer under a report
    full of "not a built-in id" warnings is the missing half of the sentence.
    """
    if ids.authoritative or not ids.usable:
        return
    if not any(
        d.code in ("W401", "W402", "W403", "W405")
        for report in reports
        for d in report.diagnostics
    ):
        return
    sys.stdout.flush()  # the footer goes under the report, not above it
    print(
        f"\nPlugin ids were checked against the {ids.source}, which does not know "
        "your own\nplugins. Re-run with --live to check against the installed mapchar.",
        file=sys.stderr,
    )


def _status(reports: list, fail_on: str) -> int:
    if fail_on == "never":
        return 0
    threshold = _LEVELS[fail_on]
    for report in reports:
        if report.fatal:
            return 1
        if any(d.severity.rank <= threshold.rank for d in report.diagnostics):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
