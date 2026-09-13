# mapchar plan

The target design of **mapchar**: a cross-platform text viewer and editor for
romhacking, in the shape of celPix (`../celpix/`, whose `docs/` are the
structural reference) but for the strings in a ROM rather than its graphics.
It finds text, decodes it through table files, edits it beside the original,
writes it back in place, and exchanges dumps with Cartographer, Atlas and
abcde. A layout-and-preview system renders strings through a font sheet to
show how they fit on screen; it is secondary to parsing, editing, export and
import.

Every doc states the design in the present tense, and the source under
`src/mapchar/` follows [architecture.md](architecture.md) module for module.
[phases.md](phases.md) records the order it was built in and what each phase
delivers; every phase is built.

## Topic index

- [features.md](features.md): what the app does and how a user drives it:
  entries, tables, the raw and strings views, search, blocks, pointers,
  writing, dump and import, compression, projects, plugins, undo, keys.
- [architecture.md](architecture.md): how it is built: layers, the data model,
  the decode and encode engines, search engines, the plugin system, the
  `.mapchar` file, the Qt UI, tests and verification against abcde.
- [table-format.md](table-format.md): the native table-file grammar, and how
  the romjuice, Cartographer, Atlas and abcde dialects import into it.
- [script-format.md](script-format.md): the native script (dump) format,
  translator hand-off files, and Cartographer/Atlas import and export.
- [preview.md](preview.md): the layout and preview system: fonts from image
  files, text boxes, wrapping and overflow.
- [phases.md](phases.md): the build order and what each phase delivers.

## Maintaining this section

- A changed decision changes the doc that states it, in the same change.
- These six docs are the reference documentation for mapchar itself, and they
  stay here: `plan/` is where the design is written down, not a staging area
  something graduates from. [phases.md](phases.md) says what each phase
  delivered; everything else says how the app is now.
- Reference behaviour of the tools being replaced stays in
  [`../romjuice.md`](../romjuice.md), [`../abcde/`](../abcde/README.md) and
  [`../table-dialects.md`](../table-dialects.md); this section only says what
  mapchar does with it.
