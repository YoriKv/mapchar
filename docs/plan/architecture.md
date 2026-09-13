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
| `resources/`| Package data: TOML presets, plugin examples, charset data, the icon font, the app icon. |

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
  edits, font map, box); and session-only state (the lazily loaded `doc`,
  revision tokens).
- **`core.document.Document`** — the interpreted, mutable model the UI binds
  to: the decompressed `data` buffer, the `TableSet`, and for a block the
  `strings`. Created on first show; dropped and rebuilt when the file
  changes on disk through a write.

Entries are compared by identity. Blocks, bindings and undo commands hold
`Entry` objects, never list positions.

### 2.2 Tables and tokens

`core/table.py`:

- **`Table`** — `id`, `entries` keyed by bit string, `labels` keyed by label,
  `charset`, and derived lookups built once: `max_bits`, a longest-first
  prefix trie for decoding, and a text index for encoding.
- **`Entry`** — frozen: `bits`, `kind` (text, end, code, switch, return),
  `text` (text or label), `weight`, `operands: tuple[OperandSpec]`,
  `params: tuple[SwitchParam]`.
- **`SwitchParam`** — `table: Table | RAW | BITS`, `stop` (a count, `ANY`,
  or fallback bits), `shared: bool`.
- **`TableSet`** — the start table plus the closure of tables its switches
  reach, resolved by id from the loaded files. It is what decode and encode
  run over.
- **`Token`** — one decoded unit: `entry` (or `None` for unmatched data),
  `bits`, `bit_start`, `operands: tuple[int]`, `text` (the rendered form,
  codes in brackets). A token list renders to text by concatenation and
  parses back from text by the script grammar; both directions are in
  `core/tokens.py` and are inverse of each other.

Bit addressing: every position and length inside `core` and `engines` is in
bits, as abcde does, so odd-width entries and bit-packed text need no
special case. The pipeline converts byte offsets at the edges.

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
- **`Extraction`** — the result of running a block: the string records plus
  notices (end of data reached, operand cut short, pointer out of range).

### 2.4 Pointers and mappings

`core/mapping.py`: a **`Mapping`** converts `offset ↔ value` given a header
size and a bank number. Built-ins: `linear`, `lorom`, `hirom`, `gb`, `gba`,
`banked(bank_size, bank_base)`, `relative` (value = target − pointer
address). Each implements both directions and declares which pointer sizes
it supports. Mappings are plugins ([5](#5-the-plugin-system)).

### 2.5 Fonts and boxes

`core/font.py` and `core/layout.py` hold `Font` (sheet geometry, glyph map,
widths), `TextBox` and `CodeEffect` as described in [preview.md](preview.md).
They are frozen values whose mutators return new instances.

### 2.6 Other core modules

| Module | Holds |
|---|---|
| `context.py` | `PipelineContext` and the `KEY_*` hint names (source offset, header size, suggested mapping, suggested table) |
| `notices.py` | Non-fatal `Notice`s carried on the context and on extractions |
| `errors.py` | `Stage`, `PipelineError`, `TableError`, `EncodeError` |
| `bits.py` | `Bits` windows over a byte buffer, plus the bit/byte/hex conversions, key spelling, alignment and bit reversal every layer shares |
| `numbers.py` | `parse_num` and `format_num`: the `$hex` spelling tables, scripts and command files share |
| `text.py` | `split_lines` and the backslash `escape`/`unescape` the file formats share |
| `capabilities.py` | `EntryKind → frozenset[Capability]` (raw view, strings view, write, dump, pointers, preview, …) and `supports()` |
| `address.py` | Offset ↔ `bank:addr` display layouts for the navigation bar |
| `charset.py` | The `Charset` protocol: an iterable of `(bits, text)` |

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

`engines/encode.py` `encode(tokens_or_text, table_set, rules) -> bits`
searches for the cheapest bit string that **decodes back to the same tokens**:

- The input is text parsed into a token pattern: literal text runs, and
  codes that must map to entries by label (with operands).
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
differences per alphabet run (letters, digits, and the case gap as an
unknown constant) and scans the buffer for byte or 16-bit-word runs that
satisfy every difference, in both endiannesses, with `?` as a free
position. A hit carries the inferred base of every run, which the table
builder turns into entries.

### 3.4 Text scan

`engines/scan.py` slides a window over the buffer, decodes each window
through the table set, and scores it: the fraction of bits consumed by text
entries, times a bonus for dictionary hits on Latin tables (a small built-in
word list), minus a penalty for runs of unmatched bytes. Regions above a
threshold merge; each region reports its most frequent candidate terminator
(the byte most often followed by a fresh text run) and its most frequent
string-initial byte.

### 3.5 Pointer discovery

`engines/pointers.py` takes string start offsets, a header size and the
candidate space (mappings × sizes × endianness × an offset range) and
produces, for each candidate, the addresses where the encoded values occur.
Candidates are ranked by how many distinct strings they explain, then by how
regular the stride between their addresses is; the top result carries the
inferred `PointerTableSource`.

### 3.6 Layout

`engines/layout.py` renders tokens through a `Font` into a `TextBox` as a
list of glyph placements, records overflow, and implements Wrap
([preview.md](preview.md#wrapping)). It draws nothing; the UI paints the
placements.

## 4. The pipeline

### 4.1 Stages

```
load:  file(s) ─► CONTAINER.read ─► COMPRESSION.decompress ─► EXTRACT
save:  file(s) ◄─ CONTAINER.write ◄─ COMPRESSION.compress   ◄─ LAYOUT
```

- One plugin per byte stage, covering both directions. `FileRef`,
  `PathwayConfig`, `PipelineContext`, notices and the failure rules are
  celPix's: required calls hard-stop with a `PipelineError`, optional calls
  degrade with a notice, missing plugins resolve to a pass-through that
  leaves the entry view-only.
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
- **Deposit** is celPix's: compress, hand the container a
  `WriteTarget`, split across joined files, rewrite only changed files. A
  bounded compressed slot refuses a longer result; a shorter one is padded
  by the block's spare-room rule.

### 4.2 Exchange

`pipeline/exchange/` holds the Cartographer and Atlas importers and
exporters and the translator-file writers and readers. They build or consume
`BlockConfig` and `StringRecord` values and never touch the UI; the mapping
tables in [script-format.md](script-format.md) are their specification.

## 5. The plugin system

celPix's system, with these stages:

| Stage        | Required                        | Optional save half        | Other optional                        |
|--------------|---------------------------------|---------------------------|---------------------------------------|
| Container    | `read(ReadSource, ctx)`         | `write(data, WriteTarget, ctx)` | `describe`, `default_mapping`, `header_size` |
| Compression  | `decompress(data, ctx)`         | `compress`                | `PartialDecompression`                |
| Charset      | `entries() -> Iterable[(bits, text)]` | —                   | —                                     |
| Mapping      | `to_offset(value, header, bank)`, `to_value(offset, header, bank)` | — | `sizes`, `needs_bank`      |

- **Presets** (TOML) name an engine plus parameters; **formats** are
  params-free codecs adapted into an engine and an implicit preset. The
  generic mapping engine takes `bank_size` and `bank_base` so most consoles
  are presets, not code.
- **Registry**, **aliases** for renamed ids, **discovery** over typed
  folders, **`SourceRegistry` / `ScopedRegistry`**, **trust** by SHA-256,
  **detection** from static `PluginInfo` only, and **refresh** by building a
  fresh registry all follow celPix's `architecture.md` §4.
- Folder → stage: `containers/`, `compression/` (`.py`),
  `charsets/` (`.py` and `.tbl` files, which register as a charset named
  after the file), `mappings/` (TOML presets and `.py`).

## 6. The project layer

### 6.1 Workspace

`project/workspace.py` is celPix's workspace: `entries`, one `current`,
callback lists (`on_added`, `on_removed`, `on_reset`, `on_current_changed`,
`on_dirty_changed`), deduplication by normalised path, cascade close from a
file to its blocks and bookmarks, revision-token dirty tracking per entry,
and `invalidate_extractions` when a table changes. It answers every question
about what is open — `find_file` / `find_table` by path, `entry_by_id` for a
tree row, `entry_for_table`, `files` / `fonts` / `table_entries` / `tables` —
so no widget walks `entries` itself.

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
dialect and applies each table's charset.

A table entry in the Files panel remembers its file path and dialect. In-app
edits live in the project as an overlay of added, changed and removed entries
over the file, so an unchanged file on disk keeps working for other tools;
**Save Table** folds the overlay into a native file.

### 6.3 The `.mapchar` file

`project/projectfile.py` follows celPix's principles: plain JSON, references
and settings only, optional keys omitted at their defaults, a tolerant reader
that drops a broken entry and ignores unknown keys, relative paths with `/`
separators and case-insensitive recovery, a version with step migrations,
and aliases for renamed plugin ids.

```jsonc
{
  "version": 1,
  "current": 1,
  "entries": [
    { "kind": "file", "name": "…", "path": "rel/rom.nes",
      "extra_paths": ["…"],                          // opt
      "container_id": "ines",                        // opt
      "session": {"table_id": "main", "compression_id": "…"},
      "view": {"offset": 32768, "address_format": "banked:8000:4000"} },
    { "kind": "block", "name": "Dialogue", "path": "rel/rom.nes",
      "compression_id": "…", "spare_room": "fill",   // opt
      "config": { "source": {"kind": "pointers", "start": 32768, "stop": 33024,
                             "size": 2, "stride": 2, "endian": "little",
                             "mapping": "banked:8000:4000", "bank": 1, "offset": 0},
                  "type": {"kind": "end"}, "strings_per_pointer": 1,
                  "realign": [0, 0], "skips": [], "table_id": "main",
                  "bound": 40960, "mode": "packed", "fill": 255 },
      "strings": [ { "i": 0, "t": "Welcome to[line]Tantegel Castle.[end]", "s": "edited", "n": "…" } ],
      "box": { "font_index": 3, "width": 128, "height": 32, "line_height": 16,
               "effects": {"line": "newline", "end": "end"} } },     // opt
    { "kind": "bookmark", "path": "…", "offset": 4096, "session": {…}, "view": {…} },
    { "kind": "table", "path": "tables/main.tbl", "dialect": "native",
      "overlay": { "main": { "41": "A", "/FF": "[end]", "-42": null } } },   // opt
    { "kind": "font", "name": "…", "path": "font.png",
      "cell": [8, 8], "columns": 16, "base": 0, "chars": "ABC…",
      "glyphs": {"[heart]": 96}, "widths": [8, 6, …] }
  ]
}
```

Only strings with a translation, a non-default status or notes are written.
Originals are never stored; they are re-decoded from the ROM, and a string
whose original changed since the project was saved is flagged *review*.

## 7. The UI layer

### 7.1 Composition

`ui/main_window/window.py` builds `MainWindow` from mixins, one per concern,
with `QMainWindow` last, as celPix does. Mixins reach each other only through
`self`; widgets outside `main_window/` are views with no model access.

| Concern | Modules in `ui/main_window/` |
|---|---|
| Shell | `window.py` (widgets, docks, menus, undo stack, alerts) |
| Active entry and refresh | `session.py`, `refresh.py`, `capability_sync.py` |
| Interpretation and position | `codecs_bar.py`, `navigation.py`, `history.py` |
| Entries and disk | `entries.py`, `entry_clipboard.py`, `writing.py`, `dumping.py`, `compression.py` |
| Tables | `tables_dock.py`, `table_editor.py` |
| Raw view | `raw_view.py`, `raw_selection.py` |
| Blocks and strings | `block_bar.py`, `strings_view.py`, `string_edit.py`, `string_filter.py`, `find_replace.py` |
| Search | `search_window.py`, `relative_search.py`, `scan.py`, `pointer_discovery.py` |
| Exchange | `import_export.py` |
| Preview | `preview_window.py`, `fonts_dock.py`, `wrap.py` |

Widgets outside the mixins: `raw_widget.py` (the two-column byte view),
`strings_table.py` (the string grid and its cell editor), the panels
(`file_list_panel.py`, `tables_panel.py`, `fonts_panel.py`,
`hex_view_panel.py`), the tool windows and the dialogs.

What more than one of them needs lives in three small modules: `ui/widgets.py`
(`ResultsTable`, the `CancellableRun` run/stop/progress mixin, `fill_pick` and
`select_data` for combos), `ui/panel.py` (`WorkspaceTreePanel`, which owns a
dock's workspace subscription and its row-to-entry lookup), and `ui/__init__.py`
(the `settings()` accessor and the view constants `BYTES_PER_ROW` and
`TEXT_WINDOW_BYTES`).

### 7.2 Where UI state lives

| State | Home |
|---|---|
| View offset, address format, current view tab | `Document.view`, rebuilt from widgets on every refresh |
| Start table, compression, selection | `Entry.session`, captured when leaving an entry |
| Bytes, table set, strings | the `Document` |
| Zoom, theme, window layouts, recent projects | `QSettings` |
| Undo history, visit trail | the window, for the session |

`SessionMixin._activate_entry` is the single funnel for switching entries:
capture the outgoing session, load the incoming document, restore widgets
with signals blocked, refresh once.

### 7.3 The refresh cycle

`RefreshMixin._refresh_view()` is the single choke point after any change:

1. **Settle** — clamp the offset; resolve the table set from the start
   table; re-extract the block when its config, table set or buffer
   revision changed (extraction is memoised on those three).
2. **Render** — Raw: decode the visible window and build the aligned
   hex/text rows; Strings: refresh the rows whose records changed.
3. **Push** to the widgets.
4. **Sync dependent surfaces** — Tables dock, Block bar, Hex panel, Preview,
   Search results, action enables.
5. **Gate** — `_sync_capabilities()` runs last.

### 7.4 Capability gating, undo and dirty tracking

Capability gating, the single `QUndoStack`, thin before/after commands with
`_apply_*` methods, re-entrancy guards, command reach (`_CurrentEntryCommand`
/ `_InPlaceCommand`), merging of typing runs and offset moves, and the two
kinds of dirty (entry by revision token, project by serialised comparison)
are celPix's design, unchanged.

**From a keystroke to disk**

1. The string editor emits the new text; `StringEditMixin` pushes a
   `TranslationCommand` holding before and after text and the entry's
   revision tokens.
2. Apply sets the record, stamps the revision, re-encodes that string for
   the byte readout and status, and refreshes the row.
3. File ▸ Write calls `pipeline.insert.layout_block`, then `pipeline.save`
   with the splices, marks saved, and invalidates other entries on that
   path.

### 7.5 Raw widget and input

The raw widget paints rows of `address · hex · text` from a prepared row
model, with token spans drawn across their bytes and tints for token kinds.
Selection is in bytes; the widget emits byte ranges and the mixins map them
to strings. Keyboard routing uses an application-wide event filter for
navigation keys, as celPix does, yielding to text fields.

### 7.6 Theme, icons and layout

celPix's: a `QPalette` on Fusion for light and dark, named ink constants, a
subset Material Symbols font driven by a Qt-free glyph enum, and a
`WindowLayout` per window saving to `QSettings`.

## 8. Build, tooling and tests

- **Packaging** — Hatchling, version single-sourced from `__init__.py`,
  PySide6 runtime dependency. Builds and releases are described in
  [`../release.md`](../release.md).
- **Tools** — `tools/mapchar-lint/` lints hand-edited `.mapchar` files
  against a registry snapshot; `tools/tbl.py` converts legacy tables to
  native from the command line; `tools/dump.py` runs a block or a
  Cartographer command file headlessly and writes a native script.
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
  table in `tests/fixtures/DIVERGENCES.md`, each with the fixture that
  exercises it. The set is the replication notes of the reference docs:
  per-frame counters with no sharing; a `+` child never ending its parent;
  end of data as a string end; longest-prefix-safe, optimal encoding with
  end tokens as ordinary alternatives; tables keyed by identity; numeric
  pointer sorting and duplicate-target merging; no NFD; fallback bits always
  emitted; romjuice's text-mode reads, stale buffers and last-line
  truncation not reproduced.
