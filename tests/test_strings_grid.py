"""The Strings grid's Translation cell: the two ways an edit leaves it.

Return commits through the view's handler, and so does leaving the cell —
clicking another row, or any other widget. A refusal keeps what was typed
either way, rather than putting the bytes' text back over it.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLineEdit, QVBoxLayout, QWidget

from mapchar.ui.strings_view import COL_TRANSLATION, RowData, StringsView


def rows() -> list[RowData]:
    return [
        RowData(0, 0, "A[end]", "A[end]", 2, 2, "untouched", ""),
        RowData(1, 4, "B[end]", "B[end]", 2, 2, "untouched", ""),
    ]


@pytest.fixture
def view(qtbot):
    """A grid of two strings, shown and active: the cell editor only commits
    on a focus-out that really left it."""
    view = StringsView()
    qtbot.addWidget(view)
    view.set_rows(rows())
    view.resize(800, 600)
    view.show()
    qtbot.waitExposed(view)
    view.activateWindow()
    return view


def click_row(view, row: int) -> None:
    point = view.table.visualRect(view.table.model().index(row, COL_TRANSLATION))
    QTest.mouseClick(
        view.table.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        point.center(),
    )


def test_leaving_a_cell_commits_and_a_refusal_keeps_the_draft(view, qtbot):
    refused: list[str] = []
    problems: list[str] = []
    landed: list[tuple[int, str]] = []
    view.commit_handler = lambda i, t: (
        landed.append((i, t)) or (refused[0] if refused else None)
    )
    view.problem_shown.connect(problems.append)

    # Refused, with the cell left by a click on another row: the reason is
    # shown, the bytes keep what they say, and the editor comes back on its
    # own row with the draft still in it.
    refused.append("too long")
    view.edit_row(0)
    view.delegate.current_editor().setPlainText("AAAAAAAA[end]")
    click_row(view, 1)
    qtbot.wait(10)
    assert landed == [(0, "AAAAAAAA[end]")] and problems == ["too long"]
    editor = view.delegate.current_editor()
    assert editor is not None and editor.toPlainText() == "AAAAAAAA[end]"
    assert view.selected_indices() == [0]
    assert (
        view.table.item(0, COL_TRANSLATION).data(Qt.ItemDataRole.EditRole) == "A[end]"
    )

    # Accepted, the edit lands once and the cell closes on the row clicked.
    refused.clear()
    landed.clear()
    editor.setPlainText("BB[end]")
    click_row(view, 1)
    qtbot.wait(10)
    assert landed == [(0, "BB[end]")]
    assert view.delegate.current_editor() is None
    assert view.table.currentRow() == 1

    # A cell left as the bytes have it is not an edit at all: no commit, and
    # so no undo step for opening a cell and thinking better of it.
    landed.clear()
    view.edit_row(0)
    click_row(view, 1)
    qtbot.wait(10)
    assert landed == []


def test_a_cell_left_by_the_window_going_inactive_keeps_its_draft(view, qtbot):
    """Another window taking the focus is not leaving the cell: the draft is
    neither committed behind the translator's back nor lost."""
    landed: list[tuple[int, str]] = []
    view.commit_handler = lambda i, t: landed.append((i, t)) or "too long"
    view.edit_row(0)
    view.delegate.current_editor().setPlainText("AAAAAAAA[end]")

    other = QWidget()
    qtbot.addWidget(other)
    QVBoxLayout(other).addWidget(QLineEdit())
    other.show()
    other.activateWindow()
    qtbot.wait(10)

    assert not view.isActiveWindow() and landed == []
    editor = view.delegate.current_editor()
    assert editor is not None and editor.toPlainText() == "AAAAAAAA[end]"


def test_escape_cancels_the_cell_and_leaves_no_editor_behind(view, qtbot):
    landed: list[tuple[int, str]] = []
    view.commit_handler = lambda i, t: landed.append((i, t)) or None
    view.edit_row(0)
    editor = view.delegate.current_editor()
    editor.setPlainText("AAAAAAAA[end]")
    QTest.keyClick(editor, Qt.Key.Key_Escape)
    qtbot.wait(10)
    assert landed == []
    assert (
        view.table.item(0, COL_TRANSLATION).data(Qt.ItemDataRole.EditRole) == "A[end]"
    )
    # The editor is gone with the cell, so a code button types into the pane
    # rather than into a widget Qt has destroyed.
    assert view.delegate.current_editor() is None
    view.select_index(0)
    view.insert_text("[line]")
    assert view.pane.editor.toPlainText() == "A[end][line]"
