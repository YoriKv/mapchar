"""A nested block in the Files panel: a row per inner table, each built and
opened on its own, so a block of hundreds of groups costs only the one read."""

from __future__ import annotations

import pytest

from mapchar.core.block import NestedPointerSource, StringRecord
from mapchar.ui.files_panel import FilesPanel
from window_helpers import add_block, item_for, make_yes_window, open_rom_and_table


@pytest.fixture
def window(qtbot, monkeypatch):
    """A live window whose every modal answers Yes / Discard without showing."""
    return make_yes_window(qtbot, monkeypatch)


NESTED_ROM = bytes.fromhex(
    "08 00 0C 00  14 00 18 00"  # two records: (table $8, base $C), ($14, $18)
    "01 00 04 00  FF 41 42 00"  # inner table 0 reaches AB[end] at $D
    "42 00 FF FF  01 00 03 00"  # B[end] at $10; inner table 1 at $14
    "FF 41 00 42  00 FF FF FF"  # A[end] at $19 and B[end] at $1B, from base $18
)
"""Two nested records, two strings each, so the block has two groups."""

NESTED = NestedPointerSource(0, 8, 2, 4, null=0, inner_size=2, inner_null=0)


def nested_block(window, tmp_path):
    entry = open_rom_and_table(window, tmp_path, NESTED_ROM)
    return add_block(window, entry, "script", NESTED)


def test_a_nested_block_opens_to_a_row_per_inner_table(window, tmp_path):
    block = nested_block(window, tmp_path)
    assert [s.current_text() for s in block.doc.strings] == [
        "AB[end]",
        "B[end]",
        "A[end]",
        "B[end]",
    ]
    item = item_for(window, block)
    item.setExpanded(True)  # builds the rows
    # One row per inner table, named for the table rather than the base, each
    # closed over its own strings.
    assert [item.child(i).text(0) for i in range(item.childCount())] == [
        "8  2 strings",
        "14  2 strings",
    ]
    assert "inner pointer table at 8" in item.child(0).toolTip(0)
    assert "its pointers count from C" in item.child(0).toolTip(0)
    panel = window.files_panel
    assert panel.group_of(item.child(0)) == (block, 0xC)
    assert panel.group_of(item.child(1)) == (block, 0x18)
    # Closed: a block of hundreds of groups costs only the one being read.
    assert [item.child(i).childCount() for i in range(2)] == [1, 1]
    assert panel._is_stub(item.child(0).child(0))


def test_opening_a_group_row_builds_that_group_s_strings_alone(window, tmp_path):
    block = nested_block(window, tmp_path)
    item = item_for(window, block)
    item.setExpanded(True)
    item.child(1).setExpanded(True)
    panel = window.files_panel
    assert (id(block), 0x18) in panel._expanded_groups
    # The rows are rebuilt, so the group row is a new item.
    group = item.child(1)
    assert [group.child(i).text(0) for i in range(group.childCount())] == [
        "2  A[end]",
        "3  B[end]",
    ]
    assert [panel.string_of(group.child(i)) for i in range(2)] == [
        (block, 2),
        (block, 3),
    ]
    # The other group is untouched.
    assert panel._is_stub(item.child(0).child(0))
    item.child(1).setExpanded(False)
    assert (id(block), 0x18) not in panel._expanded_groups
    assert panel._is_stub(item.child(1).child(0))


def test_clicking_a_group_takes_the_view_to_its_inner_table(window, tmp_path):
    block = nested_block(window, tmp_path)
    item = item_for(window, block)
    item.setExpanded(True)
    window.files_panel._activate(item.child(1))
    # The block stays current with all of its strings: a group is where to
    # look, not a reading of its own, so nothing is confined.
    assert window._entry is block
    assert len(block.doc.strings) == 4
    assert window._offset == 0x14
    assert window._bounds is None
    assert window.strings.table.rowCount() == 4
    assert window.files_panel.tree.currentItem() is item.child(1)


def test_showing_a_string_of_a_nested_block_opens_the_group_it_is_under(
    window, tmp_path
):
    block = nested_block(window, tmp_path)
    window._show_string(block, 3)
    panel = window.files_panel
    assert (id(block), 0x18) in panel._expanded_groups
    item = item_for(window, block)
    assert panel.string_of(panel.tree.currentItem()) == (block, 3)
    assert panel._string_child(item, 3) is panel.tree.currentItem()


def test_opening_a_group_row_with_a_key_stays_on_that_row(window, tmp_path):
    """The row the key is on must survive being opened: taking it out of the
    tree leaves Qt's current row on another entry, which reads as a jump."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent

    block = nested_block(window, tmp_path)
    panel = window.files_panel
    item = item_for(window, block)
    item.setExpanded(True)
    group = item.child(1)
    panel._select_item(group)
    visited: list[object] = []
    panel.entry_activated.connect(visited.append)

    def press(key):
        panel.tree.keyPressEvent(
            QKeyEvent(QKeyEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)
        )

    press(Qt.Key.Key_Right)
    assert panel.tree.currentItem() is group
    assert group.isExpanded() and group.childCount() == 2
    press(Qt.Key.Key_Left)
    assert panel.tree.currentItem() is group
    assert not group.isExpanded()
    # Nothing else in the panel was ever shown.
    assert visited == []
    assert window._entry is block


def test_the_filter_opens_a_closed_group_holding_a_match(window, tmp_path):
    """A group's rows are built only while it is open, so the filter matches
    its strings by what their rows would say, and builds and opens the one
    holding a match — and only that one."""
    block = nested_block(window, tmp_path)
    panel = window.files_panel
    item = item_for(window, block)
    item.setExpanded(True)
    first, second = item.child(0), item.child(1)
    panel.filter.setText("AB[end]")
    # The block keeps the match visible, as an unnested block's would.
    assert not item.isHidden()
    assert first.isExpanded() and not first.isHidden()
    assert [first.child(i).text(0) for i in range(first.childCount())] == [
        "0  AB[end]",
        "1  B[end]",
    ]
    assert not first.child(0).isHidden()
    assert first.child(1).isHidden()
    # The group with nothing matching is neither built nor opened.
    assert second.isHidden()
    assert panel._is_stub(second.child(0))
    # Clearing the filter leaves the block as lazy as the filter found it.
    panel.filter.setText("")
    assert not first.isExpanded()
    assert panel._is_stub(first.child(0))
    assert (id(block), 0xC) not in panel._expanded_groups
    assert panel._filter_groups == set()


def test_the_filter_leaves_a_group_the_view_is_in_open(window, tmp_path):
    """A string shown from inside a group the filter opened keeps its row:
    the group is the view's now, not the filter's to close."""
    block = nested_block(window, tmp_path)
    panel = window.files_panel
    item = item_for(window, block)
    item.setExpanded(True)
    panel.filter.setText("A[end]")
    window._show_string(block, 2)
    panel.filter.setText("")
    assert (id(block), 0x18) in panel._expanded_groups
    assert panel.string_of(panel.tree.currentItem()) == (block, 2)


def test_a_rebuild_forgets_the_open_groups_of_a_block_that_is_gone(window, tmp_path):
    """``id()`` keys the open groups, and a removed entry's id is one a new
    Entry can be handed — which would open with the old one's groups."""
    block = nested_block(window, tmp_path)
    panel = window.files_panel
    item = item_for(window, block)
    item.setExpanded(True)
    item.child(1).setExpanded(True)
    assert (id(block), 0x18) in panel._expanded_groups
    window._remove_entries([block])
    assert block not in window.workspace.entries
    assert panel._expanded_groups == set()


def test_opening_a_group_whose_strings_are_built_leaves_its_rows_alone(
    window, tmp_path, monkeypatch
):
    """A group row built with its block is opened again on the way in: doing
    the work twice would drop the very rows the reader is on."""
    block = nested_block(window, tmp_path)
    panel = window.files_panel
    item = item_for(window, block)
    item.setExpanded(True)
    group = item.child(1)
    group.setExpanded(True)
    rows = [group.child(i) for i in range(group.childCount())]
    built: list[int] = []
    made = FilesPanel._string_item
    monkeypatch.setattr(
        FilesPanel,
        "_string_item",
        lambda self, entry, rec: built.append(rec.index) or made(self, entry, rec),
    )
    panel._on_expanded(group)
    assert built == []
    assert [group.child(i) for i in range(group.childCount())] == rows


def test_a_group_under_no_base_stands_there_with_its_strings(window, tmp_path):
    """A string no inner pointer reached is grouped under no base, so there is
    no table to go to and nothing to open: its row is built once, and holds no
    expander over a stub nothing would ever replace."""
    block = nested_block(window, tmp_path)
    panel = window.files_panel
    item = item_for(window, block)
    item.setExpanded(True)
    block.doc.strings.append(StringRecord(4, 0x1C * 8, 0x1E * 8, [], original="A[end]"))
    panel._update_item(block)
    last = item.child(item.childCount() - 1)
    assert last.text(0) == "?  1 string"
    assert panel.group_of(last) is None  # nowhere to go: a click does nothing
    assert not panel._is_stub(last.child(0))
    assert panel.string_of(last.child(0)) == (block, 4)
    panel.select_string(block, 4)
    assert panel.tree.currentItem() is panel._string_child(item, 4)


def test_a_rebuild_puts_an_open_group_back_open(window, tmp_path):
    """A row added or removed rebuilds the panel; a group that was open comes
    back open over its strings, as its block does."""
    block = nested_block(window, tmp_path)
    item = item_for(window, block)
    item.setExpanded(True)
    item.child(1).setExpanded(True)
    window.files_panel.rebuild()
    item = item_for(window, block)
    assert item.isExpanded()
    group = item.child(1)
    assert group.isExpanded()
    assert [group.child(i).text(0) for i in range(group.childCount())] == [
        "2  A[end]",
        "3  B[end]",
    ]
    assert not item.child(0).isExpanded()
