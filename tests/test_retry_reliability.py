
import time

import pytest

from retry_mechanisms import ShippingLabelInput
from retry_engine import RetryEngine, RetryPolicy
from retry_reliability import (
    IdempotentShippingGateway,
    MalformedResponseError,
    execute_with_timeout,
    recovery_decision,
)


@pytest.fixture
def shipping_input():
    return ShippingLabelInput(
        shipment_id="SHIP-3001",
        destination_postal_code="395007",
        package_weight_kg=2.5,
        service_code="EXPRESS",
    )


def test_timeout_is_detected():
    result = execute_with_timeout(
        lambda: time.sleep(0.2),
        timeout_seconds=0.02,
    )

    assert result.completed is False
    assert "timeout" in result.error.lower()
    assert result.elapsed_seconds < 0.15


def test_invalid_timeout_is_rejected():
    with pytest.raises(ValueError):
        execute_with_timeout(lambda: "ok", timeout_seconds=0)


def test_malformed_provider_response_is_rejected(shipping_input):
    gateway = IdempotentShippingGateway()

    def provider(data):
        return {"wrong_field": "value"}

    with pytest.raises(MalformedResponseError):
        gateway.create_label(
            shipping_input,
            "shipping-label:SHIP-3001",
            provider,
        )

    assert gateway.purchase_count == 0


def test_duplicate_delivery_does_not_purchase_twice(shipping_input):
    gateway = IdempotentShippingGateway()
    calls = {"count": 0}

    def provider(data):
        calls["count"] += 1
        return {"label_id": "LABEL-3001"}

    key = "shipping-label:SHIP-3001"

    first = gateway.create_label(shipping_input, key, provider)
    second = gateway.create_label(shipping_input, key, provider)

    assert first == second == "LABEL-3001"
    assert calls["count"] == 1
    assert gateway.purchase_count == 1
    assert gateway.replay_count == 1



def test_different_shipments_use_different_idempotency_keys(
    shipping_input,
):
    gateway = IdempotentShippingGateway()

    second_input = ShippingLabelInput(
        shipment_id="SHIP-3002",
        destination_postal_code="380001",
        package_weight_kg=4.0,
        service_code="STANDARD",
    )

    def provider(data):
        return {"label_id": f"LABEL-{data.shipment_id}"}

    first = gateway.create_label(
        shipping_input,
        "shipping-label:SHIP-3001",
        provider,
    )

    second = gateway.create_label(
        second_input,
        "shipping-label:SHIP-3002",
        provider,
    )

    assert first == "LABEL-SHIP-3001"
    assert second == "LABEL-SHIP-3002"
    assert first != second
    assert gateway.purchase_count == 2


def test_recovery_decision_for_timeout(shipping_input):
    def provider(data, key):
        raise TimeoutError("Provider timed out")

    engine = RetryEngine(
        RetryPolicy(max_attempts=1),
        sleep_fn=lambda seconds: None,
    )
    result = engine.execute(shipping_input, provider)
    decision = recovery_decision(result)

    assert decision.status == "needs_review"
    assert decision.next_action == "query_provider_by_idempotency_key"
    assert decision.safe_to_replay is True


def test_recovery_decision_for_success(shipping_input):
    def provider(data, key):
        return "LABEL-3001"

    engine = RetryEngine(sleep_fn=lambda seconds: None)
    result = engine.execute(shipping_input, provider)
    decision = recovery_decision(result)

    assert decision.status == "completed"
    assert decision.next_action == "no_action"
    assert decision.safe_to_replay is False


def test_replay_after_malformed_response_can_be_recovered(
    shipping_input,
):
    gateway = IdempotentShippingGateway()

    def malformed(data):
        return {"wrong": "response"}

    with pytest.raises(MalformedResponseError):
        gateway.create_label(
            shipping_input,
            "shipping-label:SHIP-3001",
            malformed,
        )

    def valid(data):
        return {"label_id": "LABEL-RECOVERED"}

    label = gateway.create_label(
        shipping_input,
        "shipping-label:SHIP-3001",
        valid,
    )

    assert label == "LABEL-RECOVERED"
    assert gateway.purchase_count == 1
