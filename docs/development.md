# Development environment

How the repository is set up for day-to-day work: the Python environment,
the editor, and the checks a change has to pass.

## Python and uv

- **Python 3.12**, pinned in `.python-version`; `pyproject.toml` allows
  `>=3.10`.
- **uv** manages dependencies and the virtualenv. `uv.lock` is committed.
- **Two virtualenvs live side by side**, because binaries cannot be shared
  between Windows and WSL:
  - `.venv` — Windows (the default name, so PyCharm on Windows finds it);
  - `.venv-linux` — WSL/Linux, selected by `UV_PROJECT_ENVIRONMENT=.venv-linux`.
- **`.envrc`** (direnv) exports that variable, puts `.venv-linux/bin` and
  `~/.bun/bin` (for `qmd`) on `PATH`. Run `direnv allow` once. A shell that
  has not loaded direnv, any non-interactive one, needs
  `eval "$(direnv export bash 2>/dev/null)"` or the explicit
  `export UV_PROJECT_ENVIRONMENT=.venv-linux` first, or a bare `uv sync`
  overwrites the Windows `.venv` with Linux binaries.
- `uv sync` creates the environment; `uv run mapchar` runs the app;
  PyCharm's inspections (`ide_diagnostics`) on the files a change touched,
  then `uv run pytest`, `uv run ruff check .` and
  `uv run ruff format --check .`,
  are the checks. `tools/` is git-ignored but for `tools/mapchar-lint/`, so
  ruff passes the other scripts over: a change there runs
  `uv run ruff check tools/…` and `uv run ruff format tools/…` on what it
  touched.

## Git

- **Every git command runs through `git.exe`**, the Windows install, from WSL
  as well. It is the one carrying the committer identity; WSL's own `git` has
  none and stops at `empty ident name` instead of committing.
- **Its paths are repository-relative.** A Windows process cannot read a
  `/mnt/d` path: `git.exe log -- docs/ui.md` works where the absolute one
  fails with `Invalid path '/mnt'`.
- **`core.autocrlf` is `false`** there, so it leaves alone the LF that
  `.gitattributes` asks for.

## PyCharm

- `.idea/` is gitignored and holds the machine-specific module and
  interpreter settings; `.run/mapchar.run.xml` is the shared run
  configuration, launching `python -m mapchar` with the project interpreter.
- `.editorconfig` mirrors the ruff settings (88 columns, 4 spaces, LF, UTF-8)
  so the IDE and the linter agree.
- Python symbols are navigated through the PyCharm MCP — `ide_search_text`,
  `ide_find_references`, `ide_find_definition` and its refactoring tools —
  rather than by grep; the documentation is searched through qmd
  (see [README.md](README.md)).

## Layout

```
mapchar/
├── src/mapchar/         the app package
│   ├── core/            the data model (Qt-free)
│   ├── engines/         decode, encode, search, scan, layout (Qt-free)
│   ├── pipeline/        stages, extraction, insertion (Qt-free)
│   ├── plugins/         plugin API, registry, built-ins (Qt-free)
│   ├── project/         entries and the open-entries model, project file, table, script and exchange formats (Qt-free)
│   ├── ui/              the PySide6 application
│   └── resources/       package data
├── tests/               pytest, flat, one module per area
│   └── fixtures/abcde/  synthetic ROM, tables, command file and abcde's dump of them
├── tools/               mapchar-lint/ (the project-file linter, see lint.md), and
│                       regen_fixtures.py, dump_script.py, subset_icon_font.py
│                       (gitignored)
├── local-tools/         the sample builders, their tests and the screenshot
│                       scripts (see ui.md); gitignored
├── screenshots/         the six PNGs README.md's gallery shows
├── packaging/           build.py: the PyInstaller recipe (see release.md)
├── .github/workflows/   release.yml: the tag-driven release build
├── release.sh           cuts a release (see release.md)
├── CHANGELOG.md         release notes, one section per version
├── docs/                this documentation
├── sample-projects/     the sample projects, each beside its ROM (gitignored)
├── test-data/           the ROMs the tests read, one folder a game (gitignored)
└── tmp/                 scratch (gitignored)
```

- **Test ROMs**: a test that needs a game reads it from
  `test-data/<game>/<ROM>` (gitignored) through `conftest.game_rom`, and
  skips without it; no test reads `sample-projects/`, whose ROMs and projects
  are the user's to change.
- **Sample projects**: the sample builders live in `local-tools/`
  (gitignored), their tests in `local-tools/tests/` (`uv run pytest
  local-tools/tests`), and each sample's `README.md` in
  `sample-projects/<game>/` documents it. A suite test that loads a builder
  through `conftest.local_tool` skips without it:
  `tests/test_lint_snapshot.py` builds every sample whose ROM is in
  `test-data/` into a scratch folder and lints it.
- **Verification fixtures**: `tests/test_verify_abcde.py` compares mapchar's
  extraction with abcde's Cartographer dump of the synthetic ROM in
  `tests/fixtures/abcde/`. The fixtures are checked in, so the suite never runs
  abcde itself; `tools/regen_fixtures.py` regenerates them, and
  `tests/fixtures/abcde/DIVERGENCES.md` lists where mapchar departs from the
  reference tools on purpose.
- **Dumping a script**: `uv run python tools/dump_script.py <project.mapchar>
  [-o out.txt] [-b BLOCK]… [-m originals|translations|both]` writes a project's
  blocks as a native script. The app does not offer it — the format is prior
  art the project file has replaced, and it stays here for comparing an
  extraction with another tool's
  ([plan/script-format.md](plan/script-format.md#native-script)). Headless,
  driving an offscreen window, since reading a block is the whole pipeline.

- **Only `mapchar.ui` and `mapchar.app` import Qt.**
- **Theme.** `src/mapchar/ui/theme.py` puts a `QPalette` on the Fusion
  style, light or dark, and pins the platform colour scheme first so the
  light palette never follows a dark desktop. A widget that bakes a palette
  colour into a pixmap asks `icon_font.themed_icon` for the art and mixes in
  `icon_font.ThemedIcons`, whose `changeEvent` re-bakes it on `PaletteChange`.
- **Icons** are glyphs from `src/mapchar/resources/fonts/material-symbols-subset.ttf`,
  named in the Qt-free `src/mapchar/ui/glyphs.py` and rasterised by
  `src/mapchar/ui/icon_font.py`. The subset holds exactly the enum's
  codepoints: after adding a member, run
  `uv run --with fonttools tools/subset_icon_font.py <MaterialSymbolsOutlined.ttf>`
  with the upstream variable font, or `tests/test_icon_font.py` fails. Words
  stay on the buttons the font has no mark for (`−B`, `+B`, the page steps).
- **Names.** The app's display name is **mapChar** (`APP_NAME` in
  `src/mapchar/__init__.py`), used in window titles, dialogs and the About box.
  The package, the command, file extensions, table and script headers, the
  `QSettings` keys and the release archives stay `mapchar`.
- **App icon.** celPix's rounded square with four glyphs in place of its tiles,
  one each from Latin, Cyrillic, Japanese and Arabic, chosen to look like
  "char": Latin c, Cyrillic һ (shha), hiragana あ and Arabic ر. It
  lives in `src/mapchar/resources/icons/app.png` and, for builds, in
  `packaging/mapchar.ico` and `packaging/mapchar.icns`.
- **Tests** run headless: `tests/conftest.py` forces the offscreen platform,
  isolates `QSettings`, and marks any module that mentions Qt with `qt`, so
  `uv run pytest -m "not qt"` runs the model layer alone. It also deletes the
  widgets each test closed: a test run has no event loop to carry out
  `deleteLater`, so every window would otherwise live to the end of the run.
  Anything installed on the `QApplication` — an event filter above all — is
  paid for by every test after it, so it is one shared object or is taken off
  when its window closes. Qt-free helpers shared by test modules live in
  `tests/helpers.py`, those the compression modules share in
  `tests/compression_helpers.py`, and those that drive a live `MainWindow` in
  `tests/window_helpers.py`; the `window` fixture every window test takes is
  in `tests/conftest.py`, which imports `window_helpers` lazily so the
  headless run stays Qt-free.
- **Line endings** are LF everywhere (`.gitattributes`); paths in docs and
  code are repository-relative.
