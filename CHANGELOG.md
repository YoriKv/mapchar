# Changelog

## Unreleased

- Translations live in the ROM's bytes: editing a string rewrites the buffer at
  once, and the project keeps each string's original instead of a pending
  translation. An edit that does not fit or encode is refused rather than held.
- Return commits and opens the next row; Ctrl+Return commits and stays.
- Next / Previous Untranslated (F4) and Flagged (F6) in the Edit menu.
- The Block bar shows how much of the block and the project is translated.
- Code buttons and completion carry the table's comment for each code.
- A Same column counts strings sharing an original, and Apply to Identical
  Originals puts one translation into all of them.
- Autosave: a copy of the project every two minutes and after every write,
  offered back on the next open.
- An editing pane under the Strings grid: the selected string's original with
  its line breaks and codes dimmed or hidden, a multi-line editor, the byte
  readout and the notes.
- A fourth status, done, set by hand (Ctrl+Alt+D) or by import, carried by
  the filters, the progress readout and the TSV, CSV and PO files.
- A box's chars per line: with no font bound, overflows box, the readout and
  Wrap Translation count characters against it and lines per page.
- Search ▸ Project Strings lists every block's strings under one filter and
  jumps to any of them.
- Edit ▸ Glossary: the project's terms and translations, the ones found in
  the selected string listed first, typed into the editor on a click.
- Folders in the Files panel, to any depth.
- Table includes, switch frames that fall through, and newline, page and
  pause code effects.
- Nested pointer tables, null pointer values, fill patterns and hidden
  fixed-string end tokens.
- A nested block opens to a row per inner pointer table in the Files panel.
- Pointer views label a nested table's outer pointers instead of following
  them.
- A Mother 3 sample project, built from the ROM.
- File ▸ Refresh Tables (Shift+F5) re-reads every table file.
- The Format bar has a Table Editor button and a Show as label.
- The Table Editor splits its grid from its form and shows where a sampled
  key came from.
- Ctrl+Z works inside the Table Editor, Find and Replace and the Glossary.
- The Text tab ends a line at every string and cuts fixed strings in step
  with the block.
- The Files context menu greys rows that do not apply instead of dropping
  them.
- The mode buttons highlight the one that is down.
- The Tables and Fonts docks are gone, and the preview draws in one system
  font.
- Dropped the EUC-JP, EUC-KR, Big5 and UTF-16 BE built-in charsets.
- Faster Text tab scrolling, scrollbar dragging and encoding.
- Fixed a Text tab step up skipping a fixed-length string, and the Files
  panel losing its string row on a refresh.

## v0.1.2 - 2026-09-15

- Code and tooltip fixes

## v0.1.1 - 2026-09-15

- First release
