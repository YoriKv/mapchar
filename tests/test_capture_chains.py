"""Relative search over codes for typed text (:mod:`mapchar.capture.chains`):
voiced kana spelled with a mark code, the tightest chain winning however many
places hold the text, separators that stand on another code, text stored
backwards, and the positions a word is tried at."""

from __future__ import annotations

from mapchar.capture import chains
from mapchar.capture.chains import BEFORE, GOJUON, JIS, MARK, MAX_GAP, Codes


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


# -- voiced kana as a kana and a mark code

KANA = GOJUON["hiragana"]
DAKUTEN, HANDAKUTEN = 0x5E, 0x5F
SPACE = 0x70


def gojuon(text: str, mark_before: bool = False) -> list[int]:
    """Kana at $10 + gojūon index, a voiced one as its plain kana and a mark
    code."""
    import unicodedata

    out = []
    for ch in text:
        if ch == " ":
            out.append(SPACE)
            continue
        d = unicodedata.normalize("NFD", ch)
        code = [0x10 + KANA.index(d[0])]
        if len(d) == 2:
            mark = DAKUTEN if d[1] == "゙" else HANDAKUTEN
            code = [mark] + code if mark_before else code + [mark]
        out += code
    return out


def test_a_voiced_kana_is_its_kana_and_a_mark_in_gojuon_order():
    (w,) = chains.words("がっこう", GOJUON)[:1]
    # っ is no letter of the runs; が splits into か and its mark.
    assert w.letters[0][2] == "か" and w.marks[0] == "゙"
    assert w.text == "が" and w.span == 2
    (w,) = chains.words("かばん", GOJUON)
    assert [m for m in w.marks] == ["", "゙", ""]
    assert w.span == 4 and w.offsets() == [0, 1, 3] and w.offsets(1) == [0, 2, 3]
    assert w.text == "かばん"
    # Typed decomposed, it reads the same; in Shift-JIS order it is one code.
    assert chains.words("がばん", GOJUON)[0] == chains.words("がばん")[0]
    (j,) = chains.words("かばん", JIS)
    assert not j.marks and j.text == "かばん"


def test_voiced_kana_chain_with_their_mark_after_or_before():
    text = "ぶどうだ ですが"
    for before in (False, True):
        seq = [0x01, 0x02] + gojuon(text, before) + [0x03]
        found, ws = chains.chains(seq, text, GOJUON)
        assert found, before
        c = found[0]
        assert c.bases["hiragana"] == 0x10
        assert c.bases[MARK + "゙"] == DAKUTEN
        assert c.bases[BEFORE] == int(before)
        assert (c.start, c.end) == (2, 2 + len(gojuon(text)))
        dec = chains.decoder(seq, c, ws, GOJUON)
        assert dec[DAKUTEN] == "゙"  # the combining mark: it voices its kana
        assert dec[0x10 + KANA.index("は")] == "は"
        at = dict(chains.letters_at(c, ws))
        assert at[2 + (1 if before else 0)] == "ふ"


def test_a_mark_code_is_one_code_for_every_kana_it_voices():
    text = "ざぶとん がくせい"
    seq = gojuon(text)
    good, _ = chains.chains(seq, text, GOJUON)
    assert good
    # A different code after ぶ than after ざ is not one mark.
    bad = list(seq)
    bad[bad.index(0x10 + KANA.index("ふ")) + 1] = 0x5C
    found, _ = chains.chains(bad, text, GOJUON)
    assert not found


def test_a_single_code_voiced_kana_still_chains_in_shift_jis_order():
    text = "がっこう です"
    seq = [0x100 + JIS["hiragana"].index(c) if c != " " else 0x1FF for c in text]
    found, _ = chains.chains(seq, text, JIS)
    assert found and found[0].bases["hiragana"] == 0x100


# -- the tightest chain


def test_a_line_redrawn_as_it_grows_gives_its_tightest_chain():
    text = "Welcome to the Island of Tests"
    full = codes_of(text)
    seq = []
    for n in range(1, len(full) + 1):
        seq += full[:n]
    seq += full
    found, _ = chains.chains(seq, text)
    assert found[0].span == len(full)
    assert found[0].start >= len(seq) - 2 * len(full)


def test_a_word_further_than_the_gap_does_not_join():
    near = codes_of("Hello") + [7] * MAX_GAP + codes_of("there")
    assert chains.chains(near, "Hello there")[0]
    far = codes_of("Hello") + [7] * (MAX_GAP + 1) + codes_of("there")
    found, ws = chains.chains(far, "Hello there")
    assert not found and not chains.match(far, ws)


def test_the_limit_keeps_the_most_words_then_the_tightest():
    text = "Welcome to the Island"
    loose = codes_of("Welcome") + [7] * 20 + codes_of(" to the Island")
    tight = codes_of(text)
    seq = (loose + [9] * 50) * 30 + tight
    ws = chains.words(text)
    found = chains.match(seq, ws, limit=3)
    assert len(found) == 3
    assert found[0].span == len(tight)
    assert all(c.count == len(ws) for c in found)


# -- separators


def test_a_separator_on_several_codes_is_the_most_frequent():
    # A space typed three times: twice the space code, once a line break.
    text = "Hello there my dear friend"
    seq = (
        codes_of("Hello")
        + [0x1F]
        + codes_of("there")
        + [0xF1]
        + codes_of("my")
        + [0x1F]
        + codes_of("dear")
        + [0x1F]
        + codes_of("friend")
    )
    found, ws = chains.chains(seq, text)
    dec = chains.decoder(seq, found[0], ws, GOJUON)
    assert dec[0x1F] == " "
    assert 0xF1 not in dec
    assert (" ", [0xF1]) in chains.gaps(seq, found[0], ws)


# -- text stored backwards


def test_text_stored_backwards_chains_in_reverse():
    text = "Adventure Log"
    seq = [5, 5] + codes_of(text)[::-1] + [6]
    found, ws = chains.chains(seq, text)
    assert not found
    rev = chains.complete(chains.match(seq, ws, reverse=True), ws)
    (c,) = rev[:1]
    assert c.reverse and (c.start, c.end) == (2, 2 + len(text))
    at = dict(chains.letters_at(c, ws))
    assert at[len(seq) - 2] == "A" and at[2] == "g"
    dec = chains.decoder(seq, c, ws, GOJUON)
    assert dec[0x00] == "A" and dec[0x1F] == " "
    assert chains.gaps(seq, c, ws) == [(" ", [0x1F])]


# -- where a word is tried


def test_positions_backward_and_forward_agree():
    seq = codes_of("abc xyz abc qqq abc")
    codes = Codes(seq)
    (w,) = chains.words("abc")
    bases = {"lower": 0x40}
    fwd = list(codes.positions(w, bases, 0, len(seq) - 3))
    back = list(codes.positions(w, bases, 0, len(seq) - 3, backward=True))
    assert fwd == [0, 8, 16] and back == [16, 8, 0]
    # Unknown bases: the steps between letters pick the positions.
    assert 8 in list(codes.positions(w, {}, 0, len(seq) - 3))
    assert codes.next_fit(1, w, bases) == (8, bases)
    assert codes.next_fit(16, w, bases, backward=True) == (8, bases)
    assert codes.next_fit(1, w, bases, reach=4) is None


def test_codes_above_a_byte_and_past_the_largest_character():
    wide = [0x200 + c for c in codes_of("Big codes here")]
    found, _ = chains.chains(wide, "Big codes here")
    assert found and found[0].bases["upper"] == 0x200
    # A value past what a character can be never matches, and the rest still
    # search by value.
    huge = [chains.VMAX + 5] + wide + [chains.VMAX]
    found, _ = chains.chains(huge, "Big codes here")
    assert found and found[0].start == 1
    # Known codes past a byte are nowhere in a sequence of bytes.
    (w,) = chains.words("Big")
    narrow = Codes(codes_of("Big codes here"))
    assert list(narrow.positions(w, {"upper": 0x300, "lower": 0x340}, 0, 9)) == []


def test_the_chain_is_found_past_thousands_of_partial_ones():
    seq = codes_of("that time " * 2500)
    at = len(seq) - 500
    seq[at:at] = codes_of("that time was long")
    found, _ = chains.chains(seq, "that time was long")
    assert found and (found[0].start, found[0].span) == (at, 18)


def test_lines_two_tilemap_rows_apart_chain_with_a_wider_reach():
    # A font two cells high in a 32-cell tilemap: a line's top row, its
    # bottom row, then the next line — 50 cells between two words.
    row = 32
    cells = [0x1F] * (row * 8)
    for li, line in enumerate(["Hello there,", "welcome home."]):
        base = li * 2 * row + 1
        for k, c in enumerate(codes_of(line)):
            cells[base + k] = c
            cells[base + row + k] = c + 0x80
    found, _ = chains.chains(cells, "Hello there, welcome home.")
    assert found and found[0].start == 1
