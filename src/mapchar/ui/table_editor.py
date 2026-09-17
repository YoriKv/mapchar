"""The Table Editor: a table's entries in a grid, one of them in a form.

The window edits one table entry's table in place: the grid shows every
entry as key, kind, text, what it does and its weight, and — dimmed, naming
the table they come from — the entries the tables it includes give it; the
form under it (:mod:`mapchar.ui.table_entry_form`) edits the selected one, or
a new one,
with a picker for everything but the text. Every change is handed to the
window with the table as it was, which makes it one undo step
(:class:`~mapchar.ui.undo_commands.TableCommand`).
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mapchar.core.bits import format_key
from mapchar.core.errors import TableError
from mapchar.core.table import ID_PATTERN, Table, TokenKind
from mapchar.core.table import Entry as TableEntry
from mapchar.core.text import nfc
from mapchar.core.textmatch import matches_words, words_of
from mapchar.engines.relsearch import entries_from_base
from mapchar.project.formats.table_native import format_entry, parse_entry
from mapchar.project.workspace import Entry
from mapchar.ui import as_bool, set_setting_bool, setting_bool, settings
from mapchar.ui.table_dialogs import FillDialog, ShiftKeysDialog
from mapchar.ui.table_entry_form import KIND_NAMES, TableEntryForm, describe
from mapchar.ui.widgets import (
    CompactComboBox,
    ElidedLabel,
    EscapeCloses,
    WrapBar,
    fill_pick,
    fit_chars,
    hint_field,
    install_column_menu,
    select_data,
    show_elided_tooltips,
)
from mapchar.ui.window_layout import remember_layout, stored_bytes

LINE_SHOWN_KEY = "table_editor/line_shown"
"""Whether the form shows the line code, remembered per machine."""
COLUMNS_KEY = "table_editor/columns"
"""Which columns the grid shows, remembered per machine."""
SPLITTER_KEY = "table_editor/splitter"
"""Where the grid and the form under it are split, remembered per machine."""

KEY, KIND, TEXT, DETAILS, WEIGHT, COMMENT = range(6)
"""The grid's columns."""
HEADERS = ("Key", "Kind", "Text", "Details", "Weight", "Comment")

Speller = Callable[[int], str]
"""How the window spells a file offset."""
Inheritance = Callable[[Table], tuple[dict[str, tuple[TableEntry, str]], str]]
"""What the window answers for a table's includes: per key, the entry they give
it and the table that entry is own to; and what is wrong with the merged table
(an include not loaded, a cycle, a label twice), or nothing."""
ORIGIN_ROLE = Qt.ItemDataRole.UserRole + 1
"""On a Key cell: the id of the table an inherited row comes from, else ``None``."""


class TableEditor(EscapeCloses, QWidget):
    changed = Signal(object, object)
    """The table entry whose table changed, and its table as it was.

    The window turns the pair into one undo step; the editor itself mutates
    the table in place.
    """
    save_requested = Signal(object, bool)
    """Save, or Save As File… (``True``) asking for the path."""
    table_requested = Signal(object)
    """The Table picker chose another table entry to edit."""
    charset_chosen = Signal(object, str)
    """The Charset picker put the table entry's table on a charset."""
    rename_requested = Signal(object)
    """Rename Table… on the table entry being edited."""
    includes_chosen = Signal(object, tuple)
    """The Includes field gave the table entry's table these ``@include`` ids."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("Table Editor")
        # Size and position remembered between runs, like every tool
        # window (:mod:`mapchar.ui.window_layout`); the size set first is the
        # one a machine with nothing stored opens at.
        self.resize(720, 640)
        self._layout = remember_layout(self, "table_editor")
        self._entry: Entry | None = None
        self._table: Table | None = None
        self._editing: str | None = None
        """The bits of the entry the form is editing; ``None`` for a new one."""
        self._filling = False
        self._fills = 0
        """How many times the grid was rebuilt: a change the window took back
        through :meth:`set_entry` needs no second rebuild."""
        self._tables: dict[int, Entry] = {}
        self._queue: list[str] = []
        """Keys still to add after the one in the form: the raw view's
        selection, one entry per byte."""
        self._sample_at: int | None = None
        """Where the form's key was taken from, for the sample line."""
        self._sample_bits = ""
        """The key :attr:`_sample_at` belongs to; the line hides once the form
        holds another."""
        self.speller: Speller = lambda offset: f"{offset:X}"
        self.inheritance: Inheritance | None = None
        self._inherited: dict[str, tuple[TableEntry, str]] = {}
        """What the table's includes give it, as :data:`Inheritance` answers."""
        self._problem = ""
        """What was wrong with the merged table when the grid was filled."""
        self._columns: dict[int, bool] = _stored_columns()
        """The columns shown or hidden by choice; the rest follow the table."""
        layout = QVBoxLayout(self)

        # -- which table, on what ---------------------------------------------
        head = WrapBar()
        self.table_pick = CompactComboBox(220)
        self.table_pick.setToolTip("The loaded table to edit")
        head.add_group("Table", self.table_pick)
        self.charset_pick = CompactComboBox(150)
        self.charset_pick.setToolTip(
            "The built-in encoding the table sits on; its own entries override "
            "the encoding's code for code"
        )
        head.add_group("Charset", self.charset_pick)
        self.includes = hint_field(
            QLineEdit(),
            "table ids",
            "The tables this one starts from (@include), in order: their entries "
            "show dimmed, and an entry of this table's own overrides one key for key",
        )
        fit_chars(self.includes, 14)
        head.add_group("Includes", self.includes)
        self.rename = QPushButton("Rename Table…")
        self.rename.setToolTip(
            "Change the table's id; switches and blocks that name it follow"
        )
        head.add_group("", self.rename)
        self.filter = hint_field(
            QLineEdit(),
            "Filter…",
            "Show only entries whose key, text or comment has this (Ctrl+F)",
        )
        self.filter.setClearButtonEnabled(True)
        fit_chars(self.filter, 12)
        head.add_group("", self.filter)
        layout.addWidget(head)
        self.title = ElidedLabel("No table")
        layout.addWidget(self.title)

        # -- the entries ------------------------------------------------------
        self.grid = _EntryGrid(0, len(HEADERS))
        self.grid.setHorizontalHeaderLabels(list(HEADERS))
        header = self.grid.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setSortIndicator(KEY, Qt.SortOrder.AscendingOrder)
        header.setToolTip("Click a column to sort by it; right-click to choose columns")
        self.column_menu = install_column_menu(
            self.grid, HEADERS, KEY, self._choose_column
        )
        """A checkable entry per column; Key stays, being what a row is."""
        header.setSectionResizeMode(TEXT, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(DETAILS, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COMMENT, QHeaderView.ResizeMode.Stretch)
        self.grid.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.grid.setEditTriggers(
            QTableWidget.EditTrigger.DoubleClicked
            | QTableWidget.EditTrigger.EditKeyPressed
        )
        self.grid.verticalHeader().setVisible(False)
        self.grid.setMinimumHeight(60)
        show_elided_tooltips(self.grid)

        # -- the entry ----------------------------------------------------------
        self.sample = ElidedLabel("")
        self.sample.setToolTip("Where in the file the key's bytes were taken from")
        self.form = TableEntryForm()
        self._lower = QWidget()
        lower_box = QVBoxLayout(self._lower)
        lower_box.setContentsMargins(0, 0, 0, 0)
        lower_box.addWidget(self.sample)
        lower_box.addWidget(self.form)
        lower_box.addStretch(1)
        # The form grows and shrinks as the kind picked changes what an entry
        # takes, so it is scrolled inside a pane of its own: growing it would
        # otherwise take height off the grid and move the rows under the cursor.
        self.form_pane = QScrollArea()
        self.form_pane.setWidget(self._lower)
        self.form_pane.setWidgetResizable(True)
        self.form_pane.setFrameShape(QFrame.Shape.NoFrame)
        self.form_pane.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        # Split rather than sized to fit: how much of a long table to see at
        # once against how much of the form is the user's answer, not ours.
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.addWidget(self.grid)
        self.splitter.addWidget(self.form_pane)
        self.splitter.setChildrenCollapsible(False)
        # A taller window is more rows; the form stays the height it was put at.
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        state = stored_bytes(settings().value(SPLITTER_KEY))
        if state is not None:
            self.splitter.restoreState(state)
        self._split_set = state is not None
        """Whether the split is the user's. Until it is, the form is given the
        height it asks for and the grid keeps the rest (:meth:`_fit_split`); a
        handle once dragged is never moved again."""
        self.splitter.splitterMoved.connect(self._remember_split)
        layout.addWidget(self.splitter, 1)
        row = QHBoxLayout()
        self.add = QPushButton("Add")
        self.add.setToolTip("Put the entry in the table (Enter)")
        self.new = QPushButton("New")
        self.new.setToolTip("Clear the form for a new entry")
        self.remove = QPushButton("Remove")
        self.remove.setToolTip("Remove the selected entries (Del in the grid)")
        self.shift = QPushButton("Shift Keys…")
        self.shift.setToolTip("Move the selected entries' keys by a constant")
        self.fill = QPushButton("Fill…")
        self.fill.setToolTip("Lay a run of characters over consecutive keys")
        self.save = QPushButton("Save")
        self.save.setToolTip("Write the table back to its file")
        self.save_as = QPushButton("Save As File…")
        self.save_as.setToolTip("Write the table to a new file")
        for button in (self.add, self.new, self.remove):
            row.addWidget(button)
        row.addStretch(1)
        for button in (self.shift, self.fill, self.save, self.save_as):
            row.addWidget(button)
        layout.addLayout(row)
        self.status = ElidedLabel("")
        layout.addWidget(self.status)

        self.table_pick.currentIndexChanged.connect(self._on_table_pick)
        self.charset_pick.currentIndexChanged.connect(self._on_charset_pick)
        self.includes.editingFinished.connect(self._on_includes_edited)
        self.filter.textChanged.connect(self._apply_filter)
        QShortcut(
            QKeySequence.StandardKey.Find,
            self,
            self._focus_filter,
            context=Qt.ShortcutContext.WidgetWithChildrenShortcut,
        )
        self.grid.itemSelectionChanged.connect(self._on_selection)
        self.grid.itemChanged.connect(self._edited)
        self.grid.itemDoubleClicked.connect(self._on_double_click)
        self.grid.delete_pressed.connect(self._remove)
        self.rename.clicked.connect(lambda: self.rename_requested.emit(self._entry))
        self.form.line_toggle.toggled.connect(
            lambda on: set_setting_bool(LINE_SHOWN_KEY, on)
        )
        self.form.set_line_shown(setting_bool(LINE_SHOWN_KEY))
        self.form.changed.connect(self._on_form_changed)
        self.form.submitted.connect(self._add)
        self.add.clicked.connect(self._add)
        self.new.clicked.connect(self._new)
        self.remove.clicked.connect(self._remove)
        self.shift.clicked.connect(self._shift)
        self.fill.clicked.connect(self._fill_dialog)
        self.save.clicked.connect(lambda: self.save_requested.emit(self._entry, False))
        self.save_as.clicked.connect(
            lambda: self.save_requested.emit(self._entry, True)
        )
        self.set_charsets([])

    @property
    def new_line(self) -> QLineEdit:
        """The form's Line field: an entry typed as its line."""
        return self.form.line

    # -- what is on offer -------------------------------------------------------

    def set_tables(self, entries: list[Entry]) -> None:
        """Every table entry the Table picker can switch to, and every table
        id a switch parameter can name."""
        loaded = [e for e in entries if e.table is not None]
        fill_pick(
            self.table_pick,
            [(f"@{e.table.id}  ·  {e.name}", id(e)) for e in loaded],
        )
        self._tables = {id(e): e for e in loaded}
        if self._entry is not None:
            select_data(self.table_pick, id(self._entry))
        self.form.set_tables([e.table.id for e in loaded])

    def set_charsets(self, names: list[tuple[str, str]]) -> None:
        """``(id, name)`` of every charset a table can sit on."""
        was = self.charset_pick.blockSignals(True)
        fill_pick(
            self.charset_pick, [("none", "none"), *((n, cid) for cid, n in names)]
        )
        if self._table is not None:
            self._show_charset()
        self.charset_pick.blockSignals(was)

    def _show_charset(self) -> None:
        charset = self._table.charset if self._table is not None else "none"
        if not select_data(self.charset_pick, charset):
            # A charset no plugin provides is still the table's; it is named.
            self.charset_pick.addItem(charset, charset)
            self.charset_pick.setCurrentIndex(self.charset_pick.count() - 1)

    def _on_table_pick(self, index: int) -> None:
        if self._filling:
            return
        entry = self._tables.get(self.table_pick.itemData(index))
        if entry is not None and entry is not self._entry:
            self.table_requested.emit(entry)

    def _on_charset_pick(self, index: int) -> None:
        if self._filling or self._entry is None or self._table is None:
            return
        charset = self.charset_pick.itemData(index)
        if charset and charset != self._table.charset:
            self.charset_chosen.emit(self._entry, charset)

    def _on_includes_edited(self) -> None:
        if self._filling or self._entry is None or self._table is None:
            return
        ids = tuple(
            nfc(word) for word in self.includes.text().replace(",", " ").split()
        )
        if ids == self._table.includes:
            return
        bad = [i for i in ids if not ID_PATTERN.fullmatch(i)]
        if bad:
            self.status.setText(f"{bad[0]!r} is not a table id.")
            return
        if len(set(ids)) != len(ids):
            self.status.setText("A table is included once.")
            return
        self.includes_chosen.emit(self._entry, ids)

    # -- the table --------------------------------------------------------------

    @property
    def entry(self) -> Entry | None:
        """The workspace entry whose table is being edited; ``None`` with no
        table open."""
        return self._entry

    def set_entry(self, entry: Entry | None) -> None:
        self._entry = entry
        if entry is None or entry.table is None:
            self.title.setText(entry.name if entry is not None else "No table")
        else:
            where = entry.path or entry.name
            dialect = entry.dialect or "native"
            self.title.setText(
                where if dialect == "native" else f"{where}  ·  {dialect}, converted"
            )
        self._fill()
        if self._editing is None:
            self.form.set_entry(None)
        self._sync_buttons()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        self._fit_split()
        # Opened on a table, the first thing to do is type a key.
        if self._editing is None and not self.form.key.text():
            self.form.focus_key()

    def _fit_split(self) -> None:
        """Give the form the height it asks for and the grid the rest.

        What a splitter does for itself at first, except that the form's height
        answers to the kind picked: a switch's parameters need room a text
        entry does not. So it is fitted again whenever the form changes — up to
        the moment the handle is dragged, after which the split is the user's
        and nothing here moves it. The rows stay where they were either way:
        the grid is left scrolled exactly where it was found, which is the
        whole point of the form having a pane of its own.
        """
        if self._split_set:
            return
        sizes = self.splitter.sizes()
        total = sum(sizes)
        if total <= 0:  # not laid out yet; the first show asks again
            return
        # Half at the most: a window opened short is still a window of rows.
        wanted = min(self._lower.sizeHint().height(), total // 2)
        if wanted == sizes[1]:
            return
        bar = self.grid.verticalScrollBar()
        at = bar.value()
        self.splitter.setSizes([total - wanted, wanted])
        bar.setValue(at)

    def _remember_split(self) -> None:
        self._split_set = True
        settings().setValue(SPLITTER_KEY, self.splitter.saveState())

    def prefill(self, keys: list[str], at: int | None = None) -> None:
        """Start new entries on ``keys`` in turn — the raw view's selection,
        one per byte — the first in the form now and the rest as each is
        added. ``at`` is the file offset the first came from."""
        if not keys:
            return
        self.grid.clearSelection()
        self._editing = None
        self._queue = list(keys[1:])
        self._sample_at = at
        self._sample_bits = keys[0]
        self.form.set_entry(TableEntry(keys[0], TokenKind.TEXT, ""))
        self.form.text.setFocus()
        self._say_where()
        self._sync_buttons()

    def _say_where(self) -> None:
        if self._sample_at is None:
            return
        more = len(self._queue)
        self.status.setText(
            f"Byte at {self.speller(self._sample_at)}"
            + (f" · {more} more to add after this one" if more else "")
        )

    def _snapshot(self) -> Table | None:
        """The entry's table as it is now, to undo back to."""
        return deepcopy(self._table)

    def _fill(self) -> None:
        self._filling = True
        self._fills += 1
        same_table = self._table is (self._entry.table if self._entry else None)
        self._table = self._entry.table if self._entry is not None else None
        selected = self._editing
        # A rebuild of the same table keeps its place; a new one starts at the top.
        scroll = self.grid.verticalScrollBar().value() if same_table else 0
        header = self.grid.horizontalHeader()
        sort_column, sort_order = (
            header.sortIndicatorSection(),
            header.sortIndicatorOrder(),
        )
        self.grid.setSortingEnabled(False)
        self.grid.setRowCount(0)
        weights_used = False
        self._inherited, self._problem = {}, ""
        if self._table is not None:
            if self.inheritance is not None and self._table.includes:
                self._inherited, self._problem = self.inheritance(self._table)
            own = self._table.entries
            rows: list[tuple[TableEntry, str | None]] = [
                (e, None) for e in self._table.sorted_entries()
            ]
            rows += [
                (e, origin)
                for bits, (e, origin) in sorted(
                    self._inherited.items(), key=lambda kv: (len(kv[0]), kv[0])
                )
                if bits not in own
            ]
            self.grid.setRowCount(len(rows))
            for row, (e, origin) in enumerate(rows):
                self._set_row(row, e, origin)
                weights_used = weights_used or e.weight != 1
        self.grid.setSortingEnabled(True)
        self.grid.sortItems(sort_column, sort_order)
        self.grid.resizeColumnToContents(KEY)
        self.grid.resizeColumnToContents(KIND)
        self.grid.resizeColumnToContents(WEIGHT)
        self._show_columns(weights_used)
        self.form.set_weights_used(weights_used)
        if self._entry is not None:
            select_data(self.table_pick, id(self._entry))
        self._show_charset()
        self.includes.setText(
            " ".join(self._table.includes) if self._table is not None else ""
        )
        self.includes.setEnabled(self._table is not None)
        if self._problem:
            self.status.setText(self._problem)
        self._apply_filter(self.filter.text())
        self.grid.verticalScrollBar().setValue(scroll)
        selected_row = self._row_of(selected) if selected is not None else None
        if selected_row is not None:
            self.grid.selectRow(selected_row)
            self.grid.scrollTo(self.grid.model().index(selected_row, KEY))
        self._filling = False
        if selected is not None and selected_row is None:
            self._editing = None
            self.form.set_entry(None)
        self._sync_buttons()

    def _row_of(self, bits: str) -> int | None:
        for row in range(self.grid.rowCount()):
            if self.grid.item(row, KEY).data(Qt.ItemDataRole.UserRole) == bits:
                return row
        return None

    # -- the columns ------------------------------------------------------------

    def _show_columns(self, weights_used: bool) -> None:
        """Every column chosen shows; Weight, left to itself, only shows when
        the table weights something."""
        for column in range(len(HEADERS)):
            chosen = self._columns.get(column)
            shown = chosen if chosen is not None else (column != WEIGHT or weights_used)
            self.grid.setColumnHidden(column, not shown)

    def _choose_column(self, column: int, shown: bool) -> None:
        """Remember a column's switch; hiding it is the menu's own doing."""
        self._columns[column] = shown
        settings().setValue(
            COLUMNS_KEY, {str(c): on for c, on in self._columns.items()}
        )

    def _set_row(self, row: int, e: TableEntry, origin: str | None = None) -> None:
        """One row: an entry of the table's own, or — ``origin`` naming the
        table it is own to — one an include gives it, dimmed."""
        details = describe(e)
        hidden = self._inherited.get(e.bits) if origin is None else None
        if hidden is not None and e.kind is TokenKind.TEXT and e.text == "":
            details = f"removes @{hidden[1]}'s entry"
        elif origin is not None:
            details = f"from @{origin}" + (f" · {details}" if details else "")
        cells = [
            format_key(e.bits),
            KIND_NAMES[e.kind],
            # A code reads as the dump shows it; the form holds the bare label.
            f"[{e.text}]" if e.kind is TokenKind.CODE else e.text,
            details,
            str(e.weight),
            e.comment.replace("\n", " ⏎ "),
        ]
        dim = (
            QApplication.palette().color(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text
            )
            if origin is not None
            else None
        )
        for column, text in enumerate(cells):
            item = _KeyItem(text) if column == KEY else QTableWidgetItem(text)
            if column == WEIGHT:
                item.setData(Qt.ItemDataRole.EditRole, e.weight)
            if column not in (TEXT, COMMENT) or e.kind is TokenKind.RETURN:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if column == KEY:
                item.setData(Qt.ItemDataRole.UserRole, e.bits)
                item.setData(ORIGIN_ROLE, origin)
            if dim is not None:
                item.setForeground(dim)
                item.setToolTip(
                    f"From @{origin}; editing it gives this table its own entry"
                )
            self.grid.setItem(row, column, item)

    def _entry_at(self, bits: str) -> TableEntry | None:
        """The table's own entry at ``bits``, else the one an include gives."""
        if self._table is None:
            return None
        own = self._table.entries.get(bits)
        if own is not None:
            return own
        inherited = self._inherited.get(bits)
        return inherited[0] if inherited is not None else None

    def _apply_filter(self, text: str) -> None:
        words = words_of(text)
        for row in range(self.grid.rowCount()):
            hit = matches_words(
                words,
                self.grid.item(row, KEY).text(),
                self.grid.item(row, TEXT).text(),
                self.grid.item(row, COMMENT).text(),
            )
            self.grid.setRowHidden(row, not hit)

    def _focus_filter(self) -> None:
        self.filter.setFocus()
        self.filter.selectAll()

    def _selected_bits(self) -> list[str]:
        rows = sorted({i.row() for i in self.grid.selectedItems()})
        return [self.grid.item(r, KEY).data(Qt.ItemDataRole.UserRole) for r in rows]

    # -- the form ---------------------------------------------------------------

    def _on_selection(self) -> None:
        if self._filling or self._table is None:
            return
        bits = self._selected_bits()
        entry = self._entry_at(bits[0]) if len(bits) == 1 else None
        if entry is not None:
            self._editing = bits[0]
            self.form.set_entry(entry)
        else:
            # Several rows, or none: the form is for one entry, so it is for
            # a new one until one row is picked.
            self._editing = None
        self._sync_buttons()

    def _on_form_changed(self) -> None:
        self._sync_buttons()
        self._show_sample()
        self._fit_split()

    def _show_sample(self) -> None:
        """Where the form's key came from, for the keys the raw view sent."""
        if self._sample_at is None or self.form.key_bits() != self._sample_bits:
            self.sample.setText("")
            return
        self.sample.setText(f"sampled from {self.speller(self._sample_at)}")

    def _on_double_click(self, item: QTableWidgetItem) -> None:
        """A cell that is not edited in place opens its control in the form."""
        column = item.column()
        if column == KEY:
            self.form.focus_key()
        elif column == KIND:
            self.form.kind.setFocus()
        elif column == WEIGHT:
            self.form.weight.setFocus()
            self.form.weight.selectAll()
        elif column == DETAILS:
            self.form.focus_details()

    def _sync_buttons(self) -> None:
        editing = self._editing is not None
        several = len(self._selected_bits()) > 1
        self.add.setText("Apply" if editing else "Add")
        self.add.setEnabled(not several)
        self.add.setToolTip(
            "Select one row to edit it, or none to add an entry"
            if several
            else "Apply the form to the selected entry (Enter)"
            if editing
            else "Put the entry in the table (Enter)"
        )
        self.form.setEnabled(not several)
        self.remove.setEnabled(bool(self._selected_bits()))
        self.shift.setEnabled(bool(self._selected_bits()))
        entry = self._entry
        # Save writes back only what came from a native file; anything else
        # needs a file chosen, which is Save As File….
        self.save.setEnabled(
            entry is not None and bool(entry.path) and entry.dialect == "native"
        )
        self.rename.setEnabled(entry is not None and entry.table is not None)

    def _emit_change(self, before: Table) -> None:
        """Hand the change to the window as one undo step, and rebuild the
        grid unless the window already had the editor do so."""
        fills = self._fills
        self.changed.emit(self._entry, before)
        if self._fills == fills:
            self._fill()

    def _new(self) -> None:
        self.grid.clearSelection()
        self._editing = None
        self.form.set_entry(None)
        self.form.focus_key()
        self._sync_buttons()

    def _commit(self, entry: TableEntry, replace_bits: str | None) -> bool:
        """Put ``entry`` in the table, in place of ``replace_bits`` when that is
        another key; one undo step."""
        table = self._table
        if table is None:
            self.status.setText("No table to edit.")
            return False
        before = self._snapshot()
        replaced = table.entries.get(entry.bits) if replace_bits != entry.bits else None
        try:
            if replace_bits is not None and replace_bits != entry.bits:
                table.remove(replace_bits)
            table.add(entry, replace=True)
            self._check_merged()
        except TableError as exc:
            self.status.setText(exc.message)
            table.replace_with(before)
            return False
        self.status.setText(
            f"Replaced {format_entry(replaced)}" if replaced is not None else ""
        )
        if replace_bits is not None:
            self._editing = entry.bits
        self._emit_change(before)
        return True

    def _check_merged(self) -> None:
        """Raise what an edit newly breaks in the table merged with its
        includes — a label an included table already gives another key."""
        if self.inheritance is None or self._table is None or not self._table.includes:
            return
        _, problem = self.inheritance(self._table)
        if problem and problem != self._problem:
            raise TableError(problem)

    def _add(self) -> None:
        try:
            entry = self.form.entry()
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        editing = self._editing
        if self._commit(entry, editing) and editing is None:
            # The next of the raw view's bytes, else — entries are usually
            # typed in key order — the next key of the same width, kind and
            # weight, its text blank.
            if self._queue:
                bits = self._queue.pop(0)
                if self._sample_at is not None:
                    self._sample_at += 1
                self._sample_bits = bits
                self._say_where()
            else:
                bits = _next_key(entry.bits)
                self._sample_at = None
            self.form.set_entry(replace(entry, bits=bits, text=""))
            self.form.text.setFocus()

    def _edited(self, item: QTableWidgetItem) -> None:
        """A Text or Comment cell typed over in the grid."""
        if self._filling or self._table is None:
            return
        bits = self.grid.item(item.row(), KEY).data(Qt.ItemDataRole.UserRole)
        old = self._entry_at(bits)
        if old is None:
            return
        if item.column() == TEXT:
            text = item.text()
            if old.kind is TokenKind.CODE:
                text = text.strip().removeprefix("[").removesuffix("]")
            entry = replace(old, text=text)
            try:
                parse_entry(format_entry(entry))
            except ValueError as exc:
                self.status.setText(str(exc))
                self._fill()
                return
        elif item.column() == COMMENT:
            entry = replace(old, comment=item.text().replace(" ⏎ ", "\n").strip())
        else:
            return
        if entry != old:
            self._commit(entry, bits)
        else:
            self._fill()

    # -- whole-table tools ------------------------------------------------------

    def shift_keys(self, delta: int, bits_list: list[str] | None = None) -> bool:
        """Move the selected entries' keys (or ``bits_list``) by ``delta``."""
        table = self._table
        bits_list = self._selected_bits() if bits_list is None else bits_list
        if table is None or not bits_list:
            self.status.setText("Select the entries to shift.")
            return False
        entries = [table.entries[b] for b in bits_list if b in table.entries]
        moved = []
        for e in entries:
            value = int(e.bits, 2) + delta
            if value < 0 or value >= 1 << len(e.bits):
                self.status.setText(f"{format_key(e.bits)} would leave its width.")
                return False
            moved.append((e, format(value, f"0{len(e.bits)}b")))
        before = self._snapshot()
        for e, _ in moved:
            table.remove(e.bits)
        try:
            for e, bits in moved:
                table.add(replace(e, bits=bits))
        except TableError as exc:
            self.status.setText(exc.message)
            table.replace_with(before)
            return False
        self._editing = None
        self.status.setText(f"Shifted {len(moved)} entries by {delta:+X}.")
        self._emit_change(before)
        return True

    def _shift(self) -> None:
        if not self._selected_bits():
            self.status.setText("Select the entries to shift.")
            return
        dialog = ShiftKeysDialog(len(self._selected_bits()), self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.delta() is not None:
            self.shift_keys(dialog.delta())

    def fill_run(self, chars: str, start: int, width: int, overwrite: bool) -> int:
        """Lay ``chars`` over consecutive keys of ``width`` hex digits from
        ``start``; keys already taken are left alone unless ``overwrite``.
        Returns how many entries were written."""
        table = self._table
        if table is None or not chars:
            return 0
        entries = entries_from_base(start, width * 4, "big", chars)
        before = self._snapshot()
        added = 0
        for entry in entries:
            if entry.bits in table.entries and not overwrite:
                continue
            table.add(entry, replace=True)
            added += 1
        kept = len(entries) - added
        self.status.setText(
            f"Filled {added} entries from {start:0{width}X}."
            + (f" {kept} key(s) already taken were left alone." if kept else "")
        )
        self._emit_change(before)
        return added

    def _fill_dialog(self) -> None:
        if self._table is None:
            return
        dialog = FillDialog(self._table, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        chars, start, width = dialog.chars(), dialog.start(), dialog.width()
        if not chars or start is None:
            self.status.setText("Not a hex key.")
            return
        self.fill_run(chars, start, width, dialog.overwrite.isChecked())

    def _remove(self) -> None:
        table = self._table
        bits_list = self._selected_bits()
        if table is None or not bits_list:
            return
        # No confirmation: it is one undo step, and says what it removed.
        before = self._snapshot()
        for bits in bits_list:
            if bits in table.entries:
                # An override goes, and what the include gives shows again.
                table.remove(bits)
            elif bits in self._inherited:
                # An inherited entry is removed by an empty one of the table's own.
                table.add(TableEntry(bits, TokenKind.TEXT, ""))
        self._editing = None
        n = len(bits_list)
        self.status.setText(
            f"Removed {format_key(bits_list[0])}" if n == 1 else f"Removed {n} entries"
        )
        self._emit_change(before)
        self.form.set_entry(None)


class _KeyItem(QTableWidgetItem):
    """A key cell: sorted by width and then bits, never by its spelling."""

    def __lt__(self, other: QTableWidgetItem) -> bool:
        mine = self.data(Qt.ItemDataRole.UserRole) or ""
        theirs = other.data(Qt.ItemDataRole.UserRole) or ""
        return (len(mine), mine) < (len(theirs), theirs)


def _stored_columns() -> dict[int, bool]:
    stored = settings().value(COLUMNS_KEY, {}) or {}
    out: dict[int, bool] = {}
    for key, on in dict(stored).items():
        try:
            out[int(key)] = as_bool(on)
        except (TypeError, ValueError):
            continue
    return out


class _EntryGrid(QTableWidget):
    """The entries grid: Del removes the selected rows when no cell is open."""

    delete_pressed = Signal()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if (
            event.key() == Qt.Key.Key_Delete
            and self.state() != QTableWidget.State.EditingState
        ):
            self.delete_pressed.emit()
            return
        super().keyPressEvent(event)


def _next_key(bits: str) -> str:
    """The key after ``bits`` at the same width; the last key stays."""
    value = int(bits, 2) + 1
    return format(value, f"0{len(bits)}b") if value < 1 << len(bits) else bits


__all__ = ["TableEditor"]
