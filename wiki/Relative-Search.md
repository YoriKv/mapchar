# Relative Search

A relative search finds text in a ROM before you have a table. You type a word
the game shows; mapchar finds the places where the bytes step the way the
letters do — `B` one more than `A`, `C` one more than `B` — and tells you what
byte `A` is.

**Search ▸ Search Window…** (Ctrl+Shift+F). It searches the whole of the file
open in the window.

## 1. Type a word

Type a word you are sure the game shows, and press Enter.

Pick a long one, with no repeated letter: `KITANA` says more than `OK`. A word
of two or three letters matches almost everywhere.

The word may hold:

- letters, `A-Z` or `a-z`
- digits, `0-9`
- kana, あ-ん or ア-ン, in gojūon order and without dakuten — type `ハ` where
  the game shows `バ`
- `?`, which matches any one character

Nothing else. A space, a comma or an accent is refused, and the search says so.
Leave them out: for `ROUND 1`, search `ROUND` or `1`.

Upper and lower case are different runs, so `Hello` is fine.

## 2. The settings

**Width** — how many bytes one character takes. Start with **8-bit**. If
nothing is found, try **8 and 16-bit**, which is slower.

**Case gap** — on, upper and lower case may sit anywhere apart from each
other; off, `a` must be exactly 26 after `A`. Leave it on.

**Limit** — how many hits to keep. At the limit the search stops and says so.
A common word reaches it; a better word is worth more than a higher limit.

**Stop** stops a long search and keeps what it found.

## 3. Read the results

![Relative search for KITANA](images/02-relative-search.png)

| Column | What it says |
| --- | --- |
| **Offset** | where the match starts, in hex |
| **Width** | bytes per character, and for 16-bit which end comes first |
| **Bytes** | the bytes that matched |
| **Bases** | the byte of the first character of each run: `upper` is `A`, `lower` is `a`, `digit` is `0`, `hiragana` is `あ`, `katakana` is `ア` |

**Bases** is the answer you came for. `upper=41` means `A` is `41`, `B` is
`42`, and so on.

Click a row to see those bytes in the raw view.

Most hits are wrong — any bytes that happen to step the right way. The right
one is the one whose **Bases** repeat across several hits, and whose
neighbours look like text too. `upper=41` found four times over is a font;
`upper=9C` found once is a coincidence.

## 4. Build a table from a hit

Select the right hit and press **Build Table from Hit**.

It asks which alphabets to lay out — the runs your word pinned down, or
**Custom…** to type the characters in the order the ROM has them. Each
character becomes an entry, counting up from the base.

If a table is already open it offers to add the entries to it. **No** makes a
new table instead.

That is a start, not a finished table. Punctuation, the space, and the byte
that ends a string are not in any run, so you add them yourself in the Table
Editor.

## Nothing found

- Try another word. The game may draw that one as a picture, or compress it.
- Try **8 and 16-bit**.
- If the text is compressed, no relative search will find it. Try
  **Search ▸ Scan for Text…** instead.
