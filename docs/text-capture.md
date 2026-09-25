# Text capture from a running game

Exploration of a feature not yet built: run the ROM in an emulator, watch its
text engine through the emulator's debugging hooks, and turn what it reads and
draws into blocks, pointers and table entries in the open project. This doc
holds the ideas, what is known about the emulators, and the findings of each
experiment. Nothing here is in `src/` yet, and nothing here is yet part of
[plan/](plan/README.md).

## Constraints

- **Static code in mapchar**, not an agent-driven workflow: the probe script,
  the transport and the inference ship with the app.
- **A bridge, never an embedded emulator.** mapchar drives an emulator it
  launches and talks to its scripting layer, so each platform can use the
  emulator that suits it (Mesen2 for SNES/NES/GB/GBA/PCE/SMS/WS, others
  later). No emulator core is linked in; Mesen is GPL-3 and mapchar is MIT.
- **Qt-free core.** Transport, evidence model and inference live outside
  `mapchar.ui`; only the review and capture windows are Qt.
- **Game data stays out of the repository.** Captures, screenshots and
  savestates are game data: they may live beside a user's project, never in
  tests or fixtures.

## Shape

```
emulator + probe script ──transport──▶ capture engine ──▶ evidence ──▶ existing engines ──▶ proposals ──▶ review UI ──▶ project
 (per emulator, Lua)                    (Qt-free)                        (textmatch, relsearch,             (blocks, pointer
                                                                          scan, pointer discovery,            tables, table
                                                                          decode)                             entries, context)
```

- **Probe** — a script mapchar writes and launches the emulator with. It
  knows one emulator's API and one console's hardware, and emits
  console-neutral **evidence events**.
- **Evidence** — what the probe saw, in file coordinates where it can be:
  read runs (ROM offsets read in order, by one reading instruction, with
  frame stamps), pointer candidates, glyph draws (byte value → tile → screen
  cell), code effects, screenshots and savestates. mapchar's engines do the
  interpretation; the probe only observes.
- **Probe plugins** — one per emulator × console, through the plugin system
  ([architecture.md §5](plan/architecture.md)), so a new platform is a new
  plugin, not a new feature.

## Mesen2 facts

Checked against the MesenCE 2.2.1 source (`../MesenCE`) and the working trace
harness in `../yi-shiny/trace-harness` (its README records the traps below in
detail). The API's source of truth is
`UI/Debugger/Documentation/LuaDocumentation.json` and
`Core/Debugger/LuaApi.cpp` (`GetLibrary()`); the public docs omit parameters.

**What the Lua API offers:** `addMemoryCallback(fn, type, start, end, cpuType,
memType)` for read / write / exec; `addEventCallback` (`startFrame`,
`endFrame`, `nmi`, `irq`, `inputPolled`, `stateLoaded`, …); `read`/`write`
on any memory type, ROM included; `convertAddress`; `getState` (a flat table
of dotted keys: `cpu.a`, `ppu.*`, `frameCount`); `getAccessCounters`,
`getCdlData`; `getScreenBuffer`, `takeScreenshot`, draw calls,
`getMouseState`, `isKeyPressed`, `getInput`/`setInput`; `createSavestate` /
`loadSavestate`; `step`, `breakExecution`, `stop`.

**Launch:**

- `Mesen.exe <rom> <script.lua>` loads both; `--testRunner` runs the script
  headless at full speed until `emu.stop(code)`; `--enablestdout` sends
  `print()` to stdout.
- Settings are overridable per launch as `--section.key=value`
  (`ConfigManager.ProcessSwitch`), so mapchar never edits the user's
  `settings.json`: `--debug.scriptWindow.allowIoOsAccess=true`,
  `--debug.scriptWindow.allowNetworkAccess=true`, `--doNotSaveSettings`.
- `Preferences.SingleInstance` defaults to true: a second launch hands its
  ROM to the open window. Whether `--preferences.singleInstance=false` takes
  effect before the instance check is unverified.
- A GUI instance can only be closed from outside (`emu.stop` exits only under
  `--testRunner`); the harness kills by matching the process command line.
- Many instances run concurrently, which parallel headless sweeps rely on.
- `--testRunner` never opens a window on any OS: `TestRunner.Run`
  initialises the core with no audio, video or input and no UI at all.

**Builds:**

- **Windows** — `../mesen/Mesen.exe` (2.2.1), the reference: it is what
  mapchar's users run beside it. From WSL it is launched through interop
  with Windows paths (`wslpath -w`).
- **Linux** — the single-file build from `../mesen-linux/Mesen`, installed
  at `~/.local/opt/mesen/Mesen` and linked as `~/.local/bin/mesen`. Its home
  is `~/.config/MesenCE`, where the first run unpacks `MesenCore.so`, Skia
  and HarfBuzz and writes `settings.json`. It needs `libSDL2` (installed).
  Run it headless — `--testRunner` with `DISPLAY` and `WAYLAND_DISPLAY`
  unset — so nothing reaches WSLg. It takes Linux paths, runs as fast as the
  Windows build, and six concurrent instances ran clean. It is for
  WSL-side tests: a TCP peer in WSL reaches it without the Windows relay.
  One early run hung with no script output until the timeout and has not
  recurred in a dozen since.
- Output written to `/mnt/d` from Linux is slow: a 2.5 MB log added 6 s to a
  14 s run. Heavy output goes to a Linux path or a pipe.

**Memory callbacks:**

- An absolute memory type (`snesPrgRom`, `snesWorkRam`) matches the callback
  against the absolute address, so a `snesPrgRom` callback's range is given in
  ROM offsets and catches the ROM through every mirror. A relative one (`snesMemory`)
  matches the full 24-bit bus address, so an I/O register is a different
  address in every bank: `$2118` must be hooked in every bank a routine may
  run with.
- A callback receives `(address, value)`, and the address is always the
  **bus** address (`relAddr`), even on an absolute memory type: a
  `snesPrgRom` callback reports `$05A5D9`, not `$2A5D9`. mapchar maps it.
- Read callbacks fire for `Read`, `DmaRead`, `DummyRead` and
  `PpuRenderingRead`; opcode and operand fetches are exec, not read, so a
  read callback on ROM sees data reads only (`ScriptManager.h:41`).
- DMA reads and writes pass through `ProcessMemoryRead/Write`, so they reach
  script callbacks. A DMA's B-bus write is reported at `$002118`-style
  addresses whatever the A-bus bank (`SnesDmaController.cpp` builds
  `0x2100 | reg` as a `uint16_t`); a CPU `STA $2118` is reported in the bank
  it ran with.
- **SNES VRAM / CGRAM / OAM callbacks never fire**, reads or writes, CPU or
  DMA. The PPU hook does dispatch to scripts (`Debugger.cpp:447`), but the
  callback is matched against `GetAbsoluteAddress`, which
  `SnesConsole::GetAbsoluteAddress` (`SnesConsole.cpp:449`) answers with
  `{-1, None}` for those types, so no match ever succeeds. PCE fails the same
  way; NES works, its PPU memory being a relative type. VRAM traffic is seen
  at the ports (`$2115–$2119`) and the DMA registers (`$420B`,
  `$43x0–$43xA`) instead, or after the fact through the access counters.
- Coprocessors need their own `cpuType` and `memType` (`sa1`, `gsu` with
  `gsuMemory`); a callback with the wrong CPU silently never fires.
- `createSavestate` / `loadSavestate` only work inside a main-CPU exec
  callback, not from a frame event.

**Access counters** keep, for every byte of every memory type, read / write /
exec counts and the master clock of the last read / write / exec, and they
record PPU writes too (`SnesDebugger::ProcessPpuWrite`; a VRAM byte address
is the word address × 2; a write the PPU blocks outside vblank is not
counted). `getAccessCounters(LastWriteClock, snesVideoRam)`
therefore says which VRAM bytes changed since a moment, and
`LastReadClock` over `snesPrgRom` which ROM bytes were read since it — with
no callback running during play. Each call builds a Lua table as big as the
memory, so it suits a query on demand, not every frame.

**CDL:** the code/data logger marks every ROM byte read as data or executed as
code; `getCdlData` returns it and Mesen saves it as a `.cdl` file.

## Transport

- **stdout** (`--enablestdout`, `print`) — one way, needs no permissions,
  proven by the harness. Enough for headless runs.
- **TCP via luasocket** (built into Mesen, gated by the network switch) —
  both ways, for a live session: mapchar sends commands (arm, hotkey
  results, poke a message id, load a state).
- **Files** (needs the I/O switch) — a command file polled each frame as a
  fallback.

## Tiers

| Tier | What | Notes |
|---|---|---|
| 0 | **Files, no bridge.** Import a Mesen `.cdl`: mask the Scan to data-read bytes, shade them in the Hex panel. Export blocks as Mesen labels. | Cheapest, useful at once. |
| 1 | **Live bridge.** mapchar launches Mesen with its probe; evidence streams over TCP while the user plays. | The main path. |
| 2 | **Record, replay.** Play is recorded (movie + savestate ring); mapchar replays chosen stretches headless with heavy instrumentation, in parallel instances. | Play never slows; costly analysis runs only where needed. |

## Unassisted ideas

- **Read runs.** A text engine is one instruction (`LDA [$00],Y`) walking
  ROM a byte at a time, usually over many frames (typewriter), with stops for
  input. Group data reads by the reading PC into ascending runs; a run's last
  byte suggests the end token. Timing tells text from decompression, which
  reads in dense single-frame bursts.
- **Pointer backtrace.** Just before a run starts, its address was stored in
  direct page from ROM bytes read moments earlier. Match recent reads against
  the run's start under each mapping: that is the pointer table, its entry and
  stride. Pointer discovery extends the table both ways.
- **Engine profile.** Once a reading PC is known to be the text reader, every
  later run from it is text. The project keeps the profile (reader PC,
  pointer-setup PC, table), so later sessions start precise.
- **Glyph path.** After a byte is read, the tilemap word written for it
  (through `$2118/9` or DMA) gives byte → tile; the tile's pixels are the
  glyph. For a variable-width font, the ROM read of the glyph bitmap at
  `font + code × size` gives the code directly — the same method covers
  tilemap, sprite and VWF text.
- **Glyph → character without OCR:** match glyphs against characters drawn in
  a system font; solve the byte → glyph substitution from language statistics
  over all captured runs; or lean on font order, as relative search does.
- **Code effects from behaviour:** the next tile lands a row down (newline);
  the reader stalls until a button is polled (pause); the box's cells are
  cleared (page); palette bits change (colour); the reader jumps elsewhere and
  returns (name insert, or a DTE/MTE dictionary entry). These map onto table
  effects directly.
- **Buffered text.** When the run is in WRAM, find the ROM reads of the
  routine that filled that buffer: the compressed source, for the
  Decompressed View and a decompressed block.
- **Sightings.** Each run keeps its range, reader, pointer, a screenshot crop
  and a savestate. A review panel proposes blocks; accepted strings keep their
  context — a translator sees the string in game, and a click loads its state.
  A block can show how much of it has been seen in play.

## Assisted ideas

- **"Text is on screen" hotkey.** Everything read and drawn since a moment is
  one `LastReadClock` / `LastWriteClock` query away, so the hotkey costs
  nothing during play: rank the recent runs, the user confirms one, the probe
  saves the screenshot and savestate with it.
- **Text on / text off.** Two keys bracket a box's life; reads that also
  happen outside the bracket are noise. Over a few boxes the reader PC is what
  survives — a cheat search for the text engine.
- **Type what you see.** The user types the visible line; relative search runs
  over only the bytes read in the last seconds, or a WRAM snapshot, and Build
  table fills the characters. Least new code, handles kana.
- **Draw a box.** The user drags a rectangle over the game. PPU state (BG
  mode, scroll, tilemap base) turns it into tilemap cells; the VRAM access
  counters say when they were written; a replay from the last savestate with
  port and DMA hooks says by what, and from which run.
- **Label the font once.** The captured font tiles shown as a grid; the user
  labels them; the byte → tile map turns the labels into a table.

## Beyond capture

- **Message sweep.** With a profile known: from a savestate with a box open,
  write each message id, let the box draw, screenshot, roll back — the
  harness's `vanilla-msgbox` and rollback-sweep pattern. Real screenshots of
  every string, and with a translation patched in, real overflow.
- **Live patch.** `emu.write` on the ROM puts an edited string into the
  running game at once.
- **Box location** from the tilemap alone: the cells that differ from the
  layer's most common tile are the box (the harness's `msgbox_probe.lua`).

## Risks

- Lua speed on every ROM data read — to measure first; tier 2 is the answer
  if it is too slow.
- Noise: level data, music, graphics decompression.
- SA-1 and SuperFX games read text on a coprocessor.
- Text built at runtime (numbers, names).
- The two script switches must be passed on every launch.

## Test games

- **Super Mario World** — the known answer is in
  `tools/samples/Super Mario World/`: message boxes with a relative pointer
  table at `$2A5A7`, text `$2A5D9–$2B0FF`, table `messages.tbl`; level names.
- **Zelda: A Link to the Past** — dictionary-compressed dialogue.
- **Yoshi's Island (Japanese)** — kana, SuperFX; the yi-shiny harness already
  opens its message boxes.

## Experiments

Spike scripts live in `tmp/capture-spike/` (scratch, gitignored): `probe.lua`,
`sweep_ext.lua`, `vram_ext.lua`, `hotkey.lua`, `run.sh` (`MESEN=linux` for the
Linux build, `EXTRA=` adds switches), `analyze.py`,
`backtrace.py`, `glyph.py`, `bridge.lua`, `bridge_server.py`, `wram_ext.lua`,
`prov_ext.lua`, `dict.py`, `sweep_alttp.lua`, `dict2.py`; `run.sh` takes `ROM=`, `DRIVE=false`, `FRAMES=`, `SHOTS=`.

### 1. Read runs and pointer backtrace — Super Mario World

**Setup.** Headless Mesen 2.2.1 launched straight from WSL
(`Mesen.exe --testRunner --enablestdout --doNotSaveSettings
--debug.scriptWindow.allowIoOsAccess=true <rom> <lua>`, Windows paths), stdout
to a log; no PowerShell wrapper needed. The probe:

- hooks `read` on all of `snesPrgRom`, takes the reading instruction from
  `getCpuState(snes)` (`k`, `pc`; the PC is already past the operand, which
  still names the instruction uniquely);
- grows a run per reader while each read is the next address (or the same
  one), and prints runs of 3+ bytes with their bytes, frames, and the 12
  reads that came before the run's first byte;
- drives the menus with `setInput` from `inputPolled` (Start / A until game
  mode `$0E`), which reaches the intro level with its message box open by
  frame ~590.

A sweep extension (test scaffolding, not probe) shows every message: at the
message routine's entry it snapshots once, at the lookup it pokes the
translevel and trigger that select case *X*, and a few frames later rolls
back — 22 messages in ~500 frames.

**Findings.**

- The intro message is **one run** by one reader (`$05:B212`), 141 bytes at
  `$2A5D9`, decoding under the sample table to the full text. Across the
  sweep all 22 messages come from that reader. Runs split at some line ends
  (the routine steps back a byte to pad short lines); merging a reader's runs
  that touch within a frame gives exactly one sighting per message.
- **Pointer backtrace works from evidence alone.** For each sighting, pairs
  of consecutive-address reads by one PC in its context are candidate
  pointers; a relative hypothesis is `base = run start − value`. The
  hypothesis *16-bit relative, read by `$05:B1E3`, base `$2A5D9`* explains
  22 of 23 sightings, with pointer slots `$2A5A9–$2A5D3` at stride 2 — the
  sample's table is `$2A5A7–$2A5D9`, the missing ends being entries the
  sweep never selects. Pointer discovery would extend it.
- **Timing does not tell text here.** SMW reads a whole message in one frame
  and animates the box afterwards, so "slow reads" is a property of some
  engines, not a rule.
- **Table-free ranking is weak.** Bulk readers (DMA from ROM, the graphics
  decompressor) dominate the data reads; "read once in the session" plus
  byte entropy puts the message among title-screen tilemap and stripe runs
  of similar statistics. Telling text apart needs the glyph path, a user
  hint, or a known reader.
- **Cost.** SMW makes ~290 ROM data reads per frame in a level and 900 k over
  3000 frames. Per read: ~1.9 µs for an empty Lua callback, ~1 µs for
  `getCpuState`, ~2.5 µs for the spike's string-heavy bookkeeping. The whole
  probe ran 3000 frames in 14 s against a 9 s baseline (both incl. startup)
  — well inside real time. A game with far more data reads, or a
  decompression burst (28 k reads in one frame here), would stutter under a
  headed probe; bookkeeping in numbers instead of strings, and dropping known
  bulk readers, are the first levers.

### 2. Byte → tile → glyph — Super Mario World

**Setup.** `vram_ext.lua` on top of the probe and the sweep. VRAM callbacks
never fire, so it hooks writes to `$2115–$2119` in banks `$00–$3F` and
`$80–$BF` (128 small callbacks; DMA arrives at `$00:21xx`) and models the
word address register (`$2116/7`, increment step and timing from `$2115`).
At each frame end that touched VRAM it prints the touched words in
first-write order with their final values (`emu.read` on `snesVideoRam`
works), the layer setup from `getState` (0.2 ms; `ppu.bgMode`,
`ppu.layers[i].tilemapAddress` / `chrAddress` in words, `doubleWidth`,
`doubleHeight`, scrolls), and at two frames a whole-VRAM dump. `glyph.py`
does the rest.

**Findings.**

- **Changed, not touched.** SMW rewrites the status bar every frame with the
  same words; keeping only words whose value changes leaves the text box.
  The message lands on BG3 at `$50C7 + 32·row`, 18 cells a row, as words
  `$39xx`.
- **Byte → tile needs no table.** For each sighting, pair the run's bytes
  with the changed cells of the next frames at about the same relative
  position, vote on `tile − (byte & mask)`, and keep the rule whose mapped
  bytes form the longest common subsequence with the tiles. 20 of 21
  sightings give tile = `(byte & $7F) + $100` — the table's own structure
  (bit 7 marks a line end; attribute `$39` carries tile bit 8). What stays
  unexplained is a cell that already held the same character. The rule
  family `(b & mask) + k` covers fonts laid out by code; a lookup table from
  code to tile would need the equality-pattern alignment (a byte repeats
  exactly where its tile repeats), not yet tried.
- **Glyphs.** The text layer's char base and bit depth (mode 1 BG3: 2bpp)
  give each byte's 8×8 glyph from the VRAM dump; a sheet with each glyph at
  its byte value reproduces the sample table's layout (`$00` A … `$40` a).
  SMW's glyph cells are filled: ink is the colours other than the font's
  most common one.
- **Glyph → character is parked.** A first try at matching glyphs against
  system-font renders got 29/55 alone (case confusions) and 37/43 when each
  alphabet is fitted as a consecutive byte run; this line of work is set
  aside for now.

### 3. The hotkey from access counters — Super Mario World

**Setup.** `hotkey.lua`: no memory callbacks at all. At a mark it keeps
`emu.getMasterClock()` and a copy of VRAM (64 K `emu.read`s, 5–7 ms). At the
"press" (frame 620, the Welcome box up since 589) it reads the ROM's
`lastReadClock` and `readCount` and VRAM's `lastWriteClock`, cuts the ROM
bytes read since the mark into ranges, and diffs VRAM against the copy.
Marks at frame 560 (1 s before the press) and 300 (5 s, spanning the level
load).

**Findings.**

- **Cheap enough for a key press.** ROM `lastReadClock` 16 ms and
  `readCount` 13 ms (512 KB), VRAM `lastWriteClock` 2 ms, the Lua scans and
  the VRAM diff 7–13 ms each: under 60 ms in all, and nothing during play.
- **ROM alone depends on the window.** Of the ranges read since the mark
  whose bytes were read at most twice all session, the message is the
  largest with the 1 s window — the next two being the tables that selected
  it (`$2A594`, `$2A580`) — but only 7th with the 5 s window, behind level
  and graphics data loaded in it.
- **ROM plus the screen does not.** Aligning each such range with the VRAM
  words that changed since the mark (spike 2's vote-and-LCS) explains the
  message 141/141 as tile = `(b & $7F) + $100` in both windows; the best other
  range explains 66%. The text on screen and its ROM source come out of one
  query, with the byte → tile rule as a by-product.
- A live hotkey needs a mark before the text: a rolling pair of marks (clock
  + VRAM copy, ~7 ms every few seconds), or the user's "text on" key.

### 4. The live bridge — TCP from Mesen's Lua

**Setup.** `bridge.lua` with `--debug.scriptWindow.allowNetworkAccess=true`
(on top of the I/O switch): `require("socket.core")`, connect to
`127.0.0.1:47800`, `settimeout(0)` and `tcp-nodelay`; each frame end it
sends a line and drains the commands waiting (`ping`, `read <addr>`,
`stop`). `bridge_server.py` plays mapchar's side.

**Findings.**

- LuaSocket loads under the two switches and needs nothing else. A
  non-blocking `receive("*l")` per frame end keeps the emulator unblocked.
- **Round trip 2.6 ms median, 3.5 ms p95** with the server in Windows Python
  (mapchar's own runtime): one emulated frame, since commands are answered at
  frame end. Headed at 60 fps that bounds it at ~17 ms. A server in WSL,
  reached through localhost forwarding, costs ~44 ms a round trip to the
  Windows build — WSL's relay, not Mesen; the Linux build with the same WSL
  server takes 3.2 ms median, 4.0 ms p95.
- Closing the socket from mapchar's side is seen as `closed` on the next
  receive; the probe ends the run on it, so a vanished mapchar never leaves
  a headless Mesen behind.

### 5. Buffered, dictionary-compressed text — A Link to the Past (USA)

**Setup.** Linux build, no input: the title screen's attract sequence types
out the prologue in the dialogue font. First pass: the probe and
`vram_ext.lua` idle for 4000 frames, plus `wram_ext.lua` (whole-WRAM dumps
at three frames). Second pass, once the buffer is known: `prov_ext.lua`
hooks writes to `$7F:1200–$7F:13FF` and logs each with the writing PC and
the last ROM data read before it (probe's ring). `dict.py` interprets.

**Findings.**

- **Read runs miss it.** Neither slow (typewriter) nor one-shot ROM runs
  single out the text: the typewriter draws from RAM, and the decoder's reads
  are cut into short runs by dictionary lookups.
- **Relative search on the ROM mostly fails; on WRAM it succeeds.** Of the
  prologue's words only "Hyrule" is in the ROM whole (A = `$00`, a = `$1A`)
  — dictionary codes cover the rest. In a WRAM dump taken after the text
  appears every word is found, at `$7F:1216` onward: the whole prologue
  decoded one byte per character into a buffer at once (frame 1526), before
  the typewriter shows any of it. "Type what you see" should search WRAM
  snapshots as well as the ROM.
- **Buffer-write provenance recovers the encoding.** Pairing each buffer
  write with the ROM read just before it:
  - the PC whose writes equal the byte it just read is the literal copier
    (`$0E:C517`, copying what `$0E:C50D` reads from the stream at
    `$1C:D960`, file `$E5960`);
  - a stream byte skipped between two literals is a dictionary code, and
    what other PCs wrote in its place is its expansion (`$0E:C6F9` writing
    what `$0E:C6F5` reads from the dictionary around `$0E:C82E`).
  This gives 21 dictionary entries (`$8F` "ain", `$90` "and", `$C4` "ound",
  `$D8` "the", …) with no conflict across repeated uses, and marks `$73`,
  `$75`, `$76`, `$78` as bytes passed through as commands. Decoding the
  stream with the literals plus these entries reproduces the prologue
  ("Long a·, in the beautiful kingdom of Hyrule surrounded by mountains
  ··ests…") but for two gaps: adjacent codes, which the one-byte-gap rule
  skips, and punctuation, whose codes appear only as literals. Splitting
  the writes between two literals where the dictionary read address jumps
  resolves adjacent codes ("mountains and forests", "evil power"), but
  inferring the stream from the writes alone then mislabels a literal or
  two as codes: the stream should come from the stream reader's own read
  sequence, with the writes only saying what each byte became.
- **Checked against the disassembly** (`../alttp-disassembly`, which is the
  USA version): the stream read is `LDA [$04],Y` at `$0E:C50B` and the
  buffer write `$0E:C513` (the probe's PCs are the next instruction's); the
  buffer is `$7F1200`, holding codes and commands with the dictionary, name
  and numbers already expanded; dictionary codes are `$88–$E8`, strings
  indexed by the word table at `$0E:C703`. **All 26 dictionary entries the
  probe inferred match that table.** Its two other "codes" are a literal
  mislabelled by the split (`$07` H) and a command parameter (`$09`, of
  `$78` Wait). Punctuation per the ROM map: `$41` `.`, `$42` `,`, `$43` `…`.
- **The glyph path of a proportional font is its font reads.** ALTTP draws
  8×16 glyphs into a 2bpp tile buffer at `$7F0000`, DMA'd to BG3 characters
  `$180–$1FD`, so the tilemap holds fixed cell numbers and spike 2's byte →
  tile alignment has nothing to align. But the renderer reads each glyph's
  rows from the font at `$0E:8000` (reads at `$0E:CBD1`, bottom half
  `$0E:CC67`), and those reads, in order, spell the text as it is typed —
  "Long ago, in the beautiful kingdom of Hyrule surrounded by mountains and
  forests…" (frames 1564–2419) — with the code given by the address:
  top tile `((c & $F0)·2) | (c & $0F)`, 16 bytes a tile. The spike's run
  merging doubles or drops a letter where neighbouring glyphs are read back
  to back; per-read events fix that.
- **Two-phase capture.** The cheap pass (dumps, or the hotkey) finds where
  the text is; a targeted second pass (hooks on that range only, from a
  rollback or a replay) explains how it got there. This is tier 2's shape.
- **Showing message N**, for a sweep: message id at `$1CF0`; an exec hook on
  `Module_MainRouting` (`$00:80B5`) in module `$07`/`$09` with `$11 = 0`
  that writes `$1CF0/1`, `$0223 = 0`, `$1CD8 = 0`, `$010C = $10`, `$11 = 2`,
  `$10 = $0E` opens the box the same frame (from the disassembly; not run
  yet). Messages are found by scanning, not a pointer table: the game walks
  the text once at boot into 3-byte pointers at `$7F71C0 + 3·id`.

### 6. The whole encoding from a message sweep — A Link to the Past (USA)

**Setup.** `sweep_alttp.lua`, Linux build, no input. At the attract
sequence's own call of the decoder (`$0E:C4E2`) it snapshots once; at
`$0E:C4E4`, before `$1CF0` is read, it pokes message id *N*; until the next
frame's `$00:80B5` it prints every ROM data read (`E pc addr value`) and
every write to `$7F1200–$7F19FF` (`X pc addr value`) in order; then it rolls
back. All 397 messages decode in 9 s (172 k reads, 52 k writes). The decode
pass is all a table needs, so no text box has to open. `dict2.py`
interprets.

**Method** (no knowledge of the game):

1. **Drop DMA reads.** HDMA reads the ROM every scanline and is credited to
   whatever instruction is running; an address "read" by four or more PCs is
   a DMA source, not an operand (25 addresses here).
2. **The stream reader** is the PC whose reads are copied straight into the
   buffer from the most distinct addresses (`$0E:C50D`: 23 043; the
   dictionary reader `$0E:C6F5` copies more often but from 271).
3. **Each stream byte is classified by what happens before the next stream
   read:** a *literal* writes itself and reads nothing; a *command* writes
   itself first, or writes nothing, and its parameter count is the stream
   bytes the reader steps over; a *dictionary code* writes bytes that are
   the values just read elsewhere; an *insertion* writes bytes no ROM read
   supplied.

**Findings — every one matches the game's own tables or the disassembly:**

- literals: 89 codes, `$00–$5E`; 12 codes show one to four stray
  classifications among hundreds;
- dictionary: the 96 codes in use (of `$88–$E8`) each expand exactly to the
  ROM table's entry;
- commands: 18 codes, `$67–$7E`, each parameter count as documented (`$6B`,
  `$6D`, `$6E`, `$77`, `$78`, `$79`, `$7A` take one);
- insertions: `$6A` (the player's name, from SRAM) and `$6C` (a number, one
  parameter);
- end token: the last stream byte of all 397 messages is `$7F`;
- layout: 395 of 396 message boundaries are back to back — the break is the
  switch from the first text set to the second — so the text is a scanned
  run of `$7F`-terminated strings, not a pointer table.

That is a complete table file and block for ALTTP's dialogue, from evidence.
Only the characters behind the 89 literals remain, which the font reads of
experiment 5 or a relative search supply.

### 7. Text read by a coprocessor — Yoshi's Island (USA V1.0)

**Setup.** Linux build, no input: the attract intro is a storybook whose
captions ("A long, long time ago…", "A stork hurries across the dusky,
pre-dawn sky.") appear in a proportional font. The probe gains `gsu = true`:
a second `snesPrgRom` read callback registered for `emu.cpuType.gsu`, its PC
taken as `programBank:R15` from `getCpuState(gsu)` and tagged `$1xxxxxx`, and
one context ring per CPU.

**Findings.**

- **The SuperFX reads the text.** Relative search finds the captions whole
  in the ROM (file `$07CF7E–$07D0F0`, `a` = `$D8`, `A` = `$AA`), never in WRAM.
  Their readers are GSU instructions `$09:E9C1` and `$09:E9C5` (one byte
  apart, reading each byte and its successor), one caption line in one or
  two frames. A 65816-only probe misses this text altogether.
- **GSU code fetches look like data reads.** The GSU filling its
  instruction cache is reported to a read callback like any `GETB`: 98% of
  its "reads" were its own code. A read of the program bank within `$200`
  of R15 is dropped before any bookkeeping.
- **The pointer crosses CPUs.** With a context ring per CPU, the 65816's
  reads just before each caption hold its pointer: `$0F:CCFB` reads a word
  from the table at `$0F:CD56` (file `$7CD56`, stride 2) — `$CF78`, `$CF9A`,
  `$CFD1` — and the GSU's text run begins 6 bytes past it: a record with a
  6-byte header, then text. Backtrace needs "absolute plus a constant"
  alongside "relative to a base".
- **Cost.** 5.5 M ROM reads over 3600 frames (~1500 a frame, nearly all the
  GSU's). With `getCpuState(gsu)` on every read: 88 s against a 20 s
  baseline, slower than real time — the call costs **15 µs**, the GSU state
  being large. Peeking R15 and PBR from the 65816 side (`$00:301E`,
  `$00:3034`) returns 0, so that is no way round it. Remembering the 256-byte
  pages where a code fetch was seen, and dropping later reads there before
  asking for state, brings it to 45.6 s — faster than real time (3600 frames
  are 60 s) — with the same 14 caption runs found.

**Mesen traps met.**

- `getAccessCounters` takes `(memType, counterType)`, the reverse of
  `LuaDocumentation.json`: `LuaCallHelper` reads parameters from the last.
  The enum is `emu.counterType` (`readCount`, `lastReadClock`, …).
- One callback may run at most `ScriptTimeout` seconds (default 1); past it
  the script stops with the error only in the script window, and a
  `--testRunner` run then idles to its 100 s timeout. Pass
  `--debug.scriptWindow.scriptTimeout=N` and wrap handlers in `pcall` to see
  errors on stdout.

- A savestate taken in an exec callback at instruction *X*, when loaded,
  does not fire *X*'s callback again: snapshot at an earlier instruction
  than the one that acts.
- Environment variables set in WSL do not reach `Mesen.exe` unless listed in
  `WSLENV`; the spike passes settings by rewriting the script instead.
- `grep` treats the log as binary (the ROM header banner has high bytes):
  use `grep -a`.
