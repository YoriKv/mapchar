"""The icon glyphs mapChar draws, as codepoints in the bundled icon font.

Qt-free on purpose; :mod:`mapchar.ui.icon_font` is the half that needs a
QPainter. The font is Material Symbols Outlined (Apache 2.0), subset to exactly
the codepoints below by ``tools/subset_icon_font.py``, which reads this enum:
**adding a member here means re-running that script**, or the new icon draws
as nothing (``tests/test_icon_font.py`` fails when it does). Members are named
for what the mark is to mapChar; each comment carries the upstream name, which
is what to search for at <https://fonts.google.com/icons?icon.style=Outlined>.
"""

from __future__ import annotations

from enum import Enum


class Glyph(Enum):
    """One icon in the bundled font. ``value`` is the character to draw."""

    # The Files panel's row markers.
    FLAG = ""  # flag - a bookmark
    GRID = ""  # grid_on - a table file
    GRID_ROWS = ""  # view_list - a block: rows of strings
    FOLDER = ""  # folder - a folder of blocks and bookmarks
    QUESTION = ""  # question_mark - this entry's file is unaccounted for
    EXCLAMATION = ""  # priority_high - it opened, but something had to give

    # The navigation bar's row steps and the preview's page steps.
    ARROW_DOWN = ""  # arrow_downward
    ARROW_UP = ""  # arrow_upward
    ARROW_LEFT = ""  # arrow_back
    ARROW_RIGHT = ""  # arrow_forward
