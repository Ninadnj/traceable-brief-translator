"""Exact citation matching across harmless document-format differences."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata


_QUOTE_EQUIVALENTS = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u2032": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u201f": '"',
        "\u2033": '"',
    }
)
_HYPHEN_EQUIVALENTS = str.maketrans(
    {
        "\u00ad": "-",
        "\u058a": "-",
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2015": "-",
        "\u2212": "-",
    }
)
_SOFT_HYPHEN = "\u00ad"

# Characters a typesetter actually breaks a word on, so the hyphen itself may or
# may not be part of the word. Other dashes never split a word: an em dash at a
# line end is content, and reading it as optional would invent ambiguity.
_BREAKING_HYPHENS = "-\u00ad\u2010\u2011"
_UNBREAKING_DASHES = "\u058a\u2012\u2013\u2014\u2015\u2212"
_DASH_CLASS = re.escape(_BREAKING_HYPHENS + _UNBREAKING_DASHES)
_LETTER = r"[^\W\d_]"
_COMBINING = r"[\u0300-\u036f]"
# A dash between two letters immediately followed by a single line break: the
# break is always layout and always disappears. Requiring a letter on both sides
# keeps list bullets, numeric ranges and dashes before a blank line out of it.
# The lookbehind also accepts a combining mark so a decomposed accent before the
# dash still counts as a letter.
_LINE_BREAK_DASH = re.compile(
    rf"(?:(?<={_LETTER})|(?<={_COMBINING}))"
    rf"(?P<dash>[{_DASH_CLASS}])"
    rf"[^\S\r\n]*(?:\r\n|\r|\n)[^\S\r\n]*"
    rf"(?={_LETTER})"
)


@dataclass(frozen=True)
class NormalizedText:
    text: str
    original_starts: tuple[int, ...]
    original_ends: tuple[int, ...]
    # Normalized indices holding a hyphen the source may or may not intend. The
    # quote decides each one independently; see `_match_length`.
    optional_hyphens: frozenset[int] = frozenset()


@dataclass(frozen=True)
class ExactMatch:
    start: int
    end: int
    source_quote: str


def _optional_hyphen_sites(value: str) -> tuple[frozenset[int], frozenset[int]]:
    """Locate hyphens the source may or may not intend, and the breaks to drop.

    A soft hyphen, and a hyphen sitting at a line break, are both *optional*: the
    source may be a compound split by justification ("low-gloss") or one word
    broken mid-syllable ("scheduling"), and nothing in the text separates them.
    The hyphen is kept in the normalized form and recorded here so the quote can
    decide each site independently; the line break itself always disappears.
    """

    optional = {
        index for index, character in enumerate(value) if character == _SOFT_HYPHEN
    }
    dropped: set[int] = set()
    for match in _LINE_BREAK_DASH.finditer(value):
        dropped.update(range(match.start() + 1, match.end()))
        if match.group("dash") in _BREAKING_HYPHENS:
            optional.add(match.start())
    return frozenset(optional), frozenset(dropped)


def normalize_with_mapping(value: str) -> NormalizedText:
    """Normalize layout characters while retaining a raw-source index map.

    This is deliberately narrower than fuzzy matching: wording, order, case and
    punctuation still have to agree. Only Unicode compatibility forms, quote and
    hyphen variants, whitespace layout, and hyphenation introduced by a line
    break are treated as equivalent.
    """

    optional_raw, dropped = _optional_hyphen_sites(value)
    optional: set[int] = set()
    characters: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    index = 0
    while index < len(value):
        cluster_start = index
        index += 1
        while index < len(value) and unicodedata.combining(value[index]):
            index += 1
        cluster_end = index
        if cluster_start in dropped:
            continue
        if cluster_start in optional_raw:
            optional.add(len(characters))
            characters.append("-")
            starts.append(cluster_start)
            ends.append(cluster_end)
            continue
        normalized = unicodedata.normalize(
            "NFKC", value[cluster_start:cluster_end]
        )
        normalized = normalized.translate(_QUOTE_EQUIVALENTS)
        normalized = normalized.translate(_HYPHEN_EQUIVALENTS)
        for character in normalized:
            if character.isspace():
                if characters and characters[-1] == " ":
                    ends[-1] = cluster_end
                    continue
                character = " "
            characters.append(character)
            starts.append(cluster_start)
            ends.append(cluster_end)
    return NormalizedText(
        text="".join(characters),
        original_starts=tuple(starts),
        original_ends=tuple(ends),
        optional_hyphens=frozenset(optional),
    )


def normalized_exact_matches(source_text: str, quote: str) -> tuple[ExactMatch, ...]:
    """Find every occurrence of the quote after bounded formatting normalization.

    Each optional hyphen is resolved by the quote itself rather than by a global
    guess, so one scan finds occurrences under any mixture of readings. That
    matters for ambiguity: counting the two readings separately would let a quote
    that appears once broken and once intact be certified unique.
    """

    normalized_quote = normalize_with_mapping(quote).text.strip()
    if not normalized_quote:
        return ()
    source = normalize_with_mapping(source_text)

    matches: list[ExactMatch] = []
    start = -1
    while True:
        # A match can never open by skipping a hyphen, so its first character
        # must appear literally. Seeking that keeps the scan near-linear instead
        # of trying every offset on long or repetitive sources.
        start = source.text.find(normalized_quote[0], start + 1)
        if start < 0:
            break
        end = _match_length(source, normalized_quote, start)
        if end is None:
            continue
        raw_start = source.original_starts[start]
        raw_end = source.original_ends[end - 1]
        matches.append(
            ExactMatch(
                start=raw_start,
                end=raw_end,
                source_quote=source_text[raw_start:raw_end],
            )
        )
    return tuple(matches)


def _match_length(
    source: NormalizedText,
    normalized_quote: str,
    start: int,
) -> int | None:
    """Return the normalized end offset of the quote at `start`, or None."""

    text = source.text
    optional = source.optional_hyphens
    # Sites where the quote's own hyphen was consumed but skipping is still a
    # possible reading. Only revisited on failure, so the literal reading wins.
    alternatives: list[tuple[int, int]] = []
    source_index = start
    quote_index = 0
    while True:
        if quote_index == len(normalized_quote):
            return source_index
        skippable = (
            source_index < len(text)
            and source_index in optional
            # Never opening with a skip keeps a single occurrence from being
            # reported twice, once with the hyphen and once without.
            and source_index > start
        )
        if source_index < len(text) and text[source_index] == normalized_quote[quote_index]:
            if skippable:
                alternatives.append((source_index, quote_index))
            source_index += 1
            quote_index += 1
        elif skippable:
            source_index += 1
        elif alternatives:
            # A consumed hyphen stranded the rest of the quote; read that site
            # as a break instead. Needed when an optional hyphen abuts a real one.
            source_index, quote_index = alternatives.pop()
            source_index += 1
        else:
            return None
