from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from local_babeldoc_server.translation_budget import AttemptLimitExceeded
from local_babeldoc_server.translation_budget import BudgetExceeded
from local_babeldoc_server.translation_budget import DocumentBudget
from local_babeldoc_server.translation_budget import ReservationAlreadySettled
from local_babeldoc_server.translation_types import RequestContext


def request_context(
    paragraph_ids: tuple[str, ...] = ("p001",),
    transport_attempt: int = 1,
) -> RequestContext:
    return RequestContext(
        document_id="doc-1",
        part_index=0,
        request_category="batch",
        paragraph_ids=paragraph_ids,
        semantic_attempt_number=1,
        transport_attempt_number=transport_attempt,
        source_token_estimate=100,
        paragraph_count=len(paragraph_ids),
    )


class TranslationBudgetTest(unittest.TestCase):
    def test_concurrent_reservations_never_exceed_cost_limit(self) -> None:
        budget = DocumentBudget(
            max_requests=100,
            max_cost_usd=Decimal("1.00"),
        )

        def try_reserve(index: int):
            try:
                return budget.reserve(
                    request_context((f"p{index:03d}",)),
                    Decimal("0.11"),
                )
            except BudgetExceeded:
                return None

        with ThreadPoolExecutor(max_workers=16) as pool:
            reservations = list(pool.map(try_reserve, range(32)))

        snapshot = budget.snapshot()
        self.assertEqual(sum(item is not None for item in reservations), 9)
        self.assertEqual(snapshot.request_count, 9)
        self.assertEqual(snapshot.in_flight_requests, 9)
        self.assertEqual(snapshot.reserved_cost, Decimal("0.99"))
        self.assertLessEqual(snapshot.committed_cost, Decimal("1.00"))

    def test_unknown_billing_converts_full_reservation_to_actual(self) -> None:
        budget = DocumentBudget(
            max_requests=2,
            max_cost_usd=Decimal("1.00"),
        )
        reservation = budget.reserve(request_context(), Decimal("0.25"))

        snapshot = budget.settle_unknown(reservation, "response_timeout")

        self.assertEqual(snapshot.actual_cost, Decimal("0.25"))
        self.assertEqual(snapshot.reserved_cost, Decimal("0"))
        self.assertEqual(snapshot.committed_cost, Decimal("0.25"))
        self.assertEqual(snapshot.in_flight_requests, 0)
        self.assertEqual(snapshot.completed_requests, 1)
        self.assertEqual(snapshot.billable_exposures["p001"], 1)

    def test_success_releases_unused_reservation(self) -> None:
        budget = DocumentBudget(
            max_requests=2,
            max_cost_usd=Decimal("1.00"),
        )
        reservation = budget.reserve(request_context(), Decimal("0.25"))

        snapshot = budget.settle_success(reservation, Decimal("0.04"))

        self.assertEqual(snapshot.actual_cost, Decimal("0.04"))
        self.assertEqual(snapshot.reserved_cost, Decimal("0"))
        self.assertEqual(snapshot.billable_exposures["p001"], 1)

    def test_confirmed_non_billable_attempt_keeps_request_but_releases_exposure(self) -> None:
        budget = DocumentBudget(
            max_requests=2,
            max_cost_usd=Decimal("1.00"),
        )
        reservation = budget.reserve(request_context(), Decimal("0.25"))

        snapshot = budget.settle_not_billable(reservation, "rate_limited")

        self.assertEqual(snapshot.request_count, 1)
        self.assertEqual(snapshot.completed_requests, 1)
        self.assertEqual(snapshot.actual_cost, Decimal("0"))
        self.assertNotIn("p001", snapshot.billable_exposures)
        second = budget.reserve(request_context(transport_attempt=2), Decimal("0.25"))
        self.assertIsNotNone(second)

    def test_pending_and_committed_exposures_enforce_per_paragraph_limit(self) -> None:
        budget = DocumentBudget(
            max_requests=10,
            max_cost_usd=Decimal("10"),
            max_billable_exposures_per_paragraph=2,
        )
        first = budget.reserve(request_context(), Decimal("0.10"))
        second = budget.reserve(request_context(transport_attempt=2), Decimal("0.10"))

        with self.assertRaises(AttemptLimitExceeded):
            budget.reserve(request_context(transport_attempt=3), Decimal("0.10"))

        budget.settle_success(first, Decimal("0.01"))
        budget.settle_unknown(second, "response_timeout")
        with self.assertRaises(AttemptLimitExceeded):
            budget.reserve(request_context(transport_attempt=4), Decimal("0.10"))

    def test_semantic_attempt_limit_is_independent_from_transport(self) -> None:
        budget = DocumentBudget(
            max_requests=10,
            max_cost_usd=Decimal("10"),
            max_semantic_attempts_per_paragraph=2,
        )
        budget.record_semantic_attempt(("p001",))
        budget.record_semantic_attempt(("p001",))

        with self.assertRaises(AttemptLimitExceeded):
            budget.assert_semantic_attempt_available(("p001",))
        self.assertEqual(budget.snapshot().semantic_attempts["p001"], 2)

    def test_request_limit_and_abort_prevent_new_reservations(self) -> None:
        budget = DocumentBudget(
            max_requests=1,
            max_cost_usd=Decimal("10"),
        )
        reservation = budget.reserve(request_context(), Decimal("0.10"))
        budget.settle_not_billable(reservation, "rate_limited")

        with self.assertRaises(BudgetExceeded):
            budget.reserve(request_context(transport_attempt=2), Decimal("0.10"))

        other = DocumentBudget(max_requests=2, max_cost_usd=Decimal("10"))
        other.abort("ocr_anomaly")
        with self.assertRaises(BudgetExceeded):
            other.reserve(request_context(), Decimal("0.10"))
        self.assertEqual(other.snapshot().abort_reason, "ocr_anomaly")

    def test_reservation_can_only_be_settled_once(self) -> None:
        budget = DocumentBudget(max_requests=2, max_cost_usd=Decimal("10"))
        reservation = budget.reserve(request_context(), Decimal("0.10"))
        budget.settle_success(reservation, Decimal("0.01"))

        with self.assertRaises(ReservationAlreadySettled):
            budget.settle_success(reservation, Decimal("0.01"))


if __name__ == "__main__":
    unittest.main()
