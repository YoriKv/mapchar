"""What kind of thing an entry holds, and which controls that kind supports.

The editor shows five kinds of entry in the same window — a file, a block cut
out of one, a bookmark into one, a folder grouping them, a table — and most of
the window applies to some of them and not others. Written per control, that
answer is spread across every ``_sync_*`` method the window has, and the set a
table entry supports is knowable only by reading all of them. So it is written
down once here instead: a :class:`Capability` is one thing the editor can do, a
kind declares the set it supports, and
:meth:`~mapchar.ui.main_window.capability_sync.CapabilitySyncMixin._sync_capabilities`
applies the table at the tail of the refresh cycle.

A capability is a tag on the *control*, not an implementation: gating says
whether a control applies, and the mixin that owns it still decides what it
does and whether its own preconditions are met. Qt-free, so the table is
testable without a window.
"""

from __future__ import annotations

from enum import Enum, auto


class EntryKind(Enum):
    """What an entry is: a file, a block cut out of one, a bookmark into one, a
    folder grouping a file's blocks and bookmarks, or a table.

    ``value`` is the string the project file stores, so the on-disk schema is a
    name rather than an ordinal that reordering this enum would silently change.
    Here rather than in :mod:`mapchar.project.entry` because it is the key of
    :data:`CAPABILITIES` and ``core`` is the bottom layer; the entry module
    imports it from here, so ``from mapchar.project.entry import Entry,
    EntryKind`` names the row and its kind in one line.
    """

    FILE = "file"
    BLOCK = "block"
    BOOKMARK = "bookmark"
    FOLDER = "folder"
    TABLE = "table"


class Capability(Enum):
    """One thing the editor can do to an entry, as a gate on its controls."""

    # -- reading bytes
    NAVIGATION = auto()  # the offset box, the steps, Go to Address, the address row
    RAW_VIEW = auto()  # the Hex and Text tabs
    HEX_VIEW = auto()  # the Hex dock's dump and its overtype line
    CODECS = auto()  # the Codecs and Reading bars: how the bytes are read
    SEARCH = auto()  # Find bytes, the Search window, the text scan
    COMPRESSION_SCAN = auto()  # the Decompressed view's structure scan
    POINTER_DISCOVERY = auto()  # Find Pointers to this entry's strings

    # -- making entries out of them
    CONTAINER = auto()  # Edit File Container
    BLOCK_CREATE = auto()  # New Block, and New Block from Selection
    BOOKMARK = auto()  # New Bookmark at the view position

    # -- strings
    STRINGS = auto()  # the Strings tab, its rows and their edits
    BLOCK_CONFIG = auto()  # the Block bar: what the block's reading came to
    FIND_REPLACE = auto()  # Find and Replace over translations
    WRAP = auto()  # wrapping a translation to the block's text box
    PREVIEW = auto()  # the Preview window's drawing of a string
    IMPORT_EXPORT = auto()  # the exchange formats over this entry's strings

    # -- writing
    WRITE = auto()  # File ▸ Write on this entry
    RELOAD = auto()  # File ▸ Reload from Disk on this entry's file

    # -- the other kind
    TABLE_EDIT = auto()  # the Table Editor over this entry's tables


# Every entry that is a window on a run of bytes reads them the same way,
# whether those bytes are a whole file or a block cut out of one.
_BYTE_LEVEL = frozenset(
    {
        Capability.NAVIGATION,
        Capability.RAW_VIEW,
        Capability.HEX_VIEW,
        Capability.CODECS,
        Capability.SEARCH,
        Capability.COMPRESSION_SCAN,
        Capability.BLOCK_CREATE,
        Capability.BOOKMARK,
        Capability.WRITE,
        Capability.RELOAD,
        # Here rather than with the string surfaces because importing is how
        # blocks are *created*: a Cartographer command file or an Atlas script
        # read against a whole file makes the blocks it names, so the row has to
        # be live before there is a block to gate it on.
        Capability.IMPORT_EXPORT,
    }
)

CAPABILITIES: dict[EntryKind, frozenset[Capability]] = {
    # A file is the whole window on a run of bytes, and the only entry whose
    # container chain is its own to edit — a block reads through its parent's.
    EntryKind.FILE: _BYTE_LEVEL | {Capability.CONTAINER},
    # A block is a file's bytes plus the reading of them as strings, so it adds
    # every string surface and keeps every byte one.
    EntryKind.BLOCK: _BYTE_LEVEL
    | {
        Capability.STRINGS,
        # A block, not a file: the search is over the file's bytes but for
        # pointers to *these strings*, so there is nothing to look for until a
        # block has read some.
        Capability.POINTER_DISCOVERY,
        Capability.BLOCK_CONFIG,
        Capability.FIND_REPLACE,
        Capability.WRAP,
        Capability.PREVIEW,
    },
    # A bookmark is a *position*, not a view: activating one jumps the view that
    # owns those bytes there and leaves that entry current, so a bookmark is
    # never the entry on screen and has no controls of its own.
    EntryKind.BOOKMARK: frozenset(),
    # A folder only groups rows in the Files panel: selecting one leaves the
    # view as it was, as a group heading does, so it has no controls either.
    EntryKind.FOLDER: frozenset(),
    # A table is edited in one surface of its own — the Table Editor — and has
    # no byte window to navigate, search or write back.
    EntryKind.TABLE: frozenset({Capability.TABLE_EDIT}),
}


def supports(kind: EntryKind | None, capability: Capability) -> bool:
    """Whether ``kind`` supports ``capability`` — the one gate every control asks.

    ``None`` is the nothing-open state and supports nothing, which is what
    leaves an empty window gated rather than showing whatever the last entry
    needed. Named rather than left as a set lookup at each call site so an
    unknown kind is a clean ``False`` instead of a ``KeyError`` mid-refresh.
    """
    return capability in CAPABILITIES.get(kind, frozenset())
