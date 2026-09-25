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

**Memory callbacks:**

- An absolute memory type (`snesPrgRom`, `snesWorkRam`) matches the callback
  against the absolute address, so a `snesPrgRom` read callback reports ROM
  offsets directly, with no mapping arithmetic. A relative one (`snesMemory`)
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
record PPU writes too. `getAccessCounters(LastWriteClock, snesVideoRam)`
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
`sweep_ext.lua`, `run.sh`, `analyze.py`, `backtrace.py`.

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

**Mesen traps met.**

- A savestate taken in an exec callback at instruction *X*, when loaded,
  does not fire *X*'s callback again: snapshot at an earlier instruction
  than the one that acts.
- Environment variables set in WSL do not reach `Mesen.exe` unless listed in
  `WSLENV`; the spike passes settings by rewriting the script instead.
- `grep` treats the log as binary (the ROM header banner has high bytes):
  use `grep -a`.
