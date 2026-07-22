# Follow-ups

Tracked items that surfaced during Phases 1-3 of the rsg-intake pipeline
migration but weren't in-scope to fix at the time. Listed roughly by impact;
estimates assume a fresh Claude Code session with full repo context.

When tackling one, move it to a closed list at the bottom or delete the entry
once it's actually resolved (not just "in flight").

---

## 1. Silent CRM field drops + free-text-to-enum mapper gap

**Source:** Phase 3 Step 1 retry of Draft 1 (3D Pumps LLC), 2026-05-22.

**Symptom:** Several fields the synthesizer extracted are silently dropped on
EspoCRM writes — the queue worker reports `SUCCESS` but the field isn't
persisted. Confirmed for:

- `legalName`
- `accountType`
- `accountStatus`
- `tags`
- `producer`
- `industry` (enum-constrained: "Water Infrastructure / Specialty Contracting"
  isn't in the live enum, gets written as `NULL`)

**Bug class:** same as the LOB / phoneNumber validation issues fixed in commit
`42d2c79`. The synthesizer outputs human-readable strings; the
`agency_intake_approval` mapper field-maps them to Espo names; the live Espo
install rejects (or silently drops) anything that doesn't match the live
metadata.

**Impact:** Every CRM record written through this pipeline is missing fields
the synthesizer extracted. Real data quality erosion — renewals, commissions,
segmentation queries all degraded.

**Fix outline:**
1. Audit every field in `_map_account_to_espo`, `_map_contact_to_espo`,
   `_map_opportunity_to_espo` against live `entityDefs` metadata.
2. For every enum-constrained field, add an alias map (like `_LOB_ALIASES`)
   or — better — teach the synthesizer prompt to output canonical enum values
   directly by feeding it the field-reference enum list. Eliminates alias-map
   maintenance long-term.
3. Add round-trip regression tests: assert every alias value ∈ live enum
   (existing pattern from `test_every_LOB_alias_value_is_in_live_enum`).
4. Cover Account/Contact/Opportunity/Policy write paths.

**Estimate:** 3-5 hrs

---

## 2. Partial-token regression in approve_draft()

**Source:** End-of-Step-5 review, Phase 3.

**Symptom:** `APPROVE CRM ONLY` currently behaves identically to `APPROVE
ALL` — retrieval inserts fire regardless of which button was clicked. The
original button choice is preserved in `status_history` for audit, but not
acted on.

**Impact:** Zero today (Lamar uses `APPROVE ALL` exclusively). Tracked so it
gets fixed if/when partial-approve becomes useful (e.g., privacy filtering
calls out of RAG context).

**Fix outline:**
- Branch on `approval_token` in the worker's `approved` arc
  (`process_one_approved` + `process_writing_check`).
- Gate `_insert_retrieval_rows` on `token != 'APPROVE CRM ONLY'`.
- Gate `_enqueue_crm_writes` on `token != 'APPROVE SUPABASE ONLY'`.
- `APPROVE TASKS ONLY`: no task entity yet — leave as no-op with a clear
  comment.
- Persist the token where the worker can read it (currently lives in the
  latest `status_history` entry's `note`, which is fragile — consider a
  dedicated column or stash in `records_created.approval_token`).
- Add tests covering each branch.

**Estimate:** 1-2 hrs

---

## 3. Supabase: 91 tables without RLS

**Source:** Phase 1 advisors run on project `wibscqhkvpijzqbhjphg`.

**Symptom:** The advisor reported 91 tables in `public` schema with Row
Level Security disabled, meaning they're fully exposed to the `anon` and
`authenticated` roles via Supabase client libraries. Pre-existing hygiene
issue, not Phase-3-specific.

**Impact:** Anyone with the project's anon key can read or modify every
row in those tables. Risk level depends on how widely the anon key is
distributed.

**Fix outline:**
1. List the 91 tables (advisor output captured in Phase 1 results).
2. For each, decide:
   - Truly public (e.g., `naics_codes`, `gl_class_codes` reference data) →
     enable RLS with a permissive `for all to anon, authenticated using
     (true)` policy.
   - Server-side only → enable RLS with a `service_role` policy only
     (same pattern as `intake_submissions` in Phase 1).
   - User-scoped → enable RLS with policies keyed on `auth.uid()` /
     `auth.jwt()` claims.
3. Apply migrations.
4. Re-run advisors to confirm clean.

**Estimate:** 2-4 hrs

---

## 4. PROJECT-CONTEXT.md doc drift

**Source:** Phase 3 recon (2026-05-22) discovered the doc has diverged from
reality in three places.

**Drifts to fix:**

1. **"synthesizer → dedup probes → drafting"** — no dedup probes exist.
   The synthesizer declares a `duplicate_search` bundle in its output JSON,
   but nothing in the write path actually queries EspoCRM with it.
   `drafting` is a pure pass-through transition in the Phase 3 worker.
   Update the architecture diagram (Section 3) and the state-machine
   description (Section 6).

2. **"RAG inserts to `knowledge_chunks` (pgvector)"** — wrong table.
   The intake pipeline inserts to `client_entities` / `client_facts` /
   `client_notes` (plain text, no embeddings). `knowledge_chunks` is
   populated by a separate carrier-document ingestion path (Dify).
   Reword the architecture diagram, Section 3, and the table list in
   Section 4.

3. **`agency_intake_drafts` table not acknowledged** — the planning doc's
   model treats `intake_submissions` as the only state, but the existing
   Slack DM flow uses a parallel `agency_intake_drafts` staging table.
   This was a meaningful surprise during Phase 3 recon. Either add a note
   acknowledging the legacy table (and that it's being retired — see
   item #5 below) or remove the implication that `intake_submissions`
   was the only staging surface.

**Estimate:** 30 min

---

## 5. Retire agency_intake_drafts entirely

**Source:** Phase 3 migration directive ("Stop writing to
agency_intake_drafts entirely"), partially executed in Step 5 of Phase 3.

**Current state:** Phase 3 Step 5 rewrote `approve_draft()` to read
`intake_submissions` only. But the Slack DM `stage intake:` flow
(`hermes.commands.agency_intake.handle` → `stage_draft`) still writes to
`agency_intake_drafts`. Any draft created via Slack DM today has buttons
whose `value` is a `draft_id`, and clicking those buttons hits the new
`approve_draft` which looks up `intake_submissions` and fails with
"submission not found." So the Slack DM path is broken end-to-end, just
not formally retired.

**Trigger:** complete after Phase 5 cuts Cowork over to POST `/api/intake`
(at which point the legacy Slack DM path is genuinely unused).

**Fix outline:**
1. Take a backup snapshot of `agency_intake_drafts` (small table, all rows
   already in terminal state).
2. Port or remove `hermes.commands.agency_intake.handle`. If kept, it
   should insert into `intake_submissions` and return a short "queued —
   the async worker will post buttons in Slack momentarily" message
   instead of running synchronous synthesis.
3. Remove `stage_draft()` and the helpers it uses (`_load_draft`,
   `_update_draft_status` in `agency_intake_approval.py`).
4. Drop the `agency_intake_drafts` table via migration.
5. Remove the back-compat parameter naming (`draft_id` kwarg on
   `approve_draft`) — rename to `submission_id` everywhere.
6. Update `tests/test_agency_intake.py` to remove the StageDraftTests and
   any other tests that exercise the legacy table.

**Estimate:** 2-3 hrs

---

## 6. PAT scope tightening on hermes-elestio

**Source:** Phase 2.5 hardening session, 2026-05-22.

**Current state:** `/root/.git-credentials` on `hermes-elestio` holds a
classic GitHub PAT with `repo` scope (full read/write to
`googrlc/rsg-hermes`). The server only ever runs `git fetch origin`.
Phase 2.5's original plan was a read-only ed25519 deploy key, but that got
abandoned mid-execution when the wrong key kept getting registered on
GitHub. Worked around with the classic PAT as the pragmatic unblock.

**Impact:** Anyone with shell on `hermes-elestio` (root) can use that PAT
to push to the repo, not just pull. Blast radius is wider than needed.

**Fix outline (two acceptable paths):**

A. **Read-only deploy key (preferred).** Generate ed25519 keypair on the
   server at `/root/.ssh/id_ed25519_rsg_hermes_deploy`, register the
   public key on the repo's Deploy Keys page with "Allow write access"
   UNCHECKED, switch the remote to `git@github.com:googrlc/rsg-hermes.git`,
   add an SSH config entry pinning that key to `github.com`. Verify
   `git fetch` works, then delete `/root/.git-credentials` and revoke
   the current classic PAT in GitHub.

B. **Fine-grained PAT, `Contents: read`.** Generate a fine-grained PAT
   under owner `googrlc`, repo `googrlc/rsg-hermes`, permission
   `Contents: Read-only`. If `googrlc` is an org with PAT-approval
   enforcement enabled, approve the PAT in the org settings (this was the
   gotcha in Phase 2.5). Replace the current PAT in
   `/root/.git-credentials` via the same `git credential approve` flow.
   Revoke the old classic PAT.

**Estimate:** 30 min (assuming the GitHub-side hiccups from Phase 2.5
don't repeat)

---

## 7. Team comms hub — make Nextcloud Talk embed inline

**Source:** Team communication portal request, 2026-07-22.

**Current state:** The workspace rail now has a **Team** hub
(`hermes/webui/workspace.html`, `HUBS.team`) pointing at Nextcloud Talk
(`https://nextcloud-x6wle-u69864.vm.elestio.app/apps/spreed`). It ships as
`frameable:false`, so it renders the "opens in its own app" launcher card
(one click → Talk in a new tab) rather than an inline frame. Reason:
Nextcloud currently returns `X-Frame-Options: SAMEORIGIN` and no CSP
`frame-ancestors`, so any cross-origin frame is refused.

**Goal:** Let Talk render *inside* the workspace lane like CRM/Finance do.

**Fix outline:**

1. On the Elestio Nextcloud reverse proxy (openresty/nginx in front of the
   NC container), for the Talk/Files routes:
   - `proxy_hide_header X-Frame-Options;` (strip the SAMEORIGIN header), and
   - append `frame-ancestors 'self' https://hermes-gretch.tail1cbc83.ts.net`
     to the Content-Security-Policy. NC builds its CSP per-request with a
     nonce, so don't blindly `add_header` a second CSP — either use
     `headers-more` (`more_set_headers`) to rewrite, or set NC's own
     `overwrite*`/reverse-proxy config so it emits the frame-ancestors.
   - Scope the allow-list to the workspace origin only; do **not** open
     `frame-ancestors *`.
2. Nextcloud may also need `'trusted_domains'` / `overwritehost` correct so
   session cookies work in the frame; set `SameSite=None; Secure` on the
   session cookie if the browser drops it inside the iframe.
3. Once the header change is live, flip `HUBS.team.frameable` to `true` (or
   remove it and let the auto-probe decide) and confirm the lane renders.

**Also pending (product, not infra):**
- Create the general team channel in Talk so the hub lands somewhere useful.
- Per-client conversations: deep-link a Talk room per insured/policy from the
  CRM cockpit (a "Discuss" action that opens `…/call/<token>` for that client,
  auto-creating the room the first time). Needs a small NC Talk API call from
  the Hermes side to create/lookup the room by a stable external key.

**Estimate:** 1-2 hrs infra (proxy header + cookie), + a few hrs for the
per-client deep-link once the general channel is proven.

---

## Closed

_(none yet)_
