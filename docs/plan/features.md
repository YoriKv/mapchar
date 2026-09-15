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
  - **container** — unwraps the file: an iNES or SNES copier header, an N64
    byte order, a `.smd` or SNES interleave, joined chips;
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
  - the **Codecs** bar: container, compression and **Table** (see
    [Reading the bytes](#reading-the-bytes));
  - the **Reading** bar: every setting of how the bytes are cut into strings
    (see [Blocks](#blocks));
  - the **Block** bar, shown on a block: source, string type, table, a string
    counter, and **Dump…**;
  - the central view, three tabs: **Hex**, **Text** and **Strings**;
  - the navigation bar under Hex and Text; the string status bar under Strings.
- **Hex** dock — optional, at the bottom, hidden by default.
- **Navigate** menu — **Back / Forward** through the entries visited, **Go to
  Address**, the ends of the file, and **Show Whole File**, live while the
  view is confined to a stretch of it.
- **Panels** menu — toggles each dock. **Reset Panel Layout** puts the docks
  back where a fresh install has them, leaving the window's own size alone.
- **Tool windows** — separate top-level windows that remember their
  placement and close on Esc: Search, Scan, Table Editor, Preview, Decompressed
  View, Find and Replace.
- **Long operations** — the text scan, the relative search, the structure scan
  and pointer discovery all run with a Stop button and a progress reading, and
  hand back whatever they found when stopped.
- **Window size and dock layout** are remembered per machine, never in the
  project. They are written a moment after they change rather than at quit, so
  a drag survives a crash.
- **Messages** — errors appear as modal warnings; progress and results go to
  the status bar, whose right end shows the file's size, the selection (in
  the address format) and any view-only notice.
- **Resizing** — the two bars wrap onto more rows as the column narrows and
  keep the height those rows need; no panel, window or dialog can be shrunk
  until its controls stop working, and none needs more than a small screen; text cut short shows
  in full on hover (see [../ui.md](../ui.md)).
- **Quitting** asks about an unsaved project first, then about unsaved file
  edits.

## Opening files

- **File ▸ Open ROM…** opens a file entry. The container is detected from the
  file's signature and extension. Opening a file already open selects it.
- **File ▸ Open Table…** registers a table file. Its dialect is detected from
  the header line, or asked for when there is none (see
  [table-format.md](table-format.md#legacy-dialects)).
- **File ▸ New Table…** writes an empty native table file where the user
  picks, registers it and opens it in the Table Editor. The table is named
  after the file, numbered up (`main_2`) past a loaded table of that name. The
  Codecs bar's Table list and the Files panel's context menu offer it too;
  from the Table list the new table becomes the reading's table.
- **File ▸ Open Font…** registers a glyph sheet.
- **File ▸ Import ▸** takes a Cartographer command file, an Atlas script, a
  native script or a translator file (see
  [Dump, export and import](#dump-export-and-import)).
- **Drag and drop** onto the window: `.mapchar` opens the project and claims
  the whole drop; `.tbl` registers a table; `.png` registers a font; `.tsv`,
  `.csv` and `.po` import as translator files; a `.txt` carrying a native
  script or table header imports or registers as that; anything else opens as
  a ROM. Hold **Ctrl** while dropping to be asked instead.

## The Files panel

- **Grouping** — rows are grouped under **ROMs**, **Tables** and **Fonts**.
  Blocks and bookmarks nest under their file, and a block opens to its
  strings: one row each, `index  text`, the text on one line and cut short
  with `…`; the tooltip has the string's address and its whole text. The rows
  are built while the block is open, and opening a block the session has not
  read reads it without showing it.
- **Row markers:**
  - icons for the kind on blocks, bookmarks and tables, from the bundled
    icon font; files and fonts sit under their group heading and carry
    none; a missing file shows a warning mark instead;
  - `●` for unsaved edits;
  - a string count and a status summary on blocks (edited / too long /
    review), there from the start: opening a project, and locating its
    missing files, reads every block over a file that is on disk;
  - an entry count on tables;
  - a joined-file count and a container tag on files;
  - **?** (file missing) or **!** (read notices), washing the row amber.
- **Tooltips** give paths, container, offset and length, the start table, and
  any notice text, each notice's fuller detail indented under it.
- **Selecting** — click opens an entry; a table opens the Table Editor and a
  font the Preview window's Font tab, neither becoming the view; Shift/Ctrl
  extend the selection; with several rows selected only Remove and Move
  Up/Down apply. A block's row confines the view to its source, from its
  start — the range, the fixed strings, the pointer table, or the stretch a
  pointer list's pointers lie in — and a string's row confines it to that
  string's bytes, with none of them selected (see [Raw view](#raw-view)). A
  string row's context menu is its block's.
- **Filter box** (Ctrl+F) matches every typed word in any order. A matching
  child keeps its parent visible, and a block one of whose strings matches
  opens to show it.
- **Double-click** — bookmark jumps; table opens the Table Editor; file or
  block renames inline. **F2** renames any row inline.
- **Names** — no two blocks or bookmarks share one: a new row, a rename, an
  import or a loaded project that would repeat a name gets it numbered
  (`Script (2)`), since dumps, translator files and Atlas scripts name a
  string by its block.
- **Reorder** by drag or Alt+Up/Down. **Sort by** Name, Type or (blocks)
  Offset.
- **Context menu**, by kind: New Block / from Selection, New Bookmark,
  Rename, Edit File Container…, Container Info…, Save As File…, New
  Table…, Write, Dump…, Export ▸, Show in File Manager, Remove. Empty space
  offers Open ROM…, Open Table…, New Table… and Paste.
- **Cut / Copy / Paste / Duplicate** act on entries (references plus
  settings), never on bytes. The clipboard carries absolute paths, so entries
  paste into another mapchar window.
- **Remove (Del)** asks once for the whole selection and names child blocks
  and bookmarks, unsaved edits being discarded, and blocks reading through a
  removed table: they keep their translations, but cannot be re-read or written
  until the table is loaded again.

## Tables

- **One table per file** — a table file holds one table, named by its
  `@table` line or else after the file.
- **Tables dock** lists one row per table, `@id` and its entry count, with the
  file and dialect in the tooltip. The table the current entry reads through is
  marked; double-click makes it the reading's table.
- **Dialects** — the native grammar loads directly. romjuice, Cartographer,
  Atlas and abcde files load through their dialect and are shown converted;
  **Save As File** writes the conversion out in the native grammar. A legacy
  file that yields several tables opens as one entry per table, the extra
  ones with no file. Conversion notices (dropped duplicates, renamed labels,
  generated kanji tables, split tables) are listed once per file.
- **Charsets** — a table can sit on a built-in charset (ASCII, Latin-1,
  Windows-1252, JIS X 0201, Shift-JIS as CP932, EUC-JP as JIS X 0213, EUC-KR,
  Big5, GBK, UTF-8, UTF-16) and only list its overrides.
- **Encodings as tables** — every charset is also offered in the Table list as
  a table of its own, under the loaded tables: that encoding with a NUL of its
  code unit's width (`00`, `0000` in UTF-16) as the end token. They are built
  the first time they are read, are never entries or saved, and a loaded table
  of the same id takes the id's place. A table file that is not UTF-8 is read as `cp932`, else
  `latin-1`, and says which in a notice.
- **Table Editor** (View ▸ Table Editor…) edits one table entry's table over
  a live view of the bytes, titled with its `@id` and file:
  - a grid of one row per key: its line in the native grammar, edited as
    text, beside what that line means (kind, operands, switches);
  - **Add** — a typed entry line; the raw view's **Add to Table…** prefills it
    from the selected bytes;
  - **Shift Keys** — moves a range of keys up or down by a constant;
  - **Fill…** — templates: `A–Z`, `a–z`, `0–9`, the three together, `あ-ん`,
    `ア-ン`, or a typed string, laid over consecutive keys from a start key;
    keys that already have entries are left alone unless the prompt is
    answered with Overwrite (a whole standard encoding is a **charset** on the
    table, not a fill);
  - every change is one undo step and re-decodes every view using the table;
  - the status line carries what reading the file had to report — a conversion
    from a legacy dialect, an encoding that is not UTF-8 — with each notice's
    fuller detail in its tooltip.
- **Where the edits live** — in the project, as an overlay of the entries
  added, changed and removed over the file, so the table file on disk keeps
  saying what it said for every other tool that reads it. **Save As File**
  writes them out and spends the overlay. A table with no file of its own —
  from a relative search, or from **Add from selection** — is carried whole by
  the project the same way.
- **Reload** — a table file edited outside the app is re-read when its
  timestamp changes, with a prompt if the in-app copy has edits. The edits stay
  on top of what was re-read, and win where they overlap.

## Reading the bytes

The Codecs and Reading bars say how the entry on screen is read, and every
change to them applies as it is made, as celPix's toolbar does: the Hex, Text
and Strings tabs read again at once.

- **Whose settings** — on a block, its own configuration: each change is an
  undo step that re-reads the block, its translations matched back by index,
  and a run of changes to one control (a spin box stepped several times) is
  one step. On a file, the file's session: saved with the project, carried by
  a bookmark, and what **New Block** starts from. Anything else that changes a
  block's configuration — an import, **Use as Pointer Table**, an undo — shows
  in the bars at the next refresh.
- **Table** — **Pointer**, then the loaded tables with their entry counts,
  then the encodings, then **New Table…**. A file with no table of its own
  reads as the first loaded table, else as ASCII. A table the reading names
  that is not loaded shows as `@id (not loaded)`.
- **Pointer** — the bytes are pointers: the source becomes a pointer table (or
  list), and beside the Table list come **Strings**, the table the strings they
  reach are read through, and **Show strings**, remembered per entry. On a file
  the pointers are read every stride from where the view starts; on a block,
  its own.
  - the **Hex** tab's text column shows each pointer where it points (`→1A3F0`,
    `→?` when it maps outside the data), or with Show strings the string there
    on one line; each is tinted as a pointer, and its hover says the value,
    the target and the string;
  - the **Text** tab shows a line per pointer: its address, its value, where it
    points and, with Show strings, the string there;
  - a line step in Text is a pointer.
- **A table** — the bytes are text through it, cut into strings by the
  reading's string type from the view's first byte: at end tokens, every
  fixed length, or by a Pascal prefix. What only means something at a block's
  own addresses — skip ranges, realignment, fixed lines — applies to its
  strings, not to the view.

## Raw view

The exploration surface, the equivalent of celPix's tile canvas.

- **Columns** — address, hex bytes, and the decode of those same bytes through
  the reading's table, or its pointers (see
  [Reading the bytes](#reading-the-bytes)). Rows are aligned and banded: every byte owns a fixed cell in
  both columns — three characters in the hex, with a small gap every four
  bytes, and two and a half in the text — and each token sits in the text
  column at its **bits**, so a 6-bit code takes three quarters of a cell and
  tokens that start inside one byte never share a place. A token over a row end
  is written on the row holding most of it.
- **Text in its cells** — text never leaves its token's cells: wider text is
  condensed, then cut short with a corner notch, and one character with nothing
  left to drop is condensed the rest of the way rather than sliced at the
  cell's edge. What is measured is what draws — the painter's own face on its
  own surface, and the ink rather than the advance — so text is never called
  narrow enough to fit and then drawn wider. A token whose whole text is
  one bracketed name shows the name as a smaller label; a name inside text
  shows as `↵` when it ends a line and `▪` otherwise, so `s[line]` reads `s↵`.
  Unmatched data is a dot. Hovering a token shows its whole text, its bytes and
  its table.
- **Decoding** — starts at the view offset and runs the full decode engine
  (switches, counts, end tokens), so the raw view shows exactly what a block
  starting there would extract. Decoding restarts in the start table after
  each string.
- **Marks** — end tokens, codes and switches are chips behind their token in
  both columns, one chip per token so where one ends reads; a token that shows
  nothing (a table switch) is a tick. Unmatched bytes are chipped in the hex
  column only. The current block's pointer bytes are chipped in the hex column,
  and the string boundaries it would produce are ruled in both.
- **Two tabs** — **Hex** shows the columns above; **Text** shows the decode
  from the same offset in an ordinary read-only text box, with a **Wrap**
  switch remembered per machine. Selections follow each other between the two,
  and the open tab is part of an entry's session.
- **A window the size of the box** — each tab shows what its box has room
  for, re-fitted whenever the box changes: Hex as many rows as fit and one
  more, cut off at the bottom edge; Text the whole lines that fit, decoding
  from the offset until the text overflows the box and cutting back to the
  tokens in view (with Wrap off, the last line ends at the box's right edge,
  and a wider line above it scrolls sideways). Neither tab scrolls within
  itself: the wheel moves the view by three rows, or in Text to the start of
  the line three lines away — a line of the text in view, or of the text
  decoded before it laid out in the box, so the view never starts inside a
  line or a character. A row step (Up / Down) is a row, or in Text a line; a
  page step is what was shown, or in Text as many lines as the box holds, so
  down is exactly what was shown. Each tab has a scrollbar over the whole file:
  its handle is the window, its arrows a row or line step, its trough a page
  step, and a drag goes to the row or byte under it.
- **Bounds** — the view can be confined to a stretch of the file: a block
  opens on its source, and a string's row in the Files panel confines it to
  that string. Inside them the tabs show those bytes and no more, the
  scrollbar spans them, Home and End are their ends, and no step leaves them;
  the status bar says what is in view. While the open tab shows the whole
  stretch from its start — a string in one row — the step buttons and their
  keys are off, since a step could only hide bytes that fit; the same goes for
  a file smaller than the window. Addresses stay the file's. Any position
  asked for outside them — a typed address, a search hit, a Strings row, an
  undo reaching its edit — widens the view to the whole file, as **Navigate ▸
  Show Whole File** does in place. A file is never confined, and a block
  returning to the screen is confined to its source again, keeping its
  position if that is inside it.
- **Navigation** — the address format (Hex, a console mapping preset, or Custom
  bank fields), an address box, Home, page and row steps, **−B / +B** byte
  steps, and End. A view narrower than its rows scrolls sideways. The format is remembered per machine and drives the address
  box, **Go to Address** and the Hex panel's address column alike. **Back /
  Forward** (Alt+Left / Alt+Right, or the browser buttons on a mouse) walk a
  trail of the entries visited.
- **The keys work wherever the focus is** — a picked table or a clicked row
  does not take the navigation keys away — except inside a text field or a list,
  which spend the arrows themselves. The Text tab's box is not one: read-only,
  it has no cursor for them to move.
- **Selection** — drag over hex or text; both columns follow, and a click in
  the text that selects nothing clears the selection in both. The hex selects
  whole bytes. The text selects characters by their **bits**, so a 6-bit code
  over two bytes is tinted as it straddles them — in the text, and in the hex
  down to the digit and bit, each digit being a nibble; the rest of the window
  gets the bytes it touches. The status bar shows offset, length and the
  selected bytes' decode.
- **Context menu** — New Block from Selection, New Bookmark, Add to Table
  (opens the Table Editor with the bytes as a key), Search for Selection
  (as bytes), Copy Hex, Copy Text.
- **Editing** — the raw view is read-only; editing happens in the Strings
  view or the Hex panel.

## Finding text

All searches run over the current file through its container and
compression, and report offsets in the file's coordinates.

- **Find** (Ctrl+F in a view) — hex bytes, or `"quoted text"` run through the
  encode engine, so multi-character entries, `[codes]` and table switches are
  all searchable; next and previous, wrapping.
- **Relative search** (Search Window):
  - type a word; the tool finds byte runs with the same relative pattern,
    for 8- and 16-bit codes, in either endianness;
  - **Case gap** — an optional constant between the upper- and lower-case
    runs, so `Hello` matches even when `H` and `e` are in different runs;
  - **Wildcards** — `?` matches any one code;
  - hits list offset, the matched bytes, and the inferred base for each run;
  - **Limit** — how many hits to keep; a search that reaches it says so, so
    the limit is never a silent truncation;
  - kana are runs too: hiragana and katakana in gojūon order, so a Japanese
    word finds its font the same way a Latin one does;
  - **Build table** — a hit seeds a new table (or extends the current one)
    with the inferred characters, given the alphabets to lay out (`A–Z`,
    `a–z`, `0–9`, `あ-ん`, `ア-ン`, or a custom order).
- **Scan** (Scan window) — scores the file for text-likeness under the start
  table, in windows of a chosen size:
  - the score is the fraction of bits that decode to text tokens, plus a
    capped bonus for dictionary-word hits in Latin tables, less a penalty that
    grows with the share of unmatched data;
  - results are ranked regions with their score, the most common terminator
    byte in each, and the byte the region most often starts strings with;
  - selecting a region jumps the raw view there; **New Block from Region**
    creates a block over it with the guessed end token;
  - the scan runs with a Stop button and a percentage readout.
- **Pointer discovery** — see [Pointers](#pointers).

## Blocks

A block is the unit of extraction and insertion. **File ▸ New Block** makes one
over the selection — else from the view's position to the end — read the way
the bars read the view, and opens it on its strings; from then on the bars are
its settings (see [Reading the bytes](#reading-the-bytes)). The Reading bar
shows the settings below that the source kind and string type use; a file has
no addresses of its own, so its bar leaves out start, stop, count, pointer
addresses, skip ranges and the writing settings.

- **Source** — where the strings come from; Pointer in the Table list offers
  the two pointer kinds, a table the other two:
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
  - **Pascal (length prefix)** — a `1–4`-byte length prefix counting bytes or token weights;
  - **Next pointer** — at the next pointer's target (pointer sources only;
    the last string ends at an end token or `stop`).
- **Strings per pointer** — how many end tokens one pointer's string spans.
- **Realign** — after each end token, round the position up to a multiple of
  `M` plus `O`.
- **Skip ranges** — `from → to` pairs: reading `from` continues at `to`
  (Cartographer's auto-jump).
- **Table** — the start table: a loaded table or an encoding, picked in the
  Table list, or in **Strings** while it says Pointer; the table set follows
  from it.
- **Fixed-line layout** — for fixed strings, an optional `line length` that
  splits each string into lines marked with a `[line]` code.
- **Bound** — the exclusive end address strings may not cross on write;
  defaults to `stop`, or to the last string's end for pointer sources.
- **Write mode** — **Packed** or **Slotted**; see [Writing](#writing-back-to-disk).
- **Fill byte** — what pads unused space on write.
- **Compression** — a block inherits its parent file's container and
  compression and may override the compression in the Codecs bar (on a file
  that picker previews a scheme instead), in which case it is a
  decompressed region over the compressed slot at its offset, with its own
  **spare room** rule (fill, or keep the bytes that were there).
- **Jump to Source** shows the parent file at the block's own offset in the
  raw view — its compressed slot for a decompressed block, its first pointer
  for a pointer list — read the way the block reads and, where it has one,
  with its compression armed in the Compression preview.

## Pointers

- **Mappings** — LINEAR, LoROM, HiROM, GB, GBA, and **Banked** (bank size,
  bank base address, bank number taken from the block or a field). Each
  applies after the container's header offset, then the block's `offset`.
- **Pointer table entry** in the Strings view — every string lists the
  pointers that reach it; a target reached by several pointers is one string
  with several pointers, written back to all of them.
- **Discovery** (**Search ▸ Find Pointers…**, Ctrl+Shift+P, on a block):
  - a first dialog asks what to cover: the selected string or every string in
    the block, and an offset range (from, to, step) to try;
  - for each of those strings, compute the pointer value under each mapping,
    each of 16/24/32 bits, both endiannesses and each offset in the range;
  - search the file for those byte patterns;
  - results are grouped by the (mapping, size, endian, offset) combination
    that explains the most strings, with the address range they occupy and
    the stride between them;
  - the search runs with a Stop button and a progress bar, and a stopped
    search still offers what it had ranked;
  - **Use as Pointer Table** converts the block's source to a pointer table
    from the chosen result, as one undo step; **Attach** adds the found addresses to the
    strings without changing the source.
- **Overlays** — the raw view marks bytes that are pointers of the current
  block, and jumping from a pointer to its target and back is a click.

## Strings view

The editing surface, opened on a block.

- **Columns** — `#`, address, pointers, **Original** (read-only, the decode
  of the bytes on disk), **Translation** (editable), bytes used / bytes
  available (a string read across a skip range counts both of its pieces),
  status, notes. The header's context menu hides and shows
  columns, and dragging a header section reorders them; Translation stays.
- **Status**, per string: **untouched**, **edited**, **too long** (the
  encoding does not fit; see [Writing](#writing-back-to-disk)), **invalid**
  (the translation cannot be encoded), **review** (set by hand or by import),
  and, when a preview font is bound, **overflows box**.
- **Editing** — the Translation cell is a multi-line editor:
  - typing edits text; `[` opens code completion listing the table set's
    codes with their operand shapes; Ctrl+Return (or Return) commits and Esc
    cancels;
  - **Shift+Return** writes the block's newline code — the code carrying the
    *newline* effect, else `[line]` — never a line break, which the script
    grammar drops;
  - **Insert code** buttons for the codes this block's strings use most,
    wrapping onto more rows when the view is narrow;
  - the byte readout updates as you type, from a live encode, against the
    room the string has; the Preview follows the draft and lists what the
    font cannot spell;
  - **Revert** copies Original back; **Copy Original to Empty Translations**
    fills the ones with none;
  - the Preview window's **Wrap Translation** (font and box bound) inserts line
    codes to fit the box; see [preview.md](preview.md#wrapping).
- **Filter** — words in any order over original, translation and notes; a
  status box narrows to one status.
- **Selection sync** — selecting a string highlights its bytes in the raw
  view and the Hex panel; selecting bytes there selects the string.
- **Bulk edit** — Find and Replace across translations of the block or the
  project, with code-aware matching (`[line]` matches only the code).
- **Undo** — a run of typing in one cell is one undo step.

## Writing back to disk

- **What a write does** — encodes every translation of a block through the
  table set, lays the results out, rewrites pointers, then runs the chain in
  reverse (compress, container) over the file as it stands on disk at that
  moment, and writes the result back — the block's own region is the only part
  of the file the write changes.
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
- **Bit-level tables** — an encoding that stops short of a byte is padded with
  zero bits to the byte, as the games and Atlas pad it: the next string, or
  the pointer to it, begins on a byte.
- **File ▸ Write (Ctrl+W)** writes the current block; **Write All
  (Ctrl+Shift+W)** writes every block with edits; the Files panel writes one
  entry. An entry that cannot be written says why: a bookmark has no bytes of
  its own, a table file is written with **Save As File…**, a glyph sheet is
  never written to, and a view-only entry names the stage that has no way
  back.
- **Blocks over one compressed region write together** — they are laid out
  into one decompressed buffer and it is compressed once, since the region
  holds one stream.
- **Other open entries on the same file refresh afterwards** — a block over a
  compressed region by decompressing again, since its bytes are a reading of
  the region rather than a window on it. One with unsaved edits keeps them.
- Opening, creating or saving a project with unsaved edits offers **Write All
  / Continue Without / Cancel**.

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
  from the current offset into the floating **Decompressed View**, decoded
  through the reading's table; it hides when nothing decodes.
- **Jump to Next** skips past a complete structure; **Scan** searches forward
  for the next complete structure with a Stop button; **To Block** creates a
  decompressed block over one. All three want a *complete* structure: the
  preview will show the prefix of a stream that runs out mid-way, but a
  prefix's length is the window's rather than the structure's, so nothing steps
  by it or records it as a block's slot.
- **Editing** decompressed text goes through such a block; writing
  re-compresses into the slot, and **spare room** decides what becomes of the
  room a shorter result leaves — *fill* writes the block's fill byte over it,
  *keep* writes short and leaves the old stream's tail standing. A result that
  does not fit is refused, and a slot whose length nobody recorded is bounded by
  the end of the region rather than unbounded.
- Schemes are plugins, and every one of them compresses back:
  - **LZSS family**, one engine and a preset per framing — GBA/NDS BIOS LZ77,
    the 4 KiB ring LZSS behind a u32 size prefix, the Okumura framing it came
    from, and SLZ16/SLZ24;
  - **command LZ** — LZ1 (Zelda 3) and LZ2 (SMW, Yoshi's Island), each with a
    byte-exact parse and a ~13%-smaller one;
  - **Kosinski** and **PRS**, Sega's two general-purpose schemes;
  - **RLE** — RLE1 and RLE2, Konami RLE in its Contra and FDS readings, and
    PackBits;
  - **bit-packed text**, registered ready-made in 5-, 6- and 7-bit alphabets
    and available as an engine for any other width;
  - **Huffman** through a node table in the ROM, which is the one scheme with
    nothing to register ready-made — the node layout and the tree's address are
    the format — so it ships as the `_huffman.toml` example over its engine.
- A scheme **with no end to find** — PackBits, RLE2, bit-packed text, the
  Okumura LZSS, Huffman with no end symbol — never reports a complete
  structure, so Scan cannot find one and a block over one carries an explicit
  length. Every decoder bounds what one read may produce, and a stream that
  runs out part way through is an **error** — except under a partial preview,
  which asks for the prefix decoded so far, so the raw view can show a
  structure continuing past its window without a short read passing for a
  whole one.

## Preview

A font entry plus a text box turn a string into a picture of how it lays out
in the game. It is described in [preview.md](preview.md).

## Hex panel

- **Panels ▸ Hex** — a dump (address · hex · ASCII) of the decoded
  buffer from the current offset, following the raw view's selection and
  tinting it in both columns.
- **Overtype** — typing a hex digit over a byte in the dump changes that
  nibble in place, one undo step per digit, the caret moving on to the next
  nibble; the bytes line below writes a run of hex bytes at an offset. Both make
  the file entry unsaved. Text is decoded, not editable here.
- **Go to**, **Find** (hex bytes or quoted text through the reading's table) with
  next and previous, and **Follow selection**, which is remembered per machine.
- The address column follows the navigation bar's address format.
- Refreshes only while visible.

## Projects

- A `.mapchar` project stores **references and settings, never bytes**:
  every entry with its chain, block configuration, a file's reading; per-string
  translations, statuses and notes; table edits made in-app; font bindings
  and text boxes; the view position per entry.
- Not saved: zoom, theme, window layout, undo history.
- **New / Open / Open Recent / Save / Save As** as in celPix; paths are stored
  relative to the project file; older versions are upgraded on load, which the
  status line says, and newer ones open with what this build understands.
  **Open Recent** lists projects by name, newest first, drops rows whose file
  has gone, and offers **Clear List**.
- **Missing files** — a project that references files that are not there offers
  to locate them as it opens, and **File ▸ Locate Missing Files…** walks them
  at any time. One answer corrects every entry that named that file, and a row
  still named after the file takes the new name.
- **Saving the project resolves unsaved edits first** — a project holds
  references, not bytes, so it asks to **Write All**, continue without writing,
  or cancel. Reopening it marks every block whose translations were never
  written unsaved again, so **Write All**, the file's Write and the `●` mark
  still cover them.
- **Unsaved marker** — the title bar shows the project unsaved when its
  serialized form differs from disk — a table edit included, since that is
  project state. A session that has never been saved as a project has nothing
  to differ from and never prompts.

## Plugins

- **Kinds** — **presets** (TOML naming a built-in engine plus parameters) and
  **code plugins** (Python files) for containers, compressions, charsets
  and mappings.
- **Where they live** — **File ▸ Open Plugins Folder…** opens
  `<AppData>/mapchar/plugins` with typed subfolders (`containers`,
  `compression`, `charsets`, `mappings`), seeded with `README.md` and an
  `_`-prefixed working example per folder, which a copy without the underscore
  activates; a `plugins/` folder beside a `.mapchar` file loads and unloads with
  that project; `MAPCHAR_PLUGIN_PATH` adds folders. A `.tbl` file in `charsets`
  registers as a charset named after the file, with no code at all.
- **Refresh Plugins (F5)** reloads everything and re-reads the current entry.
  Entries with unsaved edits keep what they hold rather than being re-read.
- **Trust** — code plugins ask for trust once per file content, with a
  SHA-256 prefix; the default is No, and a plugin that came with a project says
  so. A file approved this session can be edited and refreshed without asking
  again. Presets never ask.
- **Load failures** are reported in a dialog — at startup, after a refresh, as
  a project's folder is scanned, and again from **Open Plugins Folder…** — and
  never crash the app. A plugin whose trust prompt was declined is not a
  failure: it is said once in the status bar, not in a dialog every launch. An
  entry whose plugin is missing opens view-only through a pass-through.

## Undo

- **One history** for the session: entry open, close, paste, rename, reorder;
  block, container and table edits; view moves; translation edits and status
  changes; hex overtypes; font and box edits.
- **Ctrl+Z / Ctrl+Shift+Z** undo the latest action from any surface. Undoing
  a change made elsewhere switches back to that entry **and** the view it was
  made in — the Strings tab on its row, the Hex tab at its offset.
- **Unsaved state follows undo** — undoing back to the saved state reads
  clean again, and redoing marks it unsaved once more.
- **A run of edits on one string is one step**, and a run that ends back where
  it began is no step at all. Moving to another row, or to another entry, ends
  the run. Consecutive view moves in one entry merge the same way.
- Opening or starting a project clears the history and the visit trail.

## Keyboard reference

**Help ▸ Shortcuts… (F1)** shows the live list in two balanced columns, one
section per menu, built from the menu bar plus the keys and mouse gestures no
menu row can carry (the Hex and Text views, the Strings view, the Files and Hex panels, the
tool windows). **Help ▸ Legend…** explains every colour and mark the Hex and
Text views, the Hex panel and the Strings view draw, each beside a swatch.
**Help ▸ About** gives the version, author, homepage and licenses.

| Area | Keys |
|---|---|
| File | Ctrl+N / Ctrl+O / Ctrl+S / Ctrl+Shift+S projects · Ctrl+Shift+O Open ROM · Ctrl+T Open Table · Ctrl+Shift+B New Block · Ctrl+B New Bookmark · Ctrl+E Edit File Container · Ctrl+W Write · Ctrl+Shift+W Write All · Ctrl+D Dump · F5 Refresh Plugins · Ctrl+Q Quit |
| Edit | Ctrl+Z / Ctrl+Shift+Z · Ctrl+X / C / V · Ctrl+H Find and Replace · Ctrl+Return commit cell |
| View | Ctrl+1 Hex · Ctrl+2 Text · Ctrl+3 Strings · Ctrl+Shift+T Table Editor · Ctrl+P Preview |
| Navigate | Alt+Left/Right history (also mouse 4/5) · Home/End · Up/Down row · Left/Right or - / + byte · PgUp/PgDn page · Ctrl+G go to address |
| Search | Ctrl+Shift+F Search Window · Ctrl+Shift+R scan · Ctrl+F find bytes · F3 / Shift+F3 next / previous · Ctrl+Shift+P find pointers |
| Files panel | Shift/Ctrl+click extend · Alt+Up/Down reorder · Ctrl+X/C/V/D entries · Del remove · Ctrl+F filter · F2 rename |
| Hex panel | 0-9 / A-F overtype · Enter go to, find or overtype · Shift+Enter find previous |
| Tool windows | Esc close · Enter run the query |
