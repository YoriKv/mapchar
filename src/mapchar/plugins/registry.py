"""The registry: every plugin by stage and id, detection, and degradation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mapchar.core.context import PipelineContext
from mapchar.plugins.base import (
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


class Registry:
    def __init__(self) -> None:
        self._plugins: dict[Stage, dict[str, Any]] = {s: {} for s in Stage}
        self.aliases: dict[str, str] = {}

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
        table = self._plugins[stage]
        if id in table:
            return table[id]
        alias = self.aliases.get(id)
        return table.get(alias) if alias else None

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

    def detect_container(self, data: bytes, path: str | None = None) -> Any:
        """Score containers from static info only; ties go to registration."""
        ext = ""
        if path:
            name = path.replace("\\", "/").rsplit("/", 1)[-1].lower()
            ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
        best, best_score = None, -1
        size = len(data)
        for plugin in self.plugins(Stage.CONTAINER):
            info: PluginInfo = plugin.info
            if size < info.min_size or (
                info.max_size is not None and size > info.max_size
            ):
                continue
            if info.size_multiple and size % info.size_multiple != info.size_remainder:
                continue
            score = 0
            if info.magic:
                if all(data[off : off + len(m)] == m for off, m in info.magic):
                    score = 3
                else:
                    continue
            elif ext and ext in info.extensions:
                score = 2
            elif info.size_multiple:
                score = 1
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
