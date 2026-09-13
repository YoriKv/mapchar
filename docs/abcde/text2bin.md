# abcde text2bin

`Table::text2bin` (`../abcde/abcde/Table/Table.pm`) with
`AStarNode::discoverNodes` (`AStarNode.pm`) encodes a text string as a token
list. The search looks for a low-bit-cost tokenisation that a longest-prefix
reader decodes back to the same text. The caller strips line breaks first;
table text has its `\n` removed at load time in this mode.

## Search

- **State** (node) holds:
  - `pos`: characters consumed
  - `tableStack`: frames, top first
  - `token`: the step taken to reach this node
  - `parent`
  - `paidCost`: sum of `token.binLength`
  - `tablesSincePosChange`: consecutive zero-width steps
- **Frame** holds `table`, `requiredCount`, `currentCount` (undefined means
  "not yet entered"), `FFToken`, `addNumTokens` and `paramTable`. The root frame
  is `{start table, required 0, current 0}`.
- **State key** (`stackStr`): one piece per frame,
  `fileName,requiredCount,(currentCount, or 0 if required ≤ 0, or undef),FFbits:`.
- **Priority** `minCost = paidCost + minBinPerText × (length − pos)`. The
  heuristic is admissible: no token costs fewer bits per character.
- **Queue**: `Hash::PriorityQueue` pops the lowest priority, FIFO among equal
  priorities.
- **Main loop**: pop a node and generate its successors. For each successor:
  1. Skip it if `(pos, stackStr)` was already seen.
  2. If `pos` is the end of the text and either no frame is unfulfilled or the
     token is an end token, the path back to the root is the result. A frame is
     unfulfilled when `requiredCount` is non-zero and `currentCount` is
     undefined, 0, or less than `requiredCount`.
  3. Otherwise, mark the key seen and enqueue the node.
- **Failure**: *"unable to tokenize; best attempt failed at input position N"*,
  with 20 characters of context around the farthest position reached.
- **Output**: the token list; its bits are concatenated and packed MSB-first,
  zero-padding the last byte.

## Successors

Let `top = tableStack[0]` and `T = top.table`. Rules are evaluated in order; a
rule marked **only** returns its node alone and discards everything collected.

If `T.participatesInSwitching`:

1. **Count reached** (**only**): if `top.requiredCount` is non-zero and equals
   `top.currentCount`, the successor is `FC`, popping `top`, at the same `pos`.
2. **Parent count reached** (**only**): if a frame has `addNumTokens` and the
   frame below it has reached its count, the successor is `FC`, popping `top`.
3. **Entering a param** (**only**): if `top.currentCount` is undefined:
   - `requiredCount -1`: the successor is `FI`, popping the `-1` frame and the
     frame of the table that held the switch. At depth 1 there is no successor.
   - Otherwise: the successor is `paramTable.switchTokens{T}` (an `S` token),
     setting `currentCount = 0`.
4. **Forced fallback**: if depth > 1 and `top.FFToken` exists, the successor is
   the `FF` token (its bits), popping `top`.
5. **Unlabelled switches**: for each switch entry of `T` with empty text, while
   `tablesSincePosChange` < the number of loaded tables, the successor is that
   token with its `paramStack` pushed over the stack. The token counts: see
   below.

Then, for every table:

6. Find the entries of `T` whose text starts at `pos`; results are cached per
   table and position. If the character at `pos` is `<`, do the same for the
   raw table.
7. **Raw text** (**only**): at depth 1, if a raw token matches at `pos`, the
   successor is `S` into a frame `{raw table, required 1, current 0}`. Raw text
   is therefore encodable only at depth 1 or inside a raw-table frame.
8. **Text matches**, in `tokensByFirstChar` order (sorted by bits). For each
   match, add its `numTokens` to `top.currentCount`, and to each frame below for
   as long as the frame above it has `addNumTokens`. Then:
   - A labelled switch: successor at `pos + textLength` with `paramStack`
     pushed.
   - An end token (**only**): successor at `pos + textLength` with the stack
     reset to its bottom frame, whose zero-width counter restarts.
   - Anything else: successor at `pos + textLength`.
9. With no text match and depth > 1: no successor. The `FB` "fall back on no
   match" rule is commented out, so a `0` frame without fallback bits is left
   only through a `-1` switch or an end token.

`Node::create` increments `tablesSincePosChange` whenever `pos` does not change.
This bounds zero-width chains (`S`, `FC`, `FF`, `FI`, unlabelled switches) at
the number of loaded tables.

At the end of the text, a frame waiting for fallback bits or a `0` count counts
as fulfilled, so closing fallback bits (e.g. `$FF`) are not emitted. A count
frame must be exactly satisfied.

## Defects

All *observed*:

| Defect | Example |
|--------|---------|
| **Longest-prefix safety is missing.** `Node::create` never copies the token's own `unusableSuffixes` into the node, so every node's suffix set is empty and `is_token_usable` always returns true. Output can decode to different text. | Table `01=A 02=B 0102=X`: text `AB` encodes as `01 02`, which decodes as `X`. |
| **Not optimal.** A state key is closed when first *generated*, not when popped, so a cheaper path to the same key found later is dropped. The readme's "smallest possible binary" claim does not hold. | Table `01=a 02=b 03=c 040506=ab`: `abc` becomes `04 05 06 03` instead of `01 02 03`. |
| **An end-token match suppresses all alternatives** at that position. | Table `/FF=[end] FE=[end]x 78=x`: `[end]x` becomes `FF 78` instead of `FE`. |
| **State keys use the file name, not the table.** Two tables in one multi-table file are indistinguishable. | With `@main` holding `!01=,<@A>:0 !02=,<@B>:0`, `@A` holding `10=x !FF=,-1`, and `@B` holding `20=x 21=y !FF=,-1`: `xy` fails when `@A` and `@B` share a file, and gives `02 20 21` when they are separate files. |

The engine's rules also differ from [bin2text](bin2text.md#switch-semantics)'s:

- Counts propagate only through `+` frames; bin2text also shares counters with
  `0`/fallback frames.
- A `+` child finishing does not end its parent.
- End tokens always reset to the bottom frame; plain-mode bin2text ignores them.

Insertions using those features may not round-trip.

## Performance

Search time grows roughly linearly with text length for well-behaved tables
(*observed*). 40 000 characters over a 5-entry table take about 0.3 s. The
Battle of Olympus upper/lower/numbers tables encode 640 characters in about
0.07 s.

## Replication notes

To get correct, optimal encodings, run a shortest-path search over the state
`(pos, frame stack, pending suffix constraint)`:

- Key frames by table identity.
- Close a state when it is popped, and allow improvement before that
  (Dijkstra/A*).
- Carry the set of forbidden bit prefixes: after emitting a token that is a
  proper prefix of a longer entry, the following bits must not complete that
  entry.
- Keep end-token matches as ordinary alternatives.
- Model counters identically to the extraction engine, so every encoding
  round-trips through the decoder.

Verify each result by decoding it.
