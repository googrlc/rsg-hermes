"""Map natural-language style commands to Hermes command handlers."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hermes.core.client import EspoClient
    from hermes.integrations.supabase_client import SupabaseClient

log = logging.getLogger(__name__)

_DATA_QUALITY_HINT = re.compile(
    r"(^\s*dq\s*$)|(\b(?:data\s+quality|dq\s+report|audit\s+crm|crm\s+audit|quality\s+check)\b)",
    re.I,
)


def _looks_like_data_quality(text: str) -> bool:
    return bool(_DATA_QUALITY_HINT.search(text.strip()))


@dataclass
class DispatchResult:
    ok: bool
    message: str
    data: dict[str, Any] | None = None


Handler = Callable[["EspoClient", str], DispatchResult]


class Dispatcher:
    """Order matters: first matching pattern wins."""

    def __init__(self, *, use_openai: bool = False) -> None:
        from hermes.commands import data_entry, lookup, merge, revenue

        self.use_openai = use_openai
        self.supa: SupabaseClient | None = None
        self._slack_ctx: dict[str, Any] = {}

        self._init_supabase()

        self._routes: list[tuple[re.Pattern[str], Handler | str]] = [
            (re.compile(r"^\s*(ping|health|status)\s*$", re.I), "ping"),
            (re.compile(r"\bsync\b.*\b(nowcerts|status|conflicts?|errors?|runs?)\b", re.I), "sync"),
            (re.compile(r"^\s*sync\s", re.I), "sync"),
            (re.compile(r"^\s*merge\s+", re.I), merge.handle),
            (re.compile(r"\bcan\s+be\s+merged\b", re.I), merge.handle),
            (re.compile(r"^\s*(create|update)\s+", re.I), data_entry.handle),
            (re.compile(r"^\s*add\s+", re.I), data_entry.handle),
            (re.compile(r"^\s*move\s+opportunit(?:y|ie)\s+", re.I), data_entry.handle),
            (re.compile(r"\b(total\s+premium|sum\s+premium|premium\s+for)\b", re.I), lookup.handle),
            (re.compile(r"^\s*(what|who|find|lookup|search)\b", re.I), lookup.handle),
            (
                re.compile(r"\b(expir(?:e|ing|y)|renewal[-\s]?audit|renewals?|cross-?sell|revenue|opportunit)", re.I),
                revenue.handle,
            ),
            # Data quality BEFORE reports — intent LLM sometimes rewrites “data quality” as “kpi”.
            (
                re.compile(
                    r"\b(data\s+quality|dq\s+report|audit\s+crm|crm\s+audit|quality\s+check)\b",
                    re.I,
                ),
                "data_quality",
            ),
            (
                re.compile(
                    r"\b(pipeline|lob\s+break|premium\s+by\s+lob|kpi|dashboard"
                    r"|commission\s+snap|stale|account\s*list|my\s+accounts"
                    r"|report\s*[- ]?personal|personal\s*report|cleanup\s*report"
                    r"|bulk\s*[- ]?normalize|normalize\s*preview)\b",
                    re.I,
                ),
                "reports",
            ),
            (
                re.compile(
                    r"^\s*(met|talked|spoke|just\s+met|new\s+lead|log\s+lead|intake)\b",
                    re.I,
                ),
                "intake",
            ),
        ]

    def _init_supabase(self) -> None:
        try:
            from hermes.integrations.supabase_client import SupabaseClient
            self.supa = SupabaseClient()
        except Exception:
            log.info("Supabase not configured -- dual-write disabled")
            self.supa = None

    def set_slack_context(
        self,
        *,
        channel_id: str | None = None,
        user_id: str | None = None,
        message_ts: str | None = None,
    ) -> None:
        """Stash Slack event metadata so intake can log it."""
        self._slack_ctx = {
            "channel_id": channel_id,
            "user_id": user_id,
            "message_ts": message_ts,
        }

    def _call_handler(
        self,
        handler: Handler | str,
        client: "EspoClient",
        text: str,
    ) -> DispatchResult:
        if handler == "intake":
            from hermes.commands.intake import handle as intake_handle
            return intake_handle(
                client, text,
                supa=self.supa,
                **self._slack_ctx,
            )
        if handler == "reports":
            from hermes.commands.reports import handle as reports_handle
            return reports_handle(client, text, supa=self.supa)
        if handler == "data_quality":
            from hermes.commands.data_quality import handle as dq_handle
            return dq_handle(client, text, supa=self.supa)
        if handler == "sync":
            from hermes.commands.sync import handle as sync_handle
            return sync_handle(client, text, supa=self.supa)
        if handler == "ping":
            return DispatchResult(True, "Hermes is online and connected to CRM.")
        return handler(client, text)

    def dispatch(
        self,
        client: "EspoClient",
        line: str,
        *,
        _allow_intent: bool = True,
    ) -> DispatchResult:
        text = line.strip()
        if not text:
            return DispatchResult(False, "Empty command.")
        # Never route “data quality” through OpenAI intent as “kpi” — handle explicitly first.
        if _looks_like_data_quality(text):
            return self._call_handler("data_quality", client, text)
        for pattern, handler in self._routes:
            if pattern.search(text):
                return self._call_handler(handler, client, text)
        if self.use_openai and _allow_intent:
            from hermes.core.intent_openai import command_from_intent

            command = command_from_intent(text)
            if command and command.strip().lower() != text.lower():
                return self.dispatch(client, command, _allow_intent=False)
        return DispatchResult(
            False,
            "No handler matched. Try: add … | what/find/lookup … | cross-sell/renewals … | "
            "intake <lead info> | pipeline | kpi | stale leads | my accounts | data quality | "
            "report personal | bulk normalize | merge <entity> <id1> into <id2> | "
            "sync nowcerts | sync status | sync conflicts | sync errors | ping",
        )
