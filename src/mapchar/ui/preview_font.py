"""The system font the Preview draws through: one for the whole app.

A font is no longer a project entry — every block previews through the same
system family, stored in :func:`~mapchar.ui.settings` beside the theme.
:class:`PreviewFont` is the Qt side of it: the :class:`QFont` the renderer
draws with, and the measurements it freezes into a
:class:`~mapchar.core.font.Font` for the Qt-free layout engine.
"""

from __future__ import annotations

from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics

from mapchar.core.font import Font
from mapchar.core.tokens import Token
from mapchar.engines.layout import drawn_text
from mapchar.ui import setting_int, settings

FAMILY_KEY = "preview/font_family"
"""QSettings key for the preview font's family — per machine, never the project."""
SIZE_KEY = "preview/font_size"
"""QSettings key for its point size."""

DEFAULT_SIZE = 16
MIN_SIZE = 4
MAX_SIZE = 96


def default_family() -> str:
    """The family a machine with nothing stored previews in: its fixed-width
    one, since game text is drawn on a grid more often than not."""
    return QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()


class PreviewFont:
    """The app's preview font, and what layout needs measured from it.

    Measuring is per character and kept, so a long string costs one metrics
    call for each character nobody has measured yet.
    """

    def __init__(self) -> None:
        store = settings()
        family = str(store.value(FAMILY_KEY, "") or "") or default_family()
        self._family = family
        self._size = setting_int(SIZE_KEY, DEFAULT_SIZE, MIN_SIZE, MAX_SIZE)
        self._remeasure()

    # --- what it is -------------------------------------------------------

    @property
    def family(self) -> str:
        return self._family

    @property
    def size(self) -> int:
        return self._size

    @property
    def qfont(self) -> QFont:
        """The font the renderer draws with, at one pixel per box pixel."""
        return QFont(self._font)

    def set_font(self, family: str, size: int) -> None:
        """Choose the preview font, and remember it for the next run."""
        size = max(MIN_SIZE, min(MAX_SIZE, size))
        if family == self._family and size == self._size:
            return
        self._family, self._size = family, size
        store = settings()
        store.setValue(FAMILY_KEY, family)
        store.setValue(SIZE_KEY, size)
        self._remeasure()

    # --- what layout needs ------------------------------------------------

    def measured(self, *sources: list[Token] | str) -> Font:
        """A :class:`Font` that can measure everything ``sources`` draws."""
        for source in sources:
            self._measure(drawn_text(source))
        return Font(
            self._family,
            self._size,
            self._metrics.height(),
            self._metrics.ascent(),
            dict(self._advances),
            self._default_advance,
            frozenset(self._missing),
        )

    def _remeasure(self) -> None:
        self._font = QFont(self._family)
        self._font.setPointSize(self._size)
        self._metrics = QFontMetrics(self._font)
        self._default_advance = max(1, self._metrics.averageCharWidth())
        self._advances: dict[str, int] = {}
        self._missing: set[str] = set()
        # A space is measured whether or not anything drew one: wrapping asks
        # for its width before it has seen the text.
        self._measure([" "])

    def _measure(self, units: list[str]) -> None:
        for unit in units:
            if not unit or unit in self._advances:
                continue
            self._advances[unit] = self._metrics.horizontalAdvance(unit)
            # The base character answers for the whole grapheme: a combining
            # mark the family lacks is a blemish, not a missing character.
            if not self._metrics.inFontUcs4(ord(unit[0])):
                self._missing.add(unit)


_current: PreviewFont | None = None


def preview_font() -> PreviewFont:
    """The app's one preview font, made on first ask."""
    global _current
    current = _current
    if current is None:
        current = _current = PreviewFont()
    return current


def forget_preview_font() -> None:
    """Drop the cached font, so the next ask reads the settings again."""
    global _current
    _current = None


__all__ = [
    "FAMILY_KEY",
    "MAX_SIZE",
    "MIN_SIZE",
    "SIZE_KEY",
    "PreviewFont",
    "default_family",
    "forget_preview_font",
    "preview_font",
]
