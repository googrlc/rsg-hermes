# Deluge ↔ Python fixtures

Creator has no in-repo runner. These cases are the contract
`hermes/renewals/desk.py` tests encode and Deluge must match.

## Stage guard (`stage_change_allowed`)

| current | proposed | producer_confirmed | allowed | reason |
|---|---|---|---|---|
| Identified | Identified | no | yes | unchanged |
| Identified | Outreach Sent | no | yes | advance |
| Identified | Quote Requested | no | no | cannot skip desk stages |
| Identified | Closed | no | no | cannot skip desk stages |
| Negotiating | Closed | no | yes | advance (Disposition required in Deluge) |
| Outreach Sent | Identified | no | no | moving backward requires producer confirmation |
| Outreach Sent | Identified | yes | yes | backward_with_producer |
| (blank) | Outreach Sent | no | yes | blank current treated as Identified |
| Identified | Not A Stage | no | no | unknown desk stage |

Closed without Disposition is refused in Deluge even on a legal one-step advance.

## Window bucket (`window_bucket`)

Today = 2026-08-18.

| expiration | LOB | bucket |
|---|---|---|
| 2026-08-10 | GL | past_due |
| 2026-09-01 | GL | 30 |
| 2026-10-01 | GL | 60 |
| 2026-11-10 | GL | 90 |
| 2026-09-01 | Personal Auto | personal |
| 2026-08-10 | Homeowners | past_due |
| (empty) | GL | none |

## Executor actions (`executor_action_ok`)

Allowed: `request_terms`, `prepare_options`, `client_follow_up`, `update_ams`.
Anything else is refused. `prepare_options` still enqueues (audit) but the
Hermes executor will not mutate the AMS.

Payload JSON keys (all required except `note` / `fields`):

```
action, renewal_id, policy_number, expected_result, zoho_queue_name
```

`expected_result` empty → refuse. Payload is built by Deluge, never typed.

## Approve (`approve.dg`)

Sets `Approved_By` = `zoho.loginuserid`, `Approved_At` = now, `Status` =
`queued`. Refuses non-renewal `Object_Type` and rows that are not
`needs_approval` (or `queued` with empty `Approved_By`). Does not call
NowCerts.

## Dismiss

Sets `Dismissed` true. Does not delete the Renewal or Renewal_Event.
Hermes overlay treats `dismissed=true` as a portal override keyed by
`policy_number` so `--renewal-refresh` keeps the row off the projection.
