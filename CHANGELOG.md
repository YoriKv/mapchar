# Changelog

## Unreleased

- Putting a table on a charset no longer pauses the window: the Table Editor's
  grid draws its rows from a model, and a snapshot shares the entries it holds.

## v0.1.3 - 2026-09-18

- Translations are edited straight into the ROM's bytes, and the project
  keeps each string's original.
- An editing pane under the Strings grid, with notes and a byte readout.
- String stepping: next-row commits, and Next / Previous Untranslated and
  Flagged.
- Translation progress in the Block bar, and a done status.
- Apply one translation to every string with the same original.
- Autosave, offered back on the next open.
- Search ▸ Project Strings and Edit ▸ Glossary.
- Character-based box limits when no font is bound.
- Folders in the Files panel.
- Nested pointer tables, null pointers, fill patterns and table includes.
- Newline, page and pause code effects.
- File ▸ Refresh Tables (Shift+F5).
- A reworked Table Editor, with Ctrl+Z in every tool window.
- The Text tab ends a line at every string.
- Performance and bug fixes.

## v0.1.2 - 2026-09-15

- Code and tooltip fixes

## v0.1.1 - 2026-09-15

- First release
