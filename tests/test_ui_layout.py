"""The UI conventions of ``docs/ui.md``, checked against the built window: what a
surface does when its room runs out, how cut-short text is still read, the
keys every tool window shares, and how menus and buttons are labelled."""

from __future__ import annotations

import re

import pytest

from mapchar.core.block import RangeSource
from window_helpers import TABLE, add_block, make_window, open_rom_and_table

DATA = bytes.fromhex("41 42 00 42 41 00") + b"\xff" * 20


@pytest.fixture
def window(qtbot, monkeypatch):
    return make_window(qtbot, monkeypatch)


def _codes_table(count: int) -> str:
    """A table with ``count`` codes beside its letters, so a block over it has a
    long row of code buttons."""
    lines = [f"{0x80 + i:02X}=[code{i}]" for i in range(count)]
    return TABLE + "\n".join(lines) + "\n"


# -- room running out ---------------------------------------------------------


def test_many_code_buttons_wrap_rather_than_widen_the_window(window, tmp_path):
    data = bytes(range(0x80, 0x98)) + b"\x00" + DATA
    file_entry = open_rom_and_table(window, tmp_path, data, table=_codes_table(24))
    add_block(window, file_entry, "codes", RangeSource(0, len(data)))
    window._show_view("strings")
    assert window.strings.codes_layout.count() == 24
    # One row of two dozen buttons asked for well over a thousand pixels.
    assert window.strings.codes.minimumSizeHint().width() < 200
    assert window.minimumSizeHint().width() < 1000


def test_a_long_status_line_never_widens_its_window(window, tmp_path):
    from mapchar.ui.widgets import ElidedLabel

    open_rom_and_table(window, tmp_path, DATA)
    before = window.minimumSizeHint().width()
    window.nav_status.setText("view-only: missing " + "a-very-long-plugin, " * 40)
    window.block_label.setText("x" * 600)
    assert window.minimumSizeHint().width() == before
    label = ElidedLabel("the whole of it")
    label.setToolTip("what it explains")
    label.resize(20, 20)
    # The text and the tooltip read back as set; only the drawing is cut.
    assert label.is_elided() and label.text() == "the whole of it"
    assert label.toolTip() == "what it explains"


def test_the_reading_bar_wraps_rather_than_widening_the_window(window):
    """Every control a block's reading has is in the bar at once, and a narrow
    window takes them on more rows instead of growing to fit them."""
    bar = window.reading_bar
    bar.load(bar.config(window._default_reading(), "main"), block=True)
    assert bar.minimumSizeHint().width() < 400
    assert bar.heightForWidth(400) > bar.heightForWidth(2000)


def test_a_count_is_as_wide_as_what_it_holds_not_its_maximum(window):
    """A spin box that may reach a million but holds tens is no wider than the
    tens; every address and offset field is one width."""
    bar = window.reading_bar
    assert bar.ptr_size.width() < bar.ptr_stride.width() < bar.count.width()
    assert bar.count.maximumWidth() < bar.count.sizeHint().width()
    widths = {
        field.maximumWidth()
        for field in (
            bar.start,
            bar.stop,
            bar.bound,
            bar.ptr_offset,
            window.offset_box,
            window.hex_panel.goto,
            window.hex_panel.at,
        )
    }
    assert len(widths) == 1


def test_tool_windows_stay_usable_at_their_smallest(window):
    """None of them can be made smaller than the controls in it need, and none
    needs more than a small screen has."""
    for tool in (
        window.search_window,
        window.scan_window,
        window.decompress_window,
        window.preview_window,
        window.table_editor,
        window.find_replace,
        window.hex_panel,
    ):
        hint = tool.minimumSizeHint()
        assert hint.width() < 700 and hint.height() < 400, tool


# -- cut-short text reads in full --------------------------------------------


def _hover(view, index):
    """Send a tooltip request over ``index`` and return what it showed."""
    from PySide6.QtCore import QEvent, QPoint
    from PySide6.QtGui import QHelpEvent
    from PySide6.QtWidgets import QApplication, QToolTip

    shown: list[str] = []
    original = QToolTip.showText
    QToolTip.showText = lambda *a, **k: shown.append(a[1])
    try:
        centre = view.visualRect(index).center()
        event = QHelpEvent(QEvent.Type.ToolTip, centre, view.mapToGlobal(QPoint()))
        QApplication.sendEvent(view.viewport(), event)
    finally:
        QToolTip.showText = original
    return shown


def test_a_cut_short_cell_shows_its_whole_text_on_hover(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    block = add_block(
        window, file_entry, "a rather long block name indeed", RangeSource(0, 6)
    )
    tree = window.files_panel.tree
    window.files_panel.show()
    tree.resize(90, 300)
    item = window.files_panel._items[id(block)]
    shown = _hover(tree, tree.indexFromItem(item))
    assert shown and shown[0].startswith("a rather long block name indeed")
    # The row's own tooltip still follows the name.
    assert block.path in shown[0]


def test_a_cell_with_room_keeps_its_own_tooltip_behaviour(qtbot):
    from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem

    from mapchar.ui.widgets import show_elided_tooltips

    tree = show_elided_tooltips(QTreeWidget())
    qtbot.addWidget(tree)
    item = QTreeWidgetItem(["short"])
    tree.addTopLevelItem(item)
    tree.resize(600, 200)
    tree.show()
    qtbot.waitExposed(tree)
    assert _hover(tree, tree.indexFromItem(item)) == []


def test_a_compact_picker_spells_out_a_current_item_it_cuts(qtbot):
    from mapchar.ui.widgets import CompactComboBox

    combo = CompactComboBox(60)
    qtbot.addWidget(combo)
    combo.addItem("short")
    combo.addItem("a table name far too long for sixty pixels")
    combo.resize(combo.sizeHint())
    assert not combo.current_text_elided()
    combo.setCurrentIndex(1)
    assert combo.current_text_elided()


# -- shared keys -------------------------------------------------------------


def test_esc_closes_every_tool_window(window, qtbot):
    from PySide6.QtCore import Qt

    for tool in (
        window.search_window,
        window.scan_window,
        window.decompress_window,
        window.preview_window,
        window.table_editor,
    ):
        tool.show()
        assert tool.isVisible()
        qtbot.keyClick(tool, Qt.Key.Key_Escape)
        assert not tool.isVisible(), tool


def test_f2_renames_the_files_row_in_place(window, tmp_path, qtbot):
    from PySide6.QtCore import Qt

    file_entry = open_rom_and_table(window, tmp_path, DATA)
    block = add_block(window, file_entry, "b", RangeSource(0, 6))
    tree = window.files_panel.tree
    tree.setCurrentItem(window.files_panel._items[id(block)])
    qtbot.keyClick(tree, Qt.Key.Key_F2)
    assert window.files_panel._editing is block


# -- labels ------------------------------------------------------------------

_SMALL_WORDS = frozenset(
    "a an and as at by for from in into of on or over the to via with".split()
)


def _title_case_problems(labels):
    """The labels with a word that should be capitalised and is not."""
    bad = []
    for label in labels:
        words = re.findall(r"[A-Za-z][\w'-]*", label.replace("&", ""))
        for position, word in enumerate(words):
            if position and word.lower() in _SMALL_WORDS:
                continue
            if word[0].islower():
                bad.append(label)
                break
    return bad


def _all_menu_rows(window):
    from mapchar.ui.help_dialogs import submenus

    below = submenus(window.menuBar())

    def walk(menu):
        for action in menu.actions():
            if action.isSeparator():
                continue
            yield action
            sub = below.get(action)
            if sub is not None and sub is not window.recent_menu:
                yield from walk(sub)

    return list(walk(window.menuBar()))


def test_every_menu_row_has_a_mnemonic_and_title_case(window):
    rows = _all_menu_rows(window)
    assert [a.text() for a in rows if "&" not in a.text()] == []
    assert _title_case_problems(a.text() for a in rows) == []


def test_every_context_menu_row_is_title_case(window, tmp_path):
    from mapchar.ui.help_dialogs import submenus

    file_entry = open_rom_and_table(window, tmp_path, DATA)
    block = add_block(window, file_entry, "b", RangeSource(0, 6))
    table = window.workspace.table_entries()[0]
    labels = []
    for entry in (None, file_entry, block, table):
        menu = window._build_files_menu(entry)
        below = submenus(menu)
        for action in menu.actions():
            labels.append(action.text())
            if action in below:
                labels += [a.text() for a in below[action].actions()]
    assert _title_case_problems(label for label in labels if label) == []


def test_every_button_is_title_case(window):
    from PySide6.QtWidgets import QPushButton

    labels = [
        button.text()
        for surface in (
            window,
            window.search_window,
            window.scan_window,
            window.decompress_window,
            window.preview_window,
            window.table_editor,
            window.find_replace,
        )
        for button in surface.findChildren(QPushButton)
        if button.text()
    ]
    assert labels and _title_case_problems(labels) == []


def test_the_theme_rows_are_one_choice(window):
    assert window.theme_light.isCheckable() and window.theme_dark.isCheckable()
    assert window.theme_light.isChecked() != window.theme_dark.isChecked()
