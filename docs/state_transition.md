
# Topic 10 — Shipping Label Workflow State Model

## Workflow States

| State | Description |
|---|---|
| created | Workflow run has been initialized |
| running | Shipping-label workflow is executing |
| retrying | A retryable failure occurred |
| completed | Shipping-label operation completed successfully |
| needs_review | Permanent failure or retry limit reached |
| cancelled | Workflow was cancelled before completion |

## Legal State Transitions

| Current state | Event | Next state |
|---|---|---|
| created | start | running |
| running | retry | retrying |
| retrying | retry_started | running |
| running | complete | completed |
| running | permanent_failure | needs_review |
| retrying | attempts_exhausted | needs_review |
| created | cancel | cancelled |
| running | cancel | cancelled |

## Workflow Identity

Each workflow run contains:

- A unique run ID.
- A business key derived from the shipment ID.
- An idempotency key derived from the same shipment ID.
- An attempt counter and maximum attempt limit.
- A validated shipping input.
- Structured events and traces.

## Idempotency Rule

Retries for the same shipment must reuse the same idempotency key.

The key is based on the shipment ID, not the retry attempt number. This prevents each retry from being treated as a new shipping-label purchase.

## Ownership and Dependencies

The shipping-label workflow owns its execution state and retry progress.

The shipping provider is an external dependency. It will be accessed through an injectable adapter in a later task.

## State Safety

Illegal state transitions raise a ValueError. Invalid shipping inputs and invalid retry limits are rejected before workflow execution.
