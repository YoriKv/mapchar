# Layout and preview

The secondary system: rendering a string through a game's font to show how it
fits its text box. It never changes what is written to the ROM; it reads the
encoded bytes of a string and draws them.

## Fonts

A **font** entry is a glyph sheet image plus a map:

- **Sheet** — a PNG, indexed or RGB, cut into cells of `cell width × cell
  height` pixels, `columns` across, numbered row-major from a `base` index.
- **Map** — which glyph each table token draws:
  - a **character string** laid over consecutive glyph indices from `base`
    (`ABCDEFGHIJKLMNOPQRSTUVWXYZ…`), as celPix's Font Alphabet does;
  - explicit `text → glyph index` overrides for tokens the string cannot
    express, such as dictionary entries drawn as several glyphs;
  - a **space** width and a **missing** glyph.
- **Widths** — for variable-width fonts, an advance width per glyph from a
  width list in the map, or **measured** from the sheet by trimming
  transparent columns plus a fixed gap. Fixed-width fonts use the cell width.
- **Colour** — index 0 or a chosen colour is transparent; the rest draw as in
  the sheet.

Fonts are registered under **Fonts** in the Files panel and edited in the
Preview window's Font tab. The map is saved in the project; the PNG is
referenced by path.

## Text boxes

A **box** belongs to a block and says where text goes:

| Field          | Meaning                                                   |
|----------------|-----------------------------------------------------------|
| `width`, `height` | in pixels, or in cells when the font is fixed-width    |
| `line height`  | pixels per line                                           |
| `letter spacing` | pixels added after every glyph                          |
| `lines per page` | how many lines fit before the box must clear            |
| `origin`       | where the first glyph's top-left sits inside the box      |

## Code effects

Codes in the table set can carry a **layout effect**, set per code in the
Preview window's Codes tab:

| Effect      | Rendering                                                     |
|-------------|---------------------------------------------------------------|
| *none*      | draws nothing and advances nothing (the default)              |
| *newline*   | moves to the start of the next line                           |
| *page*      | clears the box and starts at the origin                       |
| *space(N)*  | advances `N` pixels                                           |
| *glyph(i)*  | draws glyph `i`                                               |
| *end*       | stops rendering                                               |

Effects are saved with the block's box; a table shared by several blocks may
mean different things in each.

## Rendering

- The Preview window follows the selected string in the Strings view and
  draws its **translation** when one exists, else the original.
- Rendering walks the string's tokens: text tokens draw their glyphs and
  advance; codes apply their effect; unmatched bytes draw the missing glyph.
- **Overflow** is reported per string, as the **overflows box** status in
  the Strings view: a line wider than the box, or more lines than a page
  holds. The offending glyphs are tinted in the preview.
- **Pages** step with buttons when a string spans several.
- **Zoom** and a pixel grid as in the raw view; the preview can be copied as
  an image.

## Wrapping

**Wrap** in the Strings view rewrites a translation's line breaks to fit the
box:

- existing *newline* codes are removed, except those immediately after a
  *page* code;
- words (runs between spaces) are laid out greedily; a word that would cross
  the box width starts a new line with the block's chosen newline code; a
  word wider than the box is broken at a glyph;
- when the page's line count is reached, the block's *page* code is inserted
  when one exists, else the string is flagged as overflowing;
- the result is previewed before it is applied and is one undo step.

Wrap applies to one string or to every selected string.
