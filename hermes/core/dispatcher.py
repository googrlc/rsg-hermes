"""Map natural-language style commands to Hermes command handlers."""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from hermes.operations.write_gate import parse_approval_token

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
        self._pending_write: dict[str, Any] | None = None

        self._routes: list[tuple[re.Pattern[str], Handler | str]] = [
            (re.compile(r"^\s*(ping|health|status)\s*$", re.I), "ping"),
            (re.compile(r"\b(changelog|crm\s+changes|nightly\s+report|daily\s+changes|what\s+changed)\b", re.I), "changelog"),
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
            (
                re.compile(
                    r"\b(normalize\s+proper|proper\s+noun|fix\s+names?|clean\s+names?|name\s+fix|bad\s+names?|weird\s+capital|formatting\s+names?|clean\s+up\s+name)\b",
                    re.I,
                ),
                "normalize_proper_nouns",
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
                client,
                text,
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
        if handler == "changelog":
            from hermes.commands.changelog import handle as changelog_handle
            return changelog_handle(client, text)
        if handler == "normalize_proper_nouns":
            from hermes.commands.proper_noun_normalize import handle_normalize_command
            return handle_normalize_command(client, text, supa=self.supa)
        if handler == "ping":
            return DispatchResult(True, "Hermes is online and connected to CRM.")
        if (
            callable(handler)
            and getattr(handler, "__module__", "") == "hermes.commands.data_entry"
            and getattr(handler, "__name__", "") == "handle"
        ):
            return handler(client, text, **self._slack_ctx)
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
        approval = parse_approval_token(text)
        if approval:
            return self._handle_approval(client, approval)
        if _looks_like_data_quality(text):
            return self._call_handler("data_quality", client, text)
        for pattern, handler in self._routes:
            if pattern.search(text):
                result = self._call_handler(handler, client, text)
                self._capture_write_intent(result)
                return result
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
            "sync nowcerts | sync status | sync conflicts | sync errors | "
            "changelog | what changed | ping",
        )

    def _capture_write_intent(self, result: DispatchResult) -> None:
        data = result.data if isinstance(result.data, dict) else {}
        if not data:
            return
        write_intent = data.get("write_intent")
        if isinstance(write_intent, dict):
            self._pending_write = write_intent
            return
        if data.get("write_status") == "NOT_WRITTEN_AWAITING_CONFIRMATION":
            self._pending_write = {
                "kind": "intake_drafts",
                "espo_drafts": data.get("espo_drafts") or {},
                "supabase_drafts": data.get("supabase_drafts") or {},
            }

    def _handle_approval(self, client: "EspoClient", approval: Any) -> DispatchResult:
        pending = self._pending_write
        if not pending:
            return DispatchResult(False, "No pending draft update found to approve.")
        if approval.cancelled:
            self._pending_write = None
            return DispatchResult(True, "Pending draft cancelled. Nothing written.")
        if approval.revise_requested:
            return DispatchResult(True, "Revision requested. Send updated instructions and I will regenerate the draft.")

        kind = pending.get("kind")
        if kind == "intake_drafts":
            from hermes.commands.intake import execute_approved_drafts

            results = execute_approved_drafts(
                client,
                self.supa,
                espo_drafts=pending.get("espo_drafts") or {},
                supabase_drafts=pending.get("supabase_drafts") or {},
                approve_crm=approval.approve_crm,
                approve_supabase=approval.approve_supabase,
            )
            self._pending_write = None
            return DispatchResult(True, "Approved updates were written successfully.", {"results": results})
        if kind == "data_entry":
            from hermes.commands.data_entry import execute_approved_data_entry

            if not approval.approve_crm:
                return DispatchResult(False, "This pending draft only has CRM operations. Use APPROVE CRM ONLY or APPROVE ALL.")
            results = execute_approved_data_entry(client, pending.get("operations") or [])
            self._pending_write = None
            return DispatchResult(True, "Approved CRM updates were written successfully.", {"results": results})
        if kind == "merge":
            from hermes.commands.merge import execute_approved_merge

            if not approval.approve_crm:
                return DispatchResult(False, "This pending draft is a CRM merge. Use APPROVE CRM ONLY or APPROVE ALL.")
            result = execute_approved_merge(
                client,
                entity_type=str(pending.get("entity_type") or ""),
                source_id=str(pending.get("source_id") or ""),
                target_id=str(pending.get("target_id") or ""),
            )
            self._pending_write = None
            return DispatchResult(True, "Approved CRM merge was executed successfully.", {"result": result})
        return DispatchResult(False, "Pending draft type is not executable yet.")
"""Map natural-language style commands to Hermes command handlers."""
