"""Plugin ids: containers, compression, mappings and charsets, and the table
dialects.

An id nothing answers to does not stop a project opening. A container or a
compression scheme that is missing becomes a pass-through that leaves the entry
view-only; a mapping that is missing leaves a pointer block reading no strings.
"""

from __future__ import annotations

from mapchar_lint.config import ConfigReading, read_config
from mapchar_lint.context import Context, EntryView
from mapchar_lint.schema import DEFAULT_CONTAINER


def check(ctx: Context) -> None:
    if not ctx.ids.usable:
        return
    for view in ctx.entries:
        if view.kind is None or view.dropped:
            continue
        if view.kind == "file":
            _container(ctx, view)
        if view.kind in ("file", "block"):
            _compression(ctx, view)
        if view.kind == "block" and view.config is not None:
            _mapping(ctx, view, view.config, view.at("config"), session=False)
        session = view.raw.get("session")
        if view.kind != "block" and isinstance(session, dict):
            config = session.get("config")
            if isinstance(config, str) and config:
                reading = read_config(config)
                if not reading.fatal:
                    _mapping(
                        ctx, view, reading, view.at("session", "config"), session=True
                    )
        if view.kind == "table":
            _table(ctx, view)


def _renamed(ctx: Context, view: EntryView, plugin_id: str, pointer: str) -> None:
    if ctx.ids.is_renamed(plugin_id):
        ctx.info(
            "I406",
            f"{plugin_id!r} has been renamed {ctx.ids.current_id(plugin_id)!r}",
            pointer=pointer,
            entry=view,
            detail="It still resolves; the next save writes the new id.",
        )


def _container(ctx: Context, view: EntryView) -> None:
    container = view.raw.get("container_id")
    if container is None:
        return
    container = str(container)
    pointer = view.at("container_id")
    if container == DEFAULT_CONTAINER:
        ctx.info(
            "I407",
            'container_id "raw" is the default',
            pointer=pointer,
            entry=view,
            detail="The writer leaves it out.",
        )
    elif not ctx.ids.has("containers", container):
        ctx.unknown_id(
            "containers",
            container,
            "container",
            code="401",
            pointer=pointer,
            entry=view,
            consequence="The file opens view-only through a pass-through: nothing "
            "it holds can be written back.",
        )
    else:
        _renamed(ctx, view, container, pointer)


def _compression(ctx: Context, view: EntryView) -> None:
    compression = view.raw.get("compression_id")
    if not isinstance(compression, str) or not compression:
        return
    pointer = view.at("compression_id")
    if not ctx.ids.has("compression", compression):
        ctx.unknown_id(
            "compression",
            compression,
            "compression",
            code="402",
            pointer=pointer,
            entry=view,
            consequence="Its bytes are read as they lie, undecompressed, and nothing "
            "can be written back through it.",
        )
    else:
        _renamed(ctx, view, compression, pointer)


def _mapping(
    ctx: Context,
    view: EntryView,
    reading: ConfigReading,
    pointer: str,
    *,
    session: bool,
) -> None:
    mapping = reading.mapping
    if mapping is None:
        return
    if not ctx.ids.has("mappings", mapping):
        ctx.unknown_id(
            "mappings",
            mapping,
            "mapping",
            code="403",
            pointer=pointer,
            entry=view,
            consequence=(
                "The file's reading cannot follow its pointers."
                if session
                else "Its pointers map nowhere, so the block reads no strings."
            ),
        )
        return
    _renamed(ctx, view, mapping, pointer)
    sizes = ctx.ids.sizes_for(mapping)
    if sizes and reading.size is not None and reading.size not in sizes:
        ctx.warn(
            "W408",
            f"size={reading.size} is not a size the {mapping} mapping offers "
            f"({', '.join(map(str, sizes))})",
            pointer=pointer,
            entry=view,
            detail="It reads, but the Reading bar has no setting that shows it.",
        )


def _table(ctx: Context, view: EntryView) -> None:
    dialect = view.raw.get("dialect")
    if dialect is not None and ctx.ids.dialects and dialect not in ctx.ids.dialects:
        ctx.error(
            "E404",
            f"dialect {dialect!r} is not a table dialect "
            f"({', '.join(ctx.ids.dialects)})",
            pointer=view.at("dialect"),
            entry=view,
            detail="The table file does not load, and every block reading through it "
            "reads nothing.",
        )
