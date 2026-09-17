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
  `uv run pytest`, `uv run ruff check .` and `uv run ruff format --check .`
  are the checks.

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
- Python navigation is by grep; the documentation is searched through qmd
  (see [README.md](README.md)).

## Layout

```
mapchar/
├── src/mapchar/         the app package
│   ├── core/            the data model (Qt-free)
│   ├── engines/         decode, encode, search, scan, layout (Qt-free)
│   ├── pipeline/        stages, extraction, insertion (Qt-free)
│   ├── plugins/         plugin API, registry, built-ins (Qt-free)
│   ├── project/         workspace, project file, table, script and exchange formats (Qt-free)
│   ├── ui/              the PySide6 application
│   └── resources/       package data
├── tests/               pytest, flat, one module per area
│   └── fixtures/abcde/  synthetic ROM, tables, command file and abcde's dump of them
├── tools/               regen_fixtures.py, make_sample_projects.py,
│                       subset_icon_font.py, ui_screenshots.py, samples/
├── packaging/           build.py: the PyInstaller recipe (see release.md)
├── .github/workflows/   release.yml: the tag-driven release build
├── release.sh           cuts a release (see release.md)
├── CHANGELOG.md         release notes, one section per version
├── docs/                this documentation
└── tmp/                 scratch (gitignored)
```

- **Example projects**: `tests/test_examples.py` runs the Super Mario World
  sample whose tables and command file live in `tools/samples/Super Mario
  World/` (the 22 message-box messages and the 57 level-name parts of the
  unheadered USA ROM, located from the SMW disassembly). The ROM goes in
  `sample-projects/Super Mario World/` (gitignored) under the name
  `tools/make_sample_projects.py` lists; without it the test skips. `uv run
  python tools/make_sample_projects.py [game…]` copies each game's tables and
  command file beside its ROM and saves a `<game>.mapchar` project there,
  ready to open; named games are the only ones built.
- **Mother 3**: `tools/mother3_sample.py` derives the sample's tables (`m3`,
  `m3battle`, `saturn`) and its 20 blocks — 12,997 strings, 7,825 of them in
  **Script**, one block over the main script's nested offset tables — from
  `sample-projects/Mother 3/Mother 3 (Japan).gba`, since nothing extracted
  from a ROM is checked in; its docstring says which ROM structure and routine
  each fact comes from, and `FOLDERS` which Files panel folder each block goes
  in. `tests/test_examples.py` reads every block and edits
  through all three tables, and in a map of the script whose last page is a
  string of its own. A string commit in **Script** takes about a third of a
  second. The project reads every block as it opens, in
  about four seconds.
- **Verification fixtures**: `tests/test_verify_abcde.py` compares mapchar's
  extraction with abcde's Cartographer dump of the synthetic ROM in
  `tests/fixtures/abcde/`. The fixtures are checked in, so the suite never runs
  abcde itself; `tools/regen_fixtures.py` regenerates them, and
  `tests/fixtures/abcde/DIVERGENCES.md` lists where mapchar departs from the
  reference tools on purpose.

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
  `uv run pytest -m "not qt"` runs the model layer alone. Qt-free helpers
  shared by test modules live in `tests/helpers.py`.
- **Line endings** are LF everywhere (`.gitattributes`); paths in docs and
  code are repository-relative.
