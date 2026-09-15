# Script and exchange formats

The native script that dumps and imports strings, the translator hand-off
files, and how Cartographer command files and Atlas scripts import and
export. Tables are covered in [table-format.md](table-format.md).

## Contents

1. [Native script](#native-script)
2. [Translator files](#translator-files)
3. [Cartographer](#cartographer)
4. [Atlas](#atlas)

---

## Native script

A script is the text form of one or more blocks: UTF-8, LF or CRLF, the same
three line kinds as a table file (`#` comment, `@` directive, content). Text is
normalised to **NFC** on import, as table text is, so a translation composes
however the translator's editor spelled it.

```
@mapchar script 1
@rom "Dragon Warrior (U).nes"
@table "tables/main.tbl"
@block "Dialogue" source=pointers start=$8000 stop=$8100 size=2 stride=2 endian=little mapping=banked:8000:4000 offset=1 bank=0 type=end table=main bound=$A000 mode=packed
@string 0 at $8123-$8140 ptr $8000
Welcome to[line]
Tantegel Castle.[end]
@string 1 at $8140-$815A ptr $8002 $8004
# Thou hast[line]
# done well.[end]
Thou hast[line]
done well.[end]
```

- **`@mapchar script 1`** heads the file.
- **`@rom "path"`**, **`@table "path"`** — the files the script was dumped
  from, relative to the script. Import uses them to create missing entries
  and otherwise ignores them.
- **`@block "name" key=value…`** — the block's configuration as `key=value`
  pairs, one per setting of the Reading bar
  ([features.md](features.md#blocks)). Import creates the block when the
  project has none of that name, and otherwise leaves the project's
  configuration alone.
- **`@string N at $start-$end ptr $addr…`** — one string: its index in the
  block, its byte extent on disk (exclusive end), and the addresses of every
  pointer that reaches it (none for range and fixed sources).
- **Content lines** between `@string` directives are the string's text. Line
  breaks are joined with nothing; a `\n` in a token's table text is what puts
  a line break after it on dump, so `[line]` codes end lines and the text
  re-imports unchanged. A content line starting with `@`, `#` or `\` is
  written `\@`, `\#`, `\\`.
- **Comment lines** inside a string are the **original** when the dump was
  made with *both*; import ignores them.
- **Dump modes** — *originals* (content is the original decode), *translations*
  (content is the translation, or the original where none exists), *both*
  (original as comments, translation as content).
- **Import** — for each `@string`, the block named by the enclosing `@block`
  and the index select the project string; the content becomes its
  translation; a string whose content differs from its original is marked
  **edited**. A string whose content is its own original spelled decomposed is
  therefore untouched, not edited. Extents and pointers in the script are
  checked against the project and a mismatch is reported as a notice, since the
  project is the authority.

Numbers are decimal or `$hex`. Strings are in double quotes with `\"` and
`\\` escapes.

## Translator files

Both formats carry one record per string with the same fields; both import
by `id`.

| Field         | Content                                                        |
|---------------|----------------------------------------------------------------|
| `id`          | `block name/index`                                             |
| `address`     | `$start`                                                       |
| `original`    | the decode, with codes in brackets and line breaks after `\n` tokens |
| `translation` | the translation, empty when untouched                          |
| `status`      | `untouched`, `edited`, `review`, `too_long`, `invalid`         |
| `notes`       | free text                                                      |

- **TSV / CSV** — a header row then one row per string; line breaks inside
  a cell are kept (quoted per RFC 4180 in CSV; written `\n` in TSV). Export
  chooses the delimiter; import detects it. CSV is written UTF-8 with a
  byte-order mark, which is what a spreadsheet needs to read it as UTF-8; TSV
  and PO are written without one, and an import accepts either.
- **PO** — one entry per string: `msgctxt "id"`, `msgid` original, `msgstr`
  translation, `#: rom:address` reference, `#. notes` as extracted comment,
  `#, fuzzy` when the status is *review*. Multi-line strings use PO's
  standard continuation. Plural forms are not used.
- **Import rules** — a record whose `original` differs from the project's
  current decode is skipped and listed, unless **Force** is on; the comparison
  is on NFC, so a round trip through an editor that decomposes text skips
  nothing. `status` becomes *edited* when the translation changed and *review*
  when the record says so; `too_long` and `invalid` are recomputed, never
  imported.

## Cartographer

The importer reads a command file with Cartographer's rules
([`../abcde/cartographer.md`](../abcde/cartographer.md#command-file-parsing))
and creates one block per `#BLOCK`:

| Cartographer                          | Block field                                             |
|---------------------------------------|---------------------------------------------------------|
| `METHOD: RAW`, `SCRIPT START/STOP`    | source *range*                                          |
| `METHOD: POINTER*`, `POINTER TABLE START/STOP`, `POINTER SIZE/SPACE/ENDIAN` | source *pointer table*, stride = size + space |
| `BASE POINTER`                        | mapping *linear*, offset = base; `POINTER_RELATIVE_PC` is mapping *relative* |
| `TYPE: FIXED_STRING`, `STRING LENGTH` | string type *fixed length*, with **Stop at end token** off |
| `FIXED_LINE`, `LINE LENGTH`           | fixed-line layout                                       |
| `STRING END`, `END CTRL`, `LINE END`, `LINE CTRL` | labels of the artificial `[end]`/`[line]` codes shown in fixed strings |
| `STRINGS PER POINTER`                 | strings per pointer                                     |
| `STRINGS END AT NEXT POINTER`         | string type *next pointer*                              |
| `STRING END REALIGN *`                | realign                                                 |
| `AUTO JUMP START/STOP`                | skip ranges                                             |
| `TABLE`, `TABLE ID`, `SUB TABLE`      | table files registered through the abcde dialect; start table |
| `SORT OUTPUT BY STRING ADDRESS`       | Strings view sort, saved in the block                   |
| `COMMENTS`, `SHOW END ADDRESS`, `TRIM TRAILING NEWLINES`, `ATLAS PTRS`, `GAME NAME` | recorded for export, no effect on the block |

The block is then extracted. The exporter writes the reverse mapping for a
block, and reports fields Cartographer has no command for (Pascal strings,
banked mappings other than a constant base, slotted mode, a bound narrower
than the source).

Cartographer *dump files* are not imported; the native script replaces them.
A block imported from a command file dumps the same strings in native form.

## Atlas

**Export** writes, for a block, an Atlas script that abcde's Atlas module
inserts ([`../abcde/atlas.md`](../abcde/atlas.md)):

- `#VAR`/`#ADDTBL` for each table, one file each, written in the abcde dialect next to
  the script, with paths relative to the script; a table id goes out as it is,
  since abcde's `@id` accepts any line without angle brackets, and abcde
  NFD-normalises both the `@id` and the `<@id>:` that names it, so they still
  match;
- `#ACTIVETBL`, `#JMP(start, bound-1)`, `#HDR(header)`;
- per string: `#W16`/`#W24`/`#W32(addr)` for each pointer under a LINEAR or
  constant-offset mapping, or `#CREATEPTR`/`#WRITE` for LoROM, HiROM and GB;
- `#STRTYPE`, `#PASCALLEN`, `#FIXEDLENGTH` and `#STRINGALIGN` as the block's
  string type requires;
- the translation text with `[$XX]` rewritten as `<$XX>`, one string per
  line group, and a blank line between strings.

The text is what mapchar would insert; the pointer commands reproduce
mapchar's *packed* layout. A block in *slotted* mode exports one `#JMP` per
string.

**Import** reads the subset of Atlas that maps onto a block: `#ADDTBL`,
`#ACTIVETBL`, `#JMP`, `#HDR`, `#W8`–`#W32`, `#PTRTBL`/`#WRITE`,
`#STRTYPE`, `#PASCALLEN`, `#FIXEDLENGTH`, `#STRINGALIGN`, `#SMA`,
`#CREATEPTR`. Text between commands becomes one string's translation, matched
to the project's block by insert address. Any other command is listed and the
import stops at the first one that changes where text goes (`#SKIP`,
`#SETTARGETFILE`, `#AUTOCMD`, embedded-pointer commands).
