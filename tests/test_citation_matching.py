from __future__ import annotations

from compoundx.citation_matching import (
    ExactMatch,
    normalize_with_mapping,
    normalized_exact_matches,
)


DASH_VARIANTS = (
    "­",  # soft hyphen
    "‐",  # hyphen
    "‑",  # non-breaking hyphen
    "‒",  # figure dash
    "–",  # en dash
    "—",  # em dash
    "―",  # horizontal bar
    "−",  # minus sign
)


def _only(matches: tuple[ExactMatch, ...]) -> ExactMatch:
    assert len(matches) == 1
    return matches[0]


def test_a_quote_wrapped_across_pdf_lines_still_matches_the_source() -> None:
    source_text = "The housing protects and\nretains the braking mechanism.\n"
    quote = "The housing protects and retains the braking mechanism."

    match = _only(normalized_exact_matches(source_text, quote))

    assert match.source_quote == "The housing protects and\nretains the braking mechanism."


def test_collapsed_runs_of_whitespace_in_the_source_still_match_single_spaces() -> None:
    source_text = "Reliability   is\t the\n\n  first priority.\n"
    quote = "Reliability is the first priority."

    match = _only(normalized_exact_matches(source_text, quote))

    assert source_text[match.start : match.end] == "Reliability   is\t the\n\n  first priority."


def test_normalization_collapses_whitespace_runs_to_a_single_space() -> None:
    normalized = normalize_with_mapping("a  \n\t b")

    assert normalized.text == "a b"
    assert len(normalized.original_starts) == len(normalized.text)
    assert len(normalized.original_ends) == len(normalized.text)


def test_curly_quotes_in_the_quote_match_straight_quotes_in_the_source() -> None:
    source_text = "The finish must stay \"black\" and 'low-gloss'.\n"
    quote = "The finish must stay “black” and ‘low-gloss’."

    match = _only(normalized_exact_matches(source_text, quote))

    assert match.source_quote == "The finish must stay \"black\" and 'low-gloss'."


def test_straight_quotes_in_the_quote_match_curly_quotes_in_the_source() -> None:
    source_text = "The finish must stay “black” and ‘low-gloss’.\n"
    quote = "The finish must stay \"black\" and 'low-gloss'."

    match = _only(normalized_exact_matches(source_text, quote))

    assert match.source_quote == "The finish must stay “black” and ‘low-gloss’."


def test_every_unicode_dash_variant_in_the_source_matches_a_plain_hyphen() -> None:
    quote = "A low-gloss finish."

    for dash in DASH_VARIANTS:
        source_text = f"A low{dash}gloss finish.\n"
        match = _only(normalized_exact_matches(source_text, quote))
        assert match.source_quote == f"A low{dash}gloss finish."


def test_a_plain_hyphen_in_the_source_matches_every_unicode_dash_variant() -> None:
    source_text = "A low-gloss finish.\n"

    for dash in DASH_VARIANTS:
        quote = f"A low{dash}gloss finish."
        match = _only(normalized_exact_matches(source_text, quote))
        assert match.source_quote == "A low-gloss finish."


def test_nfkc_compatibility_forms_normalize_before_matching() -> None:
    ligature = _only(
        normalized_exact_matches("The ﬁnal oﬀset.\n", "The final offset.")
    )
    full_width = _only(
        normalized_exact_matches("Target ３０ mm.\n", "Target 30 mm.")
    )
    non_breaking_space = _only(
        normalized_exact_matches("Mass budget is fixed.\n", "Mass budget is fixed.")
    )
    combining_accent = _only(
        normalized_exact_matches("The café finish.\n", "The café finish.")
    )

    assert ligature.source_quote == "The ﬁnal oﬀset."
    assert full_width.source_quote == "Target ３０ mm."
    assert non_breaking_space.source_quote == "Mass budget is fixed."
    assert combining_accent.source_quote == "The café finish."


def test_a_reordered_paraphrase_does_not_match() -> None:
    source_text = "The surface must remain black and low-gloss.\n"

    assert normalized_exact_matches(
        source_text, "Black and low-gloss the surface must remain."
    ) == ()
    assert normalized_exact_matches(
        source_text, "The surface must remain low-gloss and black."
    ) == ()


def test_a_synonym_substitution_does_not_match() -> None:
    source_text = "The surface must remain black and low-gloss.\n"

    assert normalized_exact_matches(
        source_text, "The surface should remain black and low-gloss."
    ) == ()
    assert normalized_exact_matches(
        source_text, "The finish must remain black and low-gloss."
    ) == ()


def test_a_dropped_or_added_word_does_not_match() -> None:
    source_text = "The surface must remain black and low-gloss.\n"

    assert normalized_exact_matches(source_text, "The surface must remain black.") == ()
    assert normalized_exact_matches(
        source_text, "The surface must always remain black and low-gloss."
    ) == ()


def test_case_differences_do_not_match() -> None:
    source_text = "The surface must remain black and low-gloss.\n"

    assert normalized_exact_matches(
        source_text, "the surface must remain black and low-gloss."
    ) == ()
    assert normalized_exact_matches(
        source_text, "The Surface Must Remain Black And Low-Gloss."
    ) == ()


def test_a_hyphen_at_a_line_break_matches_both_of_its_readings() -> None:
    # PDF justification splits a word at a hyphen, and the split is ambiguous:
    # "low-gloss" is a compound, "scheduling" is one word broken mid-syllable.
    # The two readings differ by a single character and a quote matches at most
    # one, so both are tried rather than guessed between.
    compound = "A low-\ngloss finish.\n"
    broken = "Review sched-\nuling is quarterly.\n"

    assert (
        _only(normalized_exact_matches(compound, "A low-gloss finish.")).source_quote
        == "A low-\ngloss finish."
    )
    assert (
        _only(normalized_exact_matches(broken, "Review scheduling")).source_quote
        == "Review sched-\nuling"
    )


def test_a_soft_hyphen_matches_whether_or_not_the_quote_keeps_it() -> None:
    # A soft hyphen is the same optional-hyphen case as a line break.
    assert _only(normalized_exact_matches("sched\xaduling now", "scheduling")).source_quote == (
        "sched\xaduling"
    )
    assert _only(normalized_exact_matches("low\xadgloss now", "low-gloss")).source_quote == (
        "low\xadgloss"
    )


def test_only_a_line_break_between_two_letters_joins_a_hyphen() -> None:
    # A paragraph break, a numeric range and a list bullet are not word breaks.
    assert normalized_exact_matches("ends here-\n\nNew para", "here-New") == ()
    assert normalized_exact_matches("range 10-\n15 kg", "10-15") == ()
    assert normalized_exact_matches("items:\n- alpha\n- beta", "items:- alpha") == ()


def test_one_occurrence_is_never_counted_under_both_readings() -> None:
    # A single broken word must not report twice, once with the hyphen resolved
    # and once without, and a match may never open by skipping a hyphen.
    assert len(normalized_exact_matches("low-\ngloss", "low-gloss")) == 1
    assert len(normalized_exact_matches("low-\ngloss", "lowgloss")) == 1
    assert len(normalized_exact_matches("the co-\nop", "op")) == 1
    assert len(normalized_exact_matches("low-\ngloss and low-gloss", "low-gloss")) == 2


def test_a_quote_broken_in_one_place_and_intact_in_another_stays_ambiguous() -> None:
    # The dangerous direction: if the two readings were counted separately, this
    # citation would be certified unique and attributed to the second page only,
    # even though the statement appears twice. Ambiguity has to survive the fix.
    source_text = (
        "REQUIREMENTS\nThe wheel hard-\nness must be 78A.\n\n"
        "OUT OF SCOPE\nThe wheel hardness must be 78A.\n"
    )

    assert len(normalized_exact_matches(source_text, "The wheel hardness must be 78A.")) == 2


def test_a_quote_may_resolve_two_break_sites_differently() -> None:
    # Each site is decided by the quote, so one quote can join a compound and
    # close a mid-syllable break at the same time. A single global reading could
    # not express this and would reject the only correct quote.
    source_text = "The bear-\ning must be sealed and the wheel must be low-\ngloss.\n"

    assert (
        _only(
            normalized_exact_matches(
                source_text,
                "The bearing must be sealed and the wheel must be low-gloss.",
            )
        ).source_quote
        == "The bear-\ning must be sealed and the wheel must be low-\ngloss."
    )


def test_a_soft_hyphen_at_a_line_break_is_the_common_pdf_encoding() -> None:
    assert (
        _only(
            normalized_exact_matches("review sched\xad\nuling now", "review scheduling")
        ).source_quote
        == "review sched\xad\nuling"
    )


def test_a_dash_that_never_splits_a_word_keeps_its_dash_but_loses_the_break() -> None:
    # The line break is layout either way, so it always disappears. Only a
    # breaking hyphen is *optional*: no typesetter splits a word on an em dash,
    # so treating one as droppable would invent ambiguity rather than absorb it.
    source_text = "The plan is fixed—\nhowever the mass is not.\n"

    assert (
        _only(
            normalized_exact_matches(
                source_text, "The plan is fixed—however the mass is not."
            )
        ).source_quote
        == "The plan is fixed—\nhowever the mass is not."
    )
    assert normalized_exact_matches(source_text, "fixedhowever") == ()
    assert normalized_exact_matches("cost–\nbenefit", "costbenefit") == ()


def test_an_optional_hyphen_next_to_a_real_one_still_resolves() -> None:
    # Consuming the quote's hyphen at the optional site strands the real hyphen,
    # so the match only succeeds if that decision can be reconsidered.
    assert (
        _only(
            normalized_exact_matches("a multi\xad-purpose tool", "multi-purpose tool")
        ).source_quote
        == "multi\xad-purpose tool"
    )


def test_a_plain_source_word_never_matches_a_quote_that_adds_a_hyphen() -> None:
    # The optional hyphen is a property of the source, not a licence for the
    # model to introduce punctuation the brief does not contain.
    assert normalized_exact_matches("the coop was sealed", "co-op") == ()


def test_a_quote_occurring_twice_returns_two_matches() -> None:
    source_text = "The finish is low-gloss. The finish is\nlow-gloss.\n"

    matches = normalized_exact_matches(source_text, "The finish is low-gloss.")

    assert len(matches) == 2
    assert matches[0].start == 0
    assert matches[0].source_quote == "The finish is low-gloss."
    assert matches[1].source_quote == "The finish is\nlow-gloss."


def test_match_offsets_index_the_raw_source_text() -> None:
    source_text = "Preamble.\nThe housing protects and\nretains the mechanism.\nEnd.\n"
    quote = "The housing protects and retains the mechanism."

    match = _only(normalized_exact_matches(source_text, quote))

    assert source_text[match.start : match.end] == match.source_quote
    assert source_text[: match.start] == "Preamble.\n"
    assert source_text[match.end :] == "\nEnd.\n"


def test_match_offsets_stay_reversible_through_curly_quotes_and_dashes() -> None:
    source_text = (
        "Intro.\nThe “cold‑impact” protocol —\n"
        "with 30–35% margin — is\nundefined.\nTail.\n"
    )
    quote = 'The "cold-impact" protocol - with 30-35% margin - is undefined.'

    match = _only(normalized_exact_matches(source_text, quote))

    assert source_text[match.start : match.end] == match.source_quote
    assert (
        normalize_with_mapping(match.source_quote).text.strip()
        == normalize_with_mapping(quote).text.strip()
    )
    assert source_text[match.start - 1] == "\n"
    assert source_text[match.end] == "\n"


def test_an_empty_or_whitespace_only_quote_returns_no_matches() -> None:
    source_text = "The surface must remain black and low-gloss.\n"

    assert normalized_exact_matches(source_text, "") == ()
    assert normalized_exact_matches(source_text, "   ") == ()
    assert normalized_exact_matches(source_text, " \t\n  ") == ()


def test_a_quote_absent_from_the_source_returns_no_matches() -> None:
    source_text = "Reliability is the first priority.\n"

    assert normalized_exact_matches(source_text, "Nothing here.") == ()
    assert normalized_exact_matches("", "Reliability is the first priority.") == ()
