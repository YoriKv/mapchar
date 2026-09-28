"""Capture: relative search over codes, bit layouts, console facts, the line
protocol, evidence, finding the typed text, tracing, combining and the session
— on synthetic evidence and the scripted fake emulator of
:mod:`capture_fake`, so no emulator and no game data are needed."""

from __future__ import annotations

import os
import socket
import time

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
    build_rom,
    encode,
    write_moment,
)
from mapchar.capture import bitlayout, chains, consoles, evidence, occurrence, setup
from mapchar.capture.combine import Sighting, combine
from mapchar.capture.proposals import BLOCK, ENTRIES, GLYPH, propose
from mapchar.capture.protocol import WAIT, Closed, Lines, Listener, Timeout, drive
from mapchar.capture.session import DONE, FAILED, WAITING, Session
from mapchar.capture.trace import Tracer, edits_kind, replaced
from mapchar.core.block import EndToken, PointerTableSource
from mapchar.core.table import TokenKind
from mapchar.plugins.registry import default_registry, resolve_mapping

# -- relative search over codes


def codes_of(text: str, a: int = 0x00, lower: int = 0x40, space: int = 0x1F):
    out = []
    for ch in text:
        if ch.isupper():
            out.append(a + ord(ch) - 65)
        elif ch.islower():
            out.append(lower + ord(ch) - 97)
        else:
            out.append(space)
    return out


def test_words_split_at_what_is_not_a_letter():
    ws = chains.words("Hello, world! 42")
    assert [w.text for w in ws] == ["Hello", "world", "42"]
    assert [w.sep for w in ws] == ["", ", ", "! "]


def test_a_chain_holds_every_word_and_its_bases():
    seq = [9, 9] + codes_of("Welcome to the Island") + [9]
    found, ws = chains.chains(seq, "Welcome to the Island")
    assert found and found[0].start == 2
    assert found[0].bases == {"upper": 0, "lower": 0x40}
    dec = chains.decoder(seq, found[0], ws, chains.GOJUON)
    assert dec[0x40 + 4] == "e" and dec[0x1F] == " "


def test_the_tightest_chain_wins():
    far = codes_of("Hello") + [7] * 30 + codes_of("there")
    near = codes_of("Hello there")
    found, _ = chains.chains(far + [7] * 5 + near, "Hello there")
    assert found[0].span == len(near)


def test_line_ends_between_words_are_absorbed():
    # An engine that marks a line's last character in bit 7 still chains the
    # words either side of the line break.
    seq = codes_of("Hello") + [0x1F, 0x80 | 0x13] + codes_of("here is the rest")[1:]
    found, _ = chains.chains(seq, "Hello there is the rest")
    assert not found
    best = chains.match(seq, chains.words("Hello there is the rest"))
    assert best and best[0].count >= 3


def test_kana_in_shift_jis_order():
    text = "つよくて やさしい"
    order = chains.JIS
    seq = []
    for ch in text:
        seq.append(0x100 + order["hiragana"].index(ch) if ch != " " else 0x1FF)
    found, _ = chains.chains(seq, text, chains.GOJUON)
    assert not found
    found, _ = chains.chains(seq, text, chains.JIS)
    assert found and found[0].bases["hiragana"] == 0x100
    assert chains.orders_for(text) == [chains.GOJUON, chains.JIS]


def test_gaps_keep_what_was_typed_between_words():
    seq = codes_of("Hi there") + [0x1B] + codes_of(" Bye now")
    found, ws = chains.chains(seq, "Hi there. Bye now")
    assert (". ", [0x1B, 0x1F]) in chains.gaps(seq, found[0], ws)


# -- bit layouts


def pack(codes: list[int], width: int, escapes: int, start: int) -> bytes:
    bits = "0" * start
    for c in codes:
        if c >= 1 << width:
            bits += format(c >> width, f"0{width}b") + format(
                c & ((1 << width) - 1), f"0{width}b"
            )
        else:
            bits += format(c, f"0{width}b")
    bits += "0" * (-len(bits) % 8)
    return bytes(int(bits[i : i + 8], 2) for i in range(0, len(bits), 8))


def test_a_packed_stream_fits_its_own_layout():
    # A dictionary of entries; codes 5 bits wide, the top two escaping.
    dictionary = {1: (100, 1), 2: (101, 1), 3: (102, 2), (31 << 5) | 4: (110, 3)}
    rom = bytearray(256)
    for a in range(100, 120):
        rom[a] = a
    script = [1, 2, 3, (31 << 5) | 4, 2, 1, 3]
    data = pack(script, 5, 2, 3)
    outputs = []
    for c in script:
        a0, n = dictionary[c]
        outputs += list(range(a0, a0 + n))
    found = bitlayout.fits(outputs, data, rom.__getitem__)
    keys = {f.key for f in found}
    assert ("msb", 5, 2) in keys
    lay = next(f for f in found if f.key == ("msb", 5, 2) and f.start == 3)
    assert lay.table[(31 << 5) | 4] == (110, 3)
    assert lay.code_bits((31 << 5) | 4) == "1111100100"


def test_two_sightings_decide_one_layout():
    rom = bytes(range(256))
    dictionary = {c: (7 * c, 1) for c in range(1, 30)}

    def sighting(script):
        outs = [dictionary[c][0] for c in script]
        return bitlayout.fits(outs, pack(script, 5, 0, 0), rom.__getitem__)

    a = sighting([1, 2, 3, 4, 5, 6, 1, 2, 7, 19, 23, 2, 29, 3, 17, 6, 5, 11])
    b = sighting([8, 9, 10, 11, 1, 3, 5, 21, 13, 17, 1, 2, 26, 14, 19, 8, 9])
    decision = bitlayout.combine([a, b])
    assert decision.decided is not None
    assert decision.decided[0] == ("msb", 5, 0)


def test_merge_refuses_a_code_with_two_meanings():
    assert bitlayout.merge([{1: (5, 1)}, {1: (6, 1)}]) is None
    assert bitlayout.merge([{1: (5, 1)}, {2: (5, 1)}]) is None
    assert bitlayout.merge([{1: (5, 1)}, {2: (6, 1)}]) == {1: (5, 1), 2: (6, 1)}


# -- console facts


def test_console_mappings():
    lorom = consoles.SNES_LOROM
    assert lorom.to_rom(0x05A5D9) == 0x2A5D9
    assert 0x05A5D9 in lorom.to_bus(0x2A5D9)
    assert consoles.SNES_HIROM.to_rom(0xC4C194) == 0x04C194
    assert consoles.SNES_SUPERFX.to_rom(0x5110DB) == 0x1110DB
    assert consoles.SNES_SUPERFX.to_rom(0x09E9C1) == 0x04E9C1
    assert consoles.GBA.to_rom(0x09BC330A) == 0x1BC330A
    assert lorom.ram_of(0x7F8381) == ("snesWorkRam", 0x18381)
    assert consoles.NES.ram_of(0x6010) == ("nesSaveRam", 0x10)


def test_detect_and_image():
    ines = b"NES\x1a\x02\x01" + bytes(10) + bytes(0x8000) + bytes(0x2000)
    assert consoles.detect("x.nes", ines) is consoles.NES
    assert consoles.NES.header(ines) == 16
    assert len(consoles.NES.image(ines)) == 0x8000
    gba = bytearray(0x200)
    gba[0xB2] = 0x96
    assert consoles.detect("x.bin", bytes(gba)) is consoles.GBA
    snes = bytearray(0x10000)
    snes[0x7FD5] = 0x20
    snes[0x7FDC:0x7FE0] = bytes([0xFF, 0xFF, 0x00, 0x00])
    assert consoles.detect("x.sfc", bytes(snes)) is consoles.SNES_LOROM
    snes[0x7FD6] = 0x15
    assert consoles.detect("x.sfc", bytes(snes)) is consoles.SNES_SUPERFX
    assert consoles.detect("x.txt", b"hello") is None


def test_lua_literals():
    assert consoles.lua_value({"a": [1, None, True], "b": "p"}) == (
        "{ a = { 1, nil, true }, b = [==[p]==] }"
    )


def test_scripts_are_assembled_with_their_settings():
    from mapchar.capture.emulator import build_script

    text = build_script("probe", consoles.SNES_LOROM, {"port": 5})
    assert "port = 5" in text and "local function hookReads" in text
    assert "res %d same" in text


# -- the line protocol


def test_lines_arrive_whole_however_they_are_cut():
    a, b = socket.socketpair()
    lines = Lines(b)
    a.sendall(b"hel")
    assert lines.poll() is None
    a.sendall(b"lo\nwor")
    assert lines.poll() == "hello"
    assert lines.poll() is None
    a.sendall(b"ld\n")
    assert drive(lines.readline(time.monotonic() + 1)) == "world"
    a.close()
    with pytest.raises(Closed):
        drive(lines.readline(time.monotonic() + 1))


def test_a_silent_end_times_out():
    a, b = socket.socketpair()
    with pytest.raises(Timeout):
        drive(Lines(b).readline(time.monotonic() + 0.05))
    a.close()


def test_a_listener_accepts_a_script():
    lst = Listener()
    step = lst.accept(time.monotonic() + 2)
    assert next(step) is WAIT
    c = socket.create_connection(("127.0.0.1", lst.port))
    conn = drive(step)
    c.sendall(b"hi\n")
    assert drive(conn.readline(time.monotonic() + 1)) == "hi"
    lst.close()
    c.close()


# -- evidence


def test_a_moment_reads_with_and_without_its_clock():
    m = evidence.Moment.parse(
        "capture frame=620 poll=619 clock=123 hash=AB:CD\nstate 1 frame=500 poll=499\n"
    )
    assert (m.frame, m.clock, m.hash, m.state(1).frame) == (620, 123, "AB:CD", 500)
    assert evidence.Moment.parse("capture frame=5 poll=4 hash=X\n").clock is None


def test_a_moment_reads_its_text_and_pinned_states():
    m = evidence.Moment.parse(
        "capture frame=5000 poll=4990 hash=X\nfont watched\n"
        "text first=1564 last=1800\n"
        "state 1 frame=1356 poll=1350 pinned\nstate 2 frame=1469 poll=1460 pinned\n"
        "state 3 frame=3277 poll=3270\nstate 4 frame=3390 poll=3380\n"
    )
    assert m.font and m.text == (1564, 1800)
    assert [s.pinned for s in m.states] == [True, True, False, False]
    assert m.gap == (1800 + evidence.GAP_TAIL, 3277)
    folder_settings = evidence.script_settings(m, ".", FakeEmulator(Game(b"")))
    assert (
        folder_settings["gap"] == [1860, 3277] and folder_settings["stateFrame"] == 1356
    )
    # No pinned state, or a ring that reaches back to the text: nothing between.
    plain = evidence.Moment.parse(
        "capture frame=5 poll=4 hash=X\nstate 1 frame=0 poll=0\n"
    )
    assert not plain.font and plain.text is None and plain.gap is None
    near = evidence.Moment.parse(
        "capture frame=2000 poll=1990 hash=X\ntext first=1564 last=1800\n"
        "state 1 frame=1469 poll=1460 pinned\nstate 2 frame=1808 poll=1800\n"
    )
    assert near.gap is None


# -- the setup


def test_a_font_is_given_in_the_rom_or_in_ram(tmp_path):
    f = setup.rom_font(0x70000, 0x71FFF, 0x100000)
    assert (f.memory, f.start, f.end) == (setup.ROM, 0x70000, 0x71FFF)
    with pytest.raises(ValueError):
        setup.rom_font(0x10, 0x0F, 0x100000)
    with pytest.raises(ValueError):
        setup.rom_font(0xFFF00, 0x100000, 0x100000)
    snes = consoles.SNES_LOROM
    r = setup.ram_font(snes, 0x7F1200, 0x7F13FF)
    assert (r.memory, r.start, r.end, r.bus) == (
        "snesWorkRam",
        0x11200,
        0x113FF,
        0x7F1200,
    )
    with pytest.raises(ValueError):
        setup.ram_font(consoles.NES, 0x0700, 0x6100)  # internal RAM into save RAM
    s = setup.Setup(r)
    s.save(str(tmp_path))
    assert setup.Setup.load(str(tmp_path)) == s
    setup.Setup().save(str(tmp_path))
    assert setup.Setup.load(str(tmp_path)).font is None
    assert setup.Setup.load(str(tmp_path / "none")).font is None


def test_the_recorder_breaks_on_the_font_for_its_readers():
    yi = consoles.SNES_SUPERFX
    rom = setup.Setup(setup.rom_font(0x4BD2F, 0x4C92E, 0x200000)).recorder(yi)
    assert rom["font"] == {
        "memory": "snesPrgRom",
        "lo": 0x4BD2F,
        "hi": 0x4C92E,
        "cpus": ["snes", "gsu"],
    }
    assert rom["quiet"] == setup.QUIET
    ram = setup.Setup(setup.ram_font(yi, 0x7F0000, 0x7F0FFF)).recorder(yi)
    assert ram["font"]["memory"] == "snesWorkRam" and ram["font"]["cpus"] == ["snes"]
    assert setup.Setup().recorder(yi) == {}


@pytest.fixture
def game():
    return Game(build_rom(), message=1)


@pytest.fixture
def folder(tmp_path):
    f = tmp_path / "cap"
    write_moment(str(f))
    return str(f)


def load(folder, game):
    emu = FakeEmulator(game)
    r = drive(evidence.replay(emu, "rom.bin", CONSOLE, folder), 30)
    assert r.match
    return drive(evidence.Evidence.load(folder, CONSOLE, 0))


def test_the_replay_records_reads_and_writes(folder, game):
    ev = load(folder, game)
    reads = [e for e in ev.events if e[0] == "E" and e[4] == READER_PC]
    writes = [e for e in ev.events if e[0] == "W"]
    assert len(writes) == len(MESSAGES[1])
    assert writes[0][1] == BUFFER and writes[0][3] == 3
    assert reads[-1][2] == END
    assert ev.ram("snesWorkRam")[0x2000] == LETTERS["W"]


def test_a_replay_that_differs_says_so(folder, game):
    r = drive(
        evidence.replay(FakeEmulator(game, match=False), "r", CONSOLE, folder), 30
    )
    assert not r.match


# -- finding the text


def test_the_text_is_found_in_the_ram_writes(folder, game):
    ev = load(folder, game)
    occ, bad = drive(occurrence.find(ev, "Welcome to the Island of Tests!"))
    assert bad is None
    assert occ.kind == "ram" and occ.pc == WRITER_PC
    # The chain ends at the last word: the "!" after it is not typed text.
    assert len(occ.events) == len(MESSAGES[1]) - 1
    assert occ.decoder[LETTERS["W"]] == "W"


def test_what_the_capture_window_is_told(folder, game):
    ev = load(folder, game)
    _, bad = drive(occurrence.find(ev, "Welcome to the Islnad of Tests!"))
    assert bad.kind == "no-match" and "Islnad" not in bad.matched
    assert "Islnad" in bad.message
    assert drive(occurrence.find(ev, "Wel"))[1].kind == "too-short"
    assert drive(occurrence.find(ev, "…!?"))[1].kind == "no-letters"
    _, bad = drive(occurrence.find(ev, "Goodbye cruel world"))
    assert bad.kind == "no-match"


def test_text_drawn_before_the_replay_is_found_in_ram(folder, game):
    ev = load(folder, game)
    # Neither the writes nor the reads that made it are in the replay.
    ev.events = [e for e in ev.events if e[0] != "W" and e[4] != READER_PC]
    _, bad = drive(occurrence.find(ev, "Welcome to the Island of Tests!"))
    assert bad.kind == "before" and "snesWorkRam" in bad.where


# -- tracing


def test_changes_to_the_output():
    assert edits_kind([1, 2, 3], [1, 2]) == "truncate"
    assert edits_kind([1, 2, 3], [1, 2, 3, 4]) == "extend"
    assert edits_kind([1, 2, 3], [1, 9, 3]) == "substitute"
    assert replaced([1, 2, 3], [1, 7, 7, 3], 1) == [7, 7]
    assert replaced([1, 2, 3], [1, 3], 1) == []
    assert replaced([1, 2, 3], [1], 1) is None


def trace(folder, game, text):
    ev = load(folder, game)
    occ, _ = drive(occurrence.find(ev, text))
    emu = FakeEmulator(game)
    t = Tracer(emu, "rom.bin", game.rom, CONSOLE, folder, ev, occ)
    return drive(t.run(), 120)


def test_tracing_finds_sources_pointer_and_codes(folder, game):
    r = trace(folder, game, "Welcome to the Island of Tests!")
    start = STRINGS + len(MESSAGES[0]) + 1
    n = len(MESSAGES[1])
    assert [s.byte for s in r.sources] == list(range(start, start + n - 1))
    assert all(s.tier == "copied" for s in r.sources)
    assert r.string == (start, start + n)  # through the "!" and the end token
    assert not r.packed
    moves = {p.address: p.moves for p in r.pointers}
    assert moves[TABLE + 2] == 2 and moves[TABLE + 3] == 512
    kinds = {c.value: c.kind for c in r.codes}
    assert kinds[END] == "truncate"
    assert kinds[LETTERS["x"]] == "char"
    assert kinds[encode("W")[0]] == "same"


# -- combining


def sightings(tmp_path, texts):
    out = []
    for n, (message, text) in enumerate(texts):
        folder = str(tmp_path / f"c{n}")
        write_moment(folder)
        game = Game(build_rom(), message)
        out.append(Sighting(f"c{n}", text, trace(folder, game, text)))
    return out


MAPPINGS = {"linear": resolve_mapping(default_registry(), "linear")}


def test_two_captures_give_the_table_and_its_end(tmp_path):
    ss = sightings(
        tmp_path,
        [(1, "Welcome to the Island of Tests!"), (2, "Every string ends here.")],
    )
    c = combine(ss, build_rom(), CONSOLE, MAPPINGS)
    (e,) = c.engines
    assert e.end_code == END
    t = e.table
    assert (t.start, t.stride, t.size, t.mapping_id, t.confirmed) == (
        TABLE,
        2,
        2,
        "linear",
        True,
    )
    assert t.stop == TABLE + 2 * len(MESSAGES)
    assert c.meanings[f"{LETTERS['W']:02X}"].text == "W"
    assert c.meanings[f"{END:02X}"].kind == "end"
    props = propose(c, "captured", shift=16)
    block = next(p for p in props if p.kind == BLOCK)
    assert isinstance(block.config.source, PointerTableSource)
    assert block.config.source.start == TABLE + 16
    assert isinstance(block.config.string_type, EndToken)
    entries = next(p for p in props if p.kind == ENTRIES).entries
    assert any(x.kind is TokenKind.END and x.bits == "11111111" for x in entries)
    assert any(p.kind == GLYPH for p in props)


# -- the session


def run_session(session, seconds=60):
    end = time.monotonic() + seconds
    while session.advance(0.05):
        assert time.monotonic() < end
        time.sleep(0.001)


def make_session(tmp_path, game, **kw):
    rom = tmp_path / "game.bin"
    rom.write_bytes(game.rom)
    return Session(
        str(tmp_path / "game.bin.capture"), str(rom), CONSOLE, FakeEmulator(game, **kw)
    )


def arrive(session, name="0001", extra=""):
    src = os.path.join(session.root, "incoming", name)
    write_moment(src + "_tmp", extra)
    for f, to in (
        ("moment.txt", ".txt"),
        ("s01.mss", "_s01.mss"),
        ("input.txt", "_input.txt"),
        ("screen.png", ".png"),
    ):
        os.replace(os.path.join(src + "_tmp", f), src + to)
    return session.add_moment(name, src)


def test_a_moment_left_unannounced_is_taken_in_on_opening(tmp_path, game):
    s = make_session(tmp_path, game)
    incoming = os.path.join(s.root, "incoming")
    for name in ("0001", "0002"):
        write_moment(os.path.join(incoming, name + "_tmp"))
        for f, to in (("moment.txt", ".txt"), ("s01.mss", "_s01.mss")):
            os.replace(
                os.path.join(incoming, name + "_tmp", f),
                os.path.join(incoming, name + to),
            )
    # Only 0001 is whole: 0002 has no input yet.
    os.replace(
        os.path.join(incoming, "0001_tmp", "input.txt"),
        os.path.join(incoming, "0001_input.txt"),
    )
    again = Session(s.root, s.rom_path, CONSOLE, s.emulator)
    assert [c.id for c in again.captures] == ["0001"]
    assert again.captures[0].needs_text
    assert not os.path.exists(os.path.join(incoming, "0001.txt"))
    assert os.path.exists(os.path.join(incoming, "0002.txt"))
    assert "0002_input.txt" in again.messages[-1]


def test_the_session_plays_with_its_setup(tmp_path, game):
    s = make_session(tmp_path, game)
    s.set_setup(setup.Setup(setup.rom_font(0x200, 0x2FF, len(game.rom))))
    s.play()
    with open(os.path.join(s.root, "_recorder.lua"), encoding="utf-8") as fh:
        script = fh.read()
    assert "font = { memory = [==[snesPrgRom]==], lo = 512, hi = 767" in script
    assert "local function armFont" not in script and "armFont = function" in script
    s.stop_playing()
    again = Session(s.root, s.rom_path, CONSOLE, s.emulator)
    assert again.setup.font.start == 0x200


def test_a_font_never_read_is_said_when_a_capture_arrives(tmp_path, game):
    s = make_session(tmp_path, game)
    arrive(s, "0001", "font watched\n")
    assert any("font was not read" in m for m in s.messages)
    arrive(s, "0002", "font watched\ntext first=3 last=5\n")
    assert sum("font was not read" in m for m in s.messages) == 1


def test_a_session_traces_a_capture_to_done(tmp_path, game):
    s = make_session(tmp_path, game)
    cap = arrive(s)
    assert cap.needs_text and cap.screenshot
    assert s.submit(cap, "Wel").kind == "too-short"
    assert s.submit(cap, "Welcome to the Island of Tests!") is None
    run_session(s)
    assert cap.state == DONE, cap.reason
    assert cap.result.pointers
    again = Session(s.root, s.rom_path, CONSOLE, s.emulator)
    (back,) = again.captures
    assert back.state == DONE and back.result.string == cap.result.string
    assert again.sightings()[0].typed == "Welcome to the Island of Tests!"


def test_a_capture_that_does_not_replay_fails(tmp_path, game):
    s = make_session(tmp_path, game, match=False)
    cap = arrive(s)
    s.submit(cap, "Welcome to the Island of Tests!")
    run_session(s)
    assert cap.state == FAILED and "reproduce" in cap.reason


def test_a_capture_can_be_stopped_skipped_and_confirmed(tmp_path, game):
    s = make_session(tmp_path, game)
    cap = arrive(s)
    s.submit(cap, "Welcome to the Island of Tests!")
    s.advance(0.01)
    s.stop()
    assert cap.state == FAILED and cap.reason == "stopped"
    s.retry(cap)
    assert cap.state == WAITING
    before, after = drive(s.confirm(cap, [(STRINGS, 0x00)]), 30)
    assert os.path.exists(before) and os.path.exists(after)
    s.skip(cap)
    assert not s.captures and not os.path.exists(cap.folder)
