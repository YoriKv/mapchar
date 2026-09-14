"""The Help menu's two modal dialogs: the shortcut guide and About.

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

:func:`shortcut_sections` is separated from the dialog so the mapping can be
tested without opening a modal, which the offscreen platform can never answer.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
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

AUTHOR = "Epi"
HOMEPAGE = "https://github.com/YoriKv/mapchar"

DISPLAY_ONLY: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "Hex and Text Views",
        (
            ("Page up / down", "PgUp / PgDn"),
            ("Row up / down", "Up / Down"),
            ("Byte back / forward", "Left / Right, or − / +"),
            ("Start / end of file", "Home / End"),
            ("Back / forward through visited entries", "Mouse 4 / Mouse 5"),
            ("Select bytes", "Drag over hex or text"),
            ("The view's own menu", "Right-click"),
        ),
    ),
    (
        "Strings View",
        (
            ("Edit the selected cell", "Enter, or double-click"),
            ("Commit the cell being edited", "Ctrl+Return"),
            ("Write the block's newline code", "Shift+Return"),
            ("Complete a code", "["),
            ("Cancel the edit", "Esc"),
            ("Show or hide columns", "Right-click a header"),
            ("The rows' own menu", "Right-click"),
        ),
    ),
    (
        "Files Panel (while focused)",
        (
            ("Extend the selection", "Shift+click / Ctrl+click"),
            ("Reorder the selected rows", "Alt+Up / Alt+Down"),
            ("Cut / copy / paste entries", "Ctrl+X / C / V"),
            ("Duplicate entries", "Ctrl+D"),
            ("Remove the selected entries", "Del"),
            ("Filter the list", "Ctrl+F"),
            ("Rename the row", "F2"),
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
            ("Run the search", "Enter in the query"),
        ),
    ),
)
"""Keys and gestures no menu action carries, by the surface they belong to."""


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


def _section_widget(title: str, entries: list[tuple[str, str]]) -> QWidget:
    """One titled two-column section: names on the left, keys on the right."""
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(2)
    heading = QLabel(title)
    font = heading.font()
    font.setBold(True)
    heading.setFont(font)
    layout.addWidget(heading)
    rule = QFrame()
    rule.setFrameShape(QFrame.Shape.HLine)
    rule.setFrameShadow(QFrame.Shadow.Sunken)
    layout.addWidget(rule)
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


def balanced_columns(
    sections: list[tuple[str, list[tuple[str, str]]]], count: int = 2
) -> list[list[tuple[str, list[tuple[str, str]]]]]:
    """Split sections across ``count`` columns, keeping each column's height even.

    Sections stay whole and in order; each goes to whichever column is shortest
    so far, so one long menu does not leave the other column nearly empty.
    """
    columns: list[list[tuple[str, list[tuple[str, str]]]]] = [[] for _ in range(count)]
    heights = [0] * count
    for section in sections:
        target = heights.index(min(heights))
        columns[target].append(section)
        heights[target] += len(section[1]) + 2  # rows plus the heading and rule
    return columns


class ShortcutGuide(QDialog):
    """Help ▸ Shortcuts…: every key the app answers to, in one modal page."""

    def __init__(
        self,
        sections: list[tuple[str, list[tuple[str, str]]]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} — Shortcuts")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        body = QWidget()
        columns = QHBoxLayout(body)
        columns.setContentsMargins(12, 12, 12, 12)
        columns.setSpacing(28)
        for column in balanced_columns(sections):
            lane = QVBoxLayout()
            lane.setSpacing(14)
            for title, entries in column:
                lane.addWidget(_section_widget(title, entries))
            lane.addStretch(1)
            columns.addLayout(lane)

        # Scrolled rather than sized to fit: the list grows with the app, and a
        # short screen must still be able to reach the button.
        scroll = QScrollArea()
        scroll.setWidget(body)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(scroll)
        layout.addWidget(buttons)
        # Opened at the width the two columns need, so a new action cannot push
        # the text under a horizontal scrollbar; the vertical one it will have is
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

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(buttons)
        self.setMinimumWidth(420)


__all__ = [
    "DISPLAY_ONLY",
    "AboutDialog",
    "ShortcutGuide",
    "balanced_columns",
    "shortcut_sections",
    "submenus",
]
