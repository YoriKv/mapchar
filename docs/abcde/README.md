# abcde

abcde 0.0.10 (abw, 2024-05-25) is a Perl 5 table-based binary↔text translator.
It extends the table format with bit-level entries and multi-table switching,
dumps text with a Cartographer-compatible command module, and inserts text with
an Atlas-compatible command module that encodes using an A* search. It lives
outside this repository at `../abcde/`.

Claims marked *observed* in these docs were reproduced by running abcde on
synthetic inputs; the rest are read from the source.

## Topic index

| Doc                                   | Covers                                                                    |
|---------------------------------------|---------------------------------------------------------------------------|
| [table-files.md](table-files.md)      | Table file grammar, parsing, finalisation, raw tables                     |
| [bin2text.md](bin2text.md)            | Extraction engine: longest-prefix matching, table switching, counters, end conditions |
| [text2bin.md](text2bin.md)            | Insertion engine: A* state space, successor rules, costs, defects         |
| [cartographer.md](cartographer.md)    | `abcde::Cartographer`: command file, pointer reading, output format, embedded-pointer subclassing |
| [atlas.md](atlas.md)                  | `abcde::Atlas`: script parsing, every command, the string write pipeline, pointer math |

For how abcde's table syntax relates to romjuice, Cartographer and Atlas, see
[../table-dialects.md](../table-dialects.md).

## Source layout

| Path                                            | Role                                                             |
|-------------------------------------------------|------------------------------------------------------------------|
| `abcde.pl`                                      | Entry point: options, raw tables, plain STDIN→STDOUT mode, stats |
| `abcde/Table/Table.pm`                          | Table file parsing, `finalize`, `bin2text`, the `text2bin` A* driver |
| `abcde/Table/Token.pm`                          | Token constructor and table-line parser                          |
| `abcde/Table/AStarNode.pm`                      | A* node construction and successor generation                    |
| `abcde/String/BasicString.pm`                   | String wrapper: runs `bin2text`/`text2bin` with per-string options; concatenates output |
| `abcde/Cartographer.pm`                         | Cartographer command module (bin2text)                           |
| `abcde/Cartographer/NES/Battle_of_Olympus.pm`   | Example subclass that follows embedded pointers                  |
| `abcde/Atlas.pm`                                | Atlas command module (text2bin)                                  |
| `cpan/Hash/PriorityQueue.pm`                    | Bundled priority queue: a hash of priority → FIFO array          |
| `docs/readme.txt`                               | Author's manual                                                  |
| `docs/Cartographer/readme.txt`                  | Manual of the original Cartographer PR3 (RedComet)               |
| `docs/Atlas/Atlas.pdf`                          | Manual of the original Atlas 1.11 (Klarth)                       |
| `eg/NES/<game>/`                                | Tables, Cartographer command files, Atlas script excerpts and run scripts for Battle of Olympus, Dragon Warrior II and Dragon Quest IV (no ROMs) |

## Command line

```
perl abcde.pl <general options> [-cm <module> [<module arguments>]]
```

| Option                                | Effect                                                                         |
|---------------------------------------|--------------------------------------------------------------------------------|
| `-m`, `--mode bin2text\|text2bin`     | Required in plain mode; each module forces its own mode and warns on a conflict |
| `-t`, `--table <file>`                | Load a table file (repeatable), resolved against the base relative path       |
| `-g`, `--group`                       | Output one debug line per token (`line`, table file, flags) instead of data    |
| `-h`, `--help`                        | Usage, or the module's help when combined with `-cm`                           |
| `-brp`, `--base-relative-path <path>` | Base for relative table/script paths (default `.`); `*` means "the script's directory" in `abcde::Atlas` only |
| `-s`, `--stats`                       | Print bit/token/character/time statistics to STDERR                            |
| `--artificial-end-token <label>`      | `<label>` splitting `#FIXEDLENGTH` text in `abcde::Atlas`; must look like `<...>`, trailing `\n`s stripped |
| `--multi-table-files`                 | Allow more than one `@id` table per file                                       |
| `--no-unicode-normalization`          | Disable NFD on input and NFC on Cartographer output                            |
| `--start-table-file-name <file>`      | Plain mode start table; compared against absolute paths, so a relative name never matches (*observed*) |
| `-cm <module>`                        | Load a command module (`abcde::Atlas`, `abcde::Cartographer`, or a subclass); all remaining arguments go to it |

Parsing stops at the first argument that is false in Perl, so an argument
`0` ends option parsing. Unknown options die with the usage text.

The module name is validated as `::`-separated identifiers and loaded with
`require`, so any Perl package providing `updateOptions`, `printHelp` and
`handleCommandFile` works as a module.

## Plain mode

Without `-cm`, abcde does the following:

1. Finalises all tables. The start table is `--start-table-file-name`, else
   the first table of the first `-t` file, else the raw table.
2. Reads all of STDIN.
   - `bin2text`: STDIN is raw and converted to a bit string. Output is text,
     UTF-8, and **not** NFC-composed.
   - `text2bin`: STDIN is UTF-8 with every line break (`\R`) removed, and is
     **not** NFD-decomposed. Precomposed input therefore fails against the NFD
     tables (*observed*: `が` fails, `か`+U+3099 works).
3. Runs one `BasicString` over the whole input. Text output is the concatenated
   token texts; binary output is the concatenated token bits, packed MSB-first
   with the last byte zero-padded. `-g` prints token lines instead.

In plain `bin2text`, end tokens do not stop translation. Padding bits at the
end of the input are decoded like any other bits (*observed*).

## Data model

- **Binary** is a Perl string of `'0'`/`'1'` characters (`unpack 'B*'`).
  Every position and length inside the engines is in bits; Cartographer
  multiplies byte addresses by 8 and Atlas packs back to bytes.
- **Token** (`Token.pm`), one per table entry or internal marker:

  | Field              | Meaning                                                                    |
  |--------------------|----------------------------------------------------------------------------|
  | `bin`, `binLength` | Bit string and its length                                                  |
  | `text`, `textLength` | Right-hand side, or the label for switch entries                         |
  | `numTokens`        | Weight counted towards match counts (`<n>` suffix, default 1)              |
  | `flags`            | See below                                                                  |
  | `params`           | Switch parameters, in order                                                |
  | `paramStack`       | Copies of `params` used as A* stack entries                                |
  | `table`            | Owning table                                                               |
  | `line`, `lineNum`  | Source line, for debug                                                     |

  Flags:

  | Flag              | Meaning                                              |
  |-------------------|------------------------------------------------------|
  | `isEndToken`      | Entry has the `/` prefix                             |
  | `hasSwitchToken`  | Entry has the `!` prefix                             |
  | `B`               | Raw bit or byte token                                |
  | `S`               | Zero-width marker for switching into a table         |
  | `FC`              | Zero-width marker: required count reached            |
  | `FF`              | Zero-width marker: forced fallback                   |
  | `FI`              | Zero-width marker: immediate fallback (`-1`)         |
  | `FB`              | Zero-width marker: no-match fallback (unused)        |

- **Table** (`Table.pm`), one per logical table. Fields:
  - `fileName` (absolute path) and `id`
  - `tokensByBin` (bits → token) and `switchTokensByBin` (sorted by bits)
  - `binPat`: regex alternation of all bit strings, longest first
  - `maxBinLength`, `allTokensHaveSameBinLength`
  - `tokensByFirstChar`: text index for insertion
  - `switchTokens`: destination table → `S` token
  - `rawTableSwitchToken`: fallback into the raw table for one match
  - `child_tables`, `participatesInSwitching`, `canReachEndToken`
  - `AtlasID`: `Table_N`, assigned by Cartographer
- **Globals** (`abcde::`):
  - `%tablesByFileName` (absolute path → list of tables), `%tablesByID`,
    `@allTables` (load order)
  - `$rawTable`: 256 byte tokens `<$00>`–`<$FF>` plus `<%0>`/`<%1>`
  - `$rawBitTable`: `<%0>`/`<%1>` only
  - `%internalTokens` (`FB FC FF FI`)
  - `$minBinPerText`: the A* heuristic factor
  - `%options`, `$data` (the whole input as bits)
  - `$reserved_chars = { open => '<', close => '>' }`

## Unicode

- Table files, command files and pointer lists are decoded as strict UTF-8
  (invalid bytes are fatal), have a leading BOM removed, and are NFD-normalised
  unless `--no-unicode-normalization` is given.
- Cartographer output is NFC-normalised per pointer.
- ROM data, and STDIN/STDOUT in plain mode, are not normalised.

## Replication notes

Each topic doc ends with its own replication notes. Across the whole tool, what
distinguishes abcde from byte-oriented tools is:

- bit-addressed matching with entries of any bit length
- a stack of table switches with per-parameter match counts
- an insertion search that respects the extraction algorithm's longest-prefix
  semantics

Its defects are concentrated in the counter bookkeeping ([bin2text.md](bin2text.md))
and the search's state pruning ([text2bin.md](text2bin.md)).
