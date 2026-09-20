"""The plugin API: what a plugin declares and what each stage must provide."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, NamedTuple, Protocol, runtime_checkable

from mapchar.core.context import (
    KEY_COMPLETE,
    KEY_CONSUMED,
    KEY_DECOMPRESS_PARTIAL,
    PipelineContext,
)
from mapchar.core.errors import Stage

RAW_CONTAINER = "raw"
"""The flat-file container: the answer when nothing claims a file."""


class ContainerField(NamedTuple):
    """One value a container read out of a file, for Container Info.

    ``name`` is what the format calls the field and ``value`` what this file
    holds; ``detail`` is what the container did with it, which is the part a
    hex editor cannot give.
    """

    name: str
    value: str
    detail: str = ""


@dataclass(frozen=True)
class PluginInfo:
    id: str
    name: str
    stage: Stage
    category: str = "Generic"
    """Where this plugin belongs — the console, or ``"Generic"`` for a scheme no
    one platform owns. Carried for the plugin author's sake and for discovery,
    which relabels a user or project plugin with the folder it came from; no
    picker groups by it yet, so nothing is shown anywhere. ``"Generic"`` is the
    default because it is what a built-in with nothing else to say uses."""
    extensions: tuple[str, ...] = ()
    """Lower-case file extensions with the dot, for container detection."""
    magic: tuple[tuple[int, bytes], ...] = ()
    """``(offset, bytes)`` pairs; **any** match detects the format.

    Several pairs are alternatives — the revisions of one format, or the two
    places a signature may sit — not a conjunction: a container needing two
    things to agree at once tests that in ``read``, where it can say why."""
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
    """What a container writes into: the destination as it stands now.

    ``existing`` is the destination's current contents (``b""`` when it does not
    exist yet) and the write returns what should replace them, so a container is
    a byte transform in both directions and the host does the one thing that
    touches disk. That is what lets a container keep everything it did not
    decode — a header, the banks around a slot — without re-deriving how to read
    and rewrite a file.

    ``offset``/``length`` describe the slot inside the destination the edited
    bytes belong to; :attr:`whole_file` is the common case where they are the
    whole of it. ``length`` is ``None`` when the slot's extent is not known,
    which is not the same as zero: the bytes then run to the end of the
    destination, and that is what bounds them.
    """

    existing: bytes
    paths: tuple[str, ...] = ()
    offset: int = 0
    length: int | None = None

    @property
    def whole_file(self) -> bool:
        """Whether the edited bytes are the entire destination, not a slot in it."""
        return self.offset == 0 and self.length is None

    def room(self) -> int:
        """How many bytes fit from :attr:`offset`: the slot, or all that is left."""
        if self.length is not None:
            return self.length
        return max(0, len(self.existing) - self.offset)


@runtime_checkable
class Plugin(Protocol):
    info: PluginInfo


class Container(Protocol):
    info: PluginInfo

    def read(self, source: ReadSource, ctx: PipelineContext) -> bytes: ...

    # The save half, and the three optional hooks: see ContainerExtras.


class ContainerExtras(Protocol):
    """The optional half of the container contract, declared rather than implied.

    Every method here is reached by ``getattr`` and may simply be absent — a
    container written before one existed, or with nothing to say, is not missing
    anything. What a *failure* in one costs is nothing either: the pipeline
    probes them, and one that raises is read as one that was never written, with
    a notice recorded so the author finds out. So the rule for all four is the
    same, and none of them can take a load down.

    - ``write`` is the declaration that this container saves back at all;
      without it every entry through it is view-only.
    - ``describe`` is what Container Info shows: the fields the container read
      out of the file, as ``ContainerField`` rows (or a plain ``name -> value``
      mapping) in the format's own terms.
    - ``default_mapping`` names the pointer mapping this file's layout implies,
      for the block dialog to seed. Publishing ``KEY_SUGGESTED_MAPPING`` from
      ``read`` says the same thing; a container that can answer without reading
      answers here.
    - ``header_size`` is what pointer mappings subtract: the bytes before the
      mapped ROM image. Again the read-time twin is ``KEY_HEADER_SIZE``, and a
      container that publishes neither is taken to add no header.

    The last two are asked **with the file's bytes in hand**, always — there is
    no form that asks what a container does in the abstract, so neither has to
    invent an answer for a file it has not been shown. A container with nothing
    to read them for takes ``source`` and ignores it.
    """

    def write(
        self, data: bytes, target: WriteTarget, ctx: PipelineContext
    ) -> bytes: ...

    def describe(
        self, source: ReadSource, ctx: PipelineContext
    ) -> Iterable[ContainerField] | dict[str, Any]: ...

    def default_mapping(self, source: ReadSource) -> str | None: ...

    def header_size(self, source: ReadSource) -> int: ...


class Compression(Protocol):
    info: PluginInfo

    def decompress(self, data: bytes, ctx: PipelineContext) -> bytes: ...

    # Optional:
    # def compress(self, data: bytes, ctx: PipelineContext) -> bytes
    #     Shipping it *is* the declaration that a save-back works.
    # def bind_tree(self, rom: bytes) -> None
    #     A scheme whose tables live in the ROM rather than in the stream (a
    #     Huffman tree at a fixed address) is handed the whole buffer before
    #     each decode. Probed like a container's hooks, so a scheme that cannot
    #     bind loses its decode, not the load.
    # signature: bytes
    #     What a stream of this scheme starts with — RNC 1's b"RNC\x01". A few
    #     bytes compared before anything is decoded, which is what lets the
    #     Decompressed view arm itself as the view moves and Find All walk a
    #     whole ROM. Read by ``getattr`` like the hooks above: absent, or not
    #     bytes, is a scheme that announces itself in no way a comparison can
    #     find, and it is then searched for by decoding at each offset instead.
    #     A signature is a claim, never proof: the scheme's own decoder still
    #     has to read a complete structure there.


class PartialDecompression:
    """:class:`Compression`'s two methods over a decoder that finds its own end.

    A scheme whose decoder can report **where the structure ended** owes the
    pipeline two facts on every read, and they are the same few lines whatever
    the scheme is: take the partial flag off the context, decode, then publish
    the compressed size and whether the structure completed.

    Publishing both is a contract, not a convenience.
    :data:`~mapchar.core.context.KEY_CONSUMED` is what a save-back measures its
    slot against and what the structure scan steps over;
    :data:`~mapchar.core.context.KEY_COMPLETE` is what stops a scan calling a
    decode that merely ran out of buffer a hit. A scheme with **no end to
    find** — a fixed-width bit packing, PackBits — states that for itself
    instead and does not use this.

    A subclass supplies :meth:`_decode` and :meth:`_encode`. ``_encode`` is
    optional on the same rule as ``compress`` itself: a scheme that can be read
    but not written leaves it unset, and then has to leave ``compress`` off too,
    since shipping the method is the declaration that a save-back works.
    """

    def _decode(self, data: bytes, *, partial: bool) -> tuple[bytes, int, bool]:
        """``(output, consumed, complete)`` for one structure at ``data[0]``."""
        raise NotImplementedError

    def _encode(self, data: bytes) -> bytes:
        raise NotImplementedError

    def decompress(self, data: bytes, ctx: PipelineContext) -> bytes:
        out, consumed, complete = self._decode(
            data, partial=bool(ctx.get(KEY_DECOMPRESS_PARTIAL))
        )
        ctx.set(KEY_CONSUMED, consumed)
        ctx.set(KEY_COMPLETE, complete)
        return out

    def compress(self, data: bytes, ctx: PipelineContext) -> bytes:
        return self._encode(data)


class Charset(Protocol):
    info: PluginInfo

    def entries(self) -> Iterable[tuple[str, str]]:
        """``(bits, text)`` pairs; text is literal, not script form."""
        ...

    # Optional, both reached by ``getattr`` and probed, so one that is absent
    # and one that raises mean the same thing and neither costs a table load:
    # def aliases(self) -> Iterable[tuple[str, str]]
    #     ``(text, bits)`` pairs the encoder accepts for a code whose own text
    #     is something else — the yen sign reaching Shift-JIS ``5C`` while
    #     ``5C`` still decodes as a backslash. Absent is no aliases.
    # codec: str
    #     The Python codec this charset is, which is how wide its NUL is: two
    #     bytes in UTF-16, four in UTF-32. Absent is one byte.


class Mapping(Protocol):
    """Pointer value to payload offset and back.

    Offsets are into the header-less payload, so a mapping never sees the
    container's header: ``bank`` supplies what a short pointer leaves out and
    ``ptr_address`` is where the pointer itself sits, for the relative kinds.
    Both are keyword-or-positional with a default, so a mapping that ignores
    them is still called the one way.
    """

    info: PluginInfo

    def to_offset(
        self, value: int, bank: int = 0, ptr_address: int = 0
    ) -> int | None: ...

    def to_value(self, offset: int, bank: int = 0, ptr_address: int = 0) -> int: ...

    # Optional, both read by ``getattr`` with a default, so a mapping that
    # declares neither still resolves:
    # sizes: tuple[int, ...]
    #     The pointer widths in bytes this mapping accepts; absent offers every
    #     width the block does.
    # needs_bank: bool
    #     Whether a short pointer needs a bank supplied alongside it. Absent is
    #     True, which is the safe way round: the field is offered rather than
    #     hidden from a mapping that needs it.
    # bank_of: Callable[[int], int]
    #     Which bank an offset is reached in, for a mapping that needs one.
    #     Absent, pointer discovery has only the bank it was given, so it takes
    #     a value it finds rather than checking it back against that guess.


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
