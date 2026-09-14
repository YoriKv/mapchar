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

from mapchar.core.address import HEX_ID, PRESETS_BY_ID, format_hex
from mapchar.core.bits import parse_hex
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

        try:
            size = parse_hex(self.bank_size_box.text())
            base = parse_hex(self.addr_base_box.text())
            first = parse_hex(self.bank_base_box.text())
        except ValueError:
            return None
        return None if size <= 0 else BankLayout(size, base, first)

    def _format_address(self, offset: int) -> str:
        """``offset`` as the navigation bar spells it."""
        layout = self._address_layout()
        return format_hex(offset) if layout is None else layout.format(offset)

    def _parse_address(self, text: str) -> int | None:
        """Text typed into an address field as a file offset, or ``None``.

        A bank layout's own spelling is tried first and a flat hex offset second,
        so a ``$C0:8000`` and a bare ``008000`` both land somewhere sensible
        under a HiROM mapping.
        """
        layout = self._address_layout()
        if layout is not None:
            offset = layout.parse(text)
            if offset is not None:
                return offset
        try:
            return parse_hex(text)
        except ValueError:
            return None

    def _on_address_format(self) -> None:
        """The picker moved: remember it, show or hide Custom, and re-render."""
        chosen = self.address_pick.currentData()
        self.settings.setValue(ADDRESS_FORMAT_KEY, chosen)
        self.custom_bank_row.setVisible(chosen == CUSTOM_ID)
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
        self._refresh_view()

    def _move(self, delta: int) -> None:
        self._go_to(self._offset + delta)

    def _go_end(self) -> None:
        if self._doc is not None:
            self._go_to(max(0, self._doc.size - self.raw.visible_bytes()))

    def _on_offset_typed(self) -> None:
        offset = self._parse_address(self.offset_box.text())
        if offset is None:
            self.statusBar().showMessage("Not an address this format can read", 3000)
            self.offset_box.setText(self._format_address(self._offset))
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
        self._go_to(max(0, offset - BYTES_PER_ROW))
        self.raw.set_selection(offset, offset + length)
        self._on_selection(offset, offset + length)

    def _on_selection(self, start: int, end: int, from_text: bool = False) -> None:
        self._selection = (start, end) if end > start else None
        self._update_nav_status()
        self._sync_hex_panel()
        # Not back into the text view it came from: rewriting its cursor mid-drag
        # moves the drag's anchor, so a selection dragged upward never grows.
        if (
            self._selection
            and not from_text
            and self.display.currentWidget() is self.text
        ):
            self.text.select_bytes(*self._selection)
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
    # entries, and selection there is activation.
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
        if isinstance(QApplication.focusWidget(), self._ARROW_INPUT_TYPES):
            return False
        mods = event.modifiers()
        blocked = Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier
        if mods & (blocked | Qt.KeyboardModifier.ControlModifier):
            return False  # Alt+arrows are the visit trail's; Ctrl is the menus'
        if self._doc is None:
            return False
        page = self.raw.visible_bytes()
        moves = {
            Qt.Key.Key_PageUp: -page,
            Qt.Key.Key_PageDown: page,
            Qt.Key.Key_Up: -BYTES_PER_ROW,
            Qt.Key.Key_Down: BYTES_PER_ROW,
            Qt.Key.Key_Left: -1,
            Qt.Key.Key_Right: 1,
            Qt.Key.Key_Minus: -1,
            Qt.Key.Key_Plus: 1,
            Qt.Key.Key_Equal: 1,
        }
        key = event.key()
        if key in moves:
            self._move(moves[key])
            return True
        if key == Qt.Key.Key_Home:
            self._go_to(0)
            return True
        if key == Qt.Key.Key_End:
            self._go_end()
            return True
        return False
