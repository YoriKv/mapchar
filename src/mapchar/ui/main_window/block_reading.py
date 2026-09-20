"""Reading the project's blocks: one of them, or every one that can be read."""

from __future__ import annotations

from collections.abc import Iterator

from mapchar.core.document import Document
from mapchar.project.entry import Entry, EntryKind
from mapchar.project.missing_files import missing_paths


class BlockReadingMixin:
    """Reading the project's blocks: one of them, or every one that can be read.

    Every surface that goes over the whole project — Find and Replace, the
    Project Strings window, an import, a search for identical originals, the
    Files panel's counts — needs each block's strings before it can say
    anything, and a block the session has not opened has none. They all read
    them the same way, through :meth:`_readable_blocks`.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _read_block(self, entry: Entry) -> Document | None:
        """The block's document, its strings extracted afresh under its own
        table set; ``None`` when the block cannot be read at all."""
        doc = self._load_document(entry)
        if doc is None or entry.config is None:
            return None
        self._extract_current(entry, doc, self._table_set_of(entry))
        return doc

    def _readable_blocks(
        self,
        *,
        of_file: Entry | None = None,
        besides: Entry | None = None,
        skip_missing: bool = False,
    ) -> Iterator[tuple[Entry, Document]]:
        """Every block that can be read, with its document, read as it goes.

        The Files panel's labels are held for the whole walk: reading a
        project's worth of blocks would otherwise redraw the tree once per
        block. The walk is a generator, so a caller that stops early stops the
        reading too — every one of them takes all of it.

        ``of_file`` keeps to the blocks over one file. ``besides`` leaves out
        the one block the caller already has. ``skip_missing`` is for the read
        after a project load: a block over a file that is not on disk is left
        for Locate, a block already read is left alone, and a file that fails
        to read is tried once rather than once per block over it.
        """
        gone = set(missing_paths(self.workspace)) if skip_missing else set()
        failed: set[int] = set()
        with self.files_panel.labels_held():
            for entry in self.workspace.of_kind(EntryKind.BLOCK):
                if entry.config is None or entry is besides:
                    continue
                if of_file is not None and entry.parent is not of_file:
                    continue
                if skip_missing:
                    parent = entry.parent
                    if (
                        entry.doc is not None
                        or entry.missing
                        or parent is None
                        or id(parent) in failed
                        or any(p in gone for p in parent.paths)
                    ):
                        continue
                    if self._load_document(parent) is None:
                        failed.add(id(parent))
                        continue
                doc = self._read_block(entry)
                if doc is not None:
                    yield entry, doc
