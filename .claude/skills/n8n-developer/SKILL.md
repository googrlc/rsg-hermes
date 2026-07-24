---
name: n8n-developer
description: Developer skill for building and operating the self-hosted n8n workflow service that runs alongside Hermes. Use when authoring or debugging n8n workflows, wiring webhooks between n8n and `hermes-api`, managing the `rsg-n8n` container in `docker-compose.yml`, or rotating n8n credentials.
---

# n8n Developer Skill

For engineering work on the self-hosted n8n service used by RSG to glue
Hermes, the CRM, Slack, and external systems together.

## Service shape

`rsg-n8n` runs as a sibling container to Hermes via `docker-compose.yml`:

- Image: `n8nio/n8n:latest`
- Port: `5678` (host) → `5678` (container)
- Network: `hermes-shared` (external) — same network as `hermes-api`
- Volume: `n8n_data:/home/node/.n8n` (workflows + creds persist here)
- Public webhook base: `WEBHOOK_URL` env var (set in `docker-compose.yml`)
- Basic auth enabled (`N8N_BASIC_AUTH_ACTIVE=true`); credentials in
  `N8N_BASIC_AUTH_USER` / `N8N_BASIC_AUTH_PASSWORD`
- Encryption key: `N8N_ENCRYPTION_KEY` (rotating this re-encrypts all
  stored credentials — back up `n8n_data` first)
- Community packages allowed (`N8N_COMMUNITY_PACKAGES_ENABLED=true`)
- Timezone: `America/New_York`

## When to use this skill

- Authoring or modifying a workflow in the n8n UI and exporting it for
  version control.
- Wiring an n8n HTTP Request node to `hermes-api` (use the
  `hermes-shared` Docker DNS name, not localhost).
- Adding or rotating credentials.
- Bumping the n8n image, adjusting env vars, or changing the compose
  service shape.
- Debugging webhook delivery (production vs test URLs, encryption-key
  mismatches, queue mode).

## Working rules

1. **Service-to-service calls stay on `hermes-shared`.** Inside the n8n
   container, address Hermes as `http://hermes-api:8787`. Don't hardcode
   the host or the elestio public URL.
2. **Public webhooks use `WEBHOOK_URL`.** Anything that needs to be
   reachable from outside the Docker network must go through that base,
   which currently points at the elestio VM on `:5678`.
3. **Encryption key is load-bearing.** Never change `N8N_ENCRYPTION_KEY`
   without exporting+re-importing credentials, or all stored creds become
   unreadable. Treat it like a database password.
4. **Don't commit secrets.** `N8N_*` env values come from `.env` (which is
   gitignored). Reference them via `${VAR}` in compose only.
5. **Back up before destructive ops.** Snapshot the `n8n_data` volume
   before image upgrades, encryption-key rotation, or wiping workflows:
   `docker run --rm -v rsg-hermes_n8n_data:/data -v "$PWD":/backup
   busybox tar czf /backup/n8n_data.tgz -C /data .`
6. **Pin the image when stabilizing.** `n8nio/n8n:latest` is fine for dev
   but pin to a specific tag before relying on a workflow in production
   (e.g. `n8nio/n8n:1.x.y`).

## Common tasks

### Bring the service up locally
```
docker compose up -d n8n
# UI:  http://localhost:5678  (basic auth from .env)
```

### Call hermes-api from an n8n HTTP node
- URL: `http://hermes-api:8787/<endpoint>`
- Auth: whatever Hermes expects (bearer token from `.env`, NOT the n8n
  basic-auth credentials)

### Export a workflow for git
n8n UI → workflow → "..." → Download. Commit the JSON under a workflows/
directory (create one if it doesn't exist) so the workflow is
reproducible. Strip credential IDs — store creds in n8n, not in the JSON.

### Rotate basic auth
1. Update `N8N_BASIC_AUTH_USER` / `N8N_BASIC_AUTH_PASSWORD` in `.env`.
2. `docker compose up -d n8n` to recreate the container.

## Pitfalls

- Using `localhost` inside n8n nodes to reach Hermes — there's no host
  network on this service. Use the `hermes-shared` DNS name.
- Changing `WEBHOOK_URL` without updating any external systems that have
  registered webhooks at the old URL.
- Installing a community package and then bumping the image: community
  packages are reinstalled on container start; check the n8n logs after
  upgrade.
- Editing workflows directly in production without exporting first — the
  only durable backup is the `n8n_data` volume.

## References

- n8n docs: https://docs.n8n.io/
- Hermes API entry: `hermes/api.py`
- Compose service: `docker-compose.yml` (the `n8n:` block)
