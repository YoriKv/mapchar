"""Window size, position and panel arrangement, remembered between runs.

The layout a user arrives at is *their* answer to a question the defaults can
only guess at: how wide the Files list has to be for the names they work with,
whether the hex dump is worth standing space, which of the tabbed pair sits in
front. Rebuilding that answer every launch is a small tax paid dozens of times,
so it is stored — app-wide in ``QSettings``, beside the theme, and never in the
project file: a project restores the editing *session*, not the window chrome,
and the same project opened on a laptop should not drag a desktop's window
across.

**Written on a short delay, not at quit.** Two reasons, and the first is that a
dock separator dragged to a new width emits no signal at all — there is nothing
to hang a save off but the resize itself, which arrives in a stream while the
mouse is down. So the events arm a timer and the timer does the writing, which
coalesces a drag into one write. The second is that a layout is worth keeping
even when the app does not get a clean shutdown; a quit-time save is the one that
loses everything to a crash.

Qt does the serialising in both halves: ``saveGeometry``/``saveState`` are its own
formats, opaque here on purpose, and they carry the things a hand-rolled record
forgets — which screen the window was on, whether it was maximised, dock tab
order, which tab was in front, floating docks and their sizes. What this adds is
when to call them, and a way back for a user who has dragged a panel somewhere
they did not mean to.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QEvent, QObject, QTimer
from PySide6.QtWidgets import QDockWidget, QMainWindow, QWidget

from mapchar.ui import settings

LAYOUT_VERSION = 1
"""Passed to ``saveState``/``restoreState`` so a stored arrangement only ever
reaches the build that can make sense of it.

Bump it when a change would make an old state *wrong* rather than merely
incomplete — a dock split in two, a panel moved out of a dock — and Qt drops the
stale state for the defaults instead of restoring half an arrangement. A dock
*added* needs no bump: an unknown one simply stays where its own code put it.
"""

SAVE_DELAY_MS = 400
"""Long enough that a separator drag or a window resize is one write rather than
one per frame, short enough that a layout survives a crash a moment later."""

_LAYOUT_EVENTS = frozenset(
    {
        QEvent.Type.Resize,
        QEvent.Type.Move,
        QEvent.Type.Show,
        QEvent.Type.Hide,
    }
)
"""What counts as the layout having moved. Show and Hide are in it for the panel
toggles, which change what a restore has to put back as much as a drag does."""


def _stored_bytes(value: object) -> QByteArray | None:
    """A settings value back as bytes Qt will take, or ``None`` if there is none.

    The backends hand the same value back in different shapes — the registry
    stores a blob and returns one, the INI file writes ``@ByteArray(...)`` and
    decodes it — and a store written by another build may hold something else
    under the key. Anything that is not bytes is treated as absent, which lands
    on the defaults rather than on an exception at startup.
    """
    if isinstance(value, QByteArray):
        return value if not value.isEmpty() else None
    if isinstance(value, (bytes, bytearray)):
        return QByteArray(bytes(value)) if value else None
    return None


class WindowLayout(QObject):
    """Remembers one window's geometry, and a main window's docks with it.

    Parented to the window it watches, so it dies with it and its pending write
    dies with it too. ``key`` is the settings prefix this window stores under —
    one per window kind, since two of them on screen at once are the same window
    to a user who comes back to it.

    Construct it **after** the window is built and **before** anything is
    restored: it takes the arrangement it finds as the factory default, which is
    what :meth:`reset` puts back.
    """

    def __init__(self, window: QWidget, key: str) -> None:
        super().__init__(window)
        self._window = window
        self._key = key
        # Nothing is written until a restore has happened, so a window that is
        # still assembling cannot overwrite the layout it is about to read.
        self._live = False
        self._default_state = (
            window.saveState(LAYOUT_VERSION)
            if isinstance(window, QMainWindow)
            else None
        )
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(SAVE_DELAY_MS)
        self._timer.timeout.connect(self.save)
        window.installEventFilter(self)
        # The docks as well as the window: dragging a separator resizes *them*
        # and leaves the window exactly as it was, so watching the window alone
        # would miss the most common layout change there is.
        for dock in window.findChildren(QDockWidget):
            dock.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        # Armed by the first event of a burst and *not* pushed back by the rest.
        # Restarting it on each one would coalesce just as well while a drag was
        # in progress, but a window that never stops emitting layout events —
        # an ordinary state under some window managers — would then defer the
        # write forever. A save that always lands within the delay of the first
        # change cannot be starved; whatever comes after arms it again, so the
        # last word is still written.
        if event.type() in _LAYOUT_EVENTS and not self._timer.isActive():
            self._timer.start()
        return False  # watched, never intercepted

    def restore(self) -> bool:
        """Apply the stored layout, and start recording from here on.

        Silent where there is nothing stored, which is what leaves a first run on
        the defaults the window built for itself. Answers whether a geometry was
        found, which is what a tool window needs to know before placing itself
        beside the main window on its first show: that placement is for a window
        nobody has put anywhere yet, and running it over a remembered position
        would throw the position away every launch.
        """
        store = settings()
        geometry = _stored_bytes(store.value(f"{self._key}/geometry"))
        if geometry is not None:
            self._window.restoreGeometry(geometry)
        state = _stored_bytes(store.value(f"{self._key}/state"))
        if state is not None and isinstance(self._window, QMainWindow):
            self._window.restoreState(state, LAYOUT_VERSION)
        self._live = True
        return geometry is not None

    def save(self) -> None:
        """Write the layout now, rather than when the timer would have."""
        if not self._live:
            return
        self._timer.stop()  # this write covers whatever armed it
        store = settings()
        store.setValue(f"{self._key}/geometry", self._window.saveGeometry())
        if isinstance(self._window, QMainWindow):
            store.setValue(f"{self._key}/state", self._window.saveState(LAYOUT_VERSION))

    def reset(self) -> None:
        """Put the panels back where a fresh install has them.

        The way back out of a layout a user cannot undo by hand: a dock dragged
        into a stack it does not belong in, or shrunk past the point where its
        own separator can be grabbed. The window's size and position are left
        alone — this is the panels' arrangement, and moving the window as well
        would answer a question nobody asked.
        """
        if self._default_state is None or not isinstance(self._window, QMainWindow):
            return
        self._window.restoreState(self._default_state, LAYOUT_VERSION)
        self.save()  # the layout on screen is the layout that is stored


def remember_layout(window: QWidget, key: str) -> WindowLayout:
    """Give one tool window a remembered geometry, under ``key``.

    The one line a tool window needs at the end of its ``__init__``: a
    :class:`WindowLayout` of its own, restored at once so the window is already
    where the user left it the first time it is shown. Held by the caller so the
    object lives as long as the window (it is parented to it, so it also dies
    with it).
    """
    layout = WindowLayout(window, key)
    layout.restore()
    return layout


__all__ = [
    "LAYOUT_VERSION",
    "SAVE_DELAY_MS",
    "WindowLayout",
    "remember_layout",
]
