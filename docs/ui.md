# UI conventions

The rules every surface of `mapchar.ui` follows, and the helpers in
`src/mapchar/ui/widgets.py` that implement them. `tests/test_ui_layout.py`
checks the ones a test can see.

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
- **Words, never class names**: a source or string type is shown as the Block
  dialog names it (`Pointer table · End token`).

## Room running out

No layout's minimum size may be set by text or a count that varies.

- **Status and summary lines** are `ElidedLabel`s: they draw what fits and ask
  for no width, so a long notice cannot widen the window.
- **A row of variable length** (the Strings view's code buttons) is a
  `FlowLayout`, which wraps onto more rows. A bar of labelled controls (the
  Format and Reading bars) is a `WrapBar`: a `FlowLayout` that also asks for the
  height its rows take at its width, so a short window cannot squeeze a row out
  of sight. A long bar gathers its controls into framed, bold-captioned
  sections (`WrapBar.add_section`) that sit side by side while there is room,
  stretch to fill their row, share one caption width, and wrap their own
  controls when narrower. A fixed row too long for a narrow
  window is split into two rows instead.
- **Fields** keep a minimum from `fit_chars`, so no layout squeezes one below
  what it holds; pickers in a bar are `CompactComboBox`es of one fixed width.
- **Content that grows** scrolls: the Preview canvas and the raw view, which
  scrolls sideways when narrower than its rows. The Hex and Text tabs are the exception: each holds the window its
  box has room for, and what does not fit is reached by moving the view — its
  scrollbar is the file's (or the stretch of it the view is confined to),
  never the box's.
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

## Byte and text views

- **One face**: the Hex and Text tabs and the Hex panel draw in
  `widgets.mono_font()`, a family list tried per character — a monospaced face
  for hex and Latin text, then faces that draw kana and kanji.
- **One selection tint**: the Hex tab and the Hex panel tint the selected
  bytes with `theme.TINT_SELECTION` in both columns, whether or not they have
  focus; characters picked in the Hex tab's text are tinted by their bits.

## Keys

- **Esc closes a tool window** (`EscapeCloses`), as it closes a dialog; an
  open cell editor or popup spends its own Esc first.
- **Enter runs** the Search window's query field; Find and Replace's default button
  is Find Next. A find field (`find_row.FindRow`, the Find bar's and the Hex
  panel's) finds the next match on Enter and the previous on Shift+Enter.
- **Help ▸ Shortcuts… (F1)** is built from the menu bar plus
  `help_dialogs.DISPLAY_ONLY`, the keys and gestures no menu row carries; a
  key handled outside a menu is added there. An action whose label changes at
  runtime sets a `guideLabel` property.
- **Help ▸ Legend…** is `help_dialogs.LEGEND`: every colour and mark the Hex
  and Text views, the Hex panel and the Strings view draw, each beside a
  swatch painted from the same `theme` colour the view uses. A new tint or
  mark in a view is added there; the test suite holds that every `theme`
  tint and ink appears in it. The `PREVIEW_` colours are the exception: they
  are a stand-in screen's paper, ink and grid, not marks put on the text.
- Icon-only buttons wear the bundled icon font's arrows and always have a
  tooltip naming their key.

## Menus in code

- **Never call `QAction.menu()`.** Under PySide6 the wrapper it returns owns
  the menu and deletes it when collected, taking a menu the window keeps (Open
  Recent) with it. Walk submenus with `help_dialogs.submenus(owner)`.

## Reviewing by eye

`uv run python tools/ui_screenshots.py [--theme dark]` opens the Dragon Quest IV
sample (or `--project PATH`) from a scratch copy with scratch settings, and
saves the main window, every dock, tool window and dialog — each also at its
smallest size — and every menu and context menu to `tmp/ui-shots/<theme>/`.
