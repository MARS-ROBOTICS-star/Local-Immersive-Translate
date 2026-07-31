from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher


_PLACEHOLDER_RE = re.compile(r"\{\s*v\s*\d+\s*}", re.IGNORECASE)
_STYLE_TAG_RE = re.compile(r"</?style\b[^>]*>", re.IGNORECASE)
_URL_RE = re.compile(r"https?://[^\s<>\"'，。]+", re.IGNORECASE)
_DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
_CITATION_RE = re.compile(r"\[\s*\d+(?:\s*[-–—]\s*\d+)?\s*]")
_NUMBER_RE = re.compile(r"(?<![A-Za-z])\d+(?:\.\d+)?%?")
_SENTENCE_BREAK_RE = re.compile(r"(?<=[.!?。！？])\s+")
_TERMINAL_PUNCTUATION_RE = re.compile(r"[.!?。！？][\"'’”）)】\]]*$")


@dataclass(frozen=True, slots=True)
class ValidationResult:
    accepted: bool
    reasons: tuple[str, ...] = ()


def _protected_tokens(text: str) -> Counter[str]:
    tokens: list[str] = []
    for pattern in (
        _PLACEHOLDER_RE,
        _STYLE_TAG_RE,
        _URL_RE,
        _DOI_RE,
        _CITATION_RE,
        _NUMBER_RE,
    ):
        tokens.extend(match.group(0) for match in pattern.finditer(text))
    return Counter(tokens)


def _without_trailing_style_tags(text: str) -> str:
    stripped = text.rstrip()
    while True:
        updated = re.sub(r"</style>\s*$", "", stripped, flags=re.IGNORECASE)
        if updated == stripped:
            return stripped
        stripped = updated.rstrip()


def validate_translation(
    source: str,
    target: str,
    target_language: str,
) -> ValidationResult:
    source_text = (source or "").strip()
    target_text = (target or "").strip()
    if not source_text:
        return ValidationResult(True)

    reasons: list[str] = []
    if not target_text:
        return ValidationResult(False, ("empty_target",))

    if _protected_tokens(source_text) != _protected_tokens(target_text):
        reasons.append("protected_token_mismatch")

    normalized_source = " ".join(source_text.split())
    normalized_target = " ".join(target_text.split())
    ascii_letters = len(re.findall(r"[A-Za-z]", source_text))
    if target_language.lower().startswith("zh") and ascii_letters >= 40:
        similarity = SequenceMatcher(
            None,
            normalized_source.casefold(),
            normalized_target.casefold(),
        ).ratio()
        if similarity >= 0.96:
            reasons.append("unchanged_source")

    if len(source_text) >= 80:
        length_ratio = len(target_text) / len(source_text)
        if length_ratio < 0.08 or length_ratio > 4.5:
            reasons.append("implausible_length")

        source_end = _without_trailing_style_tags(source_text)
        target_end = _without_trailing_style_tags(target_text)
        if _TERMINAL_PUNCTUATION_RE.search(
            source_end
        ) and not _TERMINAL_PUNCTUATION_RE.search(target_end):
            reasons.append("truncated_target")

    return ValidationResult(not reasons, tuple(dict.fromkeys(reasons)))


def _hard_split(text: str, max_chars: int) -> list[str]:
    chunks: list[str] = []
    remainder = text.strip()
    while len(remainder) > max_chars:
        split_at = remainder.rfind(" ", 0, max_chars + 1)
        if split_at <= 0:
            split_at = max_chars
        chunks.append(remainder[:split_at].strip())
        remainder = remainder[split_at:].strip()
    if remainder:
        chunks.append(remainder)
    return chunks


def split_translation_chunks(text: str, max_chars: int = 700) -> list[str]:
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    source = (text or "").strip()
    if not source:
        return []

    sentences = [part.strip() for part in _SENTENCE_BREAK_RE.split(source) if part.strip()]
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_hard_split(sentence, max_chars))
            continue
        candidate = sentence if not current else f"{current} {sentence}"
        if len(candidate) <= max_chars:
            current = candidate
        else:
            chunks.append(current)
            current = sentence
    if current:
        chunks.append(current)
    return chunks
