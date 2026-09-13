# Phases

The build order. Each phase names what it delivers and when it is complete;
every phase below is built, with its tests in place. Nothing in a later phase
was started until the earlier one was complete. The other docs in this folder
say what the app does now — this one says only in what order it arrived.

## 1. Foundation and dumping

Open a ROM, load tables, browse the bytes through them, find text, cut it
into blocks and dump it.

- The Qt-free core: the data model, the native table grammar and the four
  legacy dialect readers, charsets, the decode engine, containers for iNES,
  SNES headered, headerless and interleaved, GB, GBA, Nintendo 64 and Mega
  Drive `.smd`, the registry and built-in presets.
- Blocks with *range* and *fixed strings* sources, *end token* and *fixed
  length* string types, realign and skip ranges.
- The native script writer.
- The application shell: Files panel, Tables dock, Codecs and Block bars,
  Raw view with navigation and selection, Hex panel read-only, bookmarks,
  Table Editor, undo for entry and view changes, `.mapchar` projects.
- Find and relative search with table building.
- Complete when a Cartographer command file's `RAW` and `FIXED_STRING`
  blocks, imported through phase 1's readers, dump the same strings abcde
  does over the verification fixtures.
- Everything listed above exists.

## 2. Editing and writing

Edit strings beside the original and write them back in place.

- The encode engine with round-trip verification.
- The Strings view: columns, statuses, code completion, byte readout,
  filter, find and replace, selection sync with the raw view and Hex panel.
- Write in *slotted* and *packed* modes over range and fixed sources; the
  Hex panel's overtype editing; Write / Write All; undo for text and hex
  edits; unsaved tracking and the project prompts.
- Native script import; TSV/CSV and PO export and import.
- Complete when every string of the fixtures re-inserts to the original
  bytes untouched, and edited fixtures write what an Atlas run of the
  exported script writes.
- Everything listed above exists.

## 3. Pointers

- Mappings: LINEAR, LoROM, HiROM, GB, GBA, banked.
- Block sources *pointer table* and *pointer list*; string types *next
  pointer* and *Pascal*; strings per pointer; pointer rewriting on packed
  writes.
- Pointer discovery and its result grouping; pointer overlays in the raw
  view.
- Cartographer import of pointer blocks and export; Atlas export and import.
- Complete when the abcde example projects (Battle of Olympus excluded)
  import, dump identically, and re-insert to the original bytes.
- Everything listed above exists.

## 4. Finding text

- The Scan window: text-likeness scoring, terminator guessing, block
  creation from a region.
- Relative search extensions: 16-bit codes, case gap, wildcards.
- Table Editor fills from scan and search results.
- Complete when the scan locates every block of the fixtures from a table
  alone, with the right terminator.
- Everything listed above exists.

## 5. Compression and plugins

- The compression stage, the Decompressed view, Jump to Next, scan and To
  Block; decompressed blocks with spare-room fill on write.
- Built-in schemes: LZSS variants, bit-packed alphabets, the command LZ, RLE
  and the generic decompressors, plus Huffman over an in-ROM tree as a preset.
- User and project plugin folders, discovery, trust, refresh, load-issue
  reporting, pass-through for missing plugins.
- Complete when a compressed fixture round-trips through a block and a user
  plugin loads from each root.
- Everything listed above exists.

## 6. Layout and preview

- Font entries from PNG sheets with maps and widths; text boxes; code
  effects; the Preview window; the *overflows box* status; Wrap.
- Complete when the fixtures' strings render against their fonts and Wrap
  produces strings that no longer overflow.
- Everything listed above exists.

## Afterwards

Every phase above is built. The abcde example projects (Dragon Quest IV and
Dragon Warrior II) dump identically and re-insert to their original bytes;
`tests/test_examples.py` runs them when the ROMs are present and skips
otherwise.
Candidates for what comes next: embedded pointers inside strings,
Cartographer-style subclassing for game-specific structures, relocation to
free space, fonts read from the ROM through pixel formats, and a shared
library with celPix.
