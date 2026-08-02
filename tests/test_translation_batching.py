from __future__ import annotations

import json
import unittest

from local_babeldoc_server.translation_batching import build_translation_schema
from local_babeldoc_server.translation_batching import rewrite_batch_response_for_babeldoc
from local_babeldoc_server.translation_batching import rewrite_babeldoc_batch_prompt
from local_babeldoc_server.translation_batching import validate_translation_set


EXPECTED = ("part-001/page-003/paragraph-007", "part-001/page-003/paragraph-008")


class TranslationBatchingTest(unittest.TestCase):
    def test_schema_has_exact_ids_count_and_closed_objects(self) -> None:
        schema = build_translation_schema(EXPECTED)

        translations = schema["properties"]["translations"]
        item = translations["items"]
        self.assertEqual(translations["minItems"], 2)
        self.assertEqual(translations["maxItems"], 2)
        self.assertEqual(item["properties"]["id"]["enum"], list(EXPECTED))
        self.assertEqual(item["required"], ["id", "translation"])
        self.assertFalse(item["additionalProperties"])
        self.assertFalse(schema["additionalProperties"])

    def test_validation_accepts_out_of_order_complete_ids_and_reorders(self) -> None:
        payload = json.dumps(
            {
                "translations": [
                    {"id": EXPECTED[1], "translation": "第二段"},
                    {"id": EXPECTED[0], "translation": "第一段"},
                ]
            }
        )

        result = validate_translation_set(payload, EXPECTED)

        self.assertIsNone(result.parse_error)
        self.assertEqual(list(result.valid_translations), list(EXPECTED))
        self.assertEqual(result.missing_ids, ())
        self.assertEqual(result.unknown_ids, ())
        self.assertEqual(result.duplicate_ids, ())

    def test_validation_isolates_missing_unknown_duplicate_and_malformed(self) -> None:
        mixed = validate_translation_set(
            json.dumps(
                {
                    "translations": [
                        {"id": EXPECTED[0], "translation": "first-a"},
                        {"id": EXPECTED[0], "translation": "first-b"},
                        {"id": "unknown", "translation": "unknown"},
                    ]
                }
            ),
            EXPECTED,
        )
        malformed = validate_translation_set("not-json", EXPECTED)

        self.assertEqual(mixed.missing_ids, (EXPECTED[1],))
        self.assertEqual(mixed.unknown_ids, ("unknown",))
        self.assertEqual(mixed.duplicate_ids, (EXPECTED[0],))
        self.assertEqual(mixed.valid_translations, {})
        self.assertEqual(mixed.fallback_ids, EXPECTED)
        self.assertIsNotNone(malformed.parse_error)
        self.assertEqual(malformed.fallback_ids, EXPECTED)

    def test_prompt_rewrite_uses_stable_ids_and_preserves_preprocessed_text(self) -> None:
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
        payload = json.loads(rewritten.prompt.rsplit("STABLE_INPUT_JSON:\n", 1)[1])

        self.assertIn("translation rules and glossary", rewritten.prompt)
        self.assertEqual(payload["paragraphs"][0]["id"], EXPECTED[0])
        self.assertEqual(
            payload["paragraphs"][0]["text"],
            "<style id='1'>First</style>",
        )
        self.assertEqual(rewritten.source_by_id[EXPECTED[1]], "Second {v1}")
        self.assertEqual(
            rewritten.response_format["mime_type"],
            "application/json",
        )

    def test_response_rewrite_keeps_valid_items_and_blanks_only_failed_ids(self) -> None:
        output = json.dumps(
            {
                "translations": [
                    {"id": EXPECTED[0], "translation": "第一段"},
                    {"id": "unknown", "translation": "discard"},
                ]
            }
        )

        upstream_json, validation = rewrite_batch_response_for_babeldoc(
            output,
            EXPECTED,
        )
        upstream = json.loads(upstream_json)

        self.assertEqual(
            upstream,
            [
                {"id": 0, "output": "第一段"},
                {"id": 1, "output": ""},
            ],
        )
        self.assertEqual(validation.missing_ids, (EXPECTED[1],))
        self.assertEqual(validation.unknown_ids, ("unknown",))


if __name__ == "__main__":
    unittest.main()
