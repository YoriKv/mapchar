# abcde::Atlas

`../abcde/abcde/Atlas.pm` inserts text and writes pointers according to an
Atlas script. Atlas 1.11 (Klarth, 2010) is the tool it emulates; its manual is
`../abcde/docs/Atlas/Atlas.pdf`. Encoding itself is [text2bin](text2bin.md).

```
perl abcde.pl [general options] -cm abcde::Atlas [-d <file>] <Target> <Script> [<Script> ...]
```

- The mode is forced to `text2bin`.
- `-d` opens its file (or accepts `stdout`) but writes nothing.
- `<Target>` must already exist; it is opened read/write twice, as the script
  handle and the pointer handle.
- Scripts are processed in order. Unless the previous script ran
  `#CONTINUOUSFILE("TRUE")`, each script starts from default settings, a fresh
  variable set (only `CURRENT_ADDRESS`) and freshly opened handles. Loaded
  tables persist across scripts.
- Relative paths in `#ADDTBL`, `#SETTARGETFILE` and `#SETPTRFILE` are resolved
  against `-brp` (default: the current directory; `*` means the script's
  directory). Absolute paths break, because they are appended to the base.
  `#PTRLIST` files are opened relative to the process's working directory.

## Script parsing

Each script is read twice with the same line handling:

1. Decode as strict UTF-8 and NFD-normalise; strip a BOM on line 1.
2. Skip lines matching `^[ \t]*//`; strip the line break; skip empty lines.
3. A line starting with optional spaces/tabs and then `#` is a command. It is
   trimmed and must match `#NAME(`, with NAME a known command, and then one of
   that command's signatures, anchored as `^#NAME\(signature\)([ \t]*//.*)?$`.
   Anything else is fatal.
4. Every other line is text, kept verbatim including leading and trailing
   whitespace, and appended to the text buffer without its line break.

- **Pass 1** runs only `#VAR` and `#ADDTBL`, then finalises all tables.
- **Pass 2** runs everything else. Before each command, the text buffer is
  flushed (encoded and written), so every command ends the current string.
  Comment and blank lines do not flush.
- **End of script**: flush the buffer; print the space-left line if the last
  `#JMP` had a bound; warn about embedded pointers missing an `#EMBSET` or an
  `#EMBWRITE`; close the handles.

`#JMP` bound report, printed on the next `#JMP` and at end of script:
`#JMP bounded by $MAX has N ($HEX) space left.` N is negative when strings were
truncated.

## Values

- **Numbers**: decimal, `$hex`, `$-hex` or `-$hex`, case-insensitive. A
  `CALCVAR` name is accepted wherever the signature says number, but only
  handlers that call `parse_number` resolve it (see
  [Defects](#defects)).
- **Strings** are written in straight double quotes.
- **Address types**: `LINEAR`, `LOROM00`, `LOROM80`, `HIROM`, `GB`,
  `POINTER_RELATIVE`.
- **Settings defaults**:

  | Setting        | Default      |
  |----------------|--------------|
  | address type   | `LINEAR`     |
  | endianness     | little       |
  | header         | 0            |
  | string type    | `ENDTERM`    |
  | Pascal length  | 1            |
  | Pascal type    | `BYTES`      |
  | insert address | undefined    |

## Commands

### General

| Command | Behaviour |
|---------|-----------|
| `VAR(name, type)` | Declares `CALCVAR`, `COUNTER`, `CUSTOMPOINTER`, `POINTERTABLE`, `POINTERLIST`, `EMBPOINTERTABLE`, `TABLE` or `EXTENSION`. Redeclaring is fatal. Later commands check type and initialisation state. |
| `ADDTBL("file", tablevar)` | Loads a table file (once per path) and binds its first logical table. |
| `ACTIVETBL(tablevar)`, `ACTIVETBL(@id)` | Sets the table used to encode text. |
| `JMP(addr)`, `JMP(addr, max)` | Sets the insert position and the inclusive upper bound (none without `max`). |
| `HDR(n)` | Header size, subtracted in pointer calculations and `STRINGALIGN`. |
| `SKIP(n)` | Moves the insert position by `n`. |
| `SETTARGETFILE("file")` | Reopens the script handle; the position becomes undefined, so a `JMP` must follow. |
| `SETPTRFILE("file")` | Reopens the pointer handle. |
| `CONTINUOUSFILE("TRUE"\|"FALSE")` | Carries state into the next script; resets itself when that script starts. |
| `LOADEXT`, `EXECEXT`, `AUTOEXEC`, `DISABLE("Func", "tag")` | Warning only; extensions are not run. |

### Strings

| Command | Behaviour |
|---------|-----------|
| `STRTYPE("ENDTERM"\|"PASCAL")` | Selects the string type. |
| `PASCALLEN(1-4)` | Width of the Pascal length prefix, in bytes. |
| `PASCALTYPE("BYTES"\|"TOKENS")` | Length prefix counts bytes, or the sum of token weights. |
| `FIXEDLENGTH(len, fill)` | Pads or truncates each string to `len` bytes, Pascal prefix included; `fill` is 0–255 or `$XX`; `len` 0 disables. |
| `STRINGALIGN(n)` | Advances each string's start to `(addr − HDR) ≡ 0 (mod n)`; skipped bytes are not written. |

### Pointers

| Command | Behaviour |
|---------|-----------|
| `ENDIANSWAP("TRUE"\|"FALSE")` | Big or little endian for pointers, counters and Pascal lengths. |
| `SMA("type")` | Address type for low-level writes without a custom pointer. |
| `CREATEPTR(ptr, "type", offset, bits)` | Custom pointer; `bits` is 8, 16, 24 or 32. |
| `WRITE(ptr, addr)` | Writes a pointer to the current insert position at `addr`. |
| `PTRTBL(tbl, start, step, ptr)`, `WRITE(tbl[, index])` | Writes to `start + step × index`; an explicit index sets the index; the index then increments. |
| `PTRLIST(list, "file", ptr)`, `WRITE(list)` | The file holds one decimal or `$hex` address per line; writes to the next address; writing past the end is fatal. |
| `AUTOWRITE(tbl\|list, "EndTag")` | Writes a pointer at every end token whose text equals `EndTag` (see [the pipeline](#string-write-pipeline)); duplicates are ignored with a warning. |
| `DISABLE(tbl\|list, "EndTag")` | Stops that autowrite; fatal if it is not active. |
| `W8`, `WLB` | Writes bits 0–7 (1 byte). |
| `W16` | Writes bits 0–15 (2 bytes). |
| `W24` | Writes bits 0–23 (3 bytes). |
| `W32` | Writes bits 0–31 (4 bytes). |
| `WHB` | Writes bits 8–15 (1 byte). |
| `WBB` | Writes bits 16–23 (1 byte). |
| `WUB` | Writes bits 24–31 (1 byte). |
| `WHW` | Writes bits 16–31 (2 bytes). |

Every low-level write has the form `Wxx(addr)` or `Wxx(var, addr)`, where `var`
is a `CUSTOMPOINTER`, a `COUNTER` or a `CALCVAR`. A `COUNTER` or `CALCVAR`
writes its value, not an address.

### Embedded pointers

| Command | Behaviour |
|---------|-----------|
| `EMBTYPE("type", bits, offset)` | Settings for embedded pointers not yet used. Each pointer keeps the settings current at its first `EMBSET`/`EMBWRITE`. |
| `EMBSET(n)` | Records the current position as pointer `n`'s location and skips `bits/8` bytes. Writes the pointer if `EMBWRITE(n)` already happened. |
| `EMBWRITE(n)` | Records the current position as pointer `n`'s target. Writes the pointer if `EMBSET(n)` already happened. |
| `EMBPTRTBL(tbl, count, ptr)` | Reserves `count × bits/8` bytes at the current position. |
| `WRITE(tbl[, index])` | Writes into that table, bounds-checked. |

Embedded pointers are written through the script handle, not the pointer
handle.

### abcde additions

| Command | Behaviour |
|---------|-----------|
| `AUTOCMD(addr, #COMMAND(...))` | Queues a command to run when a string write reaches `addr` (see step 9 below). Repeatable; runs in order. |
| `CALC(var, "expr")` | See below. `CURRENT_ADDRESS` is predefined and read-only. |
| `READ(var, addr)` | Reads one byte of the target file. |
| `PRINT(var, bits)` | Appends the low `bits` of `var`, MSB first, to the text buffer as `<%0>`/`<%1>` raw tokens. |
| `SKIP(n)` | See General. |
| `W8` | See Pointers. |
| `POINTER_RELATIVE` | Address type: target minus pointer address. |
| `CREATECTR(ctr, bits, value)`, `INC(ctr, n)` | Deprecated counters; `INC` wraps at `bits`. |
| `ACTIVETBL(@id)` | See General. |

`CALC` evaluates `expr` as follows:

1. Each `!name` is replaced by the value of that defined `CALCVAR`.
2. `$hex` and `%bin` literals become decimal.
3. The result is evaluated by Perl `eval`, restricted by a character whitelist
   to digits, spaces, `+ - * / & | ^ ( ) ? : < > =`.

Perl semantics apply: `/` is floating point, and results written to the file
are truncated by `%X` formatting (*observed*: `7 / 2` writes `03`).

## String write pipeline

`write_text_buffer` runs when the buffer is flushed:

- It fails without an active table.
- It joins the buffered lines with no separator.
- With `FIXEDLENGTH` and `--artificial-end-token`, it splits the text on that
  label and removes the label. With `FIXEDLENGTH` and no label, it warns and
  uses the whole buffer.
- Each piece goes through `text2bin` from the active table, then
  `write_string`.

Text2bin state does not carry across pieces or commands.

`write_string(tokens)`:

1. **Position**: fail if no `#JMP` has set the insert position.
2. **Bytes**: pack the token bits MSB-first, zero-padding the last byte.
3. **Maximum length**: `reserved` is the Pascal length for `PASCAL`, else 0.
   `maxLength = FIXEDLENGTH − reserved`.
4. **Autowrite**: walk the tokens, tracking a byte address from the insert
   position (bit tokens add fractions), and stop at the first token that would
   pass `maxLength`. At an end token whose text matches an `AUTOWRITE` tag,
   visit each registered variable in registration order:
   - Write a pointer at the variable's next slot (`start + step × index`, or
     the next list entry) through its custom pointer. It points to the start of
     this variable's current segment: the insert position for the first, or the
     byte after its previous end token.
   - Increment the variable's index.
5. **Fixed length**: truncate with a warning, or pad with the fill byte, to
   `maxLength`.
6. **Pascal**: prefix a length in `PASCALLEN` bytes and the current endianness.
   The length counts bytes after step 5, or the sum of all token weights with
   `TOKENS`. Overflow warns and keeps only `PASCALLEN` bytes. The whole buffer
   is one Pascal string, whatever end tokens it contains.
7. **Align**: apply `STRINGALIGN` by seeking forward.
8. **Bound**: if `start + length − 1 > JMP max`, warn, add the excess to the
   space report, and truncate. Otherwise reset the excess to 0.
9. **AUTOCMD**: for each queued address with `start ≤ addr < start + length`,
   in string-sorted order:
   1. Write the bytes that fit before `addr`.
   2. Run all commands queued for `addr`, in order. A `#JMP` moves the write
      position, and the rest of the string continues there.
   3. Remove `addr` from the queue.
10. **Write**: write the remaining bytes and advance the insert position.

## Pointer value

`write_pointer(pointerAddr, type, offset, shift, bits, ptrBits, target = insert position)`:

```
v = target − HDR
LOROM00:  bank = (v >> 16) & 0xFF; w = v & 0xFFFF
          v = bank·0x20000 + w + (w ≥ 0x8000 ? 0x10000 : 0x8000)
LOROM80:  LOROM00 + 0x800000
HIROM:    v + 0xC00000
GB:       bank = floor(v / 0x4000); v = v mod 0x4000; if bank: v += 0x4000 + bank·0x10000
POINTER_RELATIVE: v = target − pointerAddr
LINEAR:   v
v −= offset                        (positive offsets subtract, negative add)
if ptrBits ≥ 0: v &= 2^ptrBits − 1
v = (v >> shift) & (2^bits − 1)
write bits/8 bytes, little-endian unless ENDIANSWAP("TRUE")
```

`ptrBits` is the custom pointer's size, or −1 for `SMA`-based writes. The `GB`
formula places the bank number above bit 16: a 24-bit write includes it, a
16-bit write drops it.

## Differences from Atlas 1.11

Taken from the module's help text:

- Commands must use `(` and may only be followed by a comment.
- Nonsensical values and early variable-type misuse are fatal.
- One-character variable names and lowercase hex are allowed.
- Line breaks do not break tokenisation.
- `HDR` also affects `STRINGALIGN`, which therefore aligns ROM addresses.
- `FIXEDLENGTH`'s fill must be 8-bit. `PASCALLEN` defaults to 1.
  `ENDIANSWAP` also swaps Pascal lengths.
- `W16`/`W24`/`W32` accept custom pointers. `WHW` with a custom pointer works.
  `WRITE(table, index)` updates the index.
- Each of several `AUTOWRITE`s keeps its own pointer state. `AUTOWRITE` writes
  no pointer for data after the last end token. `AUTOWRITE` past the end of a
  list is fatal.
- An embedded pointer's type is fixed at first use.
- The commands are `EXECEXT`/`LOADEXT` (not `EXTEXEC`/`EXTLOAD`).
- The `GB` formula is corrected.
- Tables use the [abcde grammar](table-files.md). Atlas-only forms (`*FE`,
  hexless `/<END>`, `($XX)` raw input) are not accepted; the artificial end
  token option replaces `/<END>`.

## Defects

- **Unparsed numeric arguments.** `FIXEDLENGTH` length, `CREATECTR` size and
  value, `PRINT` size and the `EMBPTRTBL` reservation count use the raw
  argument, so `$hex` or `CALCVAR` values there silently become 0
  (*observed*: `#FIXEDLENGTH($4, 0)` truncates strings to nothing).
- **`AUTOCMD` order**: addresses are sorted as strings.
- **`WRITE` on an `EMBPOINTERTABLE`** uses slot address
  `start + (bits/8 + offset) × index`, wrongly including the pointer offset.
- **Address 0 counts as unset**: `EMBSET`/`EMBWRITE` treat an address of 0 as
  unset, and so does a `JMP` bound of 0.
- **Autowrite targets ignore the Pascal prefix and `STRINGALIGN`**, both applied
  after step 4.
- **Buffered handles**: the script and pointer handles are separate buffered
  handles on the same file. Overlapping writes resolve in flush order, and
  `READ` may not see unflushed pointer writes.

## Replication notes

- Model the inserter as a cursor over an output file, plus pointer writers that
  take `(location, target, address mapping, offset, width, bit shift,
  endianness)`.
- Resolve every numeric argument through one parser.
- Keep strings as the unit between commands. Apply Pascal/fixed-length/alignment
  before computing autowrite targets. Sort `AUTOCMD` addresses numerically.
- Pair the dump format with the inserter so a
  [Cartographer](cartographer.md) dump re-inserts without edits.
