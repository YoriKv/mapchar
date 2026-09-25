# mapchar documentation

Reference documentation for this repository.

## Not intended for human consumption

Read at your own risk. These docs are written by and for the assistant that
works in this repository; they are terse, unpolished and change without
notice.

This tree is indexed as the **`mapchar` qmd collection**. Search it rather than
grepping, and search it through the **qmd plugin's tools** rather than its CLI:

- **`query`** — scope it with `collections: ["mapchar"]`, give it an `intent`
  and typed sub-queries: `lex` for exact terms (`"quoted phrase"`, `-negation`),
  `vec` for a paraphrase, `hyde` for a sketch of the answer itself. The first
  sub-query carries twice the weight. `rerank: false` when only the keywords
  matter and the wait does not.
- **`get`** and **`multi_get`** — the whole of a hit, by the path or `#docid`
  the query returned; `get` takes a line range, `multi_get` a glob.

The CLI covers only what has no tool — re-indexing, and the collection itself:

```bash
qmd update && qmd embed -c mapchar    # after adding or editing a doc
qmd collection show mapchar           # where the collection points
qmd context list mapchar              # the summaries attached to it
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
  virtualenvs, the Windows git install, PyCharm files, the source layout and
  the checks a change must pass.
- [ui.md](ui.md): the UI conventions — label capitalisation, what a surface
  does when its room runs out, tooltips over cut-short text, shared keys —
  the screenshot tool for reviewing the UI, and `wiki/` with the tool that
  takes its tutorial's screenshots, and the tool that takes the README gallery's.
- [release.md](release.md): building the app with `packaging/build.py`,
  cutting a release with `release.sh`, and the GitHub release workflow.
- [lint.md](lint.md): `tools/mapchar-lint`, the standalone checker for
  hand-edited `.mapchar` files — running it, its codes, the registry snapshot
  and the test that keeps it current.
- [text-capture.md](text-capture.md): the exploration of capturing text from
  a game running in an emulator (Mesen2 first) — constraints, the probe and
  bridge shape, what Mesen's Lua API can and cannot see, the capture ideas,
  and the experiments' findings.
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
