# romjuice

romjuice v2.2 (prez, 2001) is a single-file C table-based script dumper: it
translates a byte range of a ROM into text using one table file, or two with
font-table swapping. It has no pointer support and no insertion side. The tool
lives outside this repository at `../romjuice/`:

| File            | What it is                                                              |
|-----------------|-------------------------------------------------------------------------|
| `romjuice.c`    | The whole program, 342 lines, "do whatever you like" licence            |
| `romjuice.exe`  | Win32 console build of `romjuice.c` (MSVC CRT, 49 152 bytes)            |
| `readme.txt`    | Author's manual; inaccurate in places (see [Manual errata](#manual-errata)) |
| `changes.txt`   | Changelog for 2.1 and 2.2                                               |

Everything below comes from the source and is confirmed by running both a
Linux build of `romjuice.c` and `romjuice.exe` (through WSL interop) on
synthetic inputs; the two agree except where
[Windows binary](#windows-binary) says otherwise.

## Command line

```
romjuice <rom> <table> <start> <end> <output> [-n] [-h <format>] [-t <table2>]
```

- `<start>`, `<end>`: hexadecimal file offsets parsed with `sscanf("%x")`
  (an `0x` prefix is accepted; parsing stops at the first non-hex character).
  `<end>` is inclusive. There is no header or address-mapping handling.
- `<output>` is created or truncated before the table is even read.
- Options are found by exact string comparison against **every** `argv`
  element, including `argv[0]` and the five positional arguments, so a file
  named `-n` switches commenting off. The element after `-h` or `-t` is taken as
  its value; a trailing `-h`/`-t` with no value crashes.
  - `-n`: commenting off (default on).
  - `-h <format>`: `printf` format used instead of `<$%02X>` for every
    hexadecimal output; it receives one `int`. `-h ""` suppresses hex output.
  - `-t <table2>`: second table for table swapping (see [Dump algorithm](#dump-algorithm)).
- Start-up order: banner to stdout; open ROM; open output; load table 1; parse
  addresses; scan options; load table 2; reject `start > end`.
- Every message, including errors, goes to stdout. Usage and error exits use
  `exit(printf(...))`, so the exit status is the length of the message.

## Table dialect

`fgets` reads lines of at most 999 bytes; longer lines split into several.
Each line is classified by its first character:

| Line form      | Meaning                                                                                   |
|----------------|-------------------------------------------------------------------------------------------|
| `!HEX`         | Swap trigger. The line must be only `!` and hex digits; anything after it changes the width. |
| `@HEX=N,BASE`  | Kanji array: `N` bytes follow the trigger, each looked up as a 2-byte value `byte + BASE`. |
| `$HEX=N`       | Linked hex: print the trigger and the `N` following bytes as hex.                         |
| `HEX=text`     | Normal entry.                                                                             |
| anything else  | Ignored (no `=`, or `=` in column 0). This is the only comment mechanism.                 |

Parsing rules:

- **Key value**: `sscanf("%x")` of the hex part, so at most 8 significant
  digits and parsing stops at the first non-hex character.
- **Key width** in bytes is `floor(digits / 2)`, counting leading zeros:
  `0041=` is a 2-byte entry, `041=` a 1-byte entry for `41`. Widths above 4 are
  stored but never match.
- **Counts** `N` use `sscanf("%i")`: decimal, `0x` hex, or octal when written
  with a leading `0` (`010` is 8). `BASE` uses `%x`.
- **Duplicates**: entries live in a linked list in file order and lookup is a
  linear scan for `(value, width)`, so the first entry wins.
- **Last-line truncation**: newline stripping uses a helper that returns 0 for
  "not found", so on a line without a trailing `\n` (a final line with no line
  break) it blanks index 0 of the value. A normal entry becomes an empty string
  that silently consumes its bytes; a `!` entry is lost.
- **CRLF**: the `\r` of a CRLF table stays at the end of every value on
  non-Windows builds and is printed. The Windows build reads tables in text mode
  and is unaffected.
- `while (!feof)` processes the final buffer twice; the duplicate entry is
  harmless.
- `!00` never swaps, because the swap flag is the key value itself.

Escapes in normal entry text, expanded while printing:

| Escape         | Output                                                         |
|----------------|----------------------------------------------------------------|
| `\n`           | newline, then `; ` when commenting is on                       |
| `\r`           | newline, never followed by `; `                                |
| `\\`           | `\`                                                            |
| `\` + other    | `\`; the following character prints normally                   |

There is no end-token concept. The intended idiom is to put the formatting into
the terminator's own entry, e.g. `FF=\n<end>\r\r`.

## Dump algorithm

Output begins with a fixed header, then `; ` if commenting is on:

```
; romjuice v2.2
; written by prez@lfx.org
; dumping from <START>-<END>h        (%X: uppercase, unpadded)
; --------------------------
; 
```

Then, with `cur = start` and the active table starting as table 1:

1. While `cur <= end`: seek to `cur` and read 4 bytes into a buffer. A short
   read at end of file leaves the previous iteration's bytes in the unread
   slots.
2. For width `w` = 4, 3, 2, 1: form the big-endian value of the first `w`
   buffer bytes and look up `(value, w)` in the active table. On a hit, the
   first matching rule applies:
   1. **swap** entry and table 2 is loaded: toggle the active table between 1
      and 2 and consume `w` bytes. The table switched to needs its own `!`
      entry to switch back. The active table persists for the rest of the dump.
   2. **kanji** (`BASE != 0` and `N != 0`): read `N` bytes at `cur + w`. For
      each byte `b`, look up `(b + BASE, 2)` in the active table and print its
      text. If that entry has no text, print hex (`<$%02X>` of `b + BASE`, e.g.
      `<$C012>`, or the `-h` format applied to `b` alone). If no entry exists,
      the program dereferences NULL and crashes. Consume `w + N`.
   3. **linked** (`N != 0`): read `w + N` bytes at `cur` and print each as hex.
      Consume `w + N`.
   4. **text** present: print it with escapes expanded. Consume `w`.
   5. Otherwise, i.e. a swap entry without table 2, or `@`/`$` with `N = 0`:
      nothing is consumed and the next shorter width is tried.
3. If width 1 found nothing, print the first buffer byte as hex and consume 1.
4. After the loop, write `\n`.

Consequences:

- Matching is greedy longest-prefix, capped at 4 bytes.
- A token starting at or before `<end>` is read and consumed in full, even past
  `<end>`; kanji and linked reads extend past it too.
- An `<end>` beyond end of file repeats stale buffer contents: each remaining
  offset re-decodes the last bytes read.
- `@C1=0,C000` is a no-op and `@C1=2,0` behaves like `$C1=2`.

## Crashes and memory defects

- An empty (or all-ignored) table 1 crashes in `free_tbl` at exit, before the
  output stream is flushed, so the output file is left empty. `free_tbl` also
  never frees the last node.
- A kanji lookup whose `byte + BASE` has no 2-byte entry crashes.
- `-h` copies its value into `malloc(strlen)`, one byte short.
- Kanji and linked reads go into a 256-byte stack buffer with no bound check.

## Windows binary

`romjuice.exe` is compiled from this exact source; Ghidra's decompilation
matches it function for function and contains the same strings, including
`-n`, `-h` and `-t`:

| Address    | Function        | Address    | Function         |
|------------|-----------------|------------|------------------|
| `0x401000` | `free_tbl`      | `0x401560` | `endian_swap`    |
| `0x401060` | `add_tbl_entry` | `0x4015B0` | `resolve_value`  |
| `0x401170` | `whereis`       | `0x4015F0` | `prints`         |
| `0x4011C0` | `load_table`    | `0x401710` | `scan_options`   |
| `0x4017A0` | `main`          |            |                  |

`struct tbl_entry` is 28 bytes: `value, bytes, string, kanji, linked, swap,
next`.

The build lacks `__MSDOS__`, so it takes the `open(rom, O_RDONLY)` branch.
With the CRT's default `_fmode` of text, **the ROM is read in text mode**:

- In each `read`, every `0D 0A` pair collapses to `0A`. Later bytes shift left,
  and slots past the returned count keep the raw bytes. A `0D` at the last
  buffer position peeks the next byte.
- Because every token re-seeks, a `0D 0A` byte pair dumps as though it were
  `0A 0A`, and any lookahead window containing the pair sees it collapsed
  (`41 0D 0A` cannot match a `410D` entry).
- `1A` ends the CRT's newline translation for that read, but the raw bytes are
  still in the buffer and `lseek` clears the EOF flag, so `1A` itself decodes
  normally.

Both the output (`"w"`) and the tables (`"r"`) are text-mode streams, so the
output has CRLF line endings.

## Manual errata

| `readme.txt` says                                    | The program does                                          |
|------------------------------------------------------|-----------------------------------------------------------|
| `-o` turns commenting off                            | `-n` does; `-o` is ignored                                |
| the included binary is a Linux binary                | it is Win32                                               |
| `$FF=A` "won't do anything"                          | `%i` fails, so `N` keeps the previous `@`/`$` line's value (initially uninitialised) |
| `@C1=2,C000` on `C1 12 34 56` looks up `C012 C034 C056` | it reads 2 bytes (`C012 C034`); `56` is dumped separately |
| counts are decimal                                   | counts are `%i` (decimal, `0x` hex, `0` octal)            |

## Replication notes

The behaviour worth reproducing is the dialect and the core loop:

- longest match of at most 4 bytes, first duplicate wins
- consumed widths of swap, kanji and linked entries
- lookahead past `<end>`
- escapes and the header

The text-mode ROM read, the stale buffer at end of file, last-line truncation,
and all crashes are defects; emulate them only to reproduce old dumps
byte-for-byte. For how this dialect's `!`, `@` and `$` prefixes collide with
other tools, see [table-dialects.md](table-dialects.md).
