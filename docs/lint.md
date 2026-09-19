# mapchar-lint

`tools/mapchar-lint` is a standalone checker for `.mapchar` project files,
aimed at files written by hand or by a generator, where nothing like the app
keeps a state from being reached. The project reader is tolerant: an entry
that does not read is dropped with one line in the load notice, a key or
configuration word it does not know is passed over, an unknown plugin id
becomes a pass-through. The linter reports what a load would silently change.

## Running it

```bash
PYTHONPATH=tools/mapchar-lint/src python3 -m mapchar_lint sample-projects/
uv tool install ./tools/mapchar-lint && mapchar-lint my.mapchar   # as a command
```

Any Python from 3.10 runs it; it needs nothing but the standard library. A
directory argument lints every `.mapchar` under it. Exit status: **1** when
something at or above `--fail-on` (default `error`) is found, **0** otherwise,
**2** for the run's own failures (no such path).

| Flag | Does |
|---|---|
| `--format json` | every finding with a JSON Pointer at its value — the form for a tool or an agent fixing the file |
| `--min-severity` | hide findings below `error`, `warning` or `info` (default `info`) |
| `--fail-on` | what earns exit 1: `error`, `warning`, `info` or `never` |
| `--live` | check plugin ids against the installed mapchar and the user's plugin presets instead of the shipped snapshot |
| `--no-files` | skip everything that reads the disk: missing files, addresses past a file's end, table ids from table files |
| `--select` / `--ignore` | comma-separated codes; a letter selects a level (`E`), a prefix a family (`E6`, `W52`) |

## Severity is what the loader does

- **error** — the reader drops or misreads it: something the file says is not
  in the project that opens, or is gone at the next save.
- **warning** — it degrades, probably not as meant: a row shown outside its
  folder, a reading that falls back to the default.
- **info** — the project opens as written; the file is only not in the form
  mapchar writes (a default spelled out, an older version, a renamed id).

## Codes

| Codes | About |
|---|---|
| `F0xx` | the file is not a project at all — unreadable, not UTF-8 JSON, not an object, `entries` not an array; nothing else runs |
| `E1xx` `W1xx` `I1xx` | the document: `version`, `current`, top-level keys, the glossary, a byte-order mark |
| `E20x`–`E212` | what drops an entry: not an object, no kind, the retired `font` kind, a path or `extra_paths` that is not text, an integer `int()` refuses, a `strings`, `box` or `session` of the wrong shape |
| `W213`–`E219` | keys no kind reads (`E214`) or this kind does not (`W215`), values `int()` coerces (`3.7` → 3), a file with no path, an index that is not a number |
| `E26x` `W26x` | the session: unknown keys, a view that is not `raw`/`text`/`strings` |
| `E3xx` `W3xx` | the disk: missing, empty or folder paths, one file opened twice, a block's addresses, a bookmark or a slot past the end of its file (measured over `path` and `extra_paths` joined) |
| `E4xx` `W4xx` `I4xx` | plugin ids — container, compression, mapping, charset — and table dialects, renamed ids, a pointer size the mapping does not offer |
| `E5xx` `W5xx` | references between entries: parents, folders, names two blocks share, the table a block, a session or an include names, table ids two tables share |
| `E60x`–`W625` | the block's configuration line, read as `parse_config` reads it: what raises (the block is dropped) and what it passes over (`tabel=main`, `stride` on a list source) or misreads (`endian=Big` reads as little) |
| `E64x` `W65x` | a block's saved strings, text box and compression slot |
| `E7xx` `W7xx` | a table entry's overlay and includes |

A file's `session.config` uses the configuration grammar but is only a view
setting, so what drops a block is a warning there, and its empty
`source=range start=$0 stop=$0` — what the app writes — is not reported.

What no linter catches is a reference that shifted: `parent`, `folder` and
`current` are positions in `entries`, and one that lands on another good file
opens quietly wrong.

## The schema is restated, not imported

`mapchar_lint.schema`, `reading` and `config` restate the reader
(`project/projectfile.py`, `formats/script.parse_config`): the linter runs
where mapchar is not installed, and the reader cannot lint — by the time it
returns an `Entry` the unknown key is gone and the bad word passed over. A
change to the project format or the configuration grammar changes these
modules in the same change.

Plugin ids, pointer sizes, renamed ids, table dialects and the project version
come from `src/mapchar_lint/data/registry.json`, a snapshot of the built-in
registry that `mapchar_lint.snapshot` builds. `tests/test_lint_snapshot.py`
fails when it goes stale, and checks that every sample project lints without
an error; regenerate it with:

```bash
uv run python tools/mapchar-lint/generate_snapshot.py
```

The `plugins/` folder beside a project is read first: its presets' ids count
as present for that project, and a stage whose folder holds a code plugin —
whose ids cannot be read without running it — reports an unknown id as a
warning rather than an error. `--live` never runs code plugins either.

## Tests

`tools/mapchar-lint/tests` runs with the suite (`uv run pytest`), from the
package's own folder too (`uv run --project ../.. pytest`). They write real
projects, ROMs and table files to a temp folder, since half of what is tested
is the reading. The negative cases — a legal state that must stay quiet — carry
as much weight as the positive ones.
