"""Generic lexical extraction of numeric tokens and quantity candidates."""

from __future__ import annotations

import re
from decimal import Decimal

from compoundx.models import (
    NumericToken,
    NumericTokenKind,
    Quantity,
    QuantityKind,
    QuantityQualifier,
)


_DIGIT_NUMBER = (
    r"[+\-−]?"
    r"(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d*)?|\.\d+)"
    r"(?:[eE][+\-]?\d+)?"
)
_DIGIT_RE = re.compile(_DIGIT_NUMBER)

_SMALL_NUMBERS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}
_TENS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
_NUMBER_WORDS = tuple(_SMALL_NUMBERS) + tuple(_TENS) + ("hundred", "thousand")
_WORD_ALTERNATION = "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True))
_WRITTEN_RE = re.compile(
    rf"\b(?:{_WORD_ALTERNATION})(?:[ -]+(?:and[ -]+)?"
    rf"(?:{_WORD_ALTERNATION}))*\b",
    re.IGNORECASE,
)

_QUALIFIER_TEXT = (
    r"approximately|approximate|approx\.?|about|around|roughly|at least|at most|"
    r"no less than|no more than|above|below|more than|less than|up to"
)
_UNIT_ATOM = r"(?:°?[A-Za-zµμΩ]+(?:\^?[+\-]?\d+|[²³])?)"
_UNIT = rf"(?:%|{_UNIT_ATOM}(?:[·*/]{_UNIT_ATOM})*)"
_CURRENCY = r"[$€£]"

_TOLERANCE_RE = re.compile(
    rf"(?:(?P<qualifier>{_QUALIFIER_TEXT})[ \t]+)?"
    rf"(?:(?P<currency>{_CURRENCY})[ \t]*)?"
    rf"(?P<nominal>{_DIGIT_NUMBER})[ \t]*(?P<nominal_unit>{_UNIT})?"
    rf"[ \t]*(?:±|\+/-)[ \t]*"
    rf"(?P<tolerance>{_DIGIT_NUMBER})[ \t]*(?P<tolerance_unit>{_UNIT})?",
    re.IGNORECASE,
)

_RANGE_RE = re.compile(
    rf"(?:(?P<qualifier>{_QUALIFIER_TEXT})[ \t]+)?"
    rf"(?:(?P<currency>{_CURRENCY})[ \t]*)?"
    rf"(?P<low>{_DIGIT_NUMBER})[ \t]*(?P<low_unit>{_UNIT})?"
    rf"[ \t]*(?:to|–|—|-)[ \t]*"
    rf"(?:(?P<upper_currency>{_CURRENCY})[ \t]*)?"
    rf"(?P<high>{_DIGIT_NUMBER})[ \t]*(?P<high_unit>{_UNIT})?",
    re.IGNORECASE,
)

_SCALAR_RE = re.compile(
    rf"(?:(?P<qualifier>{_QUALIFIER_TEXT})[ \t]+)?"
    rf"(?:(?P<currency>{_CURRENCY})[ \t]*)?"
    rf"(?P<number>{_DIGIT_NUMBER})"
    rf"(?:(?P<percent>%)|(?:[ \t]*|-)(?P<unit>{_UNIT})|"
    rf"(?P<per_unit>/{_UNIT_ATOM}(?:[·*/]{_UNIT_ATOM})*))?",
    re.IGNORECASE,
)

_FOLLOWING_UNIT_RE = re.compile(rf"[ \t]+(?P<unit>{_UNIT})")
_FOLLOWING_PROSE_RE = re.compile(r"[ \t]+[A-Za-z]")

_QUALIFIERS = {
    "approximately": QuantityQualifier.APPROXIMATE,
    "approximate": QuantityQualifier.APPROXIMATE,
    "approx": QuantityQualifier.APPROXIMATE,
    "approx.": QuantityQualifier.APPROXIMATE,
    "about": QuantityQualifier.APPROXIMATE,
    "around": QuantityQualifier.APPROXIMATE,
    "roughly": QuantityQualifier.APPROXIMATE,
    "at least": QuantityQualifier.MINIMUM,
    "no less than": QuantityQualifier.MINIMUM,
    "above": QuantityQualifier.MINIMUM,
    "more than": QuantityQualifier.MINIMUM,
    "at most": QuantityQualifier.MAXIMUM,
    "no more than": QuantityQualifier.MAXIMUM,
    "below": QuantityQualifier.MAXIMUM,
    "less than": QuantityQualifier.MAXIMUM,
    "up to": QuantityQualifier.MAXIMUM,
}


def _decimal(raw: str) -> Decimal:
    return Decimal(raw.replace(",", "").replace("−", "-"))


def _parse_written_number(raw: str) -> Decimal:
    words = raw.lower().replace("-", " ").split()
    total = 0
    current = 0
    for word in words:
        if word == "and":
            continue
        if word in _SMALL_NUMBERS:
            current += _SMALL_NUMBERS[word]
        elif word in _TENS:
            current += _TENS[word]
        elif word == "hundred":
            current = max(current, 1) * 100
        elif word == "thousand":
            total += max(current, 1) * 1000
            current = 0
    return Decimal(total + current)


def _qualifier(raw: str | None) -> QuantityQualifier | None:
    if raw is None:
        return None
    return _QUALIFIERS[raw.lower()]


def _is_identifier_digit(text: str, start: int, end: int) -> bool:
    """Report whether a digit run belongs to a name rather than to a measurement.

    A letter or underscore *introducing* the digits makes a name: `PA6`, `R_2`.
    A trailing unit does not: `250g` and `2.5mm` are quantities whose unit happens
    to be adjacent. Treating a trailing letter as identifier context would exclude
    the `<value><unit>` form from the numeric-provenance rule on both sides, so a
    fabricated number written that way would never be checked at all.
    """

    preceding = text[start - 1] if start else ""
    following = text[end] if end < len(text) else ""
    return preceding.isalpha() or preceding == "_" or following == "_"


def _signs_its_own_value(text: str, start: int) -> bool:
    """Report whether a leading +/- signs its number instead of separating a range.

    In `10-15` and `10 - 15` the second number is positive and the sign is the
    range separator. Only a sign that does not follow another number is a sign.
    """

    index = start - 1
    while index >= 0 and text[index] in " \t":
        index -= 1
    return index < 0 or not text[index].isdigit()


def extract_numeric_tokens(text: str) -> tuple[NumericToken, ...]:
    """Extract digit and simple English written-number tokens with raw offsets."""

    tokens: list[NumericToken] = []
    occupied: list[tuple[int, int]] = []

    for match in _DIGIT_RE.finditer(text):
        start = match.start()
        raw_text = match.group()
        if raw_text[0] in "+-−" and not _signs_its_own_value(text, start):
            start += 1
            raw_text = raw_text[1:]
        kind = (
            NumericTokenKind.IDENTIFIER
            if _is_identifier_digit(text, start, match.end())
            else NumericTokenKind.DIGIT
        )
        tokens.append(
            NumericToken(
                raw_text=raw_text,
                start=start,
                end=match.end(),
                kind=kind,
                value=_decimal(raw_text),
            )
        )
        occupied.append((start, match.end()))

    for match in _WRITTEN_RE.finditer(text):
        if any(start < match.end() and match.start() < end for start, end in occupied):
            continue
        tokens.append(
            NumericToken(
                raw_text=match.group(),
                start=match.start(),
                end=match.end(),
                kind=NumericTokenKind.WRITTEN,
                value=_parse_written_number(match.group()),
            )
        )

    return tuple(sorted(tokens, key=lambda token: (token.start, token.end)))


def _overlaps(start: int, end: int, occupied: list[tuple[int, int]]) -> bool:
    return any(other_start < end and start < other_end for other_start, other_end in occupied)


def extract_quantities(text: str) -> tuple[Quantity, ...]:
    """Extract generic quantity candidates without unit conversion."""

    quantities: list[Quantity] = []
    occupied: list[tuple[int, int]] = []

    for match in _TOLERANCE_RE.finditer(text):
        nominal_unit = match.group("nominal_unit")
        tolerance_unit = match.group("tolerance_unit")
        quantities.append(
            Quantity(
                raw_text=match.group(),
                start=match.start(),
                end=match.end(),
                kind=QuantityKind.TOLERANCE,
                values=(
                    _decimal(match.group("nominal")),
                    _decimal(match.group("tolerance")),
                ),
                unit=nominal_unit or tolerance_unit,
                upper_unit=tolerance_unit,
                currency=match.group("currency"),
                qualifier=_qualifier(match.group("qualifier")),
            )
        )
        occupied.append((match.start(), match.end()))

    for match in _RANGE_RE.finditer(text):
        if _overlaps(match.start(), match.end(), occupied):
            continue
        low_unit = match.group("low_unit")
        high_unit = match.group("high_unit")
        quantity_end = match.end()
        if (
            low_unit is None
            and high_unit is not None
            and high_unit.isalpha()
            and len(high_unit) > 3
            and not high_unit.casefold().endswith("s")
            and _FOLLOWING_PROSE_RE.match(text, match.end())
        ):
            # A long singular alphabetic token followed by more prose is more
            # likely to be the sentence predicate greedily consumed after an
            # unqualified range. Short unit symbols, plural unit words, and
            # structured unit expressions remain part of the quantity.
            high_unit = None
            quantity_end = match.start("high_unit")
            while text[quantity_end - 1] in " \t":
                quantity_end -= 1
        currency = match.group("currency") or match.group("upper_currency")
        quantities.append(
            Quantity(
                raw_text=text[match.start() : quantity_end],
                start=match.start(),
                end=quantity_end,
                kind=QuantityKind.RANGE,
                values=(_decimal(match.group("low")), _decimal(match.group("high"))),
                unit=low_unit,
                upper_unit=high_unit,
                currency=currency,
                qualifier=_qualifier(match.group("qualifier")),
            )
        )
        occupied.append((match.start(), quantity_end))

    for match in _SCALAR_RE.finditer(text):
        if _overlaps(match.start(), match.end(), occupied):
            continue
        if _is_identifier_digit(text, match.start("number"), match.end("number")):
            continue

        percent = match.group("percent")
        currency = match.group("currency")
        per_unit = match.group("per_unit")
        unit = percent or match.group("unit") or per_unit
        if currency and per_unit:
            unit = f"{currency}{per_unit}"

        if currency:
            kind = QuantityKind.CURRENCY
        elif percent:
            kind = QuantityKind.PERCENTAGE
        else:
            kind = QuantityKind.SCALAR

        quantities.append(
            Quantity(
                raw_text=match.group(),
                start=match.start(),
                end=match.end(),
                kind=kind,
                values=(_decimal(match.group("number")),),
                unit=unit,
                currency=currency,
                qualifier=_qualifier(match.group("qualifier")),
            )
        )
        occupied.append((match.start(), match.end()))

    for token in extract_numeric_tokens(text):
        if token.kind is not NumericTokenKind.WRITTEN:
            continue
        end = token.end
        unit: str | None = None
        following = _FOLLOWING_UNIT_RE.match(text, end)
        if following:
            unit = following.group("unit")
            end = following.end()
        if _overlaps(token.start, end, occupied):
            continue
        quantities.append(
            Quantity(
                raw_text=text[token.start:end],
                start=token.start,
                end=end,
                kind=QuantityKind.WRITTEN,
                values=(token.value,),
                unit=unit,
            )
        )
        occupied.append((token.start, end))

    return tuple(sorted(quantities, key=lambda quantity: (quantity.start, quantity.end)))
