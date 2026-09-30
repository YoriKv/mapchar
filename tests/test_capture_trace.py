"""Tracing on variants of the fake game: text drawn into VRAM, text with
dictionary words, text read from a RAM buffer, a byte read twice, a write
through a RAM mirror — and the tracer's helpers on synthetic evidence."""

from __future__ import annotations

import random

import pytest

from capture_fake import (
    BUFFER,
    CONSOLE,
    END,
    LETTERS,
    MESSAGES,
    READER_PC,
    STRINGS,
    TABLE,
    WRITER_PC,
    FakeEmulator,
    Game,
    Memory,
    build_rom,
    encode,
    pointer_stage,
    write_moment,
)
from mapchar.capture import consoles, evidence, occurrence
from mapchar.capture.protocol import drive
from mapchar.capture.trace import (
    PACKED,
    Code,
    Copy,
    Pointer,
    Reads,
    Result,
    Source,
    Tracer,
    advancing,
    advancing_run,
    gb_bank_fits,
    gb_follows_call,
    gb_operand,
    gba_copies,
    ldr_reaches,
    replaced,
)

TEXT = MESSAGES[1]
START = STRINGS + len(MESSAGES[0]) + 1


@pytest.fixture
def folder(tmp_path):
    f = tmp_path / "cap"
    write_moment(str(f))
    return str(f)


def trace(folder, game, text=TEXT):
    emu = FakeEmulator(game)
    assert drive(evidence.replay(emu, "rom.bin", CONSOLE, folder), 30).match
    ev = drive(evidence.Evidence.load(folder, CONSOLE, 0))
    occ, bad = drive(occurrence.find(ev, text))
    assert occ is not None, bad
    return drive(Tracer(emu, "rom.bin", game.rom, CONSOLE, folder, ev, occ).run(), 300)


# -- games


GLYPHS = 0x40


class VramGame(Game):
    """Draws each character into VRAM, eight a frame: its code and a glyph
    byte."""

    def text(self, m: Memory, at: int) -> list[tuple]:
        ev = []
        for k in range(64):
            f = 3 + k // 8
            v = m.read(READER_PC, at + k)
            ev.append(("E", READER_PC, at + k, v, f))
            if v == END:
                break
            ev.append(("V", 0x9100, 2 * k, v, f))
            ev.append(("V", 0x9100, 2 * k + 1, (v * 7 + GLYPHS) & 0xFF, f))
        return ev


class ScanGame(Game):
    """Measures the string first (another routine reads every byte up to its
    end), then copies that many bytes."""

    def text(self, m: Memory, at: int) -> list[tuple]:
        ev, n = [], 0
        while n < 64:
            v = m.read(0x8800, at + n)
            ev.append(("E", 0x8800, at + n, v, 2))
            if v == END:
                break
            n += 1
        for k in range(n):
            v = m.read(READER_PC, at + k)
            ev.append(("E", READER_PC, at + k, v, 3))
            ev.append(("W", WRITER_PC, BUFFER + k, v, 3))
        return ev


DECODE, COPY_PC = 0x7E1000, 0x9020


class BufferGame(Game):
    """Decodes the string into a RAM buffer a frame early — not from ROM
    here, and scrambled so it reads as no text — and copies the buffer,
    unscrambled, into the text's place."""

    def run(self, rom=None, subs=()):
        rom = self.rom if rom is None else rom
        m = Memory(rom, subs)
        codes = encode(MESSAGES[self.message])
        ev = []
        for k, c in enumerate(codes):
            ev.append(("W", 0x9030, DECODE + k, c ^ 0x55, 2))
            m.write((DECODE + k) & 0x1FFFF, c ^ 0x55)
        for k in range(len(codes)):
            v = m.read(COPY_PC, (DECODE + k) & 0x1FFFF, "snesWorkRam") ^ 0x55
            ev.append(("W", WRITER_PC, BUFFER + k, v, 3))
        return ev


class MirrorGame(Game):
    """Clears a cell of the buffer through a WRAM mirror before the text."""

    def run(self, rom=None, subs=()):
        ev = super().run(rom, subs)
        k = next((i for i, e in enumerate(ev) if e[0] == "W"), len(ev))
        ev.insert(k, ("W", 0x8300, 0x002005, 0x00, 3))
        return ev


DICT = 0x600


def dict_rom() -> bytes:
    rom = bytearray([0xEE] * 0x1000)
    at = DICT + 0x20
    for n, w in enumerate(["Welcome", "Tests"]):
        rom[DICT + 2 * n : DICT + 2 * n + 2] = at.to_bytes(2, "little")
        b = encode(w) + bytes([END])
        rom[at : at + len(b)] = b
        at += len(b)
    body = bytes([0xF0]) + encode(" to the Island of ") + bytes([0xF1, END])
    rom[TABLE : TABLE + 2] = STRINGS.to_bytes(2, "little")
    rom[STRINGS : STRINGS + len(body)] = body
    return bytes(rom)


class DictGame(Game):
    """Codes $F0 and up print a dictionary word."""

    def text(self, m: Memory, at: int) -> list[tuple]:
        ev, out = [], 0
        for k in range(64):
            v = m.read(READER_PC, at + k)
            ev.append(("E", READER_PC, at + k, v, 3))
            if v == END:
                break
            if v >= 0xF0:
                d = int.from_bytes(m.rom[DICT + 2 * (v - 0xF0) :][:2], "little")
                for j in range(16):
                    if d + j >= len(m.rom):
                        break
                    c = m.read(0x9100, d + j)
                    ev.append(("E", 0x9100, d + j, c, 3))
                    if c == END:
                        break
                    ev.append(("W", WRITER_PC, BUFFER + out, c, 3))
                    out += 1
            else:
                ev.append(("W", WRITER_PC, BUFFER + out, v, 3))
                out += 1
        return ev


# -- tracing the variants


def test_text_drawn_into_vram(folder):
    r = trace(folder, VramGame(build_rom(), 1))
    n = len(TEXT)
    assert r.output == "vram" and len(r.sources) == len(r.typed) - 0
    assert r.string == (START, START + n)  # the end token included
    assert r.settled == 7
    assert {p.address: p.moves for p in r.pointers}[TABLE + 2] == 2
    kinds = {c.value: c.kind for c in r.codes}
    assert kinds[END] == "end" and kinds[LETTERS["x"]] == "printable"
    images = {c.value: c.image for c in r.codes if c.kind == "printable"}
    assert images[LETTERS["x"]] != images[LETTERS["y"]]


def test_a_dictionary_word_first_is_not_the_string(folder):
    r = trace(folder, DictGame(dict_rom(), 0), "Welcome to the Island of Tests")
    assert not r.packed
    assert r.string == (STRINGS, STRINGS + len(" to the Island of ") + 2)
    assert {p.address for p in r.pointers} >= {TABLE, TABLE + 1}
    assert any(not s.stream for s in r.sources) and any(s.stream for s in r.sources)
    assert all(s.stream == (STRINGS < s.byte < DICT) for s in r.sources)


def test_a_byte_read_twice_has_only_its_copy_changed(folder):
    r = trace(folder, ScanGame(build_rom(), 1))
    kinds = {c.value: c.kind for c in r.codes}
    # Written to the ROM, END would have stopped the measuring too.
    assert kinds[LETTERS["x"]] == "char" and kinds[END] == "char"


def test_text_read_from_a_ram_buffer_is_swept_there(folder):
    r = trace(folder, BufferGame(build_rom(), 1))
    assert not r.sources and r.code_ram == ("snesWorkRam", DECODE & 0x1FFFF)
    kinds = {c.value: (c.kind, c.out) for c in r.codes}
    assert kinds[LETTERS["x"] ^ 0x55] == ("char", [LETTERS["x"]])


def test_a_write_through_a_mirror_is_the_same_byte(folder):
    r = trace(folder, MirrorGame(build_rom(), 1))
    assert len(r.sources) == len(TEXT) - 1


# -- the helpers


def test_advancing_follows_the_longest_run():
    stream = list(range(0xE5968, 0xE5968 + 40))
    dictionary_first = [0x74800, 0x74801, 0x74802] + stream
    assert advancing(dictionary_first) > 1 - PACKED
    run = advancing_run(dictionary_first)
    assert run == list(range(3, 43))
    assert advancing([9 * n for n in range(20)]) == 0
    assert advancing([5]) == 1.0 and advancing_run([]) == []


def test_replaced_one_for_one():
    assert replaced([1, 2, 3], [1, 3, 3], 1) == [3]
    assert replaced([5, 5, 5, 5], [5, 9, 5, 5], 1) == [9]
    assert replaced([1, 2, 3, 4, 5], [1, 7, 7, 3, 4, 5], 1) == [7, 7]


def tracer_on(folder, evs) -> Tracer:
    t = object.__new__(Tracer)
    t.ev = evidence.Evidence.of(folder, CONSOLE, evs)
    t.result = Result()
    return t


def test_the_extent_follows_a_reader_over_frames(folder):
    evs = []
    for k in range(10):
        evs.append(("E", 0x200 + k, 0x41, 10 + k, READER_PC))
        for n in range(5000):  # what else the game reads that frame
            evs.append(("E", 0x800 + (n & 15), 0, 10 + k, 0x8100))
    t = tracer_on(folder, evs)
    assert t._extent(0x204) == 0x209
    # A later read of the same byte, elsewhere, does not restart it.
    evs.append(("E", 0x204, 0x41, 900, READER_PC))
    t = tracer_on(folder, evs)
    assert t._extent(0x204, at=4 * 5001) == 0x209


class Stub:
    """A probe server whose test says a set changes the output when it holds
    ``hit``, and gets no answer for any set of more than ``limit``."""

    def __init__(self, hit, limit):
        self.hit, self.limit, self.n, self.no_answer = hit, limit, 0, 0

    def test(self, writes, frame, reads=()):
        yield None
        if len(writes) > self.limit:
            return None
        return any(a == self.hit for a, _ in writes)


def test_halving_goes_on_through_no_answer(folder):
    t = tracer_on(folder, [])
    t.rom, t._servers = bytes(64), []
    first = {a: 1 for a in range(32)}
    assert drive(t._find(Stub(21, 4), list(first), first)) == [21]
    notes = []
    t.result.notes = notes
    assert drive(t._find(Stub(21, 0), [21], {21: 1})) == []
    assert "no answer" in notes[0]


def test_reads_window_matches_its_oracle():
    random.seed(1)
    for _ in range(200):
        evs, f = [], 0
        for _ in range(random.randint(5, 80)):
            f += random.random() < 0.3
            evs.append(
                (
                    random.choice("EEW"),
                    random.randint(0, 12),
                    random.randint(0, 3),
                    f,
                    1,
                )
            )
        r = Reads(evs)
        for i in sorted(random.sample(range(len(evs)), min(6, len(evs)))):
            for back in (1, 4, None):
                tried = set(random.sample(range(13), 3))
                v = random.randint(0, 3)
                assert r.window(i, back, tried, v) == r._walk(i, back, tried, v)


def test_a_result_survives_json():
    r = Result(
        output="ram",
        region=("snesWorkRam", 1, 2),
        string=(3, 4),
        code_ram=("snesWorkRam", 5),
        decoder={65: "A"},
        sources=[Source(0, 3, "copied", [0], stream=False)],
        pointers=[Pointer(9, 2, "read before")],
        codes=[Code(1, "char", [1])],
        typed=[("A", 65, 3)],
        gaps=[(" ", [31])],
        strays=[7],
        copies=[Copy(0x40, 0x10, "literal")],
        follows_call=0x1234,
    )
    back = Result.from_json(r.to_json())
    assert back == r
    # A result saved before copies and calls were kept still loads.
    old = r.to_json()
    del old["copies"], old["follows_call"]
    for p in old["pointers"]:
        del p["note"]
    back = Result.from_json(old)
    assert back.copies == [] and back.follows_call is None
    assert back.pointers == [Pointer(9, 2, "read before")]


class States:
    """A probe server's states, for the tracer's counting."""

    states = [0]

    def state_for(self, frame):
        return 1


def test_a_gba_byte_counts_the_wider_reads_covering_it(folder):
    import dataclasses

    evs = [
        ("E", 0x104, 0, 1, 0xA),  # halfword read covering $105
        ("E", 0x105, 0, 1, 0xB),  # byte read by another routine
        ("E", 0x104, 0, 2, 0xA),
        ("E", 0x106, 0, 2, 0xA),  # another word: not counted
    ]
    t = tracer_on(folder, evs)
    t.console = dataclasses.replace(CONSOLE, lua="gba")
    assert t._only_read(States(), 0x105, 2) == (0xA, 2)
    t.console = CONSOLE
    assert t._only_read(States(), 0x105, 1) is None  # read once, byte-wide


# -- the pointer stage: every copy of a GBA pointer, and the Game Boy's


def gba_rom() -> bytearray:
    """A GBA ROM holding one pointer, $08001234, five more times."""
    import struct

    rom = bytearray(0x3000)
    ptr = struct.pack("<I", 0x08001234)
    rom[0x100:0x104] = ptr  # the confirmed one
    rom[0x200:0x204] = ptr  # read this run
    rom[0x400:0x404] = ptr  # a Thumb literal: ldr r0,[pc,#8] at $3F4
    rom[0x3F4:0x3F6] = bytes([0x02, 0x48])
    rom[0x800:0x804] = ptr  # an ARM literal: ldr r1,[pc,#-16] at $808
    rom[0x808:0x80C] = (0xE51F1010).to_bytes(4, "little")
    rom[0xC00:0xC04] = ptr  # among other pointers
    rom[0xC04:0xC08] = struct.pack("<I", 0x08002000)
    rom[0x2000:0x2004] = ptr  # among zeros: data
    rom[0x2402:0x2406] = ptr  # not 4-aligned: no word
    return rom


def test_a_literal_is_what_an_ldr_reaches():
    rom = gba_rom()
    assert ldr_reaches(rom, 0x400) and ldr_reaches(rom, 0x800)
    assert not ldr_reaches(rom, 0x2000)


def test_every_copy_of_a_gba_pointer_is_listed_and_ranked():
    rom = gba_rom()
    # The replay read the copy at $200 and ran both ldr instructions.
    ran = {0x200, 0x3F4, 0x808}
    got = drive(gba_copies(bytes(rom), 0x100, ran.__contains__))
    assert [(c.address, c.kind) for c in got] == [
        (0x200, "touched"),
        (0x400, "literal"),
        (0x800, "literal"),
        (0xC00, "other"),
        (0x2000, "data"),
    ]
    assert {c.of for c in got} == {0x100}
    # Not knowing what the replay read, nothing is said to be read.
    got = drive(gba_copies(bytes(rom), 0x100, None))
    assert [c.kind for c in got][:1] == ["literal"]


def test_the_pointer_stage_lists_copies_of_a_confirmed_gba_pointer(folder):
    rom = gba_rom()

    def where(r):
        return int.from_bytes(r[0x100:0x104], "little") - 0x08000000

    evs = [("E", 0x100 + k, rom[0x100 + k], 1, 0x8000) for k in range(4)]
    evs.append(("E", 0x1234, 0, 1, 0x8010))
    r = pointer_stage(folder, consoles.GBA, rom, evs, 0x1234, where)
    assert {(p.address, p.moves) for p in r.pointers} == {(0x100, 2), (0x101, 512)}
    assert [c.address for c in r.copies] == [0x400, 0x800, 0xC00, 0x200, 0x2000]
    # Elsewhere the same words are not looked for.
    r = pointer_stage(folder, CONSOLE, rom, evs, 0x1234, where)
    assert r.pointers and not r.copies


def test_text_around_a_copy_is_no_ldr():
    # Text whose "AL" sits where a Thumb ldr r4,[pc,#$104] would reach $1000.
    rom = bytearray(0x2000)
    rom[0x1000:0x1004] = (0x08001234).to_bytes(4, "little")
    rom[0xEF8:0xEFA] = b"AL"
    assert ldr_reaches(rom, 0x1000)  # two bytes alone: could be code
    rom[0xEE0:0xF20] = b"THE WALL IS TALL AND THE HALL IS SMALL. ALL SAW IT" + bytes(14)
    rom[0xEF8:0xEFA] = b"AL"
    assert not ldr_reaches(rom, 0x1000)  # inside a run of text
    # With what the replay ran known, only code it ran counts.
    rom[0xEE0:0xF20] = bytes(64)
    rom[0xEF8:0xEFA] = b"AL"
    assert not ldr_reaches(rom, 0x1000, lambda i: False)
    assert ldr_reaches(rom, 0x1000, lambda i: i == 0xEF8)
    # A copy of a pointer in the text of a string table is data.
    ptr = rom[0x1000:0x1004]
    rom[0x100:0x104] = ptr
    rom[0x1800:0x1840] = b"Welcome to the Island of Tests! It is ALL yours now.   "[:64]
    rom[0x1810:0x1814] = ptr
    got = drive(gba_copies(bytes(rom), 0x100, None))
    assert {c.address: c.kind for c in got}[0x1810] == "data"


def test_game_boy_operands_and_banks():
    rom = bytearray(0x10000)
    rom[0x150:0x153] = bytes([0x21, 0x34, 0x52])  # ld hl,$5234
    rom[0x160:0x163] = bytes([0xFA, 0x34, 0x52])  # ld a,($5234)
    assert gb_operand(rom, 0x151) == "ld hl,nn"
    assert gb_operand(rom, 0x161) == ""
    target = 0x9234  # bank 2, $5234
    assert gb_bank_fits(rom, 0x151, target)  # bank 0
    assert gb_bank_fits(rom, 0x8100, target)  # its own bank
    assert not gb_bank_fits(rom, 0x4100, target)  # bank 1 sees another $5234
    assert gb_bank_fits(rom, 0x4100, 0x1234)  # a bank-0 string, from anywhere
    rom[0xC200:0xC203] = bytes([0x34, 0x52, 0x02])  # dw $5234; db 2 in bank 3
    assert gb_bank_fits(rom, 0xC200, target)  # a far pointer names the bank
    rom[0xC202] = 0x03
    assert not gb_bank_fits(rom, 0xC200, target)
    # The Z80's ld ix/iy,nn is its own instruction.
    rom[0x170:0x174] = bytes([0xDD, 0x21, 0x34, 0x52])
    assert gb_operand(rom, 0x172, z80=True) == "ld ix,nn"
    rom[0x170] = 0xFD
    assert gb_operand(rom, 0x172, z80=True) == "ld iy,nn"
    assert gb_operand(rom, 0x172) == "ld hl,nn"  # the Game Boy has no prefix


def test_a_string_that_follows_a_call():
    rom = bytearray(0x1000)
    rom[0x300:0x303] = bytes([0xCD, 0x00, 0x20])  # call $2000
    rom[0x303:0x308] = b"HELLO"
    assert gb_follows_call(rom, 0x303, 0x2010) == 0x300
    assert gb_follows_call(rom, 0x303, 0x3010) is None  # read by another routine
    rom[0x300] = 0xDC  # call c,$2000
    assert gb_follows_call(rom, 0x304, 0x2000) == 0x300  # a byte between
    assert gb_follows_call(rom, 0x305, 0x2000) == 0x300  # two, the most
    assert gb_follows_call(rom, 0x306, 0x2000) is None
    assert gb_follows_call(rom, 0x303, None) is None
    # The reader PC is a ROM offset: a routine in bank 3 at $4100.
    rom[0x300:0x303] = bytes([0xCD, 0x00, 0x41])
    assert gb_follows_call(rom, 0x303, 3 * 0x4000 + 0x108) == 0x300
    assert gb_follows_call(rom, 0x303, 1 << 24 | 0x4108) is None  # from RAM


def test_the_game_boy_s_static_hits_skip_other_banks(folder):
    import dataclasses

    rom = bytearray(0x10000)
    target = 0x9234  # bank 2
    for at in (0x0150, 0x4150, 0x8150):
        rom[at : at + 3] = bytes([0x21, 0x34, 0x52])
    t = tracer_on(folder, [("E", target, 0, 1, 0x2000)])
    t.rom = bytes(rom)
    t.console = dataclasses.replace(
        CONSOLE, id="gb", to_bus=lambda off: [0x4000 | (off & 0x3FFF)]
    )
    hits = [c[0] for c in t._static_pointers(target, set(), 0)]
    assert hits == [0x0151, 0x8151]


def gb_rom() -> bytearray:
    """Mortal Kombat II's shape: ``ld hl,$4141`` in bank 0 reaches
    "START GAME" at $8141, bank 2."""
    rom = bytearray(0x10000)
    rom[0x30F:0x312] = bytes([0x21, 0x41, 0x41])
    rom[0x8141:0x814C] = b"START GAME\x00"
    rom[0x5000:0x5002] = bytes([0x41, 0x41])  # in bank 1: another $4141
    return rom


def gb_where(slot):
    def where(rom):
        v = int.from_bytes(rom[slot : slot + 2], "little")
        return 2 * 0x4000 + (v - 0x4000) if v >= 0x4000 else v

    return where


def test_the_pointer_stage_names_a_game_boy_operand(folder):
    rom = gb_rom()
    evs = [("E", 0x8141, 0x53, 5, 0x1735)]
    r = pointer_stage(folder, consoles.GB, rom, evs, 0x8141, gb_where(0x310))
    assert [(p.address, p.moves, p.note) for p in r.pointers] == [
        (0x310, 2, "ld hl,nn"),
        (0x311, 512, ""),  # the byte above: no instruction before it
    ]
    assert r.follows_call is None and not r.notes


def test_a_string_after_a_call_is_said_once_no_pointer_is_found(folder):
    rom = bytearray(0x10000)
    rom[0x300:0x303] = bytes([0xCD, 0x00, 0x20])
    rom[0x303:0x309] = b"HELLO\x00"
    evs = [("E", 0x303, 0x48, 5, 0x2010)]
    r = pointer_stage(folder, consoles.GB, rom, evs, 0x303, lambda _: 0x303)
    assert r.follows_call == 0x300 and not r.pointers
    assert r.notes == [
        "the string follows a call at $300: the call site is its reference, "
        "and no pointer holds it"
    ]
    # A pointer found after all: the finding stays, the note does not.
    rom[0x30F:0x312] = bytes([0x21, 0x03, 0x03])
    r = pointer_stage(folder, consoles.GB, rom, evs, 0x303, gb_where(0x310))
    assert r.follows_call == 0x300 and r.pointers and not r.notes


def test_a_vram_code_keeps_the_occurrences_width():
    from mapchar.capture.trace import vram_unit

    assert vram_unit(4, [0x41, 0x42]) == 4  # 32-bit reads of a 4-byte code
    assert vram_unit(1, [0x41, 0x1234]) == 2
    assert vram_unit(1, [0x41]) == 1
