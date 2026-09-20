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
| `core/`     | The data model: tables and tokens, strings and blocks, pointers and mappings, the preview font and boxes, the pipeline context, notices, errors, capabilities. |
| `engines/`  | Pure algorithms over the model: decode, encode, relative search, text scan, pointer discovery, layout. No I/O. |
| `pipeline/` | Runs the byte stages in both directions, extracts blocks into strings, and lays strings out for writing. |
| `plugins/`  | The plugin API, registry, discovery, trust, detection, and every built-in plugin: containers, compressions, charsets, mappings. |
| `project/`  | The open-entries model (`workspace.py`), the `.mapchar` file (`projectfile.py`), the glossary (`glossary.py`), reading a table file from disk (`tables.py`), the table-file and script readers and writers (`formats/`), and the other tools' formats (`exchange/`). |
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
  `EntryKind` (file, block, bookmark, folder, table); where its bytes
  are (`path`, `extra_paths`, block `offset`/`length`); for a file's rows —
  blocks, bookmarks and folders — the file they belong to (`parent`) and the
  folder they are shown in (`folder`, `None` directly under the file); its chain
  (`container_id`, `compression_id`); its `BlockConfig`; its
  `EntrySession` (a file's reading — a `BlockConfig` whose source has no
  addresses — its table, Follow pointers, view position, and the reading its
  last switch of mode set aside, which is not saved); kind-specific state (table
  edits, box); the `notices` its file's last read produced; and
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
  text the encoder accepts for an entry's bits), `charset`, `includes` (the
  ids of the tables it starts from), and derived lookups kept as entries are
  added: `max_bits` and entries bucketed by key length, which `match` scans
  longest length first. `revision` counts every change; what is derived from
  the whole table — its switch targets, its labels' effects, its resolution —
  is cached against it and left behind by a copy — `cached(name, make)` is how
  anything derived asks for it, including the encoder's text index over the
  entries (`engines/encode.py`), which every string encoded through the table
  would otherwise rebuild.
- **`resolve(table, available)`** — the table with its includes laid under its
  own entries: charset, then each include resolved in order, then
  `own_entries`, an empty text removing a key from below. A table that
  includes nothing is itself; otherwise the result is a table of its own,
  cached on the includer against every table it reaches and their revisions,
  so an edit to an included table shows on the next build. It raises for an
  include not in `available`, a cycle and a label twice. `inherited(table,
  available)` is the include layer alone, with the table each entry is own to,
  for the Table Editor.
- **`Entry`** — frozen: `bits`, `kind` (text, end, code, switch, return),
  `text` (text or label, composed to NFC on construction, so every dialect and
  every charset agree on one form), `weight`, `operands: tuple[OperandSpec]`,
  `params: tuple[SwitchParam]`, `comment`, and `effect` (`core.font.Effect`:
  *none*, or one of `TABLE_EFFECTS` — *newline*, *page*, *pause*).
- **`SwitchParam`** — `table_id: str` (a table's id, or `raw`, `bits` or
  `return`), `stop: Stop` (a weighted `count`, `fallback` bits, or neither —
  which `Stop.any` reports), `shared: bool`, `through: bool` (the frame falls
  through to the one beneath).
- **`TableSet`** — the start table plus the closure of tables its switches
  reach, resolved by id from the loaded files, one table each, each through
  `resolve`: a missing switch target or include fails `build`. It is what
  decode and encode run over.
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
  `PointerListSource`, `NestedPointerSource`), `string_type` (`EndToken`,
  `FixedLength(length, stop_at_end)`, `Pascal(width, counts_tokens, endian)`,
  `NextPointer`, `Lines(count)`), `table_id`, `strings_per_pointer`,
  `realign`, `skips`, `header` (bytes before each string of a range that are
  not text), `line_length`, `bound`, `write_mode` (`PACKED`,
  `SLOTTED`), `fill` (a byte pattern: `fill_run` lays it down from the start
  of the room it fills, `is_fill` recognises it), and the artificial codes a
  fixed string is shown with: `show_end`, `end_label`, `line_label`.
- **Pointer sources** — `PointerTableSource` and `PointerListSource` carry a
  `null` value that reaches no string. `NestedPointerSource` is a table of
  records, two outer pointers each — an inner table and the base its pointers
  count from — with `inner_size`, `inner_endian` and `inner_null`; the inner
  table runs from its address to the base. `PointerSource` names all three.
- **`grouped_strings(config, strings)`** — the strings a layout handles
  apart, each with the base it is told by: all of them under no base, or for a
  nested source one group per record, told by the base the string's first
  pointer counts from. **`string_groups(config, strings)`** is the same
  without the bases.
- **`StringRecord`** — one string: `index`, `start_bit`, `end_bit` (bits into
  the decompressed buffer, with `start`, `end` and `length` derived byte
  properties), `tokens: list[Token]` (the decode of the bytes as they are —
  `current_text()` renders it), `original: str` (the text when the block was
  made: seeded from the tokens by an extraction and replaced by what the
  project saved), `pointers: tuple[PointerRef]`, `replacement: str | None`
  (text to encode in place of the bytes on the next layout; transient),
  `status`, `notes`, the `notices` reading it raised, and `lines` — the token
  indices a fixed-line piece starts at. *Edited* and *untouched* follow from
  the texts (`refresh_status`); *review* is set; *overflows box* is computed
  on demand, never stored.
- **`PointerRef`** — `address`, `size`, `endian`, `mapping_id`, `offset`, and
  the `value` read from disk. A nested source's inner pointer is `linear`, its
  `offset` the base of its group.
- **`source_start(source)`** — where a source begins, or `None` when it does
  not say (an empty pointer list, no source): the one answer the Files panel's
  sort, the block bar and a restored view position all read.
- **`source_span(source)`** — the bytes a source itself occupies, `(start,
  stop)`, or `None`: the range, the fixed strings, the pointer table, or the
  stretch from a pointer list's lowest pointer to the end of its highest. What
  a block's view is confined to.
- **`Extraction`** — the result of running a block: the string records plus
  notices (end of data reached, operand cut short, pointer out of range), and
  `inner_tables`, a nested source's inner pointer table address by the base
  its pointers count from — the key `grouped_strings` tells a group by, so a
  group can say which table reached it. The `Document` carries it on.

### 2.4 Pointers and mappings

A **`Mapping`** converts `offset ↔ value` given a bank number and, for a
relative mapping, the address the pointer itself sits at. Mappings are
plugins ([5](#5-the-plugin-system)), so the protocol is declared with the
other stage protocols in `plugins/base.py` and the built-ins — `linear`,
`lorom`, `hirom`, `gb`, `gba`, `banked(bank_size, bank_base)` and the two NES
bank layouts it is also registered under (`nes_c000`, `nes_8000_2000`), and
`relative` (value = target − pointer address) — live in
`plugins/builtins/mappings.py`. Each implements both directions and declares
which pointer sizes it supports.

Looking one up by id is the registry's: `resolve_mapping(registry, id)` and
`mapping_for(source)` in `plugins/registry.py`, which also build the
parameterised `banked:<base>:<size>` ids on demand. `core/mapping.py` keeps
only the bytes on either side of the conversion, `read_pointer` and
`pointer_bytes`.

### 2.5 Fonts and boxes

`core/font.py` holds `Font` (a system font as the UI measured it: its family
and size, a line's height and baseline, an advance per character and the
characters it cannot draw), `TextBox`, `Effect` (which table entries use too)
and `CodeEffect` as described in [preview.md](preview.md); `engines/layout.py`
is what lays a string out in one. They are frozen values; measuring the real
font is `ui/preview_font.py`'s, so nothing here imports Qt.

### 2.6 Other core modules

| Module | Holds |
|---|---|
| `context.py` | `PipelineContext`, the `SourceSpan` a joined read publishes, and the `KEY_*` hint names (source files and offset, header size, suggested mapping and table, consumed size, complete, partial decode) |
| `notices.py` | Non-fatal `Notice`s — message, level, offset, detail, source — carried on the context and on extractions; `notice_lines()` renders one as its message with the detail indented under it |
| `errors.py` | `MapcharError`, which everything mapchar raises derives from: `Stage`, `PipelineError` (stage, action, plugin, pathway), `LocatedError` and its `TableError` and `ScriptError`, `EncodeError` |
| `bits.py` | `Bits` windows over a byte buffer, plus the bit/byte/hex conversions, key spelling, alignment and bit reversal every layer shares |
| `numbers.py` | Every way a number is written: `parse_num`/`format_num` for the `$hex` spelling tables, scripts and command files share, and the one hex scanner behind `parse_hex`, `parse_hex_offset`/`format_hex_offset` and `parse_flat_hex`, which the UI's always-hex fields and `address.py` read through |
| `text.py` | The Unicode model: `nfc`/`nfd`, `is_mark`, `graphemes` and `char_units` (a base character plus its combining marks — one glyph slot), and `fold` for a case- and form-insensitive comparison |
| `textmatch.py` | The one filter every list runs: `words_of` folds and splits what was typed, and `matches_words` says whether each of those words is in one of a row's fields — fields folded apart, so no word matches over the seam between two |
| `capabilities.py` | `EntryKind → frozenset[Capability]` (raw view, strings view, write, exchange, pointers, preview, …) and `supports()` |
| `address.py` | Offset ↔ `bank:addr` display layouts for the navigation bar |

## 3. Engines

Every engine is a pure function over `core` values, with a cancel/progress
callback where it can run long.

### 3.1 Decode

`engines/decode.py` `decode(bits, tables, start_bit, rules) -> DecodeResult`
— the limit is `rules.limit_bit` — is the stack machine of
[`../abcde/bin2text.md`](../abcde/bin2text.md#replication-notes), built to its
replication notes rather than to abcde's behaviour:

- **State** — the current bit, a stack of frames `(table, counter, stop,
  fallback_bits, shared)`, the tokens so far, `end_tokens_seen`.
- **Step** — in the top frame: a count the frame still has to read from the
  data (a `u8`… stop) is read first, silently, and becomes its counter;
  else check the frame's fallback bits; else longest-prefix match in the
  frame's table (its length buckets, longest first); else emit one unmatched
  byte (or the remaining bits when fewer than 8 remain before the limit).
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
- **Falling through** — with no match in the top frame's table, a frame with
  `through` set hands the window to the frame beneath, passing a pending
  return and stopping at a `raw` or `bits` frame; the match counts in the top
  frame and acts as in the table holding it, whose id the token carries.
- **Lines** — a token that is the rules' `line_label` code (`[line]`), or whose
  entry has the *newline* effect, is marked `newline`; one whose entry has the
  *page* effect is marked `page`. Both render with a line break; with
  `max_lines` set (the *Lines* string type) the string ends after that many
  `newline` tokens.
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
- **State** — `(position in text, frame stack, forbidden suffixes, silent-switch
  chain length, end tokens used)`. Frames are keyed by table identity, never
  file name.
- **Search** — Dijkstra with an admissible heuristic (the table set's
  minimum bits per character); a state closes when popped, so the result is
  optimal.
- **Longest-prefix safety** — after emitting an entry that is a proper prefix
  of a longer entry in the same table, the suffix that would complete the
  longer entry is forbidden as the next emission. This is the constraint
  abcde omits.
- **Fallback bits** are emitted whenever a fallback frame closes, including
  at the end of the string.
- **Falling through** — the top frame's successors come from its table and,
  while frames fall through, from each table beneath, in the decoder's order.
  An entry taken from beneath carries the tables above it as shadows: bits a
  shadow matches are refused, and its keys that extend them are forbidden
  suffixes, so the decoder cannot find them first.
- **A count read from the data** is a placeholder of the operand's width,
  emitted when its frame first comes on top, and filled in with the weight the
  frame matched when the frame closes — a move the search may make at any
  step, so the count is whatever the cheapest encoding needs. A frame still
  open at the end of the text is closed there, which writes its count.
- **End tokens** are ordinary alternatives; whether one is required at the
  end is the string rule's business, not the search's. In end-terminated
  rules one may only come last, except that a block reading N strings per
  pointer allows N−1 earlier ones, each restarting the frame stack as the
  decoder does, and then requires exactly N.
- **Verification** — the result is decoded with [3.1](#31-decode) and must
  give back the same tokens; a mismatch is an `EncodeError`, which refuses
  the edit.
- **Failure** is diagnosed from the atom the search got furthest to and the
  frames in use there (`_why`): a character no table has an entry for, named
  with its code points and composed with the mark it carries; one whose table
  is not in use there, with the switch code that reaches it; a code no table
  knows, or the operands one takes and what would not fit them; or an entry
  that exists and simply cannot follow what comes before it. The text around
  the atom follows when the string is long enough for the place to matter.

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
entries, plus a capped bonus for dictionary hits (a small built-in word
list), minus a penalty that grows with the square of the share of unmatched
data. Regions above a threshold merge, and each is then cut to what it
actually holds rather than to the window grid.

A region is first searched, a window either side, for chains of
length-prefixed records: 0 to `MAX_HEADER` header bytes, a one-byte length and
that many bytes of text, back to back, at least `MIN_RECORDS` of them and with
at least one byte of every record's header and length one the table does not
read as text — otherwise every run of characters would count. Chains are found
a header size at a time, the longest wins the bytes it covers, and each walks
back to the record it continues, so a chain that began before the first window
to score comes out whole. A chain carries a `Records(header, width)` on its
region, is scored on the characters alone, and becomes a `Pascal` block over a
range with that header.

What the chains leave of a region comes out as terminated text, cut to the
longest run of strings the table reads whole: the run begins at the first
character after the last byte the table does not read (a string whose front
the run-up ate is still a string) and ends at the last end token a run of
characters reached. Those regions report their most frequent candidate
terminator (the byte most often followed by a fresh text run) and their most
frequent string-initial byte.

### 3.5 Pointer discovery

`engines/pointers.py` takes string start offsets, a bank number and the
candidate space (mappings × sizes × endianness × an offset range) and
produces, for each candidate, the addresses where the encoded values occur, the
value found at each and the bank it is read in. A mapping that `needs_bank`
answers `bank_of(offset)`, and that — not the bank number passed in, which is
only the fallback for a mapping that does not say — is the bank each string's
value is computed in, so a banked table is found without the bank being known
first and the strings of one block may sit in different banks. Candidates are
ranked by `run_explained()` — the strings the table itself reaches, a far
better signal than the raw count because a short value turns up all over a file
— then by how many strings they explain at all and how regular the stride
between their addresses is. Each carries both answers a result can be taken as:
`source()` is the inferred `PointerTableSource` over `table_run()` — the
longest run of hit addresses a whole number of strides apart, at most `RUN_GAP`
of them, so stray matches elsewhere in the file stay out of the table — under
the bank its run mostly reads in, a source carrying one; and `refs()` the
`PointerRef`s per string that **Attach** puts on the strings instead, a string
the run reaches keeping only its in-run addresses, since a packed write
rewrites every attached pointer and a coincidence kept is a byte pair
corrupted. A `progress` hook is called per combination and stops the walk when
it returns `False`, so a stopped search still ranks what it had.

### 3.6 Layout

`engines/layout.py` lays tokens out through a `Font` into a `TextBox` as a
list of character placements, records overflow, lists the text the font cannot
draw, and implements Wrap ([preview.md](preview.md#wrapping)). It draws
nothing; the UI paints the placements. The box a block lays out in is its own
with `code_effects(table_set, line_label)` — the effects its entries declare,
then the line code as a *newline* — merged under it by `with_code_effects`;
the window's `_layout_box` is that box for every overflow, readout, wrap and
newline-code question.

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
  string rule, and calls the decode engine per string.
- **Nested sources** (`pipeline/extract.py`): `nested_records` maps the outer
  table's records, `pointer_addresses` lists every pointer a source reads —
  outer and inner — and `reextract` reads again only the groups a changed
  stretch reaches, keeping every other record as it was, which is what keeps
  an edit in a block of thousands of strings from reading them all.
- **Fixed strings that stop at an end token** hide it: `_decode_fixed` marks
  the end token `fallback` (`is_hidden_end`), so it renders as nothing and
  keeps its bits, when all that follows it is fill; any other tail is read
  after a visible end token. `legacy_fixed_text` and `respell_fixed_end` turn
  what a version 1 project saved into today's spelling.
- **View reading** (`pipeline/view_read.py`) is what the Hex and Text tabs
  show: the bytes in view cut by the reading's string type, or read as
  pointers — each with its value, its target and whether it is null, a nested
  source's outer and inner pointers alike, the outer ones carrying a `role` of
  `table` or `base` since they reach structure rather than text — and the
  string a target reaches, by the same `decode_one` extraction uses. The cut is in step with the strings
  the block reads: a range's fixed length runs from its start, so a view that
  starts part-way through a string is handed the byte it begins at and shows
  the rest of that one, then whole ones. Only a range of fixed strings has such
  a grid — a Pascal count is read from the data, and a view that starts inside
  one of those cannot find it; a pointer source's strings are each at their own
  target.
- **Text view** (`pipeline/text_view.py`) turns those tokens into what the Text
  tab shows: a body, a map from characters to bytes (`TextModel`), and
  `TextDecode`, which keeps the tokens from one window to the next. A string's
  end breaks the line: an end token carries its own break, and a string cut by
  its length — which ends in no token of its own — is broken from where the
  strings start, so two strings never share a line and read as one.
- **Layout** (`pipeline/insert.py`) turns a block's strings into a byte
  splice: encode each string's `replacement` (or reuse its bytes when it has
  none), lay the results out in *packed* or *slotted* mode, compute the new
  pointer values through the mappings, and refuse the whole block when any
  string crosses its bound, reporting each offender. Its output is a list of
  `(offset, bytes)` splices over the decompressed buffer plus the pointer
  splices. A slotted string's slot (`slot_ends`) is its own bytes and the run
  of whole fill patterns after them, up to the next string in address order,
  the bound or the end of the buffer, whichever is first. A packed string has
  no place of its own, so its room (`packed_ends`) is its own bytes plus its
  group's **spare** — everything between what the group's strings hold and its
  bound — which is in every string's room and in no two at once. The Bytes
  column and the byte readout pass the bytes too (`string_ends`), so what they
  report is the room the layout will take; a caller with none to hand gets the
  bytes the string holds now, and the layout is the one that refuses.
  Nothing outside a slot is written, so a splice never touches bytes no string
  owns and never runs past the buffer. The layout runs per group
  (`string_groups`): a nested block lays out only the groups holding a
  replacement, each up to its own bound (`group_bounds`). A packed layout
  writes a string that is the tail of the one before it once
  (`_is_tail`). A fixed string that stops at an end token encodes its text,
  then an end token where one fits (`_encode_stopping`).
  The window runs it on every edit (`string_edit.py`): the splices land in the
  buffer every entry over those bytes reads, as one undo step, after a
  re-extraction has shown the block reads as the same strings with the edited
  ones saying what was typed. Extraction passes over the fill byte a shortened
  string leaves behind — between the strings of a range, and at the end of a
  *next pointer* string, which keeps its whole extent — but only when the
  table maps nothing beginning with it (`padding_bits`); a fill byte the table
  maps is text and is read.
- **Slots** (`compress_for_slot`) are the write minus the store, so the checks
  that make one safe hold however the bytes are delivered — through a container
  to a file, or spliced into a parent's buffer by a block. A *bounded* slot
  refuses a longer result; an *unbounded* one (no recorded length, which is not
  the same as a length of zero) is still bounded by the end of what holds it. A
  short result in a bounded slot is padded per the pathway's `SlotFill`: `fill`
  writes the first byte of the block's fill over the slack, `keep` writes short
  and leaves the previous stream's tail standing.
- **Save** (`save`) then compresses, hands the container a `WriteTarget` over
  the destination's bytes **read at that moment** rather than remembered from
  the load, splits the result across joined files at the boundaries they have
  now, and rewrites only the files that changed.
- **Inspection** (`pipeline/inspection.py`) runs one container's read alone, on
  a context of its own, and returns a `ContainerReport` of what it published and
  what it had to assume — reported, never raised, since it is reached precisely
  when an entry did not come out as expected.
- **Scanning** (`pipeline/scan.py`) walks forward for the next complete
  structure a scheme can read (`find_next_structure`), with a progress/cancel
  callback; `decompress_at` is one probe of it, and asks for a partial decode
  when it is previewing. A scheme's `signature` says where a probe is worth
  making at all (`signature_of`): `scheme_at` is what arms the Decompressed view
  as the view moves, and `find_structures` lists every structure in a buffer,
  searching for the signature where there is one and walking byte by byte where
  there is not. Each takes the schemes to consider, so the caller decides
  whether that is one or all of them.

## 5. The plugin system

celPix's system, with these stages:

| Stage        | Required                        | Optional save half        | Other optional                        |
|--------------|---------------------------------|---------------------------|---------------------------------------|
| Container    | `read(ReadSource, ctx)`         | `write(data, WriteTarget, ctx)` | `describe`, `default_mapping`, `header_size` |
| Compression  | `decompress(data, ctx)`         | `compress`                | `bind_tree(rom)`, `signature`         |
| Charset      | `entries() -> Iterable[(bits, text)]` | —                   | `aliases()`, `codec`                  |
| Mapping      | `to_offset(value, bank, ptr_address)`, `to_value(offset, bank, ptr_address)` | — | `sizes`, `needs_bank` |

- **Optional is optional.** Every hook in the last column is reached by
  `getattr` and probed: one that is absent and one that raises mean the same
  thing, the documented default, so a display-only hook can never cost a load
  ([4.1](#41-stages)). A probe on the load path (`pipeline._probe`) also records
  a warning notice naming the plugin; `describe`, which only Container Info
  reads, loses its rows and says so by their absence.
  `plugins/base.py` declares them — `ContainerExtras` for the container's
  optional half, and comment blocks on `Compression`, `Charset` and `Mapping`
  for those stages'. Two of them are values rather than calls, read the same
  way and with the same "absent means the default": a charset's `codec`, and a
  compression's `signature`, the bytes its streams start with, which is a claim
  about where a structure may be and never proof that one is there.
  `PartialDecompression` is the base class a scheme
  whose decoder finds its own end inherits both of its methods from, publishing
  the consumed size and the complete flag the pipeline needs from one
  `_decode`.
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
- **Discovery** (`plugins/discovery.py`) scans the typed folders of each root
  in turn — `MAPCHAR_PLUGIN_PATH`, the user folder, then the project's
  `plugins/` —
  labelling everything out of a root "Your plugins" or "Project plugins".
  `_`-prefixed files and unknown folders are ignored, a loose plugin file in a
  root is reported, and every failure becomes a `PluginLoadIssue` rather than
  an exception: a bad preset or a module that raises on import cannot stop
  startup. `ScopedRegistry` is what a module's `register(registry)` receives,
  checking the folder's stage and the required methods and recording an issue
  instead of raising. A `.tbl` charset is read by the `table_reader` its caller
  passes in (`app.py` wraps `read_table_file`): reading a table file is
  `project`'s job, and `plugins` sits under it.
- **Trust** (`plugins/trust.py`) gates `.py` files only, by SHA-256 of the
  exact bytes executed, with approved digests in
  `<AppData>/trusted-plugins.json`. It is
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
- **Examples** (`plugins/examples.py`): `resources/data/plugin-examples/` is
  copied into the user folder at startup — a README and `_`-prefixed examples
  per folder, rewritten when a shipped one changes, never touching a user's own
  files. Code examples ship as `.py.txt` because frozen builds exclude `.py`
  data.
- Folder → stage: `containers/` (`.py`), `compression/` (`.py`, TOML presets),
  `charsets/` (`.py` and `.tbl` files, which register as a charset named
  after the file), `mappings/` (`.py`, TOML presets).

## 6. The project layer

### 6.1 Workspace

`project/workspace.py` is celPix's workspace: `entries`, one `current`,
callback lists (`on_added`, `on_removed`, `on_reset`, `on_rows_changed`,
`on_current_changed`, `on_dirty_changed`), deduplication by normalised path,
cascade close from a file to its rows and from a folder to its contents,
revision-token dirty tracking per entry,
`free_name` (blocks and bookmarks never share a name), and
`invalidate_extractions` when a table changes. It answers every question
about what is open — `find_file` / `find_table` by path, `entry_by_id` for a
tree row, `entry_for_table`, `dirty_entries`, `files` /
`table_entries` / `loaded_tables` / `tables`, `children` (a file's every row),
`contents` (the rows directly under a file or folder), `descendants` (what
goes with a row when it is removed, moved or copied) and `blocks_of` (the
blocks of a file or a folder alone, and with `loaded=True` only those holding
a document) — so no widget walks `entries` itself. `tables` is every table a block can read
through: the loaded ones over `builtin_tables`, the registry's charsets as
tables (`plugins.charsets.CharsetTables`, each built on first read), which the
window sets and resets with its registry.

The list is kept in tree order: a file's rows follow it, and a folder's
contents follow the folder, so a row and what it holds are one run of the list.
`add` puts a row after the last row its folder or file holds, `moved` lifts
rows with what they hold and puts them back in one pass (`reordered` is it for
one row), and a loaded project is laid out that way (`tree_order`). A folder is
where a row is *shown*: `layout` is the order with each row's folder, and
`arrange` lays one out as a single reset, which is what a reorder, a move
between folders and their undo apply. `on_added` and `on_removed` fire per row;
`on_rows_changed` fires once for an add or a close however many rows it took,
and once for a whole `batch()` — a paste, a removal, the undo of either — and
is what the tree docks rebuild on.

`entries_sharing` answers the other question the whole list settles: which
loaded entries hold the *same bytes*. A plain block reads its file's buffer,
so it shares with the file and its other plain blocks; a compressed block
reads its slot's payload, so it shares with every block over that slot; a
file's own compression is the whole file's, decoded on the way in, so it is
never a slot. Loading a second block over one slot, splicing an edit, and
writing a file all ask it.

Two more jobs are the workspace's because both are questions about the whole
list, not about one entry:

- **Dropping cached documents.** `drop_document` discards an entry's document
  but keeps what only it held (a block's originals, statuses and notes move to
  `pending_strings`, with the bits each string covered when the drop is for a
  block edit). It does not keep bytes, so nothing drops a buffer holding
  unsaved edits: a block edit re-reads a compressed block over the payload it
  already has, and where a drop is unavoidable — a container edit, a change of
  compression scheme — the entry is marked saved with it, since an entry left
  unsaved would claim edits no buffer holds. `invalidate_path` does that for every entry reading a
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

- a block's `Document` holds `strings`; a status or note edit stamps the
  block, while a string edit stamps every entry whose own buffer it changed —
  the file for a plain block, and each block over a compressed slot, since one
  payload is all of theirs;
- a plain block reads its file's buffer, so several blocks over one file
  write in one deposit; blocks over one compressed slot share its payload and
  write together. A write covers every block handed to it, and a block it
  cannot write is reported by name rather than skipped: one left out of a
  write that reported success would stay unsaved for ever.

### 6.2 Table files and scripts

`project/formats/` reads and writes the text formats:

| Module           | Reads                                             | Writes                    |
|------------------|---------------------------------------------------|---------------------------|
| `table_native.py`| the native grammar                                | the native grammar        |
| `legacy/`        | romjuice, Cartographer, Atlas and abcde dialects, a module each, following their own tool's rules into the native model with conversion notices | the abcde dialect, which an Atlas export needs |
| `script.py`      | native scripts, and `apply_script` walks one into a project's blocks | native scripts            |
| `translator.py`  | TSV, CSV, PO                                      | TSV, CSV, PO              |
| `summary.py`     | — | either importer's report as the one shape the confirmation dialog draws: which blocks, how many strings, what was skipped |
| `textfile.py`    | how a text file is spelled, under all of them: `read_text_any` (the one `open()` of a text file, which the window's imports and drop sniffing use too), `not_utf8` (the one wording of its notice), `split_lines`, `BOM` | the backslash `escape`/`unescape` |

`project/tables.py` wraps the readers for the one job every caller has:
`read_table_file(path, dialect, registry)` reads the file, parses it in its
dialect and applies the charset. It also names tables: `table_id_for` is the id
a file without a `@table` line gives its table, and `free_table_id` numbers one
up (`main_2`) against the ids already taken. A `TableFile` holds the file's one `table`,
plus the `extra_tables` a legacy conversion made (a romjuice kanji array, an
abcde file with several `@id` lines), which open as table entries with no
file. The encoding is
`textfile.read_text_any`'s decision — UTF-8, else `cp932`, else `latin-1` —
left on `TableFile.encoding` and, when it is not UTF-8, said in a notice.

A table entry in the Files panel remembers its file path and dialect. In-app
edits live in the project as an **overlay** of added, changed and removed
entries over the file, so an unchanged file on disk keeps working for other
tools; **Save As File** folds the overlay into a native file.

`project/tables.py` owns the overlay as well as the reading, because they are
two halves of one thing. The live table is the entry's `table`; what the file
gave is kept as `file_table`, the edits as `table_overlay` (per entry key: the
entry's lines in the native grammar — its comment lines, then its own — or
`null` for one removed), an id given in place of the file's as `table_id`, and
includes given in place of the file's as `table_includes`. Both tables hold
only what their file says; includes are resolved when a table set is built,
so the overlay never measures what an included table gives:

| Function | When |
|-------------------|------------------------------------------------------------|
| `adopt_table`     | a read: the file's table becomes the baseline, and the overlay goes straight back over it — so a **Reload** picks up what changed on disk without discarding the user's edits |
| `capture_overlay` | an edit: the overlay is re-measured from the table, never accumulated, so an undo and a redo leave the project holding exactly what it now says |
| `fold_overlay`    | **Save As File**: the file now says it, so the overlay is spent |

A table entry with no file — one made from a relative search or from Add from
Selection, or split from a legacy file — has no baseline, so its every entry is
overlay and the project carries the whole table, with its id as `table`.

### 6.3 The `.mapchar` file

`project/projectfile.py` follows celPix's principles: plain JSON, references
and settings only, optional keys omitted at their defaults, a tolerant reader
that drops a broken entry and ignores unknown keys, relative paths with `/`
separators and case-insensitive recovery, a version with step migrations,
and aliases for renamed plugin ids.
`tools/mapchar-lint` restates this reader to report what it drops or ignores
([lint.md](../lint.md)), so a change to the format changes the linter too.

```jsonc
{
  "version": 2,
  "current": 1,                                      // opt, an index into entries
  "entries": [
    { "kind": "file", "name": "rom.nes", "path": "rom.nes",
      "extra_paths": ["…"],                          // opt
      "container_id": "ines", "compression_id": "…", // opt
      "session": {"table_id": "main", "offset": 32768, "view": "strings",
                  "config": "source=pointers start=$0 stop=$0 size=2 …",
                  "resolve_pointers": true,
                  "preview_scheme": "rnc2"} },           // opt
    { "kind": "block", "name": "Dialogue", "path": "rom.nes", "parent": 0,
      "compression_id": "lz", "slice_offset": 16, "slice_length": 32,     // opt
      "spare_room": "keep",                          // opt, "fill" by default
      "config": "source=pointers start=$8000 stop=$8100 size=2 stride=2
                 endian=little mapping=banked:8000:4000 offset=1 bank=0
                 type=end table=main bound=$A000",   // one @block line, see 6.2
      "strings": [ { "i": 0, "t": "Welcome to[line]Tantegel Castle.[end]",
                     "s": "edited", "n": "…" } ],
      "box": { "width": 128, "height": 32, "line_height": 16,
               "letter_spacing": 0, "lines_per_page": 0, "chars_per_line": 0,
               "origin": [0, 0],
               "effects": {"line": ["newline", 0]} },                     // opt
      "session": {…} },
    { "kind": "bookmark", "name": "…", "path": "rom.nes", "parent": 0,
      "folder": 3,                                   // opt, an index into entries
      "offset": 4096 },
    { "kind": "folder", "name": "Battle", "path": "rom.nes", "parent": 0 },
    { "kind": "table", "name": "main.tbl", "path": "tables/main.tbl",
      "dialect": "native",                           // opt
      "table": "font",                               // opt, an id in place of the file's
      "includes": ["script"],                        // opt, includes in place of the file's
      "overlay": {"01000011": "# the letter C\n43=C", // opt, the in-app edits
                  "00000000": null} },               //   its lines, or null=removed
    { "kind": "table", "name": "kanji.tbl",          // no path: no file
      "table": "kanji", "overlay": {…} }             //   its id, and all of it
  ]
}
```

A block's configuration is the `@block` line of the script grammar, so one
spelling covers the project file, a script and a file's session reading. `parent` is an
index into `entries`, and so is `folder` on a block, bookmark or folder that
sits in one; a reference that is not a folder of the same file, or that loops,
leaves the row directly under its file, and the loaded list is put in tree
order. An older build drops a folder record with a notice and shows its rows
directly under their file. A `session` holds only what is not at its default, and its
`view` names the open tab: `raw` (Hex, the default), `text` or `strings`.
`preview_scheme` is a file's Compression pick — a scheme's id, or `""` for none;
absent is automatic, and `""` is written because a preview switched off is a
choice and not the default.

Every string is written with its original (`o`) and the checksum of the bytes
it was read from (`h`, a CRC-32 of the string's bits in eight hex digits), plus
a status and notes where they are not the defaults; translations are not
stored, being the ROM's bytes. *Edited* is the string's bytes no longer giving
that checksum; an original saved without one goes by its text until its bytes
say it again.
A block that was loaded but never opened keeps its own in
`Entry.pending_strings` until an extraction adopts them — so a save writes back
the state of every block, not only the ones that were looked at. A translation
(`t`) an older project still holds is put into the bytes by the first
extraction, and written out again as it came until then.

Version 2 hides a fixed string's end token (§4.1). Its migration marks each
block whose strings stop at one with `"fixed_ends_shown": true`
(`Entry.fixed_ends_shown`), since respelling the saved originals and
translations takes the block's tables: its first extraction does it
(`respell_fixed_end`), and a save before then writes the mark again.

### 6.4 Exchange

`project/exchange/` holds the Cartographer and Atlas importers and exporters.
They build or consume `BlockConfig` and `StringRecord` values and never touch
the UI; the mapping tables in [script-format.md](script-format.md) are their
specification. `addresses.shift_config` is the one place both formats meet:
they address the *file*, a block addresses the container's payload, so an
import subtracts the header and an export adds it back. The text formats
themselves — the script, table and translator grammars — are `project/formats/`'s
(§6.2).

## 7. The UI layer

### 7.1 Composition

`ui/main_window/window.py` builds `MainWindow` from mixins, one per concern,
with `QMainWindow` last, as celPix does. Mixins reach each other only through
`self`. Nothing outside `main_window/` *changes* the model: the three workspace
docks read it through `ui/panel.py`'s `WorkspaceTreePanel` — which owns their
subscription and their row-to-entry lookup — and report intent by signal; every
other widget outside is handed values and knows nothing of a workspace.

Two things are deliberately outside that rule. The **Table Editor** edits the
one `Table` the window has no other handle on, in place, and hands back the
snapshot of it taken before the edit, so the change is still the before/after
pair an undo step is made of. **Undo commands** (`ui/undo_commands.py`) stamp
the workspace directly, since they are the window's own and are reached only
through `_push_command`.

| Concern | Modules in `ui/main_window/` |
|---|---|
| Shell | `window.py` (widgets, docks, the undo stack and its guards, the title and dirty marker, file dialogs, alerts), `menus.py` (the menu bar) |
| Active entry and refresh | `session.py`, `refresh.py`, `capability_sync.py` |
| Text view | `text_view.py` (the Text tab's window, and moving it by lines) |
| Interpretation and position | `format_bar.py` (the Format and Reading bars, the reading of the entry on screen, the encodings as tables), `navigation.py`, `history.py` |
| Entries and disk | `opening.py`, `entries.py`, `files_menu.py`, `entry_clipboard.py`, `containers.py`, `writing.py`, `compression.py`, `plugins.py` |
| Tables | `table_files.py`, `table_editor.py` |
| Raw view | `raw_view.py` |
| Blocks and strings | `blocks.py`, `strings_view.py`, `string_edit.py`, `wrap.py`, `find_replace.py`, `project_strings.py` (the Project Strings window), `glossary.py` (the Glossary window and its undo steps) |
| Search | `search.py`, `relative_search.py`, `pointers.py` |
| Exchange | `import_export.py` |
| Projects | `projects.py`, `relocate.py`, `autosave.py` |
| Preview | `preview.py`, `hex_view.py` |

Widgets outside the mixins: `reading_bar.py` (the Reading bar, loaded from and
read back as a `BlockConfig`), `pointer_tokens.py` (pointers in view as tokens
the Hex and Text tabs place), `raw_widget.py` (the two-column byte view),
`text_widget.py` (the plain-text display), `strings_view.py` (the string grid),
`string_pane.py` (the pane under it, on the selected string), `code_editor.py`
(the translation editor both open, with its code completion), `table_entry_form.py` (the Table Editor's entry form:
one entry as pickers and fields, and as the line that spells it), the panels (`files_panel.py`,
`hex_panel.py`), the tool windows, and the dialogs.

What more than one of them needs lives in small modules: `ui/widgets.py`
(`ResultsTable`, the `CancellableRun` run/stop/progress mixin for a tool window
and `ModalProgress` for a menu row, `fill_pick` and `select_data` for combos,
`setting_toggle` and `apply_wrap`, the remembered checkbox and the wrap mode
the Text tab and the strings pane share,
`CompactComboBox`, the fixed-width picker of the bars whose open list
widens to its longest item, `WrapBar`, a wrapping bar of labelled controls in
optional framed sections, `ModeToggle`, side-by-side buttons one of which
is down, and `carry_undo`, which gives a tool window Undo and Redo and keeps
its fields from spending their keys on their own typing history),
`ui/panel.py` (`WorkspaceTreePanel`, which owns a
dock's workspace subscription and its row-to-entry lookup), `ui/window_layout.py`
(`WindowLayout` and `remember_layout`), `ui/find_row.py` (`FindRow`, the find
field that steps to the next match on Enter and the previous on Shift+Enter),
`ui/number_fields.py` (`AddressSpelling`, `HexEdit`, `AddressEdit` and the spin
boxes sized to what they hold), `ui/marks.py` (the chip, tick, rule and notch
the byte views and the legend both paint), `ui/token_text.py` (what a token
covers and how it reads on one line, and script text with its codes left out,
with no Qt), `ui/alphabets.py` (the canned
runs of characters a fill offers), `ui/preview_font.py` (`PreviewFont`, the
app's one system font and the `Font` values it measures),
`ui/preview_render.py` (a laid-out page drawn into an image), `ui/font_tab.py`
(`FontTab`, which picks that font), `ui/entry_tree.py` (the Files tree's drags and keys),
`ui/entry_text.py` (what a Files row says, with no Qt), `ui/skips_picker.py`
(the Reading bar's skip ranges and their popup), `ui/writing_picker.py` (its
write settings and theirs), `ui/table_dialogs.py` (Shift
Keys and Fill), `ui/entry_rows.py` (a code's operand rows and a switch's
parameter rows), `ui/help_dialogs.py` (the live shortcut
list and the legend) and `ui/__init__.py` (the `settings()` accessor, the
`setting_bool`/`set_setting_bool` pair every stored switch is read through, and
the view constants `BYTES_PER_ROW` and `DUMP_WINDOW_BYTES`).

### 7.2 Where UI state lives

| State | Home |
|---|---|
| View offset, selection, current view tab | the window, live, and re-read from its widgets on every refresh |
| View bounds (the stretch the Hex and Text tabs are confined to) | the window, live; re-derived from a block's source on every activation, so never saved |
| View offset, view tab, a file's reading, Follow pointers and its Compression pick, per entry | `Entry.session`, captured when leaving an entry and saved with the project |
| Container, compression, block configuration, box | the `Entry` |
| The glossary | the `Workspace`, swapped with the entries when a project opens |
| Bytes, table set, strings, notices | the `Document` |
| Address format, last folder used, Follow selection, theme, preview font, window layouts, recent projects, and each tool surface's own view toggles | `QSettings` |
| Undo history, visit trail | the window, for the session |

`SessionMixin._activate_entry` is the single funnel for switching entries:
capture the outgoing session, load the incoming document, restore widgets with
signals blocked, refresh once. Re-activating the entry already on screen is a
no-op, and the two kinds that never become the view — a table, which opens in
the Table Editor, and a bookmark, which jumps the entry owning its bytes — take
their own route out and never claim `workspace.current`.

### 7.3 The refresh cycle

`RefreshMixin._refresh_view()` is the single choke point after any change:

1. **Settle** — clamp the offset; resolve the table set from the start
   table; re-extract the block when its config, table set or buffer
   revision changed (extraction is memoised on those three).
2. **Render** — Hex: decode the visible window and build the aligned
   hex/text rows; Text: the same decode as plain text, while its tab is open;
   Strings: refresh the rows whose records changed.
3. **Push** to the widgets.

A refresh that only **moved** the view — a scroll, a step, a change of
bounds — passes `moved=True` and leaves the Strings grid alone unless the move
re-read the block: the grid shows the same strings wherever the view is, and
filling it is the one part of a refresh that costs by the string. A move made
by **dragging** the scrollbar also passes `live=True`, which renders the tab on
screen and nothing else: the other tab, the side panels and the title's unsaved
marker — answering which serialises the whole project — wait for the drag to
come to rest, a timer's `DRAG_REST_MS` after its last move or the moment the
handle is let go (`_on_drag_rest`). A drag that pauses mid-gesture has not come
to rest. The Text tab
keeps its decode between windows (`TextDecode`, in `pipeline/text_view.py`):
a window, and the text above
it that a step up lays out, are served from the tokens already decoded wherever
they reach, and only what lies past them is decoded, from the last token
boundary the decoder can be trusted to have read whole. The raw view lays its hex pairs and token texts out once per face
(`QStaticText`) and places them; the Hex panel rebuilds its text only when the
bytes, the window or the address column changed.
4. **Sync dependent surfaces** — the table picks, Block bar, Hex panel, Preview,
   Search results, the window title. Each is synced here and nowhere else,
   unless it sits on a path that never reaches a refresh — a single string row
   updated in place is the one that does.
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
silently gating nothing. The three capabilities whose surface is a *tool window* —
`WRAP`, `COMPRESSION_SCAN`, `TABLE_EDIT` — are asked by the mixin
that drives that window instead, since each window already decides its own
enablement from its own state. (`EntryKind` lives in `core/capabilities.py`
because it keys that table and `core/` is the bottom layer;
`project/workspace.py` imports it from there.)

Undo is one `QUndoStack` for the session, and `ui/undo_commands.py` states three
invariants once, in `_StateCommand`:

- **The guard.** Every apply runs inside the window's `_undo_apply()` context,
  and `_push_command` refuses to push while it is set, so an apply can never
  push a second command. A run of pushes that is one gesture is grouped by the
  window's `_macro()` rather than by `beginMacro` directly: it honours the same
  guard, and it defers `beginMacro` until the first push inside it lands, so a
  run that changed nothing leaves no step that undoes nothing.
- **Reach.** `_CurrentEntryCommand` switches back to the entry a change was made
  in; `_EditContextCommand` also returns to the view and the row or offset it
  was made at; `_InPlaceCommand` reaches nothing, because a rename, a reorder
  or a write shows wherever you are.
- **Revision tokens.** An entry is unsaved when its live revision differs from
  the saved one, so every command over bytes or records carries the revision on
  both sides of its pair: undo restores the token the entry had before, and an
  undo back to what was written reads clean again. A write carries both tokens
  of every entry it saved, so undoing it makes them unsaved by the pair they had
  before.

A write's step (`WriteCommand`) holds, per side, the file's buffer, each written
block's buffer, and a `FileChange` per file on disk: the run
of bytes that differed and the file's size, from `pipeline/filechange.py`. Applying a side
reads the file then and moves it only while it still holds the other side; the
disk is written by the write itself, so the command's first redo finds its
files already there and lands the in-memory half alone.

A string edit merges only within a **typing run** — the window bumps
`_edit_run` when the selection or the entry moves — and a run that ends back
where it began obsoletes its step and hands the earlier revision back to every
entry the run stamped. View
moves in one entry merge the same way.

The project's dirty state is the other kind: a serialised comparison against
what was saved, shown through Qt's `[*]` placeholder and `setWindowModified`,
and deferred over an undo push so one push re-serialises the project once
rather than at each choke point it passes — and over a whole macro, so a paste
of hundreds of rows re-serialises it once too.

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
every address the UI shows or reads — the navigation bar's offset box, Go to
Address, the Hex dock, the Reading bar and the raw view's address column —
through the window's one `AddressSpelling` (`ui/number_fields.py`), and is
remembered per machine.

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
  `make_sample_projects.py`, `subset_icon_font.py` and `ui_screenshots.py`.
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
