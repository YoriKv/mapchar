"""Built-in charsets: standard encodings a table can sit on."""

from __future__ import annotations

from collections.abc import Iterable

from mapchar.core.bits import bytes_to_bits
from mapchar.plugins.base import PluginInfo, Stage


def _printable(ch: str) -> bool:
    return ch == " " or (ch.isprintable() and not ch.isspace())


class CodecCharset:
    """Every code point a Python codec can encode, as ``(bits, text)``."""

    def __init__(
        self, id: str, name: str, codec: str, ranges: tuple[tuple[int, int], ...]
    ):
        self.info = PluginInfo(id, name, Stage.CHARSET, "Standard")
        self.codec = codec
        self.ranges = ranges

    def entries(self) -> Iterable[tuple[str, str]]:
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
                # Codecs fold several characters onto one code (Shift-JIS
                # sends both U+005C and U+00A5 to 5C); keep the one that
                # decodes back, so every code appears once.
                if data and data.decode(self.codec, errors="replace") == ch:
                    yield bytes_to_bits(data), ch


class NoCharset:
    info = PluginInfo("none", "None", Stage.CHARSET, "Standard")

    def entries(self) -> Iterable[tuple[str, str]]:
        return ()


BMP = ((0x20, 0xFFFF),)


def register(registry) -> None:
    registry.register(NoCharset())
    registry.register(CodecCharset("ascii", "ASCII", "ascii", ((0x20, 0x7E),)))
    registry.register(CodecCharset("latin-1", "Latin-1", "latin-1", ((0x20, 0xFF),)))
    registry.register(CodecCharset("shift-jis", "Shift-JIS", "shift_jis", BMP))
    registry.register(CodecCharset("euc-jp", "EUC-JP", "euc_jp", BMP))
    registry.register(CodecCharset("utf-16le", "UTF-16 LE", "utf-16-le", BMP))
    registry.register(CodecCharset("utf-16be", "UTF-16 BE", "utf-16-be", BMP))
    registry.register(CodecCharset("utf-8", "UTF-8 (BMP)", "utf-8", BMP))
