
"""Bounded retry engine for shipping-label operations."""

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel, Field

from retry_mechanisms import (
    ShippingLabelInput,
    WorkflowRun,
    WorkflowState,
    create_workflow_run,
    transition,
)


class RetryableError(Exception):
    """An error that may succeed when retried."""


class PermanentError(Exception):
    """An error that should not be retried."""


class RetryPolicy(BaseModel):
    max_attempts: int = Field(default=3, ge=1, le=10)
    initial_backoff_seconds: float = Field(default=1.0, ge=0)
    backoff_multiplier: float = Field(default=2.0, ge=1)
    max_backoff_seconds: float = Field(default=30.0, ge=0)


@dataclass
class RetryResult:
    run_id: str
    status: str
    attempts: int
    idempotency_key: str
    label_id: str | None = None
    error_type: str | None = None
    message: str = ""
    attempt_log: list[dict[str, Any]] = field(default_factory=list)
    total_backoff_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "attempts": self.attempts,
            "idempotency_key": self.idempotency_key,
            "label_id": self.label_id,
            "error_type": self.error_type,
            "message": self.message,
            "attempt_log": self.attempt_log,
            "total_backoff_seconds": self.total_backoff_seconds,
        }


class RetryEngine:
    """Execute an operation with classification and bounded backoff."""

    def __init__(
        self,
        policy: RetryPolicy | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ):
        self.policy = policy or RetryPolicy()
        self.sleep_fn = sleep_fn

    def backoff_for_attempt(self, failed_attempt: int) -> float:
        """Calculate capped exponential backoff after a failed attempt."""
        delay = (
            self.policy.initial_backoff_seconds
            * self.policy.backoff_multiplier ** (failed_attempt - 1)
        )
        return min(delay, self.policy.max_backoff_seconds)

    @staticmethod
    def classify_error(error: Exception) -> str:
        """Classify known retryable and permanent errors."""
        if isinstance(error, (RetryableError, TimeoutError, ConnectionError)):
            return "transient"

        if isinstance(error, PermanentError):
            return "permanent"

        # Unknown exceptions are not retried automatically.
        return "permanent"

    def execute(
        self,
        workflow_input: ShippingLabelInput,
        operation: Callable[[ShippingLabelInput, str], str],
        policy: RetryPolicy | None = None,
    ) -> RetryResult:
        """Execute an operation, retrying only transient failures."""
        active_policy = policy or self.policy

        run: WorkflowRun = create_workflow_run(
            workflow_input,
            max_attempts=active_policy.max_attempts,
        )
        transition(run, "start")

        attempt_log: list[dict[str, Any]] = []
        total_backoff = 0.0

        for attempt in range(1, active_policy.max_attempts + 1):
            run.attempts = attempt
            run.record("attempt_started", attempt=attempt)
            run.trace("attempt_started", attempt=attempt)

            try:
                # The same key is passed to every attempt.
                label_id = operation(
                    workflow_input,
                    run.idempotency_key,
                )

                if not isinstance(label_id, str) or not label_id.strip():
                    raise PermanentError(
                        "Shipping provider returned an invalid label ID"
                    )

                transition(run, "complete")

                attempt_log.append({
                    "attempt": attempt,
                    "outcome": "success",
                    "error_type": None,
                    "backoff_seconds": 0,
                })

                run.trace(
                    "operation_succeeded",
                    attempt=attempt,
                    label_id=label_id,
                )

                return RetryResult(
                    run_id=run.run_id,
                    status=WorkflowState.COMPLETED.value,
                    attempts=attempt,
                    idempotency_key=run.idempotency_key,
                    label_id=label_id,
                    message="Shipping label created successfully",
                    attempt_log=attempt_log,
                    total_backoff_seconds=total_backoff,
                )

            except Exception as error:
                error_type = self.classify_error(error)
                can_retry = (
                    error_type == "transient"
                    and attempt < active_policy.max_attempts
                )

                delay = (
                    min(
                        active_policy.initial_backoff_seconds
                        * active_policy.backoff_multiplier ** (attempt - 1),
                        active_policy.max_backoff_seconds,
                    )
                    if can_retry
                    else 0.0
                )

                attempt_log.append({
                    "attempt": attempt,
                    "outcome": "failure",
                    "error_type": error_type,
                    "error": str(error),
                    "backoff_seconds": delay,
                    "will_retry": can_retry,
                })

                run.record(
                    "attempt_failed",
                    attempt=attempt,
                    error_type=error_type,
                    will_retry=can_retry,
                )
                run.trace(
                    "attempt_failed",
                    attempt=attempt,
                    error_type=error_type,
                    will_retry=can_retry,
                )

                if not can_retry:
                    if error_type == "permanent":
                        transition(run, "permanent_failure")
                    else:
                        transition(run, "retry")
                        transition(run, "attempts_exhausted")

                    run.trace(
                        "workflow_stopped",
                        next_action="manual_review",
                        reason=error_type,
                    )

                    return RetryResult(
                        run_id=run.run_id,
                        status=WorkflowState.NEEDS_REVIEW.value,
                        attempts=attempt,
                        idempotency_key=run.idempotency_key,
                        error_type=type(error).__name__,
                        message=str(error),
                        attempt_log=attempt_log,
                        total_backoff_seconds=total_backoff,
                    )

                transition(run, "retry")
                total_backoff += delay
                self.sleep_fn(delay)
                transition(run, "retry_started")

        # Defensive fallback; the bounded loop should return earlier.
        return RetryResult(
            run_id=run.run_id,
            status=WorkflowState.NEEDS_REVIEW.value,
            attempts=run.attempts,
            idempotency_key=run.idempotency_key,
            error_type="RetryLimitExceeded",
            message="Retry limit reached",
            attempt_log=attempt_log,
            total_backoff_seconds=total_backoff,
        )


def run_task2_demo() -> dict[str, Any]:
    """Demonstrate two transient failures followed by success."""
    calls = {"count": 0}

    def fake_shipping_provider(
        shipping_input: ShippingLabelInput,
        idempotency_key: str,
    ) -> str:
        calls["count"] += 1

        if calls["count"] < 3:
            raise RetryableError("Temporary shipping provider outage")

        return "LABEL-DEMO-1001"

    policy = RetryPolicy(
        max_attempts=4,
        initial_backoff_seconds=1,
        backoff_multiplier=2,
        max_backoff_seconds=5,
    )

    # No real waiting during the deterministic demonstration.
    engine = RetryEngine(policy=policy, sleep_fn=lambda seconds: None)

    shipping_input = ShippingLabelInput(
        shipment_id="SHIP-1001",
        destination_postal_code="395007",
        package_weight_kg=2.5,
        service_code="EXPRESS",
    )

    result = engine.execute(shipping_input, fake_shipping_provider)

    return {
        "scenario": "two transient failures followed by success",
        "provider_calls": calls["count"],
        "result": result.to_dict(),
    }


if __name__ == "__main__":
    print("=" * 60)
    print("TOPIC 10 — TASK 2: RETRY ENGINE")
    print("=" * 60)
    print(json.dumps(run_task2_demo(), indent=2))
