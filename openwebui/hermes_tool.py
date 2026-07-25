"""
title: Hermes CRM Assistant
author: RSG Engineering
author_url: https://github.com/googrlc/rsg-hermes
description: Agency CRM middleware — account lookups, pipeline and data-quality reports, and any Hermes command. Connects to the Hermes API server.
required_open_webui_version: 0.4.0
requirements: requests
version: 0.4.0
licence: MIT
"""

import asyncio
import requests
from pydantic import BaseModel, Field


class Tools:
    """Hermes CRM tools for Open WebUI.

    Connects to the Hermes REST API to dispatch commands for CRM lookups,
    reports, and data quality audits.

    The NowCerts↔EspoCRM sync tools this file used to expose (sync_nowcerts,
    sync_status, sync_conflicts, sync_errors, crm_changelog,
    sync_bidirectional, sync_crm_to_hub, sync_hub_to_nowcerts) were removed
    when EspoCRM was decommissioned — the dispatcher no longer routes any of
    those commands. Sync now runs as scheduled jobs, not on demand from a chat
    tool; see the executor flags in `hermes --help`.
    """

    class Valves(BaseModel):
        hermes_api_url: str = Field(
            default="http://hermes-api:8787",
            description="Base URL for the Hermes API server",
        )
        timeout: int = Field(
            default=120,
            description="Request timeout in seconds",
        )

    def __init__(self):
        self.valves = self.Valves()

    async def find_account(self, query: str, __event_emitter__=None) -> str:
        """
        Search for a client in the canonical book by name, FEIN, or other fields.
        :param query: The search query (e.g. account name, FEIN, DOT number)
        """
        return await asyncio.to_thread(self._dispatch, f"find account {query}")

    async def lookup(self, query: str, __event_emitter__=None) -> str:
        """
        Look up any CRM record — contacts, accounts, policies, opportunities.
        :param query: Natural language query (e.g. "what is the FEIN for Acme Corp")
        """
        return await asyncio.to_thread(self._dispatch, f"what {query}")

    async def data_quality(self, __event_emitter__=None) -> str:
        """
        Run a data quality audit across all CRM modules.
        Scans for missing required fields and returns a summary.
        """
        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": "Running CRM data quality audit...", "done": False}})
        result = await asyncio.to_thread(self._dispatch, "data quality")
        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": "Audit complete", "done": True}})
        return result

    async def pipeline_report(self, __event_emitter__=None) -> str:
        """
        Show the current sales pipeline summary with stage counts and values.
        """
        return await asyncio.to_thread(self._dispatch, "pipeline")

    async def hermes_command(self, command: str, __event_emitter__=None) -> str:
        """
        Send any command to Hermes. Use this for commands not covered by other tools.
        :param command: The Hermes command to run (e.g. "renewal queue", "stale leads", "my accounts")
        """
        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": f"Running: {command}", "done": False}})
        result = await asyncio.to_thread(self._dispatch, command)
        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": "Done", "done": True}})
        return result

    async def ping(self, __event_emitter__=None) -> str:
        """
        Check if Hermes is online.
        """
        return await asyncio.to_thread(self._dispatch, "ping")

    def _dispatch(self, command: str) -> str:
        """Send a command to the Hermes API and return the response."""
        url = f"{self.valves.hermes_api_url}/dispatch"
        try:
            resp = requests.post(
                url,
                json={"command": command},
                timeout=self.valves.timeout,
            )
            if resp.status_code == 200:
                data = resp.json()
                prefix = "" if data.get("ok") else "⚠️ "
                return f"{prefix}{data.get('message', 'No response')}"
            return f"Hermes API error ({resp.status_code}): {resp.text[:500]}"
        except requests.ConnectionError:
            return (
                f"Cannot connect to Hermes API at {self.valves.hermes_api_url}. "
                "Make sure the Hermes server is running: `hermes --api`"
            )
        except requests.Timeout:
            return "Hermes API request timed out."
        except Exception as exc:
            return f"Unexpected error calling Hermes API: {exc}"
