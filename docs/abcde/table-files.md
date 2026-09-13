# abcde table files

Implemented in `Table::parseTableFile`, `Token::parseLine` and
`Table::finalize` (`../abcde/abcde/Table/`). How the entries behave at run time
is covered in [bin2text.md](bin2text.md) and [text2bin.md](text2bin.md).

## Reading a file

For each line of the file, in order:

1. Strip the trailing line break (`\R`); on line 1, strip a UTF-8 BOM.
2. Skip empty lines and lines whose first character is `#`. There are no
   trailing comments.
3. Decode as strict UTF-8 (invalid bytes are fatal), then NFD-normalise unless
   `--no-unicode-normalization` is given.
4. A line matching `^@([^<>]+)$` is a table ID. If the current table has no ID
   yet, the line names it, even if entries came first, so an ID may appear
   anywhere in a single-table file. Otherwise it starts a new logical table
   (only with `--multi-table-files`, else fatal).
5. Any other line is an entry. A bit string already present in the same logical
   table is fatal, whatever the prefixes or notation (`%10000000` equals `80`;
   hex digits are case-insensitive).

Line order carries no meaning. The same bit string may appear in different
tables. Loading is keyed by absolute path, and a duplicate `@id` across all
loaded tables is fatal.

## Entry grammar

The whole line must be consumed, or the entry is fatal.

```
entry     := prefixes lhs [ "<" weight ">" ] "=" rhs
prefixes  := ( "/" | "!" )*          at most one of each; "/!" and "!/" both work
lhs       := "%" [01]+               literal bits
           | [0-9A-Fa-f]+            4 bits per digit, big-endian; odd digit counts allowed
weight    := "-"? ( "0" | [1-9][0-9]* )

rhs, no "!"  := [^<>]*               text, may be empty
rhs, "!"     := [ "<" label ">" ] ( "," param )+
label        := [^$<>] [^<>]*        cannot start with "$"
param        := [ "<@" id ">:" | "<binary>:" ] match [ "+" ]
match        := "-1"                              not allowed with "<@id>:"; must be the last param
              | "0" | [1-9][0-9]*
              | ( "%" [01]+ | "$" ( [0-9A-Fa-f]{2} )+ ) [ "<" weight ">" ]
```

| Prefix | Flag             | Meaning                                                              |
|--------|------------------|----------------------------------------------------------------------|
| `/`    | `isEndToken`     | String end token                                                     |
| `!`    | `hasSwitchToken` | Table switch; the text is the label, empty when no label is given    |
| `/!`   | both             | Switch, and the string ends when translation returns to this table   |

Other rules:

- `\n` in text or a label is a newline during bin2text and is deleted during
  text2bin. No other escapes exist.
- `<`, `>` and line breaks cannot appear in text. Brackets such as `[END]` are
  the usual control-code notation.
- `<weight>` on the left-hand side sets `numTokens`: how much this entry counts
  towards match counts. It may be 0 or negative.
- The destination of a param:

  | Form            | Destination                                                        |
  |-----------------|--------------------------------------------------------------------|
  | `<@id>:`        | The table with that ID; case-sensitive, fatal at finalise if never loaded |
  | `<binary>:`     | The raw bit table (`<%0>`/`<%1>`)                                  |
  | none            | The raw table (`<$XX>` bytes, plus bits)                           |

- The match forms:

  | Form          | Meaning                                                                  |
  |---------------|--------------------------------------------------------------------------|
  | `N`           | Exactly N weighted matches                                               |
  | `0`           | As many as possible                                                      |
  | `-1`          | Fall back immediately                                                    |
  | `$hex`, `%bin` | Until those bits appear at a token boundary; stored as a forced-fallback (`FF`) token whose weight comes from its own `<weight>` |
  | trailing `+`  | Matches in the destination also count towards the switching table's counters |

  [bin2text.md](bin2text.md#switch-semantics) gives the exact, partly
  unintended, semantics, with worked examples.

## Finalisation

`finalize` runs once per table, after all table files are loaded. It:

1. Resolves every switch param to a table object (`param.table`), records
   `child_tables`, and marks both tables `participatesInSwitching`.
2. Copies every param into `token.paramStack`. Copies have no `currentCount`,
   which the A* search reads as "not yet entered".
3. Creates one zero-width `S` token per destination
   (`table.switchTokens{dest}`). Every non-raw table also gets an `S` token for
   the raw table and a `rawTableSwitchToken` whose single param is
   `{table: raw, requiredCount: 1}`.
4. Builds `binPat`, a regex alternation of every bit string sorted longest
   first, so a regex match is a longest-prefix match.
5. Computes `maxBinLength`, `allTokensHaveSameBinLength`, and each token's
   `unusableSuffixes`: for every longer entry that has this entry's bits as a
   prefix, the remaining bits. These are unused; see
   [text2bin.md](text2bin.md#defects).
6. Indexes tokens with non-empty text (normal, end, and labelled switch
   entries) in `tokensByFirstChar`.
7. Lowers the global `minBinPerText` to `binLength / textLength` of any such
   token. It starts at 0.25, the ratio of `<%0>`: 1 bit per 4 characters.

`switchTokensByBin` is sorted by bit string, and the switch-token
successors in A* follow that order.

## Raw tables

- `$rawTable` holds 256 byte tokens with text `<$00>`–`<$FF>` (uppercase, two
  digits) and two bit tokens `<%0>`/`<%1>`, all flagged `B`.
- `$rawBitTable` holds only the two bit tokens.
- Both are built before any user table is finalised.
- Insertion matches raw text by exact string comparison, so `<$ab>` is not a
  raw token.
- Atlas's `($XX)` form is not supported.

## Replication notes

- A table is a set of `(bits, flags, weight, text | label + params)` entries.
  An ID names the table, and the file is only a container.
- Keep the grammar strict and the duplicate check. Whole-table uniqueness of
  bit strings is what makes longest-prefix decoding deterministic.
- `\n` asymmetry, where the newline is emitted on dump and ignored on insert,
  is what lets dumps be re-inserted unchanged.
