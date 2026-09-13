# abcde bin2text

`Table::bin2text` (`../abcde/abcde/Table/Table.pm`) turns a bit string into a
token list. It is recursive: each table switch calls `bin2text` on the
destination table. `BasicString::parse` calls it once per string. With
`stringsPerPointer > 1`, it calls it that many times in a row, restarting in
the start table at the previous end position and concatenating the token lists.

## Interface

```
bin2text(table, \$bits, startPos, \%options, previousTable) -> (tokens, endPos, endTokensSeen)
```

| Option                     | Set by                                | Effect                                                                 |
|----------------------------|---------------------------------------|------------------------------------------------------------------------|
| `endAddress`               | `#SCRIPT STOP`, fixed lengths, next pointer, raw switches | Bit position the read window is cut at                  |
| `endTokenTerminated`       | pointer methods                       | End tokens stop translation and trigger realignment                    |
| `numTokens`                | switch params                         | Array of counters; reaching exactly 0 stops the table                  |
| `param`                    | switch params                         | The param that entered this table (its `FFToken`)                      |
| `autoJumps`                | `#AUTO JUMP START/STOP`               | `[{start, stop}]` in bits: reading bit `start` continues at bit `stop` |
| `stringEndReAlignMultiple`, `stringEndReAlignOffset` | `#STRING END REALIGN *` | Position rounding after an end token                   |

## Loop

Repeat until a step returns:

1. **Window.** Take `maxBinLength` bits from `curPos`, or 8 bits if the table
   has no entries. With auto-jumps, any jump whose `start` falls inside the
   window splices bits from `stop` onwards. In Cartographer mode (`errorEOF`),
   a window that would extend past the end of the data is fatal, even when a
   shorter token would fit. A dump that reaches the exact end of the file
   therefore dies (*observed*).
2. **Cut** the window at `endAddress`, if defined.
3. **Empty window**: return.
4. **Match**, taking the first rule that applies:
   1. If `param.FFToken` exists and the window starts with its bits, the token
      is the `FF` token.
   2. Otherwise, the longest entry of this table that prefixes the window
      (`binPat`).
   3. Otherwise, if `curPos` is at the end of the data, return.
   4. Otherwise, the token is `rawTableSwitchToken`: one raw match, handled
      like a switch in step 8.
5. **Append** the token and advance `curPos` by its length, following
   auto-jumps.
6. **Count**: subtract the token's `numTokens` from every counter in
   `numTokens`.
7. If the token is this table's `FF` token, return.
8. **Switch.** For each param of the token, in order:
   - `-1`: append `FF` and return from this table. At the top level this ends
     the whole translation.
   - If `curPos` is at the end of the data: warn *"binary string ended before
     all control code parameters could be matched"* and skip the remaining
     params.
   - Otherwise build the child's options as a copy of this table's options:
     - `param` is this param.
     - `numTokens` is a new array holding `requiredCount`, or this table's
       array with `requiredCount` pushed onto it when the param has `+`. When
       `requiredCount` is `0`, which includes `$hex`/`%bin` params, the copy
       keeps **this table's array unchanged**. The cause is Perl precedence in
       `$x = (...), push(...) if $count`.
     - For the raw table without `FFToken`, `endAddress` becomes
       `curPos + 8·count + 1`; for the raw bit table, `curPos + count + 1`.
       Here `count` is `requiredCount // 1`, so a count of `0` gives a 1-bit
       window. This replaces any tighter outer `endAddress`.
   - Recurse. Add the child's `endTokensSeen` to this table's, and append the
     `S` token followed by the child's tokens.
9. **End token**: if `endTokenTerminated` and the token is an end token,
   increment `endTokensSeen`. If realignment is set, set
   `curPos = M · ceil((curPos − O) / M) + O`, in bits.
10. If `endTokenTerminated` and `endTokensSeen > 0`, return. A child's end
    token therefore unwinds every level.
11. If any counter is exactly 0, append `FC` and return.

The text of a token list is the concatenation of `token.text`. Labels come from
switch tokens, and `S`/`FC`/`FF`/`FI` contribute nothing.

## Switch semantics

What each param form actually does:

| Param         | Child stops when                                                                                     |
|---------------|------------------------------------------------------------------------------------------------------|
| `N`           | its counter reaches exactly 0. A weight that jumps past 0 means it never stops by count (*observed*). |
| `0`           | the data or `endAddress` ends, an end token appears (pointer methods), or a `-1` switch is matched in the child. It also stops when **this table's** counter reaches 0, because the counter array is shared. |
| `$hex`/`%bin` | as `0`, and when the fallback bits appear at a token boundary. They take priority over table entries.  |
| `-1`          | immediately; the table holding the switch token returns.                                             |
| `+`           | as its match form, but its matches also decrement this table's counters. Its own exhausted counter stays in the shared array, so **this table stops right after the child finishes by count** (*observed*). |

Each table's own match counting has these rules:

- The switch token counts towards its own table's counters before its params
  run.
- A switch token that brings a count to 0 still runs its params before `FC`.
- Unmatched data never counts. The raw fallback token weighs 0, and its raw
  child gets a fresh counter.

Unmatched data is dumped as `<$XX>`: 8 bits, whatever the table's entry width,
or `<%b>` single bits when the window is shorter. Because the raw switch
replaces `endAddress`, this raw read can extend up to 8 bits past a string's
end address (*observed*: `%11=a` over `C0 3F` with `#SCRIPT STOP: 1` dumps
`a<$00>`).

### Worked examples

Data `AB 01 02 AB 03`. Table `@main` holds `01=foo 02=bar 03=cat` plus the `AB`
entry shown. `@ItemNames` holds `01=[Potion] 02=[HolyHandGrenadeOfAntioch]
03=[Sword]`. `@FontNames` holds `!01=,<@ItemNames>:1+ 02=[Green]
03<2>=[Batman]`. All outputs are observed.

| `AB` entry in `@main`                                  | Output                                                        |
|--------------------------------------------------------|---------------------------------------------------------------|
| `AB=[line]`                                            | `[line]foobar[line]cat`                                       |
| `!AB=<[Item Name:]>,1`                                 | `[Item Name:]<$01>bar[Item Name:]<$03>`                       |
| `!AB=<[Item Name:]>,<@ItemNames>:1`                    | `[Item Name:][Potion]bar[Item Name:][Sword]`                  |
| `!AB=<[Item Name/Font:]>,<@ItemNames>:1,<@FontNames>:1` | `[Item Name/Font:][Potion][Green][Item Name/Font:][Sword]`, plus the params-ended warning |
| `!AB=<[Window, X/Y]>,3` or `,1,1,1`                    | `[Window, X/Y]<$01><$02><$AB>cat`                             |
| `!AB=<[page]>,<@ItemNames>:$AB` or `:%10101011`        | `[page][Potion][HolyHandGrenadeOfAntioch]cat`                 |
| `!AB=,<@FontNames>:2`                                  | `[HolyHandGrenadeOfAntioch][Batman]`                          |
| `/AB=[end]` (plain mode)                               | `[end]foobar[end]cat`                                         |
| `!AB=<[x]>,0`                                          | `[x]<%0>`, then decoding is 1 bit out of phase                |
| `!AB=<[x]>,-1`                                         | `[x]`, and translation ends                                   |

Counter sharing, with data `AB 01 02 02 02 05`. `@P` holds
`!01=,<@ItemNames>:0 05=[five]`, and `@main` holds
`!AB=<[p]>,<@P>:3 02=bar 05=v`. The output is
`[p][HolyHandGrenadeOfAntioch][HolyHandGrenadeOfAntioch]barv`: the switch token
plus two `@ItemNames` matches use up `@P`'s count of 3.

## Defects

- Counter sharing for `0`/`$hex`/`%bin` params, and a `+` child terminating its
  parent. Both are shown above.
- A count is never reached when weights overshoot it.
- A raw count of `0` means 1 bit. The raw switch overrides the outer
  `endAddress`.
- End-of-data detection in Cartographer mode is a fatal error instead of a
  shorter read.
- The readme's `[Item Name/Font:]` example no longer matches the output shown
  above.

## Replication notes

The intended model is a stack machine:

- **Match**: longest-prefix match in the current table, with the parent's
  fallback bits checked first.
- **Switch**: a switch pushes one frame per param. Each frame has a destination
  table, a stop condition (count, unlimited, fallback bits, or immediate) and
  an optional "count towards parent" flag.
- **Pop**: a frame pops when its stop condition holds.
- **End**: an end token in pointer mode unwinds everything.

Give each frame its own counter, and propagate `+` matches as increments to the
parent rather than by sharing arrays. Stop on `count <= 0`. Bound raw reads by
the tighter of the frame and string limits.
