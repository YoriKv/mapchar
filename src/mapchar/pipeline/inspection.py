"""What one container made of one file, reported rather than loaded.

The model behind Container Info. :func:`inspect_container` runs a config's
container stage **alone**, on a context of its own: no decompression, no
extraction, and nothing here reaches the entry's document. That isolation is the
point — the context a loaded entry carries has every later stage's
contributions mixed into it, and this has to be able to say that the
*container* published a value. It is a fresh read rather than a look at what is
loaded, so a file that has never opened successfully can still be inspected.

Failures are reported, not raised. A missing file, an unregistered container and
a read that threw all land in :attr:`ContainerReport.error`, alongside whatever
the plugin managed to publish first — which is usually what explains the
failure. That is the opposite of the load's hard stop, and deliberately: this is
reached precisely when an entry did not come out as expected, and something that
explains is more use than something that refuses.

Qt-free: the report is plain data, and how it reaches the user is the UI's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mapchar.core.context import (
    KEY_SOURCE_FILES,
    KEY_SOURCE_OFFSET,
    PipelineContext,
)
from mapchar.core.errors import Stage
from mapchar.core.notices import Notice
from mapchar.pipeline.pipeline import PathwayConfig, _acquire
from mapchar.plugins.base import ContainerField, ReadSource
from mapchar.plugins.registry import PassThrough, Registry


@dataclass(frozen=True)
class ContainerReport:
    """What one container made of one file.

    Three groups, in the order a reader needs them. ``fields`` is what the
    container itself says it read (its ``describe`` hook), ``hints`` is what its
    read published for the stages after it, and ``notices`` is anything it had to
    drop, assume or substitute on the way. All three are this read's own, from a
    context nothing else has touched, so every row is attributable to this
    container.

    ``error`` is set when the read raised. The rest still stands: a plugin may
    publish and then fail, and what it managed to say first is usually why.
    """

    container_id: str
    container_name: str
    paths: tuple[str, ...]
    source_size: int
    payload_offset: int
    payload_size: int
    fields: tuple[ContainerField, ...] = ()
    hints: tuple[ContainerField, ...] = ()
    notices: tuple[Notice, ...] = field(default_factory=tuple)
    error: str = ""


# What the host itself put on the context, which is not the container's doing.
_HOST_KEYS = frozenset({KEY_SOURCE_FILES})


def inspect_container(config: PathwayConfig, registry: Registry) -> ContainerReport:
    """Run ``config``'s container read alone and report what it did with the file."""
    plugin = registry.resolve_stage(Stage.CONTAINER, config.container_id)
    paths = tuple(config.source.paths)
    if plugin is None or isinstance(plugin, PassThrough):
        return ContainerReport(
            config.container_id,
            config.container_id,
            paths,
            0,
            0,
            0,
            error=f"Container {config.container_id!r} is not available.",
        )
    name = getattr(plugin.info, "name", config.container_id)
    try:
        source, spans, _joined = _acquire(config.source)
    except OSError as exc:
        return ContainerReport(
            config.container_id, name, paths, 0, 0, 0, error=str(exc)
        )
    ctx = PipelineContext()
    # Set as the real load sets it, before the read: a container may consult it
    # while assembling its payload. Filtered back out of the hints below, since
    # this report is about what the container contributed.
    ctx.set(KEY_SOURCE_FILES, spans)
    error = ""
    payload = b""
    try:
        payload = plugin.read(source, ctx)
    except Exception as exc:  # noqa: BLE001 - a plugin may raise anything at all
        error = f"{type(exc).__name__}: {exc}"
    return ContainerReport(
        container_id=config.container_id,
        container_name=name,
        paths=paths,
        source_size=len(source.data),
        payload_offset=int(ctx.get(KEY_SOURCE_OFFSET, 0) or 0),
        payload_size=len(payload),
        fields=_described(plugin, source, ctx),
        hints=_hints(ctx),
        notices=tuple(ctx.notices),
        error=error,
    )


def _described(
    plugin: Any, source: ReadSource, ctx: PipelineContext
) -> tuple[ContainerField, ...]:
    """``plugin.describe(...)`` as rows, or none — the hook is optional.

    Reached by ``getattr`` for the reason every optional plugin method is: a
    container written before it existed, or with nothing to report, is not
    missing anything. One that raises loses its rows rather than the report — the
    read has already succeeded by here, and a display-only hook must not be able
    to retract that.

    Either shape is read: a mapping of name to value, which is all a container
    with nothing to explain needs, or a sequence of ``(name, value, detail)``
    rows, where the detail is what the container *did* with the value — the part
    a hex editor cannot give, and the row's tooltip. Rows come back as
    :class:`~mapchar.plugins.base.ContainerField`, the plugin API's own row type,
    so a container's own rows pass through rather than being rebuilt into a
    second identical one.
    """
    describe = getattr(plugin, "describe", None)
    if not callable(describe):
        return ()
    try:
        described = describe(source, ctx)
        rows = described.items() if isinstance(described, dict) else described
        return tuple(
            ContainerField(
                str(row[0]), str(row[1]), str(row[2]) if len(row) > 2 else ""
            )
            for row in rows
        )
    except Exception:  # noqa: BLE001 - see the docstring
        return ()


def _hints(ctx: PipelineContext) -> tuple[ContainerField, ...]:
    """Everything the container published, as rows.

    Enumerated off the context rather than asked for, so a **plugin's own** key
    shows up too — labelled with the bare key, which is still evidence that
    something was published and something downstream may be reading it.
    """
    rows = []
    for key, value in sorted(ctx.values.items()):
        if key in _HOST_KEYS:
            continue
        rows.append(ContainerField(key, _hint_value(key, value), f"Context key: {key}"))
    return tuple(rows)


def _hint_value(key: str, value: Any) -> str:
    """A context value as one short line.

    An offset is quoted in hex, as every address in the app is — the one place a
    key's *meaning* changes how its value reads. A bool is a yes/no answer rather
    than Python, and a long value is cut: this lands in one row of a list, and
    one that renders to kilobytes drags the whole thing out of shape.
    """
    if isinstance(value, bool):
        return "yes" if value else "no"
    if key.endswith("offset") and isinstance(value, int):
        return f"${value:X}"
    if isinstance(value, (bytes, bytearray)):
        return f"{len(value)} bytes"
    text = str(value)
    return f"{text[:57]}..." if len(text) > 60 else text
