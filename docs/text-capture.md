# Text capture from a running game

The evidence behind capturing text from a running game: what is known about
the emulators, the ideas, and the findings of each experiment. The feature's
design is in the plan — [features](plan/features.md#capturing-text-from-a-running-game),
[architecture §9](plan/architecture.md#9-capture) and
[phase 7](plan/phases.md#7-capture-from-a-running-game); this doc is what it
rests on.

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

**Traps** (each met in an experiment below):

- `getAccessCounters` takes `(memType, counterType)`, the reverse of
  `LuaDocumentation.json`: `LuaCallHelper` reads parameters from the last.
  The enum is `emu.counterType` (`readCount`, `lastReadClock`, …).
- One callback may run at most `ScriptTimeout` seconds (default 1); past it
  the script stops with the error only in the script window. A
  `--testRunner` run then idles to its own timeout — 100 s unless
  `--timeout=N` says otherwise. `--debug.scriptWindow.scriptTimeout=N` does
  not reach the core under `--testRunner` (the Linux build stopped at 1 s
  with it passed), so keep each callback short, and wrap handlers in `pcall`
  to see errors on stdout. A headed run takes the switch.
- A memory callback that returns an integer replaces the value read or
  written (`ScriptingContext::InternalCallMemoryCallback`); its address
  argument is the CPU's bus address even when the callback is set on an
  absolute memory type such as the ROM. `convertAddress(address, memType,
  cpuType)` turns a bus address into `{address, memType}` of the memory
  behind it.
- `stateLoaded` fires for a savestate loaded from a file or slot, not for a
  rewind; a rewind shows as the master clock going back.
- `getAccessCounters` on a 32 MB GBA ROM builds a 32 M-entry table: over the
  callback limit on its own.
- An exec callback over a CPU's whole address space fires on the next
  instruction, whatever it is: registered at a frame end and removed in its
  first call, it gives `createSavestate` a place to run once a frame, on any
  console. The range's top must be the CPU's own (`$FFFFFF` for the 65816,
  `$0FFFFFFF` for the ARM); `$FFFFFFFF` never fires.
- `drawString(x, y, text, fg, bg, maxWidth, duration)`: a `maxWidth` of 1
  wraps after every character.
- A savestate taken in an exec callback at instruction *X*, when loaded,
  does not fire *X*'s callback again: snapshot at an earlier instruction
  than the one that acts.
- **A read callback cannot tell who read.** DMA and HDMA reads arrive with
  whatever instruction is running as the PC; a coprocessor's instruction
  fetches (the SuperFX filling its cache) arrive as data reads; so do an ARM
  CPU's literal-pool loads. Each needs its own filter (experiments 6, 7, 9).
- The SuperFX prefetches: writing R14 reads the byte at the new address, so
  a `GETB` stream shows at the instruction that moved R14, often as two PCs.
- **A non-blocking `receive("*l")` hands back a partial line as its third
  result and drops it unless it is passed back in** (`receive("*l",
  pending)`); a non-blocking `send` can write part of a line. Either leaves
  the two ends waiting on each other: the probe server blocks for its sends
  and keeps partial commands.
- `getCpuState` costs ~1 µs for the 65816 and **15 µs for the SuperFX**, its
  state being large; `getState` 0.2 ms. The SuperFX's registers read as 0
  when peeked from the 65816 side (`$00:3000–$303F`).
- The GBA core needs a BIOS in `Firmware/` (`gba_bios.bin`); there is no
  built-in stand-in. The GBA PC to use is `pipeline.execute.address`.
- **NES:** a read callback on `nesPrgRom` also gets the 6502's dummy reads
  (the byte after an implied opcode, at the PC), and reports the bus
  address, which under a mapper names no bank: `convertAddress(addr,
  nesMemory, nes)` gives the PRG offset, for PCs too. Battery RAM is
  `nesSaveRam`; `nesWorkRam` can be empty.
- Battery saves persist between runs even with `--doNotSaveSettings`, so a
  game's title menu can differ from one run to the next.
- `emu.takeScreenshot()` returns PNG bytes; saving them needs the I/O
  switch and `io.open(…, "wb")`.
- Environment variables set in WSL do not reach `Mesen.exe` unless listed in
  `WSLENV`; the spike passes settings by rewriting the script instead.
- `grep` treats a log as binary (the ROM header banner has high bytes): use
  `grep -a`.
- **Two access-counter tables of a 3 MB ROM and a walk over them take longer
  than one callback may** (EarthBound's replay stopped there): the work is
  spread over one-shot execution callbacks, each with a second of its own.
- **A short line sent in two pieces waits for the second** — Nagle's
  algorithm against a delayed ACK, ~40 ms a probe: both ends set
  `TCP_NODELAY`.
- Mesen's home is `$XDG_CONFIG_HOME/MesenCE`: under another config home it is
  a first run, and opens a window even with `--testRunner` (the test suite
  points the variable at a temporary folder).

**CDL:** the code/data logger marks every ROM byte read as data or executed as
code; `getCdlData` returns it and Mesen saves it as a `.cdl` file.

**Pausing:** with a script loaded the debugger is attached, so the UI's pause
is a debugger break (`Emulator::Pause` → `BreakSource::Pause`). The emulation
thread then sleeps in `Debugger::SleepUntilResume`, and scripts get one
`codeBreak` event before it does, and nothing after until the user resumes.
In that event `read`, `getState`, `takeScreenshot`, `getAccessCounters`,
`getCdlData`, `io` and LuaSocket all work; `createSavestate` does not (it
needs an exec callback). A pause shows the last frame with whatever was drawn
on it; drawing in `codeBreak` never reaches the screen.

**Headed screenshots:** a GUI Mesen window is captured from WSL with
PowerShell and `user32!PrintWindow` (flag 2), which grabs the window's own
content even under other windows; `CopyFromScreen` grabs whatever is on top.

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

## The feature

Its design — the user's side, the bridge, the replay, the rules each result
is decided by, and the build order — is in the plan
([architecture §9](plan/architecture.md#9-capture)), and it is built as
`mapchar.capture` ([capture.md](capture.md) runs it). Experiments 10 to 18
below are the evidence for it.

## Unassisted ideas

- **Read runs.** A text engine is one instruction (`LDA [$00],Y`) walking
  ROM a byte at a time. Group data reads by the reading PC into ascending
  runs; a run's last byte suggests the end token. Timing does not tell text
  from decompression: SMW reads a whole message in one frame, and ALTTP's
  typewriter draws from RAM (experiments 1, 5).
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

- **Speed.** Measured: well inside real time on SMW and ALTTP; the SuperFX
  and the GBA need their read filters to stay there (experiments 7, 9).
  Tier 2 takes the cost off the player regardless.
- **Noise.** Level data, graphics decompression and DMA dominate the reads;
  telling text apart needs the screen, a lookup into a font, a user hint or
  a known reader — never read statistics alone (experiments 1, 3).
- **Showing every message needs the engine.** Each sweep so far poked a
  game-specific message id found in a disassembly. Without one, text is
  captured only as play shows it.
- Text built at runtime (names, numbers) — seen as insertions, not text.
- The script switches must be passed on every launch.

## Test games

| Game | Console | Engine | Known answer |
|---|---|---|---|
| Super Mario World | SNES | fixed tiles, plain text, relative pointers | `local-tools/samples/Super Mario World/` |
| A Link to the Past (USA) | SNES | dictionary-compressed, buffered in WRAM, 8×16 proportional font | `../alttp-disassembly` |
| Yoshi's Island (USA V1.0) | SNES + SuperFX | text read and plotted by the coprocessor, proportional font | `../yi-shiny` |
| Mother 3 | GBA | 16-bit characters, pointer tables and archives | `sample-projects/Mother 3/` |
| EarthBound (USA) | SNES | ASCII + `$30`, fixed-length menu records, a scripted main engine | none: the unseen game |
| Dragon Warrior II (U) | NES (MMC1) | plain-byte prologue and menus; a 5/10-bit packed script over a dictionary, 16 strings a pointer | `sample-projects/Dragon Warrior II/` |

## What the experiments show

Four text engines on two consoles reduce to the same evidence and the same
few inferences:

- **Evidence is per-read events**: `(cpu, pc, address, value)` for ROM data
  reads, plus writes to a buffer once one is known, in order. Runs, sightings
  and statistics are derived from them, not recorded instead of them — the
  run-merging spikes lost letters the events keep.
- **Filters come first** (DMA, coprocessor code fetches, literal pools), or
  every later step fits noise.
- **The stream reader** is the reader of the most distinct addresses in one
  contiguous span, joined by any reader inside the same span (a twin).
- **Each stream byte is classified by what happens before the next one**:
  written as itself (a literal), written as other bytes it caused to be read
  (a dictionary entry), written from no ROM read (an insertion), triggering a
  lookup at `base + code × stride` (a glyph), or nothing, the reader stepping
  over parameters (a command).
- **Pointer backtrace** needs several hypotheses — relative to a base,
  absolute plus a constant, 16-bit values on the GBA — and the pointer may
  be read by another CPU than the text. A tight window before the text and a
  score by distinct slots holding distinct values keep it honest.
- **Checked against a known answer every time**: SMW's sample, ALTTP's
  and YI's disassemblies, Mother 3's project. The inferred tables, entries,
  command lengths, end tokens and pointer tables matched, bar the gaps each
  experiment names (Mother 3's base 2 bytes off, table ends a sweep never
  reached).

Open: a generic way to show message *N*. Each pointer read
`table + id × stride` is preceded by a read of `id` — a RAM variable that,
found from the evidence, would make any game sweepable without a
disassembly (not yet tried).

## Experiments

Spike scripts live in `tmp/capture-spike/` (scratch, gitignored): `probe.lua`,
`sweep_ext.lua`, `vram_ext.lua`, `hotkey.lua`, `run.sh` (`MESEN=linux` for the
Linux build, `EXTRA=` adds switches), `analyze.py`,
`backtrace.py`, `glyph.py`, `bridge.lua`, `bridge_server.py`, `wram_ext.lua`,
`prov_ext.lua`, `dict.py`, `sweep_alttp.lua`, `dict2.py`, `sweep_yi.lua`,
`yi_infer.py`, `gbaprobe.lua`, `m3_truth.py`, `m3_analyze.py`,
`m3_backtrace.py`; `run.sh` takes `ROM=`, `DRIVE=false`, `GSU=true`, `FRAMES=`,
`SHOTS=`. The feature's spikes: `live.lua`, `replay.lua`, `cap.sh` (Linux
build; `cap.sh <lua> <game> <frames>`, output in `cap/<game>/`),
`contact.py`, `locate.py`, `session.py`, `t_break.lua`, `t_overlay.lua`,
`t_send.lua`, `shot.ps1`, `dw2_truth.py`, `bits.py`; causal probing: `probe_srv.lua`, `causal.py`, `effects.py`, `pipe.py` (`pipe.py <game> <capture> "<typed text>"`), `c_bits.py`, `t_typo.py`, `t_late.py`, `vpipe.py`, `replays.sh`, `run_all.sh`, `bench.py`; font watching: `fontwatch.lua`, `fontwatch.sh`, `fw_analyze.py`, `fontbreak.lua`, `fontbreak.sh`, `setup_e2e.py`.

**Running them.**

- Evidence logs go in `tmp/capture-spike/ev/` (`replays.sh game:capture …`
  regenerates them); anything under `/tmp` is gone after a restart.
- A probe costs the frames it replays over the emulator's headless speed —
  about 250–350 frames a second for the SNES, 150 with the SuperFX, 130 for the
  GBA. Start from the state just before the read in question, stop at the
  text's last write (or the frame its VRAM settles), and ask the narrow
  question: the full dependency set of one SMW message is over 1100 bytes.
- Launch a long job in the background and wait on its process, never on
  `ps | grep <name>`: a shell whose own command line holds the name matches
  itself and never exits. Every wait has a deadline. Stop a job by its PID,
  never `pkill -f <name>`, which kills the shell issuing it.
- `causal.py` gives its socket a timeout and `probe_srv.lua` wraps its
  callbacks in `pcall`, so a script stopped by an error or the one-second
  limit is reported instead of leaving the driver waiting.
- Run the venv's Python (`.venv-linux/bin/python`), not `uv run python`,
  under `timeout`: `timeout` stops `uv`, and the Python it started carries
  on, holding its port and its emulator.
- A probe server still busy when its driver closes it (a stalled probe) is
  killed, not left running on its port.
- `bench.py` runs the pure-Python stages on the saved logs without Mesen and
  checks them against the outputs recorded in `golden/` (`record`, `check`,
  `--orig` for the code before a change). The matching is indexed: a
  sequence's values and neighbour differences kept as byte strings pick the
  positions to test, each ROM byte's latest read is kept as the log is walked,
  and the layout search remembers positions it has failed from. Mother 3's
  full search went from 52 s to 8 s, finding the text from 3–9 s to under
  1 s, a source's candidate lists from up to 15 s to under 0.5 s, and a DW2
  layout search from 99 s to 33 s — its node budget still has to be counted
  out. There is no numpy in the environment, and most of what is left
  (building the event tuples, the layout search) would not vectorise.

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

### 8. Message boxes drawn by a coprocessor — Yoshi's Island (USA V1.0)

**Setup.** `sweep_yi.lua`, Linux build, four instances in parallel over id
ranges (102 s for all). It loads 1-1 through yi-shiny's `load_level.lua`,
then records which ROM pages 120 frames of ordinary play read (111 pages);
at `$00:8150` it snapshots, and per message id writes `$70:4070` and
`$7E:0D0F = 1`, answers every wait for input (an exec hook at `$01:E1D6`
writes `$0080` to `$70:4076`), and logs every ROM read of either CPU outside
the play pages, in order, until the box closes; then rolls back. Ids whose
pointer is `$0000` are skipped (read from the game's table, scaffolding
only): 257 messages, 604 k events. `yi_infer.py` interprets.

**Method** (no knowledge of the game):

1. **Stream:** per message, the reader with the most distinct addresses in
   one contiguous span, plus any reader whose reads fall inside that span —
   its twin (the GSU reads the low and high byte of each word at two PCs,
   `$09:B0C2` and `$09:B0C4`). The stream is the union, in address order.
2. **Lookup tables:** for each other reader, the `(stride, base)` for which
   its reads land on `base + code × stride` with `code` a stream byte. The
   game reads ahead of what it draws (a word is measured before it is
   plotted), so a lookup is paired with any stream byte within ±24, and a
   table must be hit by at least 20 distinct codes — a reader of a handful
   of addresses fits anything.
3. **Glyph codes** are the stream bytes some table lookup lands on; the rest
   are structure. A structural byte followed by the most common one (`$FF`)
   is a command, and its length is the reader's step past it.
4. **Pointer:** the two consecutive-address reads by one 65816 PC before the
   stream whose value is the stream's first address.

**Findings — matching the disassembly (yi-shiny) throughout:**

- lookup tables: the font — glyph rows at file `$04BD2F` (`$09:BD2F`),
  stride 12 (8×12, 1bpp), and widths at `$04BC2F`, stride 1 — each
  explaining 14 448 of 14 453 lookups; the scaled renderer (`$09:B5ED`) uses
  the same two; no other table survives the test;
- 100 glyph codes, `$18–$F9`;
- commands, each `code $FF`: `05–08` (row), `0A`, `0F` (wait), `0E` (line
  break), `12` (one-pixel scroll, 3792 uses), `31`, `38` (size), `3D`, `3E`
  (counters), `50–52` (yes / no), `60` (picture); every observed one is a
  two-byte word;
- end token: all 257 messages end `$FFFF`;
- pointers: `$01:E19E` reads a word from `$51:10DB + 2·id` (file `$1110DB`)
  whose value is the stream's address in bank `$51`, for 257 of 257
  messages — the 65816 reads the pointer, the GSU the text.

That is the whole block — pointer table, table file with commands, and the
font to draw it — from evidence of a coprocessor-rendered, proportional
text engine.

YI's other text, from yi-shiny (not run): every renderer is a SuperFX
routine on the same font. Level names (`$51:49BC`, 72 pointers by level,
`$FD`-terminated with `$FF`/`$FE` position codes) are plotted into OBJ tiles
at VRAM `$5C00` and shown as 16 sprites; the file-select strings (`$17:94BE`
…) and the ending story (`$0D:F3E8`) each have their own renderer and
codes; the credits appear to be bitmaps, not text.

### 9. A second console — Mother 3 (GBA)

**Setup.** Mesen's GBA core needs a BIOS (`gba_bios.bin`, 16 KB, from
`Firmware/`; there is no built-in stand-in): the Linux build uses the
open-source Cult-of-GBA replacement (MIT), installed as
`~/.config/MesenCE/Firmware/gba_bios.bin`, which runs Mother 3. The sample
project `sample-projects/Mother 3/` is the known answer: `m3_truth.py`
extracts its 20 blocks' 12 997 strings with their ROM spans and pointer
addresses. `gbaprobe.lua` logs every ARM ROM data read as an event
(`gbaPrgRom`, `emu.cpuType.gba`; the PC is `pipeline.execute.address`, the
instruction executing, not R15 with its pipeline offset) and presses A every
30 frames and Start every 90, which reaches the first naming screen (menu
labels, the character's description) and stops there. `m3_analyze.py`
checks the reads against the project; `m3_backtrace.py` infers without it.

**Findings.**

- **Literal pools look like data.** ARM code loads its constants from just
  past itself: 777 k of 6.3 M reads were within 4 KB of the PC and are
  dropped. Code copied into IWRAM (`$03000xxx`) reads the ROM too, and is
  not near it.
- **Reads are 8, 16 or 32 bits** — a callback reports one read of the
  access's width — so a run is a reader stepping forward by up to its width,
  not by one byte.
- **Against the known answer:** 40 strings were shown in 2621 frames
  (Character names, Party battle names, Battler and Enemy names, 11 Menu text
  strings), and the readers that read them read nothing else: of their reads
  only 2 fall outside every known string. For all 11 Menu text strings, one
  instruction (`$080486CC`) read the string's pointer just before its text.
- **Without the answer:** runs of 4+ reads (28 091 of them — graphics and
  map data too) and, for each, the 16-bit reads by another PC in the 10 reads
  before it: the hypothesis with the most distinct slots holding distinct
  values is `$080486CC`, 11 slots in `$1BC2462–$1BC25FC`, stride 2, base
  `$1BC263E` — the project's Menu text table (`$1BC23FC–$1BC263A`, stride 2,
  base `$1BC263C`). The base is 2 off because one PC reads a string's first
  character and another the rest, which the twin merge of experiment 8
  handles. With a 30-read window the reads of IWRAM code swamp the vote:
  scoring distinct slots, not raw votes, and keeping the window tight
  matter.
- **Cost.** Printing every read is the expense: 6.3 M events took the 100 s
  test-runner timeout for 2621 frames, slower than real time; the run-based
  probe's bookkeeping is what a real GBA probe needs.
- **Not reached:** blind input stops at the naming screen, so the Script
  (7825 strings) was never shown; a message sweep needs the engine's own
  way to show message *N*, as for ALTTP and YI.

### 10. Savestate ring and replay — five games

**Setup.** `live.lua` plays each game headless (SMW's menu driver,
EarthBound and Mother 3 pressing A and Start blind, ALTTP and YI their attract
sequences) with no data hooks: a savestate a second from a one-shot exec
callback, 16 kept, and every polled input. At chosen capture frames it writes
the ring, the inputs, a screenshot and an Adler hash of WRAM + VRAM (GBA: both
WRAMs and VRAM). `replay.lua`, in a second headless instance, loads a ring
state, feeds the inputs back with `setInput` by poll index, and compares the
hash at the capture frame; with evidence on it logs every ROM data read (PC,
address, value; the SuperFX's too, with experiment 7's filter; the ARM's with
experiment 9's), every WRAM write with its PC, every write to `$2115–$2119`,
`$420B` and `$43xx`, and at the end WRAM, VRAM and CDL.

**Findings.**

- **Replay is exact.** Nine captures (SMW 1, ALTTP 1, YI 2, EarthBound 4,
  Mother 3 1) each replayed from the ring's oldest state, 16 s back, to the
  same hash; SMW from three states too. Spaced to 113 frames (a 30 s window),
  ALTTP and two EarthBound captures replayed exactly from their oldest state,
  28–29 s back. A capture needs no savestate at the capture itself.
- **Cheap during play.** A savestate costs 1.5–3.5 ms on SNES (120–260 KB)
  and ~15 ms on GBA (40 KB); the ring is 2–4 MB. `getInput` in `inputPolled`
  reads back what `setInput` set, so it records the player's input as well.
- **Replay cost** with evidence: 6–17 s for 16 s of SNES play (0.7–2.5 M
  events), 18–21 s for 29 s (3.7–6.1 M), 58 s for 16 s of Mother 3 (3.6 M).
  Off the player's path.

### 11. Pausing, overlay and hand-off — Mesen's UI

**Setup.** `t_break.lua` headless: `emu.breakExecution()` at a frame, and a
`codeBreak` handler trying each API. `t_overlay.lua` in the Windows build,
headed: SMW with its message box up, drawing text on the frames before the
break and in `codeBreak`, the window captured by `shot.ps1`. `t_send.lua`:
the ring and a screenshot sent over TCP from `codeBreak` to a Python peer.

**Findings.**

- A break from a script and the UI's pause take the same path and raise
  `codeBreak` once; everything but `createSavestate` works there, and the
  emulator stays paused after it.
- The paused window shows the last frame with its overlay; text drawn in
  `codeBreak` never appears. An overlay can only carry what was drawn before
  the pause (a "recording" mark), never a text box.
- 16 SNES states and the screenshot (3 MB) reach the peer in 3.5 ms from
  `codeBreak`: the whole capture is handed to mapchar at the pause.

### 12. Finding typed text — five games

**Setup.** `locate.py` over each replay's evidence, given the text on screen
as a user would type it. The text is cut into words; each word of four or
more letters anchors a chain in which the others follow in order within 40
codes, sharing the anchor's bases; short words join only once their alphabet
is pinned. Channels: each PC's ROM reads (values; and `addr // stride` for 21
strides, a font lookup), each PC's WRAM writes, and WRAM and VRAM at the
capture as 1- and 2-byte codes.

**Findings.**

- **Every capture is found**, EarthBound's included:

  | Game | Found in | Answer |
  |---|---|---|
  | SMW | ROM stream (`$05:B212`, `$2A5D9`), WRAM buffer, VRAM tilemap (`$50C7`) | experiments 1–2 |
  | ALTTP | WRAM only (`$7F1205`) — dictionary codes break the ROM stream | experiment 5 |
  | YI | the SuperFX's ROM stream (`$09:E9C1`, `$07CFA0`) | experiment 7 |
  | EarthBound | ROM stream (`$C1:0F35`, file `$04C194`…; `$C4:9F06` for "The year is 199X") and a WRAM buffer; `A` `$71`, `a` `$91`, `0` `$60`, space `$50`, `?` `$6F`, `.` `$5E` | the ROM, decoded as ASCII + `$30` |
  | Mother 3 | ROM stream (`$0804936C`, `$1BC330A`) and the font lookups at `$D0B…` (stride 10) | `m3.tbl`: `い` = `$011D` |

- **Words, not lines.** A user cannot know the spacing, and engines mark
  line ends in the characters (SMW sets bit 7 on each line's last one). Per
  word chains absorb both: SMW's stream matches 18 of 24 words, the six
  misses being line-final.
- **Kana order.** Mother 3's font follows Shift-JIS, small and voiced kana
  between the plain ones; searched in gojūon order it finds nothing, in JIS
  order at once. mapchar's relative search knows only gojūon order.
- **The gaps are table entries.** Aligning typed separators with the codes
  between words gives space and punctuation (SMW space `$1F`, `.` `$1B`, `!`
  `$1A`; YI space `$D0`, `,` `$CF`), and untyped codes there are commands
  (YI's `$FE $FD $FC` line break).
- A pure-Python search takes 3–7 s a SNES capture and 100 s for Mother 3;
  the shipped one needs arrays.

### 13. Session analysis — SMW, YI, EarthBound

**Setup.** `session.py` over each game's sightings: the best stream per
sighting, its reader's run around it (addresses stepping ≤ 4, reads ≤ 4
frames apart), the last byte, pointer hypotheses from the 48 reads before the
run, and every ROM place holding the string's address, labelled by the
replay's CDL (for the 65816, an executed spot after an immediate opcode is an
immediate operand).

**Findings.**

- **Extents.** SMW's message comes out exactly (`$2A5D9`, 141 bytes); YI's
  captions and EarthBound's strings do once reads are bounded in time —
  without that bound a reader continuing into the next string later merges
  them.
- **End of string.** YI ends every string `$FF` (as experiment 8 found).
  EarthBound's menu strings are **fixed-length**: 40-byte records padded with
  `$00` (`Please name him.`, `Name her, too.` …), or packed back to back with
  no terminator (`Favorite food:Coolest thing:Are you sure?Yep`), so
  "the byte after the string" is padding or the next string, and fixed
  length is a hypothesis of its own. Its script text is framed by commands
  (`[02][0C][01][32]The year is 199X[09][00]`).
- **Pointers need more than one source.**
  - YI: the reads before the text give experiment 7's table from two
    sightings — slots `$0F:CD58` and `$0F:CD5E`, one PC (`$0F:CCFB`), each
    value 2 short of its string. Two slots give the stride only as a multiple
    (6 here, truly 2); pointer discovery settles it and finds the rest.
  - SMW, one sighting: every relative hypothesis explains one of one, so
    none can be chosen — relative pointers need two sightings of an engine,
    or pointer discovery to test each.
  - EarthBound: no pointer is read before the text. Its menu strings are
    addressed by immediates in code — `LDA #$C194` at `$01F94A`, `$01F9AD`,
    `$01FA01`, `$01FA53` (four naming prompts), `$01FBB8`, `$01FC6A` — which a
    data-read probe never sees, and "The year is 199X" by a 24-bit pointer at
    `$049EA4` read long before. The static search finds both, and the CDL
    separates them from chance matches: of 19–118 places holding each
    address, the executed and read ones are the right ones.
- **Ties.** A reader that reads many similar strings offers several chains
  for the same words ("Name another friend." … "Name your pet."); the
  tightest chain is the string, and a search that stops at its first few
  chains misses it.

### 14. A new console and a bit-packed script — Dragon Warrior II (NES)

**Setup.** `live.lua` and `replay.lua` gain the NES (`nesPrgRom`, internal,
save, CHR and nametable RAM); pressing A and Start blind reaches the title
menu, the scrolling prologue and the Moonbrooke dialogue. Five captures
(450, 1800, 3000, 4050, 4200) replayed from the ring's oldest state and were
searched with `locate.py` (plus 1-byte nametable cells). `bits.py` then
infers the script's encoding from the 3000 capture alone. The answer is the
project's Cartographer file and `dw2_script.tbl`.

**Findings.**

- **Replay is exact on the NES** too: all five hashes match, 3–18 s each.
- **Found everywhere, in the channel each text uses:** the menu's "BEGIN A
  NEW QUEST" in the ROM stream (reader PRG `$3ED01`), a RAM buffer and the
  nametable (`A` `$24`, space `$5F`); the prologue as plain bytes at file
  `$1CACE`, 28 of 28 words; the three dialogue boxes only in RAM (`$006D`,
  written by PRG `$B3F0`), since the script is bit-packed.
- **The script's source.** While a box decodes, fixed-bank code (PRG
  `$3FE50`, `$3FE56`) walks file `$14C08–$14C4C`, and the characters are
  copied (PRG `$B3EE`) from a dictionary at PRG `$B48B+`, whole entries at a
  time ("here", " the", "King"). The known answer puts the string 4 bits into
  `$14C07`, ending at `$14C4D`.
- **The code table from one sighting.** Aligning the span's bits with the
  copied characters — a code is one dictionary entry (a run of consecutive
  addresses, the same run each time, no two codes the same run) or nothing —
  leaves one parse: MSB-first 5-bit codes, the top four escaping to 10 bits,
  from bit 4. Its 31 codes are the project's own: `%00101` y, `%01001` e,
  `%01111` space, `%11010` a, `%1111101101` here, `%1111111111` " the",
  `%1110001010` King, `%00000` end. Tokens cut by address alone fail
  (single letters sit alphabetically, so "hi" looks like one entry), and so
  does a one-code-per-entry pairing (codes that print nothing drift it).
- **The pointer names a group.** Just before the decoder starts, fixed-bank
  code reads PRG `$B760` = `$880D`: slot 7 of the project's table, which as a
  CPU address in the bank the stream is read through is the group's start
  (file `$1481D`). The decoder then decodes 13 strings unseen before
  printing the 14th — the "16 strings a pointer" layout, visible as the
  reader's walk from the group start.
- **Not general yet.** `bits.py` names the copy PC, the stream readers, the
  frames and the bank itself; experiments 5 and 8 give the rules that find
  them (the reader whose reads the buffer receives; the reader of the most
  distinct addresses in one span). The search takes 110 s in Python.
- "ADVENTURE LOG" is stored backwards (the project's block of that name); it
  was not on screen in these captures, so a reversed search is untested.

### 15. Causal probing — SMW, ALTTP, EarthBound, Dragon Warrior II

**Setup.** `probe_srv.lua` replays a capture from a ring state, recording the
writes to one observed range (from the text's first frame to its last) and a
savestate every 10 frames; then it serves probes over TCP: load the state
before a frame, write ROM bytes (`emu.write` on the PRG ROM type; savestates
do not hold ROM, so the probe undoes its writes), run, and report the
observed writes and, optionally, the text reader's reads. `pipe.py` runs one
set of rules on every game; its only per-game inputs are console facts (the
address mapping, the RAM types), the capture and the typed text.

**Findings.**

| Game | Sources | Pointer | Codes |
|---|---|---|---|
| SMW | 140/140 copied, 140 probes, 6 s: `$2A5D9–$2A664`, plus the line headers from `$2A580` | slot `$2A5AF`, value `$0000`, base `$2A5D9` — from one sighting, 64 probes | `$00–$7F` one tile each, `$80–$FF` the same plus the line padded: bit 7 ends a line |
| ALTTP | 80/80, 93 probes, 3 s: the stream at `$E5968…` and dictionary entries at `$747xx–$748xx` | — | 135 literals; 120 codes expand to strings — the dictionary (`$8F` "ain", `$C4` "ound", `$D8` "the", …) — from one sighting, 256 probes, 9 s |
| EarthBound | 15/15 copied, 15 probes: `$4C194–$4C1A2` | the operand of `LDA #` at `$1F94A`, in code | 251 values one character each (ASCII + `$30`); `$00`, `$20`, `$22`, `$2F` write nothing |
| Dragon Warrior II | 123/125 (121 copied from the dictionary), 191 probes, 42 s | — | stream PRG `$14BF6–$14C3B`; MSB-first 5-bit codes, four escapes to 10 bits, from bit 4 of file `$14C07`: the project's own 29 codes |

- **Everything the correlation spikes needed a threshold for is measured
  instead**: a relative pointer from one sighting (it needed two), line-final
  characters SMW flags in bit 7 (the word matcher missed them), code
  immediates (EarthBound), and the packed script's inputs (named by hand in
  experiment 14).
- **The smallest change.** Inverting a byte flips SMW's line-end bit, so no
  letter is ever just substituted; flipping the lowest bit is.
- **Intervene late.** A probe starts from the state before the read in
  question, not before the byte's first read: 15 ms a probe instead of 1 s.
- **Finding every dependency is the wrong question**: SMW's message depends
  on over 1100 bytes (the level, the trigger); asking "where does output *k*
  come from" costs one probe an output.
- **Observe only the text.** The observed range gets other writes too (a
  level load, a character port reused by later text); the reference is the
  text's writes, from its first frame to its last.
- **Spaces move line breaks.** Changing a space re-flows the words before
  it, so a space's dictionary byte is *copied* only when the lengths match —
  hence the tiers.
- **Pointer probes must start before the pointer is used**, and compare the
  reader's reads from the string's frame on: from an early state the reader
  first reads other strings.
- **Rules still open.** Word chains still bound the gap between typed words
  (40 codes); a source is sought among the reads of the output's frame and
  the one before, within a budget (reported unsourced beyond it); when
  several bit layouts fit, the one with fewest codes is shown (8 fit one DW2
  sighting). Yoshi's Island and Mother 3 draw text from the ROM straight into
  bitmaps, so no RAM write holds it: they need the VRAM or the screen as the
  observed output.

### 16. Failure cases — what the capture window can say

- **A typo** matches nowhere; the words that chain in the best channel mark
  the one that does not: 16 of 17 words chain for "Dinosuar", and the window
  can underline it.
- **Too little text** ("Wel") occurs in 757 places: the window asks for more
  of the line.
- **Text not on screen** ("Hello there, Mario!") matches no word anywhere.
- **Drawn before the window**: the text is in RAM at the pause but no write
  in the replay produced it (ALTTP replayed from 66 frames before the pause:
  0 occurrences in the writes, found at `$7F1205` in RAM). Detected exactly;
  the table codes still come from RAM, the trace does not.

### 17. Bitmap text, a second sighting, and the rules refined — YI, Mother 3, DW2

**Setup.** `vpipe.py` runs the same rules when the text never reaches RAM:
the typed text is found in one reader's ROM reads, and the output compared is
the whole VRAM (`probe_srv.lua`'s `VOBS`), first at the capture frame and then,
once the text's VRAM range is known, at the frame it settles (`settle`: the
earliest frame after which that range holds what the user saw, in the
unchanged run). Structure comes from the reader's own reads after the swept
byte. `c_bits.py` takes several captures.

**Findings.**

- **VRAM as the output works.** Every typed character is confirmed by a VRAM
  change — 34/34 in Yoshi's Island (70 s), 18/18 in Mother 3 — and the change
  says where its glyph lands; in YI's proportional font changing an `i`
  moves everything after it (115 bytes).
- **Settle.** Mother 3's text is final in VRAM at frame 1833 against a
  capture at 2700: comparing there cut a sweep from 2437 s for 256 probes to
  253 s for 512.
- **Sweep every byte of the code.** Mother 3's low byte alone gave 256
  printable codes; its high byte gives the structure — `$FF` stops the
  reader, Mother 3's `$FFxx` commands.
- **Pointers: change by 2, from the earliest read.** Mother 3's slot
  (`$1BC25B8`, the project's) is found only when the candidates are the reads
  before the *earliest* read of the string's first byte — by another routine
  (`$08048758`) than the one reading the typed text, with the pointer read
  just before it — and when the change is 2: a +1 makes an odd address, which
  a halfword read rounds away. A second byte (`$1B90128`) moves the string the
  same way: the table's base, which a second sighting tells from a slot.
  Such probes run only to the string's read: 4 s for 64 candidates.
- **A second sighting decides the layout.** DW2's 4050 capture fits one bit
  layout on its own; with the 3000 capture (8 fits) exactly one fits both
  with one code table — MSB-first 5-bit, four escapes — and its 41 codes are
  the project's, `%10101` `‘`, `%1110100010` `’[wait][line]`, `%1111011011`
  "Hargon" and `%1111010000` "d the" among them.
- **Copied means the output moves with the byte.** SMW's line-final
  characters carry bit 7, so their output never *equals* the byte; it moves by
  the same amount. Searching on for a stronger relation cost 150 probes a
  character; the strongest relation in the nearest window of reads is taken,
  and the window widens only when it holds none.
- **Static candidates need a narrower filter.** Mesen keeps a CDL per ROM
  between runs, so after many runs it marks nearly every byte as touched, and
  SMW's candidates holding its string's address still each replay from the
  window's start.
- **A change can stall the emulator**: one YI pointer candidate left its probe
  unanswered past 600 s. The driver now gives a probe a deadline from its
  frames, restarts the server and counts it as no answer.
- **The protocol lost lines.** A non-blocking LuaSocket receive hands back a
  partial line as its third result, which the server dropped: the driver
  waited for an answer to a command the server never saw.
- **Pointer candidates: only the string's own bytes are excluded.** Skipping
  every read within 256 bytes of the string dropped SMW's slot, which sits
  just before its text. With that fixed, one SMW capture gives both bytes of
  its slot (`$2A5AF` moves the string by 2, `$2A5B0` by `$200`) and the base
  the relative pointers are added to, an operand in code (`$2B210`).
- **This replay's own accesses.** Resetting the access counters when the
  replay starts marks 94 K of EarthBound's bytes as read or executed, where
  the CDL (every run) marks 332 K: its pointer stage went from 45 s to 11 s.
- **A pointer whose change stalls the game before the string is read cannot
  be confirmed.** Yoshi's Island's caption slot (`$7CD58`, 8 reads before its
  text) points at a record's header; moved by 2, the header no longer parses
  and the game hangs before the SuperFX reads a byte. The probe now answers as
  soon as the reader's first reads are in, but here there are none, and the
  candidate is reported as stalling rather than as a pointer.
- **A packed string's pointer moves the stream**, not the dictionary reads
  the source stage finds first; its test has to follow the stream reader's
  first read (DW2, not yet done).
- **Yoshi's Island's codes** from one byte and 256 probes: `$FC`–`$FE`
  commands with one parameter, `$FF` the end, 252 printable codes leaving 160
  distinct glyph images.

### 18. Watching a font's reads from launch — ALTTP, Yoshi's Island

**Setup.** `fontwatch.lua` (`fontwatch.sh <game> <tag> [hook] [pc] [frames]`,
output in `fw/<game>/<tag>/`), Linux build, attract sequences, no input: a
read callback on the font's ROM range only — ALTTP `$70000–$71FFF`, YI's
glyphs from `$4BD2F` at stride 12 (the 65816's and the SuperFX's) — logging
frame, clock, reading PC, address and value per read. `fw_analyze.py` cuts
the reads into episodes (30 quiet frames apart) and the episodes into glyphs.

**Findings.**

- **The reads spell the text, with its codes.** Every episode is one page or
  caption, and its glyphs in read order are the text as drawn, doubled
  letters and punctuation included: ALTTP's five prologue pages ("Long ago, in
  the beautiful kingdom of Hyrule surrounded by mountains and forests…"; the
  space glyph `$59`), YI's ten screens ("A long, long time ago", "This is a
  story about baby Mario and Yoshi", "SCRREEEECH", the controller notice). A
  line break draws nothing, so the words either side of one join.
- **A glyph's code needs the font's layout, not just a stride.** YI's is
  `(address − base) / 12` for the lowest read of a burst (each glyph reads 16
  bytes, and a lone read of the table's first byte comes before each).
  ALTTP's is the top tile `((c & $F0)·2) | (c & $0F)`, 16 bytes a tile, the
  bottom half `$10` tiles on — no single stride.
- **Episodes mark when text starts.** ALTTP's first page starts at frame 1564,
  38 frames after its decode into the buffer (experiment 5); YI's captions are
  plotted in one or two frames each.
- **The readers are the renderers**: ALTTP `$0E:CBD3` and `$0E:CC69` (top and
  bottom tile), YI's SuperFX `$09:EA36` and `$09:EA3D` (plus `$09:EB0C…` for the
  large font).
- **Cheap for a proportional 65816 font, not for a SuperFX burst.** ALTTP:
  11 424 reads in 4000 frames, at most 32 a frame; its cost is inside the
  runs' own spread (13–17 s either way). YI: 7257 reads in 3600 frames but up
  to 1482 in one frame, and the SuperFX's PC costs 15 µs a read (experiment
  7): about 25 ms in that frame, a dropped frame headed. The PC is not needed
  live — the replay has it — so a recorder logs address and clock only.
- **The log is small**: 370 KB (ALTTP) and 230 KB (YI) as text.
- **A breakpoint is enough.** `fontbreak.lua` (`fontbreak.sh <game> <tag> x x
  [frames]`) hooks the same range but removes the callback on its first hit and
  re-arms it at the next frame end, so a text costs at most one callback a
  frame; a hit after 30 quiet frames starts a text. It finds the same starts
  — ALTTP's five, YI's ten, at the same frames — from 357 callbacks instead of
  11 424, and 19 instead of 7257. The hit names the renderer; ALTTP's also
  names the first glyph (`$0E:80B0`, the top tile of `L`), YI's is the lone
  read of the table's first byte.
- **A text may be decoded long before it is drawn.** ALTTP decodes the whole
  prologue at frame 1526 (experiment 5) and draws page 2 from 2132: pinning
  the two ring states before the first font read left the decode out, and the
  capture failed as drawn before the replay. Pinning the ring as it stands at
  the first read keeps the 30 seconds before the text.
- **End to end** (`setup_e2e.py <pause> <keep> "<text>" [font|none]`: the
  shipped recorder with a font setup, headless, paused by script, then the
  session): ALTTP's page 1 paused at 2100 with a 2-state ring replays from the
  pinned 1356 across the gap 1860–1920, matches its hash and traces 80/80
  sources from `$E5968`; without the font the same pause fails as drawn before
  the replay. Page 2 paused at 2760 traces 57/57.
