"""Dump a project's blocks to a native script.

The script format is how mapchar's extraction is compared with abcde's over
the verification fixtures, so it stays writable from outside the app even
though the app no longer offers it: the window's Dump was the reference dump
of prior-art tools, and a project file has replaced the job it did there
([`../docs/plan/script-format.md`](../docs/plan/script-format.md)).

```
uv run python tools/dump_script.py <project.mapchar> [-o out.txt]
                                   [-b BLOCK]... [-m originals|translations|both]
```

Without ``-o`` the script goes to standard output, and without ``-b`` every
block of the project is dumped. Headless; needs the offscreen Qt platform,
which it sets itself, and drives a real window because reading a block is the
whole pipeline — containers, compression and the project's own tables — and
not a thing this script should assemble a second time.
"""

from __future__ import annotations

import argparse
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(argv: list[str] | None = None) -> int:
    from PySide6.QtWidgets import QApplication

    from mapchar.core.capabilities import EntryKind
    from mapchar.project.formats.script import DumpMode, write_script
    from mapchar.ui.main_window import MainWindow

    parser = argparse.ArgumentParser(description="Dump a project's blocks.")
    parser.add_argument("project", help="the .mapchar project to dump")
    parser.add_argument("-o", "--out", help="where to write it (default: stdout)")
    parser.add_argument(
        "-b",
        "--block",
        action="append",
        default=[],
        help="a block to dump, repeatable (default: every block)",
    )
    parser.add_argument(
        "-m",
        "--mode",
        choices=[m.value for m in DumpMode],
        default=DumpMode.TRANSLATIONS.value,
        help="what the content lines hold (default: translations)",
    )
    args = parser.parse_args(argv)

    # Qt needs one alive, and the test that drives this already has one.
    app = QApplication.instance() or QApplication(sys.argv[:1])  # noqa: F841
    window = MainWindow()
    if not window.open_project(args.project):
        print(f"{args.project}: could not be opened", file=sys.stderr)
        return 1

    wanted = set(args.block)
    blocks = [
        e
        for e in window.workspace.of_kind(EntryKind.BLOCK)
        if not wanted or e.name in wanted
    ]
    missing = wanted - {e.name for e in blocks}
    for name in sorted(missing):
        print(f"{name}: no such block", file=sys.stderr)
    if not blocks:
        print("nothing to dump", file=sys.stderr)
        return 1

    out_dir = os.path.dirname(os.path.abspath(args.out)) if args.out else os.getcwd()
    payload = []
    for block in blocks:
        strings = window._block_strings(block)
        if strings is None:
            print(f"{block.name}: could not be read", file=sys.stderr)
            continue
        payload.append((block.name, block.config, strings))
    file_entry = blocks[0].parent
    text = write_script(
        payload,
        DumpMode(args.mode),
        rom=os.path.relpath(file_entry.path, out_dir)
        if file_entry is not None and file_entry.path
        else None,
        tables=[
            os.path.relpath(e.path, out_dir)
            for e in window.workspace.table_entries()
            if e.path
        ],
    )
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        print(f"{sum(len(p[2]) for p in payload)} strings to {args.out}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(ROOT, "src"))
    raise SystemExit(main())
