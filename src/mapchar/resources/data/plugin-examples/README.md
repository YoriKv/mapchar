# mapchar plugins

This is your plugin folder. Drop files into the subfolders below and mapchar
picks them up — no reinstall, no editing the app.

**The folder decides what a file is**, so nothing inside it declares its own
type:

| Folder | What goes in it | Takes |
|---|---|---|
| `containers/` | how a file is wrapped: a header to skip, chips to join | `.py` |
| `compression/` | how a text region is packed | `.py`, `.toml` |
| `charsets/` | the glyphs a table starts from | `.py`, `.tbl` |
| `mappings/` | how a pointer value becomes a file offset | `.py`, `.toml` |

Files starting with `_` are ignored. Every `_`-prefixed file here is a working
reference: **copy one, drop the underscore, and edit it.** Press <kbd>F5</kbd>
in mapchar to reload the folder. mapchar rewrites the `_` files at startup so
they track the version you are running, and removes one it no longer ships —
an example never outlives what it taught. Yours never start with `_` and are
never touched.

A `.tbl` file in `charsets/` needs no code at all: it registers as a charset
named after the file, and every table you make can start from it.

## Two kinds of plugin

**`.toml` presets are data.** They name a built-in engine and fill in its
parameters, so a new format is often a handful of numbers and no code. Nothing
executes and mapchar loads them without asking:

- `mappings/_banked.toml` — fixed-size banks mapped at one CPU address, which
  covers most cartridge mappers (`engine = "banked"`)
- `compression/_lzss.toml` — the LZSS family: window and length widths, flag
  order, how a reference is packed (`engine = "lzss"`)
- `compression/_huffman.toml` — Huffman text through a node table in the ROM
  (`engine = "huffman"`)
- `compression/_bitpack.toml` — fixed-width symbols packed across byte
  boundaries, the cheapest text packing there is (`engine = "bitpack"`)

A preset's `id` is what a project stores, so pick one and keep it.

**`.py` plugins are code**, for what the engines cannot express. They run with
the app's privileges, so mapchar asks before loading one and remembers your
answer by the file's SHA-256; editing the file asks again. The default is No.

Each folder carries an `_example.py` of the right shape:

- `containers/_example.py` — a container: `read` unwraps the file, `write`
  puts the payload back. Leaving `write` out makes files read through it
  view-only, which is the honest answer for a format you can only unpack.
- `compression/_example.py` — a compression: `decompress` and, when the
  scheme allows it, `compress`. This one is DTE, where one byte stands for a
  pair of bytes — the most common hand-rolled text packing in 8-bit games.
- `charsets/_example.py` — a charset: `entries()` yields `(bits, text)` pairs.
  For a game whose glyphs are in an order no standard encoding describes.
- `mappings/_example.py` — a mapping: `to_offset` and `to_value`, for a
  pointer layout the banked engine cannot express.

## Where yours appears

Everything from this folder is filed under **Your plugins** in the container,
compression, charset and mapping pickers; a `plugins/` folder beside a
`.mapchar` project file is filed under **Project plugins** and loads with that
project. `MAPCHAR_PLUGIN_PATH` adds further folders of the same shape, which is
the convenient way to work on one outside your app-data folder.

Ids must be unique within a stage, and the built-ins register first: a plugin
of yours that reuses a built-in id is reported rather than replacing it.
