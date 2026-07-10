# RSG EspoCRM MCP

Internal Docker-only MCP server for EspoCRM tools (Streamable HTTP transport).

The service is intended to run on the `hermes-shared` Docker network and be
registered in the Hermes Agent `config.yaml` as:

```yaml
mcp_servers:
  espocrm:
    url: http://rsg-espo-mcp:3000/mcp
    headers:
      Authorization: "Bearer ${MCP_ESPOCRM_API_KEY}"
```

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `ESPO_URL` | yes | EspoCRM base URL |
| `ESPO_API_KEY` | yes | EspoCRM API key |
| `ESPO_MCP_BEARER_TOKEN` | no | Bearer token for incoming requests |
| `ESPO_MCP_PORT` | no | Listening port (default `3000`) |
| `ESPO_MCP_MAX_LIST_SIZE` | no | Max records per list query (default `200`) |

## Tools (15)

### Read-only

| Tool | Description |
|---|---|
| `search_contacts` | Search contacts by name, email, or phone |
| `search_accounts` | Search accounts/companies by name |
| `search_leads` | Search leads by name, email, or company |
| `get_crm_record` | Retrieve any single record by entity + id |
| `list_open_tasks` | List open tasks with optional text filter |
| `get_opportunities` | List opportunities, filter by stage or text |
| `get_account_summary` | Account with related contacts, opps, and activity |
| `get_stream` | Activity stream (notes, updates) for a record |
| `pipeline_summary` | Aggregate open opportunities by stage |
| `recent_changes` | Records modified in the last N hours |

### Write

| Tool | Description |
|---|---|
| `create_note` | Add a note (stream post) to a record |
| `create_task` | Create a Task (renewal follow-up). Owner = Gretchen/Lamar; dedup first |
| `update_task` | Update a Task by id (e.g. mark Completed). Only passed fields change |
| `create_opportunity` | Create a renewal Opportunity (pipeline). Dedup first |
| `update_opportunity` | Update an Opportunity — advance stage / close Won or Lost |

**Governance:** `Task`/`Opportunity` fields are camelCase. `status`/`priority`
are free strings (this install customizes them, e.g. `Inbox`/`Cancelled`) — omit
to use the install default rather than risk a silently-dropped enum value. These
are the sanctioned *additive* CRM→AMS channels; never overwrite a populated field
and never create/edit a policy from here.
