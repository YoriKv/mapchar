# Getting Started

Translating two blocks of text in **Mortal Kombat II** (Game Boy,
`Mortal Kombat II (USA, Europe).gb`) into Ukrainian. Addresses are file offsets
in hex.

> **Work on a copy of the ROM.** mapchar writes into the ROM file in place and
> keeps no backup.

## 1. Create the project and open the ROM

mapchar opens on an empty project (**File ▸ New Project**, Ctrl+N).

![The empty window](images/01-empty-window.png)

**File ▸ Open ROM…** (Ctrl+Shift+O), then **File ▸ Save Project** (Ctrl+S) as
`MK2.mapchar` beside the ROM.

![The ROM, open](images/01-rom-open.png)

With no table, the file reads as ASCII.

## 2. Build `mk2.tbl`

A table maps bytes to characters.

**Search ▸ Search Window…** (Ctrl+Shift+F) runs a relative search. `KITANA`
is found with `upper=41`: `A` is `41`.

![Relative search for KITANA](images/02-relative-search.png)

**File ▸ New Table…**, save as `mk2.tbl`. It opens in the Table Editor.

![The empty table](images/02-table-empty.png)

**Fill…**: **A-Z**, first key `41`.

![Fill, A to Z from 41](images/02-fill-dialog.png)

**Fill…** again: **0-9**, first key `30`.

![Letters and digits filled](images/02-table-filled.png)

Add the rest by hand — **New**, Key, Text, **Add**:

| Key | Text |
| --- | --- |
| `20` | space |
| `21` | `!` |
| `2C` | `,` |
| `2E` | `.` |
| `3F` | `?` |
| `00` | `[end]`, **Kind: End** |

![The end entry](images/02-table-end-entry.png)

**Save**.

![mk2.tbl, finished](images/02-table-done.png)

## 3. Look for text through the table

Pick **@mk2** in the **Table** list.

**Search ▸ Scan for Text…** (Ctrl+Shift+R) lists text-like regions and how
their strings end: an **End token**, or a **Length prefix** with the bytes of
header in front of it. Selecting a row jumps to it.

![The Scan window](images/03-scan.png)

**Find** (Ctrl+F) takes hex bytes or `"quoted text"`, and pins a string down.
Search `"FINISH"` in the **Text** tab:

![Find, in the Text tab](images/03-find.png)

The match is at `$8649`. Each string here follows three bytes; the third is
its length.

## 4. A Strings block: Finishes

In the **Hex** tab, drag-select `$8646`–`$86A5`: from the three bytes before
`FINISH HIM!` to the last `!` of `BABALITY!!`.

![The region, selected](images/04-select-region.png)

**File ▸ New Block** (Ctrl+Shift+B). Rename it `Finishes` (F2).

![One long string](images/04-block-end-token.png)

No `00` in here, so it is one string. On the Reading bar set **Ends at** to
**Length prefix**, **Prefix** 1.

![Length prefix, no header yet](images/04-block-length-prefix.png)

Each record is two position bytes, a length byte, then text:

```
05 CB   0B   46 49 4E 49 53 48 20 48 49 4D 21
pos     len  FINISH HIM!
```

> The two bytes before each length are where on screen the game draws the
> string. They are not text, so the block has to step over them; left in, the
> first one is read as a length and every string after it is cut wrong, as
> above.

Set **Header** to 2.

![Finishes, read correctly](images/04-finishes-strings.png)

**Original** is the text when the block was made; **Translation** is what the
bytes say now. Writing is **slotted**: each string keeps its address and its
own room.

Ctrl+1, Ctrl+2 and Ctrl+3 show the block as **Hex**, **Text** and **Strings**.

| Hex | Text | Strings |
|:-:|:-:|:-:|
| [![Finishes in Hex](images/04-view-hex.png)](images/04-view-hex.png) | [![Finishes in Text](images/04-view-text.png)](images/04-view-text.png) | [![Finishes in Strings](images/04-view-strings.png)](images/04-view-strings.png) |

## 5. A Pointers block: Fighter names

Select the ROM in the Files panel, go to `$8DE0`, select `$8E02`–`$8E4D`.

![The names, selected](images/05-select-names.png)

**New Block**, rename to `Fighter names`.

![Fighter names as a range](images/05-names-range.png)

**Search ▸ Find Pointers…** (Ctrl+Shift+P), with the offsets left at 0.

![Find Pointers: what to try](images/05-find-pointers-setup.png)

Take the first row with **Use as Pointer Table**: 2-byte Game Boy pointers at
`$8DE9`. The names are in bank 2, which the game sees at `$4000`, so the
pointer to `$8E02` holds `$4E02`.

![Find Pointers: the results](images/05-find-pointers-results.png)

![Fighter names as a pointer table](images/05-names-pointers.png)

Writing is now **packed**: strings share the room up to **Bound** — by default
the end of the names — and pointers are rewritten.

The same three views, Strings with **Panels ▸ Hex** open:

| Hex | Text | Strings |
|:-:|:-:|:-:|
| [![The names in Hex](images/05-view-hex.png)](images/05-view-hex.png) | [![The names in Text](images/05-view-text.png)](images/05-view-text.png) | [![Strings with the Hex panel](images/05-view-strings.png)](images/05-view-strings.png) |

In Hex, **Show as Pointers** shows the pointer table instead of the strings it
points to.

![The pointer table in Hex](images/05-view-hex-pointers.png)

## 6. Build `mk2-translated.tbl`

The font has 26 letter tiles, so Cyrillic reuses the Latin codes. Lookalikes
keep their code; the other 14 are reassigned.

| Code | Latin | Cyrillic | | Code | Latin | Cyrillic |
| --- | --- | --- | --- | --- | --- | --- |
| `41` | A | А | | `4E` | N | И |
| `42` | B | В | | `4F` | O | О |
| `43` | C | С | | `50` | P | Р |
| `44` | D | Д | | `51` | Q | Ч |
| `45` | E | Е | | `52` | R | Я |
| `46` | F | Ф | | `53` | S | Б |
| `47` | G | Г | | `54` | T | Т |
| `48` | H | Н | | `55` | U | П |
| `49` | I | І | | `56` | V | Ж |
| `4A` | J | Й | | `57` | W | Ш |
| `4B` | K | К | | `58` | X | Ї |
| `4C` | L | Л | | `59` | Y | У |
| `4D` | M | М | | `5A` | Z | З |

`Ґ Є Х Ц Щ Ь Ю` do not fit.

**File ▸ New Table…**, `mk2-translated.tbl`. **Fill…**: **Custom…**, first
key `41`, all Cyrillic:

```
АВСДЕФГНІЙКЛМИОРЧЯБТПЖШЇУЗ
```

![Fill with a custom run](images/06-fill-cyrillic.png)

Add digits, punctuation and `[end]` as in step 2. **Save**.

![mk2-translated.tbl](images/06-table-translated.png)

## 7. Redraw the font

mapchar does not edit graphics. Until the font is redrawn, the game draws
`ДОБИЙ ЙОГО!` as `DOSNJ JOGO!`.

1. The font is not plain tiles in the ROM: the graphics are RNC-packed (29
   `RNC` `02` streams). **View ▸ Decompressed View…** (Ctrl+Shift+D), then
   **Structures ▸ Find All**, lists them. It is the stream at `$AC54` — 1,951
   bytes packed, 3,056 unpacked: 191 Game Boy 2bpp tiles, a blank, `0`–`9`,
   then `A`–`Z`.
2. Unpack that stream, edit it in a tile editor (celPix, YY-CHR), and repack
   it to 1,951 bytes or fewer.
3. Finishes and Fighter names are drawn by different routines; check both on
   screen.
4. Redraw 14 tiles: `D F G J L N Q R S U V W X Z` →
   `Д Ф Г Й Л И Ч Я Б П Ж Ш Ї З`.
5. Every untranslated string shares the font and turns to gibberish.

## 8. Translate the strings

Open **Finishes** and set **Table** to **@mk2-translated**.

![Finishes through the translated table](images/08-finishes-new-table.png)

The bytes are unchanged, so every string is still *untouched*; they only
decode as Cyrillic now.

Double-click a **Translation** cell and type. Enter commits and moves on.

| Original | Translation |
| --- | --- |
| FINISH HIM! | ДОБИЙ ЙОГО! |
| FINISH HER! | ДОБИЙ ЇЇ! |
| FLAWLESS VICTORY | ЧИСТА ПЕРЕМОГА |
| DOUBLE FLAWLESS | ПОДВІЙНА ЧИСТА |
| DRAW | ПАТ |
| FATALITY | ФАТАЛІТІ |
| BABALITY!! | БАБАЛІТІ!! |

A text longer than its slot is refused — `НІЧИЯ` for `DRAW`:

![A refused edit](images/08-finishes-refused.png)

![Finishes, translated](images/08-finishes-translated.png)

Do the same for **Fighter names**, keeping each `[end]`:

| Original | Translation | | Original | Translation |
| --- | --- | --- | --- | --- |
| KANG | КАНГ | | SCORPION | СКОРПІОН |
| ZERO | ЗІРО | | JAX | ДЖАКС |
| KITANA | КІТАНА | | KINTARO | КІНТАРО |
| REPTILE | РЕПТАЙЛ | | KAHN | КАН |
| SHANG | ШАНГ | | SMOKE | СМОУК |
| MILEENA | МІЛІНА | | JADE | ДЖЕЙД |

Packed strings share room: shorten some first, then `ДЖАКС` and `ДЖЕЙД` fit.

![Fighter names, translated](images/08-names-translated.png)

## 9. Write the ROM

**File ▸ Write All** (Ctrl+Shift+W), then **File ▸ Save Project** (Ctrl+S).

![After Write All](images/09-written.png)

Through `mk2`, the ROM shows what was written — moved names, rewritten
pointers:

![The ROM after writing](images/09-rom-after.png)

Test in an emulator.
