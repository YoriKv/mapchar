"""Where the view sits in the file, how that position is spelled, and the keys
that move it.

Three things that are one concern: the offset the view starts at, the *address
format* it is written in (a flat file offset, a console mapping preset, or bank
numbers typed by hand — :mod:`mapchar.core.address`), and the routing that lets
a key move the view from wherever the focus happens to be.

The keys go through an **application-wide event filter** rather than through
``QShortcut`` or ``keyPressEvent``, as celPix does. A shortcut on the window is
pre-empted by a focused combo box or list, and ``keyPressEvent`` on the window
is never reached at all while the focus is inside one — which is most of the
time, since picking a table or clicking a row puts it there. The filter sees the
press first and yields where it must: to an open popup, and to the inputs that
spend the arrow keys themselves (:data:`_ARROW_INPUT_TYPES`). Alt and Meta are
declined outright, which is what leaves Alt+Left/Right to the visit trail's real
shortcuts (:mod:`mapchar.ui.main_window.history`).
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSlider,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QInputDialog,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
)

from mapchar.core.address import (
    HEX_ID,
    PRESETS_BY_ID,
)
from mapchar.core.capabilities import Capability
from mapchar.project.workspace import Entry
from mapchar.ui import BYTES_PER_ROW
from mapchar.ui.undo_commands import OffsetCommand

ADDRESS_FORMAT_KEY = "view/address_format"
"""QSettings key for the chosen address format — per machine, never the project.

How you *read* a position says how you are working right now, and the same
project opened beside a different document should not drag one file's mapping
onto another.
"""

CUSTOM_ID = "custom"
"""The address-format id that means "the three fields beside the picker"."""


_STEP_KEYS = frozenset(
    {
        Qt.Key.Key_PageUp,
        Qt.Key.Key_PageDown,
        Qt.Key.Key_Up,
        Qt.Key.Key_Down,
        Qt.Key.Key_Left,
        Qt.Key.Key_Right,
        Qt.Key.Key_Minus,
        Qt.Key.Key_Plus,
        Qt.Key.Key_Equal,
        Qt.Key.Key_Home,
        Qt.Key.Key_End,
    }
)
"""Every key that steps the view: what the step buttons do, on the keyboard."""


class NavigationMixin:
    """The view position, its spelling, and the keys that move it.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    # -- the address format --------------------------------------------------
    def _address_layout(self):
        """The bank layout positions are written under, or ``None`` for flat hex."""
        chosen = self.address_pick.currentData()
        if chosen == CUSTOM_ID:
            return self._custom_layout()
        preset = PRESETS_BY_ID.get(chosen)
        return None if preset is None else preset.layout

    def _custom_layout(self):
        """The Custom row's three numbers as a layout, or ``None`` while unusable.

        A half-typed bank size is not an error to report — the user is still
        typing it — so it simply reads as flat hex until it makes sense.
        """
        from mapchar.core.address import BankLayout

        size = self.bank_size_box.value() or 0
        base = self.addr_base_box.value() or 0
        first = self.bank_base_box.value() or 0
        return None if size <= 0 else BankLayout(size, base, first)

    def _format_address(self, offset: int) -> str:
        """``offset`` as every address field spells it."""
        return self.address_spelling.format(offset)

    def _parse_address(self, text: str) -> int | None:
        """Text typed into an address field as a file offset, or ``None``.

        A bank layout's own spelling is tried first and a flat hex offset second
        (:func:`~mapchar.core.address.parse_address`).
        """
        return self.address_spelling.parse(text)

    def _sync_address_spelling(self) -> None:
        """Spell every address field — the Reading bar's, the Hex panel's, the
        offset box — under the format the picker and Custom fields make."""
        self.address_spelling.set_layout(self._address_layout())

    def _on_address_format(self) -> None:
        """The picker moved: remember it, show or hide Custom, and re-render."""
        chosen = self.address_pick.currentData()
        self.settings.setValue(ADDRESS_FORMAT_KEY, chosen)
        self.custom_bank_row.setVisible(chosen == CUSTOM_ID)
        self._sync_address_spelling()
        self._refresh_view()

    def _on_custom_bank(self) -> None:
        """A Custom bank field was finished: re-spell and re-render."""
        self._sync_address_spelling()
        self._refresh_view()

    def _restore_address_format(self) -> None:
        """Put the remembered format back on the picker, defaulting to flat hex.

        With the picker's signals blocked: this runs while the window is still
        building its widgets, and letting it fire :meth:`_on_address_format`
        would refresh — and gate — a window whose menus do not exist yet. The
        row is shown or hidden here instead, and ``__init__`` refreshes once at
        the end anyway.
        """
        want = str(self.settings.value(ADDRESS_FORMAT_KEY, HEX_ID))
        index = self.address_pick.findData(want)
        self.address_pick.blockSignals(True)
        try:
            self.address_pick.setCurrentIndex(max(index, 0))
        finally:
            self.address_pick.blockSignals(False)
        self.custom_bank_row.setVisible(self.address_pick.currentData() == CUSTOM_ID)
        self._sync_address_spelling()

    # -- the view's bounds ----------------------------------------------------
    def _view_range(self) -> tuple[int, int]:
        """The bytes the view moves over: its bounds, else the whole document."""
        if self._bounds is not None:
            return self._bounds
        return (0, self._doc.size if self._doc is not None else 0)

    def _view_end(self) -> int:
        """The exclusive end of what the view may show."""
        return self._view_range()[1]

    def _set_bounds(self, bounds: tuple[int, int] | None) -> None:
        """Confine the Hex and Text tabs to ``bounds`` — a block's source, one
        string's bytes — or, with ``None``, show the whole document again.

        The position moves to the bounds' start unless it is already inside
        them: what is being asked for is that stretch, and a view left pointing
        elsewhere would widen itself straight back (:meth:`_refresh_view`).
        """
        doc = self._doc
        if doc is None:
            return
        if bounds is not None:
            start, end = max(0, bounds[0]), min(bounds[1], doc.size)
            bounds = (start, end) if end > start else None
        inside = bounds is None or bounds[0] <= self._offset < bounds[1]
        if bounds == self._bounds and inside:
            return
        self._bounds = bounds
        self._sync_view_mode()
        if inside:
            self._refresh_view(moved=True)
        else:
            self._go_to(bounds[0])

    def _clamped(self, offset: int) -> int:
        """``offset`` held inside the view's bounds — what a step does, so
        walking off the end of a string cannot widen the view to the file."""
        start, end = self._view_range()
        return max(start, min(offset, max(end - 1, start)))

    def _view_fits(self) -> bool:
        """Whether the open tab shows the view's whole range from its start —
        a string in one row, a file smaller than the window — so a step has
        nowhere to go but out of sight of bytes that fit."""
        start, end = self._view_range()
        return self._offset <= start and self._offset + self._view_bytes() >= end

    def _sync_steps(self) -> None:
        """Enable the step buttons only where a step can show something: an
        entry that can be navigated, with more in range than the open tab
        holds. Neither tab has a scrollbar to say the rest is cut off, so a
        step that only hides bytes is switched off rather than clamped."""
        enabled = self._can(Capability.NAVIGATION) and not self._view_fits()
        for button in self.step_buttons:
            button.setEnabled(enabled)

    # -- moving ---------------------------------------------------------------
    def _go_to(self, offset: int) -> None:
        if self._doc is None:
            return
        offset = max(0, min(offset, max(self._doc.size - 1, 0)))
        if offset == self._offset:
            return
        if not self._applying_undo and self._entry is not None:
            self._push_command(OffsetCommand(self, self._entry, self._offset, offset))
        else:
            self.apply_offset(self._entry, offset)

    def apply_offset(self, entry: Entry | None, offset: int) -> None:
        if entry is not self._entry and entry is not None:
            self._activate_entry(entry)
        self._offset = offset
        self._refresh_view(moved=True, live=self._dragging())

    def _dragging(self) -> bool:
        """Whether the view is moving under the mouse: the scrollbar of the tab
        on screen has its handle held. More moves are coming, so a refresh can
        leave everything but that tab until they stop."""
        if self.tabs.currentWidget() is self.text:
            return self.text.bar.isSliderDown()
        return self.raw.verticalScrollBar().isSliderDown()

    def _move(self, delta: int) -> None:
        self._go_to(self._clamped(self._offset + delta))

    def _go_home(self) -> None:
        """Home: the start of the file, or of the view's bounds."""
        self._go_to(self._view_range()[0])

    def _step_rows(self, rows: int) -> None:
        """Move the view by rows: the Hex tab's, or the Text tab's lines."""
        if self.tabs.currentWidget() is self.text:
            self._on_text_scroll(rows)
        else:
            self._move(rows * BYTES_PER_ROW)

    def _step_pages(self, pages: int) -> None:
        """Move the view by pages: the Hex tab's rows shown, or as many of the
        Text tab's lines as its box holds — down, exactly what was shown."""
        if self.tabs.currentWidget() is self.text:
            self._on_text_scroll(pages * self.text.lines_in_view())
        else:
            self._move(pages * self._view_bytes())

    def _view_bytes(self) -> int:
        """How many bytes the open tab shows: the Text tab's window, sized to
        its box, or the raw view's rows. What a page step moves by."""
        if self.tabs.currentWidget() is self.text and self.text.shown_bytes():
            return self.text.shown_bytes()
        return self.raw.visible_bytes()

    def _go_end(self) -> None:
        """End: the last window of the file, or of the view's bounds."""
        if self._doc is not None:
            self._go_to(self._clamped(self._view_end() - self._view_bytes()))

    def _on_offset_typed(self) -> None:
        offset = self._parse_address(self.offset_box.text())
        if offset is None:
            self.statusBar().showMessage("Not an address this format can read", 3000)
            self.offset_box.set_value(self._offset)
            return
        self._go_to(offset)

    def _go_to_dialog(self) -> None:
        layout = self._address_layout()
        prompt = "Offset (hex):" if layout is None else "Address (bank:addr or hex):"
        text, ok = QInputDialog.getText(
            self, "Go to Address", prompt, text=self._format_address(self._offset)
        )
        if not ok:
            return
        offset = self._parse_address(text)
        if offset is None:
            self.statusBar().showMessage("Not an address this format can read", 3000)
            return
        self._go_to(offset)

    def _select_bytes(self, offset: int, length: int) -> None:
        if self._doc is None:
            return
        self._go_to(offset)
        self.raw.set_selection(offset, offset + length)
        self._on_selection(offset, offset + length)

    def _on_selection(self, start: int, end: int, from_text: bool = False) -> None:
        self._selection = (start, end) if end > start else None
        self._update_nav_status()
        self._sync_hex_panel()
        # A selection is the closest thing the byte views have to a cursor, and
        # the decompression preview reads from where it starts
        # (:mod:`mapchar.ui.main_window.compression`).
        if self._doc is not None:
            self._refresh_decompress_preview(self._doc, self._table_set())
        # Not back into the text view it came from: rewriting its cursor mid-drag
        # moves the drag's anchor, so a selection dragged upward never grows.
        if not from_text and self.tabs.currentWidget() is self.text:
            self.text.select_bytes(*(self._selection or (-1, -1)))
        if self._selection and self._doc is not None:
            s, e = self._selection
            for rec in self._doc.strings:
                if rec.start <= s < rec.end:
                    self.strings.select_index(rec.index)
                    break

    # -- key and mouse routing -----------------------------------------------
    # Inputs that spend the arrow keys themselves; while one has focus the
    # navigation keys are left alone so it can cycle its options, move its
    # cursor or walk its rows. The Files tree is one: its arrows walk the open
    # entries, and selection there is activation. The Text tab's box is not:
    # read-only, it has no cursor to move.
    _ARROW_INPUT_TYPES = (
        QComboBox,
        QAbstractSpinBox,
        QAbstractSlider,
        QAbstractItemView,
        QLineEdit,
        QTextEdit,
        QPlainTextEdit,
    )

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt override
        """Installed on the ``QApplication`` so navigation works wherever focus is.

        Only while this window is active, so a tool window's own keys stay its
        own; :meth:`_handle_nav_key` defers to popups and text inputs.
        """
        kind = event.type()
        if (
            kind
            in (
                QEvent.Type.MouseButtonPress,
                QEvent.Type.MouseButtonDblClick,
                QEvent.Type.MouseButtonRelease,
            )
            and self.isActiveWindow()
            and self._handle_history_mouse(event)
        ):
            return True
        if (
            kind == QEvent.Type.KeyPress
            and self.isActiveWindow()
            and self._handle_nav_key(event)
        ):
            return True
        return super().eventFilter(obj, event)

    def _handle_nav_key(self, event) -> bool:
        """Move the view for one key press; True when the key is consumed."""
        if self._scanning:
            return True  # a running scan owns the view position; swallow keys
        if QApplication.activePopupWidget() is not None:
            return False
        focus = QApplication.focusWidget()
        if isinstance(focus, self._ARROW_INPUT_TYPES) and focus is not self.text.edit:
            return False
        mods = event.modifiers()
        blocked = Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier
        if mods & (blocked | Qt.KeyboardModifier.ControlModifier):
            return False  # Alt+arrows are the visit trail's; Ctrl is the menus'
        if self._doc is None:
            return False
        key = event.key()
        if self._view_fits():
            # Swallowed, as the buttons are disabled: a step that reaches the
            # focused widget instead would move its own selection.
            return key in _STEP_KEYS
        steps = {
            Qt.Key.Key_PageUp: (self._step_pages, -1),
            Qt.Key.Key_PageDown: (self._step_pages, 1),
            Qt.Key.Key_Up: (self._step_rows, -1),
            Qt.Key.Key_Down: (self._step_rows, 1),
        }
        if key in steps:
            step, direction = steps[key]
            step(direction)
            return True
        moves = {
            Qt.Key.Key_Left: -1,
            Qt.Key.Key_Right: 1,
            Qt.Key.Key_Minus: -1,
            Qt.Key.Key_Plus: 1,
            Qt.Key.Key_Equal: 1,
        }
        if key in moves:
            self._move(moves[key])
            return True
        if key == Qt.Key.Key_Home:
            self._go_home()
            return True
        if key == Qt.Key.Key_End:
            self._go_end()
            return True
        return False
