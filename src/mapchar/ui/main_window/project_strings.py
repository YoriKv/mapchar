"""The Project Strings window: every block's strings, and jumping to one."""

from __future__ import annotations

from mapchar.project.entry import Entry
from mapchar.ui.project_strings_window import ProjectString
from mapchar.ui.strings_view import UNWRITTEN


class ProjectStringsMixin:
    """The Project Strings window: every block's strings, and jumping to one.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _show_project_strings(self) -> None:
        window = self.project_strings
        if window.isVisible():
            self._refresh_project_strings()
        window.show()
        window.raise_()
        window.activateWindow()

    def _all_block_strings(self) -> list[tuple[Entry, object]]:
        """Every string of every block that can be read, with its block: the
        blocks not yet open are loaded and read for it."""
        return [
            (block, rec)
            for block, doc in self._readable_blocks()
            for rec in doc.strings
        ]

    def _refresh_project_strings(self) -> None:
        """Fill the window from every block, when it is there to see."""
        if not self.project_strings.isVisible():
            return
        rows = []
        for block, rec in self._all_block_strings():
            status = rec.status.value
            if rec.unwritten is not None:
                status = UNWRITTEN
            rows.append(
                ProjectString(
                    block,
                    block.name,
                    rec.index,
                    rec.original,
                    rec.shown_text(),
                    status,
                    rec.notes,
                    self._missing_terms(rec),
                )
            )
        self.project_strings.set_strings(rows)

    def _jump_to_string(self, entry: Entry, index: int) -> None:
        """Open the block on its Strings view with the string selected."""
        self._activate_entry(entry)
        self._show_view("strings")
        self.strings.select_index(index)
        self._on_string_row(index)
        self.activateWindow()
