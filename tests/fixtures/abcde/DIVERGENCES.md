# Deliberate divergences from the reference tools

Each row names a behaviour where mapchar does not reproduce abcde, Cartographer,
Atlas or romjuice, and the fixture or test that exercises it. The reference
behaviour is documented under `docs/`.

| Divergence | mapchar | Exercised by |
|---|---|---|
| Counter sharing between `0`/fallback frames and their parent | Every frame has its own counter; only `+` propagates | `tests/test_decode.py::test_shared_counter_counts_towards_parent` |
| Auto-jump ranges only splice the match window | The same, plus the decoder's advance follows a jump mid-token (`test_decode.py::test_skip_inside_a_token`) | `test_examples.py` (Dragon Warrior II) |
| A `+` child finishing ends its parent | The parent keeps going until its own counter is used up | `test_decode.py::test_weight_two_finishes_a_count_of_two` |
| A raw count of `0` reads one bit; a raw switch overrides the string limit | `@raw:*` reads to the string limit | `test_decode.py::test_star_runs_to_end_of_data` |
| A read window past end of data is fatal | The string ends with `ended_by = DATA` | `test_decode.py::test_limit_and_partial_bits` |
| RAW blocks are one string for the whole range | A range source cuts at end tokens; the fixture test joins the strings to compare | `test_verify_abcde.py` |
| A RAW `FIXED_STRING` block dumps one string | A range source with fixed length dumps every string; the first is compared | `test_verify_abcde.py` |
| Unmatched bytes print `<$XX>` | `[$XX]`, converted on Atlas export | `test_verify_abcde.py` normalises |
| Pointer sorting by string comparison; duplicate targets dumped repeatedly | Numeric sort; one string per target with all its pointers | phase 3 |
| Tables and scripts NFD-normalised, Cartographer output NFC | NFC everywhere: table text, translations, font alphabets and search needles are composed on load and stored composed, and the encoder decomposes atoms so either spelling encodes | `tests/test_unicode.py` |
| Fallback bits not emitted at end of text on insert | Always emitted when the frame closes | phase 2 |
| Encoder without longest-prefix safety, non-optimal, end tokens suppress alternatives, frames keyed by file name | Prefix-safe Dijkstra keyed by table identity | phase 2 |
| romjuice text-mode ROM reads, stale end-of-file buffer, last-line truncation, `!00` never swapping | Not reproduced; a `!` swap names romjuice's second table file, which mapchar cannot, so it is dropped with a notice | `test_table_legacy.py::test_romjuice_swap_is_dropped_with_a_notice` |
