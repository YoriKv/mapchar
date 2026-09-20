"""The byte stages, run forward to load and backward to save.

load:  file(s) ─► CONTAINER.read ─► COMPRESSION.decompress
save:  file(s) ◄─ CONTAINER.write ◄─ COMPRESSION.compress

Two rules run through all of it. A **required** call that fails hard-stops with
a :class:`~mapchar.core.errors.PipelineError` naming the stage, the direction
and the plugin (:func:`_run`), and nothing partial is written. An **optional**
one that fails is read as one that was never written — a default plus a notice
(:func:`_probe`) — because absence is already defined for every hook that has
one, and losing a file over a piece of metadata the host has an answer for
would be the worse trade.

Everything a slot needs is stated in :func:`compress_for_slot`: a bounded slot
refuses a longer result, an unbounded one is bounded by the end of what holds
it, and the room a shorter result leaves is the config's
:class:`SlotFill`. :func:`save` then delivers the bytes; a block that lives
inside a parent's buffer takes the same checks and splices the result itself.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypeVar

from mapchar.core.context import (
    KEY_HEADER_SIZE,
    KEY_SOURCE_FILES,
    KEY_SUGGESTED_MAPPING,
    PipelineContext,
    SourceSpan,
)
from mapchar.core.errors import MapcharError, PipelineError, Stage
from mapchar.pipeline.filechange import current_bytes
from mapchar.plugins.base import RAW_CONTAINER, ReadSource, WriteTarget, writes_back
from mapchar.plugins.registry import PassThrough, Registry

T = TypeVar("T")

__all__ = [
    "FileRef",
    "Loaded",
    "MapcharError",
    "PathwayConfig",
    "PipelineError",
    "SlotFill",
    "Stage",
    "bind_tables",
    "compress_for_slot",
    "load",
    "save",
]


class SlotFill(str, Enum):
    """What becomes of a bounded slot's tail when a re-compression comes up short.

    A recompressor rarely reproduces the packing the original build used, so a
    re-encoded stream routinely lands smaller than the one it replaces and
    leaves room at the end of the slot the new stream never reaches. Every
    scheme is self-delimiting there, so nothing *reads* those bytes either way —
    what this decides is what someone looking at the file finds.

    - ``FILL`` pads to the slot with the block's fill byte, so the slack is
      visible in a hex dump and reads as unused.
    - ``KEEP`` writes only what was produced and leaves the previous stream's
      tail standing. The conservative answer, and the only one that cannot
      destroy data a slot's bounds wrongly claimed: a length measured to the
      next known offset can take in an alignment pad or a neighbour's header,
      and those bytes are only ours to overwrite if the bounds were right.

    String-valued because the project file stores the name, not an ordinal that
    reordering this enum would change.
    """

    FILL = "fill"
    KEEP = "keep"

    @classmethod
    def parse(cls, value: object) -> SlotFill:
        """``value`` as a fill, falling back to ``FILL`` for anything else.

        Tolerant like the rest of the project reader: a hand-edited typo pads
        the way an unstated rule does rather than failing the entry.
        """
        try:
            return cls(value)
        except ValueError:
            return cls.FILL

    def filler(self, byte: int) -> bytes:
        """The byte to pad with, or empty for "leave the tail alone"."""
        return b"" if self is SlotFill.KEEP else bytes([byte & 0xFF])


@dataclass(frozen=True)
class FileRef:
    """Where bytes live: one or more files joined end to end, or memory.

    The **host's** descriptor, not what a container sees: resolving it into a
    :class:`ReadSource` happens once, in :func:`acquire`, so every container is
    handed the same buffer and none has to know how many files were behind it.

    ``offset``/``length`` bound the meaningful bytes inside that buffer — a
    block's compressed slot inside its parent's region. ``length`` is ``None``
    when the extent is not known, which is **not** the same as zero: the slot
    then runs to the end of what holds it, and that is what bounds a write.

    ``data``, when set, *is* the bytes, so a block reads its parent's unsaved
    buffer rather than the stale file.
    """

    paths: tuple[str, ...] = ()
    data: bytes | None = None
    """When set, read this instead of the files (unsaved parent buffers)."""
    offset: int = 0
    length: int | None = None

    @property
    def path(self) -> str:
        """The first file: this ref's identity for messages and display."""
        return self.paths[0] if self.paths else ""

    @property
    def whole_file(self) -> bool:
        """Whether this names all of what holds it, rather than a slot in it."""
        return self.offset == 0 and self.length is None

    def joined(self) -> bytes:
        """Every file's bytes end to end, or the in-memory buffer."""
        if self.data is not None:
            return self.data
        chunks = []
        for path in self.paths:
            with open(path, "rb") as f:
                chunks.append(f.read())
        return b"".join(chunks)

    def window(self, joined: bytes) -> bytes:
        """``joined`` cut to this ref's ``offset``/``length``."""
        end = len(joined) if self.length is None else self.offset + self.length
        return joined[self.offset : end]

    def read(self) -> bytes:
        return self.window(self.joined())

    def sizes(self) -> list[int]:
        """Each file's size on disk; a file that is not there yet counts as 0."""
        return [os.path.getsize(p) if os.path.exists(p) else 0 for p in self.paths]

    def total(self) -> int:
        """How many bytes hold this ref: the buffer's, or the files' on disk."""
        if self.data is not None:
            return len(self.data)
        return sum(self.sizes())

    def room(self) -> int:
        """How many bytes fit from ``offset``: the slot, or all that is left."""
        if self.length is not None:
            return self.length
        return max(0, self.total() - self.offset)


@dataclass(frozen=True)
class PathwayConfig:
    """The plugin ids plus the source/destination of one run.

    One id per stage, covering both directions: the container that unwraps the
    bytes is the one that re-wraps them, so a load and a save cannot disagree
    about which plugin they went through.

    ``slot_fill``/``fill_byte`` are read only where a result can come up short,
    which is a bounded *compressed* slot. ``pathway`` labels the run for
    failures — the block's name, when one write lays out several.
    """

    source: FileRef
    container_id: str = RAW_CONTAINER
    compression_id: str | None = None
    slot_fill: SlotFill = SlotFill.FILL
    fill_byte: int = 0xFF
    pathway: str = ""


@dataclass
class Loaded:
    data: bytes
    """The decompressed payload every later stage works on."""
    ctx: PipelineContext
    writable: bool
    missing_plugins: list[str] = field(default_factory=list)
    raw: bytes = b""
    """The bytes as read from the files, before the container."""


def _id(plugin: Any) -> str:
    info = getattr(plugin, "info", None)
    return getattr(info, "id", "") or ""


def _run(
    stage: Stage, plugin: str, action: str, fn: Callable[[], T], pathway: str = ""
) -> T:
    """Run one stage, turning any failure into a hard-stop :class:`PipelineError`.

    Every failure is funnelled, including the ones that are not the plugin's —
    a missing source file is an ``OSError`` from the host's own read, and
    letting it out bare would leave the UI reporting a bare filename where the
    rest of the pipeline names the stage and the reason.

    A :class:`PipelineError` is re-raised as it is, since it already says all of
    this; every other :class:`~mapchar.core.errors.MapcharError` is wrapped like
    anything else, because an error raised *inside* a stage is still that
    stage's failure and reporting it unlabelled loses where it happened.
    """
    try:
        return fn()
    except PipelineError:
        raise
    except Exception as exc:
        raise PipelineError(stage, action, plugin, str(exc), pathway) from exc


def _probe(
    plugin: Any,
    name: str,
    call: Callable[[Any], T],
    default: T,
    *,
    ctx: PipelineContext,
    source: str = "",
) -> T:
    """Ask one **optional** plugin method, so a bad answer cannot cost the load.

    The counterpart to :func:`_run`, and the two are the whole of how the host
    calls into a plugin. ``_run`` is for the calls that *are* the result, where
    a failure has no fallback. This is for the optional half of a stage
    protocol, where **absence is already defined**: a container that says
    nothing about ``header_size`` adds no header, one with no ``describe`` has
    nothing to report, a scheme with no ``bind_tree`` keeps its tables in its
    own stream.

    So a method that *cannot answer* is treated as one that was never written.
    Failing the load instead would lose the file over a piece of metadata the
    host already has a documented answer for, and would make the policy depend
    on which method a plugin happened to get wrong. What stops that being silent
    is the notice: the fallback is recorded on the context and surfaced against
    the entry, which is how the plugin's author finds out.

    ``call`` runs **inside** the guard for the same reason the method does: one
    that hands back a string where a number was asked for is exactly as broken
    as one that raises, and converting outside would put the crash back where
    the guard was meant to be.
    """
    ask = getattr(plugin, name, None)
    if not callable(ask):
        return default
    try:
        return call(ask)
    except Exception as exc:  # noqa: BLE001 - a probe must not fail the load
        ctx.note(
            f"{source or 'the plugin'} could not answer {name}(), so its default"
            " was used",
            detail=f"{exc}\nRead as if the plugin had not defined {name}(),\n"
            "which is what one staying quiet means.",
            source=source,
        )
        return default


def acquire(ref: FileRef) -> tuple[ReadSource, tuple[SourceSpan, ...], bytes]:
    """Resolve a :class:`FileRef` into what a container is handed.

    The host's half of the container contract: the files are opened here, once,
    and joined end to end in the order the ref names them, so every container
    sees one buffer and none of them has to know there was more than one. A
    container that opened the path itself would serve a block the file's *saved*
    bytes while its parent holds unsaved edits.

    Three things come back: the windowed source the container reads, the spans
    the caller publishes as :data:`KEY_SOURCE_FILES`, and the joined buffer
    whole — which is what a scheme whose tables live elsewhere in the ROM is
    bound to.
    """
    joined = ref.joined()
    if ref.data is not None:
        spans = (SourceSpan(ref.path, 0, len(joined)),)
    else:
        spans, at = [], 0
        for path in ref.paths:
            size = os.path.getsize(path)
            spans.append(SourceSpan(path, at, size))
            at += size
        spans = tuple(spans)
    return ReadSource(ref.window(joined), ref.paths), spans, joined


def load(
    config: PathwayConfig, registry: Registry, ctx: PipelineContext | None = None
) -> Loaded:
    """Run the stages forward: the decompressed payload and what it took.

    ``ctx`` lets a nested run inherit its parent's hints — a block's own decode
    publishes its own ``KEY_CONSUMED``, but the header size and suggested
    mapping the parent's container found still apply to it, and the pointer
    mappings need them (:meth:`PipelineContext.inherit`).
    """
    ctx = PipelineContext() if ctx is None else ctx
    stages = _stages(config, registry)
    missing = [p.missing_id for _, p in stages if isinstance(p, PassThrough)]
    # Said once per load, here rather than where the config was built, because a
    # notice needs the context a load creates. Without it the user meets a file
    # that opens looking untransformed with Write greyed out and nothing saying
    # why.
    for stage, plugin in stages:
        if isinstance(plugin, PassThrough):
            ctx.note(
                f"Missing plugin: {plugin.missing_id}",
                detail=f"This entry reads through a {stage.folder} plugin this\n"
                "build does not have, so its bytes are shown untransformed\n"
                "and cannot be written back. Install the plugin, or choose a\n"
                f"different {stage.folder} to make the entry editable again.",
                source=stage.folder,
            )
    writable = all(writes_back(p, s) for s, p in stages)

    container = stages[0][1]
    raw = b""

    def read() -> bytes:
        nonlocal raw
        source, spans, joined = acquire(config.source)
        # Provenance the host owns, because it is the host that knows where the
        # bytes came from; a container publishes only KEY_SOURCE_OFFSET, which is
        # a fact about the format. Set before the read, so a container that wants
        # to know how its buffer was assembled can look.
        ctx.set(KEY_SOURCE_FILES, spans)
        raw = joined
        payload = container.read(source, ctx)
        _container_hints(container, source, ctx)
        return payload

    data = _run(Stage.CONTAINER, _id(container), "read", read, config.pathway)
    for stage, plugin in stages[1:]:
        bind_tables(plugin, raw, ctx)
        data = _run(
            stage,
            _id(plugin),
            "decompress",
            lambda p=plugin, d=data: p.decompress(d, ctx),
            config.pathway,
        )
    return Loaded(data, ctx, writable, missing, raw)


def _container_hints(container: Any, source: ReadSource, ctx: PipelineContext) -> None:
    """Fill in the hints a container answers for rather than publishes.

    The two routes say the same thing: a container that has to read the file to
    know its header size publishes ``KEY_HEADER_SIZE`` while reading, and one
    that can answer from the file's shape alone implements ``header_size``.
    Whatever the read already said wins — it ran with the bytes in hand — and an
    inherited value wins too, since a block's header size is its parent's.
    """
    if ctx.get(KEY_SUGGESTED_MAPPING) is None:
        mapping = _probe(
            container,
            "default_mapping",
            lambda ask: ask(source),
            None,
            ctx=ctx,
            source=_id(container),
        )
        if mapping:
            ctx.set(KEY_SUGGESTED_MAPPING, str(mapping))
    if ctx.get(KEY_HEADER_SIZE) is None:
        header = _probe(
            container,
            "header_size",
            lambda ask: int(ask(source)),
            None,
            ctx=ctx,
            source=_id(container),
        )
        if header is not None:
            ctx.set(KEY_HEADER_SIZE, header)


def bind_tables(plugin: Any, rom: bytes, ctx: PipelineContext) -> None:
    """Hand a scheme the whole buffer, for the ones whose tables live in it.

    A Huffman tree at a fixed ROM address is not in the stream being decoded, so
    the scheme is shown everything before it reads anything. Optional and
    probed: a scheme that cannot bind loses its decode with a notice, not the
    load — the decompress that follows will say so in its own terms.
    """
    _probe(plugin, "bind_tree", lambda ask: ask(rom), None, ctx=ctx, source=_id(plugin))


def _stages(config: PathwayConfig, registry: Registry) -> list[tuple[Stage, Any]]:
    stages = [
        (Stage.CONTAINER, registry.resolve_stage(Stage.CONTAINER, config.container_id))
    ]
    if config.compression_id:
        stages.append(
            (
                Stage.COMPRESSION,
                registry.resolve_stage(Stage.COMPRESSION, config.compression_id),
            )
        )
    return stages


def compress_for_slot(
    data: bytes, config: PathwayConfig, registry: Registry, ctx: PipelineContext
) -> bytes:
    """``data`` compressed and checked against its slot: the bytes that belong in it.

    The write minus the store, so the checks that make a slot safe are stated
    once and hold however the bytes are then delivered — through a container to
    a file, or spliced into a parent's buffer by a block that lives inside one.

    A **bounded** slot (``length`` set) is hard: a result that would overflow it
    raises before anything is touched. An **unbounded** one is still bounded —
    by the end of whatever holds it — because a slot whose extent nobody
    recorded is not a licence to write past the file, and extending it would
    move every byte after it.

    A result *smaller* than a bounded slot leaves room at the end, and what goes
    there is the config's :class:`SlotFill`. Only a bounded slot is padded: an
    unbounded one has no end to pad to, and only a **compressed** result can
    come up short at all — everywhere else the result is the length of the
    buffer it was read from, so a short one means something went wrong and
    inventing bytes to cover it would bury that.
    """
    return _compress_for_slot(data, config, _stages(config, registry), ctx)


def _compress_for_slot(
    data: bytes,
    config: PathwayConfig,
    stages: list[tuple[Stage, Any]],
    ctx: PipelineContext,
) -> bytes:
    packed = data
    for stage, plugin in reversed(stages[1:]):
        if not writes_back(plugin, stage):
            raise PipelineError(
                stage,
                "write",
                _id(plugin) or config.compression_id or "",
                "plugin cannot write back",
                config.pathway,
            )
        packed = _run(
            stage,
            _id(plugin),
            "compress",
            lambda p=plugin, d=packed: p.compress(d, ctx),
            config.pathway,
        )
    ref = config.source
    if ref.whole_file:
        return packed
    room = ref.room()
    if len(packed) > room:
        bound = (
            f"its {room}-byte slot"
            if ref.length is not None
            else f"the {room} bytes left in {ref.path or 'the file'}"
        )
        raise PipelineError(
            Stage.COMPRESSION if config.compression_id else Stage.CONTAINER,
            "write",
            config.compression_id or config.container_id,
            f"the result is {len(packed)} bytes, {len(packed) - room} more than "
            f"{bound} at ${ref.offset:X}",
            config.pathway,
        )
    if config.compression_id and ref.length is not None and len(packed) < ref.length:
        packed += config.slot_fill.filler(config.fill_byte) * (ref.length - len(packed))
    return packed


def save(
    data: bytes, config: PathwayConfig, registry: Registry, ctx: PipelineContext
) -> list[str]:
    """Run the stages backward and store the result: the paths that changed.

    The container is asked whether it writes back at all **before** anything is
    compressed: the answer does not depend on the bytes, so packing them first
    would only put work in front of the same refusal.
    """
    stages = _stages(config, registry)
    container = stages[0][1]
    if not writes_back(container, Stage.CONTAINER):
        raise PipelineError(
            Stage.CONTAINER,
            "write",
            _id(container) or config.container_id,
            "container cannot write back",
            config.pathway,
        )
    packed = _compress_for_slot(data, config, stages, ctx)
    return _deposit(
        config,
        lambda target: _run(
            Stage.CONTAINER,
            _id(container),
            "write",
            lambda: container.write(packed, target, ctx),
            config.pathway,
        ),
    )


def _deposit(
    config: PathwayConfig, produce: Callable[[WriteTarget], bytes]
) -> list[str]:
    """Hand the destination's current bytes to ``produce`` and store what comes back.

    The destination is read **here and now**, not remembered from the load: a
    container returns the file *whole* — that is what lets it keep the framing
    and the bytes it never decoded — so it has to be shown what is there to
    keep. Handing it a buffer captured when the entry opened would quietly undo
    every change made to the file since, by this session or anything else.

    With several files the result is cut back apart at the boundaries those
    files have now, and each piece written to its own file. So it has to be the
    length it was handed: the boundaries are the only thing saying which bytes
    belong to which file, and a buffer that changed size has moved every
    boundary after the change by an unknown amount. A single file has no
    boundaries to keep and may grow or shrink.

    Files whose bytes did not change are left alone rather than rewritten
    identically.
    """
    paths = config.source.paths
    blobs = [current_bytes(p) for p in paths]
    existing = b"".join(blobs)
    out = produce(
        WriteTarget(existing, paths, config.source.offset, config.source.length)
    )
    if len(paths) > 1 and len(out) != len(existing):
        raise PipelineError(
            Stage.CONTAINER,
            "write",
            config.container_id,
            f"the result is {len(out)} bytes but the {len(paths)} joined files "
            f"hold {len(existing)}; the boundaries would move, so nothing "
            "was written",
            config.pathway,
        )
    written: list[str] = []
    at = 0
    for path, blob in zip(paths, blobs, strict=True):
        chunk = out[at : at + len(blob)] if len(paths) > 1 else out
        at += len(blob)
        if chunk == blob and os.path.exists(path):
            continue
        with open(path, "wb") as f:
            f.write(chunk)
        written.append(path)
    return written
