from __future__ import annotations

from helpers import table_set
from mapchar.core.bits import Bits, align_up
from mapchar.core.tokens import render
from mapchar.engines.decode import DecodeRules, EndedBy, decode

MAIN = """\
@table main
01=foo
02=bar
03=cat
{ab}
@table ItemNames
01=[Potion]
02=[HolyHandGrenadeOfAntioch]
03=[Sword]
@table FontNames
!01=[fn] @ItemNames:1+
02=[Green]
03<2>=[Batman]
"""

DATA = bytes.fromhex("AB 01 02 AB 03")


def run(ab_entry: str, data: bytes = DATA, **rules) -> tuple[str, EndedBy]:
    ts = table_set(MAIN.replace("{ab}", ab_entry), "main")
    result = decode(Bits(data), ts, 0, DecodeRules(**rules))
    return render(result.tokens), result.ended_by


def test_plain_text():
    assert run("AB=[line]") == ("[line]\nfoobar[line]\ncat", EndedBy.DATA)


def test_the_line_code_breaks_the_line_once():
    """The block's line code renders with a line break, by label: text that
    ends in it too, and only one break where the table text already holds one."""
    assert run("AB=x[line]")[0] == "x[line]\nfoobar" + "x[line]\ncat"
    assert run("AB=[line]\\n")[0] == "[line]\nfoobar[line]\ncat"
    assert run("AB=[line]", line_label="br")[0] == "[line]foobar[line]cat"
    assert run("AB=[br]", line_label="br")[0] == "[br]\nfoobar[br]\ncat"
    assert run("AB=[line]", line_label="")[0] == "[line]foobar[line]cat"


def test_max_lines_ends_the_string():
    text, ended = run("AB=[line]", max_lines=2)
    assert (text, ended) == ("[line]\nfoobar[line]\n", EndedBy.LINES)
    assert run("AB=[line]", max_lines=3) == ("[line]\nfoobar[line]\ncat", EndedBy.DATA)


def test_raw_count_switch():
    assert run("!AB=[Item_Name:] @raw:1")[0] == "[Item_Name:][$01]bar[Item_Name:][$03]"


def test_table_count_switch():
    assert run("!AB=[i] @ItemNames:1")[0] == "[i][Potion]bar[i][Sword]"


def test_two_params_run_in_order():
    text, _ = run("!AB=[if] @ItemNames:1 @FontNames:1")
    assert text == "[if][Potion][Green][if][Sword]"


def test_three_raw_bytes():
    assert run("!AB=[w] @raw:3")[0] == "[w][$01][$02][$AB]cat"


def test_fallback_bits_consumed_silently():
    text, _ = run("!AB=[page] @ItemNames:$AB")
    assert text == "[page][Potion][HolyHandGrenadeOfAntioch]cat"


def test_shared_counter_counts_towards_parent():
    # FontNames:2 — the [fn] switch weighs 1 and its shared child's match
    # weighs 1 more, so the child's match also finishes the FontNames frame.
    text, _ = run("!AB=[f] @FontNames:2")
    assert text == "[f][fn][HolyHandGrenadeOfAntioch][f][Batman]"


def test_weight_two_finishes_a_count_of_two():
    text, _ = run("!AB=[f] @FontNames:2", bytes.fromhex("AB 03 01"))
    assert text == "[f][Batman]foo"


def test_star_runs_to_end_of_data():
    text, ended = run("!AB=[x] @ItemNames:*")
    assert text == "[x][Potion][HolyHandGrenadeOfAntioch][$AB][Sword]"
    assert ended is EndedBy.DATA


def test_return_at_top_level_ends_string():
    text, ended = run("!AB=return")
    assert (text, ended) == ("", EndedBy.RETURN)


def test_return_pops_child_frame():
    ts = table_set(
        "@table main\n!AB=[n] @names:*\n01=one\n@table names\n02=two\n!FF=return\n",
        "main",
    )
    r = decode(Bits(bytes.fromhex("AB 02 FF 01")), ts, 0)
    assert render(r.tokens) == "[n]twoone"


def test_end_token_and_realign():
    ts = table_set("@table main\n41=A\n/00=[end]\n", "main")
    data = bytes.fromhex("41 41 00 41 41 41 00")
    r = decode(Bits(data), ts, 0, DecodeRules(realign=(4 * 8, 0)))
    assert render(r.tokens) == "AA[end]" and r.end_bit == 4 * 8
    r = decode(Bits(data), ts, 0, DecodeRules(end_terminated=False))
    assert render(r.tokens) == "AA[end]AAA[end]"


def test_limit_and_partial_bits():
    ts = table_set("@table main\n%11=a\n", "main")
    r = decode(Bits(bytes.fromhex("C0 3F")), ts, 0, DecodeRules(limit_bit=8))
    assert render(r.tokens) == "a[%000000]" and r.ended_by is EndedBy.LIMIT
    r = decode(Bits(bytes.fromhex("F0")), ts, 0, DecodeRules(limit_bit=5))
    assert render(r.tokens) == "aa[%0]"


def test_operands():
    ts = table_set("@table main\n$F0=[color],u8\n$F1=[win],u16,2\n41=A\n", "main")
    data = bytes.fromhex("F0 03 41 F1 34 12 AA BB 41 F0")
    r = decode(Bits(data), ts, 0)
    assert render(r.tokens) == "[color $03]A[win $1234 $AA $BB]A[color]"
    assert (
        r.tokens[0].bit_end == 16 and r.tokens[0].encoded_bits() == "1111000000000011"
    )
    assert r.notices and "cut short" in r.notices[0].message


def test_skips():
    ts = table_set("@table main\n41=A\n42=B\n", "main")
    data = bytes.fromhex("41 FF FF 42")
    r = decode(Bits(data), ts, 0, DecodeRules(skips=((8, 24),)))
    assert render(r.tokens) == "AB"


def test_unmatched_bytes_do_not_disturb_a_count():
    ts = table_set("@table main\n!AB=[n] @names:2\n@table names\n01=x\n", "main")
    r = decode(Bits(bytes.fromhex("AB 01 FF 01 01")), ts, 0)
    assert render(r.tokens) == "[n]x[$FF]x[$01]"


def test_skip_inside_a_token():
    # 4-bit entries; a skip starting mid-byte splices the window.
    ts = table_set("@table main\n%0001=a\n%0010=b\n%0011=c\n", "main")
    data = bytes.fromhex("12 FF FF 3F")
    # Reading bit 8 (start of FF FF) continues at bit 24 (the 3F byte).
    r = decode(Bits(data), ts, 0, DecodeRules(skips=((8, 24),)))
    assert render(r.tokens) == "abc[%1111]"
    # A skip in the middle of a token: bits 4..6 then from 26.
    r = decode(Bits(bytes.fromhex("10 00 00 30")), ts, 0, DecodeRules(skips=((6, 26),)))
    assert render(r.tokens).startswith("a")


def test_labelled_return_with_raw_bytes():
    ts = table_set(
        "@table main\n41=A\n!F0=[sub] @names:*\n@table names\n01=x\n"
        "!FE=[pal] @raw:2 return\n!FF=[back] return\n",
        "main",
    )
    r = decode(Bits(bytes.fromhex("F0 01 FE AA BB 41 F0 01 FF 41")), ts, 0)
    assert render(r.tokens) == "[sub]x[pal][$AA][$BB]A[sub]x[back]A"
    # At the top level a return ends the string.
    ts = table_set("@table main\n41=A\n!FF=[bye] @raw:1 return\n", "main")
    r = decode(Bits(bytes.fromhex("41 FF 01 41")), ts, 0)
    assert render(r.tokens) == "A[bye][$01]" and r.ended_by is EndedBy.RETURN


def test_realign_rounds_up_to_the_next_multiple():
    assert (align_up(5, 4), align_up(8, 4), align_up(5, 4, 2)) == (8, 8, 6)
    assert align_up(5, 0) == 5  # no multiple, no move


def test_a_count_read_from_the_data_opens_the_frame():
    # 02 is read as the count, prints nothing, and two ItemNames follow.
    text, _ = run("!AB=[list] @ItemNames:u8", bytes.fromhex("AB 02 01 02 03"))
    assert text == "[list][Potion][HolyHandGrenadeOfAntioch]cat"
    # A count of zero: the frame closes at once.
    assert run("!AB=[list] @raw:u8", bytes.fromhex("AB 00 01"))[0] == "[list]foo"
    # Two counted parameters, each count read as its frame opens.
    text, _ = run(
        "!AB=[two] @ItemNames:u8 @raw:u8", bytes.fromhex("AB 01 03 02 01 02 03")
    )
    assert text == "[two][Sword][$01][$02]cat"
    # Cut short: the count bits are unmatched data and the frame is gone.
    ts = table_set(MAIN.replace("{ab}", "!AB=[n] @raw:u16"), "main")
    result = decode(Bits(bytes.fromhex("AB 05")), ts, 0)
    assert render(result.tokens) == "[n][$05]" and result.notices
