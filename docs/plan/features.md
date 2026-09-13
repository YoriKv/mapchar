# mapchar features

What the app does and how a user drives it. How it is built lives in
[architecture.md](architecture.md); the file formats in
[table-format.md](table-format.md) and [script-format.md](script-format.md);
the preview system in [preview.md](preview.md).

## Contents

1. [Core concepts](#core-concepts)
2. [Window layout](#window-layout)
3. [Opening files](#opening-files)
4. [The Files panel](#the-files-panel)
5. [Tables](#tables)
6. [Raw view](#raw-view)
7. [Finding text](#finding-text)
8. [Blocks](#blocks)
9. [Pointers](#pointers)
10. [Strings view](#strings-view)
11. [Writing back to disk](#writing-back-to-disk)
12. [Dump, export and import](#dump-export-and-import)
13. [Compression](#compression)
14. [Preview](#preview)
15. [Hex panel](#hex-panel)
16. [Projects](#projects)
17. [Plugins](#plugins)
18. [Undo](#undo)
19. [Keyboard reference](#keyboard-reference)

---

## Core concepts

- **Entry** — one row in the Files panel. Its *kind* says how it is bounded:
  - **file** — a whole ROM or binary, or several files joined end to end;
  - **block** — a region of a file read as a list of strings under one
    configuration (source, table set, string rules). The block is the unit of
    dumping, editing and writing, and the equivalent of a celPix slice;
  - **bookmark** — a saved offset and settings snapshot in a file;
  - **table** — a table file registered for use by blocks and the raw view;
  - **font** — a glyph sheet registered for [preview](preview.md).
- **Interpretation chain** — bytes reach the screen through three configurable
  steps, each run in reverse on write:
  - **container** — unwraps the file: iNES header, SNES copier header, joined
    chips;
  - **compression** — Huffman, LZ, bit-packed text;
  - **encoding** — the table set plus the string rules that cut the bytes
    into strings.

  A step with no write-back makes the entry view-only.
- **Table set** — the tables a block or view decodes through: a **start
  table** plus every table reachable from it by switch entries.
- **Token** — one table entry matched against the bytes. A token is either
  **text** or a **code**: something in square brackets, such as `[end]`,
  `[line]`, `[color $03]`, or an unmatched byte `[$1F]`.
- **String** — one extracted unit of a block: its original tokens, where they
  sit, the pointers that reach it, and an editable translation.
- **Mapping** — the rule that turns a pointer value into a file offset and
  back: LINEAR, LoROM, HiROM, GB, GBA or a generic banked layout.

## Window layout

- **Left column:** the **Files** dock on top. Below it, **Tables** and
  **Fonts** share one tabbed dock.
- **Right column:** the editing surface, top to bottom:
  - the **Codecs** bar (container, compression, start table);
  - the **Block** bar, shown on a block: source, string type, mapping, and a
    string counter;
  - the central view, a tab pair: **Raw** and **Strings**;
  - the navigation bar under Raw; the string status bar under Strings.
- **Hex** dock — optional, at the bottom, hidden by default.
- **Panels** menu — toggles each dock. **Reset Panel Layout** restores the
  defaults.
- **Tool windows** — separate top-level windows that remember their
  placement: Search, Scan, Table Editor, Preview, Decompressed view.
- **Window size and dock layout** are remembered per machine, never in the
  project.
- **Messages** — errors appear as modal warnings; progress and results go to
  the status bar.
- **Quitting** asks about an unsaved project first, then about unsaved file
  edits.

## Opening files

- **File ▸ Open ROM…** opens a file entry. The container is detected from the
  file's signature and extension. Opening a file already open selects it.
- **File ▸ Open Table…** registers a table file. Its dialect is detected from
  the header line, or asked for when there is none (see
  [table-format.md](table-format.md#legacy-dialects)).
- **File ▸ Open Font…** registers a glyph sheet.
- **File ▸ Import ▸** takes a Cartographer command file, an Atlas script, a
  native script or a translator file (see
  [Dump, export and import](#dump-export-and-import)).
- **Drag and drop** onto the window: `.mapchar` opens the project; `.tbl`
  registers a table; `.png` registers a font; a script or translator file
  imports; anything else opens as a ROM. Hold **Ctrl** while dropping to be
  asked.

## The Files panel

- **Grouping** — rows are grouped under **ROMs**, **Tables** and **Fonts**.
  Blocks and bookmarks nest under their file.
- **Row markers:**
  - icons for the kind on blocks, bookmarks and tables, from the bundled
    icon font; files and fonts sit under their group heading and carry
    none; a missing file shows a warning mark instead;
  - `●` for unsaved edits;
  - a string count and a status summary on blocks (edited / too long /
    review);
  - a joined-file count and a container tag on files;
  - **?** (file missing) or **!** (read notices), washing the row amber.
- **Tooltips** give paths, container, offset and length, the start table, and
  any notice text.
- **Selecting** — click opens an entry; Shift/Ctrl extend the selection; with
  several rows selected only Remove and Move Up/Down apply.
- **Filter box** (Ctrl+F) matches every typed word in any order. A matching
  child keeps its parent visible.
- **Double-click** — bookmark jumps; table opens the Table Editor; file or
  block renames inline.
- **Reorder** by drag or Alt+Up/Down. **Sort by** Name, Type or (blocks)
  Offset.
- **Context menu**, by kind: New Block… / from Selection, New Bookmark,
  Rename, Edit…, Edit File Container…, Container Info…, Write, Dump…,
  Export ▸, Show in File Manager, Remove.
- **Cut / Copy / Paste / Duplicate** act on entries (references plus
  settings), never on bytes. The clipboard carries absolute paths, so entries
  paste into another mapchar window.
- **Remove (Del)** asks once for the whole selection and names child blocks
  and bookmarks, unsaved edits being discarded, and blocks using a removed
  table (they keep an in-project copy of it).

## Tables

- **Tables dock** lists registered table files and the tables inside each
  (`@table` ids). The start table of the current entry is marked.
- **Dialects** — the native grammar loads directly. romjuice, Cartographer,
  Atlas and abcde files load through their dialect and are shown converted;
  **Save As Native** writes the conversion out. Conversion notices (dropped
  duplicates, renamed labels, generated kanji tables) are listed once per
  file.
- **Charsets** — a table can sit on a built-in charset (ASCII, Shift-JIS,
  EUC-JP, UTF-16) and only list its overrides.
- **Table Editor** (View ▸ Table Editor…) edits a table over a live view of
  the bytes:
  - a grid of Key / Text / Kind (text, end, code, switch) / Weight, with the
    operand and switch fields shown for the kinds that have them;
  - **Add from selection** — the bytes selected in the raw view become a new
    key;
  - **Shift keys** — moves a range of keys up or down by a constant;
  - **Fill…** — templates: `A–Z`, `a–z`, `0–9`, a typed string laid over a
    key range, or a charset;
  - every change is one undo step and re-decodes every view using the table.
- **Reload** — a table file edited outside the app is re-read on request or
  when its timestamp changes, with a prompt if the in-app copy has edits.

## Raw view

The exploration surface, the equivalent of celPix's tile canvas.

- **Columns** — address, hex bytes, and the decode of those same bytes through
  the start table. Rows are aligned: a token spanning several bytes is drawn
  across its bytes, so hex and text line up.
- **Decoding** — starts at the view offset and runs the full decode engine
  (switches, counts, end tokens), so the raw view shows exactly what a block
  starting there would extract. Decoding restarts in the start table after
  each end token.
- **Marks** — end tokens, codes, unmatched bytes and switch labels are tinted;
  the string boundaries the current block would produce are ruled.
- **Display modes** — **Aligned** (the columns above) or **Text**: the same
  window decoded into an ordinary read-only text box, with a **Wrap**
  switch remembered per machine. The toggle is a button on the navigation
  bar (Ctrl+Shift+A) and is remembered per machine. Selections follow each
  other between the two modes.
- **Navigation** — the address row (Hex, a mapping preset, or Custom bank
  fields), an offset box, page and row steps, **−B / +B / 0B** byte nudges,
  Home/End, and a file-position rail. **Back / Forward** walk a trail of
  visited entries.
- **Selection** — drag over hex or text; both columns follow. The status bar
  shows offset, length and the selected bytes' decode.
- **Context menu** — New Block from Selection, New Bookmark, Add to Table
  (opens the Table Editor with the bytes as a key), Search for Selection
  (as bytes), Copy Hex, Copy Text.
- **Editing** — the raw view is read-only; editing happens in the Strings
  view or the Hex panel.

## Finding text

All searches run over the current file through its container and
compression, and report offsets in the file's coordinates.

- **Find** (Ctrl+F in a view) — hex bytes, or text encoded through the start
  table; next and previous, wrapping.
- **Relative search** (Search window):
  - type a word; the tool finds byte runs with the same relative pattern,
    for 8- and 16-bit codes, in either endianness;
  - **Case gap** — an optional constant between the upper- and lower-case
    runs, so `Hello` matches even when `H` and `e` are in different runs;
  - **Wildcards** — `?` matches any one code;
  - hits list offset, the matched bytes, and the inferred base for each run;
  - **Build table** — a hit seeds a new table (or extends the current one)
    with the inferred letters, given the alphabets to lay out (`A–Z`, `a–z`,
    `0–9`, or a custom order).
- **Scan** (Scan window) — scores the file for text-likeness under the start
  table, in windows of a chosen size:
  - the score is the fraction of bytes that decode to text tokens, weighted
    by dictionary-word hits for Latin tables;
  - results are ranked regions with their score, the most common terminator
    byte in each, and the byte the region most often starts strings with;
  - **Go to** jumps the raw view there; **New Block** creates a block over
    the region with the guessed end token;
  - the scan runs with a Stop button and a progress bar.
- **Pointer discovery** — see [Pointers](#pointers).

## Blocks

A block is the unit of extraction and insertion. **File ▸ New Block…** and
the block's **Edit…** open the block dialog.

- **Source** — where the strings come from:
  - **Range** — `start` to `stop` (exclusive), read as consecutive strings;
  - **Pointer table** — `start`, `stop`, pointer `size`, `stride` (size plus
    space), `endian`, `mapping`, and an `offset` added to each value; strings
    are read at each target;
  - **Pointer list** — explicit pointer addresses, one per line, with the
    same pointer fields;
  - **Fixed strings** — `start`, `count`, `length`; each string is exactly
    `length` bytes.
- **String type** — how a string ends:
  - **End token** — at the first end token of the table set;
  - **Fixed length** — after `length` bytes, or earlier at an end token when
    **Stop at end token** is on;
  - **Pascal** — a `1–4`-byte length prefix counting bytes or token weights;
  - **Next pointer** — at the next pointer's target (pointer sources only;
    the last string ends at an end token or `stop`).
- **Strings per pointer** — how many end tokens one pointer's string spans.
- **Realign** — after each end token, round the position up to a multiple of
  `M` plus `O`.
- **Skip ranges** — `from → to` pairs: reading `from` continues at `to`
  (Cartographer's auto-jump).
- **Start table** — one of the registered tables; the table set follows from
  it.
- **Fixed-line layout** — for fixed strings, an optional `line length` that
  splits each string into lines marked with a `[line]` code.
- **Bound** — the exclusive end address strings may not cross on write;
  defaults to `stop`, or to the last string's end for pointer sources.
- **Write mode** — **Packed** or **Slotted**; see [Writing](#writing-back-to-disk).
- **Fill byte** — what pads unused space on write.
- A block inherits its parent file's container and compression and
  may override compression, in which case it is a decompressed region with
  its own **spare room** rule (keep bytes, fill).
- **Jump to Source** shows the parent file at the block's offset in the raw
  view.

## Pointers

- **Mappings** — LINEAR, LoROM, HiROM, GB, GBA, and **Banked** (bank size,
  bank base address, bank number taken from the block or a field). Each
  applies after the container's header offset, then the block's `offset`.
- **Pointer table entry** in the Strings view — every string lists the
  pointers that reach it; a target reached by several pointers is one string
  with several pointers, written back to all of them.
- **Discovery** (Strings view ▸ Find Pointers, or Search window):
  - for the selected string (or every string in the block), compute the
    pointer value under each mapping and each of 16/24/32 bits, both
    endiannesses, with an optional offset range;
  - search the file for those byte patterns;
  - results are grouped by the (mapping, size, endian, offset) combination
    that explains the most strings, with the address range they occupy and
    the stride between them;
  - **Use as pointer table** converts the block's source to a pointer table
    from the chosen result; **Attach** adds the found addresses to the
    strings without changing the source.
- **Overlays** — the raw view marks bytes that are pointers of the current
  block, and jumping from a pointer to its target and back is a click.

## Strings view

The editing surface, opened on a block.

- **Columns** — `#`, address, pointers, **Original** (read-only, the decode
  of the bytes on disk), **Translation** (editable), bytes used / bytes
  available, status, notes. Columns can be hidden and reordered.
- **Status**, per string: **untouched**, **edited**, **too long** (the
  encoding does not fit; see [Writing](#writing-back-to-disk)), **invalid**
  (the translation cannot be encoded), **review** (set by hand or by import),
  and, when a preview font is bound, **overflows box**.
- **Editing** — the Translation cell is a multi-line editor:
  - typing edits text; `[` opens code completion listing the table set's
    codes with their operand shapes; Ctrl+Return commits;
  - **Insert code** buttons for the most-used codes of the table set;
  - the byte readout updates as you type, from a live encode;
  - **Revert** copies Original back; **Copy Original to All** fills empty
    translations;
  - **Wrap** (preview bound only) inserts line codes to fit the box; see
    [preview.md](preview.md#wrapping).
- **Filter** — words in any order over original, translation and notes;
  status chips narrow to one status; **Go to address** selects the string
  covering an offset.
- **Selection sync** — selecting a string highlights its bytes in the raw
  view and the Hex panel; selecting bytes there selects the string.
- **Bulk edit** — Find and Replace across translations of the block or the
  project, with code-aware matching (`[line]` matches only the code).
- **Undo** — a run of typing in one cell is one undo step.

## Writing back to disk

- **What a write does** — encodes every translation of a block through the
  table set, lays the results out, rewrites pointers, then runs the chain in
  reverse (compress, container) and writes only the bytes that
  changed.
- **Write mode:**
  - **Packed** (default with pointers) — strings are laid end to end from the
    block's first string address, each pointer is rewritten to its string's
    new position, and leftover space up to the bound gets the fill byte;
  - **Slotted** (default without pointers, and always for fixed strings) —
    every string stays at its address and may use up to its original extent
    (the gap to the next string, or its fixed length), padded with the fill
    byte.
- **In place only** — a string or block that does not fit is **too long**:
  the write is refused, and the strings that would cross the bound are
  listed with the bytes over. Nothing is relocated; making room is the
  user's job.
- **Encoding is verified** — every encoded string is decoded again and must
  give back the same tokens; a mismatch is **invalid** and blocks the write.
- **File ▸ Write (Ctrl+W)** writes the current block; **Write All
  (Ctrl+Shift+W)** writes every block with edits; the Files panel writes one
  entry. Blocks over one compressed region write together.
- Other open entries on the same file refresh afterwards. Opening, creating
  or saving a project with unsaved edits offers **Write All / Continue
  Without / Cancel**.

## Dump, export and import

- **Dump…** (block or file) writes a native script: one file per block or one
  for all, with originals, translations or both (see
  [script-format.md](script-format.md#native-script)).
- **Import script** reads a native script back: strings are matched by block
  and index, text becomes the translation, and strings that differ from the
  original are marked **edited**. Unknown blocks are created when the script
  carries their configuration.
- **Translator files** — **Export ▸ TSV / CSV** and **Export ▸ PO** write one
  row or entry per string; **Import** reads them back by id. Rows whose
  original no longer matches are reported and skipped unless forced.
- **Cartographer** — **Import** reads a command file into blocks (one per
  `#BLOCK`) and its tables through the abcde dialect, then extracts. **Export**
  writes a command file for a block whose settings Cartographer can express,
  and says what it cannot.
- **Atlas** — **Export** writes an Atlas script plus abcde-dialect tables for
  a block; **Import** reads the subset of Atlas commands that map to block
  settings and reports the rest.
- **Export raw** writes the block's decoded, decompressed bytes.

## Compression

- **Preview** — the raw view always shows the bytes of the chain as far as
  the block's compression. Choosing a scheme in **Compression** decompresses
  from the current offset into the floating **Decompressed view**, decoded
  through the start table; it hides when nothing decodes.
- **Jump to Next** skips past a complete structure; **Scan** searches forward
  for the next complete structure with a Stop button; **To Block** creates a
  decompressed block over the structure.
- **Editing** decompressed text goes through such a block; writing
  re-compresses into the slot and **spare room** fills the rest.
- Schemes are plugins: Huffman (tree in ROM, table-driven), LZSS variants,
  bit-packed text (5/6-bit alphabets), and the generic decompressors shared
  with graphics tools.

## Preview

A font entry plus a text box turn a string into a picture of how it lays out
in the game. It is described in [preview.md](preview.md).

## Hex panel

- **Panels ▸ Hex Panel** — a dump (address · hex · ASCII) of the decoded
  buffer from the current offset, following the raw view's selection.
- **Overtype** — typing a hex digit over a byte in the dump changes that
  nibble in place, one undo step per digit; the bytes line below writes a
  run of hex bytes at an offset. Both make the file entry unsaved. Text is
  decoded, not editable here.
- **Go to**, **Find** (hex bytes or quoted text through the start table),
  **Follow selection**.
- Refreshes only while visible.

## Projects

- A `.mapchar` project stores **references and settings, never bytes**:
  every entry with its chain, block configuration and start table; per-string
  translations, statuses and notes; table edits made in-app; font bindings
  and text boxes; the view position per entry.
- Not saved: zoom, theme, window layout, undo history.
- **New / Open / Open Recent / Save / Save As** as in celPix; paths are stored
  relative to the project file; missing files prompt **Locate…**; older
  versions are upgraded on load and newer ones open with what this build
  understands.
- **Unsaved marker** — the title bar shows the project unsaved when its
  serialized form differs from disk.

## Plugins

- **Kinds** — **presets** (TOML naming a built-in engine plus parameters) and
  **code plugins** (Python files) for containers, compressions, charsets
  and mappings.
- **Where they live** — **File ▸ Open plugins folder…** opens
  `<AppData>/mapchar/plugins` with typed subfolders (`containers`,
  `compression`, `charsets`, `mappings`), seeded with `_`-prefixed
  examples and a README; a `plugins/` folder beside a `.mapchar` file loads
  with that project; `MAPCHAR_PLUGIN_PATH` adds folders.
- **Refresh plugins (F5)** reloads everything and re-reads the current entry.
- **Trust** — code plugins ask for trust once per file content, with a
  SHA-256 prefix; the default is No. Presets never ask.
- **Load failures** are reported in a dialog and never crash the app. An
  entry whose plugin is missing opens view-only through a pass-through.

## Undo

- **One history** for the session: entry open, close, paste, rename, reorder;
  block, container and table edits; view moves; translation edits and status
  changes; hex overtypes; font and box edits.
- **Ctrl+Z / Ctrl+Shift+Z** undo the latest action from any surface. Undoing
  a change made elsewhere switches back to that entry and view.
- **Unsaved state follows undo** — undoing back to the saved state reads
  clean again.
- Opening or starting a project clears the history.

## Keyboard reference

**Help ▸ Shortcuts… (F1)** shows the live list.

| Area | Keys |
|---|---|
| File | Ctrl+N / Ctrl+O / Ctrl+S / Ctrl+Shift+S projects · Ctrl+B New Bookmark · Ctrl+E Edit File Container · Ctrl+W Write · Ctrl+Shift+W Write All · Ctrl+D Dump · F5 Refresh plugins · Ctrl+Q Quit |
| Edit | Ctrl+Z / Ctrl+Shift+Z · Ctrl+X / C / V · Ctrl+F Find · Ctrl+H Find and Replace · Ctrl+Return commit cell |
| View | Ctrl+1 Raw · Ctrl+2 Strings · Ctrl+= / Ctrl+- zoom · Ctrl+T Table Editor · Ctrl+P Preview |
| Navigate | Alt+Left/Right history · Home/End · arrows byte/row · PgUp/PgDn page · - / + byte nudge · 0 clear nudge · Ctrl+G go to address |
| Search | Ctrl+Shift+F Search window · Ctrl+Shift+R relative search · F3 / Shift+F3 next / previous |
| Files panel | Shift/Ctrl+click extend · Alt+Up/Down reorder · Ctrl+X/C/V/D entries · Del remove · Ctrl+F filter |
