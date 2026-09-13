"""The plugin API: what a plugin declares and what each stage must provide."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from mapchar.core.context import PipelineContext


class Stage(Enum):
    CONTAINER = "containers"
    COMPRESSION = "compression"
    CHARSET = "charsets"
    MAPPING = "mappings"

    @property
    def folder(self) -> str:
        """The user plugin subfolder that accepts this stage."""
        return self.value


@dataclass(frozen=True)
class PluginInfo:
    id: str
    name: str
    stage: Stage
    category: str = "Built-in"
    extensions: tuple[str, ...] = ()
    """Lower-case file extensions with the dot, for container detection."""
    magic: tuple[tuple[int, bytes], ...] = ()
    """``(offset, bytes)`` pairs; when declared, all must match to detect."""
    min_size: int = 0
    max_size: int | None = None
    size_multiple: int = 0
    """When non-zero, the file size must be a multiple of this to detect."""
    size_remainder: int = 0
    """With ``size_multiple``, the remainder the size must leave."""


@dataclass(frozen=True)
class ReadSource:
    """What a container reads: the joined file bytes and where they came from."""

    data: bytes
    paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class WriteTarget:
    """What a container writes into: the destination's current bytes."""

    existing: bytes
    paths: tuple[str, ...] = ()


@runtime_checkable
class Plugin(Protocol):
    info: PluginInfo


class Container(Protocol):
    info: PluginInfo

    def read(self, source: ReadSource, ctx: PipelineContext) -> bytes: ...

    # Optional:
    # def write(self, data: bytes, target: WriteTarget, ctx: PipelineContext) -> bytes
    # def describe(self, source: ReadSource, ctx: PipelineContext) -> dict[str, Any]


class Compression(Protocol):
    info: PluginInfo

    def decompress(self, data: bytes, ctx: PipelineContext) -> bytes: ...

    # Optional: def compress(self, data: bytes, ctx: PipelineContext) -> bytes


class Charset(Protocol):
    info: PluginInfo

    def entries(self) -> Iterable[tuple[str, str]]:
        """``(bits, text)`` pairs; text is literal, not script form."""
        ...


class Mapping(Protocol):
    info: PluginInfo
    sizes: tuple[int, ...]

    def to_offset(self, value: int, header: int, bank: int) -> int | None: ...

    def to_value(self, offset: int, header: int, bank: int) -> int: ...


REQUIRED_METHODS: dict[Stage, tuple[str, ...]] = {
    Stage.CONTAINER: ("read",),
    Stage.COMPRESSION: ("decompress",),
    Stage.CHARSET: ("entries",),
    Stage.MAPPING: ("to_offset", "to_value"),
}

SAVE_METHODS: dict[Stage, str | None] = {
    Stage.CONTAINER: "write",
    Stage.COMPRESSION: "compress",
    Stage.CHARSET: None,
    Stage.MAPPING: None,
}


def writes_back(plugin: Any, stage: Stage) -> bool:
    """Whether a byte-stage plugin can run in reverse. Declared by presence."""
    method = SAVE_METHODS.get(stage)
    return method is None or callable(getattr(plugin, method, None))
