# Layout and preview

The secondary system: rendering a string through a game's font to show how it
fits its text box. It never changes what is written to the ROM; it reads the
encoded bytes of a string and draws them.

## Fonts

A **font** entry is a glyph sheet image plus a map:

- **Sheet** — a PNG, indexed or RGB, cut into cells of `cell width × cell
  height` pixels, `columns` across, numbered row-major from zero.
- **Map** — which glyph each table token draws:
  - a **character string** laid over consecutive glyph indices from `base`
    (`ABCDEFGHIJKLMNOPQRSTUVWXYZ…`), as celPix's Font Alphabet does; one glyph
    per character, where a character is a base plus the combining marks that
    follow it, so a decomposed dakuten kana takes one glyph and not two;
  - explicit `text → glyph index` overrides for text the string cannot
    express — a `[code]`, or several characters drawn as one glyph. Rendering
    tries the longest override first, so `th → glyph` fires inside a word;
  - a **space** width and a **missing** glyph.
- **Widths** — for variable-width fonts, an advance width per glyph from a
  width list in the map, or **measured** from the sheet: the last inked
  column of each cell plus the gap set beside the Measure button.
  Fixed-width fonts use the cell width.
- **Colour** — on an indexed sheet the chosen palette index is transparent; an
  RGB sheet takes the top-left pixel's colour, as does an indexed one with no
  index given. The rest draw as in the sheet.

Fonts are listed in the **Fonts** panel, which shares a tabbed dock with
**Tables**, and are edited in the Preview window's Font tab. The map is saved
in the project; the PNG is referenced by path.

The Font tab shows the sheet as a grid of cells, each captioned with what it
spells, at its own zoom; a click picks one cell or a whole row, and the pick
is where the alphabet starts:

- **Fill from table** lays the start table's one-character text over the
  glyphs from the pick, in key order;
- **Fill with…** does the same for `A–Z`, `a–z`, `0–9`, the three together,
  printable ASCII, `あ-ん`, `ア-ン` or a typed string;
- **Shift up / down** moves the whole alphabet one row of glyphs;
- **Copy / Paste** carry the alphabet as `20=A` lines, one glyph per line.

## Text boxes

A **box** belongs to a block and says where text goes:

| Field          | Meaning                                                   |
|----------------|-----------------------------------------------------------|
| `width`, `height` | in pixels                                              |
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
  A space with no glyph of its own advances the font's space width and draws
  nothing — a space is never a missing glyph.
- Text the font cannot spell is listed under the preview, beside the byte
  readout of the draft being typed. The Preview follows the Translation cell
  as it is typed, not only what has been committed.
- **Overflow** is reported per string, as the **overflows box** status in
  the Strings view: a line wider than the box, or more lines than a page
  holds. The offending glyphs are tinted in the preview.
- **Pages** step with buttons when a string spans several.
- **Zoom** and a pixel grid as in the raw view; the preview can be copied as
  an image.

## Wrapping

**Wrap translation**, in the Preview window, rewrites the selected strings'
line breaks to fit the box:

- existing *newline* codes are removed, except those immediately after a
  *page* code;
- words (runs between spaces) are laid out greedily; a word that would cross
  the box width starts a new line with the block's chosen newline code; a
  word wider than the box is broken at a glyph;
- codes are measured as they render: a *space(N)* effect advances `N`, a
  *glyph(i)* effect advances that glyph, and any *newline* or *page* effect
  breaks the line as its own code would;
- when the page's line count is reached, the block's *page* code is inserted
  when one exists, else the string is flagged as overflowing. A page starts on
  its own first line, so no newline code goes with it;
- the result is applied directly and is one undo step.

Wrap applies to one string or to every selected string.
