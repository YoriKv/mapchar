"""Every built-in plugin, registered through the same API user plugins use."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mapchar.plugins.registry import Registry


def register_builtins(registry: Registry) -> None:
    from mapchar.plugins.builtins import (
        charsets,
        compression,
        containers,
        mappings,
    )

    for module in (containers, compression, charsets, mappings):
        module.register(registry)
