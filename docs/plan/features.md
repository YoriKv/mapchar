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
  - **folder** — a group of a file's blocks, bookmarks and folders in the
    Files panel; it changes nothing about the rows it holds;
  - **table** — a table file registered for use by blocks and the raw view.
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
- **String** — one extracted unit of a block: the tokens its bytes decode to
  now, where they sit, the pointers that reach it, and the **original** — its
  text as it was when the block was made, which the project keeps. The bytes
  are the translation: editing a string rewrites them.
- **Mapping** — the rule that turns a pointer value into a file offset and
  back: LINEAR, LoROM, HiROM, GB, GBA or a generic banked layout.

## Window layout

- **Left column:** the **Files** dock, which lists the tables too.
- **Right column:** the editing surface, top to bottom:
  - the **Format** bar: **Table** with **Edit…**, the **Show as**
    **Strings** / **Pointers** mode and, for pointers, **Follow pointers** (see
    [Reading the bytes](#reading-the-bytes));
  - the **Reading** bar: every setting of how the bytes are cut into strings,
    in framed sections — **Source**, **Pointers**, **Strings**, **Writing**
    (see [Blocks](#blocks));
  - the **Block** bar, shown on a block: source, string type, table, a string
    counter, and **Dump…**;
  - the central view, three tabs: **Hex**, **Text** and **Strings**;
  - the navigation bar under Hex and Text; the string status bar under Strings.
- **Hex** dock — optional, at the bottom, hidden by default.
- **Navigate** menu — **Back / Forward** through the entries visited, **Go to
  Address** and the ends of the file.
- **Panels** menu — toggles each dock. **Reset Panel Layout** puts the docks
  back where a fresh install has them, leaving the window's own size alone.
- **Tool windows** — separate top-level windows that remember their
  placement and close on Esc: Search, Scan, Table Editor, Preview, Decompressed
  View, Find and Replace, Project Strings, Glossary.
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
  Table list and the Files panel's context menu offer it too; from the
  Table list the new table becomes the reading's table.
- **File ▸ Refresh Tables (Shift+F5)** reads every registered table file again
  and refreshes the ones whose contents changed, saying how many in the status
  bar.
- **File ▸ Import ▸** takes a Cartographer command file, an Atlas script, a
  native script or a translator file (see
  [Dump, export and import](#dump-export-and-import)).
- **Drag and drop** onto the window: `.mapchar` opens the project and claims
  the whole drop; `.tbl` registers a table; `.tsv`,
  `.csv` and `.po` import as translator files; a `.txt` carrying a native
  script or table header imports or registers as that; anything else opens as
  a ROM. Hold **Ctrl** while dropping to be asked instead.

## The Files panel

- **Grouping** — rows are grouped under **String Data** and **Tables**.
  Blocks and bookmarks nest under their file, in folders or directly, and a
  block opens to its strings: one row each, `index  text`, the text on one line and cut short
  with `…`; the tooltip has the string's address and its whole text. The rows
  are built while the block is open, and opening a block the session has not
  read reads it without showing it.
- **A nested block opens to its inner tables**, not straight to its strings:
  one row per group (see [Nested tables](#blocks)), `address  n strings`, with
  that group's strings under it; the tooltip has the inner table's address,
  the base its pointers count from and the count. The groups are what such a
  block is — one archive entry's offset table and the text it reaches — and a
  block of hundreds of them would otherwise open to one flat list of
  thousands. A group's own rows are built only while its row is open.
  Clicking a group takes the view to its inner table and leaves the block
  current with all of its strings: a group is where to look, not a reading of
  its own, so there is nothing to come back out of.
- **Folders** group a file's blocks, bookmarks and other folders, to any
  depth. A folder belongs to one file, and moving a row into or out of one
  changes neither its file nor its configuration. A folder opens and closes
  like any row, and stays as it was left over rebuilds for the rest of the
  project's session; closing the project forgets it.
- **Row markers:**
  - icons for the kind on blocks, bookmarks, folders and tables, from the bundled
    icon font; files and fonts sit under their group heading and carry
    none; a missing file shows a warning mark instead;
  - `●` for unsaved edits;
  - a string count and a status summary on blocks (edited / review / done), there
    from the start: opening a project, and locating its
    missing files, reads every block over a file that is on disk;
  - on a folder, how many rows it holds directly, and the status summary of
    every block inside it at any depth added up (`Battle  (12 items, 40
    edited, 3 done)`);
  - an entry count on tables;
  - a joined-file count and a container tag on files;
  - **?** (file missing) or **!** (read notices), washing the row amber.
- **Tooltips** give paths, container, offset and length, the start table, and
  any notice text, each notice's fuller detail indented under it.
- **Selecting** — click opens an entry, and an arrow key onto a row opens it
  the same way; a table opens the Table Editor without becoming the view; a
  folder, like a group heading, leaves the view as it was; Shift/Ctrl extend the
  selection; with several rows selected only Remove, Move
  Up/Down and, on a file's rows, New Folder apply. A block's row confines the view to its source, from its
  start — the range, the fixed strings, the pointer table, or the stretch a
  pointer list's pointers lie in, unless it was left reading its strings, which
  it comes back on — and a string's row confines it to that
  string's bytes, with none of them selected (see [Raw view](#raw-view)),
  read as text even when the block reads pointers: the mode shows **Strings**
  and the Reading bar only its **Strings** and **Writing** sections. A string
  row's context menu is its block's, with everything that edits the row —
  Rename…, Cut, Copy, Duplicate, Move Up / Down, Sort By ▸, New Folder,
  Remove — greyed, since those name the block and not the string clicked.
- **Filter box** (Ctrl+F) matches every typed word in any order. A matching
  child keeps its parent and its folders visible, and a folder, a block or a
  nested block's group with a match inside opens to show it — a closed group
  is matched by the strings it holds, and only one holding a match has its
  rows built; clearing the filter closes again the folders that were closed
  and the groups it opened, bar one the view has gone into.
- **Double-click** — bookmark jumps; table opens the Table Editor; file,
  block or folder renames inline. **F2** renames any row inline.
- **Names** — no two blocks or bookmarks share one: a new row, a rename, an
  import or a loaded project that would repeat a name gets it numbered
  (`Script (2)`), since dumps, translator files and Atlas scripts name a
  string by its block. A folder's name is free text and never counts: New
  Folder numbers its default among the file's folders, and nothing else does.
- **New Folder** on a file or a folder makes an empty folder last inside it;
  on a block, a bookmark or a folder among selected rows, a folder in the
  clicked row's place holding the selected rows of that file. Either is one
  undo step, and the new row opens for its name.
- **Reorder** by drag or Alt+Up/Down. A drag moves the selection when it
  starts on it: dropped between two rows of a file or folder, the rows land
  there; dropped onto a file or folder, they go last inside it. A file's rows
  only move under that file and a folder never into itself; files and tables
  only reorder within their group. **Sort by** Name, Type or
  (a file's rows) Offset puts the rows the clicked row sits among in order —
  one folder's, one file's top level, or one group's. By name and by type
  folders come first; by offset a folder sits where its earliest row does.
- **Context menu**, by kind: New Block / from Selection, New Bookmark, New
  Folder, Edit File Container…, Container Info…, Dump All Blocks… (on a
  folder, the blocks inside it), Dump…, Jump to
  Source, Jump to Bookmark, Edit…, Save As File…, New Table…, Write, Export ▸,
  Rename…, Cut / Copy / Paste / Duplicate, Move Up / Down, Sort By ▸, Show in
  File Manager, Remove. Empty space offers Open ROM…, Open Table…, New Table…
  and Paste. A row the clicked kind cannot do is greyed, never dropped: Write
  on a bookmark or a table (a bookmark has no bytes of its own; a table is
  written with Save As File…), Duplicate on a file or a table (either is its
  path, so it can only be open once), Sort By ▸ Offset outside a file's rows,
  and Paste with nothing on the clipboard.
- **Cut / Copy / Paste / Duplicate** act on entries (references plus
  settings), never on bytes. The clipboard carries absolute paths, so entries
  paste into another mapchar window. A folder travels with what it holds.
  Rows pasted onto a folder, or onto a row inside one, land in that folder;
  a duplicate lands in the folder its original is in.
- **Remove (Del)** asks once for the whole selection and names the rows that
  go with it (a file's rows; a folder's contents, which a folder takes as a
  file takes its rows and as a cut carries them, so one step and its undo
  cover the whole group), unsaved edits being discarded, and blocks reading
  through a
  removed table: they keep their originals and notes, but cannot be read or
  edited until the table is loaded again. When the current entry goes, the nearest
  row of the same group takes its place — after the hole, else before it —
  so removing a block never puts a table in the hex and text panels; failing
  that, a String Data entry.

## Tables

- **One table per file** — a table file holds one table, named by its
  `@table` line or else after the file.
- **The Files panel's Tables group** lists one row per table, with the file,
  the dialect and the entry count in the tooltip. Which table the current entry
  reads through is the Format bar's **Table** pick.
- **Dialects** — the native grammar loads directly. romjuice, Cartographer,
  Atlas and abcde files load through their dialect and are shown converted;
  **Save As File** writes the conversion out in the native grammar. A legacy
  file that yields several tables opens as one entry per table, the extra
  ones with no file. Conversion notices (dropped duplicates, renamed labels,
  generated kanji tables, split tables) are listed once per file.
- **Charsets** — a table can sit on a built-in charset (ASCII, Latin-1,
  Windows-1252, JIS X 0201, Shift-JIS as CP932, GBK, UTF-8, UTF-16 LE) and only
  list its overrides.
- **Includes** — a table can start from other loaded tables (`@include`) and
  only list what differs: a battle table that is the script's with a few codes
  redefined, or two scripts' tables sharing one table of control codes. An
  include that is not loaded, or tables that include each other, make every
  reading through the table fail with that error, as a switch to a table that
  is not loaded does; an edit to an included table reaches every table that
  includes it.
- **Effects** — a code's entry can say what it does to the text box: a
  *newline*, a *page* (the box ends and the next starts) or a *pause*. A
  *newline* or *page* code breaks the line after it in the Text tab, the
  Strings view, its editing pane and dumps, and the Preview lays it out
  ([preview.md](preview.md#code-effects)); a *pause* only says what the code
  is.
- **Falling through** — a switch parameter can let its frame read what its
  table has no entry for in the table beneath, so a table switched to for the
  rest of a string need not repeat the codes it shares.
- **Encodings as tables** — every charset is also offered in the Table list as
  a table of its own, under the loaded tables: that encoding with a NUL of its
  code unit's width (`00`, `0000` in UTF-16) as the end token. They are built
  the first time they are read, are never entries or saved, and a loaded table
  of the same id takes the id's place. A table file that is not UTF-8 is read as `cp932`, else
  `latin-1`, and says which in a notice.
- **Table Editor** (View ▸ Table Editor…) edits one table entry's table over
  a live view of the bytes. Only text is typed; everything else an entry can
  be is a picker, so the grammar need not be known to use it:
  - a header with the **Table** picker (every loaded table, to switch between
    them without the dock), the **Charset** picker (`none` or any registered
    charset; choosing one moves the table onto it keeping its own entries and
    edits, as one undo step), **Includes** (the ids of the tables it starts
    from, in order; a change is one undo step, carried by the project until
    Save As File writes it), **Rename Table…** (a new `@id`; every switch
    parameter, include, block and reading that named the old one follows, as
    one undo step, and the project carries the new id until Save As File
    writes it)
    and a **filter** (Ctrl+F) matching key, text or comment, under which the
    file and, for a converted table, its dialect are named;
  - a **grid** of one row per key — Key, Kind, Text (a code as `[label]`, as
    the dump shows it), Details (what the entry does, in words: `reads u8,
    u16`, `@items ×1, then return`), Weight, Comment — whose Text and Comment
    cells are edited in place, and whose other cells open their control in
    the form on double-click. The entries an include gives the table show
    dimmed, their Details naming the table they come from (`from @script ·
    reads u16`); editing one gives the table its own entry at that key, and
    removing one gives it an empty one (`removes @script's entry`), while
    removing an entry of its own brings back what the include gives. An edit
    that would give the merged table a label twice is refused, and a problem
    with the includes themselves is said on the status line. A click on a
    header sorts by that column (Key by width, then bits); a right-click
    chooses which columns show, kept between runs. Weight, left to itself,
    shows only when the table weights something. The grid and the form under
    it are **split** by a handle, where it is left kept between runs: a taller
    window is more rows, and the form opens at a height that holds the tallest
    entry and scrolls in its own pane after that, so no kind picked moves the
    rows;
  - a **sample** line under the grid, for a key the raw view sent: where in
    the file its bytes were taken from (`sampled from 0C4A10`);
  - an **entry form** under the grid, loaded from the selected row or blank
    for a new one: the key as hex or, for a width that is not whole digits,
    bits, with its width read out; the kind as a picker (Text, End, Code,
    Switch, Return) that shows only what that kind takes; the **effect**
    (none, newline, page, pause), which Details shows after what the entry
    does; the text or, for a code, the label; the weight; a code's operands as a list of spec pickers
    (`u8`…`s16be`, N bytes, N bits); a switch's parameters as a list of rows —
    the table (loaded ones, `raw`, `bits`), how it stops (until the string
    ends, a count, a count read from the data as `u8`…`u32be`, until given
    bytes or bits), whether its matches count towards the parent (`+`) and
    whether bytes its table lacks fall through to the table beneath (`|`) —
    with **then return** after them; the comment; and, behind a **Line**
    disclosure kept between runs, the line the form spells in the native
    grammar, which also works the other way: a line typed or pasted into it
    fills the form. Weight shows for a code or a switch, for an entry that
    is weighted, and for every entry of a table that weights something. A
    problem with the entry — or a
    word on one that is valid but probably not meant, such as a switch whose
    text has no brackets — is said under the form before **Add** (or
    **Apply**, when a row is being edited) puts it in the table. After an
    Add the form moves on to the next key of the same width, kind and
    weight with its text blank, since entries are usually typed in key
    order; **New** clears it. With several rows selected the form waits:
    only **Remove** (Del) and **Shift Keys…** apply, and Remove asks nothing,
    being one undo step;
  - the raw view's **Add to Table…** opens the form on the first selected
    byte as the key, saying where it is, and queues the rest one entry each:
    every Add moves on to the next byte, then to the next key as usual;
  - **Shift Keys…** moves the selected entries' keys by a constant, typed as
    an offset;
  - **Fill…** — templates: `A–Z`, `a–z`, `0–9`, the three together, `あ-ん`,
    `ア-ン`, or a typed string, laid over consecutive keys from a first key,
    the dialog saying how many keys the run covers and how many already have
    entries, which are left alone unless **Overwrite** is ticked (a whole
    standard encoding is a **charset** on the table, not a fill);
  - every change is one undo step and re-decodes every view using the table;
  - **Save** writes a native table back to its file, and only that: a
    converted table or one with no file needs **Save As File…**, which asks
    where;
  - the status line carries what reading the file had to report — a conversion
    from a legacy dialect, an encoding that is not UTF-8 — with each notice's
    fuller detail in its tooltip.
- **Where the edits live** — in the project, as an overlay of the entries
  added, changed and removed over the file, so the table file on disk keeps
  saying what it said for every other tool that reads it; a charset or
  includes chosen in the app in place of the file's are carried the same way.
  **Save As File** writes them out and spends the overlay. A table with no file of its own —
  from a relative search, or from **Add from selection** — is carried whole by
  the project the same way.
- **Reload** — a table file edited outside the app is re-read when its
  timestamp changes, with a prompt if the in-app copy has edits, and
  **File ▸ Refresh Tables** asks for the same of every table file at once —
  what an editor that replaces the file rather than writing over it leaves the
  watcher blind to. The edits stay on top of what was re-read, and win where
  they overlap.

## Reading the bytes

The Format and Reading bars say how the entry on screen is read, and every
change to them applies as it is made, as celPix's toolbar does: the Hex, Text
and Strings tabs read again at once.

- **Whose settings** — on a block, its own configuration: each change is an
  undo step that re-reads the block, its originals, statuses and notes matched
  back by index — a string the new reading cuts at other bits is a new string
  and takes its original from the bytes afresh — and a run of changes to one control (a spin box stepped several times) is
  one step. A compressed block is re-read over the payload it already holds,
  since that is where its translations are; only changing its **compression**
  cannot keep them — what the old scheme decoded is not what the new one
  would — so that one change asks first, and discards them whole. On a file, the file's session: saved with the project, carried by
  a bookmark, and what **New Block** starts from. Anything else that changes a
  block's configuration — an import, **Use as Pointer Table**, an undo — shows
  in the bars at the next refresh.
- **Table** — the table or encoding the text is read through, in either
  mode: the loaded tables with their entry counts, then the encodings, then
  **New Table…**. A file with no table of its own reads as the first loaded
  table, else as ASCII. A table the reading names that is not loaded shows as
  `@id (not loaded)`. **Edit…** beside it opens the picked table in the Table
  Editor, and is disabled on an encoding, which has no table entry to edit.
- **Show as** — **Strings** or **Pointers**, side by side, one of them down.
  On a file, switching turns the source to the other mode's kind and keeps
  every other setting; switching back restores the source and string type the
  file had in that mode, for as long as the session lasts. A block keeps the
  mode it was made with, and every other setting stays editable: on a block
  the toggle picks only what the view shows. On a pointer block **Pointers**
  confines the view to its pointer table and **Strings** to its strings, read
  as text, from the lowest-placed string to the end of the highest — the
  pointers may reach them in any order, share one, or leave bytes between
  them, and those bytes show too. A block comes back on whichever of the two
  it was left on, for as long as the session lasts. A block without pointers
  has **Pointers** disabled.
  The Reading bar shows only the sections and settings the mode uses — in
  Pointers mode the **Strings** section too, since it shapes what Follow
  pointers shows.
- **Pointers** — the bytes are pointers: the source becomes a pointer table (or
  list), the Reading bar adds its **Pointers** section, and beside the mode
  comes **Follow pointers**, remembered per entry. On a file the pointers are
  read every stride from where the view starts; on a block, its own.
  - the **Hex** tab's text column shows each pointer where it points (`→1A3F0`,
    `→?` when it maps outside the data), or resolved, the string there on one
    line; each is tinted as a pointer, and its hover says the value, the
    target and the string;
  - the **Text** tab shows a line per pointer: its address, its value, where it
    points and, resolved, the string there;
  - a nested source's **outer** pointers reach structure rather than text, so
    they are never followed: the Text line says `inner table` or `base` for
    which of its record's two pointers it is, the Hex cell keeps showing where
    it points however **Follow pointers** is set, and the hover says the same.
    Following them would read an inner pointer table's own bytes as characters.
    Its inner pointers are pointers to strings and resolve like any other;
  - a line step in Text is a pointer;
  - a pointer's string is read for at most 256 bytes past its target and shown
    with a trailing `…` when that cut it short, so a pointer into anything but
    text does not read to the end of the file; each target is read once while
    the data, the reading and the table stay the same.
- **Strings** — the bytes are text through the format, cut into strings by the
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
  from the same offset in an ordinary read-only text box, where every string
  ends its line — an end token carries the break, and a string cut by its
  length is broken where the next one starts, so two strings never share a
  line and read as one — with **Show codes**, **Show unknown** and **Wrap**
  switches remembered per machine. Off, Show
  codes hides the bracketed codes — a CODE entry, or an END or SWITCH whose
  text is exactly `[label]`, keeping the line breaks after it — and Show
  unknown hides the `[$XX]` bytes no entry matches; a hidden token is still
  there, as a switch is, so a selection over it still maps to its bytes.
  Selections follow each other between the two tabs, and the open tab is part
  of an entry's session.
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
- **In step** — the view reads from wherever it starts, and cuts its strings
  in step with the ones the block reads: a fixed length runs from the source's
  start, so a view that starts part-way through a string shows the rest of that
  one and whole ones after it, whichever byte it was moved to. Only a range of
  fixed strings has such a grid: a Pascal count is read from the data, and a
  pointer source's strings are each at their own target.
- **Bounds** — the view can be confined to a stretch of the file: a block
  opens on its source, and a string's row in the Files panel confines it to
  that string. Inside them the tabs show those bytes and no more, the
  scrollbar spans them, Home and End are their ends, and no step leaves them;
  the status bar says what is in view. While the open tab shows the whole
  stretch from its start — a string in one row — the step buttons and their
  keys are off, since a step could only hide bytes that fit; the same goes for
  a file smaller than the window. Addresses stay the file's. Any position
  asked for outside them — a typed address, a Search Window or Scan hit, a
  Strings row, an undo reaching its edit — widens the view to the whole file;
  the Find bar never asks for one, searching only the bytes in bounds. A file is never confined, and a block
  returning to the screen is confined to its source again, keeping its
  position if that is inside it.
- **Navigation** — the address format (Hex, a console mapping preset, or Custom
  bank fields), an address box, Home, page and row steps, **−B / +B** byte
  steps, and End. A view narrower than its rows scrolls sideways. The format is remembered per machine and drives every address:
  the address box, **Go to Address**, the Hex panel's address column and fields,
  and the Reading bar's start, stop, bound and pointer addresses. Offsets are
  typed in hex, with a leading `-` to subtract. **Back /
  Forward** (Alt+Left / Alt+Right, or the browser buttons on a mouse) walk a
  trail of what has been on screen: the entries visited, and each string
  opened from the Files panel, which Back comes out of the way it came in.
  The Files panel's selection follows each step, opening a block to the
  string landed on. A project opens with its current entry as the first visit.
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
- **Context menu** — New Block from Selection, New Bookmark, Jump to Pointer
  Target (on a pointer of the current block) and Jump to Pointer (on a string
  one of its pointers reaches), Add to Table… (opens the Table Editor with
  the bytes as a key), Add Skip from Selection (in a block: a skip range over
  the selected bytes, its own undo step), Search for Selection (as bytes),
  Copy Hex, Copy Text.
- **Editing** — the raw view is read-only; editing happens in the Strings
  view or the Hex panel.

## Finding text

All searches run over the current file through its container and
compression, and report offsets in the file's coordinates.

- **Find** — the **Find bar** under the navigation row holds the current
  search: hex bytes, or `"quoted text"` run through the encode engine, so
  multi-character entries, `[codes]` and table switches are all searchable.
  Ctrl+F in a view puts the keyboard there; Enter, F3 and the bar's arrows
  find the next match and Shift+Enter, Shift+F3 the previous, wrapping. The
  Hex panel's find field and **Search for Selection** (which spells the bytes
  as hex) hand the bar their search first, so it always shows the current one.
  The search runs over the bytes the view is confined to — inside a block,
  the block's source; on one string, that string — so a hit never widens the
  view; a file is searched whole.
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
shows the settings below that the mode, source kind and string type use, in
four framed sections that sit side by side while there is room: **Source**
(the kind, start, stop, pointer addresses), **Pointers** (size, stride,
endian, mapping, offset, bank, null, and a nested source's inner size, endian
and null), **Strings** (string type and what it takes, strings per pointer,
realign, skip ranges, lines, Show `[end]`) and **Writing** (bound, write mode,
fill, spare room). A file has no addresses of its
own, so its bar leaves out start, stop, count, pointer addresses, skip
ranges and the Writing section; a section with nothing to show is hidden. With
nothing open the bars are disabled and show the default reading — a file read
as a **Range** of end-token strings — rather than every control at once.

- **Source** — where the strings come from; the Pointers mode picks between
  the three pointer kinds — a file's reading only a pointer table — and the
  Strings mode has one, so shows no picker:
  - **Range** — `start` to `stop` (exclusive), read as consecutive strings;
  - **Pointer table** — `start`, `stop`, pointer `size`, `stride` (size plus
    space), `endian`, `mapping` (listed by name), an `offset` added to each
    value, and a `bank`, shown only for a mapping that reads one; strings are
    read at each target;
  - **Pointer list** — explicit pointer addresses, one per line, with the
    same pointer fields;
  - **Nested tables** — a pointer table (`start`, `stop`, the same pointer
    fields) whose records each hold two pointers: an inner pointer table, and
    the base its pointers count from, `size` bytes further on — so `stride` is
    normally twice `size`. The inner table runs from its own address up to the
    base, `inner size` bytes a pointer in the **Inner** byte order, and each
    inner pointer reaches its value plus the base. The strings one record's
    table reaches are a **group**, laid out over its own text
    ([Writing](#writing-back-to-disk)). An archive of paired entries — each
    map's offset table, then the text the offsets count from — is one block.
- **Null** — a raw pointer value that means "no string", for every pointer
  kind, and **Inner null** for a nested source's inner pointers: such a
  pointer is not read, reaches no string, is not listed as a string's pointer
  and is never rewritten. A nested record either of whose pointers holds the
  null value is skipped. Blank is none.
- **String type** — how a string ends:
  - **End token** — at the first end token of the table set;
  - **Fixed length** — after `length` bytes, or earlier at an end token when
    **Stop at end token** is on. On a range this is the fixed-string block
    of other tools, and a **Count** beside the length says how many strings
    the range holds and, typed into, moves `stop` to hold that many. A string
    that stops at an end token followed by nothing but fill shows neither:
    a short name padded with `FFFF` reads `あき`, a full-width one
    `あたらしいカクザイ`, and editing writes the text, then the end token where
    there is room, then fill to the length. One whose bytes after the end
    token are anything else shows the end token and those bytes after it, fill
    and all, and writes them back as they read;
  - **Length prefix** — a `1–4`-byte prefix, little- or big-endian when wider
    than one byte, counting bytes or token weights (**Counts tokens**);
  - **Next pointer** — at the next pointer's target (pointer sources only;
    the last string ends at an end token or `stop`);
  - **Lines** — after `N` line codes, or earlier at an end token: a message
    the game reads as a fixed number of terminated lines, with no end of its
    own. Edited text must hold exactly `N`.
- **Ends per string** — how many end tokens one string runs through before
  it ends (Cartographer's strings per pointer).
- **Line code** — `[line]`, or the block's own label (`line_label=` in the
  config line). A token that is this code, or whose table text ends in it,
  renders with a line break after it everywhere text is shown, so table text
  needs no `\n` for it; the break is dropped on insert as any line break is.
- **Realign** — after each end token, round the position up to a multiple of
  `M` plus `O`.
- **Skip ranges** — `from → to` pairs: reading a string's bytes reaches `from`
  and continues at `to` (Cartographer's auto-jump), so data sitting inside the
  text is stepped over. A Pascal prefix is among a string's bytes, and a string
  that begins on a skip begins where it lands, so a header before each record
  is stepped over too. They shape the strings, not where a source's addresses
  are: a pointer table is still walked from `start` by `stride`, and a gap in
  one is a matter for the stride or for a second block. Being a string's own
  setting, the Skips picker stays in the Strings section, so it is there in
  either mode of a block. It shows the ranges on one line and opens a popup
  list of `from` / `to` rows in hex, with Add and Remove, that applies as it is
  edited — a run of edits is one undo step — and the Hex tab's **Add Skip from
  Selection** adds one over the selected bytes.
- **Table** — the start table: a loaded table or an encoding, picked in the
  Table list; the table set follows from it.
- **Fixed-line layout** — for fixed-length strings, an optional `line length`
  that splits each string into lines marked with a `[line]` code.
- **Bound** — the exclusive end address strings may not cross on write;
  defaults to `stop`, or to the last string's end for pointer sources, and
  the field's placeholder shows which. A nested source's groups each have
  their own ([Writing](#writing-back-to-disk)), which the bound caps.
- **Write mode** — **Packed**, **Slotted**, or **Automatic**, which says
  which of the two it picks; see [Writing](#writing-back-to-disk).
- **Fill** — what pads unused space on write: a byte, or a pattern of
  several (`FFFF` is a word), repeated from the start of the space it fills —
  a slot's tail, a packed block's tail, a fixed string's padding. A run of
  whole patterns is what reads as padding.
- **Compression** — a block inherits its parent file's container and
  compression and may override the compression — **To Block** in the
  Decompressed View makes such a block, and no bar picks a scheme — in which
  case it is a decompressed region over the compressed slot at its offset,
  with its own **spare room** rule (fill, or keep the bytes that were there).
- **Jump to Source** shows the parent file at the block's own offset in the
  raw view — its compressed slot for a decompressed block, its first pointer
  for a pointer list — read the way the block reads and, where it has one,
  with its compression armed in the Decompressed View.

## Pointers

- **Mappings** — LINEAR, LoROM, HiROM, GB, GBA, **Relative** (the value is
  the distance from the pointer to its target) and **Banked** (bank size, bank
  base address, bank number taken from the block or a field), which the two
  NES layouts `nes_c000` and `nes_8000_2000` are ready-made settings of. Each
  applies after the container's header offset, then the block's `offset`.
- **Pointer table entry** in the Strings view — every string lists the
  pointers that reach it; a target reached by several pointers is one string
  with several pointers, written back to all of them. A null pointer reaches
  none. A nested source's strings list their inner pointers, each written
  back counting from its own group's base.
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
  block — a nested source's outer pointers as well as its inner ones — and
  jumping from a pointer to its target and back is a click: **Jump to Pointer
  Target** from an outer pointer goes to the inner table or base it names.
  Read as pointers, a null pointer points at `null`.

## Strings view

The editing surface, opened on a block.

- **Columns** — `#`, address, pointers, **Original** (read-only: the string's
  text when the block was made, kept by the project), **Translation** (what
  the bytes say now; editable), bytes used / room, status, **Same** (`×N`
  when other strings of the block share the original), notes. The header's
  context menu hides and shows columns, and dragging a header section reorders
  them; Translation stays.
- **Status**, per string: **untouched** (the bytes still say the original),
  **edited** (they say something else), **review** and **done** (set by hand
  — **Edit ▸ Toggle Review / Toggle Done on Selected**, Ctrl+Alt+D for done —
  or by import, and kept whatever the text does), and **overflows box** when
  the block has a text box (see [preview.md](preview.md#text-boxes)). Nothing is ever *too long* or
  *invalid*: the bytes cannot hold such a text, so an edit that would need
  them is refused instead.
- **Editing** — the Translation cell is a multi-line editor, opened on the
  text the bytes hold:
  - typing edits text; `[` opens code completion listing the table set's
    codes with their operand shapes and the comment the table gives each;
    **Return** commits and opens the next row, **Ctrl+Return** commits and
    stays, Esc cancels; a blank cell puts the original back;
  - a commit lays the block out again with the new text in place of the
    string's bytes and every other string's bytes as they are, and splices
    the result into the buffer — the file's, or the payload of the slot a
    compressed block decodes — which then reads unsaved. A packed block moves
    the strings after the edit and rewrites their pointers; a slotted one
    keeps the string in its slot, padded with the fill byte, and the slot is
    the string's own bytes and the padding after them, so a string shortened
    once can grow back;
  - leaving the cell — for another row, or another widget — commits the same
    way Return does; a cell left as the bytes have it commits nothing, and
    the window going inactive leaves the draft where it is;
  - a commit is **refused** — the editor stays open on its row with the draft
    and the reason under it — when the text does not encode, does not fit its
    room, would not read back as typed, or would change how the bytes after
    it are cut into strings;
  - **Shift+Return** writes the block's newline code — the code carrying the
    *newline* effect, by the box, its table entry or as the block's line
    code, else `[line]` — never a line break, which the script
    grammar drops;
  - **Insert code** buttons for the codes this block's strings use most,
    wrapping onto more rows when the view is narrow, each with the table's
    comment as its tooltip;
  - the byte readout updates as you type, from a live encode, against the
    room the string has; the Preview follows the draft and lists what the
    font cannot draw;
  - **Revert** puts the original back; the Preview window's **Wrap
    Translation** inserts line codes to fit the block's box; see
    [preview.md](preview.md#wrapping);
  - **Apply to Identical Originals**, in Block or in Project, puts the
    selected string's text into every string whose original is the same. One
    string with no room for it does not hold the others back: the block takes
    them together where they all fit and one at a time where they do not, and
    what is refused is listed with why.
- **Editing pane** — under the grid, on the selected string, read whole:
  the original with its line breaks, codes dimmed and, with **Show codes**
  off, left out as the Text tab leaves them; beside it the same editor the
  cell opens, the byte readout above it and the notes under it. **Wrap**
  wraps both boxes. Return, Ctrl+Return, Shift+Return, `[` and Esc do what
  they do in the cell; a commit from the pane moves the selection on and
  keeps typing in the pane. Leaving the editor lands its draft as leaving a
  cell does; a refused draft stays, with the reason in the readout, and the
  selection stays on its row. A code button types into the cell being
  edited, else into the pane. The split between grid and pane is remembered
  per machine.
- **Project Strings** — **Search ▸ Project Strings…** (Ctrl+Shift+G) lists
  every string of every block, read as needed: block, index, original,
  translation, status and notes, under a word filter and a status filter.
  Double-click or Enter opens the block on that string. The list follows
  edits while it is open; **Refresh** reads every block again.
- **Glossary** — **Edit ▸ Glossary…** (Ctrl+Shift+L) opens the project's
  terms: a term, its translation and notes, edited in place, added and
  removed, filtered by words; every change is an undo step and the project
  reads unsaved. Above them, **In this string** lists the terms the selected
  string's original holds, case and form folded, longest first;
  **Insert Translation** (or a double-click) types the term's translation
  into the cell being edited, else into the pane.
- **Stepping** — **Edit ▸ Next / Previous Untranslated** (F4 / Shift+F4) and
  **Next / Previous Flagged** (F6 / Shift+F6: review or overflows box) move
  among the rows the filter shows, wrapping round.
- **Progress** — the Block bar says how many strings are translated, and how
  many done, of the block and of the project.
- **Filter** — words in any order over original, translation and notes; a
  status box narrows to one status.
- **Selection sync** — selecting a string highlights its bytes in the raw
  view and the Hex panel; selecting bytes there selects the string. A hex
  overtype inside a string reads as an edit of it.
- **Bulk edit** — Find and Replace across the block or the project, with
  code-aware matching (`[line]` matches only the code). A block's
  replacements land as one edit; one that will not fit is tried string by
  string and the refusals listed.
- **Undo** — a run of commits on one cell is one undo step.

## Writing back to disk

- **What a write does** — a string edit is already in the buffer, so a write
  lays nothing out: it compresses each edited slot back into the file, runs
  the container in reverse over the file as it stands, and writes the result
  back. The buffer is written whole, edits from every surface in it.
- **Write mode**, per block, governs how an edit lays the block out:
  - **Packed** (default with pointers) — strings are laid end to end from the
    block's first string address, each pointer is rewritten to its string's
    new position, and leftover space up to the bound gets the fill. A string
    that starts inside the string before it and ends with it — the last page
    of a message, with pointers of its own — is written once while its bytes
    still end that string's, its pointers reaching into it; edited apart, each
    has bytes of its own from then on. A nested source packs each group apart,
    from its first string to its own bound: the end of its last string and
    the run of whole fill patterns after it, never as far as the next inner
    table or text the outer table points at, nor past the block's bound. Only
    the groups an edit touches are laid out and read again, and only their
    inner pointers are rewritten;
  - **Slotted** (default without pointers, and always with skip ranges) —
    every string stays at its address and may use up to its slot, padded with
    the fill. A slot is the bytes the string holds itself and the run of whole
    fill patterns after them — the padding a shorter string left, which the
    next edit takes back — stopping at the next string, at the bound (a
    nested source's group bound), or at the end of the bytes, whichever comes
    first, and a fixed length caps it. Bytes between two strings that are not
    that padding belong to no slot and are left standing. A run of the fill
    between two strings is read as padding rather than text only when the
    block's table maps nothing beginning with the fill, so the fill should be
    one no string begins with: one the table maps is read like any other
    bytes, and a shortened string's padding is then text in front of the next
    string.
- **In place only** — a string that does not fit is refused at the edit, with
  the bytes over. Nothing is relocated; making room is the user's job.
- **Encoding is verified** — every encoded string is decoded again and must
  give back the same tokens, and the block must read again as the same
  strings; a mismatch refuses the edit.
- **Bit-level tables** — an encoding that stops short of a byte is padded with
  zero bits to the byte, as the games and Atlas pad it: the next string, or
  the pointer to it, begins on a byte.
- **File ▸ Write (Ctrl+W)** writes the current entry's file; **Write All
  (Ctrl+Shift+W)** writes every file and slot with edits; the Files panel
  writes one entry. An entry that cannot be written says why: a bookmark has
  no bytes of its own, a table file is written with **Save As File…**, a glyph
  sheet is never written to, and a view-only entry names the stage that has no
  way back.
- **Blocks over one compressed region share its payload** — an edit in one is
  in the bytes the others read, the slot is compressed once, and a write of
  any of them writes them all.
- **Other open entries on the same file refresh afterwards** — a block over a
  compressed region by decompressing again, since its bytes are a reading of
  the region rather than a window on it. One with unsaved edits keeps them.
- **A write is one undo step** — per file, and one step for all of a Write
  All. Undoing it puts the bytes it replaced back in the file and leaves the
  buffer as it was, unsaved again; redoing writes the result once more. The
  file is read at that moment and only touched while it still holds what the
  step is moving away from: one changed since by another program is left
  alone, and the step says so.
- Opening, creating or saving a project with unsaved edits offers **Write All
  / Continue Without / Cancel**, as does changing a file's container, which
  reads the file from disk again.

## Dump, export and import

- **Dump…** (block or file) writes a native script: one file per block or one
  for all, with originals, translations or both (see
  [script-format.md](script-format.md#native-script)).
- **Import script** reads a native script back: strings are matched by block
  and index and the text goes into the bytes as an edit. Unknown blocks are
  created when the script carries their configuration.
- **Translator files** — **Export ▸ TSV / CSV** and **Export ▸ PO** write one
  row or entry per string; **Import** reads them back by id. Rows whose
  original no longer matches are reported and skipped unless forced.
- An import is one undo step. A block's texts land as one edit; a block whose
  texts will not all fit is tried string by string, and what is refused is
  listed and left as it was.
- **Cartographer** — **Import** reads a command file into blocks (one per
  `#BLOCK`) and its tables through the abcde dialect, then extracts. **Export**
  writes a command file for a block whose settings Cartographer can express,
  and says what it cannot.
- **Atlas** — **Export** writes an Atlas script plus abcde-dialect tables for
  a block; **Import** reads the subset of Atlas commands that map to block
  settings and reports the rest.

## Compression

- **Preview** — the raw view always shows the bytes of the chain as far as
  the block's compression. A scheme armed on a file — by **Jump to Source**
  from a compressed block, or a bookmark made under one — decompresses from
  the current offset into the floating **Decompressed View**, decoded through
  the reading's table, until another entry opens; it hides when nothing
  decodes.
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

A text box and the app's preview font turn a string into a picture of how it
lays out in the game. It is described in [preview.md](preview.md).

## Hex panel

- **Panels ▸ Hex** — a dump (address · hex · ASCII) of the decoded
  buffer from the current offset, following the raw view's selection and
  tinting it in both columns.
- **Overtype** — typing a hex digit over a byte in the dump changes that
  nibble in place, one undo step per digit, the caret moving on to the next
  nibble; the bytes line below writes a run of hex bytes at an offset. Both make
  the file entry unsaved, and a block over the bytes reads them again. Text is
  decoded, not editable here.
- **Go to**, **Find** (the same field as the Find bar, which takes over what
  is searched from here) with next and previous, and **Follow selection**,
  which is remembered per machine.
- The address column follows the navigation bar's address format.
- Refreshes only while visible.

## Projects

- A `.mapchar` project stores **references and settings, never bytes**:
  every entry with its chain, block configuration, a file's reading; the
  folders and which rows each holds; per
  string its **original**, status and notes; table edits made in-app; text
  boxes; the glossary; the view position per entry.
  Translations are not in it: they are the ROM's bytes.
- Not saved: zoom, theme, the preview font, window layout, undo history.
- **New / Open / Open Recent / Save / Save As** as in celPix; paths are stored
  relative to the project file; older versions are upgraded on load, which the
  status line says, and newer ones open with what this build understands. A
  project from before originals were kept still holds translations: opening
  it puts them into the bytes, and the file reads unsaved until written. One
  that cannot be placed — the block's table is not loaded, or the bytes would
  not read back as it — stays in the project, which saves it again, and is
  said in the load's notices rather than in a status message the load
  replaces.
  **Open Recent** lists projects by name, newest first, drops rows whose file
  has gone, and offers **Clear List**.
- **Missing files** — a project that references files that are not there offers
  to locate them as it opens, and **File ▸ Locate Missing Files…** walks them
  at any time. One answer corrects every entry that named that file, and a row
  still named after the file takes the new name.
- **Saving the project resolves unsaved edits first** — a project holds
  references, not bytes, so it asks to **Write All**, continue without writing,
  or cancel. Edits left unwritten are lost with the session.
- **Unsaved marker** — the title bar shows the project unsaved when its
  serialized form differs from disk — a table edit included, since that is
  project state. A session that has never been saved as a project has nothing
  to differ from and never prompts.
- **Autosave** — the project is what remembers the originals once the ROM
  has been written, so a copy of it is saved every two minutes when it has
  changed, and at once after every write to disk: `name.mapchar.autosave`
  beside the project file, or under the application's data folder for a
  session with no project file. Saving the project removes the copy, as does
  quitting. A copy newer than its project is offered when the project is next
  opened, and a session's copy is offered at the next start. A copy that was
  recovered stays, written again from what it restored: until the project is
  saved it is the only place that work exists. It is not a project of its own,
  so Open Recent and the folder the pickers start in name the project it
  stands for, and nothing at all for a recovered session that has no project
  file.

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

- **One history** for the session: entry open, close, paste, rename, reorder,
  new folders and moves between them;
  block, container and table edits; view moves; string edits, status changes
  and notes; hex overtypes; box edits; writes to disk.
- **Ctrl+Z / Ctrl+Shift+Z** undo the latest action from any surface. Undoing
  a change made elsewhere switches back to that entry **and** the view it was
  made in — the Strings tab on its row, the Hex tab at its offset.
- **Unsaved state follows undo** — undoing back to the saved state reads
  clean again, and redoing marks it unsaved once more. A write is a step of
  its own, so undoing it and then the edits behind it ends at the bytes on
  disk, clean.
- **A run of commits on one string is one step**, and a run that ends back
  with the bytes it began with is no step at all. Moving to another row, or to another entry, ends
  the run. Consecutive view moves in one entry merge the same way.
- Opening or starting a project clears the history and the visit trail.

## Keyboard reference

**Help ▸ Shortcuts… (F1)** shows the live list in two balanced columns, one
section per menu, built from the menu bar plus the keys and mouse gestures no
menu row can carry (the Hex and Text views, the Strings view, the Files panel, the
Find bar, the Table Editor, the Hex panel, the tool windows). **Help ▸ Legend…** explains every colour and mark the Hex and
Text views, the Hex panel and the Strings view draw, each beside a swatch.
**Help ▸ About** gives the version, author, homepage and licenses.

| Area | Keys |
|---|---|
| File | Ctrl+N / Ctrl+O / Ctrl+S / Ctrl+Shift+S projects · Ctrl+Shift+O Open ROM · Ctrl+T Open Table · Ctrl+Shift+B New Block · Ctrl+B New Bookmark · Ctrl+E Edit File Container · Ctrl+W Write · Ctrl+Shift+W Write All · Ctrl+D Dump · Shift+F5 Refresh Tables · F5 Refresh Plugins · Ctrl+Q Quit |
| Edit | Ctrl+Z / Ctrl+Shift+Z · Ctrl+X / C / V · Ctrl+H Find and Replace · Ctrl+Shift+L Glossary · Ctrl+Alt+D toggle done · F4 / Shift+F4 next / previous untranslated · F6 / Shift+F6 next / previous flagged |
| View | Ctrl+1 Hex · Ctrl+2 Text · Ctrl+3 Strings · Ctrl+Shift+T Table Editor · Ctrl+P Preview |
| Navigate | Alt+Left/Right history (also mouse 4/5) · Home/End · Up/Down row · Left/Right or - / + byte · PgUp/PgDn page · Ctrl+G go to address |
| Search | Ctrl+Shift+F Search Window · Ctrl+Shift+R scan · Ctrl+F the Find bar · F3 / Shift+F3 next / previous · Ctrl+Shift+P find pointers · Ctrl+Shift+G Project Strings |
| Find bar | Enter next · Shift+Enter previous · Esc closes Find and Replace |
| Strings view | F2, double-click or typing edit the cell · Enter commit and move on · Ctrl+Enter commit and stay · Shift+Enter newline code · [ complete a code · Esc cancel · the same keys in the pane under the grid |
| Files panel | Up/Down or double-click open the row · Shift/Ctrl+click extend · Alt+Up/Down or drag reorder · Ctrl+X/C/V/D entries · Del remove · Ctrl+F filter · F2 rename · right-click menu |
| Hex panel | 0-9 / A-F overtype · Enter go to, find or overtype · Shift+Enter find previous |
| Tool windows | Esc close · Enter run the Search window's query · double-click a result to jump · Ctrl+Z / Ctrl+Shift+Z in one that edits the project (Table Editor, Find and Replace, Glossary) |
| Table Editor | Enter put the entry in the table · Del remove the selected entries · Ctrl+F filter · F2 or double-click edit a Text or Comment cell, double-click opens any other in the form · click a header to sort, right-click to choose columns |
