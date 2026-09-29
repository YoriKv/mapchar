# Capture: running it

How to run text capture here — the emulator it needs, where its files go, and
its tests. The design is [architecture §9](plan/architecture.md#9-capture); the
evidence it rests on is [text-capture.md](text-capture.md).

## The emulator

- **Mesen 2** (MesenCE 2.2.1). mapchar finds `mesen`, `Mesen` or `Mesen.exe`
  on the `PATH`; otherwise **Capture ▸ Emulator Path…** sets it (QSettings key
  `capture/emulator`, per machine).
- On WSL the Linux build is `~/.local/bin/mesen`; a Windows `Mesen.exe` works
  too and is handed Windows paths (`wslpath -w`).
- The GBA core needs a BIOS: `gba_bios.bin` in Mesen's `Firmware/` folder.
- Mesen keeps its settings and unpacked libraries in `$XDG_CONFIG_HOME/MesenCE`
  (`~/.config/MesenCE`). A run with a different config home is a first run and
  opens a window even under `--testRunner`.
- Every launch passes its settings as switches; the user's `settings.json` is
  never written.

## Files

A session's captures are in `<project>.capture/` — beside the ROM while the
project is unsaved. Saving the project for the first time moves them beside
it, by a rename (not across drives, not while something is there already or
the game is played); a project whose folder is missing opens the one beside
the ROM. Saving under another name leaves a project's captures where they
are.

| Path | What |
|---|---|
| `setup.json` | what the setup window was given: the font's memory and range |
| `incoming/` | the recorder's moments before the session takes them; any left there — mapchar was not listening — are taken in when the captures open, and about once a second while the game is played or captures are traced; a description under a second old waits |
| `incoming/rejected/` | a moment's files that never read, set aside after five minutes |
| `_recorder-<time>-<n>.lua`, `.out` | the latest Play's recorder script and output, one name per launch |
| `<id>/moment.txt` | the capture point (frame, poll, master clock, RAM + VRAM hash), the ring's states (pinned ones marked), and the latest text's first and last font read |
| `<id>/sNN.mss`, `input.txt`, `screen.png` | the ring, every polled input since its oldest state, the screenshot |
| `<id>/capture.json` | the typed text, the state, whether the replay finished and matched (`replayed`), the result; one that does not read leaves the capture failed, its text kept when that much reads |
| `<id>/evidence.log` and `.<memory>`, `.acc` | the replay's log (`E pc bus value [ROM offset]`, `W pc bus value [memory:offset]`, `F frame`, `B page` / `U page` for a GBA page's writes dropped / logged again), every memory at the capture point, the ROM bytes it touched — removed whenever a replay is stopped, fails or does not match |
| `<id>/_*.lua`, `_*.out` | each launch's generated script and output, removed once the capture is done |
| `<id>/confirm_*.png`, `sweep_NNNN.png` | Confirm in game's two frames; Show Its Strings in Game's frame per string |
| `review.json` | the proposals marked accepted or rejected, and what each acceptance added (a block's source, the entries as they stood), which counts as accepted while the project holds it |

All of it is game data: it never enters the repository.

## Tests

- `tests/test_capture.py` and `tests/test_capture_ui.py` run on synthetic
  evidence and the fake emulator of `tests/capture_fake.py`, in seconds;
  `tests/test_capture_session.py` covers the session's own life (stopped and
  failed replays, recorder lines, playing, `incoming/`, unreadable files),
  `tests/test_capture_tablesweep.py` a table's strings in the game, and
  `tests/test_capture_review_ui.py` the Captures dock and Review tab.
- `tests/test_capture_chains.py`, `_bitlayout.py`, `_occurrence.py`,
  `_combine.py` and `_proposals.py` test the analysis on synthetic events and
  results, with no emulator.
- `tests/test_capture_consoles.py` covers the profiles and the Mesen bridge's
  switches and paths; `_evidence.py` the replay's process and log; `_probe.py`
  the probe client against a failing server (`FakeEmulator(fault="silent" |
  "cut" | "die")`, `replay_hang=True`); `_trace.py` tracing text drawn into
  VRAM, dictionary words, a RAM buffer, a byte read twice and a RAM mirror.
  None of them runs the scripts' Lua: the fake mirrors the probe script's
  rules for read substitutes (by PC, n-th read, after the n-th write, a GBA
  byte's lane) but only `test_capture_games.py` shows the scripts keep them.
  Covered only there, or not at all: the hooks and address conversion per
  console, the hash, the GBA page guard, the VRAM window's stillness, the
  ROM-write readback, the idle wait, and the recorder (state loads, rewinds,
  repeated breaks, moment ids) — which no test runs.
- `tests/test_capture_games.py` traces the experiments' games against their
  known answers with Mesen. Opt-in — about half an hour:

  ```bash
  MAPCHAR_CAPTURE_GAMES=1 MAPCHAR_ROMS="/mnt/d/Dev/SNES:/mnt/d/Dev/SNES/roms/earthbound" \
    uv run pytest tests/test_capture_games.py -v
  ```

  `MAPCHAR_ROMS` lists folders holding the ROMs (besides `test-data/*/`),
  `MAPCHAR_CAPTURES` the recorded moments (default `tmp/capture-spike/cap`,
  `<game>/capNNNNN.txt` and its files), `MAPCHAR_MESEN` the emulator. A game
  with any of them missing skips.
