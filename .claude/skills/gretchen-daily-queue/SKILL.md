---
name: gretchen-daily-queue
description: >
  Plain-English daily task queue for Gretchen (Personal Lines Specialist), built
  from the Supabase `agency_crm_tasks` table via `hermes/operations/team_queue.py`
  and delivered to the Nextcloud Talk team chat plus the CRM Tasks view. Runs
  weekdays 8:30am ET, and on "gretchen queue", "gretchen tasks", "what does
  gretchen have today", "gretchen's list", or "queue for gretchen". Gretchen-facing:
  zero jargon, zero insurance-speak, zero IDs — plain action steps only. Frees Lamar
  from being Gretchen's task dispatcher.
---

# Gretchen Daily Queue

Gretchen should start every day knowing exactly what to do, in what order,
without asking Lamar.

> **Rewritten 2026-07-26.** The prior version resolved Gretchen's user id out of
> EspoCRM (`GET {espo_base_url}/api/v1/User?...`), pulled her tasks from Espo
> Task entities, and posted to a Slack DM. **EspoCRM is retired**, and task
> notifications moved to Nextcloud Talk. None of the old steps run.

---

## Where the queue actually comes from

**Table:** `agency_crm_tasks` (Supabase). **Code:**
[hermes/operations/team_queue.py](../../../hermes/operations/team_queue.py) —
use it rather than re-deriving the logic.

| Column | Note |
|---|---|
| `title` | What to do. This is the line Gretchen reads. |
| `assigned_to_email` | **Must be a `.net` address** — FK to `agency_crm_users`. |
| `status` | Open = anything not in `completed` / `cancelled` / `canceled` / `done`, stored lowercase. |
| `priority` | `high` / `medium` / `low`. |
| `due_at` | Nullable — and often null. |
| `case_id` | What the task hangs off. Required at create time. |
| `description`, `nowcerts_task_id`, `sync_status` | Detail + AMS linkage. |

`list_open_tasks()` returns open tasks sorted most-urgent-first, with undated
tasks last. `assignee_label()` renders "Gretchen" / "Lamar" from the email —
**never render a raw address to her.**

The one allowed write-back is `complete_task()` — mark a task completed. Nothing
else in this skill writes.

### Live picture (2026-07-26)

18 tasks, **all 18 still `not_started`** — nothing has been completed through
this system yet:

| Owner | Priority | Count | No due date |
|---|---|---|---|
| Gretchen | medium | 10 | 5 |
| Gretchen | high | 1 | 0 |
| Lamar | high | 7 | 0 |

So Gretchen has **11 open tasks, 5 with no due date**. A queue sorted purely by
due date buries nearly half her list. Surface the undated ones explicitly.

---

## Delivery

**Primary: Nextcloud Talk team chat** —
[hermes/operations/task_notify.py](../../../hermes/operations/task_notify.py).
The room comes from `NEXTCLOUD_TALK_TOKEN`; the `hermes` Nextcloud user must be
a participant. **If the token is unset, posting is silently skipped** — a
"successful" run can deliver nothing. Verify the post landed before reporting
that it did.

**Secondary: the CRM Tasks view** at `/cockpit#tasks`. `task_notify` appends an
"open in CRM ↗" link when `HERMES_PUBLIC_BASE_URL` is set.

Slack is **not** the delivery path for Gretchen's queue any more.

---

## Format

Plain English. No jargon, no insurance-speak, no IDs, no field names, no system
navigation.

```text
👋 Good morning Gretchen! Here's your list for {day}, {date}:

🔴 DO FIRST ({n})
1. {plain-English action} — {client}
2. {plain-English action} — {client}

🟡 DO TODAY ({n})
3. {plain-English action}

📋 NO DUE DATE — pick these up when you have room ({n})
4. {plain-English action}

📊 {n} open · {n} overdue · {n} with no due date
```

Light day:

```text
👋 Good morning Gretchen! Light day today — here's what's on deck:
{list}
```

Nothing at all:

```text
👋 Good morning Gretchen! Queue is clear — no open tasks right now.
Check with Lamar if you need something to work on.
```

**Never leave her without a response.** A blank queue still gets a message.

### Translating a task into her language

| Don't write | Write |
|---|---|
| "Dec page needed" | "Get the policy summary document and email it to the client" |
| "Obtain loss runs for remarket" | "Ask their current insurance company for their claims history" |
| "Task 3f2a… on case 91b…" | "Follow up with the Nelsons about their home quote" |
| "Bind confirmation pending" | "Check whether the coverage actually started" |

---

## Notify Lamar

One line, and **only when there are urgent or overdue items**:

```text
📋 Gretchen's queue posted: {n} tasks ({n} urgent)
```

If the day is light, skip the ping entirely.

---

## Error handling

| Situation | Do this |
|---|---|
| Supabase unreachable | Post: "Good morning Gretchen! The task system is down this morning — please check with Lamar for today's priorities." Report the error to `#systems-check`. |
| `NEXTCLOUD_TALK_TOKEN` unset | The post is silently dropped. **Don't report success.** Say the queue was built but not delivered, and flag the missing config. |
| No tasks found | Post the "queue is clear" message. |
| A task has no `.net` assignee | It could not have been created — the FK to `agency_crm_users` rejects `.com`. Flag it rather than guessing the owner. |

---

## Known gaps

- **Zero tasks have ever been completed** through this system (18/18
  `not_started`). Treat the completion path as unproven.
- **5 of Gretchen's 11 tasks have no due date**, so urgency ordering is partly
  guesswork. Prompting her to date them is worth more than reordering them.
- **The queue is task-only.** It does not pull expiring personal-lines policies;
  that signal lives in `retention-risk-scout` and is not joined here. The old
  version claimed to merge them. It didn't, and this one doesn't.

## Notes

- Schedule: weekdays 8:30am ET.
- Gretchen-facing output is plain English, always. That is the hard rule of this
  skill — everything else is mechanics.
- Medicare clients are excluded from automated client touches; never
  age-reference a client in writing.

## References

- `hermes/operations/team_queue.py` — the queue builder and the one write-back
- `hermes/operations/task_notify.py` — Talk delivery + CRM deep link
- `retention-risk-scout` — what's at risk (not yet merged into this queue)
- `renewal-desk` — executes renewal actions
