# Retired branches — 2026-07-26

Unmerged branches deleted during branch cleanup. Recorded so nothing is
actually lost: any of these can be restored with

```
git push origin <sha>:refs/heads/<branch-name>
```

All predate the EspoCRM decommission (2026-07-23) or the commission-surface
build, so their content is superseded rather than pending. They were agent
branches (devin/, cursor/, claude/, copilot/) or superseded prototypes.

| Last commit | Tip SHA | Branch | Commits ahead of main | Subject |
|---|---|---|---|---|
| 2026-05-07 | `a98dd91` | `devin/1778127842-review-fixes-v2` | 2 | resolve merge conflict: keep both sync CLI and new commands catalog from |
| 2026-05-07 | `db15787` | `cursor/nl-crm-routing-fallback` | 3 | Expand Hermes CRM intake and deployment tooling. |
| 2026-05-07 | `e8be529` | `devin/1778125923-bidirectional-sync` | 9 | fix: address remaining Devin Review bugs |
| 2026-05-08 | `3cc5c2d` | `hermes-workflow-integration-0fe38` | 1 | Title: Architecture documentation and OpenClaw separation planning |
| 2026-05-08 | `408a3fc` | `crm-form-updates` | 2 | Merge branch 'main' into crm-form-updates |
| 2026-05-08 | `52b0fd9` | `cursor/setup-dev-env-e652` | 3 | Merge branch 'main' into cursor/setup-dev-env-e652 |
| 2026-05-08 | `91cda15` | `devin/1778110035-openwebui-nl-crm` | 3 | Merge branch 'main' into devin/1778110035-openwebui-nl-crm |
| 2026-05-08 | `a0e5b83` | `performance-optimization-d1765` | 1 | Title: Add proper noun normalization staging and improve NL agent handli |
| 2026-05-08 | `ad84846` | `performance-optimization-tips-ecc18` | 1 | Title: Configure form for production deployment with environment variabl |
| 2026-05-08 | `c49fec5` | `devin/1778124931-nightly-changelog` | 9 | Merge branch 'main' into devin/1778124931-nightly-changelog |
| 2026-05-09 | `16a353d` | `cursor/auto-backend-config-8c73` | 6 | Add engineering guardrails and validation checks |
| 2026-05-13 | `4c8a0e0` | `claude/diagnose-problem-uQLwS` | 2 | fix(dispatcher): collapse 4 concatenated copies into one canonical class |
| 2026-05-13 | `993f181` | `claude/enable-external-code-execution-3iISl` | 5 | feat(slack): add archive_task_message button handler to Hermes |
| 2026-05-15 | `05e0de2` | `devin/1778823012-crm-mcp-training-profile` | 1 | docs: add CRM MCP training profile for AI agents |
| 2026-05-15 | `19e58df` | `devin/1778823327-hermes-training-espocrm` | 2 | docs: fix concatenation order and match symbol consistency |
| 2026-05-15 | `57f4869` | `devin/1778825025-fix-espo-mcp-tools` | 2 | fix: address Devin Review feedback |
| 2026-05-15 | `a61d6e1` | `devin/1778790455-hermes-shared-network` | 2 | fix: resolve merge conflict with main (keep espo-mcp sidecar) |
| 2026-05-16 | `cd054f9` | `claude/migrate-to-elestio-N0yCQ` | 3 | Remove remaining GitHub Pages references from docs |
| 2026-05-19 | `051b54f` | `claude/gmail-email-automation-c6lon` | 2 | fix(mcp): nginx sidecar proxy that injects /api/mcp into hermes-agent da |
| 2026-05-19 | `a8832c6` | `claude/fix-espocrm-skill-path-RVEdh` | 1 | feat(skills): add espocrm-field-reference and developer skills |
| 2026-05-20 | `5b71a4b` | `claude/agency-memory-crm-system-vtp2W` | 1 | feat(skills): agency memory + CRM intake/retrieval skill family |
| 2026-05-20 | `c0b500d` | `hermes/test-vps-access` | 4 | Restore core RSG operational skills |
| 2026-05-22 | `b1fb29e` | `claude/optimistic-newton-ZvNKA` | 1 | feat(slack): listen for Hermes blocks in #crm-entry |
| 2026-05-29 | `19d5a25` | `hermes/1780080932-nowcerts-field-alignment` | 2 | fix(sync): missed carrier→carrierName in lookup.py and commission_reconc |
| 2026-06-13 | `2329e17` | `hermes/ingestion-client-refactor` | 2 | docs(hermes): document full runtime platform in agent configs |
| 2026-06-13 | `9d34a4b` | `hermes/agency-persona` | 1 | feat(personas): add combined RSG agency SOUL persona |
| 2026-06-22 | `b77e008` | `hermes/openwebui-supermemory-bridge` | 3 | fix(webui): default hermes_tool valve to durable host-gateway route |
| 2026-06-24 | `e05f3d4` | `hermes/dry-refactor-policy-fields` | 4 | refactor: apply presets in nl_agent search and data_entry account search |
| 2026-06-26 | `f9ea7b4` | `hermes/commission-engine-prototype` | 4 | Add commission engine export snapshot for worksheet build |
| Last commit | Tip SHA | Branch | Commits | Why retired |
| 2026-07-10 | `545bb47` | `phase2-commission-ingest` | 16 | Momentum-era work; identical tree db8d643 shared by three names. Superseded by the NowCerts architecture. |
| 2026-07-08 | `5a1ae17` | `phase3-statement-reconciliation` | 15 | Momentum-era work; identical tree db8d643 shared by three names. Superseded by the NowCerts architecture. |
| 2026-07-10 | `65fdbf8` | `hermes/momentum-foundation` | 17 | Momentum-era work; identical tree db8d643 shared by three names. Superseded by the NowCerts architecture. |
| 2026-07-13 | `62f3cfe` | `renewals-pause-crons-2026-07-13` | 1 | Already applied — the live crontab matches main. |
| 2026-07-22 | `f43aa13` | `claude/fix-access-hermes-instance` | +28909 / -35655 vs main | Superseded architecture — main diverges by +28909/-35655 lines. Its insertions are old code main has since replaced, not unlanded work. |
| 2026-07-15 | `dbe5a2d` | `copilot/fix-merge-issue-on-main` | +28909 / -35655 vs main | Superseded architecture — main diverges by +28909/-35655 lines. Its insertions are old code main has since replaced, not unlanded work. |
| 2026-07-14 | `fd970d5` | `copilot/fix-sync-nowcerts-and-policies` | +28216 / -35660 vs main | Superseded architecture — main diverges by +28216/-35660 lines. Its insertions are old code main has since replaced, not unlanded work. |
| 2026-07-15 | `8e1e904` | `copilot/unable-to-access-hermes-instance` | +28907 / -35652 vs main | Superseded architecture — main diverges by +28907/-35652 lines. Its insertions are old code main has since replaced, not unlanded work. |
| 2026-07-21 | `4b94c16` | `feat/commissions-hub-ai` | +27346 / -19902 vs main | Superseded architecture — main diverges by +27346/-19902 lines. Its insertions are old code main has since replaced, not unlanded work. |
| 2026-07-21 | `9ed68be` | `feat/crm-intake-hub-ai` | +27346 / -19902 vs main | Superseded architecture — main diverges by +27346/-19902 lines. Its insertions are old code main has since replaced, not unlanded work. |
| 2026-07-21 | `42c017e` | `feat/intake-router` | +27301 / -19391 vs main | Superseded architecture — main diverges by +27301/-19391 lines. Its insertions are old code main has since replaced, not unlanded work. |
| 2026-07-21 | `f62a485` | `feat/intake-synthesis` | +27301 / -19391 vs main | Superseded architecture — main diverges by +27301/-19391 lines. Its insertions are old code main has since replaced, not unlanded work. |
| 2026-07-10 | `cd86a25` | `hermes/entity-field-admin` | +22237 / -40988 vs main | EspoCRM raw API proxy. Espo decommissioned 2026-07-23. |
| 2026-07-25 | `a97942e` | `hermes/fix-crashloop-and-retire-socket-mode` | +1234 / -13162 vs main | Identical trees. The one piece main lacked — resolve_room accepting a category name — was extracted in PR #244. guardrails.py already byte-identical to main; slack_notifier.py deletions would remove a facade main deliberately keeps. |
| 2026-07-26 | `64040f2` | `hermes/fix-talk-room-routing` | +0 / -0 vs main | Already merged (PR #244); identical to main. |
| 2026-07-25 | `57a1f4e` | `hermes/slack-to-nextcloud-talk` | +1234 / -13162 vs main | Identical trees. The one piece main lacked — resolve_room accepting a category name — was extracted in PR #244. guardrails.py already byte-identical to main; slack_notifier.py deletions would remove a facade main deliberately keeps. |
| 2026-07-09 | `b2ac7cb` | `intake-orchestrator-langgraph` | +29964 / -39970 vs main | Superseded architecture — main diverges by +29964/-39970 lines. Its insertions are old code main has since replaced, not unlanded work. |
