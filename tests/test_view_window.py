"""The central view's window is sized to its box: the Hex tab's rows and the
Text tab's lines, re-fitted as the box changes, and what a page step moves by."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QTextCursor, QWheelEvent
from PySide6.QtWidgets import QApplication

from mapchar.project.formats.table_native import HEADER
from mapchar.ui import BYTES_PER_ROW
from window_helpers import ASCII_TABLE, make_window, open_rom_and_table

LINE = b"The quick brown fox jumps over the lazy dog. "
PROSE = LINE * 400
"""Plain text with no line breaks, so every line is the box's own wrapping."""


@pytest.fixture
def window(qtbot, monkeypatch):
    return make_window(qtbot, monkeypatch)


def _shown(window, width: int, height: int):
    window.resize(width, height)
    window.show()
    QApplication.processEvents()


def _wheel(window, notches: int) -> None:
    viewport = window.text.edit.viewport()
    at = QPointF(10, 10)
    event = QWheelEvent(
        at,
        QPointF(viewport.mapToGlobal(at.toPoint())),
        QPoint(0, 0),
        QPoint(0, -120 * notches),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(viewport, event)


def test_the_hex_window_is_the_rows_shown_and_one_more(window, tmp_path):
    open_rom_and_table(window, tmp_path, bytes(range(256)) * 32)
    _shown(window, 900, 500)
    small = window.raw.visible_bytes()
    assert len(window.raw._model.data) == small + BYTES_PER_ROW
    _shown(window, 900, 1000)
    assert window.raw.visible_bytes() > small
    assert len(window.raw._model.data) == window.raw.visible_bytes() + BYTES_PER_ROW


def test_the_text_window_holds_the_lines_that_fit(window, tmp_path):
    open_rom_and_table(window, tmp_path, PROSE, table=ASCII_TABLE)
    window.text.wrap.setChecked(True)
    _shown(window, 900, 500)
    window.text_tab_action.trigger()
    QApplication.processEvents()
    text = window.text
    shown = text.shown_bytes()
    assert 0 < shown < len(PROSE)
    body = text.edit.toPlainText()
    assert body == PROSE[:shown].decode()
    # Whole lines, and the last of them in view.
    assert text.fitted_chars() == len(body)
    # Nothing to scroll: the box holds exactly its window.
    assert text.edit.verticalScrollBar().maximum() == 0
    # A taller box holds more.
    _shown(window, 900, 1000)
    assert text.shown_bytes() > shown
    # And a page is what was shown.
    page = text.shown_bytes()
    window._move(page)
    assert window._offset == page
    assert text.edit.toPlainText() == PROSE[page : page + text.shown_bytes()].decode()


def test_unwrapped_the_last_line_ends_at_the_box_edge(window, tmp_path):
    open_rom_and_table(window, tmp_path, PROSE, table=ASCII_TABLE)
    _shown(window, 900, 500)
    window.text_tab_action.trigger()
    window.text.wrap.setChecked(True)
    QApplication.processEvents()
    wrapped = window.text.shown_bytes()
    window.text.wrap.setChecked(False)
    QApplication.processEvents()
    # One unbroken line: only as much of it as the box is wide.
    unwrapped = window.text.shown_bytes()
    assert 0 < unwrapped < wrapped
    assert "\n" not in window.text.edit.toPlainText()


def test_the_wheel_moves_the_view_by_lines(window, tmp_path):
    open_rom_and_table(window, tmp_path, PROSE, table=ASCII_TABLE)
    window.text.wrap.setChecked(True)
    _shown(window, 900, 500)
    window.text_tab_action.trigger()
    QApplication.processEvents()
    shown = window.text.shown_bytes()
    _wheel(window, 1)
    assert 0 < window._offset < shown
    step = window._offset
    _wheel(window, -1)
    assert window._offset == 0
    window._go_to(step)
    _wheel(window, -2)
    assert window._offset == 0


def test_the_whole_file_fits_a_short_one(window, tmp_path):
    data = b"Hello\x00"
    open_rom_and_table(window, tmp_path, data, table=ASCII_TABLE)
    _shown(window, 900, 500)
    window.text_tab_action.trigger()
    QApplication.processEvents()
    assert window.text.shown_bytes() == len(data)
    assert window.text.edit.toPlainText() == "Hello[end]"


WIDE_TABLE = f"{HEADER}\n@table main\n/00=[end]\n8020= \n81FF=[line]\\n\n" + "".join(
    f"80{ord(c):02X}={c}\n" for c in "abcdefghijklmnopqrstuvwxyz"
)
"""Two bytes a character, so a view started a byte late decodes out of step."""


def _wide(text: str) -> bytes:
    return b"".join(b"\x81\xff" if c == "\n" else bytes([0x80, ord(c)]) for c in text)


WIDE_TEXT = "the quick brown fox jumps over the lazy dog\n" + (
    "pack my box with five dozen liquor jugs " * 3 + "\n"
)
WIDE_PROSE = _wide(WIDE_TEXT * 60)


def _text_tab(window, tmp_path, data, table, wrap=True):
    open_rom_and_table(window, tmp_path, data, table=table)
    window.text.wrap.setChecked(wrap)
    _shown(window, 900, 500)
    window.text_tab_action.trigger()
    QApplication.processEvents()
    return window.text


@pytest.mark.parametrize("wrap", [True, False])
def test_the_wheel_moves_to_where_a_line_starts(window, tmp_path, wrap):
    text = _text_tab(window, tmp_path, WIDE_PROSE, WIDE_TABLE, wrap)
    first = text.edit.toPlainText()
    for _ in range(5):
        body, starts = text.edit.toPlainText(), text.line_starts()
        _wheel(window, 1)
        # The view starts on the fourth line, and decodes in step with it.
        assert text.edit.toPlainText().startswith(body[starts[3] :])
    for _ in range(5):
        _wheel(window, -1)
    assert window._offset == 0
    assert text.edit.toPlainText() == first


def test_the_wheel_keeps_a_selection_on_its_text(window, tmp_path):
    text = _text_tab(window, tmp_path, WIDE_PROSE, WIDE_TABLE)
    starts = text.line_starts()
    cursor = text.edit.textCursor()
    cursor.setPosition(starts[4])
    cursor.setPosition(starts[6] + 5, QTextCursor.MoveMode.KeepAnchor)
    text.edit.setTextCursor(cursor)
    selected = window._selection
    chosen = text.edit.textCursor().selectedText()
    _wheel(window, 1)
    assert window._selection == selected
    assert text.edit.textCursor().selectedText() == chosen


@pytest.mark.parametrize("late", [0, 1])
def test_the_wheel_up_measures_the_lines_above(window, tmp_path, late):
    text = _text_tab(window, tmp_path, WIDE_PROSE, WIDE_TABLE)
    # A line start well into the file, reached without the wheel — or a byte
    # past it, inside a character, which the view decodes out of step.
    line = len(_wide(WIDE_TEXT * 10))
    window._go_to(line + late)
    body = text.edit.toPlainText()
    _wheel(window, -1)
    above = text.edit.toPlainText()
    # Three lines up, in step with the file: the line is a line again.
    starts = text.line_starts()
    assert text.byte_at_char(starts[3]) == line
    assert above.startswith("the quick")
    if not late:
        assert above[starts[3] :].startswith(body[: len(above) - starts[3]])


def test_the_wheel_stops_at_the_last_window(window, tmp_path):
    _text_tab(window, tmp_path, b"Hello\x00", ASCII_TABLE)
    _wheel(window, 1)
    assert window._offset == 0
