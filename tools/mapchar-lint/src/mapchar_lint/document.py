"""Reading the project file, and resolving the paths inside it.

Path handling mirrors ``mapchar.project.projectfile`` exactly, and it has to: a
project written on Windows is opened under WSL from the same checkout, so a
linter that resolved case-sensitively would report every reference in it as
missing. The walk below is mapchar's own ``_abs`` / ``_recover_case``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from os import listdir, stat
from os.path import abspath, dirname, exists, isabs, isdir, join, normcase, normpath
from os.path import split as split_path

from mapchar_lint.diagnostics import Diagnostic, Severity


@dataclass
class ProjectDocument:
    """One parsed ``.mapchar``, plus what its paths resolve to on this machine."""

    path: str
    base_dir: str
    data: dict
    #: Whether the file opened with a byte-order mark, which the reader skips
    #: and the writer never puts back.
    bom: bool = False
    _resolved: dict = field(default_factory=dict, repr=False)
    _sizes: dict = field(default_factory=dict, repr=False)
    _texts: dict = field(default_factory=dict, repr=False)

    @property
    def entries(self) -> list:
        raw = self.data.get("entries")
        return raw if isinstance(raw, list) else []

    def resolve(self, stored: str) -> str:
        """A stored path as mapchar resolves it, case differences tolerated.

        A path that resolves nowhere comes back as its literal self, which is
        what the entry would carry — and what the missing-file check reports.
        """
        if stored not in self._resolved:
            self._resolved[stored] = _resolve_path(stored, self.base_dir)
        return self._resolved[stored]

    def size_of(self, stored: str) -> int | None:
        """The referenced file's size in bytes, or None if it is not a file."""
        resolved = self.resolve(stored)
        if resolved not in self._sizes:
            try:
                info = stat(resolved)
            except OSError:
                self._sizes[resolved] = None
            else:
                self._sizes[resolved] = None if isdir(resolved) else info.st_size
        return self._sizes[resolved]

    def text_of(self, stored: str) -> str | None:
        """A referenced text file's contents, or None when it does not read.

        UTF-8 with or without a mark, then ``cp932``, then ``latin-1``: the
        order mapchar reads a table file in, so a Shift-JIS legacy table is
        not reported as unreadable.
        """
        resolved = self.resolve(stored)
        if resolved not in self._texts:
            try:
                with open(resolved, "rb") as handle:
                    raw = handle.read()
            except OSError:
                self._texts[resolved] = None
            else:
                for codec in ("utf-8-sig", "cp932", "latin-1"):
                    try:
                        self._texts[resolved] = raw.decode(codec)
                        break
                    except UnicodeDecodeError:
                        continue
        return self._texts[resolved]

    def exists(self, stored: str) -> bool:
        return exists(self.resolve(stored))

    def is_dir(self, stored: str) -> bool:
        return isdir(self.resolve(stored))

    def identity(self, stored: str) -> str:
        """The key two entries referencing the same file agree on."""
        return normcase(self.resolve(stored))


def load(path: str) -> tuple[ProjectDocument | None, list[Diagnostic]]:
    """Parse ``path``, or return the one fatal diagnostic that stopped it.

    These are the only failures mapchar itself refuses a project on — every
    other problem drops or degrades one entry — so they are the only ones that
    end the run for a file.
    """
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        return None, [
            Diagnostic(
                "F001",
                Severity.ERROR,
                f"cannot read the file: {exc.strerror or exc}",
                detail="mapchar would refuse to open this project.",
            )
        ]
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        return None, [
            Diagnostic(
                "F002",
                Severity.ERROR,
                f"not valid UTF-8: {exc}",
                detail="A project file is a UTF-8 JSON document.",
            )
        ]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, [
            Diagnostic(
                "F002",
                Severity.ERROR,
                f"not valid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno})",
                detail="mapchar would refuse to open this project.",
            )
        ]
    if not isinstance(data, dict):
        return None, [
            Diagnostic(
                "F003",
                Severity.ERROR,
                f"the document is a JSON {type(data).__name__}, not an object",
                detail="A project is a single JSON object with an `entries` array.",
            )
        ]
    if "entries" in data and not isinstance(data["entries"], list):
        return None, [
            Diagnostic(
                "F004",
                Severity.ERROR,
                "`entries` is not an array",
                pointer="/entries",
                detail="mapchar would refuse to open this project.",
            )
        ]
    return (
        ProjectDocument(
            path=path,
            base_dir=dirname(abspath(path)),
            data=data,
            bom=raw.startswith(b"\xef\xbb\xbf"),
        ),
        [],
    )


# -- mapchar's own path resolution (projectfile._abs / _recover_case) -------
def _resolve_path(stored: str, base_dir: str) -> str:
    path = stored if isabs(stored) else normpath(join(base_dir, stored))
    return path if exists(path) else _recover_case(path)


def _recover_case(path: str) -> str:
    # Walk up to the deepest existing ancestor, then re-descend matching each
    # missing segment case-insensitively against the real directory listing.
    head, missing = path, []
    while head and not exists(head):
        head, tail = split_path(head)
        if not tail:
            return path
        missing.append(tail)
    for segment in reversed(missing):
        try:
            names = listdir(head or ".")
        except OSError:
            return path
        match = next((n for n in names if n.lower() == segment.lower()), None)
        if match is None:
            return path
        head = join(head, match)
    return head
