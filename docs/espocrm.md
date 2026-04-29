# EspoCRM and this repo

**Hermes** (`hermes/`) is the only application code here. It uses Espo’s **REST API** (`ESPO_URL` + `ESPO_API_KEY`).

RSG-specific **EspoCRM configuration** (entity defs, hooks, field-reference docs, deploy scripts) lives in the dedicated repo:

- **https://github.com/googrlc/rsg-espocrm**

You can copy snippets from that repo’s `field-reference/` into Hermes command handlers as you align field names—without vendoring the whole Espo tree into **rsg-hermes**.
