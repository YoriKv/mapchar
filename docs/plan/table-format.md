# Table format

The native table-file grammar, and how the four existing dialects
([`../table-dialects.md`](../table-dialects.md)) import into it. The grammar
keeps one line per entry so tables stay hand-editable and diffable, gives each
prefix character exactly one meaning, and names its dialect in a header line.

## Contents

1. [File structure](#file-structure)
2. [Keys](#keys)
3. [Entry kinds](#entry-kinds)
4. [Codes and text](#codes-and-text)
5. [Switch parameters](#switch-parameters)
6. [Charsets](#charsets)
7. [Validation](#validation)
8. [Legacy dialects](#legacy-dialects)

---

## File structure

A table file is UTF-8 text, LF or CRLF, optional BOM. A file whose bytes are
not UTF-8 — the Shift-JIS tables legacy tools left behind — is read as `cp932`,
and failing that as `latin-1`; the encoding used is recorded on the loaded file
and, when it is not UTF-8, said in a notice. Text is normalised to **NFC** on
load, so a decomposed dakuten kana becomes one composed character whatever the
file spelled, and a file written back is NFC. Three kinds of line:

| Line starts with | It is                                                     |
|------------------|-----------------------------------------------------------|
| `#`              | a comment; whole line only, no trailing comments          |
| `@`              | a directive: `@keyword arguments`                         |
| anything else    | an entry, or blank                                        |

Directives:

| Directive           | Meaning                                                                             |
|---------------------|-------------------------------------------------------------------------------------|
| `@mapchar table 1`  | Header. The first non-blank, non-comment line of every native file; `1` is the grammar version. |
| `@table id`         | Starts a logical table named `id`. Entries before the first `@table` belong to a table named after the file. |
| `@charset name`     | The built-in charset the current table sits on ([Charsets](#charsets)).             |

`id` is `[\w.-]+` — letters and digits of any script, `_`, `.` and `-`, so
`@table かんじ` names a table after what is in it — and is unique across every
loaded file. A file may hold any number of tables. Line order carries no
meaning inside a table.

```
@mapchar table 1
# Dragon Warrior, main font
@table main
@charset none
41=A
42=B
0041=あ
%01=x
/FF=[end]
FE=[line]\n
$F0=[color],u8
!F1=[item] @items:1
!F2=[name] @names:*
@table items
01=Herb
!FF=return
```

## Keys

The left-hand side of an entry is the bits it matches:

| Form            | Bits                                                              |
|-----------------|-------------------------------------------------------------------|
| `41`, `0041`    | hex digits, 4 bits each, most significant first; leading zeros count, so `0041` is 16 bits and `41` is 8 |
| `%0101`         | literal bits                                                      |
| `41<2>`         | either form followed by a **weight** in angle brackets            |

- Odd digit counts are allowed: `041` is 12 bits.
- Matching is longest-prefix over the whole table, at any bit length; the
  table's entries need not share a width.
- **Weight** is how much the entry counts towards a switch counter
  ([Switch parameters](#switch-parameters)); default `1`, may be `0`.

## Entry kinds

| Line                        | Kind        | Meaning                                                                        |
|-----------------------------|-------------|--------------------------------------------------------------------------------|
| `KEY=text`                  | **text**    | The bits decode to `text`; `text` may be empty                                 |
| `/KEY=text`                 | **end**     | As text, and the string ends after it (in end-terminated blocks)               |
| `$KEY=[label],operands`     | **code**    | A control code with typed operands read from the bytes that follow             |
| `!KEY=text params`          | **switch**  | Prints `text` (often a `[label]`, possibly nothing), then continues decoding in other tables |
| `!KEY=return`               | **return**  | Leaves the current switch frame; at the top level, ends the string             |

One prefix, one meaning: `/` end, `$` operands, `!` table control, `@`
directive, `#` comment. Nothing else is special at the start of a line.

### Operand entries

`$KEY=[label],spec,spec…` reads one operand per spec after the key, in order:

| Spec              | Reads                                        | Shown as             |
|-------------------|----------------------------------------------|----------------------|
| `u8`, `u16`, `u24`, `u32` | that many bits, little-endian        | `$XX`, `$XXXX`, …    |
| `u16be`, `u24be`, `u32be` | big-endian                           | hex, as above        |
| `s8`, `s16`, `s16be`      | signed                               | decimal with sign    |
| `N` (an integer)  | `N` raw bytes                                | `N` hex bytes        |
| `bits:N`          | `N` raw bits                                 | `%bbb`               |

The dump shows `[label op op…]`, for example `[color $03]` or
`[window $10 $08]`; the operands are parsed back on insert. An operand that
runs past the end of the data leaves the string with an
`[label]` code followed by the raw bytes that were there, and a notice.

### Switch entries

`!KEY=text params` emits `text` and then decodes the bytes that follow in
other tables, one frame per parameter, in order. The text is written like a
text entry's (usually a single code such as `[item]`, but `Name:` or `\n`
work too) and may be empty: a **silent** switch prints nothing, and the
encoder inserts it wherever the following text needs its table, as abcde
does. Parameters are the trailing words that parse as parameters
([Switch parameters](#switch-parameters)); the same code label may not name
two entries in one table.

## Codes and text

Everything in **square brackets** in decoded text is a code, and nothing else
is:

| Code               | Meaning                                                            |
|--------------------|--------------------------------------------------------------------|
| `[label]`          | an end, code or switch entry with that label                       |
| `[label $03 -2]`   | the same with its operands                                         |
| `[$1F]`            | one unmatched byte                                                 |
| `[%101]`           | unmatched bits shorter than a byte, at the end of a window         |

- A label is `[^\[\]\s$%][^\[\]\s]*`: no whitespace, no brackets, and not
  starting with `$` or `%`. A code may sit inside a text entry's text
  (`40=[FB]\n＊「`) and is then matched as a code by the encoder too.
- In table text and in scripts, a literal bracket is written `\[` or `\]`.
  The other escapes are `\n` (a line break: emitted after the token on dump,
  ignored on insert, so dumps stay re-insertable) and `\\`.
- Text is NFC: an entry's text is composed when the file loads and written
  back composed. The encoder compares text decomposed, so a table that spells
  `が` in one entry and one that spells it as `か` plus a separate dakuten code
  — which is how a ROM that draws the mark on its own does — both encode the
  same typed text.
- `<`, `>` and `=` carry no meaning in text.

A text entry's text can therefore never be mistaken for a code, and a script
can be re-parsed without knowing which table produced it.

## Switch parameters

A switch entry's parameters are space-separated, and each pushes one frame
when the entry is matched:

```
params    := param ( " " param )* [ " return" ]
param     := "@" table ":" stop [ "+" ]
stop      := N            exactly N weighted matches, then pop; 0 is no stop, as "*"
           | "*"          until the string ends, an end token, or a return entry
           | "$" hex      until those bits appear at a token boundary; they are consumed and pop the frame
           | "%" bits     the same, in bits
table     := id           a loaded table
           | "raw"        one unmatched byte per match, shown [$XX]
           | "bits"       one unmatched bit per match, shown [%b]
```

Semantics, in the stack machine of [architecture.md](architecture.md#31-decode):

- **Frames are independent.** Each frame has its own counter. A match in a
  frame decrements only that frame's counter, except that a `+` frame also
  decrements the frame beneath it. A frame pops when its counter reaches
  zero or below.
- **`*` frames** pop when the data or the string's bound ends, when an end
  token is matched, or when a `return` entry of the frame's table is matched.
- **Fallback bits** (`$hex`, `%bits`) are checked before the table's entries
  at every step of that frame. On decode they print nothing; on encode they
  are written whenever the frame closes, including at the end of the string,
  so a decoded string re-encodes to the same bytes.
- **`return`** as a trailing parameter runs after the parameters before it
  and then pops the innermost frame of the table the switch was matched in;
  at the top level it ends the string. `!KEY=return` alone is the same with
  no text and no other parameters. A labelled return such as
  `!FF00=[palette:_green] return` prints its label and leaves the frame.
- **The switch entry itself** counts its weight towards the frame it was
  matched in before its parameters run.
- **Unmatched data** in any frame decodes as `[$XX]`, weighs `0`, and the
  frame keeps going. Nothing in a table can make decoding stop dead.
- **Chained switches** — a switch matched inside a frame pushes its own
  frames on top; a frame that pops returns to the frame beneath it, whatever
  table that is.

```
!F1=[item] @items:1          one item name, then back
!F3=[menu] @items:$FF        item names until FF, which is consumed
!03=[str] @upper:3+          three tokens from upper that also count here (length-prefix strings)
!F4=[font2] @font2:*         switch until [end] or a return entry in font2
```

## Charsets

`@charset name` fills the current table with every code of a built-in
charset before the file's own entries apply. Entries in the file override
the charset code for code; an entry with empty text removes a code.

| Name          | Entries                                                    |
|---------------|------------------------------------------------------------|
| `none`        | nothing (the default)                                      |
| `ascii`       | `20`–`7E`                                                  |
| `latin-1`     | `20`–`7E`, `A1`–`AC`, `AE`–`FF` (the blank `A0` and the soft hyphen `AD` are left out, as every unprintable code is) |
| `shift-jis`   | every single- and double-byte **CP932** code: Shift-JIS plus the NEC and IBM extension rows Japanese games use |
| `euc-jp`      | every **JIS X 0213** EUC-JP code (`euc_jis_2004`), a superset of plain EUC-JP |
| `utf-16le`, `utf-16be` | every plane as 16-bit keys, an astral code point as a surrogate pair (4 bytes) |
| `utf-8`       | one entry per encoded code point, every plane, up to 4 bytes |

A charset may also carry **aliases**: text the encoder accepts for a code whose
own text is something else, so nothing is lost where an encoding folds several
characters onto one code. `shift-jis` and `euc-jp` alias the JIS X 0201 yen
sign `¥` to `5C` and the overline `‾` to `7E` — typing either encodes to that
byte, while the byte still decodes as plain ASCII `\` or `~` — and every
character `cp932` folds (`¢`, `£`, `¬`, `‖`, `−`, `〜`) reaches its code the
same way.

Charsets are plugins; more can be added.

## Validation

Loading fails with a line number for:

- a missing or unknown header;
- a line that is neither blank, a comment, a directive, nor a valid entry;
- the same bits twice in one table, in any notation;
- the same label twice in one table;
- a switch parameter naming a table that no loaded file defines (checked
  after all files load);
- `return` anywhere but last among a switch entry's parameters.

Not errors: the same text under two keys (decode is deterministic by bits;
encode picks the cheapest), and the same bits in two different tables.

## Legacy dialects

A file without a `@mapchar` header loads through one of four dialects. The
dialect is guessed from the file's content (`@id` lines and `!KEY=<…>,` mean
abcde; `$KEY=label,N` means Cartographer; `!HEX`/`@HEX=N,BASE` mean
romjuice; `*HEX` and `/text` mean Atlas) and confirmed by the user when the
guess is ambiguous. Each dialect follows its own tool's parsing rules from
[`../table-dialects.md`](../table-dialects.md) and converts to the native
model. Conversion never writes the source file; **Save As Native** writes a
new one.

| Legacy form                          | Native form                                                             |
|--------------------------------------|-------------------------------------------------------------------------|
| `HEX=text` (all)                     | `HEX=text`, with the dialect's width rule applied (romjuice's `⌊digits/2⌋` bytes) |
| `/HEX=text` (Cartographer, Atlas, abcde) | `/HEX=text`                                                         |
| Atlas `/text`                        | Not an entry: recorded as the file's suggested artificial end label      |
| Atlas `*HEX`, `*HEX=text`            | `HEX=\n`, `HEX=text\n`                                                  |
| Atlas `!XX`, `@XX` (dakuten)         | Dropped, with a notice                                                  |
| Cartographer `$HEX=label,N`          | `$HEX=[label],N`; whitespace in `label` becomes `_`                     |
| romjuice `$HEX=N`                    | `$HEX=[raw_HEX],N`                                                      |
| romjuice `@HEX=N,BASE`               | `!HEX=[kanji_BASE] @kanji_BASE:N`, plus a generated table `kanji_BASE` holding `b=text` for every 2-byte entry `BASE+b` of the source table |
| romjuice `!HEX` in table 1, table 2  | `!HEX=[swap] @table2:*` in table 1; `!HEX=return` in table 2. romjuice's swap persists across strings; the conversion does not, and says so |
| romjuice duplicate keys              | first wins, the rest are dropped with a notice                          |
| romjuice `\r`                        | `\n`                                                                    |
| A file that is not UTF-8             | read as `cp932`, else `latin-1`, with a notice naming the encoding       |
| abcde's NFD text                     | composed to NFC, as all table text is                                   |
| abcde `@id`                          | `@table id`                                                             |
| abcde `%bits`, `KEY<w>`              | unchanged                                                               |
| abcde `!KEY=<label>,params`          | `!KEY=[label] params` with `<@id>:N` → `@id:N`, bare `N` → `@raw:N`, `<binary>:N` → `@bits:N`, `0` → `:*`, `-1` → `return`, `$hex`/`%bin` → `:$hex`/`:%bin`, `+` kept |
| abcde unlabelled `!KEY=,params`      | `!KEY= params`: a silent switch                                          |
| abcde `!KEY=<label>,params,-1`        | `!KEY=text params return`; `<[X Y]>` becomes `[X_Y]`, other labels become printed text |
| Codes inside legacy text (`[FB]`, `[cardinal #]`) | kept as codes, whitespace becoming `_`                    |
| abcde `\n`                           | unchanged                                                               |
| Bookmark lines `(…)`, `[…]`, `{…}`   | dropped                                                                 |

Every dialect's raw-byte notation (`<$XX>`) is written `[$XX]` in native
scripts and translated back on Atlas export.
