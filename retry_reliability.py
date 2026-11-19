
"""Failure handling, timeout protection, and replay safety."""

import json
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from retry_mechanisms import ShippingLabelInput
from retry_engine import (
    PermanentError,
    RetryEngine,
    RetryPolicy,
    RetryResult,
    RetryableError,
)


class MalformedResponseError(PermanentError):
    """The provider returned an invalid response."""


class DuplicateRequestError(PermanentError):
    """A request conflicts with an existing idempotency record."""


@dataclass
class IdempotencyRecord:
    idempotency_key: str
    label_id: str
    created_at: float
    replay_count: int = 0


class IdempotentShippingGateway:
    """
    In-memory fake provider boundary.

    Repeated requests with the same idempotency key return the original
    label ID rather than purchasing another label.
    """

    def __init__(self):
        self._records: dict[str, IdempotencyRecord] = {}
        self._lock = threading.Lock()
        self.purchase_count = 0
        self.replay_count = 0

    def create_label(
        self,
        shipping_input: ShippingLabelInput,
        idempotency_key: str,
        provider: Callable[[ShippingLabelInput], Any],
    ) -> str:
        with self._lock:
            existing = self._records.get(idempotency_key)

            if existing is not None:
                existing.replay_count += 1
                self.replay_count += 1
                return existing.label_id

            response = provider(shipping_input)

            if not isinstance(response, dict):
                raise MalformedResponseError(
                    "Provider response must be a dictionary"
                )

            label_id = response.get("label_id")

            if not isinstance(label_id, str) or not label_id.strip():
                raise MalformedResponseError(
                    "Provider response is missing a valid label_id"
                )

            record = IdempotencyRecord(
                idempotency_key=idempotency_key,
                label_id=label_id,
                created_at=time.time(),
            )
            self._records[idempotency_key] = record
            self.purchase_count += 1
            return label_id

    def summary(self) -> dict[str, Any]:
        return {
            "unique_labels_purchased": self.purchase_count,
            "duplicate_replays": self.replay_count,
            "idempotency_records": len(self._records),
        }


@dataclass
class TimeoutOutcome:
    completed: bool
    value: Any = None
    error: str | None = None
    elapsed_seconds: float = 0.0


def execute_with_timeout(
    operation: Callable[[], Any],
    timeout_seconds: float,
) -> TimeoutOutcome:
    """
    Bound caller wait time for an operation.

    A daemon worker may continue after timeout. This helper does not
    forcibly terminate Python threads.
    """
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than zero")

    result_queue: queue.Queue = queue.Queue(maxsize=1)
    started = time.monotonic()

    def worker():
        try:
            result_queue.put(("success", operation()))
        except Exception as error:
            result_queue.put(("error", error))

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    try:
        outcome, value = result_queue.get(timeout=timeout_seconds)
        elapsed = time.monotonic() - started

        if outcome == "error":
            return TimeoutOutcome(
                completed=True,
                error=f"{type(value).__name__}: {value}",
                elapsed_seconds=elapsed,
            )

        return TimeoutOutcome(
            completed=True,
            value=value,
            elapsed_seconds=elapsed,
        )

    except queue.Empty:
        return TimeoutOutcome(
            completed=False,
            error="Operation exceeded timeout",
            elapsed_seconds=time.monotonic() - started,
        )


@dataclass
class RecoveryDecision:
    status: str
    next_action: str
    reason: str
    safe_to_replay: bool


def recovery_decision(result: RetryResult) -> RecoveryDecision:
    """Provide an explicit operator action for a workflow outcome."""
    if result.status == "completed":
        return RecoveryDecision(
            status="completed",
            next_action="no_action",
            reason="Shipping label was created successfully",
            safe_to_replay=False,
        )

    if result.error_type == "TimeoutError":
        return RecoveryDecision(
            status="needs_review",
            next_action="query_provider_by_idempotency_key",
            reason=(
                "Timeout does not prove the provider did not create a label"
            ),
            safe_to_replay=True,
        )

    if result.error_type == "MalformedResponseError":
        return RecoveryDecision(
            status="needs_review",
            next_action="verify_provider_record_before_replay",
            reason="Provider response was malformed",
            safe_to_replay=True,
        )

    return RecoveryDecision(
        status="needs_review",
        next_action="manual_investigation",
        reason=result.message,
        safe_to_replay=False,
    )


def run_task3_demo() -> dict[str, Any]:
    """Demonstrate timeout, malformed response, and replay protection."""
    shipping_input = ShippingLabelInput(
        shipment_id="SHIP-3001",
        destination_postal_code="395007",
        package_weight_kg=2.5,
        service_code="EXPRESS",
    )

    # Scenario 1: A hanging dependency exceeds the caller's time budget.
    timeout_outcome = execute_with_timeout(
        lambda: time.sleep(0.2),
        timeout_seconds=0.02,
    )

    # Scenario 2: Malformed provider response is a permanent failure.
    gateway = IdempotentShippingGateway()

    def malformed_provider(data):
        return {"unexpected": "missing label_id"}

    engine = RetryEngine(
        RetryPolicy(max_attempts=3, initial_backoff_seconds=0),
        sleep_fn=lambda seconds: None,
    )

    malformed_result = engine.execute(
        shipping_input,
        lambda data, key: gateway.create_label(
            data, key, malformed_provider
        ),
    )

    malformed_recovery = recovery_decision(malformed_result)

    # Scenario 3: Repeated delivery uses the same key and purchases once.
    def valid_provider(data):
        return {"label_id": "LABEL-3001"}

    first_label = gateway.create_label(
        shipping_input,
        "shipping-label:SHIP-3001",
        valid_provider,
    )

    replayed_label = gateway.create_label(
        shipping_input,
        "shipping-label:SHIP-3001",
        valid_provider,
    )

    return {
        "timeout": {
            "completed": timeout_outcome.completed,
            "error": timeout_outcome.error,
            "elapsed_seconds": round(
                timeout_outcome.elapsed_seconds, 4
            ),
            "next_action": "inspect provider using idempotency key",
        },
        "malformed_response": {
            "status": malformed_result.status,
            "attempts": malformed_result.attempts,
            "error_type": malformed_result.error_type,
            "recovery": {
                "next_action": malformed_recovery.next_action,
                "safe_to_replay": malformed_recovery.safe_to_replay,
            },
        },
        "duplicate_replay": {
            "first_label": first_label,
            "replayed_label": replayed_label,
            "same_label": first_label == replayed_label,
            "gateway": gateway.summary(),
        },
    }


if __name__ == "__main__":
    print("=" * 60)
    print("TOPIC 10 — TASK 3: FAILURE AND REPLAY SAFETY")
    print("=" * 60)
    print(json.dumps(run_task3_demo(), indent=2))
