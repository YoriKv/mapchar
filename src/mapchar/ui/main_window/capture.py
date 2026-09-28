"""Capture: Play in Emulator and its setup window, the capture window, the
Captures dock and its Review tab, and the timer that advances the session.

The session (:mod:`mapchar.capture.session`) does the work in emulator
processes; a timer here advances it a bounded step at a time, so the window
never waits on an emulator. An accepted proposal becomes an ordinary block or
table entries through the usual undoable edits.
"""

from __future__ import annotations

import json
import os
import time
from copy import deepcopy

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDockWidget, QFileDialog, QInputDialog

from mapchar.capture.combine import Meaning
from mapchar.capture.consoles import detect
from mapchar.capture.emulator import Mesen
from mapchar.capture.proposals import (
    BLOCK,
    CONFLICT,
    ENTRIES,
    GLYPH,
    Proposal,
    entry_for,
    propose,
    relabelled,
)
from mapchar.capture.protocol import WAIT
from mapchar.capture.session import Capture, Session, capture_root
from mapchar.core.table import Table, TableEntry
from mapchar.plugins.base import Stage
from mapchar.plugins.registry import resolve_mapping
from mapchar.project.formats.table_native import HEADER, format_entry, parse_native
from mapchar.ui.block_dialog import NewBlockDialog
from mapchar.ui.capture import (
    CaptureSetupWindow,
    CapturesPanel,
    CaptureWindow,
    ConfirmDialog,
)
from mapchar.ui.undo_commands import TableCommand

EMULATOR_KEY = "capture/emulator"
"""QSettings key for the emulator's path — per machine, never the project."""

TICK_MS = 30
"""How often the session is advanced."""

TICK_BUDGET = 0.02
"""Seconds of work per tick."""

REVIEW = "review.json"
"""Which proposals were accepted or rejected, in the captures' folder."""

NEW_TABLE = "captured"
"""The table accepted entries go to when the reading has none."""


class CaptureMixin:
    """Capture from a running game.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    _game_capture: Session | None = None
    _capture_file = None
    """The file entry the session plays."""
    _capture_side = None
    """Confirm in game while it runs: ``(title, step)``."""
    _capture_proposals: list[Proposal] = []
    _capture_states: dict[str, str] = {}
    _capture_windows: dict = {}
    _capture_prompted: set = set()
    _capture_shown = 0.0

    def _build_capture(self) -> None:
        self.captures_panel = CapturesPanel()
        dock = QDockWidget("Captures", self)
        dock.setObjectName("captures_dock")
        dock.setWidget(self.captures_panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        dock.hide()
        self.captures_dock = dock
        self._capture_windows = {}
        self._capture_prompted = set()
        self._capture_states = {}
        self._capture_proposals = []
        self._capture_timer = QTimer(self)
        self._capture_timer.setInterval(TICK_MS)
        self._capture_timer.timeout.connect(self._capture_tick)
        p = self.captures_panel
        p.text_requested.connect(self._capture_prompt)
        p.retry_requested.connect(self._capture_retry)
        p.stop_requested.connect(self._capture_stop)
        p.remove_requested.connect(self._capture_remove)
        p.review_requested.connect(self._capture_review)
        p.accept_requested.connect(self._capture_accept)
        p.edit_requested.connect(self._capture_edit)
        p.reject_requested.connect(self._capture_reject)
        p.label_requested.connect(self._capture_label)
        p.confirm_requested.connect(self._capture_confirm)

    # -- the session

    def _capture_emulator(self) -> Mesen | None:
        path = str(self.settings.value(EMULATOR_KEY, "") or "")
        emu = Mesen(path or None)
        if emu.available():
            return emu
        return self._capture_choose_emulator()

    def _capture_choose_emulator(self) -> Mesen | None:
        path, _ = QFileDialog.getOpenFileName(self, "Where is Mesen 2?")
        if not path:
            return None
        self.settings.setValue(EMULATOR_KEY, path)
        emu = Mesen(path)
        if self._game_capture is not None:
            self._game_capture.emulator = emu
        return emu

    def _capture_open(self) -> Session | None:
        """The session for the file on screen, made when there is none."""
        file_entry = self._current_file()
        if file_entry is None or not file_entry.path:
            self._error("Open a ROM first.")
            return None
        s = self._game_capture
        if s is not None and s.rom_path == file_entry.path:
            return s
        with open(file_entry.path, "rb") as fh:
            data = fh.read()
        console = detect(file_entry.path, data)
        if console is None:
            self._error(
                "Capture knows the SNES, NES and Game Boy Advance; this file is "
                "none of them."
            )
            return None
        emu = self._capture_emulator()
        if emu is None:
            return None
        if s is not None:
            s.close()
        root = capture_root(self.project_path, file_entry.path)
        s = Session(
            root, file_entry.path, console, emu, on_change=self._capture_changed
        )
        self._game_capture, self._capture_file = s, file_entry
        self._capture_states = self._capture_load_states()
        self._capture_prompted = {c.id for c in s.captures}
        self.captures_dock.show()
        self._capture_refresh(force=True)
        self._capture_timer.start()
        return s

    def _play_in_emulator(self) -> None:
        s = self._capture_open()
        if s is None:
            return
        if s.playing:
            self.statusBar().showMessage(f"Already playing in {s.emulator.name}.", 4000)
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
        s.set_setup(dialog.setup)
        try:
            s.play()
        except OSError as e:
            self._error(f"The emulator could not be started: {e}")
            return
        # The timer stops itself while the setup window is open, with nothing
        # to watch yet: the recorder's connection is only read while it runs.
        self._capture_timer.start()
        self.statusBar().showMessage(
            f"Playing in {s.emulator.name}: pause it to capture the text on screen.",
            8000,
        )

    def _show_captures(self) -> None:
        if self._capture_open() is not None:
            self.captures_dock.raise_()

    def _capture_changed(self, cap: Capture) -> None:
        self._capture_dirty = True

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
        busy = any(c.state in ("replaying", "finding", "tracing") for c in s.captures)
        now = time.monotonic()
        if getattr(self, "_capture_dirty", False) or (
            busy and now - self._capture_shown > 0.5
        ):
            self._capture_refresh()
        if not (s.playing or s.busy or s.listener or self._capture_side):
            self._capture_timer.stop()

    def _capture_refresh(self, force: bool = False) -> None:
        s = self._game_capture
        if s is None:
            return
        self._capture_dirty = False
        self._capture_shown = time.monotonic()
        messages = list(s.messages[-3:])
        self.captures_panel.show_captures(s.captures, messages)

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
        cap = self._capture_find(cid)
        if cap is None:
            return
        bad = self._game_capture.submit(cap, text)
        win = self._capture_windows.get(cid)
        if bad is not None:
            if win is not None:
                win.show_finding(bad)
            return
        if win is not None:
            win.close()
        self._capture_timer.start()
        self._capture_refresh()

    def _capture_retry(self, cid: str) -> None:
        cap = self._capture_find(cid)
        if cap is not None:
            self._game_capture.retry(cap)
            self._capture_timer.start()
            self._capture_refresh()

    def _capture_stop(self) -> None:
        if self._game_capture is not None:
            self._game_capture.stop()
            self._capture_refresh()

    def _capture_remove(self, cid: str) -> None:
        cap = self._capture_find(cid)
        if cap is not None:
            win = self._capture_windows.pop(cid, None)
            if win is not None:
                win.close()
            self._game_capture.skip(cap)
            self._capture_refresh()

    # -- review

    def _capture_load_states(self) -> dict[str, str]:
        try:
            path = os.path.join(self._game_capture.root, REVIEW)
            with open(path, encoding="utf-8") as fh:
                return dict(json.load(fh))
        except (OSError, ValueError):
            return {}

    def _capture_mark(self, pid: str, state: str) -> None:
        self._capture_states[pid] = state
        path = os.path.join(self._game_capture.root, REVIEW)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(self._capture_states, fh)
        self.captures_panel.show_proposals(
            self._capture_proposals, self._capture_states
        )

    def _capture_table_id(self) -> str:
        return self._current_table_id() or NEW_TABLE

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
        self._capture_proposals = propose(
            combined, self._capture_table_id(), self._capture_shift()
        )
        self.captures_panel.show_proposals(
            self._capture_proposals, self._capture_states
        )

    def _capture_proposal(self, pid: str) -> Proposal | None:
        return next((p for p in self._capture_proposals if p.id == pid), None)

    def _capture_accept(self, pid: str) -> None:
        p = self._capture_proposal(pid)
        if p is None:
            return
        if p.kind == BLOCK:
            self._add_block(self._capture_file, p.name, p.config)
        elif p.kind == ENTRIES:
            if not self._capture_add_entries(p.entries):
                return
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
            if not ok:
                return
            kind, _, text = choice.partition(":")
            m = p.conflict.code
            chosen = Meaning(m.code, m.width, kind, text, m.params, m.bits)
            entry = entry_for(chosen)
            if entry is None or not self._capture_add_entries([entry]):
                return
        self._capture_mark(pid, "accepted")

    def _capture_reject(self, pid: str) -> None:
        self._capture_mark(pid, "rejected")

    def _capture_label(self, pid: str, text: str) -> None:
        p = self._capture_proposal(pid)
        if p is None or p.meaning is None:
            return
        entry = entry_for(p.meaning, text)
        if entry is not None and self._capture_add_entries([entry]):
            self._capture_mark(pid, "accepted")

    def _capture_edit(self, pid: str) -> None:
        p = self._capture_proposal(pid)
        if p is None:
            return
        if p.kind == BLOCK:
            dialog = NewBlockDialog(
                p.config,
                self._table_items(),
                self.registry.plugins(Stage.MAPPING),
                lambda reading: self._count_strings(self._capture_file, reading),
                spelling=self.address_spelling,
                parent=self,
            )
            if dialog.exec() != NewBlockDialog.DialogCode.Accepted:
                return
            self._add_block(self._capture_file, dialog.block_name(), dialog.config())
            self._capture_mark(pid, "accepted")
        elif p.kind == ENTRIES:
            body = "\n".join(format_entry(e) for e in p.entries)
            text, ok = QInputDialog.getMultiLineText(
                self, "Edit Entries", "Table entries, one a line:", body
            )
            if not ok:
                return
            try:
                table = parse_native(f"{HEADER}\n@table edit\n{text}\n").table
            except ValueError as e:
                self._error(f"Those entries do not read: {e}")
                return
            if self._capture_add_entries(list(table.entries.values())):
                self._capture_mark(pid, "accepted")

    def _capture_add_entries(self, entries: list[TableEntry]) -> bool:
        """Put entries in the reading's table, or a new one; an entry that
        would change one the table has is shown first, and kept only if the
        user says so."""
        table_id = self._current_table_id()
        target = self.workspace.entry_for_table(table_id or "") if table_id else None
        if target is None or target.table is None:
            existing = self.workspace.entry_for_table(NEW_TABLE)
            if existing is None or existing.table is None:
                table = Table(NEW_TABLE)
                for e in entries:
                    table.add(e)
                self._add_memory_table(table, f"{NEW_TABLE}.tbl")
                self._tables_changed()
                return True
            target = existing
        before = target.table
        after = deepcopy(before)
        entries = relabelled(entries, before)
        changed = [
            (before.entries[e.bits], e)
            for e in entries
            if e.bits in before.entries and before.entries[e.bits] != e
        ]
        replace = False
        if changed:
            lines = "\n".join(
                f"{format_entry(old)}  →  {format_entry(new)}"
                for old, new in changed[:40]
            )
            replace = self._ask(
                "Table Entries",
                f"{len(changed)} entries would change what @{before.id} has:\n\n"
                f"{lines}\n\nReplace them? (No keeps the table's own)",
            )
        added = 0
        for e in entries:
            if e.bits in after.entries:
                if not replace or after.entries[e.bits] == e:
                    continue
                after.remove(e.bits)
            after.add(e)
            added += 1
        if added:
            self._push_command(TableCommand(self, target, deepcopy(before), after))
            self._tables_changed()
        self.statusBar().showMessage(f"{added} entries into @{before.id}", 4000)
        return True

    # -- Confirm in game

    def _capture_confirm(self, pid: str) -> None:
        s = self._game_capture
        p = self._capture_proposal(pid)
        if s is None or p is None or self._capture_side is not None:
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
        self._capture_side = (title, s.confirm(cap, writes))
        self.statusBar().showMessage("Running the capture again with the change…", 4000)
        self._capture_timer.start()

    def _capture_side_step(self) -> None:
        title, step = self._capture_side
        end = time.monotonic() + TICK_BUDGET
        try:
            while time.monotonic() < end:
                if next(step) is WAIT:
                    break
        except StopIteration as stop:
            self._capture_side = None
            before, after = stop.value
            ConfirmDialog(title, before, after, self).show()
        except Exception as e:  # noqa: BLE001 - reported, never raised into Qt
            self._capture_side = None
            self._error(f"Confirm in game failed: {e}")

    def _capture_close(self) -> None:
        if self._game_capture is not None:
            self._game_capture.close()
            self._game_capture = None
