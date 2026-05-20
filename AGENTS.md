# Hermes Repo Instructions

This repository supports the Hermes AI operations environment for Risk Solutions Group.

## Rules

- Never push directly to `main`.
- Always create a branch using `hermes/<short-description>`.
- Always show `git diff` before committing.
- Never edit `.env`, credentials, tokens, secrets, private keys, or production connection strings without explicit approval.
- Never run destructive commands such as `rm -rf`, forced resets, database drops, or production restarts without explicit approval.
- Prefer small, reviewable commits.
- Explain every change in plain language.
- Before pushing, summarize:
  - files changed
  - purpose of change
  - risk level
  - rollback steps
