"""The pieces every container here is built out of.

A container is a byte transform in both directions, so what it shares with the
next one is small and exact: laying edited bytes back into a file, the three
hints a header-stripping read publishes, and the flat case where there is no
header at all.
"""

from __future__ import annotations

from mapchar.core.context import (
    KEY_HEADER_SIZE,
    KEY_SOURCE_OFFSET,
    KEY_SUGGESTED_MAPPING,
    PipelineContext,
)
from mapchar.plugins.base import (
    RAW_CONTAINER,
    ContainerField,
    PluginInfo,
    ReadSource,
    Stage,
    WriteTarget,
)


def splice(existing: bytes, at: int, data: bytes) -> bytes:
    """``existing`` with ``data`` laid over it at ``at``, keeping every other byte.

    Zero-extends when the result reaches past the end, so a write into a file
    shorter than the payload — or one that is not there yet — lands rather than
    failing on the gap.
    """
    out = bytearray(existing)
    end = at + len(data)
    if len(out) < end:
        out.extend(b"\x00" * (end - len(out)))
    out[at:end] = data
    return bytes(out)


def slot_write(data: bytes, target: WriteTarget) -> bytes:
    """``data`` back into the window it was read from, keeping every other byte.

    What every write half does when the target is a **slot** rather than the
    whole file — a block's compressed region inside its parent. The read was
    handed that window alone and unwrapped no framing inside it, so neither does
    the write: it puts the bytes back where they came from and leaves the file
    around them, framing included, exactly as it stands.
    """
    return data if target.whole_file else splice(target.existing, target.offset, data)


def format_size(count: int) -> str:
    """``count`` bytes as one short phrase: whole binary multiples get a unit."""
    for unit, size in (("MiB", 1 << 20), ("KiB", 1 << 10)):
        if count >= size and count % size == 0:
            return f"{count // size} {unit}"
    return f"{count} bytes"


def publish_hints(
    ctx: PipelineContext, source_offset: int, header: int, mapping: str | None
) -> None:
    """The three facts a header-stripping ``read`` publishes about its file.

    Where the payload starts, how many bytes a pointer mapping subtracts to
    reach it, and the mapping the format implies. The mapping is published only
    when there is one: absence is what says "nothing to suggest", and setting it
    to ``None`` would overwrite a suggestion a parent's container already made.
    """
    ctx.set(KEY_SOURCE_OFFSET, source_offset)
    ctx.set(KEY_HEADER_SIZE, header)
    if mapping is not None:
        ctx.set(KEY_SUGGESTED_MAPPING, mapping)


class _Flat:
    """A container whose payload is the whole file: no header, no offset.

    ``default_mapping`` names the pointer mapping the format implies, if any.
    """

    info: PluginInfo

    def header_size(self, source: ReadSource | None = None) -> int:
        return 0

    def default_mapping(self, source: ReadSource | None = None) -> str | None:
        return None

    def read(self, source: ReadSource, ctx: PipelineContext) -> bytes:
        # Nothing published about a header, rather than a zero: absence already
        # means "adds none", and a zero would overwrite the header size a block
        # inherits from its parent file — which its pointers are quoted against,
        # and which it runs this container over a slot of.
        suggested = self.default_mapping(source)
        if suggested is not None:
            ctx.set(KEY_SUGGESTED_MAPPING, suggested)
        return source.data

    def write(self, data: bytes, target: WriteTarget, ctx: PipelineContext) -> bytes:
        return slot_write(data, target)

    def describe(
        self, source: ReadSource, ctx: PipelineContext
    ) -> tuple[ContainerField, ...]:
        return (
            ContainerField(
                "Payload",
                f"the whole file, {format_size(len(source.data))}",
                "Nothing is stripped: this format's bytes are read where "
                "they lie, so a file offset is a payload offset.",
            ),
        )


class Raw(_Flat):
    info = PluginInfo(RAW_CONTAINER, "Flat file", Stage.CONTAINER, "Generic")


def _sum_value(stored: int, computed: int, spec: str) -> str:
    """``0xNN`` when the file's copy is right, both values when it is not."""
    if stored == computed:
        return f"0x{stored:{spec}} (correct)"
    return f"0x{stored:{spec}} in file, 0x{computed:{spec}} correct"
