"""Built-in charsets: standard encodings a table can sit on."""

from __future__ import annotations

from collections.abc import Iterable

from mapchar.core.bits import bytes_to_bits
from mapchar.core.text import nfc
from mapchar.plugins.base import PluginInfo, Stage


def _printable(ch: str) -> bool:
    return ch == " " or (ch.isprintable() and not ch.isspace())


class CodecCharset:
    """Every code point a Python codec can encode, as ``(bits, text)``."""

    def __init__(
        self,
        id: str,
        name: str,
        codec: str,
        ranges: tuple[tuple[int, int], ...],
        extra_aliases: tuple[tuple[str, int], ...] = (),
    ):
        self.info = PluginInfo(id, name, Stage.CHARSET, "Standard")
        self.codec = codec
        self.ranges = ranges
        self.extra_aliases = extra_aliases
        """``(text, byte)`` pairs the codec cannot encode but the code means
        anyway: the single-byte yen and overline of JIS X 0201."""

    def _points(self) -> Iterable[tuple[str, bytes]]:
        for lo, hi in self.ranges:
            for cp in range(lo, hi + 1):
                if 0xD800 <= cp <= 0xDFFF:
                    continue
                ch = chr(cp)
                if not _printable(ch):
                    continue
                try:
                    data = ch.encode(self.codec)
                except UnicodeEncodeError:
                    continue
                if data:
                    yield ch, data

    def entries(self) -> Iterable[tuple[str, str]]:
        for ch, data in self._points():
            # Codecs fold several characters onto one code (cp932 sends both
            # U+2212 and U+FF0D to 817C); keep the one that decodes back, so
            # every code appears once. The others become aliases.
            if data.decode(self.codec, errors="replace") == ch:
                yield bytes_to_bits(data), nfc(ch)

    def aliases(self) -> Iterable[tuple[str, str]]:
        """``(text, bits)`` the encoder accepts for a code that decodes as
        something else: the characters this codec folds, plus its own extras."""
        for ch, data in self._points():
            if data.decode(self.codec, errors="replace") != ch:
                yield nfc(ch), bytes_to_bits(data)
        for text, byte in self.extra_aliases:
            yield nfc(text), bytes_to_bits(bytes([byte]))


class NoCharset:
    info = PluginInfo("none", "None", Stage.CHARSET, "Standard")

    def entries(self) -> Iterable[tuple[str, str]]:
        return ()


BMP = ((0x20, 0xFFFF),)
UNICODE = ((0x20, 0x10FFFF),)
"""Every plane, so an emoji or a CJK extension character has a code too; the
astral ones encode as four UTF-8 bytes or a UTF-16 surrogate pair."""

JIS_ROMAN = (("¥", 0x5C), ("‾", 0x7E))
"""JIS X 0201's Roman set puts the yen sign where ASCII has the backslash and
the overline where it has the tilde. ASCII still decodes those two codes, and
typing the yen or the overline encodes to them."""


def register(registry) -> None:
    registry.register(NoCharset())
    registry.register(CodecCharset("ascii", "ASCII", "ascii", ((0x20, 0x7E),)))
    registry.register(CodecCharset("latin-1", "Latin-1", "latin-1", ((0x20, 0xFF),)))
    registry.register(
        CodecCharset("shift-jis", "Shift-JIS (CP932)", "cp932", BMP, JIS_ROMAN)
    )
    registry.register(
        CodecCharset("euc-jp", "EUC-JP (JIS X 0213)", "euc_jis_2004", BMP, JIS_ROMAN)
    )
    registry.register(CodecCharset("utf-16le", "UTF-16 LE", "utf-16-le", UNICODE))
    registry.register(CodecCharset("utf-16be", "UTF-16 BE", "utf-16-be", UNICODE))
    registry.register(CodecCharset("utf-8", "UTF-8", "utf-8", UNICODE))
