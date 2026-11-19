"""Injectable shipping-provider adapter and deterministic fake implementation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from retry_engine import PermanentError, RetryableError
from retry_mechanisms import ShippingLabelInput


class ProviderResponseError(PermanentError):
    """Raised when the fake provider returns an invalid response."""


def utc_timestamp() -> str:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def redact_sensitive_data(
    value: Any,
    secrets: tuple[str, ...] = (),
) -> Any:
    """Recursively redact sensitive fields and known secret values."""
    sensitive_names = {
        "password",
        "token",
        "api_key",
        "authorization",
        "secret",
        "credential",
    }

    if isinstance(value, dict):
        cleaned = {}

        for key, item in value.items():
            normalized_key = str(key).lower().replace("-", "_")

            if any(name in normalized_key for name in sensitive_names):
                cleaned[key] = "[REDACTED]"
            else:
                cleaned[key] = redact_sensitive_data(item, secrets)

        return cleaned

    if isinstance(value, list):
        return [redact_sensitive_data(item, secrets) for item in value]

    if isinstance(value, tuple):
        return [redact_sensitive_data(item, secrets) for item in value]

    if isinstance(value, str):
        cleaned = value

        for secret in secrets:
            if secret:
                cleaned = cleaned.replace(secret, "[REDACTED]")

        return cleaned

    return value


class FakeShippingProviderAdapter:
    """
    Injectable fake shipping provider.

    scripted_results controls the response sequence:
    - A non-empty string represents a successful label ID.
    - RetryableError represents a temporary provider failure.
    - PermanentError represents a non-retryable provider failure.
    - A dictionary represents a raw provider response and is validated.
    """

    def __init__(
        self,
        scripted_results: list[Any] | None = None,
        secrets: tuple[str, ...] = (),
    ) -> None:
        self.scripted_results = list(scripted_results or [])
        self.secrets = secrets
        self.call_count = 0
        self.events: list[dict[str, Any]] = []
        self.created_labels: dict[str, str] = {}

    def _record_event(
        self,
        event_name: str,
        shipping_input: ShippingLabelInput,
        idempotency_key: str,
        **details: Any,
    ) -> None:
        """Record a structured, redacted provider event."""
        event = {
            "timestamp": utc_timestamp(),
            "component": "fake_shipping_provider",
            "event": event_name,
            "shipment_id": shipping_input.shipment_id,
            "idempotency_key": idempotency_key,
            **details,
        }

        self.events.append(
            redact_sensitive_data(event, self.secrets)
        )

    def __call__(
        self,
        shipping_input: ShippingLabelInput,
        idempotency_key: str,
    ) -> str:
        """Make one simulated provider request."""
        self.call_count += 1

        self._record_event(
            "provider_request_started",
            shipping_input,
            idempotency_key,
            call_number=self.call_count,
        )

        # Replays with the same key return the original label.
        if idempotency_key in self.created_labels:
            label_id = self.created_labels[idempotency_key]

            self._record_event(
                "provider_request_replayed",
                shipping_input,
                idempotency_key,
                outcome="existing_label_returned",
            )

            return label_id

        response: Any

        if self.scripted_results:
            response = self.scripted_results.pop(0)
        else:
            response = {
                "label_id": f"LABEL-{shipping_input.shipment_id}"
            }

        if isinstance(response, Exception):
            self._record_event(
                "provider_request_failed",
                shipping_input,
                idempotency_key,
                error_type=type(response).__name__,
                outcome="failure",
            )
            raise response

        if isinstance(response, dict):
            label_id = response.get("label_id")
        else:
            label_id = response

        if not isinstance(label_id, str) or not label_id.strip():
            self._record_event(
                "provider_response_invalid",
                shipping_input,
                idempotency_key,
                outcome="invalid_response",
            )
            raise ProviderResponseError(
                "Shipping provider returned an invalid response"
            )

        self.created_labels[idempotency_key] = label_id

        self._record_event(
            "provider_request_succeeded",
            shipping_input,
            idempotency_key,
            outcome="success",
        )

        return label_id

    def get_status(self, idempotency_key: str) -> dict[str, Any]:
        """Expose a safe status lookup for an idempotency key."""
        label_id = self.created_labels.get(idempotency_key)

        return {
            "found": label_id is not None,
            "label_id": label_id,
            "status": "completed" if label_id else "not_found",
        }

    def get_trace(self) -> list[dict[str, Any]]:
        """Return a copy of the structured provider event trace."""
        return [dict(event) for event in self.events]


def run_adapter_demo() -> dict[str, Any]:
    """Demonstrate transient failure followed by provider success."""
    from retry_engine import RetryEngine, RetryPolicy

    from retry_mechanisms import ShippingLabelInput

    shipping_input = ShippingLabelInput(
        shipment_id="SHIP-4001",
        destination_postal_code="395007",
        package_weight_kg=2.5,
        service_code="EXPRESS",
    )

    adapter = FakeShippingProviderAdapter(
        scripted_results=[
            RetryableError("Temporary provider outage"),
            {"label_id": "LABEL-SHIP-4001"},
        ],
        secrets=("demo-secret-token",),
    )

    engine = RetryEngine(
        policy=RetryPolicy(
            max_attempts=3,
            initial_backoff_seconds=0,
            max_backoff_seconds=0,
        ),
        sleep_fn=lambda seconds: None,
    )

    result = engine.execute(shipping_input, adapter)

    return {
        "scenario": "transient failure followed by success",
        "provider_call_count": adapter.call_count,
        "result": result.to_dict(),
        "provider_trace": adapter.get_trace(),
        "provider_status": adapter.get_status(
            result.idempotency_key
        ),
    }


if __name__ == "__main__":
    import json

    print(json.dumps(run_adapter_demo(), indent=2))
