"""The byte stages, run forward to load and backward to save.

load:  file(s) ─► CONTAINER.read ─► RESHAPE.reshape ─► COMPRESSION.decompress
save:  file(s) ◄─ CONTAINER.write ◄─ RESHAPE.unshape ◄─ COMPRESSION.compress
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from mapchar.core.context import KEY_SOURCE_FILES, PipelineContext
from mapchar.core.errors import MapcharError
from mapchar.plugins.base import ReadSource, Stage, WriteTarget, writes_back
from mapchar.plugins.registry import PassThrough, Registry


class PipelineError(MapcharError):
    def __init__(self, stage: Stage, action: str, plugin: str, message: str):
        self.stage = stage
        self.action = action
        self.plugin = plugin
        super().__init__(f"[{stage.name.lower()}:{action}] {plugin}: {message}")


@dataclass(frozen=True)
class FileRef:
    """Where bytes live: one or more files joined end to end, or memory."""

    paths: tuple[str, ...] = ()
    data: bytes | None = None
    """When set, read this instead of the files (unsaved parent buffers)."""

    def read(self) -> bytes:
        if self.data is not None:
            return self.data
        chunks = []
        for path in self.paths:
            with open(path, "rb") as f:
                chunks.append(f.read())
        return b"".join(chunks)

    def sizes(self) -> list[int]:
        return [os.path.getsize(p) for p in self.paths]


@dataclass(frozen=True)
class PathwayConfig:
    source: FileRef
    container_id: str = "raw"
    reshape_id: str | None = None
    compression_id: str | None = None


@dataclass
class Loaded:
    data: bytes
    """The decompressed payload every later stage works on."""
    ctx: PipelineContext
    writable: bool
    missing_plugins: list[str] = field(default_factory=list)
    raw: bytes = b""
    """The bytes as read from the files, before the container."""


def _run(stage: Stage, plugin: Any, action: str, fn, *args):
    try:
        return fn(*args)
    except MapcharError:
        raise
    except Exception as exc:
        raise PipelineError(stage, action, plugin.info.id, str(exc)) from exc


def load(config: PathwayConfig, registry: Registry) -> Loaded:
    ctx = PipelineContext()
    raw = config.source.read()
    ctx.set(KEY_SOURCE_FILES, config.source.paths)
    stages = _stages(config, registry)
    missing = [p.missing_id for _, p in stages if isinstance(p, PassThrough)]
    writable = all(writes_back(p, s) for s, p in stages)

    container = stages[0][1]
    data = _run(
        Stage.CONTAINER,
        container,
        "read",
        container.read,
        ReadSource(raw, config.source.paths),
        ctx,
    )
    for stage, plugin in stages[1:]:
        if stage is Stage.RESHAPE:
            data = _run(stage, plugin, "reshape", plugin.reshape, data, ctx)
        else:
            data = _run(stage, plugin, "decompress", plugin.decompress, data, ctx)
    return Loaded(data, ctx, writable, missing, raw)


def _stages(config: PathwayConfig, registry: Registry) -> list[tuple[Stage, Any]]:
    stages = [
        (Stage.CONTAINER, registry.resolve_stage(Stage.CONTAINER, config.container_id))
    ]
    if config.reshape_id:
        stages.append(
            (Stage.RESHAPE, registry.resolve_stage(Stage.RESHAPE, config.reshape_id))
        )
    if config.compression_id:
        stages.append(
            (
                Stage.COMPRESSION,
                registry.resolve_stage(Stage.COMPRESSION, config.compression_id),
            )
        )
    return stages


def encode_for_save(
    data: bytes,
    config: PathwayConfig,
    registry: Registry,
    existing: bytes,
    ctx: PipelineContext,
) -> bytes:
    """Run the stages backward: the whole new file contents."""
    stages = _stages(config, registry)
    for stage, plugin in reversed(stages[1:]):
        if not writes_back(plugin, stage):
            raise PipelineError(
                stage, "write", plugin.info.id, "plugin cannot write back"
            )
        if stage is Stage.COMPRESSION:
            data = _run(stage, plugin, "compress", plugin.compress, data, ctx)
        else:
            data = _run(stage, plugin, "unshape", plugin.unshape, data, ctx)
    container = stages[0][1]
    if not writes_back(container, Stage.CONTAINER):
        raise PipelineError(
            Stage.CONTAINER, "write", container.info.id, "container cannot write back"
        )
    target = WriteTarget(existing, config.source.paths)
    return _run(Stage.CONTAINER, container, "write", container.write, data, target, ctx)


def deposit(new_bytes: bytes, config: PathwayConfig) -> list[str]:
    """Split a whole-file result across joined files; rewrite only changed ones."""
    written: list[str] = []
    sizes = config.source.sizes()
    if sum(sizes) != len(new_bytes) and len(config.source.paths) > 1:
        raise PipelineError(
            Stage.CONTAINER, "write", config.container_id, "joined files changed size"
        )
    pos = 0
    for path, size in zip(config.source.paths, sizes, strict=True):
        chunk = new_bytes[pos : pos + size] if len(sizes) > 1 else new_bytes
        pos += size
        with open(path, "rb") as f:
            if f.read() == chunk:
                continue
        with open(path, "wb") as f:
            f.write(chunk)
        written.append(path)
    return written
