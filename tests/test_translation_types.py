from __future__ import annotations

import unittest
from decimal import Decimal
from types import SimpleNamespace

from local_babeldoc_server.translation_types import NormalizedUsage
from local_babeldoc_server.translation_types import PricingSnapshot
from local_babeldoc_server.translation_types import calculate_usage_cost


PRICING = PricingSnapshot(
    provider="google",
    model="gemini-3.6-flash",
    api_surface="interactions-v1",
    service_tier="standard",
    input_usd_per_million=Decimal("1.5"),
    output_usd_per_million=Decimal("7.5"),
    cached_input_usd_per_million=Decimal("0.15"),
    usd_to_jpy=Decimal("150"),
    tax_included=False,
    captured_at="2026-08-02T00:00:00+08:00",
)


class TranslationTypesTest(unittest.TestCase):
    def test_cached_input_is_not_charged_twice(self) -> None:
        usage = NormalizedUsage(
            prompt_tokens=1000,
            visible_completion_tokens=100,
            reasoning_tokens=50,
            cached_tokens=400,
            tool_use_tokens=0,
            total_tokens=1150,
        )

        cost = calculate_usage_cost(usage, PRICING)

        self.assertEqual(cost, Decimal("0.002085"))

    def test_openai_usage_separates_reasoning_from_total_completion(self) -> None:
        usage = SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=40,
            total_tokens=140,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=15),
            prompt_tokens_details=SimpleNamespace(cached_tokens=25),
        )

        normalized = NormalizedUsage.from_openai_usage(usage)

        self.assertEqual(normalized.prompt_tokens, 100)
        self.assertEqual(normalized.visible_completion_tokens, 25)
        self.assertEqual(normalized.reasoning_tokens, 15)
        self.assertEqual(normalized.cached_tokens, 25)
        self.assertIsNone(normalized.tool_use_tokens)
        self.assertEqual(normalized.total_tokens, 140)

    def test_missing_openai_usage_details_stay_none(self) -> None:
        usage = SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=4,
            total_tokens=14,
        )

        normalized = NormalizedUsage.from_openai_usage(usage)

        self.assertEqual(normalized.visible_completion_tokens, 4)
        self.assertIsNone(normalized.reasoning_tokens)
        self.assertIsNone(normalized.cached_tokens)
        self.assertIsNone(normalized.tool_use_tokens)

    def test_pricing_snapshot_serializes_decimal_values_as_strings(self) -> None:
        serialized = PRICING.to_dict()

        self.assertEqual(serialized["input_usd_per_million"], "1.5")
        self.assertEqual(serialized["output_usd_per_million"], "7.5")
        self.assertEqual(serialized["cached_input_usd_per_million"], "0.15")
        self.assertEqual(serialized["usd_to_jpy"], "150")


if __name__ == "__main__":
    unittest.main()
