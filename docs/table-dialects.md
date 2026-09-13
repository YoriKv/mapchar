# Table file dialects

Four table dialects are in play. Each tool reads its own, and prefix characters
mean different things in each. Sources:

| Tool                  | Source                                                         |
|-----------------------|----------------------------------------------------------------|
| romjuice              | its code ([romjuice.md](romjuice.md))                          |
| Cartographer PR3      | its manual, `../abcde/docs/Cartographer/readme.txt` (built on Klarth's TableLib 1.0) |
| Atlas 1.11            | its manual, `../abcde/docs/Atlas/Atlas.pdf`                    |
| abcde                 | its code ([abcde/table-files.md](abcde/table-files.md))        |

A dash means the manual does not cover it.

## Entries

| Feature              | romjuice                          | Cartographer PR3                 | Atlas 1.11                       | abcde                              |
|----------------------|-----------------------------------|----------------------------------|----------------------------------|------------------------------------|
| Normal `HEX=text`    | 1–4 bytes; width = ⌊digits/2⌋     | whole bytes, any length; odd digit count is an error | whole bytes, any length, even digit count | any multiple of 4 bits, any length |
| Bit entries          | no                                | no                               | no                               | `%0101=text`                       |
| Empty text `HEX=`    | consumes silently                 | "valid and simply ignored"       | —                                | empty token                        |
| End token            | none; use text escapes            | `/HEX=text`                      | `/HEX=text`, or `/text` with no bytes | `/HEX=text`                   |
| Newline entry        | —                                 | —                                | `*HEX` or `*HEX=text`            | —                                  |
| `$` prefix           | `$HEX=N`: trigger + N bytes as hex | `$HEX=label,N`: label + N bytes as `<$XX>` | skipped                  | error                              |
| `!` prefix           | `!HEX`: swap to the other table   | —                                | `!XX`: dakuten, skipped          | table switch with params           |
| `@` prefix           | `@HEX=N,BASE`: kanji array        | —                                | `@XX`: handakuten, skipped       | `@id` names a table                |
| Bookmarks `(…)`, `[…]`, `{…}` lines | ignored (no `=`)    | —                                | ignored                          | error                              |
| Other lines          | ignored if no `=`; a non-hex key before `=` gives a bogus entry | error if the first character is not a hex digit, `/` or `$` | —            | `#` comments; anything else is an error |
| Duplicate keys       | first wins                        | —                                | —                                | error                              |
| Multiple tables      | 2, swapped by `!`                 | one per block                    | `ADDTBL`/`ACTIVETBL` between strings | any number, switched mid-string |
| Text encoding        | bytes                             | —                                | ASCII or UTF-8                   | strict UTF-8, NFD                  |

## Escapes and raw bytes

| Feature              | romjuice                             | Cartographer PR3                | Atlas 1.11            | abcde                                 |
|----------------------|--------------------------------------|---------------------------------|-----------------------|---------------------------------------|
| `\n`                 | newline, then `; ` when commenting   | newline, not commented          | —                     | newline on dump; deleted on insert    |
| `\r`                 | newline, not commented               | newline, then `//`              | —                     | literal characters                    |
| Unmatched byte output | `<$XX>`, or a `-h` format           | `<$XX>`                         | n/a                   | `<$XX>` bytes, `<%b>` bits            |
| Raw input            | n/a                                  | n/a                             | `<$XX>` and `($XX)`, any case | `<$XX>` uppercase, `<%b>`     |
| Comment prefix in output | `; `                             | `//`                            | `//` lines skipped    | `//`                                  |

## Collisions to watch

- `\n` and `\r` are swapped between romjuice and Cartographer.
- `!` and `@` mean three different things across romjuice, Atlas and abcde, so
  a table is not portable by extension alone. A reader must be told the
  dialect.
- Linked entries use `$` in romjuice and Cartographer, with different right-hand
  sides. abcde expresses them as switch entries instead: `!HEX=<label>,N` is
  Cartographer's `$HEX=label,N`.
- Atlas's hexless `/<END>` (an end marker that inserts nothing) has no table
  form in abcde; `--artificial-end-token` replaces it.
- Width rules differ: `041=` is a 1-byte entry in romjuice, an error in
  Cartographer and Atlas, and a 12-bit entry in abcde.
