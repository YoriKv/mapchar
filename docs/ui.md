# UI conventions

The rules every surface of `mapchar.ui` follows, and the helpers in
`src/mapchar/ui/widgets.py` that implement them. `tests/test_ui_layout.py`
checks the ones a test can see.

## Labels

- **Title Case** for menu rows, context-menu rows, submenus, buttons, tabs,
  window and dialog titles, group boxes and the shortcut guide's sections.
  Short joining words stay lower case inside a title (`Go to Address…`,
  `Copy Original to Empty Translations`).
- **Sentence case** for form labels, checkboxes, placeholders, tooltips,
  status lines and combo items (`Stop at end token`, `hex bytes or "text"`).
- **`…`** ends a row or button that asks for more before it acts: a dialog, a
  file picker, a prompt.
- **Every menu row carries a mnemonic** (`&`), unique within its menu, context
  menus included.
- **A switch left off says so**: a spin box whose zero or −1 means "none" shows
  a special value text (`off`) rather than a label explaining the number.
- **Words, never class names**: a source or string type is shown as the Block
  dialog names it (`Pointer table · End token`).

## Room running out

No layout's minimum size may be set by text or a count that varies.

- **Status and summary lines** are `ElidedLabel`s: they draw what fits and ask
  for no width, so a long notice cannot widen the window.
- **A row of variable length** (the Strings view's code buttons) is a
  `FlowLayout`, which wraps onto more rows. A fixed row too long for a narrow
  window is split into two rows instead.
- **Fields** keep a minimum from `fit_chars`, so no layout squeezes one below
  what it holds; pickers in a bar are `CompactComboBox`es of one fixed width.
- **Content that grows** scrolls: the Preview canvas, the Font tab's fields,
  the glyph sheet, and the raw view, which scrolls sideways when narrower than
  its rows.
- **Dialogs fit a 768-pixel-high screen**: a long form is grouped into titled
  boxes in columns (the Block dialog).
- **Docks**: a tree's name column stretches and gives way first; count columns
  size to their contents.
- The file size, selection and view-only notice sit at the **status bar's
  right end**, not in the navigation row.

## Cut-short text reads in full

Wherever text can be cut short, hovering it shows the whole of it, ahead of any
tooltip the control has of its own.

- **Item views** — `show_elided_tooltips(view)` on the Files, Tables and Fonts
  trees, the Strings table, every `ResultsTable`, the Table Editor grid, the
  Preview's tables and the container's file list. A cell with room keeps the
  view's usual tooltip.
- **Labels** — `ElidedLabel`; `text()` and `toolTip()` still read back what
  was set.
- **Pickers** — `CompactComboBox` spells out a current item it cuts.
- **Fields** — `hint_field` sets a placeholder and a tooltip saying the same,
  since a placeholder is the first thing a narrow field cuts and vanishes once
  typed over.
- A path in a list is cut in its middle (`ElideMiddle`), keeping the file name.

## Keys

- **Esc closes a tool window** (`EscapeCloses`), as it closes a dialog; an
  open cell editor or popup spends its own Esc first.
- **Enter runs** a tool window's query field; Find and Replace's default button
  is Find Next.
- **Help ▸ Shortcuts… (F1)** is built from the menu bar plus
  `help_dialogs.DISPLAY_ONLY`, the keys and gestures no menu row carries; a
  key handled outside a menu is added there. An action whose label changes at
  runtime sets a `guideLabel` property.
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
