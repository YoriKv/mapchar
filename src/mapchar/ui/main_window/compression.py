"""Decompression previews and the Compression picker."""

from __future__ import annotations

from mapchar.core.capabilities import Capability
from mapchar.core.context import KEY_DECOMPRESS_PARTIAL, PipelineContext
from mapchar.core.document import Document
from mapchar.pipeline.scan import (
    decompress_at,
    scheme_at,
    signature_of,
)
from mapchar.plugins.base import Stage
from mapchar.project.workspace import EntryKind
from mapchar.ui import DUMP_WINDOW_BYTES
from mapchar.ui.raw_cells import RowModel
from mapchar.ui.widgets import fill_pick, select_data

AUTOMATIC = None
"""The Compression picker's first item: arm whichever registered scheme's
signature the view has landed on. The default, and what a file that never
picked anything is on."""

NO_SCHEME = ""
"""Its second: preview nothing, whatever the bytes look like."""

MISSING = " (missing)"
"""What a scheme no plugin provides is marked with, wherever it is shown."""

NOTHING_ARMED = (
    "No scheme is armed here. Pick one on the Format bar, or go to a structure "
    "that announces itself."
)
"""What the Decompressed View says with nothing to read the bytes through."""


class CompressionMixin:
    """Decompression previews and the Compression picker.

    The walk over a whole file for structures is
    :mod:`mapchar.ui.main_window.structure_scan`; what is here is what the
    Decompressed view shows of the position the window is already at.

    The picker says what the Decompressed view reads a file through: a scheme by
    name, nothing, or — the default — whichever registered scheme announces
    itself where the view is. A pick is an instruction, so it holds wherever the
    view goes; automatic is the item that follows the bytes.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    # -- the picker -------------------------------------------------------------

    def _fill_compression_pick(self) -> None:
        """The schemes on offer: automatic, none, then every registered one."""
        fill_pick(
            self.compression_pick,
            [("automatic", AUTOMATIC), ("none", NO_SCHEME)]
            + [
                (p.info.name, p.info.id)
                for p in self.registry.plugins(Stage.COMPRESSION)
            ],
        )

    def _sync_compression_pick(self) -> None:
        """Show what the entry on screen reads its compression through.

        A block read through a scheme is already looking at the decompressed
        bytes, and what scheme that is was settled when it was made, so the
        picker states it and is disabled — changing it has a path of its own. A
        file's pick is its session's, and it is the one thing here that is a
        choice.
        """
        entry, pick = self._entry, self.compression_pick
        block = self._current_block()
        with self._bars_quiet():
            # A missing scheme is one entry's own, so the row the last one
            # needed goes before this one is shown: it is not on offer.
            for index in range(pick.count() - 1, -1, -1):
                if pick.itemText(index).endswith(MISSING):
                    pick.removeItem(index)
            if block is not None:
                self._show_scheme(block.compression_id or NO_SCHEME)
                pick.setEnabled(False)
                return
            on_file = entry is not None and entry.kind is EntryKind.FILE
            pick.setEnabled(on_file)
            self._show_scheme(entry.session.preview_scheme if on_file else AUTOMATIC)

    def _show_scheme(self, scheme: str | None) -> None:
        """Show ``scheme`` on the picker, adding a row for one no plugin
        provides.

        A scheme nothing can read is still what the entry is set to — the
        project keeps it, so a plugin that comes back arms it again — and shown
        as automatic it would claim the bytes decide while nothing decodes at
        all, with no change to make to get out of it.
        """
        pick = self.compression_pick
        if select_data(pick, scheme):
            return
        pick.addItem(f"{scheme}{MISSING}", scheme)
        pick.setCurrentIndex(pick.count() - 1)

    def _on_compression_pick(self) -> None:
        """A scheme picked on a file: arm the preview through it from here on.

        A view setting like the table beside it, so it is applied to the
        session and the views read again — no undo step, for the same reason a
        table pick on a file is none.
        """
        entry = self._entry
        if entry is None or entry.kind is not EntryKind.FILE:
            return
        entry.session.preview_scheme = self.compression_pick.currentData()
        # The row a scheme no plugin provides needed is not on offer any more.
        self._sync_compression_pick()
        self._refresh_view(moved=True)

    # -- what is armed ----------------------------------------------------------

    def _preview_offset(self) -> int:
        """Where the preview reads from: the selection's first byte, or the
        view's own position when nothing is selected.

        The selection is what a click in the raw view leaves behind, so it is
        the closest thing to a cursor there is — and a structure is picked out
        by landing on its first byte.
        """
        return self._selection[0] if self._selection is not None else self._offset

    def _arm_scheme(self, scheme_id: str | None) -> None:
        """Arm ``scheme_id`` on the file on screen, picker and all.

        What Jump to Source and a bookmark made under a scheme do: the block
        they came from names the scheme its bytes are packed with, and a preview
        armed behind the picker's back would leave the two disagreeing.
        """
        self._preview_scheme = scheme_id
        entry = self._entry
        if entry is not None and entry.kind is EntryKind.FILE:
            entry.session.preview_scheme = scheme_id
        self._sync_compression_pick()
        # Shown at once rather than at the next refresh: what armed this may
        # land on the offset the view is already at, and move nothing.
        if self._doc is not None:
            self._refresh_decompress_preview(self._doc, self._table_set())

    def _armed_schemes(self) -> list:
        """The schemes automatic arming and Find All work over: the one picked,
        or, on automatic, every registered scheme that announces itself.

        A scheme with no signature cannot be found by looking, so automatic
        never considers one: it is picked by name or not at all.
        """
        pick = self._preview_pick()
        if pick is AUTOMATIC:
            return [
                p for p in self.registry.plugins(Stage.COMPRESSION) if signature_of(p)
            ]
        plugin = pick and self.registry.plugin(Stage.COMPRESSION, pick)
        return [plugin] if plugin else []

    def _preview_pick(self) -> str | None:
        """What the picker holds for the entry on screen. Only a file arms a
        preview, so anything else reads as none."""
        entry = self._entry
        if entry is None or entry.kind is not EntryKind.FILE:
            return NO_SCHEME
        return entry.session.preview_scheme

    def _arm_automatically(self, doc: Document, offset: int):
        """On automatic, arm whichever scheme's signature sits at ``offset``,
        and hand back the structure it read there.

        Run wherever the view lands, so it compares a few bytes per scheme
        before anything is decoded (:func:`~mapchar.pipeline.scan.scheme_at`),
        and only a complete structure counts — moving off one disarms, which is
        what hides a view that armed itself. The structure comes back because
        the probe decoded it whole: the preview shows that reading rather than
        running the decoder a second time over the same bytes.
        """
        pick = self._preview_pick()
        if pick is not AUTOMATIC:
            self._preview_scheme = pick or None
            self._auto_armed = False
            return None
        found = scheme_at(doc.data, self._armed_schemes(), offset)
        self._preview_scheme = found[0].info.id if found is not None else None
        self._auto_armed = found is not None
        return found[1] if found is not None else None

    # -- the preview ------------------------------------------------------------

    def _decompress_at(self, doc: Document, offset: int, *, partial: bool = True):
        """The picked scheme's reading of the structure at ``offset``, or ``None``.

        ``partial`` by default, because this is a *preview*: a scheme that can
        hand back the prefix it managed does, since the window the view is
        looking at is its own limit, not evidence that the data is bad.
        """
        plugin = self._scheme()
        if plugin is None:
            return None
        bind = getattr(plugin, "bind_tree", None)
        if callable(bind):
            try:
                bind(doc.data)
            except Exception:  # noqa: BLE001 - a scheme that cannot bind cannot read
                return None
        return decompress_at(doc.data, plugin, offset, partial=partial)

    def _complete_structure_at(self, doc: Document, offset: int):
        """The **complete** structure at ``offset``, read strictly, or ``None``.

        What the two actions that *act* on a structure work from, rather than
        the preview's partial read. A prefix's ``consumed`` is how far the
        window reached and not where the structure ends, so stepping by it
        would land mid-stream and recording it as a block's slot length would
        hand the write-back a boundary nobody measured. The same rule as the
        structure scan's, which is why a scheme with no end to find offers
        neither action.
        """
        found = self._decompress_at(doc, offset, partial=False)
        return found if found is not None and found.complete else None

    def _nothing_decodes(self, doc: Document, offset: int) -> str:
        """Why the Decompressed View has nothing to show at ``offset``.

        The probe reports rather than raises, which is right for a walk over a
        whole file and leaves the one offset the user is looking at with nothing
        to say; the decode that already failed is asked again here for its own
        words, since it fails the same way and at the same place.
        """
        plugin = self._scheme()
        if plugin is None:
            return NOTHING_ARMED
        said = ""
        ctx = PipelineContext()
        ctx.set(KEY_DECOMPRESS_PARTIAL, True)
        try:
            plugin.decompress(doc.data[offset:], ctx)
        except ValueError as exc:  # how every scheme here refuses bytes
            said = f" — {exc}"
        except Exception:  # noqa: BLE001 - one that fails otherwise says nothing
            pass
        return f"Nothing decodes at {offset:X} through {plugin.info.name}{said}"

    def _refresh_decompress_preview(self, doc: Document, tables) -> None:
        view = self.decompress_window
        block = self._current_block()
        # A block that is *already* read through a scheme is looking at the
        # decompressed bytes, so there is nothing left to preview; anything else
        # is asked of the capability table rather than listed here.
        if not self._can(Capability.COMPRESSION_SCAN) or (
            block is not None and block.compression_id
        ):
            self.raw.set_structure(None)
            view.show_nothing("There is nothing here to read through a scheme.")
            view.hide_if_armed()
            return
        offset = self._preview_offset()
        # Whatever automatic arming decoded to arm itself; a scheme picked by
        # name is read here, as a preview, so a stream that runs past the
        # window still shows its prefix.
        found = self._arm_automatically(doc, offset) or self._decompress_at(doc, offset)
        if found is None:
            self.raw.set_structure(None)
            # Asking the scheme why costs a decode, and only a window on screen
            # has anyone to tell; one opened later is refreshed as it opens.
            said = self._nothing_decodes(doc, offset) if view.isVisible() else ""
            view.show_nothing(said)
            view.hide_if_armed()
            return
        data, consumed, complete = found.data, found.consumed, found.complete
        # Where the structure sits in the file shows in the file: only a
        # complete one, since a prefix's length is the window's, not its own.
        self.raw.set_structure((offset, offset + consumed) if complete else None)
        window = data[:DUMP_WINDOW_BYTES]
        tokens = self._decode_window(window, tables).tokens
        model = RowModel(0, window, tokens, set(), len(data))
        status = f"{consumed:,} compressed bytes at {offset:X} → {len(data):,} bytes"
        if self._auto_armed:
            # Nothing else says which scheme recognised the bytes: the picker
            # says only that it was left to them.
            status = f"{self._scheme().info.name}  ·  {status}"
        if not complete:
            status += "  ·  no end marker before the window's edge"
        view.show_result(model, tokens, status, complete)
        view.show_armed()

    def _show_decompress(self) -> None:
        """View ▸ Decompressed View…: the floating view, opened as any other
        tool window is — and kept open, whatever the bytes here decode to.

        The only way to the structure scan and Find All from a file that is not
        already sitting on a structure, which is where a search for one starts.
        """
        view = self.decompress_window
        view.keep_open()
        view.show()
        view.raise_()
        view.activateWindow()
        if self._doc is not None:
            self._refresh_decompress_preview(self._doc, self._table_set())
