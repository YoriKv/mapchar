from __future__ import annotations

from mapchar.core.textmatch import matches_words, words_of


def test_words_of_folds_and_splits():
    assert words_of("  Sword   FISH ") == ["sword", "fish"]
    assert words_of("") == []
    assert words_of("   ") == []


def test_words_of_composes_before_folding():
    # NFD ga and NFC ga are the same word.
    assert words_of("が") == words_of("が")


def test_no_words_matches_everything():
    assert matches_words([], "anything")
    assert matches_words([])


def test_every_word_must_be_in_some_field():
    words = words_of("sword fish")
    assert matches_words(words, "a sword", "one fish")
    assert matches_words(words, "swordfish")
    assert not matches_words(words, "a sword", "one hook")


def test_fields_are_matched_apart():
    # A word is never matched across the seam between two fields.
    assert not matches_words(words_of("ab"), "a", "b")


def test_matching_is_case_and_form_insensitive():
    assert matches_words(words_of("ELF"), "an Elf")
    assert matches_words(words_of("が"), "がme")
