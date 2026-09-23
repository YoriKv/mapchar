# Layout and preview

The secondary system: drawing a string into its text box to show how it fits.
It never changes what is written to the ROM; it reads the encoded bytes of a
string and draws them.

## The preview font

The preview draws in a **system font**, not in the game's own art. One family
and size serve the whole app: they are picked on the Preview window's
**Preview** tab, under the page, and stored beside the theme, per machine and
never in the project. Nothing about a font is a project entry, and no block
binds one.

The point is fit, not fidelity. A stand-in family says whether a translation is
too long for its box long before anyone has ripped the game's glyphs, and a
family that cannot draw a game's kana says so the moment it is picked — the
page redraws in it, and the status under it lists what it cannot draw.

Measuring a real font is Qt's work, so `ui/preview_font.py` does it and the
Qt-free engine never sees a `QFont`. `PreviewFont.measured()` walks everything
a string draws (`layout.drawn_text`), asks `QFontMetrics` for each character
once, and freezes the answers into `core.font.Font`: the family and size, the
line's height and baseline, an advance per character, and the set of characters
the family has no glyph for. That value is what `engines/layout.py` measures
with.

## Text boxes

A **box** belongs to a block and says where text goes. The Preview window's
**Box** tab draws it at the Preview's zoom, with the string on screen in it:
the right and bottom edges and their corner drag to resize it, the origin mark
drags to move where text starts, faint rules show where each line begins, and
a caption reads its size and origin. A drag redraws the text as it goes and
lands once, on release, as one undo step. The fields under the picture spell
every setting:

| Field          | Meaning                                                   |
|----------------|-----------------------------------------------------------|
| `width`, `height` | in pixels                                              |
| `line height`  | pixels per line                                           |
| `letter spacing` | pixels added after every character                      |
| `lines per page` | how many lines fit before the box must clear            |
| `chars per line` | how many characters a line holds; *off* by default      |
| `origin`       | where the first character's top-left sits inside the box |

`chars per line` is what a box says it counts by. With it set, the byte
readout's `chars` and `lines` counts and **Wrap Translation** count characters
instead of measuring them: every character is
one cell, a *space* code one cell, a *newline* code ends the line, a *page*
code the page, and `lines per page` bounds the page when it is set. A game
whose own font is on a grid is truer counted than measured through a stand-in
family, so a box that sets it is never measured.

## Code effects

Codes in the table set carry a **layout effect**:

| Effect      | Rendering                                                     |
|-------------|---------------------------------------------------------------|
| *none*      | draws nothing and advances nothing (the default)              |
| *newline*   | moves to the start of the next line                           |
| *page*      | clears the box and starts at the origin                       |
| *pause*     | draws nothing and advances nothing; the code is known to wait |
| *space(N)*  | advances `N` pixels                                           |
| *end*       | stops rendering                                               |

A code's effect comes from, last word first:

1. the block's box, set per code in the Preview window's Codes tab and saved
   with the box — *none* included, so a block can silence what its table
   says;
2. the code's table entry, which declares *newline*, *page* or *pause*
   ([table-format.md](table-format.md#effects)) for every block that reads it;
3. the block's line code, which is a *newline*.

The Codes tab shows each code's effect from wherever it comes, with what it
is without a pick in the tooltip, and the box keeps only picks that differ
from that. *space* belongs to a box alone: it is pixels of one box.

## Rendering

- The Preview window follows the selected string in the Strings view and
  draws its **translation** when one exists, else the original.
- Rendering walks the string's tokens: text tokens draw their characters and
  advance; codes apply their effect and draw nothing; unmatched bytes draw a
  box. A character the family cannot draw takes its room and is boxed too —
  except a space, which advances and draws nothing, drawable or not.
- Text the font cannot draw is listed under the preview, beside the byte
  readout of the draft being typed. The Preview follows the Translation cell
  as it is typed, not only what has been committed.
- **Overflow** — a line wider than the box, or more lines than a page holds
  — is the preview's to show and nobody else's: the offending characters are
  tinted, and the status under the page says *too wide* or *too many lines*.
  The preview is a reference; it is never a string's status, never an error,
  and nothing outside the window flags it.
- **Pages** step with buttons when a string spans several.
- **Zoom** and **Grid**, which rules the box in pixels; **Copy Image** puts
  the page as drawn on the clipboard.

## Wrapping

**Wrap Translation**, in the Preview window, rewrites the selected strings'
line breaks to fit the box:

- existing *newline* codes are removed, except those immediately after a
  *page* code;
- words (runs between spaces) are laid out greedily; a word that would cross
  the box width starts a new line with the block's chosen newline code; a
  word wider than the box is broken at a character;
- codes are measured as they render: a *space(N)* effect advances `N`, and any
  *newline* or *page* effect breaks the line as its own code would;
- when the page's line count is reached, the block's *page* code is inserted
  when one exists, else the rest stays on the page and the preview shows it
  past the box. A page starts on its own first line, so no newline code goes
  with it;
- a *page* code already in the text starts the wrap over, on the first line
  of the next page;
- the result is applied directly and is one undo step;
- with `chars per line` set, it is the width and every character is one cell;
  a *space* code advances one cell.

Wrap applies to one string or to every selected string.
