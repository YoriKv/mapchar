# Changelog

## v0.1.4 - unreleased

- Remove charset picker in table editor
- Performance improvement for table editor
- Better errors when refusing a translation write
- Fixed bytes column display in strings tab
- Find Pointers leaves stray matches out of the pointer table it infers
- Write status no longer reports 0 blocks
- Status goes by the bytes: switching a block's table no longer marks every string edited
- A pointer block with no Bound keeps the room its shortened strings gave up
- Changing how an edited block is read asks first
- Header: a range block steps over the bytes before each string, in place of a skip range per record
- RNC methods 1 and 2
- Find Pointers finds a banked table without the offset, ranks a real table above stray matches, and Attach leaves strays off
- A packed write keeps a banked 2-byte pointer short
- Scan cuts regions to the text they hold and recognises length-prefixed records
- Shift+click extends a Hex selection
- Undoing a write always says Restored
- Compression picker on the Format bar, armed automatically by a scheme's signature
- The Decompressed View reads its payload as Hex and Text, and Find All lists every structure in the file
- The Hex view washes the structure on preview
- The bars keep their place: what does not apply is greyed, and Writing opens from one line
- View ▸ Decompressed View (Ctrl+Shift+D) opens the view anywhere, and it says why nothing decodes
- Skip ranges and a record header always write slotted, so Packed no longer lays strings over them
- A packed write rewrites the pointers attached to a range block, and refuses a pointer that cannot reach its string
- Hex and Text stay in step past the padding of a shortened length-prefixed string

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
