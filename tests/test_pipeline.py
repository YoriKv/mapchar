from __future__ import annotations

from mapchar.core.context import KEY_HEADER_SIZE
from mapchar.pipeline.pipeline import (
    FileRef,
    PathwayConfig,
    deposit,
    encode_for_save,
    load,
)
from mapchar.plugins.builtins.containers import NES_MAGIC


def test_load_and_save_roundtrip(tmp_path, registry):
    header = NES_MAGIC + bytes([1, 0, 0, 0]) + b"\x00" * 8
    body = bytes(range(64))
    rom = tmp_path / "g.nes"
    rom.write_bytes(header + body)
    cfg = PathwayConfig(FileRef((str(rom),)), "ines")
    loaded = load(cfg, registry)
    assert loaded.writable and not loaded.missing_plugins
    assert loaded.ctx.get(KEY_HEADER_SIZE) == 16
    assert loaded.data == body
    new = bytearray(loaded.data)
    new[1] = 0xEE
    out = encode_for_save(bytes(new), cfg, registry, loaded.raw, loaded.ctx)
    assert out[:16] == header and out[17] == 0xEE and out[16] == 0
    assert deposit(out, cfg) == [str(rom)]
    assert deposit(out, cfg) == []
    assert rom.read_bytes() == out


def test_missing_plugin_is_view_only(tmp_path, registry):
    rom = tmp_path / "x.bin"
    rom.write_bytes(b"abc")
    loaded = load(PathwayConfig(FileRef((str(rom),)), "raw", "gone"), registry)
    assert loaded.data == b"abc" and not loaded.writable
    assert loaded.missing_plugins == ["gone"]


def test_joined_files(tmp_path, registry):
    a, b = tmp_path / "a.bin", tmp_path / "b.bin"
    a.write_bytes(b"AA")
    b.write_bytes(b"BB")
    cfg = PathwayConfig(FileRef((str(a), str(b))), "raw")
    loaded = load(cfg, registry)
    assert loaded.data == b"AABB"
    assert deposit(b"AAXX", cfg) == [str(b)]
    assert b.read_bytes() == b"XX" and a.read_bytes() == b"AA"
