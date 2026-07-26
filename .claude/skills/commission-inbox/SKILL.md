---
name: commission-inbox
description: >
  Slack #commission-inbox auto-ingest for carrier commission statements and rate
  sheets (build spec §9/§10). Poll the channel, dedupe, classify, parse to a
  staging batch, post a review card, and on Lamar's approval commit to the money
  tables, reconcile, and archive the original to Nextcloud. Approval gate is
  mandatory — money data never auto-commits.
---

# Commission Inbox (Slack-drop auto-ingest)

> **Status check, verified 2026-07-26.** The logic below is sound but the
> plumbing is only partly live. Before promising an ingest:
> - **Ingest has barely run.** `commission_statements` = 1 row,
>   `commission_ingest_batches` = 3. `commission_reconciliation` = **0**.
> - **`#commission-inbox` (`C0BFXEZL1BP`) is not in `slack_registry`.** That
>   table holds three different channels (`#rsg-hermes-commission-audit`,
>   `#rsg-hermes-project85-renewals`, `#rsg-hermes-operations`) and its names are
>   known-orphaned pending a rename. Confirm the channel before polling it.
> - **The Slack MCP may not be authorized** in a given session. If it isn't, say
>   so — don't silently skip the poll and report a clean run.
> - **Nextcloud archive endpoint** must be set (`NEXTCLOUD_URL`); the old Elestio
>   host is gone. Archiving is not optional — an uncommitted statement with no
>   archived original is unauditable.

## Role in the pipeline
Slack = the transient doorway (Lamar drops files from his phone) · Supabase = the
queryable numbers · Nextcloud = the file archive (audit/dispute) · Onyx = optional
read-only search. **Onyx is NOT a parser** and is never in the write path.

This skill is the Hermes side of the commission system built in
`rsg-commission-tracker` (Supabase `wibscqhkvpijzqbhjphg`). The React app is the
operate/review UI; this skill is the scheduled ingest engine.

## Channel & systems
- Slack channel: **#commission-inbox** `C0BFXEZL1BP` (Slack MCP).
- Supabase project `wibscqhkvpijzqbhjphg` (Supabase MCP — session_user `postgres`).
- Parsers live in the repo: `rsg-commission-tracker/src/parsers/`
  (`progressive_v1`, `next_v1`; registry in `registry.ts`). Run one via
  `scripts/load-*.ts` patterns or `npx tsx`.
- Nextcloud archive: `nextcloud_archive.py` (this dir). **Endpoint TBD** — the old
  Elestio host is dead; set `NEXTCLOUD_URL` first.

## Run cadence
Scheduled (not an always-on listener): **9am / 1pm / 5pm ET**, with the end-of-day
summary on the 5pm run. Each run is one pass of the flow below. Manual runs are
fine any time ("check the inbox").

## Flow (per run)
1. **Read** #commission-inbox since the last run (`slack_read_channel`). For each
   message with a file attachment, `slack_read_file` to get bytes/text.
2. **Dedupe** by SHA-256 of the file content. Skip if `content_hash` already exists
   in `commission_ingest_batches` (durable guard) or in
   `~/.hermes/commission_ingest_state.json` (fast local guard — append after each run).
3. **Classify** (spec §10): per-policy rows with dates + amounts → `statement`;
   a rate grid by LOB with no per-policy rows → `rate_sheet`; both present → `both`.
   Low confidence → ask in the review card, don't guess.
4. **Identify carrier + parser**: filename + content sniff → `detectParserKey`
   (repo). Map raw carrier name → canonical via `carrier_alias_map`. Unknown format
   → create the batch as `needs_mapping` and post a card asking for a parser; do NOT
   invent columns.
5. **Parse** to normalized rows (repo parser). Insert one
   `commission_ingest_batches` row (`ingest_status='pending_review'`, kind, parser_key,
   extraction_method, is_ocr, parsed totals, canonical_carrier, slack ids) and the
   rows into `commission_transactions_staging` (statements) or `carrier_rate_intake`
   (rate sheets, spec §10). **Nothing touches commission_transactions yet.**
6. **Cross-check** parsed vs carrier-stated totals when the statement carries a
   summary/total. If none (e.g. NEXT), say so — don't fabricate a match.
7. **Post the review card** to #commission-inbox: carrier, row count, parsed vs
   stated, extraction method, `⚠️ OCR — spot-check` if OCR, and flags (statement-only
   policies, as-earned partials, missing cross-check, archive status). Include the
   short batch id.
8. **Await approval.** On **approve**: FIRST archive the original (step 9) and write
   the returned path into `commission_ingest_batches.archive_url`, THEN
   `select commit_ingest_batch('<batch_id>')` (creates the statement carrying that
   archive_url, moves rows to commission_transactions, runs `reconcile_carrier`), then
   reply with the reconciliation delta. On **reject** →
   `select reject_ingest_batch('<batch_id>','reason')`; money tables untouched.
   Rate-sheet batches are approved through the Rates tab / `carrier_rate_intake` flow,
   not `commit_ingest_batch`.
9. **Archive** the original to Nextcloud (creds+URL in 1P `Nextcloud WebDAV`; host is
   `nextcloud-x6wle-u69864.vm.elestio.app`, user `root`, app-password):
   `nextcloud_archive.py --file <local> --carrier "<canonical>" --date <stmt_date> --kind statement|rate_sheet`
   → prints the archive path (`Commission Statements/{Carrier}/{YYYY}/{date}_{file}`).
   Set it on the batch before commit. Rate sheets → `--kind rate_sheet`.

## End-of-day summary (5pm run, post to #commission-inbox)
Files received today, batches committed, **batches still pending approval** (nag so
nothing rots), anomalies/flags, and refreshed book totals from `v_book_summary`.

## Payment-model nuance (don't cry wolf)
Check `carrier_commission_profile.payment_model` before interpreting a delta:
- **as_earned** (e.g. NEXT): a monthly statement is a *partial* of the full-term
  expected. Partial ≠ short. Say "as-earned partial", not "underpaid".
- **advance** (e.g. Progressive): commission paid up front; a cancel is a realized
  clawback (negative line).
- **confirm_on_upload**: model unknown — flag it, don't compute cancel math.

## Guardrails
Approval gate mandatory · OCR batches tagged · idempotent dedupe by content hash ·
never auto-delete a Slack file · read-only from Slack · idempotent writes to Supabase
(`commit_ingest_batch` clears any prior load of the same source_file first).

## State
`~/.hermes/commission_ingest_state.json` — `{ "<sha256>": {"file","batch_id","ts"} }`.
Append after each processed file; consult at step 2.
