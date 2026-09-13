"""A file entry's container chain, and what it says about the file."""

from __future__ import annotations

import os

from mapchar.core.notices import notice_lines
from mapchar.pipeline.inspection import inspect_container
from mapchar.pipeline.pipeline import FileRef, PathwayConfig
from mapchar.plugins.base import Stage
from mapchar.project.workspace import Entry, EntryKind, retarget_files
from mapchar.ui.dialogs import ContainerDialog, TextDialog
from mapchar.ui.undo_commands import ContainerCommand
from mapchar.ui.widgets import select_data


class ContainerMixin:
    """A file entry's container chain, and what it says about the file.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _edit_container(self, entry: Entry | None = None) -> None:
        """Edit File Container… (Ctrl+E): re-point a file entry as one undo step.

        The container is otherwise fixed at open, detected from a signature that
        is a guess; this is where a wrong guess is overruled and where split ROM
        chips are joined in a stated order.
        """
        # A menu action hands its slot the triggered flag; only a real entry
        # names a row.
        if not isinstance(entry, Entry):
            entry = self._current_file()
        if entry is None or entry.kind is not EntryKind.FILE:
            self._error("Select a ROM to edit its container.")
            return
        from mapchar.plugins.base import writes_back

        readonly = frozenset(
            p.info.id
            for p in self.registry.plugins(Stage.CONTAINER)
            if not writes_back(p, Stage.CONTAINER)
        )
        dialog = ContainerDialog(
            self._plugin_items(Stage.CONTAINER),
            entry.paths,
            entry.container_id,
            self._detected_container(entry),
            readonly,
            self,
        )
        if dialog.exec() != ContainerDialog.DialogCode.Accepted:
            return
        after = (dialog.container_id(), dialog.paths())
        before = (entry.container_id, entry.paths)
        if after == before:
            return
        # One file, one entry: the first path is the row's identity, so pointing
        # it at a file another row already holds would leave two entries claiming
        # the same file and a block unable to say which one is its parent.
        clash = self.workspace.find_file(after[1][0]) if after[1] else None
        if clash is not None and clash is not entry:
            self._error(
                f"{os.path.basename(after[1][0])} is already open in this "
                f"project as {clash.name}."
            )
            return
        if entry.dirty and not self._ask(
            "Edit Container",
            f"{entry.name} has unsaved edits. Applying this re-reads the file "
            "and discards them. Continue?",
        ):
            return
        self._push_command(ContainerCommand(self, entry, before, after))

    def _detected_container(self, entry: Entry) -> str | None:
        """What a signature read of the file's first chip says it is wrapped in."""
        if not entry.path:
            return None
        try:
            with open(entry.path, "rb") as f:
                head = f.read(1 << 16)
        except OSError:
            return None
        plugin = self.registry.detect_container(head, entry.path)
        return plugin.info.id if plugin else None

    def apply_container(
        self, entry: Entry, container_id: str, paths: tuple[str, ...]
    ) -> None:
        """Re-point a file entry and read it again — a container edit and its undo.

        The file list moves through
        :func:`~mapchar.project.workspace.retarget_files`, so every block and
        bookmark under it is re-pointed with it — their offsets are counted
        against the join, so a change to the file list changes what they address
        — and a row still named after its first file follows the new one.
        """
        entry.container_id = container_id
        for moved in retarget_files(self.workspace, entry, paths):
            self.workspace.drop_document(moved)
        if entry is self._entry or (
            self._entry is not None and self._entry.parent is entry
        ):
            self._doc = self._load_document(self._entry)
        select_data(self.container_pick, container_id)
        self.files_panel.refresh_labels()
        self._update_title()
        self._refresh_view()

    def _container_info(self, entry: Entry) -> None:
        """What the container made of the file, from a read of its own.

        The reading is :func:`~mapchar.pipeline.inspection.inspect_container`'s,
        which runs the container stage alone on a context nothing else has
        touched; all that is left here is laying the report out.
        """
        report = inspect_container(
            PathwayConfig(FileRef(entry.paths), entry.container_id), self.registry
        )
        lines = [f"Container: {report.container_name} ({report.container_id})"]
        if report.error:
            lines.append(f"read failed: {report.error}")
        lines.append(f"source: {report.source_size:,} bytes")
        lines.append(
            f"payload: {report.payload_size:,} bytes at ${report.payload_offset:X}"
        )
        for row in report.fields:
            # A described field's detail is what the container *did* with the
            # value — the part a hex editor cannot give, and the reason to open
            # this rather than look at the bytes — so it is laid out under the
            # row rather than dropped. A hint's detail only names its context
            # key, which the row already is.
            lines.append(f"{row.name}: {row.value}")
            lines.extend(f"    {line}" for line in row.detail.splitlines())
        for row in report.hints:
            lines.append(f"{row.name}: {row.value}")
        for notice in report.notices:
            lines.extend(notice_lines(notice, "notice: "))
        TextDialog(f"Container Info — {entry.name}", "\n".join(lines), self).exec()
