
"""Workflow contract and state model for shipping-label retries."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now() -> datetime:
    """Return the current timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


class WorkflowState(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    RETRYING = "retrying"
    COMPLETED = "completed"
    NEEDS_REVIEW = "needs_review"
    CANCELLED = "cancelled"


class ShippingLabelInput(BaseModel):
    """Validated input required to purchase a shipping label."""

    model_config = ConfigDict(str_strip_whitespace=True)

    shipment_id: str = Field(min_length=1)
    destination_postal_code: str = Field(min_length=3, max_length=12)
    package_weight_kg: float = Field(gt=0, le=1000)
    service_code: str = Field(min_length=1)

    @field_validator("shipment_id", "service_code")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        if not value:
            raise ValueError("Value cannot be empty")
        return value


class WorkflowEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str
    event: str
    state: WorkflowState
    timestamp: datetime = Field(default_factory=utc_now)
    details: dict[str, Any] = Field(default_factory=dict)


class WorkflowTask(BaseModel):
    task_id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    owner: str
    dependency_ids: list[str] = Field(default_factory=list)
    status: str = "pending"


class WorkflowResult(BaseModel):
    run_id: str
    status: WorkflowState
    success: bool
    attempts: int
    label_id: str | None = None
    error_type: str | None = None
    message: str


class WorkflowTrace(BaseModel):
    trace_id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str
    timestamp: datetime = Field(default_factory=utc_now)
    action: str
    state: WorkflowState
    details: dict[str, Any] = Field(default_factory=dict)


class WorkflowRun(BaseModel):
    """Stateful execution record for one shipping-label workflow."""

    run_id: str = Field(default_factory=lambda: f"RUN-{uuid4().hex[:10].upper()}")
    business_key: str
    idempotency_key: str
    state: WorkflowState = WorkflowState.CREATED
    attempts: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1, le=10)
    input: ShippingLabelInput
    context: dict[str, Any] = Field(default_factory=dict)
    events: list[WorkflowEvent] = Field(default_factory=list)
    traces: list[WorkflowTrace] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    def record(self, event: str, **details: Any) -> None:
        """Append an event and update the run timestamp."""
        self.events.append(
            WorkflowEvent(
                run_id=self.run_id,
                event=event,
                state=self.state,
                details=details,
            )
        )
        self.updated_at = utc_now()

    def trace(self, action: str, **details: Any) -> None:
        """Append a structured trace event."""
        self.traces.append(
            WorkflowTrace(
                run_id=self.run_id,
                action=action,
                state=self.state,
                details=details,
            )
        )


LEGAL_TRANSITIONS = {
    (WorkflowState.CREATED, "start"): WorkflowState.RUNNING,
    (WorkflowState.RUNNING, "retry"): WorkflowState.RETRYING,
    (WorkflowState.RETRYING, "retry_started"): WorkflowState.RUNNING,
    (WorkflowState.RUNNING, "complete"): WorkflowState.COMPLETED,
    (WorkflowState.RUNNING, "permanent_failure"): WorkflowState.NEEDS_REVIEW,
    (WorkflowState.RETRYING, "attempts_exhausted"): WorkflowState.NEEDS_REVIEW,
    (WorkflowState.CREATED, "cancel"): WorkflowState.CANCELLED,
    (WorkflowState.RUNNING, "cancel"): WorkflowState.CANCELLED,
}


def transition(run: WorkflowRun, event: str) -> WorkflowRun:
    """Apply a legal state transition or reject an illegal one."""
    next_state = LEGAL_TRANSITIONS.get((run.state, event))

    if next_state is None:
        raise ValueError(
            f"Illegal workflow transition: {run.state.value} + {event}"
        )

    previous_state = run.state
    run.state = next_state
    run.record(
        event,
        previous_state=previous_state.value,
        next_state=next_state.value,
    )
    run.trace(
        "state_transition",
        event=event,
        previous_state=previous_state.value,
        next_state=next_state.value,
    )
    return run


def create_workflow_run(
    workflow_input: ShippingLabelInput,
    max_attempts: int = 3,
) -> WorkflowRun:
    """Create a run with a stable business and idempotency key."""
    if not 1 <= max_attempts <= 10:
        raise ValueError("max_attempts must be between 1 and 10")

    run = WorkflowRun(
        business_key=f"shipping-label:{workflow_input.shipment_id}",
        idempotency_key=f"shipping-label:{workflow_input.shipment_id}",
        input=workflow_input,
        max_attempts=max_attempts,
    )

    run.record(
        "workflow_created",
        business_key=run.business_key,
        idempotency_key=run.idempotency_key,
    )
    run.trace(
        "workflow_created",
        business_key=run.business_key,
        idempotency_key=run.idempotency_key,
    )
    return run


def run_topic_fixture() -> dict[str, Any]:
    """Run a deterministic Task 1 workflow example."""
    workflow_input = ShippingLabelInput(
        shipment_id="SHIP-1001",
        destination_postal_code="395007",
        package_weight_kg=2.5,
        service_code="EXPRESS",
    )

    run = create_workflow_run(workflow_input)
    transition(run, "start")

    return {
        "run_id": run.run_id,
        "business_key": run.business_key,
        "idempotency_key": run.idempotency_key,
        "state": run.state.value,
        "attempts": run.attempts,
        "max_attempts": run.max_attempts,
        "event_count": len(run.events),
        "trace_count": len(run.traces),
        "events": [event.model_dump(mode="json") for event in run.events],
    }


if __name__ == "__main__":
    import json

    print("=" * 60)
    print("TOPIC 10 — TASK 1: WORKFLOW CONTRACT")
    print("=" * 60)
    print(json.dumps(run_topic_fixture(), indent=2))
