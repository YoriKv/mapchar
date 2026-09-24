# UI conventions

The rules every surface of `mapchar.ui` follows, and the helpers that
implement them — `src/mapchar/ui/widgets.py`, `bars.py` for a bar that wraps
and `progress.py` for long work. `tests/test_ui_layout.py` checks the ones a
test can see.

## Labels

- **Title Case** for menu rows, context-menu rows, submenus, buttons, tabs,
  window and dialog titles, group boxes and the shortcut guide's sections.
  Short joining words stay lower case inside a title (`Go to Address…`,
  `Apply to Identical Originals in Block`).
- **Sentence case** for form labels, checkboxes, placeholders, tooltips,
  status lines and combo items (`Stop at end token`, `hex bytes or "text"`).
- **`…`** ends a row or button that asks for more before it acts: a dialog, a
  file picker, a prompt.
- **Every menu row carries a mnemonic** (`&`), unique within its menu, context
  menus included.
- **A number that means something else says so**: a spin box whose zero or −1
  is not a count shows a special value text — `off`, `none`, `fit`, `box`,
  `top-left` — rather than a label explaining the number.
- **Words, never class names**: a source or string type is shown as the
  Reading bar names it (`Pointer table · End token`). `ui/kind_names.py` is
  the one table that decides it (`SOURCE_NAMES`, `STRING_TYPE_NAMES`), read by
  the bar's pickers, the block bar's label, the Scan window and the Files
  panel's tooltips alike.

## Modes

- **A choice of mode is a `ModeToggle`**: buttons side by side, exactly one
  down — the Format bar's Strings/Pointers, the Table Entry form's Hex/Bits.
  The one down is painted in the palette's `Highlight` and `HighlightedText`,
  since the checked bevel a style draws on its own vanishes into the dark
  theme's surface; a mode that cannot be picked is disabled and keeps the
  greyed look.

## Nothing moves

Opening another entry, or picking another kind of source or string, leaves
every control where it was.

- **A bar keeps its rows.** The Format, Reading and Block bars show the same
  controls in the same order whatever is open; one that does not apply is
  greyed where it stands, as a menu row is, never hidden.
- **What only some readings have goes last**: the fields one kind of source or
  string type has and another has not sit at the end of their section — under
  Strings on a row of their own (`bars.ROW_BREAK`) — so nothing stands after
  them to be pushed along. A bar's width counts them whether they are showing
  or not (`bars.FlowLayout.natural_width`), so the sections beside it do not
  shift either.
- **What a bar has no room for is folded away**: a block's write settings, its
  skip ranges and a pointer list's addresses are one line each, opening a popup
  under it (`WritingPicker`, `SkipsPicker`, `AddressesPicker`) — all
  `PopupPicker`s over a `PopupFrame` (`mapchar.ui.popup_picker`), which is
  where the panel opens, that the combo has no list, and the one row the
  summary is written to. A setting made once and left alone earns no row of its
  own; neither does a list, which has no length a row can be sized to.
- A window made narrower wraps its bars onto more rows; that is the user's
  doing and the only thing that moves them.

## Room running out

No layout's minimum size may be set by text or a count that varies.

- **Status and summary lines** are `ElidedLabel`s: they draw what fits and ask
  for no width, so a long notice cannot widen the window.
- **A row of variable length** (the Strings view's code buttons) is a
  `bars.FlowLayout`, which wraps onto more rows. A bar of labelled controls (the
  Format and Reading bars) is a `bars.WrapBar`: a `FlowLayout` that also asks
  for the height its rows take at its width, so a short window cannot squeeze a
  row out of sight. A long bar gathers its controls into framed, bold-captioned
  sections (`WrapBar.add_section`) that sit side by side while there is room,
  stretch to fill their row, share one caption width, and wrap their own
  controls when narrower. A fixed row too long for a narrow
  window is split into two rows instead.
- **Fields** keep a minimum from `fit_chars`, so no layout squeezes one below
  what it holds; pickers in a bar are `CompactComboBox`es of a stated width —
  `PICKER_WIDTH` unless one whose items are shorter or longer passes its own.
- **Content that grows** scrolls: the Preview canvas, the Box tab's picture and the raw view, which
  scrolls sideways when narrower than its rows. The Hex and Text tabs are the exception: each holds the window its
  box has room for, and what does not fit is reached by moving the view — its
  scrollbar is the file's (or the stretch of it the view is confined to),
  never the box's.
- **A pane whose height is a judgement is split, never fitted**: the Strings
  view's grid and pane, and the Table Editor's grid and form, sit in a
  `QSplitter` whose position is kept in `QSettings`. A pane whose contents grow
  — a form whose kind takes parameters — opens at a height that holds the
  tallest of them and scrolls inside its half after that; only a drag ever
  moves the handle. Fitting it to what it shows would move the rows of the view
  above out from under the cursor, which is the thing to avoid.
- **Dialogs fit a 768-pixel-high screen**: a long form is grouped into titled
  boxes in columns.
- **Docks**: a tree's name column stretches and gives way first; count columns
  size to their contents.
- The file size, selection and view-only notice sit at the **status bar's
  right end**, not in the navigation row.

## Numbers and addresses

`src/mapchar/ui/number_fields.py` holds the fields every number is typed into.

- **A count is as wide as what it holds**, not its maximum: `number_spin` and
  `fit_spin` fix a spin box to the digits it is expected to hold — a pointer
  size one, a stride two, a string count four — so a range reaching a million
  does not make a field for tens a million wide.
- **An address is an `AddressEdit`**: one width (`ADDRESS_CHARS`), spelled as
  the navigation bar's address format spells a position — flat six-digit hex or
  a bank layout's `$BB:AAAA` — and read in that spelling first and flat hex
  second. The window's one `AddressSpelling` follows the format, and every
  address field re-spells what it shows when the format changes; a list of
  addresses does the same. Finished typing is re-spelled.
- **An offset is an `OffsetEdit`**: the addresses' width, in hex with `$`
  optional and a leading `-` to subtract, shown as `1F0` or `-10`.
- **Any other hex number** — a fill byte, a bank, the Custom bank fields — is a
  `HexEdit` or `HexSpinBox`, upper case and padded to its digits; a number
  whose digit count means something, as the Fill dialog's first key means the
  width of every key it lays down, is a `HexEdit(pad=False)` and keeps the
  digits it was typed with.

## Cut-short text reads in full

Wherever text can be cut short, hovering it shows the whole of it, ahead of any
tooltip the control has of its own.

- **Item views** — `show_elided_tooltips(view)` on the Files tree, the
  Strings table, every `ResultsTable`, the Table Editor grid, the
  Preview's tables and the container's file list. A cell with room keeps the
  view's usual tooltip.
- **Labels** — `ElidedLabel`; `text()` and `toolTip()` still read back what
  was set.
- **Pickers** — `CompactComboBox` spells out a current item it cuts.
- **Fields** — `hint_field` sets a placeholder and a tooltip saying the same,
  since a placeholder is the first thing a narrow field cuts and vanishes once
  typed over.
- A path in a list is cut in its middle (`ElideMiddle`), keeping the file name.

## Rows counted in thousands

A grid filled from a file is a `QTableWidget`: an item per cell is the plainest
thing that works, and a block, a glossary or a result list is a screenful.
A grid filled from a **table** is not — a table on the Shift-JIS charset is
seven thousand entries, on UTF-8 a hundred and fifty thousand — so the Table
Editor's grid is a `QTableView` over a model of the entries themselves
(`table_grid._EntryModel`).

- **A row is spelled when it is looked at**, and kept: nothing is built for a
  row that is never drawn, sorted on or filtered.
- **The sort and the filter are the model's.** The key order is the entries'
  own — width, then bits — and costs no cell; a filter leaves out the rows
  that do not match rather than the view hiding them, so Select All under a
  filter reaches only what shows.
- **A row is read by its entry** (`bits_at`, `row_of`), never by walking cells.

## Byte and text views

- **One face**: the Hex and Text tabs and the Hex panel draw in
  `widgets.mono_font()`, a family list tried per character — a monospaced face
  for hex and Latin text, then faces that draw kana and kanji.
- **One selection tint**: the Hex tab and the Hex panel tint the selected
  bytes with `theme.TINT_SELECTION` in both columns, whether or not they have
  focus; characters picked in the Hex tab's text are tinted by their bits.
- **One address column**: the raw view and the Hex panel size and spell it the
  same way, from `address_column` — the window's address format, in a column
  wide enough for the file's highest address and never narrower than
  `ADDRESS_COLUMN_CHARS`, flat hex's six digits — so a small file's column
  still reads like every other address.

## Keys

- **Esc closes a tool window** (`EscapeCloses`), as it closes a dialog; an
  open cell editor or popup spends its own Esc first.
- **A tool window that edits the project carries Undo and Redo itself.** A
  window shortcut reaches only the active top-level window, and a tool window
  is one of its own, so `widgets.carry_undo` puts the Edit menu's two actions
  on the Table Editor, Find and Replace and the Glossary dock — a top-level
  window once it is floated — as well: Ctrl+Z means
  the same thing wherever the focus is. It means it inside their fields too —
  a text field, a text box and a spin box each claim the key for their own
  typing history, which in a window whose fields are typed and then applied is
  no use and makes Ctrl+Z look dead, so the claim is declined. Everything else
  on the menu bar is reached from the main window.
- **Enter runs** the Search window's query field; Find and Replace's default button
  is Find Next. A find field (`find_row.FindRow`, the Find bar's and the Hex
  panel's) finds the next match on Enter and the previous on Shift+Enter.
- **Help ▸ Shortcuts… (F1)** is built from the menu bar plus
  `help_dialogs.DISPLAY_ONLY`, the keys and gestures no menu row carries; a
  key handled outside a menu is added there. An action whose label changes at
  runtime sets a `guideLabel` property.
- **Help ▸ Legend…** is `help_dialogs.LEGEND`: every colour and mark the Hex
  and Text views, the Hex panel, the Strings view, the Files panel, the Table
  Editor, the Glossary and the Preview draw, each beside a swatch painted from the same `theme` colour the view uses. A new tint or
  mark in a view is added there; the test suite holds that every `theme`
  tint and ink appears in it. The `PREVIEW_` colours are the exception: they
  are a stand-in screen's paper, ink and grid, not marks put on the text.
- Icon-only buttons wear the bundled icon font's arrows and always have a
  tooltip naming their key.

## Menus in code

- **A row that cannot act is greyed, never dropped.** A context menu keeps the
  same shape wherever it is opened, so what does not apply to the row under the
  pointer says so rather than disappearing and leaving the rows below it
  somewhere else.
- **Never call `QAction.menu()`.** Under PySide6 the wrapper it returns owns
  the menu and deletes it when collected, taking a menu the window keeps (Open
  Recent) with it. Walk submenus with `help_dialogs.submenus(owner)`.

## Reviewing by eye

`uv run python local-tools/ui_screenshots.py [--theme dark]` opens the Dragon
Quest IV sample ([development.md](development.md#layout)) or `--project PATH`
from a scratch copy with scratch settings, and
saves the main window, every dock, tool window and dialog — each also at its
smallest size — and every menu and context menu to `tmp/ui-shots/<theme>/`.

## The wiki

`wiki/` is the GitHub wiki's pages: `Home.md`, `_Sidebar.md`, a page per file,
and their screenshots in `wiki/images/`, linked by relative path.

`.github/workflows/wiki.yml` publishes them: a push to `main` that touches
`wiki/` mirrors the folder into the repository's `.wiki.git` and pushes, so
the folder is the source of truth and a page edited in the wiki's web editor
is overwritten. The wiki repository only exists once its first page has been
created through the web UI; until then the workflow fails saying so.

`uv run python local-tools/wiki_screenshots.py` walks the Getting Started tutorial
and then the Advanced Pointers page (its shots are named `adv-…`) on a scratch
copy of the Mortal Kombat II sample ROM in `tmp/wiki-tutorial/` and saves every
screenshot the pages use, in the dark theme. `WIKI_SHOTS_OUT` names another
folder to save them to. It runs from WSL,
loading Windows' fonts, since the offscreen platform has none. A change to a
surface the tutorial shows runs it again, and changes the page's text where the
steps changed.

## The README gallery

`uv run python local-tools/readme_screenshots.py` opens a scratch copy of the
Super Mario World sample project in `tmp/readme-shots/` and saves the six shots
`README.md`'s gallery shows to `screenshots/`, in the dark theme: the ROM in
Hex, the Message boxes pointer table, one message in Text, Level names in
Strings with a few translated, the table picker open, and the Table Editor
with Kind open. `README_SHOTS_OUT` names another folder to save them to. Like
the wiki's tool it runs from WSL, loading Windows' fonts.
