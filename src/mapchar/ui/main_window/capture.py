"""Capture: Play in Emulator and its setup window, the capture window, the
Captures dock and its Review tab, runs in the game, and the timer that
advances the session.

The session (:mod:`mapchar.capture.session`) does the work in emulator
processes; a timer here advances it a bounded step at a time, so the window
never waits on an emulator. An accepted proposal becomes an ordinary block or
table entries through the usual undoable edits; whether a proposal is
accepted is read from the project itself, so undo takes it back.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, replace

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDockWidget, QFileDialog, QInputDialog

from mapchar.capture.consoles import detect
from mapchar.capture.emulator import Mesen
from mapchar.capture.proposals import (
    BLOCK,
    CONFLICT,
    ENTRIES,
    GLYPH,
    Proposal,
    code_bits,
    entry_for,
    propose,
    relabelled,
)
from mapchar.capture.protocol import WAIT, CaptureError, Step
from mapchar.capture.session import (
    DONE,
    Capture,
    Session,
    capture_root,
    open_root,
)
from mapchar.capture.setup import write_json_atomic
from mapchar.capture.tablesweep import MAX_STRINGS, own_slot, table_of
from mapchar.core.errors import MapcharError
from mapchar.core.table import Table, TableEntry
from mapchar.plugins.base import Stage
from mapchar.plugins.registry import resolve_mapping
from mapchar.project.entry import EntryKind
from mapchar.project.formats.table_native import HEADER, format_entry, parse_native
from mapchar.ui.block_dialog import NewBlockDialog
from mapchar.ui.capture import (
    ACCEPTED,
    REJECTED,
    CaptureSetupWindow,
    CapturesPanel,
    CaptureWindow,
    ConfirmDialog,
    StringShotsDialog,
)
from mapchar.ui.undo_commands import TableCommand

EMULATOR_KEY = "capture/emulator"
"""QSettings key for the emulator's path — per machine, never the project."""

TICK_MS = 30
"""How often the session is advanced."""

TICK_BUDGET = 0.02
"""Seconds of work per tick."""

PROGRESS_SECONDS = 0.5
"""How often the work under way is shown again while nothing else changes."""

REVIEW = "review.json"
"""Which proposals were accepted or rejected, in the captures' folder."""

NEW_TABLE = "captured"
"""The table accepted entries go to when the reading has none."""


@dataclass
class _Side:
    """A run in the game beside the session's work: Confirm in game, or a
    table's strings. Its step yields a shot to :attr:`shot` as it takes one."""

    step: Step
    capture: str
    done: Callable[[object], None]
    failed: Callable[[str], None]
    shot: Callable[[tuple[int, str]], None] | None = None
    stopped: Callable[[], None] | None = None


class CaptureMixin:
    """Capture from a running game.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    _game_capture: Session | None = None
    _capture_side: _Side | None = None

    def _build_capture(self) -> None:
        self.captures_panel = CapturesPanel(self.address_spelling)
        dock = QDockWidget("Captures", self)
        dock.setObjectName("captures_dock")
        dock.setWidget(self.captures_panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        dock.hide()
        self.captures_dock = dock
        self._capture_file = None
        """The file entry the session plays."""
        self._capture_side = None
        self._capture_windows: dict[str, CaptureWindow] = {}
        self._capture_prompted: set[str] = set()
        self._capture_states: dict[str, str] = {}
        """The proposals marked accepted or rejected (review.json)."""
        self._capture_added: dict[str, dict] = {}
        """What accepting each proposal added — a block's source, or table
        entries — which it counts as accepted while the project holds
        (review.json)."""
        self._capture_proposals: list[Proposal] = []
        self._capture_target = NEW_TABLE
        """The table the proposals shown read through, and entries go to."""
        self._capture_shift_used = 0
        self._capture_shown = 0.0
        self._capture_dirty = False
        self._capture_review_due = False
        self._capture_messages_shown = 0
        self._capture_timer = QTimer(self)
        self._capture_timer.setInterval(TICK_MS)
        self._capture_timer.timeout.connect(self._capture_tick)
        self.undo_stack.indexChanged.connect(self._capture_show_proposals)
        dock.visibilityChanged.connect(
            lambda shown: self._capture_show_proposals() if shown else None
        )
        p = self.captures_panel
        p.text_requested.connect(self._capture_prompt)
        p.retry_requested.connect(self._capture_retry)
        p.stop_requested.connect(self._capture_stop)
        p.remove_requested.connect(self._capture_remove_asked)
        p.stop_playing_requested.connect(self._capture_stop_playing)
        p.review_requested.connect(self._capture_review)
        p.accept_requested.connect(self._capture_accept)
        p.accept_all_requested.connect(self._capture_accept_all)
        p.edit_requested.connect(self._capture_edit)
        p.reject_requested.connect(self._capture_reject)
        p.reset_requested.connect(self._capture_unreview)
        p.label_requested.connect(self._capture_label)
        p.confirm_requested.connect(self._capture_confirm)
        p.sweep_requested.connect(self._capture_sweep)
        p.goto_requested.connect(self._capture_goto)

    # -- the emulator

    def _capture_emulator(self) -> Mesen | None:
        path = str(self.settings.value(EMULATOR_KEY, "") or "")
        emu = Mesen(path or None)
        if emu.available():
            return emu
        return self._capture_choose_emulator()

    def _capture_choose_emulator(self) -> Mesen | None:
        stored = str(self.settings.value(EMULATOR_KEY, "") or "")
        path, _ = QFileDialog.getOpenFileName(
            self, "Where is Mesen 2?", os.path.dirname(stored) if stored else ""
        )
        if not path:
            return None
        emu = Mesen(path)
        if not emu.available():
            self._error(f"{path} is not a program mapchar can start.")
            return None
        self.settings.setValue(EMULATOR_KEY, path)
        if self._game_capture is not None:
            self._game_capture.emulator = emu
            self._capture_timer.start()
        return emu

    def _capture_ensure_emulator(self, s: Session) -> bool:
        """The session has an emulator, asked for now if it has none: only
        playing and the work need one, never looking at the captures."""
        if s.emulator is None:
            s.emulator = self._capture_emulator()
        return s.emulator is not None

    # -- the session

    def _capture_open(self) -> Session | None:
        """The session for the file on screen, made when there is none."""
        file_entry = self._current_file()
        if file_entry is None or not file_entry.path:
            self._error("Open a ROM first.")
            return None
        root = open_root(self.project_path, file_entry.path)
        s = self._game_capture
        if s is not None and s.rom_path == file_entry.path and s.root == root:
            self._capture_file = file_entry
            return s
        if s is not None and (s.busy or s.playing):
            doing = "played" if s.playing else "traced"
            if not self._ask(
                "Captures",
                f"The captures of {os.path.basename(s.rom_path)} are being "
                f"{doing}. Close them, and open those of "
                f"{os.path.basename(file_entry.path)}?",
            ):
                return None
        try:
            with open(file_entry.path, "rb") as fh:
                data = fh.read()
        except OSError as e:
            self._error(f"The file could not be read: {e}")
            return None
        console = detect(file_entry.path, data)
        if console is None:
            self._error(
                "Capture knows the SNES, NES and Game Boy Advance; this file is "
                "none of them."
            )
            return None
        console = console.for_rom(data)
        self._capture_reset()
        try:
            s = Session(
                root,
                file_entry.path,
                console,
                None,
                on_change=self._capture_changed,
                on_message=self._capture_said,
            )
        except (OSError, ValueError, CaptureError) as e:
            self._error(f"The captures could not be opened: {e}")
            return None
        self._game_capture, self._capture_file = s, file_entry
        self._capture_load_review()
        self._capture_prompted = {c.id for c in s.captures}
        self.captures_dock.show()
        if s.needs_emulator:
            self._capture_ensure_emulator(s)
        self._capture_refresh()
        self._capture_timer.start()
        return s

    def _capture_reset(self) -> None:
        """Close the captures: their windows, a run in the game, the session's
        work and connection. The emulator being played is the user's and
        stays open."""
        self._capture_side_stop()
        for win in list(self._capture_windows.values()):
            win.close()
        self._capture_windows = {}
        # A table's strings shown for these captures: Show More would run
        # them on whatever is opened next.
        for dialog in self.findChildren(StringShotsDialog):
            dialog.close()
        if self._game_capture is not None:
            self._game_capture.close()
        self._game_capture = None
        self._capture_file = None
        self._capture_proposals = []
        self._capture_states = {}
        self._capture_added = {}
        self._capture_prompted = set()
        self._capture_review_due = False
        self._capture_dirty = False
        self._capture_timer.stop()
        self.captures_panel.show_no_session()
        self.stop_playing_action.setEnabled(False)

    def _capture_rehome(self) -> None:
        """The project was saved: captures made before it had a file, beside
        the ROM, move beside it, when they can and nothing is there yet. A
        project saved under another name leaves its own captures where they
        are."""
        s = self._game_capture
        if s is None or s.root != capture_root(None, s.rom_path):
            return
        root = capture_root(self.project_path, s.rom_path)
        if root == s.root or os.path.exists(root):
            return
        if not s.can_rehome(root):
            self.statusBar().showMessage(
                f"The captures stay in {s.root} while the game is played.", 8000
            )
            return
        self._capture_side_stop()  # it reads the folder being moved
        if s.rehome(root):
            self._capture_refresh()

    def _capture_usable(self) -> bool:
        """Whether the captures' file is still in the project, for a proposal
        to be added under it."""
        f = self._capture_file
        if f is not None and any(e is f for e in self.workspace.entries):
            return True
        self._error(
            "The captures' file is no longer open in the project: open "
            "Capture ▸ Captures on it again."
        )
        return False

    def _play_in_emulator(self) -> None:
        s = self._capture_open()
        if s is None:
            return
        if s.playing:
            self.statusBar().showMessage("Already playing.", 4000)
            return
        if not self._capture_ensure_emulator(s):
            return
        dialog = CaptureSetupWindow(
            s.setup,
            s.console,
            len(s.rom),
            self._capture_shift(),
            self.address_spelling,
            self,
        )
        if dialog.exec() != CaptureSetupWindow.DialogCode.Accepted:
            return
        try:
            s.set_setup(dialog.setup)
            s.play()
        except (OSError, CaptureError) as e:
            self._error(f"The emulator could not be started: {e}")
            return
        self._capture_timer.start()
        self._capture_refresh()
        self.statusBar().showMessage(
            f"Playing in {s.emulator.name}: pause it to capture the text on screen.",
            8000,
        )

    def _capture_stop_playing(self) -> None:
        s = self._game_capture
        if s is not None and (s.playing or s.listener is not None):
            s.stop_playing()
            self._capture_refresh()

    def _show_captures(self) -> None:
        if self._capture_open() is not None:
            self.captures_dock.show()
            self.captures_dock.raise_()

    def _capture_changed(self, cap: Capture) -> None:
        self._capture_dirty = True
        if cap.state == DONE:
            self._capture_review_due = True

    def _capture_said(self, text: str) -> None:
        self._capture_dirty = True
        self.statusBar().showMessage(text, 8000)

    def _capture_tick(self) -> None:
        s = self._game_capture
        if s is None:
            self._capture_timer.stop()
            return
        s.advance(TICK_BUDGET)
        if self._capture_side is not None:
            self._capture_side_step()
        for cap in s.captures:
            if cap.needs_text and cap.id not in self._capture_prompted:
                self._capture_prompted.add(cap.id)
                self._capture_prompt(cap.id)
        if self._capture_review_due and self.captures_panel.tabs.currentIndex() == 1:
            self._capture_review_due = False
            self._capture_review()
        now = time.monotonic()
        if self._capture_dirty or (
            len(s.recent_messages()) != self._capture_messages_shown
        ):
            self._capture_refresh()
        elif s.job is not None and now - self._capture_shown > PROGRESS_SECONDS:
            self._capture_shown = now
            self.captures_panel.show_work()
        if not (s.playing or s.busy or s.listener or self._capture_side):
            self._capture_timer.stop()

    def _capture_refresh(self) -> None:
        s = self._game_capture
        if s is None:
            return
        self._capture_dirty = False
        self._capture_shown = time.monotonic()
        messages = s.recent_messages()
        self._capture_messages_shown = len(messages)
        if s.needs_emulator:
            messages = [
                *messages,
                "Captures wait for the emulator: set it in Capture ▸ Emulator Path…",
            ]
        self.captures_panel.show_captures(s.captures, messages)
        self.captures_panel.show_recorder(s.recorder_state, s.handed_over)
        self.stop_playing_action.setEnabled(s.playing or s.listener is not None)

    def _capture_find(self, cid: str) -> Capture | None:
        s = self._game_capture
        return s.capture(cid) if s is not None else None

    # -- the capture window

    def _capture_prompt(self, cid: str) -> None:
        cap = self._capture_find(cid)
        if cap is None:
            return
        win = self._capture_windows.get(cid)
        if win is None:
            win = CaptureWindow(cap, self)
            win.captured.connect(self._capture_submit)
            win.skipped.connect(self._capture_remove)
            win.destroyed.connect(lambda *_, c=cid: self._capture_windows.pop(c, None))
            self._capture_windows[cid] = win
        win.show()
        win.raise_()
        win.activateWindow()

    def _capture_submit(self, cid: str, text: str) -> None:
        s = self._game_capture
        cap = self._capture_find(cid)
        if cap is None:
            return
        bad = s.submit(cap, text)
        win = self._capture_windows.get(cid)
        if bad is not None:
            if win is not None:
                win.show_finding(bad)
            return
        if win is not None:
            win.close()
        self._capture_ensure_emulator(s)
        self._capture_timer.start()
        self._capture_refresh()

    def _capture_retry(self, cid: str) -> None:
        s = self._game_capture
        cap = self._capture_find(cid)
        if cap is not None:
            s.retry(cap)
            self._capture_ensure_emulator(s)
            self._capture_timer.start()
            self._capture_refresh()

    def _capture_stop(self) -> None:
        self._capture_side_stop()
        if self._game_capture is not None:
            self._game_capture.stop()
            self._capture_refresh()

    def _capture_remove_asked(self, cid: str) -> None:
        """Remove, from the dock: its files go for good, so it is asked."""
        cap = self._capture_find(cid)
        if cap is not None and self._ask(
            "Remove Capture",
            f"Remove the capture “{cap.text or cap.id}” and its files? This "
            "cannot be undone.",
        ):
            self._capture_remove(cid)

    def _capture_remove(self, cid: str) -> None:
        cap = self._capture_find(cid)
        if cap is not None:
            if self._capture_side is not None and self._capture_side.capture == cid:
                self._capture_side_stop()
            win = self._capture_windows.pop(cid, None)
            if win is not None:
                win.close()
            self._game_capture.skip(cap)
            self._capture_refresh()

    # -- review

    def _capture_load_review(self) -> None:
        """Read review.json: the marks, and what each acceptance added. A
        file that does not read is no review."""
        self._capture_states, self._capture_added = {}, {}
        try:
            path = os.path.join(self._game_capture.root, REVIEW)
            with open(path, encoding="utf-8") as fh:
                data = dict(json.load(fh))
            if isinstance(data.get("states"), dict):
                states, added = data["states"], dict(data.get("added") or {})
            else:  # the marks alone, as they once were kept
                states, added = data, {}
            self._capture_states = {str(k): str(v) for k, v in states.items()}
            self._capture_added = {
                str(k): v for k, v in added.items() if isinstance(v, dict)
            }
        except (OSError, ValueError, TypeError, AttributeError):
            self._capture_states, self._capture_added = {}, {}

    def _capture_mark(self, pid: str, state: str | None) -> None:
        """Remember a proposal accepted, rejected, or (None) neither."""
        if state is None:
            self._capture_states.pop(pid, None)
            self._capture_added.pop(pid, None)
        else:
            self._capture_states[pid] = state
        self._capture_save_review()

    def _capture_save_review(self) -> None:
        self._capture_show_proposals()
        try:
            write_json_atomic(
                os.path.join(self._game_capture.root, REVIEW),
                {"states": self._capture_states, "added": self._capture_added},
            )
        except OSError as e:
            self.statusBar().showMessage(f"The review could not be saved: {e}", 8000)

    def _capture_target_table(self) -> str:
        """The table proposals read through and entries go to: the reading's
        when the project has it, else :data:`NEW_TABLE`."""
        tid = self._current_table_id()
        if tid:
            e = self.workspace.entry_for_table(tid)
            if e is not None and e.table is not None:
                return tid
        return NEW_TABLE

    def _capture_target_entry(self):
        e = self.workspace.entry_for_table(self._capture_target)
        return e if e is not None and e.table is not None else None

    def _capture_shift(self) -> int:
        """Where the ROM image starts in the payload the file is read as."""
        s = self._game_capture
        doc = getattr(self._capture_file, "doc", None)
        if doc is None and self._entry is self._capture_file:
            doc = self._doc
        if doc is None:
            return s.console.header(s.file_bytes)
        at = doc.data.find(s.rom[:4096], 0, 4096 + 1024)
        return at if at >= 0 else s.console.header(s.file_bytes)

    def _capture_review(self) -> None:
        s = self._game_capture
        if s is None:
            return
        mappings = {
            mid: resolve_mapping(self.registry, mid)
            for mid in self.registry.ids(Stage.MAPPING)
        }
        combined = s.combined(mappings)
        self._capture_target = self._capture_target_table()
        self._capture_shift_used = self._capture_shift()
        self._capture_proposals = propose(
            combined, self._capture_target, self._capture_shift_used
        )
        self._capture_show_proposals()

    def _capture_holds(self, p: Proposal) -> bool:
        """Whether the project already has what the proposal proposes, as it
        proposes it: a block with its source, every entry as it is, a glyph's
        code; a conflict, once settled, with one of its readings."""
        if p.kind == BLOCK:
            return p.config is not None and self._capture_has_source(
                repr(p.config.source)
            )
        target = self._capture_target_entry()
        if target is None:
            return False
        have = target.table.entries
        if p.kind == ENTRIES:
            return all(
                have.get(e.bits) == e for e in relabelled(p.entries, target.table)
            )
        if p.kind == GLYPH and p.meaning is not None:
            return code_bits(p.meaning) in have
        if p.kind == CONFLICT and p.conflict is not None:
            if self._capture_states.get(p.id) != ACCEPTED:
                return False
            got = have.get(code_bits(p.conflict.code))
            return got is not None and any(
                got == entry_for(m) for m in p.conflict.choices.values()
            )
        return False

    def _capture_has_source(self, source: str) -> bool:
        return any(
            e.kind is EntryKind.BLOCK
            and e.config is not None
            and repr(e.config.source) == source
            for e in self.workspace.entries
        )

    def _capture_still_holds(self, added: dict) -> bool:
        """Whether the project still holds what an acceptance added."""
        if "source" in added:
            return self._capture_has_source(str(added["source"]))
        e = self.workspace.entry_for_table(str(added.get("table", "")))
        lines = added.get("entries")
        if e is None or e.table is None or not isinstance(lines, dict):
            return False
        have = e.table.entries
        return all(
            bits in have and format_entry(have[bits]) == line
            for bits, line in lines.items()
        )

    def _capture_state(self, p: Proposal) -> str:
        added = self._capture_added.get(p.id)
        if added is not None and self._capture_still_holds(added):
            return ACCEPTED
        if self._capture_holds(p):
            return ACCEPTED
        return REJECTED if self._capture_states.get(p.id) == REJECTED else ""

    def _capture_entries_added(self, entries: list[TableEntry]) -> dict:
        """What adding ``entries`` left in the target table, to know it by."""
        target = self._capture_target_entry()
        have = target.table.entries if target is not None else {}
        return {
            "table": self._capture_target,
            "entries": {
                e.bits: format_entry(have[e.bits]) for e in entries if e.bits in have
            },
        }

    def _capture_show_proposals(self, *_) -> None:
        if not self._capture_proposals and not self.captures_panel.proposals:
            return
        if self.captures_dock.isHidden():  # shown again when the dock is
            return
        self.captures_panel.show_proposals(
            self._capture_proposals,
            {p.id: self._capture_state(p) for p in self._capture_proposals},
            self._capture_target,
            self._capture_target_entry() is None,
        )

    def _capture_proposal(self, pid: str) -> Proposal | None:
        return next((p for p in self._capture_proposals if p.id == pid), None)

    def _capture_apply(self, p: Proposal, edit: bool) -> dict | None:
        """Add what a proposal proposes — after the user edits it, with
        ``edit`` — through the usual undoable edits; what was added, or None
        when nothing was."""
        if p.kind == BLOCK:
            cfg = replace(p.config, table_id=self._capture_target)
            name = p.name
            if edit:
                dialog = NewBlockDialog(
                    cfg,
                    self._table_items(),
                    self.registry.plugins(Stage.MAPPING),
                    lambda reading: self._count_strings(self._capture_file, reading),
                    spelling=self.address_spelling,
                    parent=self,
                )
                if dialog.exec() != NewBlockDialog.DialogCode.Accepted:
                    return None
                name, cfg = dialog.block_name(), dialog.config()
            self._add_block(self._capture_file, name, cfg)
            return {"source": repr(cfg.source)}
        entries = None
        if p.kind == ENTRIES:
            entries = self._capture_edit_entries(p.entries) if edit else p.entries
        elif p.kind == CONFLICT:
            readings = sorted(p.conflict.readings)
            choice, ok = QInputDialog.getItem(
                self,
                "Conflict",
                f"Which reading of {p.conflict.key}?",
                readings,
                0,
                False,
            )
            meaning = p.conflict.choices.get(choice) if ok else None
            entry = entry_for(meaning) if meaning is not None else None
            entries = [entry] if entry is not None else None
        if entries is None or not self._capture_add_entries(entries):
            return None
        return self._capture_entries_added(entries)

    def _capture_edit_entries(self, entries: list[TableEntry]) -> list | None:
        """The entries as the user edits them as table lines; asked again, with
        what was typed, while they do not read. None when cancelled."""
        body = "\n".join(format_entry(e) for e in entries)
        note = ""
        while True:
            text, ok = QInputDialog.getMultiLineText(
                self, "Edit Entries", f"{note}Table entries, one a line:", body
            )
            if not ok:
                return None
            try:
                table = parse_native(f"{HEADER}\n@table edit\n{text}\n").table
            except (MapcharError, ValueError) as e:
                body, note = text, f"Those entries do not read: {e}\n\n"
                continue
            return list(table.entries.values())

    def _capture_accept(self, pid: str, edit: bool = False) -> None:
        p = self._capture_proposal(pid)
        if p is None or self._capture_state(p) == ACCEPTED:
            return
        if not self._capture_usable():
            return
        added = self._capture_apply(p, edit)
        if added is not None:
            self._capture_added[pid] = added
            self._capture_mark(pid, ACCEPTED)

    def _capture_edit(self, pid: str) -> None:
        self._capture_accept(pid, edit=True)

    def _capture_accept_all(self) -> None:
        """Accept every block and entries proposal not yet reviewed, as one
        undo step: the entries first, so the table the blocks read is there.
        Each is looked at again when its turn comes, as one accepted before
        it may have added it already."""
        if not self._capture_usable():
            return
        todo = [p for p in self._capture_proposals if p.kind in (BLOCK, ENTRIES)]
        todo.sort(key=lambda p: p.kind != ENTRIES)
        with self._macro("Accept Captured Proposals"):
            for p in todo:
                if self._capture_state(p):
                    continue
                added = self._capture_apply(p, edit=False)
                if added is not None:
                    self._capture_added[p.id] = added
                    self._capture_states[p.id] = ACCEPTED
        self._capture_save_review()

    def _capture_reject(self, pid: str) -> None:
        self._capture_mark(pid, REJECTED)

    def _capture_unreview(self, pid: str) -> None:
        self._capture_mark(pid, None)

    def _capture_label(self, pid: str, text: str) -> None:
        p = self._capture_proposal(pid)
        if p is None or p.meaning is None or not self._capture_usable():
            return
        entry = entry_for(p.meaning, text)
        if entry is not None and self._capture_add_entries([entry]):
            self._capture_added[pid] = self._capture_entries_added([entry])
            self._capture_mark(pid, ACCEPTED)

    def _capture_add_entries(self, entries: list[TableEntry]) -> bool:
        """Put entries in the target table, or a new one; an entry that would
        change one the table has is shown first, and kept only if the user
        says so."""
        target = self._capture_target_entry()
        if target is None:
            table = Table(self._capture_target)
            for e in relabelled(entries, table):
                table.add(e)
            self._add_memory_table(table, f"{table.id}.tbl")
            self._tables_changed()
            self.statusBar().showMessage(
                f"{len(table.entries)} entries into a new table @{table.id}", 4000
            )
            return True
        before = target.table
        after = deepcopy(before)
        entries = relabelled(entries, before)
        changed = [
            (before.entries[e.bits], e)
            for e in entries
            if e.bits in before.entries and before.entries[e.bits] != e
        ]
        replace_them = False
        if changed:
            lines = "\n".join(
                f"{format_entry(old)}  →  {format_entry(new)}"
                for old, new in changed[:40]
            )
            replace_them = self._ask(
                "Table Entries",
                f"{len(changed)} entries would change what @{before.id} has:\n\n"
                f"{lines}\n\nReplace them? (No keeps the table's own)",
            )
        added = 0
        for e in entries:
            if e.bits in after.entries:
                if not replace_them or after.entries[e.bits] == e:
                    continue
                after.remove(e.bits)
            after.add(e)
            added += 1
        if added:
            self._push_command(TableCommand(self, target, deepcopy(before), after))
            self._tables_changed()
            message = f"{added} entries into @{before.id}"
        else:
            message = f"@{before.id} already has these entries"
        self.statusBar().showMessage(message, 4000)
        return True

    def _capture_goto(self, pid: str) -> None:
        p = self._capture_proposal(pid)
        if p is None or not self._capture_usable():
            return
        if self._entry is not self._capture_file:
            self._activate_entry(self._capture_file)
        self._go_to(p.offset)

    # -- runs in the game

    def _capture_side_busy(self) -> bool:
        if self._capture_side is None:
            return False
        self._error("Another run in the game is under way: stop it first.")
        return True

    def _capture_confirm(self, pid: str) -> None:
        s = self._game_capture
        p = self._capture_proposal(pid)
        if s is None or p is None or self._capture_side_busy():
            return
        caps = [s.capture(c) for c in p.captures]
        caps = [c for c in caps if c is not None and c.result is not None]
        if not caps:
            return
        cap = caps[0]
        r = cap.result
        if p.kind == BLOCK and r.sources:
            b = r.sources[0].byte
            writes = [(b, s.rom[b] ^ 0x01)]
            title = "The block's first character, changed"
        elif p.kind == GLYPH and p.meaning is not None and r.code_byte is not None:
            m = p.meaning
            if m.bits:
                self._error("A bit-packed code cannot be shown on its own.")
                return
            writes = [
                (r.code_byte + k, (m.code >> 8 * k) & 0xFF) for k in range(m.width)
            ]
            title = f"Code {m.key} in place of a typed character"
        else:
            self._error("This proposal has nothing to show in the game.")
            return
        if not self._capture_ensure_emulator(s):
            return

        def done(value) -> None:
            before, after = value
            ConfirmDialog(title, before, after, self).show()

        self._capture_side_start(
            _Side(
                s.confirm(cap, writes),
                cap.id,
                done,
                lambda message: self._error(f"Confirm in game failed: {message}"),
            ),
            "Running the capture again with the change…",
        )

    def _capture_sweep(self, pid: str) -> None:
        """Show Its Strings in Game: every string of a block's pointer table,
        through a capture's own slot."""
        s = self._game_capture
        p = self._capture_proposal(pid)
        if s is None or p is None or self._capture_side_busy():
            return
        table = table_of(p.config, self._capture_shift_used)
        if table is None:
            self._error("This block has no pointer table to show the strings of.")
            return
        caps = [s.capture(c) for c in p.captures]
        cap = next(
            (
                c
                for c in caps
                if c is not None
                and c.result is not None
                and own_slot(c.result, table) is not None
            ),
            None,
        )
        if cap is None:
            self._error(
                "No capture of this block found its pointer in the table, so its "
                "strings cannot be shown through it."
            )
            return
        if not self._capture_ensure_emulator(s):
            return
        dialog = StringShotsDialog(f"Strings of {p.name}", table.count, self)
        dialog.stop_requested.connect(self._capture_side_stop)
        dialog.more_requested.connect(
            lambda first: self._capture_sweep_run(dialog, cap, table, first)
        )
        dialog.show()
        self._capture_sweep_run(dialog, cap, table, 0)

    def _capture_sweep_run(self, dialog, cap: Capture, table, first: int) -> None:
        s = self._game_capture
        if s is None or s.capture(cap.id) is not cap:
            dialog.fail("its capture is no longer open")
            return
        if self._capture_side_busy():
            return
        dialog.start(first, min(table.count, first + MAX_STRINGS))
        self._capture_side_start(
            _Side(
                s.sweep(cap, table, first),
                cap.id,
                dialog.finish,
                dialog.fail,
                dialog.add_shot,
                dialog.stopped,
            ),
            "Showing the table's strings in the game…",
        )

    def _capture_side_start(self, side: _Side, text: str) -> None:
        self._capture_side = side
        self.captures_panel.set_side(text)
        self.statusBar().showMessage(text, 4000)
        self._capture_timer.start()

    def _capture_side_step(self) -> None:
        side = self._capture_side
        end = time.monotonic() + TICK_BUDGET
        try:
            while time.monotonic() < end and self._capture_side is side:
                y = next(side.step)
                if isinstance(y, tuple) and side.shot is not None:
                    side.shot(y)
                elif y is WAIT:
                    break
        except StopIteration as stop:
            self._capture_side_end(side)
            side.done(stop.value)
        except Exception as e:  # noqa: BLE001 - reported, never raised into Qt
            self._capture_side_end(side)
            side.failed(str(e) or type(e).__name__)

    def _capture_side_end(self, side: _Side) -> None:
        if self._capture_side is side:
            self._capture_side = None
            self.captures_panel.set_side(None)

    def _capture_side_stop(self) -> None:
        """Stop a run in the game, its emulator with it."""
        side = self._capture_side
        if side is None:
            return
        self._capture_side_end(side)
        side.step.close()
        if side.stopped is not None:
            side.stopped()
