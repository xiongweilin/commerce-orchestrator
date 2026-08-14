import unicodedata
from dataclasses import dataclass

from .schemas import EvidenceQuote, LocatedEvidence


@dataclass(frozen=True)
class EvidenceLocationResult:
    located: list[LocatedEvidence]
    failures: list[str]


def _normalized_with_map(value: str) -> tuple[str, list[int]]:
    normalized: list[str] = []
    source_indexes: list[int] = []
    previous_was_space = False
    for source_index, source_char in enumerate(value):
        expanded = unicodedata.normalize("NFKC", source_char)
        for char in expanded:
            if char.isspace():
                if previous_was_space:
                    continue
                normalized.append(" ")
                source_indexes.append(source_index)
                previous_was_space = True
            else:
                normalized.append(char.casefold())
                source_indexes.append(source_index)
                previous_was_space = False
    return "".join(normalized), source_indexes


def _all_occurrences(haystack: str, needle: str) -> list[int]:
    starts: list[int] = []
    cursor = 0
    while needle and (position := haystack.find(needle, cursor)) >= 0:
        starts.append(position)
        cursor = position + 1
    return starts


def locate_quote(message: str, quote: str) -> LocatedEvidence | None:
    exact_positions = _all_occurrences(message, quote)
    if exact_positions:
        start = exact_positions[0]
        return LocatedEvidence(
            quote=quote,
            start=start,
            end=start + len(quote),
            match_method="exact",
            match_count=len(exact_positions),
        )

    normalized_message, message_map = _normalized_with_map(message)
    normalized_quote, _ = _normalized_with_map(quote)
    normalized_positions = _all_occurrences(normalized_message, normalized_quote)
    if not normalized_positions or not normalized_quote:
        return None
    normalized_start = normalized_positions[0]
    normalized_end = normalized_start + len(normalized_quote) - 1
    start = message_map[normalized_start]
    end = message_map[normalized_end] + 1
    return LocatedEvidence(
        quote=message[start:end],
        start=start,
        end=end,
        match_method="normalized",
        match_count=len(normalized_positions),
    )


def locate_evidence(message: str, evidence: list[EvidenceQuote]) -> EvidenceLocationResult:
    located: list[LocatedEvidence] = []
    failures: list[str] = []
    for index, item in enumerate(evidence):
        result = locate_quote(message, item.quote)
        if result is None:
            failures.append(f"evidence_not_found:{index}")
        else:
            located.append(result)
    return EvidenceLocationResult(located=located, failures=failures)
