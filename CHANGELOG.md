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

## v0.1.2 - 2026-09-15

- Code and tooltip fixes

## v0.1.1 - 2026-09-15

- First release
