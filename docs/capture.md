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
project is unsaved:

| Path | What |
|---|---|
| `incoming/` | the recorder's moments before the session takes them |
| `<id>/moment.txt` | the capture point (frame, poll, master clock, RAM + VRAM hash) and the ring's states |
| `<id>/sNN.mss`, `input.txt`, `screen.png` | the ring, every polled input since its oldest state, the screenshot |
| `<id>/capture.json` | the typed text, the state, the result |
| `<id>/evidence.log` and `.<memory>`, `.acc` | the replay's log, every RAM at the capture point, the ROM bytes it touched |
| `<id>/_*.lua`, `_*.out` | each launch's generated script and output |
| `review.json` | which proposals were accepted or rejected |

All of it is game data: it never enters the repository.

## Tests

- `tests/test_capture.py` and `tests/test_capture_ui.py` run on synthetic
  evidence and the fake emulator of `tests/capture_fake.py`, in seconds.
- `tests/test_capture_games.py` traces the experiments' games against their
  known answers with Mesen. Opt-in — about half an hour:

  ```bash
  MAPCHAR_CAPTURE_GAMES=1 MAPCHAR_ROMS="/mnt/d/Dev/SNES:/mnt/d/Dev/SNES/roms/earthbound" \
    uv run pytest tests/test_capture_games.py -v
  ```

  `MAPCHAR_ROMS` lists folders holding the ROMs (besides `sample-projects/*/`),
  `MAPCHAR_CAPTURES` the recorded moments (default `tmp/capture-spike/cap`,
  `<game>/capNNNNN.txt` and its files), `MAPCHAR_MESEN` the emulator. A game
  with any of them missing skips.
