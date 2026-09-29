"""The Captures dock and its Review tab against what went wrong in review: a
proposal accepted twice or read through a table the project lacks, entries
that do not read, the captures of a project left behind, the buttons each
proposal offers, runs in the game, and the list kept in place."""

from __future__ import annotations

import json
import os
import time
from dataclasses import replace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QInputDialog

from capture_fake import CONSOLE, MESSAGES, FakeEmulator, Game, build_rom
from mapchar.capture.occurrence import Finding
from mapchar.capture.proposals import BLOCK, ENTRIES, GLYPH
from mapchar.capture.session import FAILED, TRACING, Session, capture_root
from mapchar.project.entry import EntryKind
from mapchar.ui.capture import ACCEPTED, REJECTED, StringShotsDialog, finding_html
from mapchar.ui.main_window.capture import NEW_TABLE, REVIEW
from test_capture import arrive
from test_capture_ui import start, trace_one
from window_helpers import open_rom_and_table


def two(window, qtbot, tmp_path, **kw):
    s = start(window, tmp_path, **kw)
    trace_one(window, qtbot, s, MESSAGES[1])
    s.emulator.game.message = 2
    trace_one(window, qtbot, s, MESSAGES[2])
    window._capture_review()
    return s, {p.kind: p for p in window._capture_proposals}


def blocks(window):
    return [e for e in window.workspace.entries if e.kind is EntryKind.BLOCK]


def select(window, pid):
    panel = window.captures_panel
    panel.select_proposal(pid)
    assert panel._proposal().id == pid
    return panel


def tick_until(window, qtbot, check, seconds=60):
    end = time.monotonic() + seconds
    while not check():
        window._capture_tick()
        qtbot.wait(1)
        assert time.monotonic() < end, "timed out"


# -- accepting


def test_an_accepted_block_cannot_be_accepted_again(window, qtbot, tmp_path):
    s, props = two(window, qtbot, tmp_path)
    block = props[BLOCK]
    panel = select(window, block.id)
    assert panel.accept_button.isEnabled()
    panel.accept_button.click()
    assert len(blocks(window)) == 1
    panel = select(window, block.id)
    assert not panel.accept_button.isEnabled() and not panel.edit_button.isEnabled()
    assert "Accepted" in panel.detail.toPlainText()
    window._capture_accept(block.id)
    assert len(blocks(window)) == 1


def test_undo_takes_an_acceptance_back(window, qtbot, tmp_path):
    s, props = two(window, qtbot, tmp_path)
    block = props[BLOCK]
    window._capture_accept(block.id)
    assert window.captures_panel.states[block.id] == ACCEPTED
    window.undo_stack.undo()
    assert not blocks(window)
    panel = select(window, block.id)
    assert panel.states[block.id] == "" and panel.accept_button.isEnabled()
    window._capture_accept(block.id)
    assert [b.name for b in blocks(window)] == [block.name]


def test_accept_all_is_one_undo_step(window, qtbot, tmp_path):
    s, props = two(window, qtbot, tmp_path)
    panel = window.captures_panel
    assert panel.accept_all_button.isEnabled()
    table = window.workspace.entry_for_table("main").table
    had = dict(table.entries)
    panel.accept_all_button.click()
    assert len(blocks(window)) == 1
    assert len(window.workspace.entry_for_table("main").table.entries) > len(had)
    assert not panel.accept_all_button.isEnabled()
    window.undo_stack.undo()
    assert not blocks(window)
    assert window.workspace.entry_for_table("main").table.entries == had


def test_without_a_table_blocks_read_the_one_entries_go_to(window, qtbot, tmp_path):
    game = Game(build_rom(), 1)
    rom = tmp_path / "game.bin"
    rom.write_bytes(game.rom)
    entry = window.open_rom(str(rom))
    s = Session(
        str(tmp_path / "game.bin.capture"),
        entry.path,
        CONSOLE,
        FakeEmulator(game),
        on_change=window._capture_changed,
    )
    window._game_capture, window._capture_file = s, entry
    window.captures_dock.show()
    trace_one(window, qtbot, s, MESSAGES[1])
    s.emulator.game.message = 2
    trace_one(window, qtbot, s, MESSAGES[2])
    window._capture_review()
    props = {p.kind: p for p in window._capture_proposals}
    assert window._capture_target == NEW_TABLE
    assert props[BLOCK].config.table_id == NEW_TABLE
    panel = select(window, props[BLOCK].id)
    assert f"@{NEW_TABLE} (a new table)" in panel.detail.toPlainText()
    window._capture_accept(props[ENTRIES].id)
    window._capture_accept(props[BLOCK].id)
    (b,) = blocks(window)
    assert b.config.table_id == NEW_TABLE
    assert window.workspace.entry_for_table(NEW_TABLE).table.entries


def test_entries_that_do_not_read_are_asked_for_again(
    window, qtbot, tmp_path, monkeypatch
):
    s, props = two(window, qtbot, tmp_path)
    asked = []
    answers = iter([("this is not an entry", True), ("", False)])

    def ask(parent, title, label, text):
        asked.append((label, text))
        return next(answers)

    monkeypatch.setattr(QInputDialog, "getMultiLineText", ask)
    before = dict(window.workspace.entry_for_table("main").table.entries)
    window._capture_edit(props[ENTRIES].id)
    assert len(asked) == 2
    assert "do not read" in asked[1][0] and asked[1][1] == "this is not an entry"
    assert window.workspace.entry_for_table("main").table.entries == before


def test_a_rejected_proposal_can_be_reset(window, qtbot, tmp_path):
    s, props = two(window, qtbot, tmp_path)
    pid = props[ENTRIES].id
    panel = select(window, pid)
    panel.reject_button.click()
    panel = select(window, pid)
    assert panel.states[pid] == REJECTED and panel.reset_button.isEnabled()
    assert not panel.reject_button.isEnabled()
    with open(os.path.join(s.root, REVIEW), encoding="utf-8") as fh:
        assert json.load(fh)["states"][pid] == REJECTED
    panel.reset_button.click()
    panel = select(window, pid)
    assert panel.states[pid] == "" and not panel.reset_button.isEnabled()


def test_a_review_file_that_does_not_read_is_no_review(window, qtbot, tmp_path):
    s = start(window, tmp_path)
    for written in ("", "{", "[1]", "3"):
        with open(os.path.join(s.root, REVIEW), "w", encoding="utf-8") as fh:
            fh.write(written)
        window._capture_load_review()
        assert window._capture_states == {} and window._capture_added == {}


# -- what each proposal offers


def test_the_buttons_each_proposal_offers(window, qtbot, tmp_path):
    s, props = two(window, qtbot, tmp_path)
    panel = window.captures_panel
    glyph = next(
        p
        for p in window._capture_proposals
        if p.kind == GLYPH and not panel.states.get(p.id)
    )
    expect = {
        # accept, edit, reject, confirm, sweep, go to, label
        BLOCK: (True, True, True, True, True, True, False),
        ENTRIES: (True, True, True, False, False, False, False),
        GLYPH: (False, False, True, True, False, False, True),
    }
    for p in (props[BLOCK], props[ENTRIES], glyph):
        select(window, p.id)
        got = tuple(
            b.isEnabled()
            for b in (
                panel.accept_button,
                panel.edit_button,
                panel.reject_button,
                panel.confirm_button,
                panel.sweep_button,
                panel.goto_button,
                panel.label_button,
            )
        )
        assert got == expect[p.kind], p.kind
    select(window, glyph.id)
    assert panel.label_edit.isEnabled()
    select(window, props[BLOCK].id)
    assert panel.label_edit.text() == "" and not panel.label_edit.isEnabled()


def test_glyphs_are_gathered_under_one_row(window, qtbot, tmp_path):
    s, props = two(window, qtbot, tmp_path)
    panel = window.captures_panel
    glyphs = [p for p in window._capture_proposals if p.kind == GLYPH]
    tops = [
        panel.review.topLevelItem(i) for i in range(panel.review.topLevelItemCount())
    ]
    assert len(tops) == len(window._capture_proposals) - len(glyphs) + 1
    group = tops[-1]
    assert group.childCount() == len(glyphs) and "Glyphs to label" in group.text(0)
    panel.review.setCurrentItem(group)
    assert panel._proposal() is None and not panel.reject_button.isEnabled()


def test_enter_and_delete_on_the_review(window, qtbot, tmp_path):
    s, props = two(window, qtbot, tmp_path)
    panel = select(window, props[ENTRIES].id)
    panel.review.setFocus()
    panel._delete_proposal()
    assert panel.states[props[ENTRIES].id] == REJECTED
    select(window, props[BLOCK].id)
    panel._enter_proposal()
    assert len(blocks(window)) == 1


def test_go_to_shows_where_the_strings_are(window, qtbot, tmp_path):
    s, props = two(window, qtbot, tmp_path)
    block = props[BLOCK]
    window._capture_goto(block.id)
    assert window._entry is window._capture_file
    assert window._offset == block.offset


# -- a project left behind


def test_a_new_project_closes_the_captures(window, qtbot, tmp_path):
    s, props = two(window, qtbot, tmp_path)
    window._capture_timer.start()
    window._new_project()
    panel = window.captures_panel
    assert window._game_capture is None and window._capture_file is None
    assert not window._capture_timer.isActive() and not window._capture_proposals
    assert panel.list.count() == 0 and not panel.review_items()
    assert not panel.hint.isHidden()
    window._capture_accept(props[BLOCK].id)
    assert not blocks(window)


def test_opening_a_project_closes_the_captures(window, qtbot, tmp_path):
    s, props = two(window, qtbot, tmp_path)
    window._capture_accept(props[BLOCK].id)
    project = str(tmp_path / "game.mapchar")
    assert window._write_project(project)
    # Saving moved the captures beside the project.
    assert s.root == project + ".capture" and os.path.isdir(s.root)
    assert not os.path.exists(str(tmp_path / "game.bin.capture"))
    assert window.open_project(project)
    assert window._game_capture is None and window.captures_panel.list.count() == 0


def test_accepting_under_a_file_no_longer_open_refuses(window, qtbot, tmp_path):
    s, props = two(window, qtbot, tmp_path)
    window.workspace.entries.remove(window._capture_file)
    window._capture_accept(props[BLOCK].id)
    assert not blocks(window)
    assert any("no longer open" in m for m in window.errors)


def test_captures_of_a_rom_that_is_gone_are_said(window, tmp_path):
    entry = open_rom_and_table(window, tmp_path, build_rom(), rom_name="game.bin")
    os.remove(entry.path)
    window._show_captures()
    assert window._game_capture is None
    assert any("could not be read" in m for m in window.errors)


# -- the captures list


def test_the_list_is_kept_in_place(window, qtbot, tmp_path):
    s = start(window, tmp_path)
    arrive(s, "0001")
    arrive(s, "0002")
    window._capture_refresh()
    panel = window.captures_panel
    first = panel.list.item(0)
    panel.list.setCurrentRow(1)
    s.captures[0].text = "changed"
    window._capture_refresh()
    assert panel.list.item(0) is first and "changed" in first.text()
    assert panel.list.currentRow() == 1
    assert set(panel._thumbs) == {"0001", "0002"}
    s.skip(s.captures[0])
    window._capture_refresh()
    assert panel.list.count() == 1 and set(panel._thumbs) == {"0002"}


def test_a_capture_that_fails_is_selected_with_its_reason(window, qtbot, tmp_path):
    s = start(window, tmp_path)
    cap = arrive(s)
    window._capture_refresh()
    s.submit(cap, "Welcome to the Islnad of Tests!")
    tick_until(window, qtbot, lambda: cap.state == FAILED)
    window._capture_refresh()
    panel = window.captures_panel
    assert panel._selected() == cap.id
    assert "<u" in panel.list.item(0).toolTip()
    assert "Islnad</u>" in panel.list.item(0).toolTip()


def test_enter_in_the_capture_window_captures_once(window, qtbot, tmp_path):
    s = start(window, tmp_path)
    cap = arrive(s)
    window._capture_tick()
    win = window._capture_windows[cap.id]
    got = []
    win.captured.connect(lambda *a: got.append(a))
    win.text.setText("Welcome to the Island")
    win.text.setFocus()
    qtbot.keyClick(win.text, Qt.Key.Key_Return)
    assert len(got) == 1


def test_retyping_while_tracing_traces_the_new_text(window, qtbot, tmp_path):
    s = start(window, tmp_path)
    cap = arrive(s)
    window._capture_tick()
    s.submit(cap, MESSAGES[1])
    tick_until(window, qtbot, lambda: cap.state == TRACING)
    window._capture_submit(cap.id, "Island of Tests")
    tick_until(window, qtbot, lambda: not s.busy)
    assert s.sightings()[0].typed == "Island of Tests"


def test_messages_show_at_once(window, qtbot, tmp_path):
    s = start(window, tmp_path)
    s.on_message = window._capture_said
    arrive(s, "0001", "font watched\n")
    assert "font was not read" in window.statusBar().currentMessage()
    window._capture_tick()
    assert "font was not read" in window.captures_panel.status.text()


# -- runs in the game


def test_confirm_is_not_offered_twice_at_once(window, qtbot, tmp_path):
    s, props = two(window, qtbot, tmp_path)
    panel = select(window, props[BLOCK].id)
    panel.confirm_button.click()
    assert window._capture_side is not None
    assert not panel.confirm_button.isEnabled() and not panel.sweep_button.isEnabled()
    assert not panel.side_line.isHidden()
    # A second run is refused, and said so.
    window._capture_sweep(props[BLOCK].id)
    assert any("under way" in m for m in window.errors)
    # Both Stops are there to press while it runs.
    assert panel.stop_button.isEnabled() and panel.side_stop_button.isEnabled()
    panel.side_stop_button.click()
    assert window._capture_side is None and panel.confirm_button.isEnabled()
    assert panel.side_line.isHidden()
    panel.confirm_button.click()
    assert window._capture_side is not None
    panel.stop_button.click()
    assert window._capture_side is None


def test_a_tables_strings_are_shown_in_the_game(window, qtbot, tmp_path):
    s, props = two(window, qtbot, tmp_path)
    panel = select(window, props[BLOCK].id)
    panel.sweep_button.click()
    dialog = window.findChild(StringShotsDialog)
    assert dialog is not None and dialog.running
    tick_until(window, qtbot, lambda: window._capture_side is None)
    assert sorted(dialog.cells) == list(range(len(MESSAGES)))
    assert not dialog.running and not dialog.more_button.isEnabled()
    dialog.close()


def test_showing_a_tables_strings_can_be_stopped(window, qtbot, tmp_path):
    s, props = two(window, qtbot, tmp_path)
    window._capture_sweep(props[BLOCK].id)
    dialog = window.findChild(StringShotsDialog)
    dialog.stop_button.click()
    assert window._capture_side is None and not dialog.running
    assert "Stopped" in dialog.status.text() and dialog.more_button.isEnabled()
    dialog.more_button.click()
    assert window._capture_side is not None
    dialog.close()  # closing stops it too
    assert window._capture_side is None


# -- the underlined words


@pytest.mark.parametrize(
    ("words", "matched", "text", "under"),
    [
        (["the", "he", "color"], ["the"], "the he color", ["he", "color"]),
        (["Hello", "wrold"], ["Hello"], "Hello wrold", ["wrold"]),
        (["a", "u"], ["a"], "a u", ["u"]),  # a word that is a tag's name
    ],
)
def test_only_whole_words_that_did_not_fit_are_underlined(words, matched, text, under):
    out = finding_html(text, Finding("no-match", "m", words, matched))
    got = [chunk.split("</u>")[0].split(">")[-1] for chunk in out.split("<u ")[1:]]
    assert got == under
    assert out.count("<u ") == len(under) and "style='color:#d33'" in out
    assert "&lt;" not in out


def test_a_conflict_is_settled_by_the_reading_chosen(
    window, qtbot, tmp_path, monkeypatch
):
    from mapchar.capture.combine import Conflict, Meaning
    from mapchar.capture.proposals import CONFLICT, Proposal
    from mapchar.core.table import OperandSpec

    start(window, tmp_path)
    window._capture_target = "main"
    code = Meaning(0x77, 1, "command", params=1)
    two_bytes = Meaning(0x77, 1, "command", params=2)
    choices = {
        "command:(1 parameter bytes)": code,
        "command:(2 parameter bytes)": two_bytes,
    }
    conflict = Conflict("77", {k: {"0001"} for k in choices}, code, choices)
    window._capture_proposals = [
        Proposal("conflict:77", CONFLICT, "Conflict: 77", conflict=conflict)
    ]
    monkeypatch.setattr(
        QInputDialog,
        "getItem",
        lambda *a, **k: ("command:(2 parameter bytes)", True),
    )
    window._capture_accept("conflict:77")
    entry = window.workspace.entry_for_table("main").table.entries["01110111"]
    assert [str(o) for o in entry.operands] == [str(OperandSpec.parse("u16"))]
    assert window.captures_panel.states["conflict:77"] == ACCEPTED


# -- review findings


def test_entries_the_table_gives_other_texts_are_not_accepted(window, qtbot, tmp_path):
    from mapchar.core.table import TokenKind

    s, props = two(window, qtbot, tmp_path)
    p = props[ENTRIES]
    table = window._capture_target_entry().table
    for e in p.entries:
        if e.bits in table.entries:
            table.remove(e.bits)
        table.add(replace(e, text="Z") if e.kind is TokenKind.TEXT else e)
    window._capture_show_proposals()
    assert window.captures_panel.states[p.id] == ""  # the texts differ
    asked = []
    window._ask = lambda title, message: asked.append(message) or False
    window._capture_accept(p.id)
    assert asked and "would change" in asked[0]


def test_what_was_accepted_after_an_edit_reads_as_accepted(
    window, qtbot, tmp_path, monkeypatch
):
    from mapchar.ui.main_window import capture as capmod

    s, props = two(window, qtbot, tmp_path)
    block = props[BLOCK]

    class Dialog:
        DialogCode = capmod.NewBlockDialog.DialogCode

        def __init__(self, cfg, *a, **k):
            self.cfg = cfg

        def exec(self):
            return self.DialogCode.Accepted

        def block_name(self):
            return "Edited"

        def config(self):
            src = self.cfg.source
            return replace(self.cfg, source=replace(src, stop=src.stop + 1))

    monkeypatch.setattr(capmod, "NewBlockDialog", Dialog)
    window._capture_edit(block.id)
    assert window.captures_panel.states[block.id] == ACCEPTED
    window._capture_accept_all()
    assert [b.name for b in blocks(window)] == ["Edited"]
    window.undo_stack.undo()  # the Accept All (its entries)
    window.undo_stack.undo()  # the edited block
    assert not blocks(window)
    assert window.captures_panel.states[block.id] == ""
    # Kept across reopening, in review.json.
    window._capture_load_review()
    assert "source" in window._capture_added[block.id]


def test_accept_all_adds_one_block_per_source(window, qtbot, tmp_path):
    s, props = two(window, qtbot, tmp_path)
    block = props[BLOCK]
    twin = replace(block, id=block.id + ":twin", name="Twin")
    window._capture_proposals.append(twin)
    window._capture_accept_all()
    assert len(blocks(window)) == 1


def test_save_as_leaves_the_old_projects_captures(window, qtbot, tmp_path):
    s = start(window, tmp_path)
    arrive(s)
    a = str(tmp_path / "a.mapchar")
    window.project_path = a
    window._capture_rehome()  # the first save: from beside the ROM
    assert s.root == capture_root(a, s.rom_path)
    window.project_path = str(tmp_path / "b.mapchar")
    window._capture_rehome()  # Save As: a's captures stay a's
    assert s.root == capture_root(a, s.rom_path) and os.path.isdir(s.root)
    assert not os.path.exists(capture_root(window.project_path, s.rom_path))


def test_a_label_being_typed_survives_a_refresh(window, qtbot, tmp_path):
    s, props = two(window, qtbot, tmp_path)
    panel = window.captures_panel
    glyph = next(
        p
        for p in window._capture_proposals
        if p.kind == GLYPH and not panel.states.get(p.id)
    )
    select(window, glyph.id)
    panel.label_edit.setText("half-ty")
    window.undo_stack.undo()  # anything moving the undo stack refreshes
    window._capture_review()  # and so does a capture being done
    assert panel._current_pid() == glyph.id
    assert panel.label_edit.text() == "half-ty"


def test_a_glyph_row_stays_selected_when_rebuilt(qtbot):
    from mapchar.capture.proposals import Proposal
    from mapchar.ui.capture import CapturesPanel

    panel = CapturesPanel()
    qtbot.addWidget(panel)
    props = [
        Proposal("block:1", BLOCK, "B"),
        Proposal("glyph:1", GLYPH, "G1"),
        Proposal("glyph:2", GLYPH, "G2"),
    ]
    panel.show_proposals(props, {})
    panel.select_proposal("glyph:2")
    panel.show_proposals(list(reversed(props)), {})  # other rows: rebuilt
    assert panel._current_pid() == "glyph:2"


def test_the_review_waits_while_the_dock_is_hidden(window, qtbot, tmp_path):
    s, props = two(window, qtbot, tmp_path)
    window._capture_accept(props[BLOCK].id)
    window.captures_dock.hide()
    window.undo_stack.undo()
    assert window.captures_panel.states[props[BLOCK].id] == ACCEPTED  # not yet
    window.captures_dock.show()
    window._capture_show_proposals()
    assert window.captures_panel.states[props[BLOCK].id] == ""


def test_removing_a_capture_from_the_dock_is_asked(window, qtbot, tmp_path):
    s = start(window, tmp_path)
    cap = arrive(s)
    window._capture_refresh()
    panel = window.captures_panel
    panel.list.setCurrentRow(0)
    panel.remove_button.click()  # answered No
    assert s.captures == [cap] and os.path.isdir(cap.folder)
    window._ask = lambda *a: True
    panel.remove_button.click()
    assert not s.captures and not os.path.exists(cap.folder)


def test_show_more_never_runs_another_roms_captures(window, qtbot, tmp_path):
    s, props = two(window, qtbot, tmp_path)
    window._capture_sweep(props[BLOCK].id)
    dialog = window.findChild(StringShotsDialog)
    dialog.stop_button.click()
    window._new_project()
    assert window.findChild(StringShotsDialog) is None or not dialog.isVisible()


def test_stop_playing_is_offered_while_playing(window, qtbot, tmp_path):
    s = start(window, tmp_path)
    window._capture_refresh()
    assert not window.stop_playing_action.isEnabled()
    s.play()
    window._capture_refresh()
    assert window.stop_playing_action.isEnabled()
    window.stop_playing_action.trigger()
    assert not s.playing and not window.stop_playing_action.isEnabled()
