# Advanced Pointers

Three pointer layouts the [Getting Started](Getting-Started) blocks do not
cover, on the same ROM and project: pointers that sit inside code, one pointer
that reaches several strings, and pointers into length-prefixed records.
Everything here reads through **@mk2**.

## 1. Pointers inside code: Link

Find `"PLAYER 1 HAS"` in the **Text** tab. Five link-cable messages sit
together at `$83E0`.

![The link messages](images/adv-1-find-text.png)

There is no pointer table for them. The game loads each address straight into
a register before it calls the print routine:

```
21 E0 43     ld hl,$43E0     ; PLAYER 1 HAS ENTERED
```

`21` is the Game Boy's *load HL* instruction, and the two bytes after it are an
ordinary pointer: bank 2's `$43E0` is file `$83E0`. So search for `21`, then
the string's address, low byte first. In the **Hex** tab, Find `21 E0 43`:

![The load instruction](images/adv-1-find-load.png)

The match is at `$976`, so the pointer is at `$977`. The same search for each
string:

| String | At | Find | Pointer |
| --- | --- | --- | --- |
| PLAYER 1 HAS ENTERED | `$83E0` | `21 E0 43` | `$977` |
| PLAYER 2 HAS ENTERED | `$83F5` | `21 F5 43` | `$97E` |
| THE TOURNAMENT ! | `$840A` | `21 0A 44` | `$989` |
| YOU ARE PLAYER 1 | `$841B` | `21 1B 44` | `$994` |
| YOU ARE PLAYER 2 | `$842C` | `21 2C 44` | `$99B` |

The five pointers have code between them, so no **Pointer table** holds them.
A **Pointer list** does: it names each pointer's address.

Set **Show as** to **Pointers**, select the two bytes at `$977`–`$978`, and
**New Block**. Rename it `Link`. It is a pointer table of one pointer.

On the Reading bar set **Source** to **Pointer list** and open **Addresses**.
It is a list, a row an address: **Add** appends one, **Remove** drops the row
picked. A row typed with several addresses in it, separated by commas, spreads
over a row each, so the whole list goes in at once:

```
$977, $97E, $989, $994, $99B
```

| One pointer | The list | In Hex |
|:-:|:-:|:-:|
| [![A table of one pointer](images/adv-1-one-pointer.png)](images/adv-1-one-pointer.png) | [![Link as a pointer list](images/adv-1-link.png)](images/adv-1-link.png) | [![The five pointers, in the code](images/adv-1-link-pointers.png)](images/adv-1-link-pointers.png) |

Writing is **packed**, as for any pointer block: the strings share their room,
and a string that moves has its operand rewritten inside the instruction.

> Only the two bytes after `21` are the pointer. Check every address in Hex:
> a list entry that is off by one rewrites the instruction itself.

## 2. One pointer, several strings: Main menu

Find `"START GAME"`: it is at `$8141`, with `OPTIONS` right behind it at
`$814C`.

![The main menu's strings](images/adv-2-find-text.png)

Find `21 41 41` gives the pointer to `START GAME`, at `$310`. Find `21 4C 41`
gives nothing: no pointer reaches `OPTIONS`. The print routine stops at `00`
and leaves its position on the next byte, so the game prints `OPTIONS` by
calling it again without loading an address.

Make a block over the pointer at `$310`–`$311` as in step 1, and rename it
`Main menu`. It reads one string and stops at the first `[end]`, which leaves
`OPTIONS` outside the block and `START GAME` with no room to grow: the block's
room ends at `$814C`, where `OPTIONS` begins.

Set **Ends per string** to 2. The string now runs through two end tokens.

| Ends per string 1 | Ends per string 2 |
|:-:|:-:|
| [![One end per string](images/adv-2-one-end.png)](images/adv-2-one-end.png) | [![Two ends per string](images/adv-2-two-ends.png)](images/adv-2-two-ends.png) |

Translate it as one string and keep both `[end]` codes. The two texts share
the room, so one may grow by what the other gives up.

The options menu is the same with three: the pointer at `$367` reaches
`OPTIONS[end]CREDITS[end]DIFFICULTY[end]`.

## 3. Pointers into records: Winners

The winner messages are records like [Finishes](Getting-Started#4-a-strings-block-finishes)
— two position bytes, a length byte, then text — but the game picks one by
fighter, so they also have a pointer table. It sits right before them, at
`$857B`: twelve 2-byte pointers, the first holding `$4593`, which is file
`$8593`.

With **Show as** on **Pointers**, select `$857B`–`$8592`:

![The winners' pointer table](images/adv-3-select-table.png)

**New Block**, rename it `Winners`. Every string runs on through the records
after it, since nothing here ends in `00`. Set **Ends at** to **Length
prefix**, **Prefix** 1, and it is still wrong:

```
03 CB   0D   4C 49 55 20 4B 41 4E 47 20 57 49 4E 53
pos     len  LIU KANG WINS
^ the pointer lands here
```

Each pointer reaches the start of its record, so the first position byte is
read as the length. A range block steps over that with **Header**; a pointer
block has no header, and moves where its pointers land instead. Set **Offset**
to 2: it is added to every pointer value, which puts each one on its length
byte.

| End token | Length prefix | Offset 2 |
|:-:|:-:|:-:|
| [![Read to the end token](images/adv-3-no-offset.png)](images/adv-3-no-offset.png) | [![The position read as a length](images/adv-3-length-prefix.png)](images/adv-3-length-prefix.png) | [![Landing on the length byte](images/adv-3-offset.png)](images/adv-3-offset.png) |

One setting is left. A pointer block writes **packed**, and packing slides each
string up against the one before it — over the position bytes between them.
Open **Writing** and set **Write** to **Slotted**, so every string keeps its
address and its own room:

![Winners, slotted](images/adv-3-winners.png)

**Bytes** now reads `14 / 14` rather than room shared with the other records:
a translation can be shorter than the original, never longer.
