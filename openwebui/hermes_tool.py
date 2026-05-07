"""
title: Hermes CRM Assistant
author: RSG Engineering
author_url: https://github.com/googrlc/rsg-hermes
description: EspoCRM coordination middleware — sync NowCerts, lookup accounts, run data quality audits, and more. Connects to the Hermes API server.
required_open_webui_version: 0.4.0
requirements: requests
version: 0.2.0
licence: MIT
"""

import asyncio
import requests
from pydantic import BaseModel, Field


class Tools:
    """Hermes CRM tools for Open WebUI.

    Connects to the Hermes REST API to dispatch commands for CRM operations,
    NowCerts sync, data quality audits, and lookups.
    """

    class Valves(BaseModel):
        hermes_api_url: str = Field(
            default="http://localhost:8484",
            description="Base URL for the Hermes API server",
        )
        timeout: int = Field(
            default=120,
            description="Request timeout in seconds (sync operations can take time)",
        )

    def __init__(self):
        self.valves = self.Valves()

    async def sync_nowcerts(self, __event_emitter__=None) -> str:
        """
        Trigger a full NowCerts → EspoCRM sync for Insured → Account.
        Pulls from NowCerts, stages in Supabase, matches identities, and writes to EspoCRM.
        """
        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": "Starting NowCerts sync...", "done": False}})
        result = await asyncio.to_thread(self._dispatch, "sync nowcerts")
        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": "Sync complete", "done": True}})
        return result

    async def sync_nowcerts_dry_run(self, __event_emitter__=None) -> str:
        """
        Preview NowCerts → EspoCRM sync without writing to EspoCRM.
        Shows what would be created/updated without making changes.
        """
        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": "Running dry-run sync...", "done": False}})
        result = await asyncio.to_thread(self._dispatch, "sync nowcerts dry-run")
        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": "Dry run complete", "done": True}})
        return result

    async def sync_status(self, __event_emitter__=None) -> str:
        """
        Show recent NowCerts sync run history with record counts and status.
        Returns the last 5 sync runs.
        """
        return await asyncio.to_thread(self._dispatch, "sync status")

    async def sync_conflicts(self, __event_emitter__=None) -> str:
        """
        Show unresolved sync conflicts where NowCerts and EspoCRM data disagree.
        These need manual review before the conflicting fields are overwritten.
        """
        return await asyncio.to_thread(self._dispatch, "sync conflicts")

    async def sync_errors(self, __event_emitter__=None) -> str:
        """
        Show recent sync errors from the NowCerts → EspoCRM pipeline.
        """
        return await asyncio.to_thread(self._dispatch, "sync errors")

    async def find_account(self, query: str, __event_emitter__=None) -> str:
        """
        Search for an account in EspoCRM by name, FEIN, or other fields.
        :param query: The search query (e.g. account name, FEIN, DOT number)
        """
        return await asyncio.to_thread(self._dispatch, f"find account {query}")

        return await asyncio.to_thread(self._dispatch, f"{query}")

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

    async def crm_changelog(self, hours: int = 24, __event_emitter__=None) -> str:
        """
        Show recent CRM changes — new and updated records across all entity types.
        :param hours: Lookback window in hours (default: 24)
        """
        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": f"Fetching CRM changes (last {hours}h)...", "done": False}})
        result = await asyncio.to_thread(self._dispatch, f"changelog {hours} hours")
        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": "Done", "done": True}})
        return result

    async def hermes_command(self, command: str, __event_emitter__=None) -> str:
        """
        Send any command to Hermes. Use this for commands not covered by other tools.
        :param command: The Hermes command to run (e.g. "sync nowcerts since 2026-05-01", "stale leads", "my accounts")
        """
        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": f"Running: {command}", "done": False}})
        result = await asyncio.to_thread(self._dispatch, command)
        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": "Done", "done": True}})
        return result

    async def ping(self, __event_emitter__=None) -> str:
        """
        Check if Hermes and the CRM connection are online.
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
            return "Hermes API request timed out. The sync operation may still be running."
        except Exception as exc:
            return f"Unexpected error calling Hermes API: {exc}"
