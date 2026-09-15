"""ROM containers: flat files, iNES, SNES, GB, GBA, Nintendo 64 and Mega Drive.

A container unwraps a file into the image every published offset, pointer and
table is quoted against. ``read`` publishes where the payload starts
(``KEY_SOURCE_OFFSET``), the bytes a pointer mapping subtracts
(``KEY_HEADER_SIZE``) and the mapping the format implies
(``KEY_SUGGESTED_MAPPING``), and notes anything it had to assume or drop.

``write`` works its destination out from the destination's own bytes, through
the same helper ``read`` used so the two cannot drift, and preserves
everything it did not decode: a copier or iNES header, and a trailing partial
block a deinterleave could not reassemble. A destination that is a **slot**
rather than a whole file is spliced where it lies instead
(:func:`slot_write`), the framing around it being the file's own.

Three optional hooks answer without a full read: ``header_size`` and
``default_mapping`` for the two hints, and ``describe`` for the fields
Container Info lists.

The formats themselves are :mod:`~mapchar.plugins.builtins.containers.nintendo`
and :mod:`~mapchar.plugins.builtins.containers.sega`, over the pieces they share
in ``_common.py``; :func:`register` below is where detection order is decided.
"""

from __future__ import annotations

from mapchar.plugins.base import ContainerField
from mapchar.plugins.builtins.containers._common import (
    Raw,
    format_size,
    publish_hints,
    slot_write,
    splice,
)
from mapchar.plugins.builtins.containers.nintendo import (
    COPIER_HEADER,
    COPIER_MIN_SIZE,
    COPIER_SIZE_MULTIPLE,
    GB_GLOBAL_SUM_AT,
    GB_HEADER_END,
    GB_HEADER_SUM_AT,
    GB_LOGO,
    GB_SUM_RANGE,
    GBA_LOGO,
    INES_HEADER,
    INES_TRAINER,
    KEY_INES_SOURCE,
    KEY_N64_SWAP,
    N64_NATIVE,
    NES_MAGIC,
    GameBoy,
    GameBoyAdvance,
    INes,
    N64Rom,
    Snes,
    SnesHeadered,
    SnesInterleaved,
    copier_header_size,
    n64_swap_width,
    repair_gb_checksums,
    swap_groups,
)
from mapchar.plugins.builtins.containers.sega import Smd

__all__ = [
    "COPIER_HEADER",
    "COPIER_MIN_SIZE",
    "COPIER_SIZE_MULTIPLE",
    "GBA_LOGO",
    "GB_GLOBAL_SUM_AT",
    "GB_HEADER_END",
    "GB_HEADER_SUM_AT",
    "GB_LOGO",
    "GB_SUM_RANGE",
    "INES_HEADER",
    "INES_TRAINER",
    "KEY_INES_SOURCE",
    "KEY_N64_SWAP",
    "N64_NATIVE",
    "NES_MAGIC",
    "ContainerField",
    "GameBoy",
    "GameBoyAdvance",
    "INes",
    "N64Rom",
    "Raw",
    "Smd",
    "Snes",
    "SnesHeadered",
    "SnesInterleaved",
    "copier_header_size",
    "format_size",
    "n64_swap_width",
    "publish_hints",
    "register",
    "repair_gb_checksums",
    "slot_write",
    "splice",
    "swap_groups",
]


def register(registry) -> None:
    # Registration order breaks a tie in detection, so the consoles come in
    # the order their claims are worth checking. The interleaved image comes
    # last: it claims nothing, being picked by hand.
    for plugin in (
        INes(),
        GameBoy(),
        GameBoyAdvance(),
        N64Rom(),
        SnesHeadered(),
        Snes(),
        Smd(),
        Raw(),
        SnesInterleaved(),
    ):
        registry.register(plugin)
