from __future__ import annotations

import json
import unittest

from local_babeldoc_server.translation_batching import build_translation_schema
from local_babeldoc_server.translation_batching import rewrite_batch_response_for_babeldoc
from local_babeldoc_server.translation_batching import rewrite_babeldoc_batch_prompt
from local_babeldoc_server.translation_batching import partition_batch_indices
from local_babeldoc_server.translation_batching import validate_translation_set


EXPECTED = ("part-001/page-003/paragraph-007", "part-001/page-003/paragraph-008")
ALIASES = ("0", "1")


class TranslationBatchingTest(unittest.TestCase):
    def test_partition_targets_1000_tokens_without_old_five_paragraph_cutoff(self) -> None:
        batches = partition_batch_indices([90] * 17)

        self.assertEqual(batches, ((0, 12), (12, 17)))
        self.assertGreater(batches[0][1] - batches[0][0], 5)

    def test_partition_never_exceeds_1500_tokens_or_16_paragraphs(self) -> None:
        token_counts = [800, 600, 200] + [70] * 20

        batches = partition_batch_indices(token_counts)

        for start, end in batches:
            self.assertLessEqual(sum(token_counts[start:end]), 1500)
            self.assertLessEqual(end - start, 16)

    def test_schema_has_exact_ids_count_and_closed_objects(self) -> None:
        schema = build_translation_schema(ALIASES)

        translations = schema["properties"]["t"]
        item = translations["items"]
        self.assertEqual(translations["minItems"], 2)
        self.assertEqual(translations["maxItems"], 2)
        self.assertEqual(item["properties"]["i"]["enum"], list(ALIASES))
        self.assertEqual(item["required"], ["i", "t"])
        self.assertFalse(item["additionalProperties"])
        self.assertFalse(schema["additionalProperties"])

    def test_validation_accepts_out_of_order_complete_ids_and_reorders(self) -> None:
        payload = json.dumps(
            {
                "t": [
                    {"i": ALIASES[1], "t": "第二段"},
                    {"i": ALIASES[0], "t": "第一段"},
                ]
            }
        )

        result = validate_translation_set(payload, ALIASES)

        self.assertIsNone(result.parse_error)
        self.assertEqual(list(result.valid_translations), list(ALIASES))
        self.assertEqual(result.missing_ids, ())
        self.assertEqual(result.unknown_ids, ())
        self.assertEqual(result.duplicate_ids, ())

    def test_validation_isolates_missing_unknown_duplicate_and_malformed(self) -> None:
        mixed = validate_translation_set(
            json.dumps(
                {
                    "t": [
                        {"i": ALIASES[0], "t": "first-a"},
                        {"i": ALIASES[0], "t": "first-b"},
                        {"i": "unknown", "t": "unknown"},
                    ]
                }
            ),
            ALIASES,
        )
        malformed = validate_translation_set("not-json", ALIASES)

        self.assertEqual(mixed.missing_ids, (ALIASES[1],))
        self.assertEqual(mixed.unknown_ids, ("unknown",))
        self.assertEqual(mixed.duplicate_ids, (ALIASES[0],))
        self.assertEqual(mixed.valid_translations, {})
        self.assertEqual(mixed.fallback_ids, ALIASES)
        self.assertIsNotNone(malformed.parse_error)
        self.assertEqual(malformed.fallback_ids, ALIASES)

    def test_prompt_rewrite_uses_short_aliases_without_changing_instruction_prefix(self) -> None:
        upstream = (
            "translation rules and glossary\n\n## Here is the input:\n\n"
            + json.dumps(
                [
                    {"id": 0, "input": "<style id='1'>First</style>"},
                    {"id": 1, "input": "Second {v1}"},
                ]
            )
        )

        rewritten = rewrite_babeldoc_batch_prompt(upstream, EXPECTED)
        self.assertIn("BATCH_INPUT_JSON:\n", rewritten.prompt)
        payload = json.loads(rewritten.prompt.rsplit("BATCH_INPUT_JSON:\n", 1)[1])
        serialized_contract = json.dumps(
            {
                "prompt": rewritten.prompt,
                "response_format": rewritten.response_format,
            },
            ensure_ascii=False,
        )

        self.assertEqual(
            rewritten.instruction_prefix,
            "translation rules and glossary",
        )
        self.assertEqual(
            payload,
            {
                "p": [
                    {"i": "0", "s": "<style id='1'>First</style>"},
                    {"i": "1", "s": "Second {v1}"},
                ]
            },
        )
        self.assertNotIn(EXPECTED[0], serialized_contract)
        self.assertNotIn(EXPECTED[1], serialized_contract)
        self.assertEqual(
            rewritten.stable_id_by_alias,
            {"0": EXPECTED[0], "1": EXPECTED[1]},
        )
        self.assertEqual(
            rewritten.source_by_stable_id[EXPECTED[1]],
            "Second {v1}",
        )
        self.assertEqual(
            rewritten.response_format["mime_type"],
            "application/json",
        )

    def test_response_rewrite_keeps_valid_items_and_blanks_only_failed_ids(self) -> None:
        output = json.dumps(
            {
                "t": [
                    {"i": ALIASES[0], "t": "第一段"},
                    {"i": "unknown", "t": "discard"},
                ]
            }
        )

        upstream_json, validation = rewrite_batch_response_for_babeldoc(
            output,
            ALIASES,
        )
        upstream = json.loads(upstream_json)

        self.assertEqual(
            upstream,
            [
                {"id": 0, "output": "第一段"},
                {"id": 1, "output": ""},
            ],
        )
        self.assertEqual(validation.missing_ids, (ALIASES[1],))
        self.assertEqual(validation.unknown_ids, ("unknown",))

    def test_compact_protocol_cuts_legacy_overhead_by_half(self) -> None:
        stable_ids = tuple(
            f"part-002/page-041/table-000/block-{index:03d}"
            for index in range(32)
        )
        sources = tuple(f"source paragraph {index}" for index in range(32))
        upstream = (
            "translation rules and glossary\n\n## Here is the input:\n\n"
            + json.dumps(
                [
                    {"id": index, "input": source}
                    for index, source in enumerate(sources)
                ]
            )
        )
        rewritten = rewrite_babeldoc_batch_prompt(upstream, stable_ids)
        compact_protocol_bytes = len(
            (
                rewritten.prompt
                + json.dumps(rewritten.response_format, ensure_ascii=False)
            ).encode("utf-8")
        )
        legacy_protocol_bytes = len(
            (
                json.dumps(
                    {
                        "paragraphs": [
                            {"id": stable_id, "text": source}
                            for stable_id, source in zip(
                                stable_ids,
                                sources,
                                strict=True,
                            )
                        ]
                    },
                    ensure_ascii=False,
                )
                + json.dumps(
                    {
                        "id_enum": list(stable_ids),
                        "fields": ["id", "translation"],
                    },
                    ensure_ascii=False,
                )
            ).encode("utf-8")
        )

        self.assertLessEqual(
            compact_protocol_bytes,
            legacy_protocol_bytes * 0.5,
        )


if __name__ == "__main__":
    unittest.main()
