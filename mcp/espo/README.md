# RSG EspoCRM MCP

Internal Docker-only MCP server for read-only EspoCRM tools.

The service is intended to run on the `hermes-shared` Docker network and be registered in Hermes Agent as:

```text
http://rsg-espo-mcp:3000/mcp
```

Environment variables:

- `ESPO_URL`
- `ESPO_API_KEY`
- `ESPO_MCP_BEARER_TOKEN`
- `ESPO_MCP_PORT`
- `ESPO_MCP_MAX_LIST_SIZE`

Initial tools are read-only:

- `search_contacts`
- `search_accounts`
- `get_crm_record`
- `list_open_tasks`
