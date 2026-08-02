from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from typing import Any
from typing import Mapping

from local_babeldoc_server.translation_quality import validate_translation


class BatchPromptError(RuntimeError):
    pass


class OversizedParagraphError(ValueError):
    pass


def partition_batch_indices(
    token_counts: list[int] | tuple[int, ...],
    *,
    target_tokens: int = 2400,
    max_tokens: int = 3200,
    max_paragraphs: int = 40,
) -> tuple[tuple[int, int], ...]:
    if target_tokens < 1 or max_tokens < target_tokens:
        raise ValueError("batch token limits are invalid")
    if max_paragraphs < 1:
        raise ValueError("max_paragraphs must be positive")
    batches: list[tuple[int, int]] = []
    start = 0
    total = 0
    count = 0
    for index, raw_tokens in enumerate(token_counts):
        tokens = max(0, int(raw_tokens))
        if tokens > max_tokens:
            raise OversizedParagraphError(
                f"paragraph {index} exceeds {max_tokens} source tokens"
            )
        if count and (
            total + tokens > max_tokens or count >= max_paragraphs
        ):
            batches.append((start, index))
            start = index
            total = 0
            count = 0
        total += tokens
        count += 1
        if total >= target_tokens or count >= max_paragraphs:
            batches.append((start, index + 1))
            start = index + 1
            total = 0
            count = 0
    if count:
        batches.append((start, len(token_counts)))
    return tuple(batches)


@dataclass(frozen=True, slots=True)
class BatchValidation:
    expected_ids: tuple[str, ...]
    valid_translations: Mapping[str, str]
    missing_ids: tuple[str, ...]
    unknown_ids: tuple[str, ...]
    duplicate_ids: tuple[str, ...]
    invalid_ids: tuple[str, ...]
    parse_error: str | None = None

    @property
    def fallback_ids(self) -> tuple[str, ...]:
        if self.parse_error is not None:
            return self.expected_ids
        failed = set(self.missing_ids)
        failed.update(self.duplicate_ids)
        failed.update(self.invalid_ids)
        return tuple(item for item in self.expected_ids if item in failed)


@dataclass(frozen=True, slots=True)
class RewrittenBatchPrompt:
    prompt: str
    instruction_prefix: str
    expected_stable_ids: tuple[str, ...]
    stable_id_by_alias: Mapping[str, str]
    alias_by_stable_id: Mapping[str, str]
    source_by_stable_id: Mapping[str, str]
    response_format: dict[str, Any]

    @property
    def expected_ids(self) -> tuple[str, ...]:
        return tuple(self.stable_id_by_alias)


@dataclass(frozen=True, slots=True)
class StableBatchValidation:
    valid_translations: Mapping[str, str]
    recovery_ids: tuple[str, ...]
    reason_by_stable_id: Mapping[str, tuple[str, ...]]
    parse_error: str | None = None


def build_translation_schema(expected_ids: tuple[str, ...]) -> dict[str, Any]:
    count = len(expected_ids)
    return {
        "type": "object",
        "properties": {
            "t": {
                "type": "array",
                "minItems": count,
                "maxItems": count,
                "items": {
                    "type": "object",
                    "properties": {
                        "i": {
                            "type": "string",
                            "enum": list(expected_ids),
                        },
                        "t": {"type": "string"},
                    },
                    "required": ["i", "t"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["t"],
        "additionalProperties": False,
    }


def validate_translation_set(
    payload: str,
    expected_ids: tuple[str, ...],
) -> BatchValidation:
    try:
        parsed = json.loads(payload)
    except (TypeError, json.JSONDecodeError) as exc:
        return BatchValidation(
            expected_ids=expected_ids,
            valid_translations={},
            missing_ids=expected_ids,
            unknown_ids=(),
            duplicate_ids=(),
            invalid_ids=(),
            parse_error=str(exc),
        )
    if not isinstance(parsed, dict) or not isinstance(
        parsed.get("t"), list
    ):
        return BatchValidation(
            expected_ids=expected_ids,
            valid_translations={},
            missing_ids=expected_ids,
            unknown_ids=(),
            duplicate_ids=(),
            invalid_ids=(),
            parse_error="root must contain a t array",
        )

    expected_set = set(expected_ids)
    rows: list[tuple[str, Any]] = []
    for item in parsed["t"]:
        if not isinstance(item, dict) or "i" not in item:
            continue
        rows.append((str(item["i"]), item.get("t")))
    counts = Counter(item_id for item_id, _value in rows)
    returned = set(counts)
    missing_ids = tuple(item for item in expected_ids if item not in returned)
    unknown_ids = tuple(
        dict.fromkeys(item_id for item_id, _value in rows if item_id not in expected_set)
    )
    duplicate_ids = tuple(
        item for item in expected_ids if counts.get(item, 0) > 1
    )
    invalid = {
        item_id
        for item_id, value in rows
        if item_id in expected_set
        and (not isinstance(value, str) or not value.strip())
    }
    invalid_ids = tuple(item for item in expected_ids if item in invalid)
    invalid_set = set(duplicate_ids) | invalid
    values = {item_id: value for item_id, value in rows}
    valid_translations = {
        item_id: values[item_id]
        for item_id in expected_ids
        if item_id in values and item_id not in invalid_set
    }
    return BatchValidation(
        expected_ids=expected_ids,
        valid_translations=valid_translations,
        missing_ids=missing_ids,
        unknown_ids=unknown_ids,
        duplicate_ids=duplicate_ids,
        invalid_ids=invalid_ids,
        parse_error=None,
    )


def rewrite_babeldoc_batch_prompt(
    prompt: str,
    stable_ids: tuple[str, ...],
) -> RewrittenBatchPrompt:
    marker = "## Here is the input:"
    marker_index = prompt.rfind(marker)
    if marker_index < 0:
        raise BatchPromptError("BabelDOC batch prompt has no input marker")
    json_start = marker_index + len(marker)
    encoded_input = prompt[json_start:].lstrip()
    try:
        upstream_input, _end = json.JSONDecoder().raw_decode(encoded_input)
    except json.JSONDecodeError as exc:
        raise BatchPromptError("BabelDOC batch input is not valid JSON") from exc
    if not isinstance(upstream_input, list):
        raise BatchPromptError("BabelDOC batch input must be an array")
    if len(upstream_input) != len(stable_ids):
        raise BatchPromptError(
            "stable ID count does not match BabelDOC batch input"
        )
    source_by_stable_id: dict[str, str] = {}
    for stable_id, item in zip(stable_ids, upstream_input, strict=True):
        if not isinstance(item, dict) or not isinstance(item.get("input"), str):
            raise BatchPromptError("BabelDOC batch item has no string input")
        source_by_stable_id[stable_id] = item["input"]
    prefix = prompt[:marker_index].rstrip()
    return _build_batch_prompt(prefix, stable_ids, source_by_stable_id)


def _build_batch_prompt(
    instruction_prefix: str,
    stable_ids: tuple[str, ...],
    source_by_stable_id: Mapping[str, str],
) -> RewrittenBatchPrompt:
    paragraphs: list[dict[str, str]] = []
    stable_id_by_alias: dict[str, str] = {}
    alias_by_stable_id: dict[str, str] = {}
    selected_sources: dict[str, str] = {}
    for index, stable_id in enumerate(stable_ids):
        source = source_by_stable_id[stable_id]
        alias = str(index)
        paragraphs.append({"i": alias, "s": source})
        selected_sources[stable_id] = source
        stable_id_by_alias[alias] = stable_id
        alias_by_stable_id[stable_id] = alias
    compact_payload = {"p": paragraphs}
    rewritten_prompt = (
        instruction_prefix
        + "\n\n## Structured output override\n"
        + "Return a JSON object with a t array. Each item must contain "
        + "exactly i and t. Use every short i exactly once. "
        + "This final contract overrides any earlier array example.\n\n"
        + "BATCH_INPUT_JSON:\n"
        + json.dumps(compact_payload, ensure_ascii=False, separators=(",", ":"))
    )
    schema = build_translation_schema(tuple(stable_id_by_alias))
    return RewrittenBatchPrompt(
        prompt=rewritten_prompt,
        instruction_prefix=instruction_prefix,
        expected_stable_ids=stable_ids,
        stable_id_by_alias=stable_id_by_alias,
        alias_by_stable_id=alias_by_stable_id,
        source_by_stable_id=selected_sources,
        response_format={
            "type": "text",
            "mime_type": "application/json",
            "schema": schema,
        },
    )


def validate_rewritten_batch_response(
    output: str,
    rewritten: RewrittenBatchPrompt,
    target_language: str,
) -> StableBatchValidation:
    structural = validate_translation_set(output, rewritten.expected_ids)
    valid_translations: dict[str, str] = {}
    reason_by_stable_id: dict[str, tuple[str, ...]] = {}
    recovery = set()
    for alias in structural.fallback_ids:
        stable_id = rewritten.stable_id_by_alias[alias]
        recovery.add(stable_id)
        reasons = []
        if alias in structural.missing_ids:
            reasons.append("missing_id")
        if alias in structural.duplicate_ids:
            reasons.append("duplicate_id")
        if alias in structural.invalid_ids:
            reasons.append("invalid_text")
        if structural.parse_error is not None:
            reasons.append("parse_error")
        reason_by_stable_id[stable_id] = tuple(reasons)
    for alias, translation in structural.valid_translations.items():
        stable_id = rewritten.stable_id_by_alias[alias]
        quality = validate_translation(
            rewritten.source_by_stable_id[stable_id],
            translation,
            target_language,
        )
        if quality.accepted:
            valid_translations[stable_id] = translation
            continue
        recovery.add(stable_id)
        reason_by_stable_id[stable_id] = quality.reasons
    return StableBatchValidation(
        valid_translations=valid_translations,
        recovery_ids=tuple(
            stable_id
            for stable_id in rewritten.expected_stable_ids
            if stable_id in recovery
        ),
        reason_by_stable_id=reason_by_stable_id,
        parse_error=structural.parse_error,
    )


def build_recovery_batches(
    rewritten: RewrittenBatchPrompt,
    recovery_ids: tuple[str, ...],
    *,
    split_full_batch: bool,
) -> tuple[RewrittenBatchPrompt, ...]:
    if not recovery_ids:
        return ()
    max_paragraphs = 40
    if split_full_batch and len(recovery_ids) > 1:
        max_paragraphs = max(1, (len(recovery_ids) + 1) // 2)
    token_counts = [
        max(
            1,
            (len(rewritten.source_by_stable_id[stable_id].encode("utf-8")) + 3)
            // 4,
        )
        for stable_id in recovery_ids
    ]
    groups = partition_batch_indices(
        token_counts,
        target_tokens=2400,
        max_tokens=3200,
        max_paragraphs=max_paragraphs,
    )
    return tuple(
        _build_batch_prompt(
            rewritten.instruction_prefix,
            recovery_ids[start:end],
            rewritten.source_by_stable_id,
        )
        for start, end in groups
    )


def rewritten_batch_response_for_babeldoc(
    rewritten: RewrittenBatchPrompt,
    translations_by_stable_id: Mapping[str, str],
    source_preserved_ids: tuple[str, ...] = (),
) -> str:
    preserved = set(source_preserved_ids)
    upstream = []
    for index, stable_id in enumerate(rewritten.expected_stable_ids):
        output = translations_by_stable_id.get(stable_id, "")
        if stable_id in preserved:
            output = rewritten.source_by_stable_id[stable_id]
        upstream.append({"id": index, "output": output})
    return json.dumps(upstream, ensure_ascii=False)


def rewrite_batch_response_for_babeldoc(
    output: str,
    expected_ids: tuple[str, ...],
) -> tuple[str, BatchValidation]:
    validation = validate_translation_set(output, expected_ids)
    upstream = [
        {
            "id": index,
            "output": validation.valid_translations.get(stable_id, ""),
        }
        for index, stable_id in enumerate(expected_ids)
    ]
    return json.dumps(upstream, ensure_ascii=False), validation
