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

## v0.1.2 - 2026-09-15

- Code and tooltip fixes

## v0.1.1 - 2026-09-15

- First release
