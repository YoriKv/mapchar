# abcde::Cartographer

`../abcde/abcde/Cartographer.pm` dumps text from a ROM according to a
Cartographer command file. Cartographer PR3 (RedComet, 2008) is the tool it
emulates; its manual is `../abcde/docs/Cartographer/readme.txt`. Translation
itself is [bin2text](bin2text.md).

```
perl abcde.pl [general options] -cm abcde::Cartographer <ROM> <CommandFile> <OutputBase> -s|-m
```

- `-s` writes every block to `<OutputBase>.txt`; `-m` writes one
  `<OutputBase>_NNN.txt` per block (3-digit block number, widening as needed).
- The mode is forced to `bin2text`, and `errorEOF` is set (see
  [bin2text.md](bin2text.md#loop) step 1).
- Each block prints `Dumping BLOCK [<name>]...` to stdout.

## Command file parsing

Each line goes through these steps:

1. Decode as strict UTF-8, NFD-normalise, strip a BOM on line 1, strip the line
   break.
2. Delete `\s*//.*` anywhere in the line, so `//` inside a value is cut too.
3. Skip blank lines.
4. Match `^#<COMMAND>(?::\s*(.*))?$` against the known command names, or die.
   Whitespace after the colon is dropped; trailing whitespace is kept and makes
   anchored numeric values fail.
5. Check the value against the command's pattern. The enumerations (`Yes|No`,
   `NORMAL|...`) are unanchored, so `Yesterday` passes validation but does not
   compare equal to `Yes` later.
6. A command repeated in a block is fatal, except `#SUB TABLE`. `#SUB TABLE` is
   file-level: it may appear anywhere and does not belong to a block.
7. Exactly `#END BLOCK` closes a block, which is then:
   1. Given the previous block's `GAME NAME` if it has none.
   2. Checked for required, dependent and forbidden commands (table below). The
      check runs before defaults are applied, so a defaulted command such as
      `ATLAS PTRS` still has to be written when a method requires it.
   3. Given its defaults, plus `STRING END REALIGN OFFSET = 0`.
   4. Resolved: `TABLE` becomes an absolute path under `-brp` (default: the
      current directory). An absolute `#TABLE` path breaks, because `catdir`
      prefixes the base.
   5. Converted: numeric values become integers and are multiplied by 8 into
      bits, except `STRINGS PER POINTER`.
8. At end of file: no blocks, or an unterminated final block, is fatal.

Every block must be valid before any table is loaded or anything is dumped.

Number syntax:

| Name              | Pattern                                    |
|-------------------|--------------------------------------------|
| `dec_or_hex`      | `-?[0-9]+`, `-?\$hex` or `\$-hex`          |
| `non_negative`    | `[0-9]+` or `\$hex`                        |
| `positive`        | non-negative and non-zero                  |

Hex is written with `$`, not `0x`.

## Commands

| Command                          | Value          | Default  | Meaning                                                                       |
|----------------------------------|----------------|----------|-------------------------------------------------------------------------------|
| `GAME NAME`                      | any            | previous block | Printed once per output file                                            |
| `BLOCK NAME`                     | any            | required | Printed per block                                                             |
| `TYPE`                           | `NORMAL`, `FIXED_STRING`, `FIXED_STRING && FIXED_LINE` | required | See [Strings](#strings)                   |
| `STRING LENGTH`                  | positive       |          | Bytes per fixed string                                                        |
| `STRING END`                     | `Yes`/`No`     |          | Append `END CTRL` to fixed strings                                            |
| `END CTRL`                       | any            | `(END)`  | Artificial end marker                                                         |
| `LINE LENGTH`                    | positive       |          | Bytes per output line in `FIXED_LINE`                                         |
| `LINE END`                       | `Yes`/`No`     |          | Append `LINE CTRL` to lines                                                   |
| `LINE CTRL`                      | any            | `(LINE)` | Artificial line marker                                                        |
| `METHOD`                         | `POINTER`, `POINTER_RELATIVE`, `POINTER_RELATIVE_PC`, `RAW` | required | See [Processing](#processing)            |
| `POINTER ENDIAN`                 | `BIG`/`LITTLE` |          |                                                                               |
| `POINTER TABLE START`            | non-negative   |          | First pointer's byte address                                                  |
| `POINTER TABLE STOP`             | non-negative   |          | Exclusive: pointers are read while address < stop                             |
| `POINTER SIZE`                   | positive       |          | Bytes                                                                         |
| `POINTER SPACE`                  | non-negative   |          | Bytes skipped after each pointer                                              |
| `ATLAS PTRS`                     | `Yes`/`No`     | `No`     | Emit Atlas commands                                                           |
| `BASE POINTER`                   | dec_or_hex     |          | Added to pointer values (may be negative)                                     |
| `RELATIVE PC`                    | any            |          | Accepted and ignored                                                          |
| `SCRIPT START`                   | non-negative   |          | `RAW` start byte                                                              |
| `SCRIPT STOP`                    | non-negative   |          | Exclusive end byte: `RAW` range, and an upper bound for pointer strings       |
| `TABLE`                          | path           | required | Start table: the first table in the file                                      |
| `TABLE ID`                       | id             |          | Start table by `@id`; `TABLE` is still required                               |
| `SUB TABLE`                      | path           |          | Extra table file, file-level, repeatable                                      |
| `COMMENTS`                       | `Yes`/`No`/`Both` | required | `//` prefixing; see [Output](#output)                                     |
| `END BLOCK`                      | none           |          | Closes the block                                                              |
| `STRINGS PER POINTER`            | non-negative   | `1`      | End tokens per pointer                                                        |
| `STRING END REALIGN MULTIPLE`    | positive       |          | Realign after end tokens (bytes)                                              |
| `STRING END REALIGN OFFSET`      | non-negative   | `0`      |                                                                               |
| `AUTO JUMP START` / `AUTO JUMP STOP` | non-negative |        | Reading byte START continues at byte STOP; each requires the other           |
| `SHOW END ADDRESS`               | `Yes`/`No`     | `Yes`    | `// current address` after each string                                        |
| `STRINGS END AT NEXT POINTER`    | `Yes`/`No`     | `No`     | End each string at the next pointer's target instead of an end token          |
| `SORT OUTPUT BY STRING ADDRESS`  | `Yes`/`No`     | `No`     | Order strings by target                                                       |
| `TRIM TRAILING NEWLINES`         | `Yes`/`No`     | `Yes`    | Strip trailing line breaks from each string                                   |

Dependencies:

| When                           | Required                                        | Forbidden                                 |
|--------------------------------|-------------------------------------------------|-------------------------------------------|
| `TYPE: FIXED_STRING`           | STRING LENGTH, STRING END                       | LINE LENGTH, LINE END                     |
| `TYPE: FIXED_STRING && FIXED_LINE` | STRING LENGTH, STRING END, LINE LENGTH, LINE END |                                     |
| `TYPE: NORMAL`                 |                                                 | STRING LENGTH, STRING END, LINE LENGTH, LINE END |
| `METHOD: POINTER`              | POINTER TABLE START/STOP, POINTER SIZE/SPACE/ENDIAN, ATLAS PTRS | BASE POINTER, SCRIPT START |
| `METHOD: POINTER_RELATIVE[_PC]` | the `POINTER` set + BASE POINTER              | SCRIPT START                              |
| `METHOD: RAW`                  | SCRIPT START, SCRIPT STOP                       | all pointer commands, BASE POINTER, STRINGS PER POINTER |

## Processing

1. **Load tables.** Every `TABLE`/`SUB TABLE` file is loaded, sorted by path,
   unless already loaded with `-t`. All tables are finalised. `AtlasID` is
   `Table_<index in load order>`.
2. **Read the ROM** and convert it to bits.
3. **Pointers**, per block:
   - `RAW`: one pseudo-pointer targeting `SCRIPT START`.
   - Otherwise, for `a = START; a < STOP; a += SIZE + SPACE`:
     1. Read `SIZE` bytes at `a`, byte-reversed when `LITTLE`.
     2. `target = value × 8`, plus `BASE POINTER` (`POINTER_RELATIVE`), or plus
        `BASE POINTER + a` (`POINTER_RELATIVE_PC`, which replaces
        Cartographer's broken `RELATIVE PC`).
     3. A pointer straddling `STOP` is still read.
   - `SORT OUTPUT BY STRING ADDRESS` sorts with a **string** comparison of the
     bit addresses, so addresses with different digit counts misorder
     (*observed*: `$100` before `$64`). Pointer numbers keep their table
     positions.
4. **String setup**, per pointer (`setupString`):
   - Start table: `TABLE ID`, or the first table of `TABLE`.
   - Auto-jumps, realignment, and `endAddress = SCRIPT STOP`.
   - Pointer methods add end-token termination and `STRINGS PER POINTER`, and
     start at the target.
   - `STRINGS END AT NEXT POINTER`, when a next pointer exists in the
     (possibly sorted) order: no end-token termination, and
     `endAddress = min(SCRIPT STOP, next target)`. The last pointer still ends
     at an end token. Adjacent duplicate pointers give empty strings.
   - With no `endAddress` and no end token reachable from the start table
     through switches, warn that extraction runs to end of file.
   - `RAW` blocks never stop at end tokens and never realign. Unlike
     Cartographer PR3, they do not drop text around end tokens.

## Strings

- `NORMAL`: one parse from start to end token or `endAddress`.
- `FIXED_STRING`: `endAddress = start + STRING LENGTH`, capped by
  `SCRIPT STOP`. Pointer methods still stop early at end tokens.
- `FIXED_STRING && FIXED_LINE`: repeatedly parse
  `[s, min(s + LINE LENGTH, start + STRING LENGTH, SCRIPT STOP))`, joining the
  pieces with `LINE CTRL\n` when `LINE END` is `Yes`, else `\n`.
- A token crossing a length boundary is cut at the boundary; the next piece
  starts inside it. Cartographer PR3 instead dumps the whole token and then
  re-reads its tail.
- `STRING END: Yes` appends `END CTRL\n\n` to fixed strings.

## Output

Observed layout for a `POINTER_RELATIVE` block with `ATLAS PTRS: Yes` and
`COMMENTS: Both`:

```
//GAME NAME:		Synthetic

// Define required TABLE variables and load the corresponding tables
#VAR(Table_0, TABLE)
#ADDTBL("/absolute/path/main.tbl", Table_0)

//BLOCK #000 NAME:		Pointers
#ACTIVETBL(Table_0) // Activate this block's starting TABLE

#JMP($100, $120) // Jump to insertion point
#HDR($80) // Difference between ROM and RAM addresses for pointer value calculations

//POINTER #0 @ $10 - STRING #0 @ $11A
#W16($10)
//THIRD.[end]
THIRD.[end]
// current address: $121
```

- **File header**: `//GAME NAME:\t\t<name>`, once for `-s` or per file for
  `-m`. If any block has `ATLAS PTRS: Yes`, a `#VAR`/`#ADDTBL` pair follows for
  every loaded table, with absolute paths; a multi-table file repeats its path.
- **Block header**: `\n//BLOCK #NNN NAME:\t\t<name>`.
- **Atlas block prelude** (`ATLAS PTRS: Yes`), printed after all of the block's
  strings are generated but before them:
  - `#ACTIVETBL(Table_N)`, or `#ACTIVETBL(@id)` with `TABLE ID`.
  - `#JMP($min start, $max end − 1)`.
  - `#HDR($BASE)` when `BASE POINTER` is set. Negative values print as `$-HEX`,
    and 0 prints as `$-0`.
- **String header**:
  - pointer: `\n//POINTER #n @ $addr - STRING #n @ $target\n`, plus
    `#W<SIZE×8>($addr)\n` with `ATLAS PTRS`.
  - RAW: `\n//Block Range: $START - $STOP\n`.
  - embedded pointer: `\n//EMBEDDED POINTER #n @ $addr - SUB-STRING #n @ $target\n`,
    plus `#EMBWRITE(n)\n` with `ATLAS PTRS`.
- **Body**, by `COMMENTS`:

  | Value  | Body                                                                         |
  |--------|------------------------------------------------------------------------------|
  | `No`   | the text                                                                     |
  | `Yes`  | the text with `//` at the start and after every newline                      |
  | `Both` | for each sub-string, split after end tokens whose text ends in `\n`: the commented copy (trailing newlines trimmed when `TRIM TRAILING NEWLINES`), a newline, then the plain copy |

  `-g` replaces the text with per-token debug lines.
- **Footer**: trailing newlines are trimmed if configured; then, with
  `SHOW END ADDRESS`, `\n// current address: $END\n`, where END is the bit
  position after the string, truncated to a byte.
- The pointer's whole output is NFC-normalised. Embedded-pointer strings follow
  their parent.

## Embedded pointers

Override `generateOutput` in a subclass and pass the subclass to `-cm`:

```
generateOutput(block, string, pointer_address, \$embedded_pointer_num)
  -> (output_text, [ { address => byte, pointer_num => n, string => BasicString }, ... ])
```

`extract` appends the extraction of every returned embedded pointer after the
parent string, recursively, using the embedded-pointer header.

`Cartographer/NES/Battle_of_Olympus.pm` is the shipped example. Its script data
is function indexes with parameters; text is a Pascal string of 5-bit tokens.
The subclass works as follows:

- It drops zero-width tokens and walks the rest, tracking the current bit
  address.
- It looks at tokens from the table with ID `functions`:
  - `03` (read string at address): reads the next two tokens as a
    little-endian address plus `$C010`. It queues a sub-string in
    `@OneBytePascalString` and emits `#SKIP(-2)`, `#EMBTYPE("LINEAR", 16, $0)`,
    `#EMBSET(n)`.
  - `01`, `07`, `0C` (one flag or amount parameter, then a byte count) and
    `02`, `0A`, `0B` (a byte count): the target is the count token's address
    plus the count. It queues a sub-string in the block table and emits
    `#SKIP(-1)`, `#EMBTYPE("POINTER_RELATIVE", 8, $0)`, `#EMBSET(n)`.
  - `09` (dialogue): appends the raw text `<$09>` and `#ACTIVETBL(upper)`. The
    Atlas side then writes the Pascal string with `#STRTYPE("PASCAL")` and
    `#PASCALTYPE("TOKENS")`, instead of searching for length-prefix tokens.
- Sub-string settings are copied from the parent string.

The example also shows how table syntax expresses a token-counted Pascal
string: `/!NN=,<@upper>:NN` for every length NN, with `<2>` weights on the
10-bit tokens.

## Defects

- `SORT OUTPUT BY STRING ADDRESS` uses a string comparison (*observed*).
- A read window crossing end of file is fatal, even at a legitimate end
  (*observed*).
- Absolute `#TABLE` paths break, and the module's own help text wrongly says
  relative paths are anchored at the command file. The emitted absolute
  `#ADDTBL` paths are not loadable by `abcde::Atlas`, which joins them onto
  its base path (*observed*).
- `//` is stripped inside values.
- Duplicate pointers are dumped repeatedly. Re-inserting the dump writes the
  text once per pointer (*observed*: the block overflowed its `#JMP` bound).
- The `TABLE ID` error message prints a literal `#block_num`.

## Replication notes

- A block is `(address source → list of (pointer address, target bit),
  string end rule, start table, output template)`.
- Emit one canonical output format that the inserter reads directly, keeping
  pointer address, target, end address and table.
- Sort numerically. Deduplicate identical targets, keeping all pointer
  addresses for re-insertion.
- Report an end-of-file hit as a string end, not a crash.
