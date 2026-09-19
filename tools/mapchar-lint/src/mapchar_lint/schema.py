"""The ``.mapchar`` schema, restated.

This is a deliberate second copy of what ``docs/plan/architecture.md`` §6.3
specifies and ``mapchar/project/projectfile.py`` reads. The linter does not
import that reader, for two reasons:

- It has to run where mapchar is not installed, which is most of what it is for.
- More importantly, **the reader cannot be used to lint**. It is tolerant by
  design: a key nothing reads is ignored, a configuration word that is not a
  setting is passed over, an unknown plugin id becomes a pass-through. By the
  time it hands back an ``Entry`` the evidence of the mistake is gone — the
  very thing a linter is supposed to report. Checking the *document* rather
  than the parse result is the only way to see what was written as opposed to
  what was understood.

The cost of the copy is drift, and it is paid down where it can be: the plugin
ids, the dialects and the project version live in a generated snapshot with a
test behind it (``data/registry.json``, ``tests/test_lint_snapshot.py`` in the
mapchar suite), and the enumerations below carry the source they were taken
from so a reviewer can check a row without hunting for it.
"""

from __future__ import annotations

# -- enumerations ---------------------------------------------------------
#: ``mapchar.core.capabilities.EntryKind``.
KINDS = ("file", "block", "bookmark", "folder", "table")
#: ``EntryKind``s that belong to a file (``workspace.CHILD_KINDS``).
CHILD_KINDS = ("block", "bookmark", "folder")
#: Kinds a project once had, and what became of them (``projectfile._entry_from``).
RETIRED_KINDS = {
    "font": "glyph-sheet fonts are gone; the Preview draws every block in the "
    "system font its Font tab picks",
}
#: The kinds :func:`workspace.free_name` numbers apart (``NAMED_UNIQUELY``).
NAMED_UNIQUELY = ("block", "bookmark")
#: What ``current`` may name and have something to show. A bookmark is refused by
#: the reader; a folder is kept but has no view (``CAPABILITIES``).
KINDS_WITH_VIEW = ("file", "block", "table")

#: ``mapchar.core.block.Status``.
STATUSES = ("untouched", "edited", "review", "done")
#: ``mapchar.core.font.Effect``.
EFFECTS = ("none", "newline", "page", "pause", "space", "end")
#: ``EntrySession.view``.
VIEWS = ("raw", "text", "strings")
DEFAULT_VIEW = "raw"
#: ``Entry.spare_room``.
SPARE_ROOM = ("fill", "keep")
DEFAULT_SPARE_ROOM = "fill"
#: The container a file entry without ``container_id`` gets.
DEFAULT_CONTAINER = "raw"

# -- the keys each kind reads (projectfile._entry_from / entry_dict) --------
COMMON_KEYS = ("kind", "name", "path", "extra_paths", "session")
KEYS_BY_KIND = {
    "file": COMMON_KEYS + ("container_id", "compression_id"),
    "block": COMMON_KEYS
    + (
        "parent",
        "folder",
        "compression_id",
        "slice_offset",
        "slice_length",
        "spare_room",
        "config",
        "strings",
        "fixed_ends_shown",
        "box",
    ),
    "bookmark": COMMON_KEYS + ("parent", "folder", "offset"),
    "folder": COMMON_KEYS + ("parent", "folder"),
    "table": COMMON_KEYS + ("dialect", "charset", "includes", "table", "overlay"),
}
#: Every key some kind reads: one outside this is a typo, not a misplaced key.
ALL_ENTRY_KEYS = frozenset(key for keys in KEYS_BY_KIND.values() for key in keys)
#: ``EntrySession`` as the file stores it.
SESSION_KEYS = ("table_id", "offset", "view", "config", "resolve_pointers")
#: A block's reading is its own ``config``; the writer drops ``session.config``.
SESSION_KEYS_NOT_FOR = {"config": ("block",)}
#: The document's own keys (``project_dict``).
TOP_KEYS = ("version", "current", "entries", "glossary")
#: A string record (``_string_records``): index, original, translation, status,
#: notes.
STRING_KEYS = ("i", "o", "t", "s", "n")
#: A glossary term (``glossary_dicts``).
GLOSSARY_KEYS = ("t", "r", "n")
#: ``TextBox`` as the file stores it, with the reader's defaults.
BOX_DEFAULTS = {
    "width": 128,
    "height": 32,
    "line_height": 8,
    "letter_spacing": 0,
    "lines_per_page": 0,
    "chars_per_line": 0,
}
BOX_KEYS = tuple(BOX_DEFAULTS) + ("origin", "effects")

# -- the configuration line (mapchar.project.formats.script.parse_config) ---
SOURCES = ("range", "pointers", "list", "nested")
STRING_TYPES = ("end", "fixed", "pascal", "next", "lines")
WRITE_MODES = ("packed", "slotted")
ENDIANS = ("little", "big")
#: The words each source reads. Anything else a source is handed is ignored.
SOURCE_KEYS = {
    "range": ("start", "stop"),
    "pointers": (
        "start",
        "stop",
        "size",
        "stride",
        "endian",
        "mapping",
        "offset",
        "bank",
        "null",
    ),
    "list": ("addresses", "size", "endian", "mapping", "offset", "bank", "null"),
    "nested": (
        "start",
        "stop",
        "size",
        "stride",
        "endian",
        "mapping",
        "offset",
        "bank",
        "null",
        "inner_size",
        "inner_endian",
        "inner_null",
    ),
}
#: The words every source takes and without which ``parse_config`` raises.
REQUIRED_SOURCE_KEYS = {
    "range": ("start", "stop"),
    "pointers": ("start", "stop", "size"),
    "list": ("addresses", "size"),
    "nested": ("start", "stop", "size"),
}
#: The words that shape strings, whatever the source.
STRING_KEYS_CONFIG = (
    "type",
    "table",
    "spp",
    "realign",
    "skips",
    "lines",
    "bound",
    "mode",
    "fill",
    "show_end",
    "line_label",
)
CONFIG_KEYS = frozenset(
    {"source"}
    | {key for keys in SOURCE_KEYS.values() for key in keys}
    | set(STRING_KEYS_CONFIG)
)
