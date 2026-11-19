"""Tests for the injectable fake shipping-provider adapter."""

import pytest

from retry_adapters import (
    FakeShippingProviderAdapter,
    ProviderResponseError,
    redact_sensitive_data,
)
from retry_engine import PermanentError, RetryableError
from retry_mechanisms import ShippingLabelInput


@pytest.fixture
def shipping_input():
    return ShippingLabelInput(
        shipment_id="SHIP-4001",
        destination_postal_code="395007",
        package_weight_kg=2.5,
        service_code="EXPRESS",
    )


def test_fake_provider_returns_successful_label(shipping_input):
    adapter = FakeShippingProviderAdapter()

    label_id = adapter(shipping_input, "shipping-label:SHIP-4001")

    assert label_id == "LABEL-SHIP-4001"
    assert adapter.call_count == 1
    assert adapter.get_status("shipping-label:SHIP-4001") == {
        "found": True,
        "label_id": "LABEL-SHIP-4001",
        "status": "completed",
    }


def test_transient_failure_can_be_simulated(shipping_input):
    adapter = FakeShippingProviderAdapter(
        scripted_results=[RetryableError("Temporary outage")]
    )

    with pytest.raises(RetryableError):
        adapter(shipping_input, "shipping-label:SHIP-4001")

    assert adapter.call_count == 1
    assert adapter.get_status("shipping-label:SHIP-4001")["found"] is False


def test_permanent_failure_can_be_simulated(shipping_input):
    adapter = FakeShippingProviderAdapter(
        scripted_results=[PermanentError("Invalid shipment")]
    )

    with pytest.raises(PermanentError):
        adapter(shipping_input, "shipping-label:SHIP-4001")

    assert adapter.call_count == 1


def test_malformed_response_is_rejected(shipping_input):
    adapter = FakeShippingProviderAdapter(
        scripted_results=[{"unexpected": "response"}]
    )

    with pytest.raises(ProviderResponseError):
        adapter(shipping_input, "shipping-label:SHIP-4001")

    assert adapter.get_status("shipping-label:SHIP-4001")["found"] is False


def test_duplicate_request_returns_existing_label(shipping_input):
    adapter = FakeShippingProviderAdapter()
    key = "shipping-label:SHIP-4001"

    first = adapter(shipping_input, key)
    second = adapter(shipping_input, key)

    assert first == second
    assert first == "LABEL-SHIP-4001"
    assert adapter.call_count == 2
    assert len(adapter.created_labels) == 1

    events = adapter.get_trace()
    assert any(
        event["event"] == "provider_request_replayed"
        for event in events
    )


def test_different_idempotency_keys_create_separate_labels(shipping_input):
    adapter = FakeShippingProviderAdapter()

    first = adapter(shipping_input, "key-one")
    second = adapter(shipping_input, "key-two")

    assert first == second
    assert len(adapter.created_labels) == 2


def test_status_lookup_for_unknown_key(shipping_input):
    adapter = FakeShippingProviderAdapter()

    status = adapter.get_status("unknown-key")

    assert status == {
        "found": False,
        "label_id": None,
        "status": "not_found",
    }


def test_secret_fields_are_redacted():
    data = {
        "shipment_id": "SHIP-4001",
        "api_key": "private-api-key",
        "nested": {
            "authorization": "Bearer private-token",
            "status": "completed",
        },
        "message": "Request used private-api-key",
    }

    cleaned = redact_sensitive_data(
        data,
        secrets=("private-api-key", "private-token"),
    )

    assert cleaned["api_key"] == "[REDACTED]"
    assert cleaned["nested"]["authorization"] == "[REDACTED]"
    assert cleaned["nested"]["status"] == "completed"
    assert "private-api-key" not in cleaned["message"]
    assert "private-token" not in str(cleaned)


def test_provider_trace_contains_structured_events(shipping_input):
    adapter = FakeShippingProviderAdapter()

    adapter(shipping_input, "shipping-label:SHIP-4001")

    trace = adapter.get_trace()

    assert len(trace) == 2
    assert trace[0]["event"] == "provider_request_started"
    assert trace[1]["event"] == "provider_request_succeeded"

    for event in trace:
        assert "timestamp" in event
        assert "component" in event
        assert "shipment_id" in event
        assert "idempotency_key" in event
