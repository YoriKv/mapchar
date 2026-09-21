# Getting Started

This tutorial translates two blocks of text in **Mortal Kombat II** (Game Boy,
`Mortal Kombat II (USA, Europe).gb`) into Ukrainian. All addresses are file
offsets in hex.

> **Work on a copy of the ROM.** mapchar writes into the ROM file in place and
> keeps no backup.

## 1. Create the project and open the ROM

mapchar starts with an empty project. To start a new one later, use
**File ▸ New Project** (Ctrl+N).

![The empty window](images/01-empty-window.png)

Open the ROM with **File ▸ Open ROM…** (Ctrl+Shift+O). Then save the project
with **File ▸ Save Project** (Ctrl+S) as `MK2.mapchar`, in the same folder as
the ROM.

![The ROM, open](images/01-rom-open.png)

No table is loaded yet, so the file is shown as ASCII.

## 2. Build `mk2.tbl`

A table maps bytes to characters.

Open **Search ▸ Search Window…** (Ctrl+Shift+F) and run a
[relative search](Relative-Search) for `KITANA`. The result shows `upper=41`,
which means the letter `A` is the byte `41`.

![Relative search for KITANA](images/02-relative-search.png)

Choose **File ▸ New Table…** and save the table as `mk2.tbl`. The table opens
in the Table Editor.

![The empty table](images/02-table-empty.png)

Click **Fill…**, choose **A-Z**, and set the first key to `41`.

![Fill, A to Z from 41](images/02-fill-dialog.png)

Click **Fill…** again, choose **0-9**, and set the first key to `30`.

![Letters and digits filled](images/02-table-filled.png)

Add the remaining entries one at a time. For each entry, click **New**, type
the Key and the Text, and click **Add**.

| Key | Text |
| --- | --- |
| `20` | space |
| `21` | `!` |
| `2C` | `,` |
| `2E` | `.` |
| `3F` | `?` |
| `00` | `[end]`, **Kind: End** |

![The end entry](images/02-table-end-entry.png)

Click **Save**.

![mk2.tbl, finished](images/02-table-done.png)

## 3. Look for text through the table

Select **@mk2** in the **Table** list.

**Search ▸ Scan for Text…** (Ctrl+Shift+R) lists the regions that look like
text. For each region it shows how the strings end: with an **End token**, or
with a **Length prefix** and the number of header bytes before it. Select a row
to go to that region.

![The Scan window](images/03-scan.png)

**Find** (Ctrl+F) searches for hex bytes or for `"quoted text"`. Use it to find
the exact address of a string. In the **Text** tab, search for `"FINISH"`:

![Find, in the Text tab](images/03-find.png)

The match is at `$8649`. Each string in this region has three bytes before it.
The third byte is the length of the string.

## 4. A Strings block: Finishes

In the **Hex** tab, drag to select `$8646` to `$86A5`. The selection starts at
the three bytes before `FINISH HIM!` and ends at the last `!` of `BABALITY!!`.

![The region, selected](images/04-select-region.png)

Choose **File ▸ New Block** (Ctrl+Shift+B). Press F2 and rename the block to
`Finishes`.

![One long string](images/04-block-end-token.png)

The region contains no `00` byte, so the block reads it as one string. On the
Reading bar, set **Ends at** to **Length prefix** and **Prefix** to 1.

![Length prefix, no header yet](images/04-block-length-prefix.png)

Each record is two position bytes, a length byte, then text:

```
05 CB   0B   46 49 4E 49 53 48 20 48 49 4D 21
pos     len  FINISH HIM!
```

> The two bytes before each length byte are the screen position of the string.
> They are not text, so the block must skip them. If it does not, the first
> position byte is read as a length and every string is read incorrectly, as in
> the screenshot above.

Set **Header** to 2.

![Finishes, read correctly](images/04-finishes-strings.png)

**Original** shows the text as it was when the block was created.
**Translation** shows the text that is in the ROM now. This block is written
**slotted**: each string keeps its address and can only use its own space.

Ctrl+1, Ctrl+2 and Ctrl+3 show the block as **Hex**, **Text** and **Strings**.

| Hex | Text | Strings |
|:-:|:-:|:-:|
| [![Finishes in Hex](images/04-view-hex.png)](images/04-view-hex.png) | [![Finishes in Text](images/04-view-text.png)](images/04-view-text.png) | [![Finishes in Strings](images/04-view-strings.png)](images/04-view-strings.png) |

## 5. A Pointers block: Fighter names

Select the ROM in the Files panel. Go to `$8DE0` and select `$8E02` to `$8E4D`.

![The names, selected](images/05-select-names.png)

Choose **New Block** and rename the block to `Fighter names`.

![Fighter names as a range](images/05-names-range.png)

Open **Search ▸ Find Pointers…** (Ctrl+Shift+P). Leave the offsets at 0.

![Find Pointers: what to try](images/05-find-pointers-setup.png)

Select the first row and click **Use as Pointer Table**. The row is a table of
2-byte Game Boy pointers at `$8DE9`. The names are in bank 2, which the game
maps to address `$4000`. The pointer to `$8E02` therefore holds the value
`$4E02`.

![Find Pointers: the results](images/05-find-pointers-results.png)

![Fighter names as a pointer table](images/05-names-pointers.png)

The block is now written **packed**. The strings share all the space up to the
**Bound**, and the pointers are rewritten when strings move. By default, the
bound is the end of the last name.

These are the same three views. The Strings view is shown with
**Panels ▸ Hex** open:

| Hex | Text | Strings |
|:-:|:-:|:-:|
| [![The names in Hex](images/05-view-hex.png)](images/05-view-hex.png) | [![The names in Text](images/05-view-text.png)](images/05-view-text.png) | [![Strings with the Hex panel](images/05-view-strings.png)](images/05-view-strings.png) |

In Hex, **Show as Pointers** shows the pointer table instead of the strings it
points to.

![The pointer table in Hex](images/05-view-hex-pointers.png)

## 6. Build `mk2-translated.tbl`

The font has only 26 letter tiles, so the Cyrillic letters must use the codes
of the Latin letters. A Cyrillic letter that looks like a Latin letter keeps
that letter's code. The other 14 codes are assigned to new letters.

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

There is no room for `Ґ Є Х Ц Щ Ь Ю`.

Choose **File ▸ New Table…** and save the table as `mk2-translated.tbl`. Click
**Fill…**, choose **Custom…**, set the first key to `41`, and type the Cyrillic
letters in code order:

```
АВСДЕФГНІЙКЛМИОРЧЯБТПЖШЇУЗ
```

![Fill with a custom run](images/06-fill-cyrillic.png)

Add the digits, the punctuation and `[end]` as in step 2. Click **Save**.

![mk2-translated.tbl](images/06-table-translated.png)

## 7. Redraw the font

mapchar does not edit graphics. Until the font is redrawn, the game shows
`ДОБИЙ ЙОГО!` as `DOSNJ JOGO!`.

1. Find the font. The graphics in this ROM are compressed with RNC, in 29
   `RNC` `02` streams. To list them, open **View ▸ Decompressed View…**
   (Ctrl+Shift+D) and choose **Structures ▸ Find All**. The font is the stream
   at `$AC54`. It is 1,951 bytes compressed and 3,056 bytes decompressed. It
   holds 191 Game Boy 2bpp tiles: a blank tile, `0` to `9`, then `A` to `Z`.
2. Decompress the stream and edit it in a tile editor such as celPix or
   YY-CHR. Compress it again to 1,951 bytes or fewer.
3. Redraw these 14 tiles: `D F G J L N Q R S U V W X Z` become
   `Д Ф Г Й Л И Ч Я Б П Ж Ш Ї З`.
4. Check both blocks in the game. Finishes and Fighter names are drawn by
   different routines.
5. All text in the game uses this font. Any string that is not translated
   becomes unreadable.

## 8. Translate the strings

Open **Finishes** and set **Table** to **@mk2-translated**.

![Finishes through the translated table](images/08-finishes-new-table.png)

The bytes have not changed, so every string still has the status *untouched*.
The same bytes are now shown as Cyrillic letters.

Double-click a **Translation** cell and type the translation. Press Enter to
save it and move to the next string.

| Original | Translation |
| --- | --- |
| FINISH HIM! | ДОБИЙ ЙОГО! |
| FINISH HER! | ДОБИЙ ЇЇ! |
| FLAWLESS VICTORY | ЧИСТА ПЕРЕМОГА |
| DOUBLE FLAWLESS | ПОДВІЙНА ЧИСТА |
| DRAW | ПАТ |
| FATALITY | ФАТАЛІТІ |
| BABALITY!! | БАБАЛІТІ!! |

A translation that is longer than its slot is refused. For example, `НІЧИЯ`
does not fit in the slot of `DRAW`:

![A refused edit](images/08-finishes-refused.png)

![Finishes, translated](images/08-finishes-translated.png)

Translate **Fighter names** in the same way. Keep the `[end]` code at the end
of each name:

| Original | Translation | | Original | Translation |
| --- | --- | --- | --- | --- |
| KANG | КАНГ | | SCORPION | СКОРПІОН |
| ZERO | ЗІРО | | JAX | ДЖАКС |
| KITANA | КІТАНА | | KINTARO | КІНТАРО |
| REPTILE | РЕПТАЙЛ | | KAHN | КАН |
| SHANG | ШАНГ | | SMOKE | СМОУК |
| MILEENA | МІЛІНА | | JADE | ДЖЕЙД |

Packed strings share their space. `ДЖАКС` and `ДЖЕЙД` are longer than the
originals, so enter the shorter names first. The space they free is then
available.

![Fighter names, translated](images/08-names-translated.png)

## 9. Write the ROM

Choose **File ▸ Write All** (Ctrl+Shift+W), then **File ▸ Save Project**
(Ctrl+S).

![After Write All](images/09-written.png)

View the ROM through the `mk2` table to see what was written. The names have
moved and the pointers have new values:

![The ROM after writing](images/09-rom-after.png)

Test the ROM in an emulator.

Next: [Advanced Pointers](Advanced-Pointers).
