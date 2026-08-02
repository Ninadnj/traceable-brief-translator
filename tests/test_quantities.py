from __future__ import annotations

from decimal import Decimal

from compoundx.models import NumericTokenKind, QuantityKind, QuantityQualifier
from compoundx.quantities import extract_numeric_tokens, extract_quantities


def _values(text: str) -> list[Decimal]:
    return [token.value for token in extract_numeric_tokens(text)]


def _kinds(text: str) -> list[NumericTokenKind]:
    return [token.kind for token in extract_numeric_tokens(text)]


def test_negative_temperatures_keep_their_sign() -> None:
    text = "Impact testing at -20 °C and −40 °C."

    assert _values(text) == [Decimal("-20"), Decimal("-40")]
    assert _kinds(text) == [NumericTokenKind.DIGIT, NumericTokenKind.DIGIT]
    assert [quantity.values for quantity in extract_quantities(text)] == [
        (Decimal("-20"),),
        (Decimal("-40"),),
    ]


def test_decimals_tokenize_at_the_precision_written_in_the_source() -> None:
    text = "Cycle times of .75 s, 0.0 s and 12.5 s were recorded."

    tokens = extract_numeric_tokens(text)

    assert [token.raw_text for token in tokens] == [".75", "0.0", "12.5"]
    assert [token.value for token in tokens] == [
        Decimal("0.75"),
        Decimal("0.0"),
        Decimal("12.5"),
    ]


def test_thousand_separators_are_stripped_from_the_compared_value() -> None:
    token = extract_numeric_tokens("A durability target of 1,200 cycles.")[0]

    assert token.raw_text == "1,200"
    assert token.value == Decimal("1200")


def test_percentages_carry_their_value_and_percent_unit() -> None:
    text = "At least 30% recycled content is expected."

    assert _values(text) == [Decimal("30")]

    quantity = extract_quantities(text)[0]

    assert quantity.kind is QuantityKind.PERCENTAGE
    assert quantity.values == (Decimal("30"),)
    assert quantity.unit == "%"
    assert quantity.qualifier is QuantityQualifier.MINIMUM


def test_currency_amounts_record_their_symbol_and_value() -> None:
    text = "The unit price is $12.50."

    assert _values(text) == [Decimal("12.50")]

    quantity = extract_quantities(text)[0]

    assert quantity.kind is QuantityKind.CURRENCY
    assert quantity.values == (Decimal("12.50"),)
    assert quantity.currency == "$"


def test_a_range_written_with_to_yields_a_single_two_valued_quantity() -> None:
    text = "Operating range 5 to 40 °C."

    assert _values(text) == [Decimal("5"), Decimal("40")]

    quantities = extract_quantities(text)

    assert len(quantities) == 1
    assert quantities[0].kind is QuantityKind.RANGE
    assert quantities[0].values == (Decimal("5"), Decimal("40"))
    assert quantities[0].upper_unit == "°C"


def test_a_range_written_with_an_en_dash_yields_both_bounds_unsigned() -> None:
    text = "Operating range 5–40 °C."

    assert _values(text) == [Decimal("5"), Decimal("40")]
    assert extract_quantities(text)[0].values == (Decimal("5"), Decimal("40"))


def test_a_hyphenated_range_does_not_sign_its_upper_bound() -> None:
    # The verifier compares token values, so if a hyphen separating two numbers
    # were absorbed as a sign, evidence quoting "10-15 kg" would fail to support
    # an output saying "15 kg". Both public functions must agree about the text.
    for text in ("The load range is 10-15 kg.", "The load range is 10 - 15 kg."):
        assert _values(text) == [Decimal("10"), Decimal("15")]

    assert extract_quantities("The load range is 10-15 kg.")[0].values == (
        Decimal("10"),
        Decimal("15"),
    )


def test_a_sign_that_follows_no_number_still_signs_its_value() -> None:
    assert _values("Conditioned at -10 °C overnight.") == [Decimal("-10")]
    assert _values("A drop of -5 units was recorded.") == [Decimal("-5")]
    assert _values("approximately -10°C to 45°C") == [Decimal("-10"), Decimal("45")]


def test_a_tolerance_records_its_nominal_and_tolerance_values() -> None:
    for text in ("Clearance 4.0 mm ± 0.2 mm.", "Clearance 4.0 mm +/- 0.2 mm."):
        quantities = extract_quantities(text)

        assert len(quantities) == 1
        assert quantities[0].kind is QuantityKind.TOLERANCE
        assert quantities[0].values == (Decimal("4.0"), Decimal("0.2"))
        assert quantities[0].unit == "mm"
        assert quantities[0].upper_unit == "mm"


def test_digits_fused_to_letters_are_classified_as_identifiers() -> None:
    text = "Grades PA6, R2D2 and ABS-1200X with an M8 bolt."

    assert set(_kinds(text)) == {NumericTokenKind.IDENTIFIER}
    assert extract_quantities(text) == ()


def test_a_number_standing_apart_from_words_is_a_digit_not_an_identifier() -> None:
    text = "Standard ISO 9001 lists 3 bolts."

    assert _kinds(text) == [NumericTokenKind.DIGIT, NumericTokenKind.DIGIT]
    assert _values(text) == [Decimal("9001"), Decimal("3")]


def test_a_number_fused_to_its_unit_is_a_digit_not_an_identifier() -> None:
    # The verifier drops IDENTIFIER tokens on BOTH sides of its comparison. If the
    # very common <value><unit> form counted as an identifier, a fabricated number
    # written that way would bypass the same-object numeric rule entirely.
    for text in ("The mass budget is 250g.", "A 30mm boss.", "Payload 12kg."):
        assert [token.kind for token in extract_numeric_tokens(text)] == [
            NumericTokenKind.DIGIT
        ]

    assert [token.kind for token in extract_numeric_tokens("The mass budget is 250 g.")] == [
        NumericTokenKind.DIGIT
    ]
    assert extract_quantities("The mass budget is 250g.")[0].values == (Decimal("250"),)
    assert extract_quantities("The mass budget is 250 g.")[0].unit == "g"


def test_a_letter_introducing_digits_still_makes_an_identifier() -> None:
    # The distinction is which side the letter is on: PA6 names a grade, 250g
    # measures a mass.
    assert [token.kind for token in extract_numeric_tokens("Grade PA6 only.")] == [
        NumericTokenKind.IDENTIFIER
    ]
    assert [token.kind for token in extract_numeric_tokens("Sensor R_2 failed.")] == [
        NumericTokenKind.IDENTIFIER
    ]


def test_qualifiers_are_preserved_on_digit_quantities() -> None:
    text = (
        "Approximately 15% reduction, at least 30 g mass, no more than 5 kg, "
        "up to 9 tests, less than 2 s."
    )

    assert [quantity.qualifier for quantity in extract_quantities(text)] == [
        QuantityQualifier.APPROXIMATE,
        QuantityQualifier.MINIMUM,
        QuantityQualifier.MAXIMUM,
        QuantityQualifier.MAXIMUM,
        QuantityQualifier.MAXIMUM,
    ]


def test_written_numbers_tokenize_with_their_arithmetic_value() -> None:
    text = "Twenty-five of one thousand two hundred cycles failed."

    tokens = extract_numeric_tokens(text)

    assert [token.kind for token in tokens] == [
        NumericTokenKind.WRITTEN,
        NumericTokenKind.WRITTEN,
    ]
    assert [token.value for token in tokens] == [Decimal("25"), Decimal("1200")]


def test_a_written_quantity_does_not_record_its_qualifier() -> None:
    # Qualifier parsing is modelled for digit quantities only, so "at least
    # twenty" is indistinguishable from a bare "twenty" downstream.
    quantity = extract_quantities("At least twenty units.")[0]

    assert quantity.kind is QuantityKind.WRITTEN
    assert quantity.values == (Decimal("20"),)
    assert quantity.qualifier is None


def test_tokens_are_returned_in_source_order_across_digits_and_words() -> None:
    text = "Section 2 covers one hundred cycles at 30 g."

    tokens = extract_numeric_tokens(text)

    assert [token.raw_text for token in tokens] == ["2", "one hundred", "30"]
    assert [token.start for token in tokens] == sorted(token.start for token in tokens)


def test_token_offsets_index_the_raw_text() -> None:
    text = "Rev 2 uses .75 s, −40 °C, 1,200 cycles and one hundred housings."

    for token in extract_numeric_tokens(text):
        assert text[token.start : token.end] == token.raw_text


def test_quantity_offsets_index_the_raw_text() -> None:
    text = (
        "Approximately 15% reduction over 5 to 40 °C at $12.50 per unit "
        "with 4.0 mm ± 0.2 mm clearance and one hundred housings."
    )

    for quantity in extract_quantities(text):
        assert text[quantity.start : quantity.end] == quantity.raw_text


def test_prose_without_numbers_yields_no_tokens_and_no_quantities() -> None:
    text = "Reliability is the first priority."

    assert extract_numeric_tokens(text) == ()
    assert extract_quantities(text) == ()
