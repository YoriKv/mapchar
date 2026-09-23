"""The Help menu's modal dialogs: the shortcut guide, the legend and About.

A hand-written list of keys is wrong the day after a shortcut moves, and nothing
tells you. So the guide is assembled from the **menu bar** — one section per
menu, every action that advertises a key, submenus flattened into their parent —
which means a shortcut added to a menu appears here with no second edit.

What a menu cannot hold is declared here instead, in :data:`DISPLAY_ONLY`: keys
that are deliberately *not* registered as shortcuts because they would then be
stolen from focused text inputs (the navigation keys go through an application
event filter — :mod:`mapchar.ui.main_window.navigation`), keys that belong to one
panel or tool window while it has focus, and mouse gestures, which no
``QKeySequence`` can spell.

An action whose label is rebuilt at runtime — Undo and Redo carry the name of the
command they would undo — sets a ``guideLabel`` property to pin what the guide
calls it.

The legend (:data:`LEGEND`) is the other kind of thing a menu cannot hold: every
colour and mark the Hex and Text views, the Hex panel, the Strings view, the
Files panel, the Table Editor, the Glossary and the Preview draw, each beside a
swatch painted the way the view paints it, from the same
:mod:`~mapchar.ui.theme` colours — so a tint that changes changes here too.

:func:`shortcut_sections` is separated from the dialog so the mapping can be
tested without opening a modal, which the offscreen platform can never answer.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TypeVar

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QAction, QColor, QKeySequence, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from mapchar import APP_NAME, __version__, resources
from mapchar.ui import marks, theme
from mapchar.ui.widgets import close_box, mono_font

_S = TypeVar("_S", bound="tuple[str, Sequence]")
"""A titled section — its rows are whatever the page lays out."""

AUTHOR = "Epi"
HOMEPAGE = "https://github.com/YoriKv/mapchar"

DISPLAY_ONLY: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "Main Window",
        (
            ("Open files", "Drop them on the window"),
            ("Choose what a dropped file opens as", "Ctrl+drop"),
        ),
    ),
    (
        "Hex and Text Views",
        (
            ("Page up / down", "PgUp / PgDn"),
            ("Row up / down, or a line in Text", "Up / Down"),
            ("Byte back / forward", "Left / Right, or - / +"),
            ("Start / end of the file or block", "Home / End"),
            ("Go to the typed address", "Enter in the address box"),
            ("Back / forward through visited entries", "Mouse 4 / Mouse 5"),
            ("Select bytes", "Drag over hex or text"),
            ("Extend the selection", "Shift+click"),
            ("Zoom the Text view, Hex panel or Original", "Ctrl+Wheel"),
            ("The Hex view's own menu", "Right-click"),
        ),
    ),
    (
        "Strings View",
        (
            ("Edit the selected cell", "F2, double-click, or start typing"),
            ("Edit the selected string whole", "The pane under the grid"),
            ("Commit and move to the next row", "Enter"),
            ("Commit and stay", "Ctrl+Enter"),
            ("Write the block's newline code", "Shift+Enter"),
            ("Complete a code", "[, then Enter or Tab"),
            ("Cancel the edit", "Esc"),
            ("Add the marked text to the glossary", "Right-click in the pane"),
            ("Show or hide columns", "Right-click a header"),
            ("Reorder columns", "Drag a header"),
            ("The rows' own menu", "Right-click"),
        ),
    ),
    (
        "Files Panel (while focused)",
        (
            ("Open the row", "Click, or Up / Down"),
            ("Extend the selection", "Shift+click / Ctrl+click"),
            ("Reorder the selected rows", "Alt+Up / Alt+Down, or drag"),
            ("Duplicate entries", "Ctrl+D"),
            ("Remove the selected entries", "Del"),
            ("Filter the list", "Ctrl+F"),
            ("Rename the row", "F2, or double-click"),
            ("The rows' own menu", "Right-click"),
        ),
    ),
    (
        "Glossary Panel",
        (
            ("Insert a term's translation", "Double-click or Enter, In this string"),
            ("Edit a term in place", "F2, double-click, or start typing"),
            ("The terms' own menu", "Right-click"),
        ),
    ),
    (
        "Find Bar and Find and Replace",
        (
            ("Find the next match", "Enter"),
            ("Find the previous match in the bar", "Shift+Enter"),
            ("Close Find and Replace", "Esc"),
        ),
    ),
    (
        "Table Editor",
        (
            ("Add the entry, or apply it to the row", "Enter in Key, Text or the line"),
            ("Edit a Text or Comment cell in place", "F2, or double-click"),
            ("Open any other cell's control in the form", "Double-click"),
            ("Sort by a column", "Click its header"),
            ("Choose the columns", "Right-click a header"),
            ("Remove the selected entries", "Del"),
            ("Filter the entries", "Ctrl+F"),
        ),
    ),
    (
        "Hex Panel",
        (
            ("Overtype the nibble under the caret", "0-9 / A-F"),
            ("Go to, find, or overtype", "Enter in the field"),
            ("Find the previous match", "Shift+Enter"),
        ),
    ),
    (
        "Tool Windows",
        (
            ("Close the window", "Esc"),
            ("Run the search", "Enter in the Search window's query"),
            ("Jump to a result", "Select its row"),
            ("Open a Project Strings row in its block", "Double-click, or Enter"),
        ),
    ),
)
"""Keys and gestures no menu action carries, by the surface they belong to."""


@dataclass(frozen=True)
class Swatch:
    """One legend sample, painted as the views paint it.

    ``tint`` is a chip behind ``text``; ``ink`` colours the text (the palette's
    text colour when ``None``); ``mark`` is one of the marks that are not a
    chip — ``"tick"`` for a token that prints nothing, ``"rule"`` for a string
    boundary, ``"notch"`` for text cut short, ``"box"`` for a missing glyph;
    ``small`` draws the text in the
    label face and its ink, and ``dim`` in the dimmed ink.
    """

    text: str = ""
    tint: QColor | None = None
    ink: QColor | None = None
    mark: str = ""
    small: bool = False
    dim: bool = False


LEGEND: tuple[tuple[str, tuple[tuple[Swatch, str], ...]], ...] = (
    (
        "Hex View",
        (
            (Swatch("A"), "Text a table entry matched"),
            (Swatch("tile60", small=True), "Text a table writes as a [name]"),
            (Swatch("line", tint=theme.TINT_CODE, small=True), "A control code"),
            (Swatch("end", tint=theme.TINT_END, small=True), "An end token"),
            (Swatch("kanji", tint=theme.TINT_SWITCH, small=True), "A table switch"),
            (
                Swatch(tint=theme.TINT_SWITCH, mark="tick"),
                "A token that prints nothing: a silent switch, a return, "
                "or bits read by a table's fallback",
            ),
            (
                Swatch("FF", tint=theme.TINT_RAW),
                "Bytes no table matches; a dim · in the text column",
            ),
            (
                Swatch("1F", tint=theme.TINT_POINTER),
                "A pointer; shown as Pointers, its text is →address, "
                "or the string with Follow pointers on",
            ),
            (Swatch("A", tint=theme.TINT_SELECTION), "The selected bytes"),
            (
                Swatch("52", tint=theme.TINT_STRUCTURE),
                "The compressed structure the Decompressed View is reading",
            ),
            (Swatch("A", mark="rule"), "The start of a string"),
            (Swatch("s↵"), "A line break inside a run of text"),
            (Swatch("s▪"), "A code inside a run of text"),
            (Swatch("Abc", mark="notch"), "Text cut short; hover for the whole"),
            (Swatch("000100", dim=True), "The row's address"),
        ),
    ),
    (
        "Text View",
        (
            (
                Swatch("[line]"),
                "A code, with Show codes on; a newline code ends the line",
            ),
            (
                Swatch("[$FF]"),
                "A byte no table matches, with Show unknown on; "
                "[%bits] for a tail shorter than a byte",
            ),
        ),
    ),
    (
        "Hex Panel",
        (
            (Swatch("A", tint=theme.TINT_SELECTION), "The selected bytes"),
            (Swatch("."), "A byte with no printable ASCII"),
        ),
    ),
    (
        "Strings View",
        (
            (Swatch("untouched"), "The bytes still say the original"),
            (Swatch("edited"), "The bytes say something else: a translation"),
            (
                Swatch("review", ink=theme.WARNING_INK),
                "Marked for a second look, by hand or by an import",
            ),
            (
                Swatch("done", ink=theme.DONE_INK),
                "Marked done, by hand or by an import",
            ),
            (
                Swatch("unwritten", ink=theme.ERROR_INK),
                "A translation the bytes refused, kept unwritten",
            ),
            (
                Swatch("Abc", ink=theme.ERROR_INK),
                "An unwritten translation; hover for why",
            ),
            (
                Swatch("Abc", ink=theme.WARNING_INK),
                "A translation that differs from the glossary; hover for the terms",
            ),
            (Swatch("12 / 16"), "Bytes the string takes, and the room it has"),
            (Swatch("×3"), "Strings of the block sharing this original"),
            (Swatch("↵"), "A line break in the Original or Translation"),
            (Swatch("1F40 1F42"), "The addresses of the pointers to the string"),
        ),
    ),
    (
        "Files Panel",
        (
            (Swatch("?", tint=theme.NOTICE_WASH), "The file is missing"),
            (
                Swatch("!", tint=theme.NOTICE_WASH),
                "A read gave something up; hover for what",
            ),
            (Swatch("●"), "Unsaved edits"),
        ),
    ),
    (
        "Table Editor and Glossary",
        (
            (Swatch("41=A", dim=True), "An entry from an included table"),
            (
                Swatch("Abc", ink=theme.ERROR_INK),
                "A glossary translation the block's table cannot encode",
            ),
        ),
    ),
    (
        "Preview",
        (
            (Swatch("A", tint=theme.TINT_END), "Text past the box's edge"),
            (Swatch(mark="box"), "A character the font has no glyph for"),
        ),
    ),
)
"""Every colour and mark the views draw, by the surface that draws it."""


def _key_text(action: QAction) -> str:
    """The key ``action`` advertises, or ``""`` when it has none.

    The primary sequence only: Qt's ``StandardKey`` lists carry every historical
    and media-key alternate for a role, and showing them all would bury the
    binding people actually use.
    """
    return action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)


def _label_text(action: QAction) -> str:
    """``action``'s name as a reader sees it — Qt renders ``&`` as an underline."""
    pinned = action.property("guideLabel")
    if pinned:
        return str(pinned)
    return action.text().replace("&&", "\0").replace("&", "").replace("\0", "&").strip()


def submenus(owner: QWidget) -> dict[QAction, QMenu]:
    """Every menu below ``owner``, keyed by the action that opens it.

    The walk goes through the widget tree rather than ``QAction.menu()``: under
    PySide6 that call hands its result's ownership to Python, so the wrapper it
    returns deletes the submenu when it is collected — and a menu kept on the
    window (Open Recent) is then gone the next time anything reaches for it.
    """
    return {menu.menuAction(): menu for menu in owner.findChildren(QMenu)}


def _menu_entries(menu: QMenu, below: dict[QAction, QMenu]) -> list[tuple[str, str]]:
    """Every ``(label, keys)`` pair in ``menu``, submenus flattened in place.

    Actions with no key are dropped: they are reachable by mouse and the menu
    itself is their documentation.
    """
    entries: list[tuple[str, str]] = []
    for action in menu.actions():
        if action.isSeparator():
            continue
        keys = _key_text(action)
        if keys:
            entries.append((_label_text(action), keys))
        submenu = below.get(action)
        if submenu is not None:
            entries.extend(_menu_entries(submenu, below))
    return entries


def shortcut_sections(window) -> list[tuple[str, list[tuple[str, str]]]]:
    """The guide's contents: one section per menu, then the declared surfaces."""
    bar = window.menuBar()
    below = submenus(bar)
    sections: list[tuple[str, list[tuple[str, str]]]] = []
    for action in bar.actions():
        menu = below.get(action)
        if menu is None:
            continue
        entries = _menu_entries(menu, below)
        if entries:
            sections.append((_label_text(action), entries))
    sections.extend((title, list(rows)) for title, rows in DISPLAY_ONLY)
    return sections


def _heading(title: str) -> QLabel:
    heading = QLabel(title)
    font = heading.font()
    font.setBold(True)
    heading.setFont(font)
    return heading


def _rule() -> QFrame:
    rule = QFrame()
    rule.setFrameShape(QFrame.Shape.HLine)
    rule.setFrameShadow(QFrame.Shadow.Sunken)
    return rule


def _section_widget(title: str, entries: list[tuple[str, str]]) -> QWidget:
    """One titled two-column section: names on the left, keys on the right."""
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(2)
    layout.addWidget(_heading(title))
    layout.addWidget(_rule())
    grid = QGridLayout()
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(18)
    grid.setVerticalSpacing(1)
    grid.setColumnStretch(0, 1)
    for row, (name, keys) in enumerate(entries):
        grid.addWidget(QLabel(name), row, 0)
        key_label = QLabel(keys)
        key_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        key_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        grid.addWidget(key_label, row, 1)
    layout.addLayout(grid)
    return box


class SwatchWidget(QWidget):
    """A :class:`Swatch`, painted as the Hex view paints the thing it stands for:
    the mono face on the palette's base, a rounded chip, a tick, a rule, a notch."""

    def __init__(self, swatch: Swatch, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.swatch = swatch
        self._font = mono_font()
        self._label_font = mono_font()
        self._label_font.setPointSize(8)
        metrics = self.fontMetrics()
        self.setFixedSize(metrics.horizontalAdvance("0") * 14, metrics.height() + 6)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        swatch = self.swatch
        painter = QPainter(self)
        pal = self.palette()
        painter.fillRect(self.rect(), pal.base())
        ink = pal.text().color()
        dim = QColor(ink)
        dim.setAlpha(140)
        cell = QRectF(self.rect())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if swatch.tint is not None and swatch.mark != "tick":
            marks.chip(painter, cell, swatch.tint)
        if swatch.mark == "tick":
            marks.tick(painter, cell.adjusted(3, 0, 0, 0), swatch.tint or ink)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        if swatch.mark == "rule":
            marks.rule(painter, cell.adjusted(3, 0, 0, 0))
        if swatch.mark == "box":  # as the Preview outlines a missing glyph
            painter.setPen(QPen(theme.ERROR_INK, 1))
            side = cell.height() - 8
            painter.drawRect(
                QRectF(cell.center().x() - side / 2, cell.top() + 4, side, side)
            )
        if swatch.text:
            painter.setFont(self._label_font if swatch.small else self._font)
            colour = swatch.ink if swatch.ink is not None else ink
            if swatch.small:  # a label's ink, as the Hex view draws one
                colour = QColor(colour)
                colour.setAlpha(200)
            painter.setPen(QPen(dim if swatch.dim else colour))
            painter.drawText(cell, Qt.AlignmentFlag.AlignCenter, swatch.text)
        if swatch.mark == "notch":
            marks.notch(painter, cell, dim)
        painter.end()


def _legend_section(title: str, entries: tuple[tuple[Swatch, str], ...]) -> QWidget:
    """One titled section of the legend: swatches on the left, meanings beside."""
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(2)
    layout.addWidget(_heading(title))
    layout.addWidget(_rule())
    grid = QGridLayout()
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(12)
    grid.setVerticalSpacing(3)
    grid.setColumnStretch(1, 1)
    for row, (swatch, meaning) in enumerate(entries):
        grid.addWidget(SwatchWidget(swatch), row, 0)
        grid.addWidget(QLabel(meaning), row, 1)
    layout.addLayout(grid)
    return box


def balanced_columns(sections: Sequence[_S], count: int = 2) -> list[list[_S]]:
    """Split sections across ``count`` columns, keeping each column's height even.

    Sections stay whole and in order; each goes to whichever column is shortest
    so far, so one long menu does not leave the other column nearly empty.
    """
    columns: list[list[_S]] = [[] for _ in range(count)]
    heights = [0] * count
    for section in sections:
        target = heights.index(min(heights))
        columns[target].append(section)
        heights[target] += len(section[1]) + 2  # rows plus the heading and rule
    return columns


class _ScrolledPage(QDialog):
    """A modal page of sections in balanced columns, scrolled, with Close.

    Scrolled rather than sized to fit: the lists grow with the app, and a short
    screen must still be able to reach the button.
    """

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} — {title}")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

    def _lay_out(self, columns: list[list[QWidget]]) -> None:
        body = QWidget()
        lanes = QHBoxLayout(body)
        lanes.setContentsMargins(12, 12, 12, 12)
        lanes.setSpacing(28)
        for column in columns:
            lane = QVBoxLayout()
            lane.setSpacing(14)
            for section in column:
                lane.addWidget(section)
            lane.addStretch(1)
            lanes.addLayout(lane)

        scroll = QScrollArea()
        scroll.setWidget(body)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        buttons = close_box(self)

        layout = QVBoxLayout(self)
        layout.addWidget(scroll)
        layout.addWidget(buttons)
        # Opened at the width the columns need, so a new row cannot push the
        # text under a horizontal scrollbar; the vertical one it will have is
        # allowed for. The height is a starting size, never a limit.
        margins = layout.contentsMargins()
        width = (
            body.sizeHint().width()
            + scroll.verticalScrollBar().sizeHint().width()
            + margins.left()
            + margins.right()
        )
        screen = self.screen().availableGeometry()
        self.resize(
            min(width, screen.width()),
            min(body.sizeHint().height() + 60, 640, screen.height()),
        )


class ShortcutGuide(_ScrolledPage):
    """Help ▸ Shortcuts…: every key the app answers to, in one modal page."""

    def __init__(
        self,
        sections: list[tuple[str, list[tuple[str, str]]]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("Shortcuts", parent)
        self._lay_out(
            [
                [_section_widget(title, entries) for title, entries in column]
                for column in balanced_columns(sections)
            ]
        )


class LegendDialog(_ScrolledPage):
    """Help ▸ Legend…: every colour and mark the views draw, with a swatch."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Legend", parent)
        self._lay_out(
            [
                [_legend_section(title, entries) for title, entries in column]
                for column in balanced_columns(LEGEND)
            ]
        )


class AboutDialog(QDialog):
    """Help ▸ About: what this is, which version, who wrote it, and the license."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        icon = QLabel()
        pixmap = QPixmap()
        pixmap.loadFromData(resources.read_bytes("icons", "app.png"))
        icon.setPixmap(
            pixmap.scaled(
                64,
                64,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        icon.setAlignment(Qt.AlignmentFlag.AlignTop)

        # One rich-text label rather than a stack of them: it keeps the link
        # clickable and the whole thing selectable for a bug report.
        text = QLabel(
            f"<h2 style='margin-bottom:2px'>{APP_NAME} {__version__}</h2>"
            "<p style='margin-top:0'>A text viewer and editor for retro-game"
            " ROMs.</p>"
            f"<p>By <b>{AUTHOR}</b><br>"
            f"<a href='{HOMEPAGE}'>{HOMEPAGE}</a></p>"
            "<p>Released under the MIT license. Built on Python and Qt via"
            " PySide6, which is licensed under the LGPLv3.</p>"
            # Apache 2.0 asks that the notice travel with the work, so it is in
            # the app and not only in the license file shipped beside the font.
            "<p>Icons from <a href='https://fonts.google.com/icons'>Material"
            " Symbols</a>, licensed Apache 2.0.</p>"
        )
        text.setWordWrap(True)
        text.setOpenExternalLinks(True)
        text.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
            | Qt.TextInteractionFlag.TextSelectableByMouse
        )

        top = QHBoxLayout()
        top.setSpacing(14)
        top.addWidget(icon)
        top.addWidget(text, 1)

        buttons = close_box(self)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(buttons)
        self.setMinimumWidth(420)


__all__ = [
    "DISPLAY_ONLY",
    "LEGEND",
    "AboutDialog",
    "LegendDialog",
    "ShortcutGuide",
    "Swatch",
    "SwatchWidget",
    "balanced_columns",
    "shortcut_sections",
    "submenus",
]
