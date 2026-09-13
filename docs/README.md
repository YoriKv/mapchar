# mapchar documentation

Reference documentation for this repository. `CLAUDE.md` at the repository root
holds only the project-wide rules; everything else lives here.

## Not intended for human consumption

Read at your own risk. These docs are written by and for the assistant that
works in this repository; they are terse, unpolished and change without
notice.

This tree is indexed as the **`mapchar` qmd collection**. Search it rather than
grepping:

```bash
qmd query -c mapchar $'intent: <what you are looking for>\nlex: <exact terms>\nvec: <paraphrase>'
qmd search -c mapchar "<exact terms>" -n 10
qmd update && qmd embed        # re-index after adding or editing docs
```

## Topic index

Every doc in this folder gets a line here: its link and what it covers.

- [romjuice.md](romjuice.md): the romjuice v2.2 script dumper. Command line,
  table dialect, dump algorithm, defects, and how the Windows binary differs.
- [abcde/README.md](abcde/README.md): the abcde translator. Source layout,
  command line, data model, and an index of its topic docs (table files,
  bin2text, text2bin, Cartographer module, Atlas module).
- [table-dialects.md](table-dialects.md): the table-file syntaxes of romjuice,
  Cartographer, Atlas and abcde side by side, and where they collide.
- [development.md](development.md): the Python/uv environment, the two
  virtualenvs, PyCharm files, the source layout and the checks a change must
  pass.
- [ui.md](ui.md): the UI conventions — label capitalisation, what a surface
  does when its room runs out, tooltips over cut-short text, shared keys — and
  the screenshot tool for reviewing the UI.
- [release.md](release.md): building the app with `packaging/build.py`,
  cutting a release with `release.sh`, and the GitHub release workflow.
- [plan/README.md](plan/README.md): the target design of mapchar itself and
  its build order: features, architecture, the native table and script
  formats, the preview system, and phases.

## Conventions

- **Describe the present.** Every document states the current state of the
  repository — not history, not progress, not what was tried before. When a
  change makes a document wrong, fix it in the same change.
- **Add a doc, add an index line.** A new file is linked from this README.
  A folder stays flat until a topic outgrows one file, at which point it gets a
  subfolder with its own README.
