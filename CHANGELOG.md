# Changelog

## Unreleased

- The Table Editor's Charset picker is gone: Includes does the same, since
  every encoding is offered as a table of its own.
- A table of tens of thousands of entries no longer pauses the window: the
  Table Editor's grid draws its rows from a model, and a snapshot shares the
  entries it holds.
- A refused translation says why: the character no table has an entry for, the
  table that does have it and the code that switches there, the code or the
  operands no table knows, or what the text encodes to against its room.
- The Bytes column counts a packed block's room as the string's own bytes plus
  the block's spare, not the whole block; its tooltip says what the room is.

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
