"""The glossary's matching, replacing and interchange, with no window."""

from __future__ import annotations

from mapchar.project.glossary import (
    GlossaryTerm,
    glossary_dicts,
    glossary_from,
    glossary_from_text,
    glossary_text,
    merged_terms,
    missing_terms,
    replace_hit,
    replace_terms,
    term_hits,
    term_uses,
)

FIRE = GlossaryTerm("Fire", "Feu")
SWORD = GlossaryTerm("Fire Sword", "Épée de feu")


def test_hits_are_in_reading_order_and_the_longer_term_wins():
    hits = term_hits([FIRE, SWORD], "Fire! The Fire Sword.")
    assert [(h.start, h.stop, h.term) for h in hits] == [(0, 4, FIRE), (10, 20, SWORD)]


def test_a_term_never_matches_inside_a_code():
    line = GlossaryTerm("line", "ligne")
    assert term_hits([line], "A[line]B") == []
    assert replace_terms([line], "one line[line]") == ("one ligne[line]", 1)


def test_matching_options_are_each_term_s_own():
    ann = GlossaryTerm("Ann", "Anne", whole_word=True)
    assert [h.start for h in term_hits([ann], "Annex, Ann and ANN")] == [7, 15]
    cased = GlossaryTerm("Ann", "Anne", match_case=True, whole_word=True)
    assert [h.start for h in term_hits([cased], "Annex, Ann and ANN")] == [7]


def test_replacing_skips_the_terms_with_no_translation():
    bare = GlossaryTerm("Sword")
    assert replace_terms([FIRE, bare], "Fire Sword") == ("Feu Sword", 1)
    hit = term_hits([FIRE], "a Fire")[0]
    assert replace_hit("a Fire", hit) == "a Feu"


def test_missing_terms_are_the_ones_translated_some_other_way():
    assert missing_terms([FIRE], "Fire!", "Feu !") == []
    assert missing_terms([FIRE], "Fire!", "Flamme !") == [FIRE]
    # A term with nothing to become cannot be missed.
    assert missing_terms([GlossaryTerm("Fire")], "Fire!", "Flamme !") == []


def test_uses_count_strings_not_occurrences():
    uses = term_uses([FIRE, SWORD], ["Fire, Fire", "Fire Sword", "ice"])
    assert uses == {FIRE: 1, SWORD: 1}


def test_options_round_trip_through_the_project_file_and_a_tsv():
    terms = [
        FIRE,
        GlossaryTerm("Ann", "Anne", "the\thero", match_case=True, whole_word=True),
    ]
    assert glossary_from(glossary_dicts(terms)) == terms
    assert glossary_dicts([FIRE]) == [{"t": "Fire", "r": "Feu"}]
    assert glossary_from_text(glossary_text(terms)) == terms
    assert glossary_from_text(glossary_text(terms, ","), ",") == terms
    # A bare list of terms is a glossary too.
    assert glossary_from_text("Ice\tGlace\n\nBolt\n") == [
        GlossaryTerm("Ice", "Glace"),
        GlossaryTerm("Bolt"),
    ]


def test_an_import_lays_over_the_terms_already_there():
    merged = merged_terms(
        [FIRE, GlossaryTerm("Ice", "Glace")],
        [GlossaryTerm("fire", "Flamme"), GlossaryTerm("Bolt", "Foudre")],
    )
    assert merged == [
        GlossaryTerm("Fire", "Flamme"),
        GlossaryTerm("Ice", "Glace"),
        GlossaryTerm("Bolt", "Foudre"),
    ]
