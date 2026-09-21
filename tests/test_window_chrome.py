"""The window around the views: the menu bar walked whole, the docks and tool
windows, the shortcut guide and the legend, and the room each row is given."""

from __future__ import annotations

import re

from conftest import ROOT
from mapchar.core.block import RangeSource
from window_helpers import add_block, menu_actions, open_rom_and_table

# --- the menu bar as a whole ------------------------------------------------
# Three walks over the built window, so a new action, a new gate or a new
# shortcut has to be classified rather than quietly joining the crowd.

# The rows that apply whatever is on screen, so no capability gates them: the
# project and plugin rows, the panels, the themes, the help, the visit trail
# (armed by the trail), the undo pair (armed by the stack) and the entry
# clipboard (scoped to the Files panel, which has a selection of its own).
ALWAYS_ON = frozenset(
    {
        "Open ROM…",
        "Open Table…",
        "New Table…",
        "Refresh Tables",
        "Open Font…",
        "Write All",
        "New Project",
        "Open Project…",
        "Locate Missing Files…",
        "Script…",
        "TSV / CSV…",
        "PO…",
        "Cartographer Command File…",
        "Atlas Script…",
        "TSV…",
        "CSV…",
        "Save Project",
        "Save Project As…",
        "Open Plugins Folder…",
        "Refresh Plugins",
        "Quit",
        "Undo",
        "Redo",
        "Cut Entry",
        "Copy Entry",
        "Paste Entry",
        "Duplicate Entry",
        "Table Editor…",
        "Glossary",
        "Project Strings…",
        "Light Theme",
        "Dark Theme",
        "Back",
        "Forward",
        "Files",
        "Tables",
        "Fonts",
        "Hex",
        "Reset Panel Layout",
        "Shortcuts…",
        "Legend…",
        "About",
    }
)


def _gated_controls(window):
    from mapchar.ui.main_window.capability_sync import _GATES

    found = set()
    for names in _GATES.values():
        for name in names:
            control = getattr(window, name)
            found.update(
                id(c) for c in (control if isinstance(control, tuple) else (control,))
            )
    return found


def test_every_menu_action_is_gated_or_always_on(window):
    """The capability table's coverage read off the menu bar rather than
    promised: a row that is neither gated nor deliberately always-on stays live
    on an entry it cannot act on."""
    gated = _gated_controls(window)
    ungated = [
        label
        for label, action in menu_actions(window)
        # A row that only opens a submenu is not itself a row that acts, unless
        # the table gates it (Import and Export are gated on their whole menu).
        if action.menu() is None and id(action) not in gated and label not in ALWAYS_ON
    ]
    assert ungated == []


def test_every_gate_names_a_control_the_window_has(window):
    """A gate naming a control that is not there used to be a silent skip, so
    the control was never gated and nothing said so."""
    from mapchar.ui.main_window.capability_sync import _GATES

    for names in _GATES.values():
        for name in names:
            assert getattr(window, name) is not None


def test_no_two_window_actions_share_a_shortcut(window):
    """Qt calls one sequence bound twice on a window ambiguous and fires
    neither, so a duplicate is two dead keys rather than one."""
    seen: dict[str, str] = {}
    clashes = []
    for label, action in menu_actions(window):
        keys = action.shortcut().toString()
        if not keys:
            continue
        if keys in seen:
            clashes.append(f"{keys}: {seen[keys]} and {label}")
        seen[keys] = label
    assert clashes == []


# --- the window's own chrome ------------------------------------------------


def test_the_dock_layout_is_remembered_and_resettable(window):
    from mapchar.ui import settings

    window.show()
    window.hex_dock.show()
    window.files_dock.hide()
    window._window_layout.save()
    assert settings().value("window/state") is not None
    window._reset_layout()
    # The factory arrangement is whatever the docks built for themselves, which
    # is the Files dock up and the Hex dock down.
    assert window.files_dock.isVisibleTo(window)
    assert not window.hex_dock.isVisibleTo(window)


def test_every_tool_window_remembers_its_geometry(window):
    for tool in (
        window.search_window,
        window.scan_window,
        window.table_editor,
        window.decompress_window,
        window.preview_window,
        window.find_replace,
    ):
        assert tool._layout is not None
        tool._layout.save()


def test_the_architecture_doc_lists_every_main_window_module():
    """§7.1's table is the map of the split, so a module added or renamed without
    a line there leaves the design describing a window that is not this one."""
    doc = (ROOT / "docs/plan/architecture.md").read_text(encoding="utf-8")
    section = doc.split("### 7.1 Composition", 1)[1].split("### 7.2", 1)[0]
    listed = set(re.findall(r"`(\w+\.py)`", section))
    on_disk = {
        path.name
        for path in (ROOT / "src/mapchar/ui/main_window").glob("*.py")
        if path.name != "__init__.py"
    }
    assert on_disk - listed == set()


def test_a_layout_version_bump_drops_a_stored_arrangement(window):
    """What the version is for: an arrangement this build cannot make sense of is
    dropped for the defaults rather than half-restored."""
    from mapchar.ui.window_layout import LAYOUT_VERSION

    window.show()
    state = window.saveState(LAYOUT_VERSION)
    assert window.restoreState(state, LAYOUT_VERSION)
    assert not window.restoreState(state, LAYOUT_VERSION + 1)


def test_the_navigation_filter_comes_off_the_application_on_close(
    window, tmp_path, monkeypatch
):
    """It was installed on a singleton, so a closed window that left its filter
    behind would keep answering for a window that is gone."""
    from PySide6.QtWidgets import QApplication

    entry = open_rom_and_table(window, tmp_path, b"AB\x00" + b"\xff" * 32)
    window._activate_entry(entry)
    removed = []
    monkeypatch.setattr(
        QApplication.instance(),
        "removeEventFilter",
        lambda obj: removed.append(obj),
        raising=False,
    )
    window.close()
    assert removed == [window]


def test_a_push_re_serialises_the_project_once(window, tmp_path, monkeypatch):
    """The project's unsaved marker costs the whole project re-serialised, and one
    push passes several choke points that would each ask for it."""
    file_entry = open_rom_and_table(window, tmp_path, b"AB\x00" + b"\xff" * 32)
    add_block(window, file_entry, "b", RangeSource(0, 3))
    assert window._write_project(str(tmp_path / "p.mapchar"))
    calls = []
    real = window._snapshot
    monkeypatch.setattr(window, "_snapshot", lambda: (calls.append(1), real())[1])
    window._on_translation_edited(0, "B[end]")
    assert len(calls) == 1


def test_no_widget_wears_a_stylesheet():
    """The theme is one palette on Fusion; a stylesheet anywhere would paint one
    widget out of step with both themes (``ui/theme.py``)."""
    root = ROOT / "src/mapchar/ui"
    wearing = [
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if "setStyleSheet" in path.read_text(encoding="utf-8")
    ]
    assert wearing == []


def test_the_shortcut_guide_is_built_from_the_window(window):
    from mapchar.ui.help_dialogs import (
        ShortcutGuide,
        balanced_columns,
        shortcut_sections,
    )

    sections = dict(shortcut_sections(window))
    assert "File" in sections and "Navigate" in sections
    assert ("Write", "Ctrl+W") in sections["File"]
    assert ("Go to Address…", "Ctrl+G") in sections["Navigate"]
    assert ("Back", "Alt+Left") in sections["Navigate"]
    # Undo's label names the command it would undo; the guide pins the verb.
    assert ("Undo", "Ctrl+Z") in sections["Edit"]
    # A submenu's rows are flattened into its parent's section.
    assert ("Script…", "") not in sections["File"]
    # And the keys no menu can carry are declared beside them.
    assert ("Page up / down", "PgUp / PgDn") in sections["Hex and Text Views"]
    columns = balanced_columns(shortcut_sections(window))
    assert sum(len(c) for c in columns) == len(sections) and all(columns)
    guide = ShortcutGuide(shortcut_sections(window), window)
    assert guide.width() > 0
    guide.close()


def test_the_legend_shows_every_colour_the_views_draw(window):
    """A tint added to the theme is a tint the legend has to explain."""
    from PySide6.QtGui import QColor

    from mapchar.ui import theme
    from mapchar.ui.help_dialogs import LEGEND, LegendDialog, SwatchWidget

    shown = {
        colour.name(QColor.NameFormat.HexArgb)
        for _title, entries in LEGEND
        for swatch, _meaning in entries
        for colour in (swatch.tint, swatch.ink)
        if colour is not None
    }
    drawn = {
        getattr(theme, name).name(QColor.NameFormat.HexArgb)
        for name in dir(theme)
        # The Preview's own paper, ink and grid are the colours of a stand-in
        # screen rather than marks a view puts on the text: nothing in the
        # Legend would explain them.
        if not name.startswith("PREVIEW_")
        and (name.startswith("TINT_") or name.endswith("_INK"))
    }
    assert drawn - shown == {theme.TINT_STRING_RULE.name(QColor.NameFormat.HexArgb)}
    assert any(
        swatch.mark == "rule" for _t, entries in LEGEND for swatch, _m in entries
    )
    assert all(meaning for _t, entries in LEGEND for _s, meaning in entries)
    legend = LegendDialog(window)
    assert legend.width() > 0
    swatches = legend.findChildren(SwatchWidget)
    assert len(swatches) == sum(len(entries) for _t, entries in LEGEND)
    for swatch in swatches:
        swatch.grab()  # paints every kind of mark without a screen
    legend.close()


def test_walking_the_menus_for_the_guide_leaves_every_submenu_alive(window):
    """PySide's ``QAction.menu()`` hands its wrapper ownership of the menu, so a
    walk through it deleted Open Recent the first time Help ▸ Shortcuts opened."""
    import gc

    from mapchar.ui.help_dialogs import shortcut_sections

    shortcut_sections(window)
    gc.collect()
    window._rebuild_recent()  # raised RuntimeError on a deleted QMenu
    assert window.recent_menu.title() == "Open &Recent"


_KEY = re.compile(
    r"\b(?:Ctrl|Alt|Shift|Meta)(?:\+(?:Ctrl|Alt|Shift|Meta))*"
    r"\+(?:F\d|[A-Za-z0-9]|Return|Enter|Left|Right|Up|Down|Home|End|PgUp|PgDn|Del)\b"
    r"|\bF\d\b"
)
"""A key sequence as either the docs or Qt spells one.

Bare keys (Home, PgUp, the arrows) are deliberately not matched: they are the
navigation filter's, not any action's, and the reference writes them as prose
("Up/Down row") that no pattern should try to read as a binding.
"""


def _documented_keys() -> set[str]:
    """Every sequence the Keyboard reference table in features.md advertises."""
    doc = (ROOT / "docs/plan/features.md").read_text(encoding="utf-8")
    table = doc.split("## Keyboard reference", 1)[1]
    return {m.group(0) for line in table.splitlines() for m in _KEY.finditer(line)}


def test_every_documented_shortcut_is_really_bound(window):
    """The Keyboard reference walked against the window it describes.

    A key that moved, or one the docs promise and nothing carries, is a row the
    reader tries and finds dead. Both halves count: an action's own shortcut, and
    the keys ``help_dialogs.DISPLAY_ONLY`` declares because they are handled
    somewhere a ``QKeySequence`` cannot reach.
    """
    from mapchar.ui.help_dialogs import DISPLAY_ONLY

    bound = {
        action.shortcut().toString()
        for _, action in menu_actions(window)
        if action.shortcut().toString()
    }
    for _, rows in DISPLAY_ONLY:
        for _, keys in rows:
            bound.update(m.group(0) for m in _KEY.finditer(keys))
    assert _documented_keys() - bound == set()


def test_a_files_row_name_takes_the_panel_width(window, tmp_path):
    """The status mark is a second column; the name still fills what it leaves."""
    open_rom_and_table(window, tmp_path, b"AB\x00" * 10, rom_name="a" * 60 + ".sfc")
    tree = window.files_panel.tree
    tree.resize(500, 300)
    window.files_panel.rebuild()
    assert tree.columnWidth(0) > 400


def test_the_bar_pickers_are_narrow_and_open_to_their_longest_item(window):
    from mapchar.ui.widgets import PICKER_WIDTH

    pick = window.address_pick
    assert pick.sizeHint().width() == PICKER_WIDTH
    longest = max(
        pick.fontMetrics().horizontalAdvance(pick.itemText(i))
        for i in range(pick.count())
    )
    assert longest > PICKER_WIDTH
    pick.showPopup()
    assert pick.view().width() >= longest
    pick.hidePopup()
