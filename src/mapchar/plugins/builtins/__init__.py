"""Every built-in plugin, registered through the same API user plugins use."""

from __future__ import annotations

from mapchar.plugins.registry import Registry


def register_builtins(registry: Registry) -> None:
    from mapchar.plugins.builtins import charsets, containers, reshapes

    for module in (containers, reshapes, charsets):
        module.register(registry)
