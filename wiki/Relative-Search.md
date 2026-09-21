# Relative Search

A relative search finds text in a ROM when you do not have a table yet.

You type a word that the game shows. In most fonts the letters are stored in
order, so `B` is one more than `A` and `C` is one more than `B`. mapchar looks
for bytes that differ from each other by the same amounts as the letters of
your word. For each match, it reports which byte is `A`.

Open **Search ▸ Search Window…** (Ctrl+Shift+F). The search covers the whole
file that is open in the window.

## 1. Type a word

Type a word that you know the game shows, and press Enter.

Use a long word with no repeated letters. `KITANA` is a good word. `OK` is not:
a word of two or three letters matches in too many places.

The word can contain:

- letters, `A-Z` or `a-z`
- digits, `0-9`
- kana, あ-ん or ア-ン, in gojūon order and without dakuten. For example, type
  `ハ` where the game shows `バ`.
- `?`, which matches any one character

No other characters are allowed. If the word contains a space, a comma or an
accented letter, the search is refused with a message. Leave these characters
out. For example, for `ROUND 1`, search for `ROUND` or for `1`.

Upper case and lower case letters are separate sequences, so a word such as
`Hello` is allowed.

## 2. The settings

**Width** is the number of bytes in one character. Start with **8-bit**. If
nothing is found, try **8 and 16-bit**. This setting is slower.

**Case gap** controls how upper case and lower case relate. When it is on, the
two sequences can be any distance apart. When it is off, `a` must be exactly 26
more than `A`. Leave it on.

**Limit** is the maximum number of results to keep. When the search reaches the
limit, it stops and shows a message. A common word reaches the limit quickly.
In that case, choose a better word instead of raising the limit.

**Stop** ends a long search and keeps the results found so far.

## 3. Read the results

![Relative search for KITANA](images/02-relative-search.png)

| Column | Meaning |
| --- | --- |
| **Offset** | The address where the match starts, in hex. |
| **Width** | The number of bytes per character. For 16-bit, also the byte order. |
| **Bytes** | The bytes that matched. |
| **Bases** | The byte of the first character of each sequence: `upper` is `A`, `lower` is `a`, `digit` is `0`, `hiragana` is `あ`, `katakana` is `ア`. |

**Bases** is the result you need. `upper=41` means that `A` is `41`, `B` is
`42`, and so on.

Click a row to see its bytes in the raw view.

Most results are false matches: bytes that are not text but differ by the same
amounts. To find the correct result, check two things:

- The same **Bases** value appears in several results.
- The bytes around the match also look like text.

For example, `upper=41` in four results is the font. `upper=9C` in one result
is a false match.

## 4. Build a table from a hit

Select the correct result and click **Build Table from Hit**.

A dialog asks which alphabets to add. Choose the sequences that your word
identified, or choose **Custom…** and type the characters in the order the ROM
stores them. Each character becomes a table entry, numbered upward from the
base.

If a table is already open, mapchar offers to add the entries to it. Click
**No** to create a new table instead.

The new table is not complete. Punctuation, the space, and the byte that ends a
string are not part of any sequence. Add them in the Table Editor.

## Nothing found

- Try another word. The game may draw that word as a picture, or compress it.
- Try **8 and 16-bit**.
- A relative search cannot find compressed text. Use
  **Search ▸ Scan for Text…** instead.
