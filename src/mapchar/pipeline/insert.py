"""Lay a block's strings out for writing back in place.

Encodes every translation, places the results in *slotted* or *packed*
mode, and refuses the whole block when anything crosses its bound. The
output is byte splices over the decompressed buffer; nothing is written here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mapchar.core.bits import Bits, bits_to_bytes
from mapchar.core.block import (
    BlockConfig,
    EndToken,
    FixedLength,
    FixedSource,
    Pascal,
    StringRecord,
    WriteMode,
)
from mapchar.core.errors import EncodeError
from mapchar.core.table import TableSet
from mapchar.core.tokens import CodeRef, TextRun, escape_text, parse_text
from mapchar.engines.decode import DecodeRules, EndedBy, decode
from mapchar.engines.encode import encode


@dataclass(frozen=True)
class Splice:
    offset: int
    data: bytes

    @property
    def end(self) -> int:
        return self.offset + len(self.data)


@dataclass
class Problem:
    index: int
    message: str
    over: int = 0
    """Bytes over the available room, when that is the problem."""


@dataclass
class Encoded:
    index: int
    data: bytes
    """The string's bytes, Pascal prefix included, before padding."""
    problem: Problem | None = None


@dataclass
class LayoutResult:
    splices: list[Splice] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    encoded: dict[int, Encoded] = field(default_factory=dict)
    used: int = 0
    """Bytes the packed layout occupies, or the sum of slot use when slotted."""
    available: int = 0

    @property
    def ok(self) -> bool:
        return not self.problems


def encode_string(
    rec: StringRecord, config: BlockConfig, tables: TableSet, data: bytes
) -> Encoded:
    """One string's bytes: the original bytes when untouched, else its encoding."""
    if rec.translation is None:
        return Encoded(rec.index, data[rec.start : rec.end])
    st = config.string_type
    fixed = isinstance(st, FixedLength) or isinstance(config.source, FixedSource)
    text = rec.translation
    try:
        if fixed:
            body = _encode_fixed(text, config, tables)
        elif isinstance(st, Pascal):
            payload = encode(text, tables, end_terminated=False)
            body = _pascal(payload.data, st, payload, text, tables)
        else:
            result = encode(text, tables, end_terminated=True)
            if not result.ends_with_end and isinstance(st, EndToken):
                raise EncodeError("the translation must end with an end token")
            if len(result.bits) % 8:
                raise EncodeError("the encoding is not a whole number of bytes")
            body = result.data
    except EncodeError as exc:
        return Encoded(rec.index, b"", Problem(rec.index, str(exc)))
    return Encoded(rec.index, body)


def _strip_artificial(text: str, config: BlockConfig) -> list[str]:
    """Split fixed-line text on the artificial line code; drop the end code.

    Only a final ``[end]`` is artificial; one earlier in the text is the
    table's own end token and stays.
    """
    items = parse_text(text)
    if (
        config.show_end
        and items
        and isinstance(items[-1], CodeRef)
        and items[-1].label == config.end_label
        and not items[-1].words
    ):
        items = items[:-1]
    pieces: list[list[str]] = [[]]
    for item in items:
        if isinstance(item, TextRun):
            pieces[-1].append(escape_text(item.text))
            continue
        if config.line_length and item.label == config.line_label and not item.words:
            pieces.append([])
            continue
        words = " ".join(item.words)
        pieces[-1].append(f"[{item.label}{' ' + words if words else ''}]")
    return ["".join(p) for p in pieces]


def _encode_fixed(text: str, config: BlockConfig, tables: TableSet) -> bytes:
    st = config.string_type
    length = st.length if isinstance(st, FixedLength) else config.source.length  # type: ignore[union-attr]
    stop_at_end = isinstance(st, FixedLength) and st.stop_at_end
    pieces = _strip_artificial(text, config)
    if config.line_length:
        out = b""
        for i, piece in enumerate(pieces):
            r = encode(piece, tables, end_terminated=stop_at_end)
            if len(r.bits) % 8:
                raise EncodeError("a line is not a whole number of bytes")
            room = min(config.line_length, length - i * config.line_length)
            if len(r.data) > room:
                raise EncodeError(
                    f"line {i + 1} is {len(r.data) - room} byte(s) too long"
                )
            out += r.data + bytes([config.fill]) * (room - len(r.data))
        if len(out) > length:
            raise EncodeError(f"{len(pieces)} lines do not fit in {length} bytes")
        return out
    r = encode(pieces[0], tables, end_terminated=stop_at_end)
    if len(r.bits) % 8:
        raise EncodeError("the encoding is not a whole number of bytes")
    return r.data


def _pascal(payload: bytes, st: Pascal, result, text: str, tables: TableSet) -> bytes:
    if st.counts_tokens:
        r = decode(Bits(payload), tables, 0, DecodeRules(end_terminated=False))
        n = sum(t.weight if t.entry is not None else 1 for t in r.tokens)
    else:
        n = len(payload)
    if n >= 1 << (st.width * 8):
        raise EncodeError(f"length {n} does not fit a {st.width}-byte prefix")
    prefix = n.to_bytes(st.width, "big" if st.endian == "big" else "little")
    return prefix + payload


def layout_block(
    data: bytes,
    config: BlockConfig,
    tables: TableSet,
    strings: list[StringRecord],
    registry=None,
) -> LayoutResult:
    result = LayoutResult()
    if not strings:
        return result
    fill = bytes([config.fill])
    for rec in strings:
        enc = encode_string(rec, config, tables, data)
        result.encoded[rec.index] = enc
        if enc.problem is not None:
            result.problems.append(enc.problem)
    mode = config.effective_write_mode
    st = config.string_type
    fixed_len = None
    if isinstance(st, FixedLength):
        fixed_len = st.length
    elif isinstance(config.source, FixedSource):
        fixed_len = config.source.length

    if mode is WriteMode.SLOTTED:
        out = bytearray()
        first = strings[0].start
        last = strings[-1].end
        out[:] = data[first:last]
        used = 0
        for rec in strings:
            enc = result.encoded[rec.index]
            if enc.problem is not None:
                continue
            room = fixed_len if fixed_len is not None else rec.end - rec.start
            room = min(room, rec.end - rec.start) if fixed_len is None else room
            if len(enc.data) > room:
                result.problems.append(
                    Problem(
                        rec.index,
                        f"{len(enc.data) - room} byte(s) too long for its slot",
                        len(enc.data) - room,
                    )
                )
                continue
            used += len(enc.data)
            chunk = enc.data + fill * (room - len(enc.data))
            at = rec.start - first
            out[at : at + room] = chunk
        result.used = used
        result.available = sum(
            (fixed_len if fixed_len is not None else s.end - s.start) for s in strings
        )
        if result.ok:
            result.splices.append(Splice(first, bytes(out)))
        return result

    # Packed: end to end from the first string, up to the bound.
    first = strings[0].start
    bound = config.bound
    if bound is None:
        bound = getattr(config.source, "stop", None) or strings[-1].end
    pos = first
    out = bytearray()
    m, o = config.realign
    for rec in strings:
        enc = result.encoded[rec.index]
        if enc.problem is not None:
            continue
        if m > 0:
            target = pos - o
            if target > 0 and target % m:
                aligned = (-(-target // m)) * m + o
                out += fill * (aligned - pos)
                pos = aligned
        if fixed_len is not None:
            chunk = enc.data + fill * (fixed_len - len(enc.data))
        else:
            chunk = enc.data
        if pos + len(chunk) > bound:
            over = pos + len(chunk) - bound
            result.problems.append(
                Problem(rec.index, f"crosses the block bound by {over} byte(s)", over)
            )
            pos += len(chunk)
            continue
        rec_new_start = pos
        enc.new_start = rec_new_start  # type: ignore[attr-defined]
        out += chunk
        pos += len(chunk)
    result.used = pos - first
    result.available = bound - first
    if result.ok:
        out += fill * (bound - first - len(out))
        result.splices.append(Splice(first, bytes(out)))
        result.splices.extend(_pointer_splices(config, strings, result, registry))
    return result


def _pointer_splices(config, strings, result: LayoutResult, registry) -> list[Splice]:
    """Rewrite every pointer of a packed block to its string's new position."""
    from mapchar.core.mapping import pointer_bytes, resolve_mapping

    if not config.has_pointers:
        return []
    if registry is None:
        from mapchar.plugins.registry import default_registry

        registry = default_registry()
    source = config.source
    mapping = resolve_mapping(registry, source.mapping_id)
    if mapping is None:
        result.problems.append(Problem(-1, f"unknown mapping {source.mapping_id!r}"))
        return []
    splices = []
    for rec in strings:
        enc = result.encoded.get(rec.index)
        new_start = getattr(enc, "new_start", None)
        if new_start is None:
            continue
        for ref in rec.pointers:
            value = mapping.to_value(new_start - ref.offset, source.bank, ref.address)
            if value < 0 or value >= 1 << (ref.size * 8):
                result.problems.append(
                    Problem(
                        rec.index, f"pointer at ${ref.address:X} cannot hold ${value:X}"
                    )
                )
                continue
            splices.append(
                Splice(ref.address, pointer_bytes(value, ref.size, ref.endian))
            )
    return splices


def apply_splices(data: bytes, splices: list[Splice]) -> bytes:
    out = bytearray(data)
    for s in splices:
        out[s.offset : s.end] = s.data
    return bytes(out)


def check_roundtrip(
    data: bytes, config: BlockConfig, tables: TableSet, strings
) -> list[str]:
    """After a layout, the strings that do not decode back as expected."""
    from mapchar.pipeline.extract import extract

    ex = extract(data, config, tables)
    problems = []
    by_index = {s.index: s for s in ex.strings}
    for rec in strings:
        want = rec.translation if rec.translation is not None else rec.original_text()
        got = by_index.get(rec.index)
        if got is None:
            problems.append(f"string {rec.index} vanished")
            continue
        if got.original_text().replace("\n", "") != want.replace("\n", ""):
            if got.ended_by_data if hasattr(got, "ended_by_data") else False:
                continue
            problems.append(f"string {rec.index} reads back differently")
    return problems


__all__ = [
    "EndedBy",
    "LayoutResult",
    "Problem",
    "Splice",
    "apply_splices",
    "layout_block",
    "bits_to_bytes",
]
