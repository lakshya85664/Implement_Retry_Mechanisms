
import pytest
from pydantic import ValidationError

from retry_mechanisms import ShippingLabelInput
from retry_engine import (
    PermanentError,
    RetryEngine,
    RetryPolicy,
    RetryableError,
)


@pytest.fixture
def shipping_input():
    return ShippingLabelInput(
        shipment_id="SHIP-2001",
        destination_postal_code="395007",
        package_weight_kg=3.0,
        service_code="STANDARD",
    )


def test_transient_failure_then_success(shipping_input):
    calls = {"count": 0}
    sleeps = []

    def operation(data, idempotency_key):
        calls["count"] += 1
        if calls["count"] < 3:
            raise RetryableError("Temporary provider outage")
        return "LABEL-2001"

    engine = RetryEngine(
        RetryPolicy(max_attempts=4, initial_backoff_seconds=1),
        sleep_fn=sleeps.append,
    )

    result = engine.execute(shipping_input, operation)

    assert result.status == "completed"
    assert result.attempts == 3
    assert result.label_id == "LABEL-2001"
    assert calls["count"] == 3
    assert sleeps == [1, 2]


def test_permanent_failure_is_not_retried(shipping_input):
    calls = {"count": 0}

    def operation(data, idempotency_key):
        calls["count"] += 1
        raise PermanentError("Invalid shipping service")

    engine = RetryEngine(sleep_fn=lambda seconds: None)
    result = engine.execute(shipping_input, operation)

    assert result.status == "needs_review"
    assert result.attempts == 1
    assert calls["count"] == 1
    assert result.error_type == "PermanentError"


def test_retry_limit_is_enforced(shipping_input):
    calls = {"count": 0}

    def operation(data, idempotency_key):
        calls["count"] += 1
        raise RetryableError("Provider unavailable")

    engine = RetryEngine(
        RetryPolicy(max_attempts=3, initial_backoff_seconds=0),
        sleep_fn=lambda seconds: None,
    )

    result = engine.execute(shipping_input, operation)

    assert result.status == "needs_review"
    assert result.attempts == 3
    assert calls["count"] == 3


def test_backoff_is_exponential_and_capped():
    engine = RetryEngine(
        RetryPolicy(
            max_attempts=5,
            initial_backoff_seconds=2,
            backoff_multiplier=3,
            max_backoff_seconds=10,
        )
    )

    assert engine.backoff_for_attempt(1) == 2
    assert engine.backoff_for_attempt(2) == 6
    assert engine.backoff_for_attempt(3) == 10
    assert engine.backoff_for_attempt(4) == 10


def test_same_idempotency_key_is_used_for_every_attempt(shipping_input):
    keys = []

    def operation(data, idempotency_key):
        keys.append(idempotency_key)
        if len(keys) == 1:
            raise RetryableError("Temporary failure")
        return "LABEL-2002"

    engine = RetryEngine(
        RetryPolicy(max_attempts=3, initial_backoff_seconds=0),
        sleep_fn=lambda seconds: None,
    )

    result = engine.execute(shipping_input, operation)

    assert result.status == "completed"
    assert len(keys) == 2
    assert len(set(keys)) == 1
    assert keys[0] == result.idempotency_key


def test_invalid_retry_policy_is_rejected():
    with pytest.raises(ValidationError):
        RetryPolicy(max_attempts=100)


def test_unknown_exception_is_not_retried(shipping_input):
    calls = {"count": 0}

    def operation(data, idempotency_key):
        calls["count"] += 1
        raise RuntimeError("Unexpected programming error")

    engine = RetryEngine(sleep_fn=lambda seconds: None)
    result = engine.execute(shipping_input, operation)

    assert result.status == "needs_review"
    assert result.attempts == 1
    assert calls["count"] == 1
    assert result.error_type == "RuntimeError"


def test_successful_operation_runs_once(shipping_input):
    calls = {"count": 0}

    def operation(data, idempotency_key):
        calls["count"] += 1
        return "LABEL-2003"

    engine = RetryEngine(sleep_fn=lambda seconds: None)
    result = engine.execute(shipping_input, operation)

    assert result.status == "completed"
    assert result.attempts == 1
    assert calls["count"] == 1
