"""The registry: every plugin by stage and id, detection, and degradation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mapchar.core.context import PipelineContext
from mapchar.plugins.aliases import current_id
from mapchar.plugins.base import (
    RAW_CONTAINER,
    REQUIRED_METHODS,
    PluginInfo,
    ReadSource,
    Stage,
    WriteTarget,
)


class RegistryError(Exception):
    pass


@dataclass
class PassThrough:
    """Stands in for a missing byte-stage plugin; reads through, never writes."""

    info: PluginInfo
    missing_id: str

    def read(self, source: ReadSource, ctx: PipelineContext) -> bytes:
        return source.data

    def decompress(self, data: bytes, ctx: PipelineContext) -> bytes:
        return data


# How much of a file detection looks at: comfortably past every signature a
# container declares while staying one cheap read on a ROM of tens of megabytes.
SIGNATURE_HEAD = 0x10000


def _claim(info: PluginInfo, ext: str, head: bytes, size: int) -> int:
    """How strongly ``info``'s container claims this file: 2 magic, 1 extension.

    The size rules are narrowing terms, never a claim of their own: they fail
    the whole match rather than contribute to it, so declaring one only makes a
    container more selective. Otherwise every ROM-sized binary would be claimed
    by whichever console's size rule its length happened to satisfy.
    """
    if size < info.min_size or (info.max_size is not None and size > info.max_size):
        return 0
    if info.size_multiple and size % info.size_multiple != info.size_remainder:
        return 0
    if info.magic:
        # Magic is an assertion about the format, so it decides alone — a
        # matching extension cannot rescue a container whose bytes disagree.
        return 2 if any(head[at : at + len(m)] == m for at, m in info.magic) else 0
    return 1 if ext and ext in info.extensions else 0


class Registry:
    def __init__(self) -> None:
        self._plugins: dict[Stage, dict[str, Any]] = {s: {} for s in Stage}

    def register(self, plugin: Any) -> None:
        info: PluginInfo = plugin.info
        for method in REQUIRED_METHODS[info.stage]:
            if not callable(getattr(plugin, method, None)):
                raise RegistryError(
                    f"{info.id}: {info.stage.name} plugin lacks {method}()"
                )
        table = self._plugins[info.stage]
        if info.id in table:
            raise RegistryError(
                f"duplicate plugin id {info.id!r} for {info.stage.name}"
            )
        table[info.id] = plugin

    def plugin(self, stage: Stage, id: str) -> Any | None:
        """The plugin registered as ``id``, trying its current name if it misses.

        A retired id keeps resolving through :mod:`mapchar.plugins.aliases`, so
        a project or a preset written against an older build still opens.
        """
        table = self._plugins[stage]
        if id in table:
            return table[id]
        return table.get(current_id(id))

    def plugins(self, stage: Stage) -> list[Any]:
        return list(self._plugins[stage].values())

    def ids(self, stage: Stage) -> list[str]:
        return list(self._plugins[stage])

    def resolve_stage(self, stage: Stage, id: str | None) -> Any:
        """The plugin, or a ``PassThrough`` that leaves the pathway view-only."""
        if not id:
            return None
        plugin = self.plugin(stage, id)
        if plugin is not None:
            return plugin
        info = PluginInfo(id, f"{id} (missing)", stage, "Missing")
        return PassThrough(info, id)

    def detect_container(
        self, head: bytes, path: str | None = None, size: int | None = None
    ) -> Any:
        """The container that best claims a file, from static info only.

        ``head`` is the file's leading bytes — :data:`SIGNATURE_HEAD` of them is
        enough for every signature declared — and ``size`` its full length,
        defaulting to ``len(head)`` when the whole file is passed. No plugin
        code runs: a container claims files by describing itself, so detection
        is safe to run across untrusted plugins before the file is open.

        Magic decides on its own when declared, otherwise an extension match
        counts, and the **size rules only reject**: a container that recognises
        nothing about a file does not get to claim it because the length
        happens to divide. Nothing claiming the file leaves the flat-file
        container, which is the answer for a plain binary, and a tie goes to
        registration order, so a user plugin never displaces a built-in on an
        equal claim.
        """
        ext = ""
        if path:
            name = path.replace("\\", "/").rsplit("/", 1)[-1].lower()
            ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
        if size is None:
            size = len(head)
        best, best_score = self.plugin(Stage.CONTAINER, RAW_CONTAINER), 0
        for plugin in self.plugins(Stage.CONTAINER):
            info: PluginInfo = plugin.info
            score = _claim(info, ext, head, size)
            if score > best_score:
                best, best_score = plugin, score
        return best


def default_registry() -> Registry:
    from mapchar.plugins.builtins import register_builtins

    registry = Registry()
    register_builtins(registry)
    return registry


__all__ = [
    "PassThrough",
    "Registry",
    "RegistryError",
    "WriteTarget",
    "default_registry",
]
