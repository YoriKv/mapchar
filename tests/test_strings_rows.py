"""The Strings grid's rows: the room a string has, and which rows an edit
touches.

Room is the slot the layout will actually take — the string's own bytes plus
the fill after them — so what the grid says and what a commit accepts are the
same number. An edit refreshes the rows its bytes reach and leaves the others
where they are, and a text one string cannot hold does not hold back the ones
that can.
"""

from __future__ import annotations

from mapchar.core.block import PointerTableSource, RangeSource, Status, WriteMode
from window_helpers import ABC_TABLE, add_block, open_rom_and_table

# --- room ------------------------------------------------------------------


POINTER_ROM = (
    bytes.fromhex("10 00 16 00")  # two pointers, to 0x10 and 0x16
    + b"\x00" * 12
    + bytes.fromhex("41 42 00")  # 0x10: AB[end]
    + bytes.fromhex("43 43 43")  # 0x13: bytes no string owns, and not fill
    + bytes.fromhex("42 41 00")  # 0x16: BA[end]
    + b"\xee" * 3  # 0x19: fill, which the string before it may use
)


def _pointer_block(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, POINTER_ROM, table=ABC_TABLE)
    return add_block(
        window,
        file_entry,
        "b",
        PointerTableSource(0, 4, 2, 2, "little", "linear"),
        bound=0x1C,
        fill=b"\xee",
        write_mode=WriteMode.SLOTTED,
    )


def test_room_is_the_slot_the_layout_will_take(window, tmp_path):
    """Bytes between two strings that are not the block's fill belong to no
    slot, and the Room column says so.

    A pointer block's strings need not sit end to end. The gap after the first
    is three bytes of something else — a table, another block's text — which
    the layout will not write over, so the string's room is its own three bytes
    and not the six up to the next one. The second string's gap *is* fill, the
    padding a shorter translation would leave, and that is room.
    """
    block = _pointer_block(window, tmp_path)
    rows = window._row_data(block, block.doc)
    assert [(r.index, r.address, r.used, r.room) for r in rows] == [
        (0, 0x10, 3, 3),
        (1, 0x16, 3, 6),
    ]

    # And the number is the layout's: a translation over it is refused, and one
    # that fits the fill after the second string lands.
    steps = window.undo_stack.count()
    window._on_translation_edited(0, "ABBB[end]")
    assert window.undo_stack.count() == steps
    assert block.doc.strings[0].current_text() == "AB[end]"
    window._on_translation_edited(1, "BAAB[end]")
    assert block.doc.strings[1].current_text() == "BAAB[end]"


def test_the_draft_readout_counts_the_same_room(window, tmp_path):
    """What the pane says as the text is typed is the room the grid shows."""
    _pointer_block(window, tmp_path)
    window.strings.select_index(0)
    window._on_draft("ABB[end]")
    assert window.strings.pane.readout.text().startswith("4 / 3 byte(s)")


PACKED_ROM = (
    bytes.fromhex("10 00 13 00")  # two pointers, to 0x10 and 0x13
    + b"\x00" * 12
    + bytes.fromhex("41 42 00")  # 0x10: AB[end]
    + bytes.fromhex("42 41 00")  # 0x13: BA[end]
    + b"\xee" * 4  # 0x16: four bytes of spare up to the bound
)


def test_packed_room_is_the_string_s_own_bytes_and_the_block_s_spare(window, tmp_path):
    """A packed block lays its strings out afresh, so no string has a place of
    its own and the room to the bound is not any one string's.

    What a string may grow to is its own bytes plus the spare the block has
    left over — the same spare in every row, and in no two rows at once.
    """
    file_entry = open_rom_and_table(window, tmp_path, PACKED_ROM, table=ABC_TABLE)
    block = add_block(
        window,
        file_entry,
        "b",
        PointerTableSource(0, 4, 2, 2, "little", "linear"),
        bound=0x1A,
        fill=b"\xee",
    )
    assert block.config.effective_write_mode is WriteMode.PACKED
    rows = window._row_data(block, block.doc)
    assert [(r.used, r.room) for r in rows] == [(3, 7), (3, 7)]

    # And it is the number a commit accepts: one string may take all four
    # spare bytes, and once it has, the other has none left to take.
    window._on_translation_edited(0, "ABABAB[end]")
    assert block.doc.strings[0].current_text() == "ABABAB[end]"
    rows = window._row_data(block, block.doc)
    assert [(r.used, r.room) for r in rows] == [(7, 7), (3, 3)]
    steps = window.undo_stack.count()
    window._on_translation_edited(1, "BAC[end]")
    assert window.undo_stack.count() == steps
    assert block.doc.strings[1].current_text() == "BA[end]"


# --- which rows an edit refreshes ------------------------------------------


def test_a_commit_refreshes_only_the_rows_its_bytes_reach(
    window, tmp_path, monkeypatch
):
    """A block is laid out row by row, so a commit that changed one string may
    not lay out the rest again.

    The grid holds a row per string; the rows a commit does not change are the
    very rows it held before, and no row is rebuilt.
    """
    data = bytes.fromhex("41 42 00 EE EE EE 42 41 00 EE EE EE")
    file_entry = open_rom_and_table(window, tmp_path, data, table=ABC_TABLE)
    block = add_block(window, file_entry, "b", RangeSource(0, 12), fill=b"\xee")
    kept = window.strings.rows_by_index()[1]
    rebuilt: list[int] = []
    monkeypatch.setattr(
        type(window.strings),
        "set_rows",
        lambda self, rows, keep_selection=True: rebuilt.append(len(rows)),
    )

    window._on_translation_edited(0, "A[end]")
    assert not rebuilt
    rows = window.strings.rows_by_index()
    assert rows[0].translation == "A[end]" and rows[0].status == "edited"
    assert rows[1] is kept

    window.undo_stack.undo()
    assert not rebuilt
    rows = window.strings.rows_by_index()
    assert rows[0].translation == "AB[end]" and rows[0].status == "untouched"
    assert rows[1] is kept
    assert block.doc.strings[0].current_text() == "AB[end]"


# --- one string that cannot take it ----------------------------------------


def test_apply_to_identical_keeps_the_strings_that_do_fit(window, tmp_path):
    """A block is laid out whole, so one string with no room for the
    translation refuses the whole batch; going on string by string keeps
    everything the block does have room for."""
    data = (
        bytes.fromhex("41 42 00")  # 0x00: AB[end], fill after it
        + b"\xee" * 3
        + bytes.fromhex("41 42 00")  # 0x06: AB[end], fill after it
        + b"\xee" * 3
        + bytes.fromhex("41 42 00")  # 0x0C: AB[end], and no room at all
    )
    file_entry = open_rom_and_table(window, tmp_path, data, table=ABC_TABLE)
    block = add_block(window, file_entry, "b", RangeSource(0, 15), fill=b"\xee")
    rows = window._row_data(block, block.doc)
    assert [r.room for r in rows] == [6, 6, 3]

    window._on_translation_edited(0, "ABBB[end]")
    window.strings.select_index(0)
    window._apply_to_identical(0, False)
    texts = [r.current_text() for r in block.doc.strings]
    assert texts == ["ABBB[end]", "ABBB[end]", "AB[end]"]
    assert window.statusBar().currentMessage() == "Applied to 1 string(s)"


# --- the dirty check ---------------------------------------------------------


def test_the_dirty_check_follows_a_string_s_state(window, tmp_path):
    """A block's strings are nearly all a project file holds, so each block's
    serialisation is kept until its strings change; a status or a note the
    keeping missed would leave the project reading clean with work in it."""
    data = bytes.fromhex("41 42 00 EE EE EE 42 41 00 EE EE EE")
    file_entry = open_rom_and_table(window, tmp_path, data, table=ABC_TABLE)
    block = add_block(window, file_entry, "b", RangeSource(0, 12), fill=b"\xee")
    proj = str(tmp_path / "p.mapchar")
    assert window._write_project(proj) and not window._project_dirty()

    window.strings.select_index(1)
    window._toggle_review_selected()
    assert block.doc.strings[1].status is Status.REVIEW
    assert window._project_dirty()

    assert window._write_project(proj) and not window._project_dirty()
    window._on_notes_edited(1, "check this")
    assert block.doc.strings[1].notes == "check this"
    assert window._project_dirty()

    assert window._write_project(proj) and not window._project_dirty()
    window._on_translation_edited(0, "A[end]")
    assert block.doc.strings[0].status is Status.EDITED
    assert window._project_dirty()
