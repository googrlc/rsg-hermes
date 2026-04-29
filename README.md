# Hermes (rsg-hermes)

Python coordinator for **EspoCRM** over the REST API: REPL / one-shot CLI, optional **Slack** Socket Mode, and pluggable commands (lookup, data entry, revenue views).

The **EspoCRM customization repo** (PHP metadata, hooks, field reference) stays separate: [googrlc/rsg-espocrm](https://github.com/googrlc/rsg-espocrm). This repo only talks to Espo via HTTP; it does not ship Espo source or custom PHP.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env   # then set ESPO_URL and ESPO_API_KEY
```

## Run

```bash
hermes --ping
hermes --kpi
hermes --slack        # needs SLACK_* tokens in .env
hermes 'What is Jane phone'
```

See `docs/espocrm.md` for how this relates to the RSG EspoCRM repo.
