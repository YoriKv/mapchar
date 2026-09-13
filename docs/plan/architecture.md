# mapchar architecture

How the application is structured: the layers, the data model, the engines
that turn bytes into strings and back, the plugin system, the project layer,
the Qt UI, and tests. What the app does for a user lives in
[features.md](features.md). Paths are relative to `src/mapchar/`.

## Contents

1. [Layers and dependency rules](#1-layers-and-dependency-rules)
2. [The data model](#2-the-data-model)
3. [Engines](#3-engines)
4. [The pipeline](#4-the-pipeline)
5. [The plugin system](#5-the-plugin-system)
6. [The project layer](#6-the-project-layer)
7. [The UI layer](#7-the-ui-layer)
8. [Build, tooling and tests](#8-build-tooling-and-tests)

---

## 1. Layers and dependency rules

The layering is celPix's, with the same rules:

```
app.py ─────────────► ui/ ───────────────┐          Qt lives only here
                        │                │
                        ▼                ▼
                    project/ ──► pipeline/ ──► plugins/ ──► core/     Qt-free
                                     │                        ▲
                                     └──────► engines/ ───────┘
```

| Package     | Role |
|-------------|------|
| `core/`     | The data model: tables and tokens, strings and blocks, pointers and mappings, fonts and boxes, the pipeline context, notices, errors, capabilities. |
| `engines/`  | Pure algorithms over the model: decode, encode, relative search, text scan, pointer discovery, layout. No I/O. |
| `pipeline/` | Runs the byte stages in both directions, extracts blocks into strings, lays strings out for writing, and drives the exchange formats. |
| `plugins/`  | The plugin API, registry, discovery, trust, detection, and every built-in plugin: containers, compressions, charsets, mappings. |
| `project/`  | The open-entries model (`workspace.py`), the `.mapchar` file (`projectfile.py`), reading a table file from disk (`tables.py`), and the table-file and script readers and writers (`formats/`). |
| `ui/`       | The PySide6 application: `MainWindow`, the raw and strings views, docks, tool windows, dialogs, undo commands, theme. |
| `app.py`    | Entry point: `QApplication`, theme, plugin folders, trust store, registry, `MainWindow`. |
| `resources/`| Package data: the plugin examples seeded into the user's folder, the icon font, the app icon. |

Rules:

- **Only `ui/` and `app.py` import Qt.** Everything else is headless and
  tested without a `QApplication`.
- **`core/` is the bottom layer.** `engines/` depends only on `core/`.
- **Everything runs on the GUI thread.** Long operations (scan, pointer
  discovery, compression scan) pump the event loop through a progress
  callback and can be cancelled.
- **Text formats live in `project/formats/`**, not in `core/`: table files
  and scripts are file formats, and the model does not know how it was
  spelled.

## 2. The data model

### 2.1 Entries and documents

- **`project.workspace.Entry`** — the persistent identity of an open thing:
  `EntryKind` (file, block, bookmark, table, font); where its bytes are
  (`path`, `extra_paths`, block `offset`/`length`); its chain
  (`container_id`, `compression_id`); its `BlockConfig`; its
  `EntrySession` (start table, view position); kind-specific state (table
  edits, font map, box); the `notices` its file's last read produced; and
  session-only state (the lazily loaded `doc`, revision tokens).
- **`core.document.Document`** — the interpreted, mutable model the UI binds
  to: the decompressed `data` buffer, the `TableSet`, and for a block the
  `strings`. Created on first show; dropped and rebuilt when the file
  changes on disk through a write.

Entries are compared by identity. Blocks, bindings and undo commands hold
`Entry` objects, never list positions.

### 2.2 Tables and tokens

`core/table.py`:

- **`Table`** — `id` (`[\w.-]+`, so kana and kanji name tables too),
  `entries` keyed by bit string, `labels` keyed by label, `aliases` (extra
  text the encoder accepts for an entry's bits), `charset`, and derived
  lookups kept as entries are added: `max_bits` and entries bucketed by key
  length, which `match` scans longest length first. The encoder builds its own
  text index per search (`engines/encode.py`), not held on the table.
- **`Entry`** — frozen: `bits`, `kind` (text, end, code, switch, return),
  `text` (text or label, composed to NFC on construction, so every dialect and
  every charset agree on one form), `weight`, `operands: tuple[OperandSpec]`,
  `params: tuple[SwitchParam]`.
- **`SwitchParam`** — `table_id: str` (a table's id, or `raw`, `bits` or
  `return`), `stop: Stop` (a weighted `count`, `fallback` bits, or neither —
  which `Stop.any` reports), `shared: bool`.
- **`TableSet`** — the start table plus the closure of tables its switches
  reach, resolved by id from the loaded files. It is what decode and encode
  run over.
- **`Token`** — one decoded unit: `entry` (or `None` for unmatched data),
  `bits`, `bit_start`, `operands: tuple[int]`, `text` (the rendered form,
  codes in brackets). A token list renders to text by concatenation and
  parses back from text by the script grammar; both directions are in
  `core/tokens.py` and are inverse of each other.

Bit addressing: a table key, a token span and everything decode and encode
carry are in bits, as abcde does, so odd-width entries and bit-packed text need
no special case. The pipeline converts byte offsets at the edges. The engines
that hunt for candidates rather than decode them — `scan` and `relsearch` — work
in bytes, which is the unit their results are reported and selected in.

### 2.3 Blocks and strings

`core/block.py`:

- **`BlockConfig`** — frozen: `source` (`RangeSource`, `PointerTableSource`,
  `PointerListSource`, `FixedSource`), `string_type` (`EndToken`,
  `FixedLength(length, stop_at_end)`, `Pascal(width, counts_tokens)`,
  `NextPointer`), `strings_per_pointer`, `realign`, `skips`, `line_length`,
  `start_table_id`, `bound`, `write_mode` (`PACKED`, `SLOTTED`), `fill`.
- **`StringRecord`** — one string: `index`, `start`, `end` (byte offsets in
  the decompressed buffer), `pointers: tuple[PointerRef]`, `original:
  tuple[Token]`, `translation: str | None`, `status`, `notes`. Statuses that
  derive from the data (`too_long`, `invalid`, `overflows_box`) are computed
  on demand, never stored.
- **`PointerRef`** — `address`, `size`, `endian`, `mapping_id`, `offset`, and
  the `value` read from disk.
- **`source_start(source)`** — where a source begins, or `None` when it does
  not say (an empty pointer list, no source): the one answer the Files panel's
  sort, the block bar and a restored view position all read.
- **`Extraction`** — the result of running a block: the string records plus
  notices (end of data reached, operand cut short, pointer out of range).

### 2.4 Pointers and mappings

`core/mapping.py`: a **`Mapping`** converts `offset ↔ value` given a header
size and a bank number. Built-ins: `linear`, `lorom`, `hirom`, `gb`, `gba`,
`banked(bank_size, bank_base)`, `relative` (value = target − pointer
address). Each implements both directions and declares which pointer sizes
it supports. Mappings are plugins ([5](#5-the-plugin-system)).

### 2.5 Fonts and boxes

`core/font.py` holds `Font` (sheet geometry, glyph map, widths), `TextBox` and
`CodeEffect` as described in [preview.md](preview.md); `engines/layout.py` is
what lays a string out in one.
They are frozen values whose mutators return new instances.

### 2.6 Other core modules

| Module | Holds |
|---|---|
| `context.py` | `PipelineContext`, the `SourceSpan` a joined read publishes, and the `KEY_*` hint names (source files and offset, header size, suggested mapping and table, consumed size, complete, partial decode) |
| `notices.py` | Non-fatal `Notice`s — message, level, offset, detail, source — carried on the context and on extractions; `notice_lines()` renders one as its message with the detail indented under it |
| `errors.py` | `Stage`, `PipelineError` (stage, action, plugin, pathway), `TableError`, `EncodeError` |
| `bits.py` | `Bits` windows over a byte buffer, plus the bit/byte/hex conversions, key spelling, alignment and bit reversal every layer shares |
| `numbers.py` | `parse_num` and `format_num`: the `$hex` spelling tables, scripts and command files share |
| `text.py` | `split_lines` and the backslash `escape`/`unescape` the file formats share |
| `capabilities.py` | `EntryKind → frozenset[Capability]` (raw view, strings view, write, dump, pointers, preview, …) and `supports()` |
| `address.py` | Offset ↔ `bank:addr` display layouts for the navigation bar |

## 3. Engines

Every engine is a pure function over `core` values, with a cancel/progress
callback where it can run long.

### 3.1 Decode

`engines/decode.py` `decode(data, table_set, start_bit, limit_bit, rules) ->
DecodeResult` is the stack machine of
[`../abcde/bin2text.md`](../abcde/bin2text.md#replication-notes), built to its
replication notes rather than to abcde's behaviour:

- **State** — the current bit, a stack of frames `(table, counter, stop,
  fallback_bits, shared)`, the tokens so far, `end_tokens_seen`.
- **Step** — in the top frame: check the frame's fallback bits first; else
  longest-prefix match in the frame's table (the trie); else emit one
  unmatched byte (or the remaining bits when fewer than 8 remain before the
  limit).
- **Count** — subtract the token's weight from the top frame's counter and,
  while the frame is `shared`, from the one beneath. A counter at or below
  zero pops the frame.
- **Switch** — push one frame per parameter, innermost last, so the first
  parameter runs first.
- **Return** — pop the innermost frame whose table holds the entry; at the
  root, end the string.
- **End** — an end token ends the string in end-terminated rules, after
  which realignment applies. `strings_per_pointer` runs the machine that
  many times, restarting in the start table.
- **Limits** — the tighter of the string rule's limit and the block's
  bound. Reaching the end of data ends the string with a notice, never an
  error.
- **Skips** — a bit window that contains a skip start is spliced from the
  skip end onwards, before matching.

The engine is deterministic given the table set, so a decoded string's
tokens are the canonical form of those bytes.

### 3.2 Encode

`engines/encode.py`
`encode(text, table_set, *, end_terminated, ends, verify) -> EncodeResult`
searches for the cheapest bit string that **decodes back to the same tokens**:

- The input is text parsed into a token pattern: literal text runs split into
  **atoms** — one code, or one character of NFD-decomposed text — and codes
  that must map to entries by label (with operands). Decomposing is what makes
  a composed `が` meet both a table entry spelling it whole and a pair of
  entries spelling the kana and the dakuten separately, and entry text is
  split the same way. An alias is one more way to reach an entry's bits.
- **State** — `(position in text, frame stack, forbidden prefixes)`. Frames
  are keyed by table identity, never file name.
- **Search** — Dijkstra with an admissible heuristic (the table set's
  minimum bits per character); a state closes when popped, so the result is
  optimal.
- **Longest-prefix safety** — after emitting an entry that is a proper prefix
  of a longer entry in the same table, the bits that would complete the
  longer entry are forbidden as the next emission. This is the constraint
  abcde omits.
- **Fallback bits** are emitted whenever a fallback frame closes, including
  at the end of the string.
- **End tokens** are ordinary alternatives; whether one is required at the
  end is the string rule's business, not the search's. In end-terminated
  rules one may only come last, except that a block reading N strings per
  pointer allows N−1 earlier ones, each restarting the frame stack as the
  decoder does, and then requires exactly N.
- **Verification** — the result is decoded with [3.1](#31-decode) and must
  give back the same tokens; a mismatch is an `EncodeError`, which the
  Strings view shows as *invalid*.
- **Failure** reports the farthest position reached and the text around it.

Encoding one string is fast enough to run on every keystroke for the byte
readout; the Strings view debounces it anyway.

Each engine keeps its own frame representation: the decoder mutates a stack
of records, while the encoder's frames are immutable tuples because a stack
is part of the search state it hashes and branches from. What both do the
same way, leaving a table by popping its innermost frame and everything
above, is `innermost_index` in `engines/decode.py`.

### 3.3 Relative search

`engines/relsearch.py` turns a query into a sequence of relative
differences per alphabet run (upper and lower case letters, digits, hiragana
and katakana in gojūon order, and the case gap as an unknown constant) and
scans the buffer for byte or 16-bit-word runs that
satisfy every difference, in both endiannesses, with `?` as a free
position. A hit carries the inferred base of every run, which the table
builder turns into entries.

### 3.4 Text scan

`engines/scan.py` slides a window over the buffer, decodes each window
through the table set, and scores it: the fraction of bits consumed by text
entries, plus a capped bonus for dictionary hits on Latin tables (a small
built-in word list), minus a penalty that grows with the square of the share of
unmatched data. Regions above a
threshold merge; each region reports its most frequent candidate terminator
(the byte most often followed by a fresh text run) and its most frequent
string-initial byte.

### 3.5 Pointer discovery

`engines/pointers.py` takes string start offsets, a bank number and the
candidate space (mappings × sizes × endianness × an offset range) and
produces, for each candidate, the addresses where the encoded values occur and
the value found at each. Candidates are ranked by how many distinct strings
they explain, then by how regular the stride between their addresses is. Each
carries both answers a result can be taken as: `source()` is the inferred
`PointerTableSource`, and `refs()` the `PointerRef`s per string that **Attach**
puts on the strings instead. A `progress` hook is called per combination and
stops the walk when it returns `False`, so a stopped search still ranks what it
had.

### 3.6 Layout

`engines/layout.py` renders tokens through a `Font` into a `TextBox` as a
list of glyph placements, records overflow, lists the text the font cannot
spell, and implements Wrap ([preview.md](preview.md#wrapping)). It draws
nothing; the UI paints the placements.

### 3.7 Code-aware find and replace

`engines/scriptfind.py` splits script text into the pieces the grammar makes
— a `[...]` code or an escape is one piece, everything else one character —
and matches a needle as whole pieces. The needle is composed to NFC, and a
case-insensitive match folds each piece on its own, never the whole string,
because folding changes lengths and the spans are the original text's. `[line]` in a needle matches the code
and nothing inside it, and a needle of letters never matches part of a code.
Find and Replace over translations runs through it.

## 4. The pipeline

### 4.1 Stages

```
load:  file(s) ─► CONTAINER.read ─► COMPRESSION.decompress ─► EXTRACT
save:  file(s) ◄─ CONTAINER.write ◄─ COMPRESSION.compress   ◄─ LAYOUT
```

- One plugin per byte stage, covering both directions. `FileRef`,
  `PathwayConfig`, `PipelineContext`, notices and the failure rules are
  celPix's. **Required** calls hard-stop with a `PipelineError` naming the
  stage, the direction, the plugin and the pathway — including the host's own
  source read, so a missing file is reported like any other failure of the
  container stage. **Optional** calls (`_probe`) degrade to the documented
  default plus a warning notice, because absence is already defined for every
  hook that has one. A **missing plugin** resolves to a pass-through that
  leaves the entry view-only and says so in a notice on the context.
- `load` takes a context, so a nested run inherits its parent's hints: a
  block's own decode publishes its own consumed size, while the header size
  and suggested mapping its parent's container found still apply to it.
- **Extract** (`pipeline/extract.py`) runs a `BlockConfig` over the
  decompressed buffer: it resolves the source into `(start, pointers)` pairs
  (reading and mapping pointer values, sorting numerically, merging
  duplicate targets into one string with several pointers), applies the
  string rule, and calls the decode engine per string. The raw view uses the
  same function with a `RangeSource` from the view offset and no bound.
- **Layout** (`pipeline/insert.py`) turns a block's strings into a byte
  splice: encode each translation (or reuse the original bits when
  untouched), lay the results out in *packed* or *slotted* mode, compute the
  new pointer values through the mappings, and refuse the whole block when
  any string crosses its bound, reporting each offender. Its output is a
  list of `(offset, bytes)` splices over the decompressed buffer plus the
  pointer splices.
- **Slots** (`compress_for_slot`) are the write minus the store, so the checks
  that make one safe hold however the bytes are delivered — through a container
  to a file, or spliced into a parent's buffer by a block. A *bounded* slot
  refuses a longer result; an *unbounded* one (no recorded length, which is not
  the same as a length of zero) is still bounded by the end of what holds it. A
  short result in a bounded slot is padded per the pathway's `SlotFill`: `fill`
  writes the block's fill byte over the slack, `keep` writes short and leaves
  the previous stream's tail standing.
- **Save** (`save`) then compresses, hands the container a `WriteTarget` over
  the destination's bytes **read at that moment** rather than remembered from
  the load, splits the result across joined files at the boundaries they have
  now, and rewrites only the files that changed.
- **Inspection** (`pipeline/inspection.py`) runs one container's read alone, on
  a context of its own, and returns a `ContainerReport` of what it published and
  what it had to assume — reported, never raised, since it is reached precisely
  when an entry did not come out as expected.
- **Scanning** (`find_next_structure`) walks forward for the next complete
  structure a scheme can read, with a progress/cancel callback; `decompress_at`
  is one probe of it, and asks for a partial decode when it is previewing.

### 4.2 Exchange

`pipeline/exchange/` holds the Cartographer and Atlas importers and exporters
and the native-script importer. They build or consume `BlockConfig` and
`StringRecord` values and never touch the UI; the mapping tables in
[script-format.md](script-format.md) are their specification. The text formats
themselves — the script and translator grammars — are `project/formats/`'s
(§6.2).

## 5. The plugin system

celPix's system, with these stages:

| Stage        | Required                        | Optional save half        | Other optional                        |
|--------------|---------------------------------|---------------------------|---------------------------------------|
| Container    | `read(ReadSource, ctx)`         | `write(data, WriteTarget, ctx)` | `describe`, `default_mapping`, `header_size` |
| Compression  | `decompress(data, ctx)`         | `compress`                | `bind_tree(rom)`                      |
| Charset      | `entries() -> Iterable[(bits, text)]` | —                   | —                                     |
| Mapping      | `to_offset(value, header, bank)`, `to_value(offset, header, bank)` | — | `sizes`, `needs_bank`      |

- **Optional is optional.** Every hook in the last column is reached by
  `getattr` and probed: one that is absent and one that raises mean the same
  thing, the documented default, so a display-only hook can never cost a load
  ([4.1](#41-stages)). A probe on the load path (`pipeline._probe`) also records
  a warning notice naming the plugin; `describe`, which only Container Info
  reads, loses its rows and says so by their absence.
  `plugins/base.py` declares them — `ContainerExtras` for the container's
  optional half, comments on `Compression` for the scheme's — and
  `PartialDecompression` is the base class a scheme whose decoder finds its own
  end inherits both of its methods from, publishing the consumed size and the
  complete flag the pipeline needs from one `_decode`.
- **One tier: plugins.** A **preset** is a TOML file naming a built-in engine
  and its parameters, adapted into an ordinary plugin as it loads, so the
  registry holds one kind of thing and a project stores one kind of id.
  `mappings/` presets take the `banked` engine (`bank_size`, `bank_base`) and
  `compression/` presets take `huffman`, `lzss` or `bitpack` — which is why
  most consoles are a few numbers rather than code. A preset may restate the
  `stage` its folder implies but not contradict it.
- **Registry**: plugins by stage and id, duplicates refused, `resolve_stage`
  degrading a missing id to a `PassThrough`, and a lookup that falls back to
  `aliases.current_id`, so a retired id keeps resolving. `plugins/aliases.py`
  holds the flat, append-only `RENAMED` table — a rename is the new id plus a
  row — and a project's container, compression and mapping ids are walked
  through it as it loads.
- **Discovery** scans the typed folders of each root in turn —
  `MAPCHAR_PLUGIN_PATH`, the user folder, then the open project's `plugins/` —
  labelling everything out of a root "Your plugins" or "Project plugins".
  `_`-prefixed files and unknown folders are ignored, a loose plugin file in a
  root is reported, and every failure becomes a `PluginLoadIssue` rather than
  an exception: a bad preset or a module that raises on import cannot stop
  startup. `ScopedRegistry` is what a module's `register(registry)` receives,
  checking the folder's stage and the required methods and recording an issue
  instead of raising.
- **Trust** gates `.py` files only, by SHA-256 of the exact bytes executed,
  with approved digests in `<AppData>/trusted-plugins.json`. It is
  **default-deny**: with no store and no confirm callback, nothing runs. A
  path approved this run reloads without a prompt when its code changes, and a
  declined plugin's issue is marked `declined` so a refusal stays out of the
  failure dialog.
- **Detection** reads static `PluginInfo` only, so no plugin code runs before
  the file is open: the size rules only reject, magic decides alone when
  declared (any one probe matching), otherwise an extension match counts, and
  nothing claiming the file leaves the flat-file container. Ties go to
  registration order, so a built-in wins.
- **Refresh** (F5) builds a fresh registry and re-reads the current entry, and
  so does opening or closing a project, before the workspace is replaced.
  Documents holding unsaved edits are kept rather than re-read from disk.
- **Examples**: `resources/data/plugin-examples/` is copied into the user
  folder at startup — a README and `_`-prefixed examples per folder, rewritten
  when a shipped one changes and removed when one is retired, never touching a
  user's own files. Code examples ship as `.py.txt` because frozen builds
  exclude `.py` data.
- Folder → stage: `containers/` (`.py`), `compression/` (`.py`, TOML presets),
  `charsets/` (`.py` and `.tbl` files, which register as a charset named
  after the file), `mappings/` (`.py`, TOML presets).

## 6. The project layer

### 6.1 Workspace

`project/workspace.py` is celPix's workspace: `entries`, one `current`,
callback lists (`on_added`, `on_removed`, `on_reset`, `on_current_changed`,
`on_dirty_changed`), deduplication by normalised path, cascade close from a
file to its blocks and bookmarks, revision-token dirty tracking per entry,
and `invalidate_extractions` when a table changes. It answers every question
about what is open — `find_file` / `find_table` by path, `entry_by_id` for a
tree row, `entry_for_table`, `dirty_entries`, `files` / `fonts` /
`table_entries` / `tables` — so no widget walks `entries` itself.

Two more jobs are the workspace's because both are questions about the whole
list, not about one entry:

- **Dropping cached documents.** `drop_document` discards an entry's document
  but keeps what only it held (a block's translations move to
  `pending_strings`), and `invalidate_path` does that for every entry reading a
  path a write just rewrote — sparing the entry that wrote, the blocks under
  it, and anything with unsaved edits of its own.
- **Missing files.** `missing_paths` is the de-duplicated worklist of
  referenced files not on disk, and `relocate_path` re-points every reference
  to one of them at a new file — so a ROM and the blocks and bookmarks under it
  are located once and corrected together, and a row still named after its file
  follows the new name. `retarget_files` is the other half: it gives one file
  entry a whole new file *list*, carrying its blocks and bookmarks, which is
  what a container edit applies. Both are pure data; the caller drops the
  documents and reads again.

Blocks are to files what celPix slices are, with these differences:

- a block's `Document` holds `strings`, and its dirty token changes on any
  translation, status or note edit;
- writing a block settles through the parent file's buffer, so several
  blocks over one file write in one deposit;
- a block over a compressed region owns the decompressed buffer and writes
  it back as one slot.

### 6.2 Table files and scripts

`project/formats/` reads and writes the text formats:

| Module           | Reads                                             | Writes                    |
|------------------|---------------------------------------------------|---------------------------|
| `table_native.py`| the native grammar                                | the native grammar        |
| `table_legacy.py`| romjuice, Cartographer, Atlas and abcde dialects, each with its own tool's rules, into the native model with conversion notices | — |
| `script.py`      | native scripts                                    | native scripts            |
| `translator.py`  | TSV, CSV, PO                                      | TSV, CSV, PO              |

`project/tables.py` wraps the readers for the one job every caller has:
`read_table_file(path, dialect, registry)` reads the file, parses it in its
dialect and applies each table's charset. The encoding is
`core.text.read_text_any`'s decision — UTF-8, else `cp932`, else `latin-1` —
left on `TableFile.encoding` and, when it is not UTF-8, said in a notice.

A table entry in the Files panel remembers its file path and dialect. In-app
edits live in the project as an **overlay** of added, changed and removed
entries over the file, so an unchanged file on disk keeps working for other
tools; **Save Table** folds the overlay into a native file.

`project/tables.py` owns the overlay as well as the reading, because they are
two halves of one thing. What the file gave is kept on the entry as
`file_tables`, the edits as `table_overlay` (per table id, per entry key: the
entry's line in the native grammar, or `null` for one removed):

| Function | When |
|-------------------|------------------------------------------------------------|
| `adopt_tables`    | a read: the file's tables become the baseline, and the overlay goes straight back over them — so a **Reload** picks up what changed on disk without discarding the user's edits |
| `capture_overlay` | an edit: the overlay is re-measured from the tables, never accumulated, so an undo and a redo leave the project holding exactly what they now say |
| `fold_overlay`    | **Save Table**: the file now says it, so the overlay is spent |

A table entry with no file — one made from a relative search or from Add from
Selection — has an empty baseline, so its every entry is overlay and the project
carries the whole table.

### 6.3 The `.mapchar` file

`project/projectfile.py` follows celPix's principles: plain JSON, references
and settings only, optional keys omitted at their defaults, a tolerant reader
that drops a broken entry and ignores unknown keys, relative paths with `/`
separators and case-insensitive recovery, a version with step migrations,
and aliases for renamed plugin ids.

```jsonc
{
  "version": 1,
  "current": 1,                                      // opt, an index into entries
  "entries": [
    { "kind": "file", "name": "rom.nes", "path": "rom.nes",
      "extra_paths": ["…"],                          // opt
      "container_id": "ines", "compression_id": "…", // opt
      "session": {"table_id": "main", "offset": 32768, "view": "strings"} }, // opt
    { "kind": "block", "name": "Dialogue", "path": "rom.nes", "parent": 0,
      "compression_id": "lz", "slice_offset": 16, "slice_length": 32,     // opt
      "spare_room": "keep",                          // opt, "fill" by default
      "config": "source=pointers start=$8000 stop=$8100 size=2 stride=2
                 endian=little mapping=banked:8000:4000 offset=1 bank=0
                 type=end table=main bound=$A000",   // one @block line, see 6.2
      "strings": [ { "i": 0, "t": "Welcome to[line]Tantegel Castle.[end]",
                     "s": "edited", "n": "…" } ],
      "box": { "font_index": 3, "width": 128, "height": 32, "line_height": 16,
               "letter_spacing": 0, "lines_per_page": 0, "origin": [0, 0],
               "effects": {"line": ["newline", 0]} },                     // opt
      "session": {…} },
    { "kind": "bookmark", "name": "…", "path": "rom.nes", "parent": 0,
      "offset": 4096 },
    { "kind": "table", "name": "main.tbl", "path": "tables/main.tbl",
      "dialect": "native",                           // opt
      "overlay": {"main": {"01000011": "43=C",       // opt, the in-app edits
                           "00000000": null}} },     //   a line, or null=removed
    { "kind": "font", "name": "font.png", "path": "font.png",
      "font": { "cell": [8, 8], "columns": 16, "base": 0, "chars": "ABC…",
                "glyphs": {"[heart]": 96}, "widths": [8, 6, …],
                "space": " ", "missing": "?", "transparent": 0 } }
  ]
}
```

A block's configuration is the `@block` line of the script grammar, so one
spelling covers the project file, a script and the block dialog. `parent` is an
index into `entries`; a `session` holds only what is not at its default, and a
`view` of `strings` means the strings tab was the open one.

Only strings with a translation, a non-default status or notes are written, and
a block that was loaded but never opened keeps its own in
`Entry.pending_strings` until an extraction adopts them — so a save writes back
the state of every block, not only the ones that were looked at. Originals are
never stored; they are re-decoded from the ROM, and a string whose original
changed since the project was saved is flagged *review*.

## 7. The UI layer

### 7.1 Composition

`ui/main_window/window.py` builds `MainWindow` from mixins, one per concern,
with `QMainWindow` last, as celPix does. Mixins reach each other only through
`self`. Nothing outside `main_window/` *changes* the model: the three workspace
docks read it through `ui/panel.py`'s `WorkspaceTreePanel` — which owns their
subscription and their row-to-entry lookup — and report intent by signal; every
other widget outside is handed values and knows nothing of a workspace.

| Concern | Modules in `ui/main_window/` |
|---|---|
| Shell | `window.py` (widgets, docks, menus, the undo stack and its guards, the title and dirty marker, file dialogs, alerts) |
| Active entry and refresh | `session.py`, `refresh.py`, `capability_sync.py` |
| Interpretation and position | `codecs_bar.py`, `navigation.py`, `history.py` |
| Entries and disk | `opening.py`, `entries.py`, `entry_clipboard.py`, `containers.py`, `writing.py`, `dumping.py`, `compression.py`, `plugins.py` |
| Tables | `tables_dock.py`, `table_editor.py` |
| Raw view | `raw_view.py`, `block_bar.py` |
| Blocks and strings | `strings_view.py`, `string_edit.py`, `wrap.py`, `find_replace.py` |
| Search | `search.py`, `relative_search.py`, `pointers.py` |
| Exchange | `import_export.py` |
| Projects | `projects.py` |
| Preview and fonts | `preview.py`, `fonts.py`, `hex_view.py` |

Widgets outside the mixins: `raw_widget.py` (the two-column byte view),
`text_widget.py` (the plain-text display), `strings_view.py` (the string grid
and its cell editor), the panels (`files_panel.py`, `tables_panel.py`,
`fonts_panel.py`, `hex_panel.py`), the tool windows, and the dialogs.

What more than one of them needs lives in small modules: `ui/widgets.py`
(`ResultsTable`, the `CancellableRun` run/stop/progress mixin for a tool window
and `ModalProgress` for a menu row, `fill_pick` and `select_data` for combos,
and `CompactComboBox`, the fixed-width picker of the bars whose open list
widens to its longest item),
`ui/panel.py` (`WorkspaceTreePanel`, which owns a
dock's workspace subscription and its row-to-entry lookup), `ui/window_layout.py`
(`WindowLayout` and `remember_layout`), `ui/help_dialogs.py` (the live shortcut
list) and `ui/__init__.py` (the `settings()` accessor and the view constants
`BYTES_PER_ROW` and `TEXT_WINDOW_BYTES`).

### 7.2 Where UI state lives

| State | Home |
|---|---|
| View offset, selection, current view tab | the window, live, and re-read from its widgets on every refresh |
| View offset, view tab and start table, per entry | `Entry.session`, captured when leaving an entry and saved with the project |
| Container, compression, block configuration, font, box | the `Entry` |
| Bytes, table set, strings, notices | the `Document` |
| Address format, display mode, Follow selection, theme, window layouts, recent projects | `QSettings` |
| Undo history, visit trail | the window, for the session |

`SessionMixin._activate_entry` is the single funnel for switching entries:
capture the outgoing session, load the incoming document, restore widgets with
signals blocked, refresh once. Re-activating the entry already on screen is a
no-op, and the three kinds that never become the view — a table, which opens in
the Table Editor, a font, which opens the Preview window's Font tab, and a
bookmark, which jumps the entry owning its bytes — take their own route out and
never claim `workspace.current`.

### 7.3 The refresh cycle

`RefreshMixin._refresh_view()` is the single choke point after any change:

1. **Settle** — clamp the offset; resolve the table set from the start
   table; re-extract the block when its config, table set or buffer
   revision changed (extraction is memoised on those three).
2. **Render** — Raw: decode the visible window and build the aligned
   hex/text rows; Strings: refresh the rows whose records changed.
3. **Push** to the widgets.
4. **Sync dependent surfaces** — Tables dock, Block bar, Hex panel, Preview,
   Search results, the window title.
5. **Gate** — `_sync_capabilities()` runs last, including on the nothing-open
   early return.

### 7.4 Capability gating, undo and dirty tracking

`core/capabilities.py` declares the gating once: a `Capability` is one thing the
editor can do, `CAPABILITIES` maps an `EntryKind` to the set it supports, and
`supports()` is the one question a control asks. `CapabilitySyncMixin` applies
that table at the tail of the refresh, in place of each surface carrying its own
"...and not on a table entry" clause; a control that would be *meaningless* on
this kind is hidden, one merely unavailable is disabled. Its `_GATES` table names
window controls by attribute and resolves them strictly, so a gate naming
something the window does not have raises on the first refresh rather than
silently gating nothing. The four capabilities whose surface is a *tool window* —
`WRAP`, `COMPRESSION_SCAN`, `TABLE_EDIT`, `FONT_EDIT` — are asked by the mixin
that drives that window instead, since each window already decides its own
enablement from its own state. (`EntryKind` lives in `core/capabilities.py`
because it keys that table and `core/` is the bottom layer;
`project/workspace.py` imports it from there.)

Undo is one `QUndoStack` for the session, and `ui/undo_commands.py` states three
invariants once, in `_StateCommand`:

- **The guard.** Every apply runs inside the window's `_undo_apply()` context,
  and `_push_command` refuses to push while it is set, so an apply can never
  push a second command.
- **Reach.** `_CurrentEntryCommand` switches back to the entry a change was made
  in; `_EditContextCommand` also returns to the view and the row or offset it
  was made at; `_InPlaceCommand` reaches nothing, because a rename or a reorder
  shows wherever you are.
- **Revision tokens.** An entry is unsaved when its live revision differs from
  the saved one, so every command over bytes or records carries the revision on
  both sides of its pair: undo restores the token the entry had before, and an
  undo back to what was written reads clean again.

A string edit merges only within a **typing run** — the window bumps
`_edit_run` when the selection or the entry moves — and a run that ends back
where it began obsoletes its step and hands back the earlier revision. View
moves in one entry merge the same way.

The project's dirty state is the other kind: a serialised comparison against
what was saved, shown through Qt's `[*]` placeholder and `setWindowModified`,
and deferred over an undo push so one push re-serialises the project once
rather than at each choke point it passes.

### 7.5 Raw widget and input

The raw widget paints rows of `address · hex · text` from a prepared row model,
with token spans drawn across their bytes and tints for token kinds. Selection
is in bytes; the widget emits byte ranges and the mixins map them to strings.

Navigation keys and the back/forward mouse buttons are routed through an
**application-wide event filter** (`NavigationMixin.eventFilter`), as celPix
does, rather than through shortcuts or the window's `keyPressEvent`: either of
those is pre-empted by a focused combo, list or tool window. The filter answers
only for the active window and yields to open popups, to the inputs that spend
the arrow keys themselves, and to Alt and Meta — which is what leaves Alt+Left
and Alt+Right to the visit trail's real shortcuts.

A position is spelled through `core/address.py`: a flat file offset, one of the
`BANK_PRESETS` console mappings, or three custom bank numbers. One format drives
the navigation bar's offset box, Go to Address and the Hex dock's address
column, and is remembered per machine.

### 7.6 Theme, icons and layout

celPix's: a `QPalette` on Fusion for light and dark, named ink constants, a
subset Material Symbols font driven by a Qt-free glyph enum, and a
`WindowLayout` per window — the main window and every tool window — saving to
`QSettings` on a coalescing 400 ms timer under a layout version, with `reset()`
putting the factory arrangement back behind Panels ▸ Reset Panel Layout.

## 8. Build, tooling and tests

- **Packaging** — Hatchling, version single-sourced from `__init__.py`,
  PySide6 runtime dependency. Builds and releases are described in
  [`../release.md`](../release.md).
- **Tools** — `tools/` holds the development scripts
  [development.md](../development.md) describes: `regen_fixtures.py`,
  `make_sample_projects.py` and `subset_icon_font.py`.
- **Tests** — `tests/` is flat, one module per area, with the celPix
  headless setup (offscreen platform, automatic `qt` marking, isolated
  `QSettings`, recorded dialogs). Model-layer tests run without Qt.
- **Verification fixtures** — `tests/fixtures/` holds synthetic ROMs, tables
  in every dialect, Cartographer command files and Atlas scripts, together
  with expected outputs produced by running abcde and romjuice from
  `../abcde/` and `../romjuice/`. `tools/regen_fixtures.py` regenerates them
  and skips when the tools are absent. Tests compare mapchar's dumps and
  insertions to those outputs.
- **Deliberate divergences** from the reference tools are enumerated in one
  table in `tests/fixtures/abcde/DIVERGENCES.md`, each with the fixture that
  exercises it. The set is the replication notes of the reference docs:
  per-frame counters with no sharing; a `+` child never ending its parent;
  end of data as a string end; longest-prefix-safe, optimal encoding with
  end tokens as ordinary alternatives; tables keyed by identity; numeric
  pointer sorting and duplicate-target merging; NFC on load and in storage
  where abcde normalises to NFD; fallback bits always emitted; romjuice's text-mode reads, stale buffers and last-line
  truncation not reproduced.
