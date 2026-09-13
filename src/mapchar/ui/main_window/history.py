"""Where the view has been, and stepping back and forth through it.

A session wanders: a bookmark's target, a block's parent file, the entry an undo
reverted in, the block a search result turned into — several gestures move the
view somewhere the user did not pick from the Files list, and finding the way
back by hand means remembering which row it was. So the window keeps a **visit
trail** of the entries that have been on screen and walks it like a browser's
history: Back returns to the previous one, Forward retraces, and visiting
somewhere new from the middle of the trail drops whatever lay ahead.

It is **session state, not project state** — a trail of live ``Entry`` objects,
never written to the ``.mapchar`` file, the same reasoning that keeps the undo
stack out of it. Two consequences: closing an entry takes its slots out of the
trail, because they cannot be returned to, and opening a project replaces the
workspace wholesale, which wipes the trail on the way through.

Recorded from the workspace's ``on_current_changed`` rather than at the
activation call sites: every way the view can move ends there, and it is the only
place that is true of.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QApplication, QMenu

from mapchar.project.workspace import Entry, EntryKind

_TRAIL_LIMIT = 64
"""Deep enough that a session's worth of hopping stays retraceable, bounded so a
long one does not pin every entry it ever showed. The oldest visit falls off."""


class HistoryMixin:
    """The visit trail, its two actions, and the mouse buttons that drive them.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _init_history(self) -> None:
        """Seed the trail and its two actions — before anything can make an entry
        current, since the first visit arms them.

        The actions carry **real shortcuts**, unlike the navigation keys beside
        them: Alt+arrows carry the Alt modifier
        :meth:`~mapchar.ui.main_window.navigation.NavigationMixin._handle_nav_key`
        deliberately declines, so they reach the shortcut system instead of the
        filter and cannot be stolen from a focused text input.
        """
        # The entries visited, oldest first, and where in them the view sits.
        # _history[_history_pos] is always the entry on screen; -1 is an empty
        # trail, meaning nothing has been shown yet.
        self._history: list[Entry] = []
        self._history_pos = -1
        # Set while a Back/Forward step activates its target, so the activation
        # it causes is not recorded as a new visit — the whole point of a trail
        # is that walking it does not rewrite it.
        self._history_walking = False

        self._back_action = QAction("&Back", self)
        self._back_action.setShortcut(QKeySequence("Alt+Left"))
        self._back_action.triggered.connect(lambda: self._history_step(-1))
        self._forward_action = QAction("&Forward", self)
        self._forward_action.setShortcut(QKeySequence("Alt+Right"))
        self._forward_action.triggered.connect(lambda: self._history_step(1))
        self._sync_history_actions()

    def _add_history_actions(self, menu: QMenu) -> None:
        """Put Back and Forward at the head of ``menu``, then a separator — the
        two rows that move between entries rather than within one."""
        menu.addAction(self._back_action)
        menu.addAction(self._forward_action)
        menu.addSeparator()

    def _sync_history_actions(self) -> None:
        """Arm each direction only where there is a visit, and name where it goes."""
        for action, delta, way in (
            (self._back_action, -1, "Back"),
            (self._forward_action, 1, "Forward"),
        ):
            target = self._history_target(delta)
            action.setEnabled(target is not None)
            where = (
                f"{way} to {target.name}"
                if target is not None
                else f"Nothing to go {way.lower()} to"
            )
            button = "Mouse 4" if delta < 0 else "Mouse 5"
            action.setToolTip(f"{where}\nAlso {button} (the browser {way} button)")

    def _history_target(self, delta: int) -> Entry | None:
        """The entry ``delta`` steps along the trail, or ``None`` at that end."""
        at = self._history_pos + delta
        if 0 <= at < len(self._history):
            return self._history[at]
        return None

    def _history_step(self, delta: int) -> None:
        """Move one visit back (-1) or forward (+1); a no-op at either end.

        The position only moves once the view actually did: an entry whose file
        has gone fails to activate, and swallowing the step would leave Back
        pointing somewhere the user never got to.
        """
        if self._scanning:
            return  # a running scan owns the view
        target = self._history_target(delta)
        if target is None:
            return
        self._history_walking = True
        try:
            self._activate_entry(target)
        finally:
            self._history_walking = False
        if self.workspace.current is target:
            self._history_pos += delta
            self._sync_history_actions()

    def _handle_history_mouse(self, event) -> bool:
        """Route a back/forward mouse button to the trail; True if consumed.

        Consumes the whole click — press, double-click and release — even when
        the trail cannot move that way, because these two buttons mean nothing
        else in this window and a widget receiving half a click is worse than a
        press that did nothing. A double-click steps twice, as a browser does.
        """
        delta = {
            Qt.MouseButton.BackButton: -1,
            Qt.MouseButton.ForwardButton: 1,
        }.get(event.button())
        if delta is None:
            return False
        if QApplication.activePopupWidget() is not None:
            return False  # an open menu gets its own clicks
        if event.type() in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonDblClick,
        ):
            self._history_step(delta)
        return True

    def _record_visit(self, entry: Entry | None) -> None:
        """Note ``entry`` as the newest visit, dropping any forward tail.

        Nothing-open (``None``) is not a visit but the absence of one. Re-showing
        the entry already on screen is not one either: consecutive duplicates
        would make Back a no-op that looks like a dead key.
        """
        if entry is None or self._history_walking:
            return
        # Only a kind that can *be* the view. A table or a glyph sheet opens in
        # a window of its own and a bookmark jumps somewhere else, so none of
        # them is a place Back could return to — and one can still land in
        # ``workspace.current`` as the neighbour of a closed row.
        if entry.kind not in (EntryKind.FILE, EntryKind.BLOCK):
            return
        if self._history_target(0) is entry:
            return
        del self._history[self._history_pos + 1 :]
        self._history.append(entry)
        del self._history[: max(0, len(self._history) - _TRAIL_LIMIT)]
        self._history_pos = len(self._history) - 1
        self._sync_history_actions()

    def _forget_visits(self, entry: Entry) -> None:
        """Drop every visit to ``entry`` — it is closing, so it cannot be
        returned to.

        Removing a slot from the middle can leave the same entry either side of
        the gap; those collapse into one, or Back would step onto the entry
        already shown and spend a keypress going nowhere.
        """
        kept: list[Entry] = []
        pos = self._history_pos
        for i, visited in enumerate(self._history):
            if visited is entry or (kept and kept[-1] is visited):
                if i <= pos:
                    pos -= 1  # a slot at or before the view's own vanished
            else:
                kept.append(visited)
        self._history = kept
        self._history_pos = max(-1, min(pos, len(kept) - 1))
        self._sync_history_actions()

    def _forget_all_visits(self) -> None:
        """Wipe the trail — what a project swap calls, beside clearing the stack.

        Not the workspace's ``on_reset``, which a reorder fires too: a project
        swap discards every entry the trail could name, so the new project opens
        on a fresh trail the same way it opens on a cleared undo stack, while a
        reorder leaves every entry exactly where the trail can still reach it
        (:mod:`mapchar.ui.main_window.projects`).
        """
        self._history = []
        self._history_pos = -1
        self._sync_history_actions()
