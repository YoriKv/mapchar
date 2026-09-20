"""One pass through the whole window, and what it offers while nothing is open.

The round trip a session is — a ROM, a table, a block, a project, an undo — the
capability table read off the built window rather than promised, and what the
harness itself isolates, which is the ground every other window test stands on.
"""

from __future__ import annotations

from helpers import texts
from mapchar.core.block import RangeSource
from mapchar.project.entry import Entry, EntryKind
from window_helpers import ab_ba_rom, add_block, open_rom_and_table


def test_open_rom_table_and_block(window, tmp_path, monkeypatch):
    data = ab_ba_rom(20)
    entry = open_rom_and_table(window, tmp_path, data, rom_name="game.bin")
    assert entry is not None and window._doc is not None and window._doc.size == 26
    assert window.format_pick.currentData() == "main"
    window._refresh_view()
    model = window.raw._model
    assert model is not None and [t.text() for t in model.tokens][:3] == [
        "A",
        "B",
        "[end]",
    ]
    assert 0 in model.string_starts and 3 in model.string_starts

    # A block over the first six bytes, created without the dialog.
    add_block(window, entry, "b", RangeSource(0, 6))
    assert texts(window._doc.strings) == ["AB[end]", "BA[end]"]
    assert window.strings.table.rowCount() == 2

    # Project round trip.
    proj = tmp_path / "p.mapchar"
    assert window._write_project(str(proj))
    window._new_project()
    assert not window.workspace.entries
    assert window.open_project(str(proj))
    names = [e.name for e in window.workspace.entries]
    assert names == ["game.bin", "b", "main.tbl"]

    # Undo removes the last added entry.
    window.undo_stack.clear()
    window._push_add(
        Entry(EntryKind.BOOKMARK, "bm", entry.path, parent=window.workspace.entries[0])
    )
    assert len(window.workspace.entries) == 4
    window.undo_stack.undo()
    assert len(window.workspace.entries) == 3


# --- capability gating -----------------------------------------------------


def test_the_capability_table_covers_every_kind():
    from mapchar.core.capabilities import CAPABILITIES, Capability, EntryKind, supports

    assert set(CAPABILITIES) == set(EntryKind)
    assert not supports(None, Capability.NAVIGATION)  # nothing open supports nothing
    assert supports(EntryKind.FILE, Capability.CONTAINER)
    assert not supports(EntryKind.BLOCK, Capability.CONTAINER)
    assert supports(EntryKind.BLOCK, Capability.STRINGS)
    assert not supports(EntryKind.FILE, Capability.STRINGS)
    assert CAPABILITIES[EntryKind.BOOKMARK] == frozenset()


def test_nothing_open_leaves_the_controls_gated(window):
    assert not window.format_bar.isEnabled()
    assert not window.offset_box.isEnabled()
    assert not window.write_action.isEnabled()
    # The Block bar keeps its row, greyed and with nothing to say.
    assert not window.block_bar.isEnabled() and window.block_label.text() == ""


def test_a_file_gates_the_string_surfaces_off(window, tmp_path):
    entry = open_rom_and_table(window, tmp_path, b"AB\x00")
    window._activate_entry(entry)
    assert window.format_bar.isEnabled()
    assert window.offset_box.isEnabled() and window.goto_action.isEnabled()
    assert window.container_action.isEnabled()
    assert not window.strings_tab_action.isEnabled()
    assert not window.find_replace_action.isEnabled()
    assert not window.tabs.isTabEnabled(window.tabs.indexOf(window.strings))
    # Greyed, not gone — opening a block moves nothing — and it names the file.
    assert window.block_bar.isVisibleTo(window) and not window.block_bar.isEnabled()
    assert window.block_label.text() == "rom.bin · 3 bytes"


def test_a_block_gates_the_string_surfaces_on(window, tmp_path):
    data = bytes.fromhex("41 42 00")
    file_entry = open_rom_and_table(window, tmp_path, data)
    block = add_block(window, file_entry, "b", RangeSource(0, 3))
    assert window._entry is block
    assert window.strings_tab_action.isEnabled()
    assert window.find_replace_action.isEnabled()
    assert window.preview_action.isEnabled()
    assert window.tabs.isTabEnabled(window.tabs.indexOf(window.strings))
    assert window.block_bar.isEnabled()
    assert window.block_export.isEnabled()
    assert window.reading_bar.isEnabled()
    # A block reads its container through its parent, so the row is not its own.
    assert not window.container_action.isEnabled()


# --- long operations can be stopped -----------------------------------------


def test_modal_progress_asks_the_engine_to_stop_once_cancelled(window):
    from mapchar.ui.progress import ModalProgress

    with ModalProgress(window, "Working", "Working…") as run:
        assert run.progress(1, 4) is True
        assert not run.cancelled
        run.cancel()
        assert run.progress(2, 4) is False
        assert run.cancelled


# --- what the harness isolates ---------------------------------------------
# Two tests in order: the first writes, the second proves the fixture emptied
# the store between them. Without ``conftest.settings_root`` these would be
# reading and writing the developer's own profile, because Qt resolves the
# settings location once per process and no environment variable set afterwards
# moves it.

_PROBE = "probe/settings-are-isolated"


def test_the_settings_store_is_a_folder_of_this_runs_own(tmp_path):
    from mapchar.ui import settings

    store = settings()
    assert "pytest" in store.fileName()
    store.setValue(_PROBE, "written")
    store.sync()
    assert settings().value(_PROBE) == "written"


def test_the_settings_store_is_empty_again_for_the_next_test():
    from mapchar.ui import settings

    assert settings().value(_PROBE) is None


def test_a_dialog_nobody_arranged_for_answers_itself(window):
    """The autouse fixture answers ``QDialog`` as well as ``QMessageBox``: a
    modal reached by a path a test did not expect would otherwise run a loop
    the offscreen platform can never close."""
    from PySide6.QtWidgets import QDialog

    from mapchar.ui.dialogs import TextDialog

    assert TextDialog("Report", "body", window).exec() == QDialog.DialogCode.Rejected
