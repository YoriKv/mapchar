"""Fixtures for the linter's tests.

The tests write real project files to a temp directory rather than feeding
dicts to the checks, because half of what is being tested is the reading —
path resolution, JSON tolerance, the file-size arithmetic, the table files a
block's table id comes from. A check that passes on a hand-built dict and fails
on a file it had to parse has not been tested.
"""

from __future__ import annotations

import json

import pytest

from mapchar_lint.known import KnownIds
from mapchar_lint.linter import lint

ROM = {"rom.nes": 0x10000}
TABLE = "@mapchar table 1\n@table main\n41=A\n/00=[end]\n"


@pytest.fixture
def ids() -> KnownIds:
    """A small stand-in registry, so the tests do not move when mapchar ships a
    new plugin. The real snapshot is checked by mapchar's own suite."""
    return KnownIds(
        plugins={
            "containers": {"raw", "ines", "gb"},
            "compression": {"lz2", "rle1"},
            "charsets": {"none", "ascii", "shift-jis"},
            "mappings": {"linear", "gb", "lorom"},
        },
        mapping_sizes={"linear": [1, 2, 3, 4], "gb": [2, 3], "lorom": [2, 3]},
        renamed={"old-lz": "lz2"},
        dialects=("native", "abcde", "cartographer", "atlas", "romjuice"),
        project_version=2,
        source="test registry",
        authoritative=True,
    )


@pytest.fixture
def project(tmp_path, ids):
    """Write a project and lint it — returns the codes it produced.

    ``write(document, files={"rom.nes": 4096, "main.tbl": "text"})`` creates the
    named files beside the project: a number is a file of that many bytes, a
    string is its text.
    """

    def write(document: dict, files: dict | None = None, **kwargs) -> list:
        return [d.code for d in report(document, files, **kwargs).diagnostics]

    def report(document: dict, files: dict | None = None, **kwargs):
        for name, body in (files or {}).items():
            target = tmp_path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(body, int):
                target.write_bytes(b"\x00" * body)
            else:
                target.write_text(body, encoding="utf-8")
        path = tmp_path / "test.mapchar"
        path.write_text(json.dumps(document), encoding="utf-8")
        return lint(str(path), kwargs.pop("known", ids), **kwargs)

    write.report = report
    return write


@pytest.fixture
def doc():
    """A clean project: a ROM, a table, and a block reading through it — the
    tests bend one key at a time. ``doc(block=..., file=..., table=...)``
    updates those records; ``extra=[...]`` appends entries."""

    def build(block=None, file=None, table=None, extra=(), **top) -> dict:
        entries = [
            {"kind": "file", "name": "rom.nes", "path": "rom.nes"},
            {
                "kind": "block",
                "name": "Dialogue",
                "path": "rom.nes",
                "parent": 0,
                "config": "source=pointers start=$100 stop=$120 size=2 stride=2 "
                "endian=little mapping=linear offset=0 bank=0 type=end table=main",
            },
            {"kind": "table", "name": "main.tbl", "path": "main.tbl"},
        ]
        entries[0].update(file or {})
        entries[1].update(block or {})
        entries[2].update(table or {})
        for record in entries:
            for key in [k for k, v in record.items() if v is None]:
                del record[key]
        return {"version": 2, "current": 1, "entries": entries + list(extra), **top}

    return build


@pytest.fixture
def files():
    return {**ROM, "main.tbl": TABLE}
