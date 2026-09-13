"""Help ▸ Shortcuts…: the live key list, built from the window rather than typed.

A hand-written list of keys is wrong the day after a shortcut moves, and nothing
tells you. So the guide is assembled from the **menu bar** — one section per
menu, every action that advertises a key, submenus flattened into their parent —
which means a shortcut added to a menu appears here with no second edit.

What a menu cannot hold is declared here instead, in :data:`DISPLAY_ONLY`: keys
that are deliberately *not* registered as shortcuts because they would then be
stolen from focused text inputs (the navigation keys go through an application
event filter — :mod:`mapchar.ui.main_window.navigation`), keys that belong to one
panel while it has focus, and mouse gestures, which no ``QKeySequence`` can
spell.

:func:`shortcut_sections` is separated from the dialog so the mapping can be
tested without opening a modal, which the offscreen platform can never answer.
"""

from __future__ import annotations

from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QMenu

DISPLAY_ONLY: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "Raw view",
        (
            ("Page up / down", "PgUp / PgDn"),
            ("Row up / down", "Up / Down"),
            ("Byte back / forward", "Left / Right, or − / +"),
            ("Start / end of file", "Home / End"),
            ("Back / forward through visited entries", "Alt+Left / Alt+Right"),
            ("Also back / forward", "Mouse 4 / Mouse 5"),
            ("Select bytes", "Drag over hex or text"),
            ("The view's own menu", "Right-click"),
        ),
    ),
    (
        "Strings view",
        (
            ("Edit the selected cell", "Enter, or double-click"),
            ("Commit the cell being edited", "Ctrl+Return"),
            ("The rows' own menu", "Right-click"),
        ),
    ),
    (
        "Files panel (while focused)",
        (
            ("Extend the selection", "Shift+click / Ctrl+click"),
            ("Reorder the selected rows", "Alt+Up / Alt+Down"),
            ("Cut / copy / paste / duplicate entries", "Ctrl+X / C / V / D"),
            ("Remove the selected entries", "Del"),
            ("Filter the list", "Ctrl+F"),
            ("Rename the row", "F2"),
        ),
    ),
    (
        "Hex panel",
        (
            ("Overtype the nibble under the caret", "0-9 / A-F"),
            ("Go to, or find", "Enter in the field"),
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
    return action.text().replace("&", "").strip()


def _menu_entries(menu: QMenu) -> list[tuple[str, str]]:
    """Every ``(label, keys)`` pair in ``menu``, submenus flattened in place.

    Actions with no key are dropped: they are reachable by mouse and the menu
    itself is their documentation.
    """
    entries: list[tuple[str, str]] = []
    for action in menu.actions():
        if action.isSeparator():
            continue
        submenu = action.menu()
        if submenu is not None:
            keys = _key_text(action)
            if keys:
                entries.append((_label_text(action), keys))
            entries.extend(_menu_entries(submenu))
            continue
        keys = _key_text(action)
        if keys:
            entries.append((_label_text(action), keys))
    return entries


def shortcut_sections(window) -> list[tuple[str, list[tuple[str, str]]]]:
    """The guide's contents: one section per menu, then the declared surfaces."""
    sections: list[tuple[str, list[tuple[str, str]]]] = []
    for action in window.menuBar().actions():
        menu = action.menu()
        if menu is None:
            continue
        entries = _menu_entries(menu)
        if entries:
            sections.append((_label_text(action), entries))
    sections.extend((title, list(rows)) for title, rows in DISPLAY_ONLY)
    return sections


def shortcut_text(window) -> str:
    """The guide as the text dialog shows it: a titled block per section."""
    width = max(
        (len(keys) for _, rows in shortcut_sections(window) for _, keys in rows),
        default=0,
    )
    blocks = []
    for title, rows in shortcut_sections(window):
        lines = [title, "-" * len(title)]
        lines += [f"{keys:<{width}}  {label}" for label, keys in rows]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


__all__ = ["DISPLAY_ONLY", "shortcut_sections", "shortcut_text"]
