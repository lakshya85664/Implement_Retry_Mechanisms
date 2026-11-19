
import pytest
from pydantic import ValidationError

from retry_mechanisms import (
    ShippingLabelInput,
    WorkflowState,
    create_workflow_run,
    transition,
)


@pytest.fixture
def sample_input():
    return ShippingLabelInput(
        shipment_id="SHIP-1001",
        destination_postal_code="395007",
        package_weight_kg=2.5,
        service_code="EXPRESS",
    )


def test_create_workflow_run(sample_input):
    run = create_workflow_run(sample_input)

    assert run.state == WorkflowState.CREATED
    assert run.attempts == 0
    assert run.max_attempts == 3
    assert run.business_key == "shipping-label:SHIP-1001"


def test_legal_transition_created_to_running(sample_input):
    run = create_workflow_run(sample_input)

    transition(run, "start")

    assert run.state == WorkflowState.RUNNING
    assert run.events[-1].event == "start"


def test_illegal_transition_is_rejected(sample_input):
    run = create_workflow_run(sample_input)

    with pytest.raises(ValueError, match="Illegal workflow transition"):
        transition(run, "complete")

    assert run.state == WorkflowState.CREATED


def test_retry_state_transitions(sample_input):
    run = create_workflow_run(sample_input)

    transition(run, "start")
    transition(run, "retry")
    assert run.state == WorkflowState.RETRYING

    transition(run, "retry_started")
    assert run.state == WorkflowState.RUNNING


def test_completion_transition(sample_input):
    run = create_workflow_run(sample_input)

    transition(run, "start")
    transition(run, "complete")

    assert run.state == WorkflowState.COMPLETED


def test_permanent_failure_transition(sample_input):
    run = create_workflow_run(sample_input)

    transition(run, "start")
    transition(run, "permanent_failure")

    assert run.state == WorkflowState.NEEDS_REVIEW


def test_invalid_shipping_input_is_rejected():
    with pytest.raises(ValidationError):
        ShippingLabelInput(
            shipment_id="SHIP-1002",
            destination_postal_code="395007",
            package_weight_kg=-1,
            service_code="EXPRESS",
        )


def test_attempt_limit_is_bounded(sample_input):
    with pytest.raises(ValueError, match="max_attempts must be between 1 and 10"):
        create_workflow_run(sample_input, max_attempts=100)


def test_idempotency_key_is_stable(sample_input):
    first_run = create_workflow_run(sample_input)
    second_run = create_workflow_run(sample_input)

    assert first_run.idempotency_key == second_run.idempotency_key
    assert first_run.business_key == second_run.business_key


def test_trace_is_recorded_for_transition(sample_input):
    run = create_workflow_run(sample_input)

    transition(run, "start")

    assert len(run.traces) >= 2
    assert run.traces[-1].action == "state_transition"
