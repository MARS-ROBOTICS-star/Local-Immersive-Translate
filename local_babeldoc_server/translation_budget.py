from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping

from local_babeldoc_server.translation_types import RequestContext


class BudgetExceeded(RuntimeError):
    pass


class AttemptLimitExceeded(RuntimeError):
    pass


class ReservationAlreadySettled(RuntimeError):
    pass


class BudgetAccountingError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Reservation:
    reservation_id: str
    context: RequestContext
    reserved_cost: Decimal


@dataclass(frozen=True, slots=True)
class BudgetSnapshot:
    request_limit: int
    cost_limit_usd: Decimal
    request_count: int
    reserved_requests: int
    in_flight_requests: int
    completed_requests: int
    reserved_cost: Decimal
    actual_cost: Decimal
    committed_cost: Decimal
    semantic_attempts: Mapping[str, int]
    billable_exposures: Mapping[str, int]
    pending_exposures: Mapping[str, int]
    aborted: bool
    abort_reason: str | None


class DocumentBudget:
    def __init__(
        self,
        *,
        max_requests: int,
        max_cost_usd: Decimal,
        max_semantic_attempts_per_paragraph: int = 2,
        max_billable_exposures_per_paragraph: int = 2,
    ) -> None:
        if max_requests < 1:
            raise ValueError("max_requests must be positive")
        if max_cost_usd <= 0:
            raise ValueError("max_cost_usd must be positive")
        if max_semantic_attempts_per_paragraph < 1:
            raise ValueError(
                "max_semantic_attempts_per_paragraph must be positive"
            )
        if max_billable_exposures_per_paragraph < 1:
            raise ValueError(
                "max_billable_exposures_per_paragraph must be positive"
            )
        self.max_requests = int(max_requests)
        self.max_cost_usd = Decimal(max_cost_usd)
        self.max_semantic_attempts_per_paragraph = int(
            max_semantic_attempts_per_paragraph
        )
        self.max_billable_exposures_per_paragraph = int(
            max_billable_exposures_per_paragraph
        )
        self._lock = threading.RLock()
        self._idle_condition = threading.Condition(self._lock)
        self._request_count = 0
        self._reserved_requests = 0
        self._in_flight_requests = 0
        self._completed_requests = 0
        self._reserved_cost = Decimal("0")
        self._actual_cost = Decimal("0")
        self._semantic_attempts: dict[str, int] = {}
        self._billable_exposures: dict[str, int] = {}
        self._pending_exposures: dict[str, int] = {}
        self._active: dict[str, Reservation] = {}
        self._settled_ids: set[str] = set()
        self._aborted = False
        self._abort_reason: str | None = None

    def abort(self, reason: str) -> None:
        with self._lock:
            self._abort_locked(reason)
            self._idle_condition.notify_all()

    def _abort_locked(self, reason: str) -> None:
        self._aborted = True
        if self._abort_reason is None:
            self._abort_reason = reason

    def assert_semantic_attempt_available(
        self,
        paragraph_ids: tuple[str, ...],
    ) -> None:
        with self._lock:
            for paragraph_id in paragraph_ids:
                attempts = self._semantic_attempts.get(paragraph_id, 0)
                if attempts >= self.max_semantic_attempts_per_paragraph:
                    raise AttemptLimitExceeded(
                        f"semantic attempt limit reached for {paragraph_id}"
                    )

    def record_semantic_attempt(
        self,
        paragraph_ids: tuple[str, ...],
    ) -> None:
        with self._lock:
            for paragraph_id in paragraph_ids:
                attempts = self._semantic_attempts.get(paragraph_id, 0)
                if attempts >= self.max_semantic_attempts_per_paragraph:
                    raise AttemptLimitExceeded(
                        f"semantic attempt limit reached for {paragraph_id}"
                    )
            for paragraph_id in paragraph_ids:
                self._semantic_attempts[paragraph_id] = (
                    self._semantic_attempts.get(paragraph_id, 0) + 1
                )

    def reserve(
        self,
        context: RequestContext,
        worst_case_cost: Decimal,
    ) -> Reservation:
        cost = Decimal(worst_case_cost)
        if cost < 0:
            raise ValueError("worst_case_cost cannot be negative")
        with self._lock:
            if self._aborted:
                raise BudgetExceeded(
                    f"document budget is aborted: {self._abort_reason}"
                )
            if self._request_count >= self.max_requests:
                self._abort_locked("budget_request_limit")
                raise BudgetExceeded("document request limit reached")
            for paragraph_id in context.paragraph_ids:
                exposure = self._billable_exposures.get(paragraph_id, 0)
                exposure += self._pending_exposures.get(paragraph_id, 0)
                if exposure >= self.max_billable_exposures_per_paragraph:
                    raise AttemptLimitExceeded(
                        f"billable exposure limit reached for {paragraph_id}"
                    )
            if self._actual_cost + self._reserved_cost + cost > self.max_cost_usd:
                self._abort_locked("budget_cost_limit")
                raise BudgetExceeded("document cost limit reached")

            reservation = Reservation(
                reservation_id=uuid.uuid4().hex,
                context=context,
                reserved_cost=cost,
            )
            self._active[reservation.reservation_id] = reservation
            self._request_count += 1
            self._reserved_requests += 1
            self._in_flight_requests += 1
            self._reserved_cost += cost
            for paragraph_id in context.paragraph_ids:
                self._pending_exposures[paragraph_id] = (
                    self._pending_exposures.get(paragraph_id, 0) + 1
                )
            return reservation

    def settle_success(
        self,
        reservation: Reservation,
        actual_cost: Decimal,
    ) -> BudgetSnapshot:
        return self._settle(
            reservation,
            actual_cost=Decimal(actual_cost),
            billable=True,
        )

    def settle_unknown(
        self,
        reservation: Reservation,
        _outcome: str,
    ) -> BudgetSnapshot:
        return self._settle(
            reservation,
            actual_cost=reservation.reserved_cost,
            billable=True,
        )

    def settle_not_billable(
        self,
        reservation: Reservation,
        _outcome: str,
    ) -> BudgetSnapshot:
        return self._settle(
            reservation,
            actual_cost=Decimal("0"),
            billable=False,
        )

    def _settle(
        self,
        reservation: Reservation,
        *,
        actual_cost: Decimal,
        billable: bool,
    ) -> BudgetSnapshot:
        if actual_cost < 0:
            raise ValueError("actual_cost cannot be negative")
        with self._lock:
            active = self._active.pop(reservation.reservation_id, None)
            if active is None:
                raise ReservationAlreadySettled(
                    f"reservation already settled: {reservation.reservation_id}"
                )
            self._settled_ids.add(reservation.reservation_id)
            self._reserved_requests -= 1
            self._in_flight_requests -= 1
            self._completed_requests += 1
            self._reserved_cost -= active.reserved_cost
            self._actual_cost += actual_cost

            for paragraph_id in active.context.paragraph_ids:
                pending = self._pending_exposures.get(paragraph_id, 0) - 1
                if pending > 0:
                    self._pending_exposures[paragraph_id] = pending
                else:
                    self._pending_exposures.pop(paragraph_id, None)
                if billable:
                    self._billable_exposures[paragraph_id] = (
                        self._billable_exposures.get(paragraph_id, 0) + 1
                    )

            overrun = actual_cost > active.reserved_cost
            if overrun:
                self._abort_locked("budget_reservation_overrun")
            self._idle_condition.notify_all()
            snapshot = self._snapshot_locked()
            if overrun:
                raise BudgetAccountingError(
                    "settled cost exceeded its worst-case reservation"
                )
            return snapshot

    def wait_for_idle(self, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        with self._idle_condition:
            while self._in_flight_requests:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._idle_condition.wait(remaining)
            return True

    def snapshot(self) -> BudgetSnapshot:
        with self._lock:
            return self._snapshot_locked()

    def _snapshot_locked(self) -> BudgetSnapshot:
        return BudgetSnapshot(
            request_limit=self.max_requests,
            cost_limit_usd=self.max_cost_usd,
            request_count=self._request_count,
            reserved_requests=self._reserved_requests,
            in_flight_requests=self._in_flight_requests,
            completed_requests=self._completed_requests,
            reserved_cost=self._reserved_cost,
            actual_cost=self._actual_cost,
            committed_cost=self._actual_cost + self._reserved_cost,
            semantic_attempts=MappingProxyType(dict(self._semantic_attempts)),
            billable_exposures=MappingProxyType(
                dict(self._billable_exposures)
            ),
            pending_exposures=MappingProxyType(dict(self._pending_exposures)),
            aborted=self._aborted,
            abort_reason=self._abort_reason,
        )
