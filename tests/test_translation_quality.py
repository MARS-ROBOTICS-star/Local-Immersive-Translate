import unittest

from local_babeldoc_server.translation_quality import split_translation_chunks
from local_babeldoc_server.translation_quality import validate_translation


class TranslationValidationTest(unittest.TestCase):
    def test_rejects_empty_target_for_nonempty_source(self) -> None:
        for target in ("", "  \n\t"):
            with self.subTest(target=repr(target)):
                result = validate_translation(
                    "Mobile robots navigate autonomously.", target, "zh"
                )
                self.assertFalse(result.accepted)
                self.assertIn("empty_target", result.reasons)

    def test_rejects_missing_structural_and_protected_tokens(self) -> None:
        source = (
            "<style id='1'>Method</style> keeps {v1}, citation [15]–[17], "
            "DOI 10.1109/ACCESS.2020.2975643 and https://example.org/a at 25.4%."
        )
        target = "该方法保留引用，但删除了所有受保护标记。"

        result = validate_translation(source, target, "zh")

        self.assertFalse(result.accepted)
        self.assertIn("protected_token_mismatch", result.reasons)

    def test_rejects_unchanged_substantial_english(self) -> None:
        source = (
            "Mobile robots use several sensors to estimate position and avoid "
            "obstacles in dynamic environments."
        )

        result = validate_translation(source, source, "zh")

        self.assertFalse(result.accepted)
        self.assertIn("unchanged_source", result.reasons)

    def test_rejects_obviously_truncated_target(self) -> None:
        source = (
            "Mobile robots use several sensors to estimate position. "
            "They combine measurements to reduce uncertainty."
        )
        target = "移动机器人使用多个传感器估计位置，并通过融合测量来"

        result = validate_translation(source, target, "zh")

        self.assertFalse(result.accepted)
        self.assertIn("truncated_target", result.reasons)

    def test_accepts_complete_chinese_translation_with_protected_tokens(self) -> None:
        source = (
            "The method keeps {v1}, citation [15]–[17], DOI "
            "10.1109/ACCESS.2020.2975643 and 25.4%."
        )
        target = (
            "该方法保留 {v1}、引用 [15]–[17]、DOI "
            "10.1109/ACCESS.2020.2975643 和 25.4%。"
        )

        result = validate_translation(source, target, "zh")

        self.assertTrue(result.accepted, result.reasons)
        self.assertEqual(result.reasons, ())


class TranslationChunkingTest(unittest.TestCase):
    def test_splits_at_sentence_boundaries_without_losing_or_reordering_text(self) -> None:
        source = (
            "First sentence has useful context. "
            "Second sentence is also complete! "
            "Third sentence finishes the paragraph?"
        )

        chunks = split_translation_chunks(source, max_chars=48)

        self.assertEqual(
            chunks,
            [
                "First sentence has useful context.",
                "Second sentence is also complete!",
                "Third sentence finishes the paragraph?",
            ],
        )
        self.assertEqual(" ".join(chunks), source)

    def test_hard_splits_a_single_sentence_at_the_limit(self) -> None:
        source = "abcdefghij"

        chunks = split_translation_chunks(source, max_chars=4)

        self.assertEqual(chunks, ["abcd", "efgh", "ij"])
        self.assertEqual("".join(chunks), source)


if __name__ == "__main__":
    unittest.main()
