# Data contract (D1)

The machine-readable contract is served at `GET /v1/contract` (JSON Schema generated from `app/contracts.py`, the single source of
truth that the service itself enforces). Both caller and service are bound by it.

## `POST /v1/complaints`  (synthetic complaints only)

| Field | Type | Rule |
|---|---|---|
| `investor_ref` | string | required, `^INV[0-9]{6}$` (synthetic reference) |
| `complaint_text` | string | required, 20-4000 chars; must not contain PAN-, Aadhaar-, phone- or account-number-like values |
| `channel` | enum | required: `web` `email` `phone` `letter` |
| `complaint_date` | date (ISO) | required, not in the future |
| `transaction_date` | date | optional |
| `amount_inr` | number | optional, > 0 and <= 100,000,000 |
| `entity_name` | string | optional, 2-120 chars, same identifier rule as the text |
| `prior_complaint_to_entity` | bool | default false |
| `prior_complaint_date` | date | optional |
| `pending_in_court` | bool | default false |
| (anything else) | | rejected: `extra_forbidden` |

Cross-field rules: `transaction_date` <= `complaint_date`, and not more than 3 years before it; `prior_complaint_date` is required if
and only if `prior_complaint_to_entity` is true, must not be after `complaint_date` nor before `transaction_date`.

Responses: `202 {run_id, status, status_url, cache_hit, guardrails}` | `422 {error, quarantine_id, agent_started:false, errors:[{field,message,type}]}`
| `429 {error, retry_after_s}` + `Retry-After`. Rejected requests are stored in the quarantine (`GET /v1/quarantine`) with their field-level errors; **no agent run starts and nothing is queued**.

## Other interfaces
* `GET /v1/runs/{id}` -> `{status: queued|running|completed|failed, result?}`; `result` holds `determination`, `citations` (resolved to retrieved text), `verification`, `prompt_version`, `degraded`, `usage` (tokens and cost per step), `latency`.
* `GET /v1/runs/{id}/trace` -> every event of the run: each model call (step, prompt version + hash, tokens, cost, latency, provider, degraded), each tool call (arguments, passages with scores), each verification (issues), escalation.
* `POST /v1/runs/{id}/feedback` `{satisfactory: bool, reason: string<=500}`; `reason` is required when `satisfactory` is false.
* Determination: `route` in `assign-to-entity | refer-to-other-regulator | not-maintainable | escalate-to-human`; `escalation.reason` in `split_regulators | entity_out_of_corpus | insufficient_evidence | verification_failed | limits_exceeded | provider_unavailable | output_filter_blocked`. The service never disposes of a complaint.
