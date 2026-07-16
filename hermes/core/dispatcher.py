"""Map natural-language style commands to Hermes command handlers."""

from __future__ import annotations

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
_SYNC_STATUS_HINT = re.compile(
    r"\b(sync|synced|integration|updated|up\s*to\s*date|status|did\s+.*work)\b",
    re.I,
)

_BROAD_REPORT_HINTS: dict[str, re.Pattern[str]] = {
    "data quality": _DATA_QUALITY_HINT,
    "kpi": re.compile(r"\b(kpi|dashboard|metrics?)\b", re.I),
    "pipeline": re.compile(r"\bpipeline\b", re.I),
    "premium by lob": re.compile(r"\b(premium\s+by\s+lob|lob|line\s+of\s+business)\b", re.I),
    "commission snapshot": re.compile(r"\bcommission\b", re.I),
    "stale leads": re.compile(r"\b(stale|untouched|old)\b", re.I),
    "my accounts": re.compile(r"\b(my\s+accounts|account\s+list)\b", re.I),
    "account list": re.compile(r"\b(account\s+list|my\s+accounts)\b", re.I),
    "report personal": re.compile(r"\b(report\s*[- ]?personal|cleanup)\b", re.I),
    "bulk normalize": re.compile(r"\b(normalize|normalization)\b", re.I),
}


def _looks_like_data_quality(text: str) -> bool:
    return bool(_DATA_QUALITY_HINT.search(text.strip()))


def _looks_like_sync_status_question(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if not _SYNC_STATUS_HINT.search(stripped):
        return False
    return stripped.endswith("?") or bool(
        re.match(r"^\s*(is|are|did|does|has|what|where|when|can)\b", stripped, re.I)
    )


def _clarification_message(text: str) -> str:
    if _looks_like_sync_status_question(text):
        return (
            "I can check that, but I need the exact check you want.\n"
            "Reply with one of:\n"
            "- `sync nowcerts dry-run` (run a fresh sync check)\n"
            "- `data quality` (CRM completeness audit)\n"
            "- `kpi` (quick CRM health dashboard)"
        )
    return (
        "I wasn't fully sure what you wanted, so I did not run a report automatically.\n"
        "Try one of: `data quality`, `kpi`, `pipeline`, `sync nowcerts dry-run`, or tell me exactly what to check."
    )


def _intent_translation_needs_clarification(user_text: str, command: str) -> bool:
    hint = _BROAD_REPORT_HINTS.get(command.lower())
    if hint is None:
        return False
    return not bool(hint.search(user_text))


def _should_retry_with_intent(text: str, result: DispatchResult) -> bool:
    """Let plain English writes fall through to intent, but preserve explicit field errors."""
    if result.ok:
        return False
    lowered = result.message.lower()
    if "=" in text or "unknown field" in lowered or "read-only" in lowered:
        return False
    return True


@dataclass
class DispatchResult:
    ok: bool
    message: str
    data: dict[str, Any] | None = None


Handler = Callable[["EspoClient", str], DispatchResult]


class Dispatcher:
    """Order matters: first matching pattern wins."""

    def __init__(self, *, use_openai: bool = False) -> None:
        from hermes.commands import business_research, data_entry, lookup, revenue

        self.use_openai = use_openai
        self.supa: SupabaseClient | None = None
        self._slack_ctx: dict[str, Any] = {}
        self._pending_write: dict[str, Any] | None = None

        self._init_supabase()

        self._routes: list[tuple[re.Pattern[str], Handler | str]] = [
            # CRM change proposal approval — must precede research/intake/lookup.
            (
                re.compile(r"^\s*(?:APPROVE|COMMIT)\s+CHANGE\s+(?P<id>\S+)\s*$", re.I),
                "change_proposals",
            ),
            (
                re.compile(r"^\s*(?:APPROVE|COMMIT)\s+CHANGE\s+ALL\s*$", re.I),
                "change_proposals",
            ),
            (
                re.compile(r"^\s*(?:LIST|SHOW)\s+CHANGES?\s*$", re.I),
                "change_proposals",
            ),
            (
                re.compile(r"^\s*(?:REJECT|CANCEL)\s+CHANGE\s+(?P<id>\S+)", re.I),
                "change_proposals",
            ),
            # Renewal exposure review — MUST precede business_research so that
            # "research renewal client / exposures" isn't treated as intake NAICS research.
            (
                re.compile(
                    r"\bresearch\s+(the\s+)?renewal\s+client\b"
                    r"|\bresearch\s+(the\s+)?(missing\s+)?exposures?\b"
                    r"|\brenewal\s+exposure\s+review\b",
                    re.I,
                ),
                "renewal_research",
            ),
            # Renewal case + tasks — MUST precede the data_entry "^create" route
            # so "create a renewal case and tasks" isn't captured as a generic create.
            (
                re.compile(r"\b(create|open|start)\s+(a\s+)?renewal\s+case\b", re.I),
                "renewal_case_create",
            ),
            (
                re.compile(r"\b(create|generate|add)\s+(the\s+)?renewal\s+tasks?\b", re.I),
                "renewal_tasks_create",
            ),
            (
                re.compile(r"^\s*(research|enrich|investigate|look\s+up|web\s+research)\s+(business|account|company)?\b", re.I),
                business_research.handle,
            ),
            (re.compile(r"^\s*(create|update)\s+", re.I), data_entry.handle),
            (re.compile(r"^\s*add\s+", re.I), data_entry.handle),
            (re.compile(r"^\s*move\s+opportunit(?:y|ie)\s+", re.I), data_entry.handle),
            # Agency intake — explicit verbs that mean "stage a draft from this summary".
            (
                re.compile(
                    r"^\s*(stage|draft|agency)\s*(an?\s+)?(intake|account|summary)\b",
                    re.I,
                ),
                "agency_intake",
            ),
            (re.compile(r"^\s*new\s+(commercial|personal|life|benefits|medicare)\s+(account|prospect|client)\b", re.I), "agency_intake"),
            # Structured Hermes intake block produced by intake skills
            # ("Hermes:\n…\nMODULE: …"). Routes to the same multi-entity
            # extractor even when posted without a leading "stage intake:" verb,
            # so producer-submitted full-summary posts in #crm-entry land in
            # the right pipeline (Account + Contacts + per-LOB Opportunities).
            (
                re.compile(r"\bHermes\s*:\s*\n.*?\bMODULE\s*:", re.I | re.S),
                "agency_intake",
            ),
            # Fact retrieval — narrow: question word + recognized fact label,
            # OR short-form "<label> for <entity>". Must precede the broad
            # `lookup.handle` route below.
            (
                re.compile(
                    # Form A: "what/who/find/tell me … <label>"
                    r"^\s*(what|who|find|lookup|tell\s+me)\b.*?\b("
                    r"ein|fein|federal\s+(employer|tax)\s+id|"
                    r"dob|date\s+of\s+birth|birthdate|birthday|"
                    r"phone(\s+number)?|email(\s+address)?|address|"
                    r"annual\s+revenue|gross\s+revenue|payroll|"
                    r"employee\s+count|headcount|naics|"
                    r"renewal\s+date|expir(es|ation)|x-?date|effective\s+date|"
                    r"principal|sole\s+member|spouse|decision\s+maker|"
                    r"quote\s+(number|#)|policy\s+(number|#)|carrier|premium"
                    r")\b"
                    # Form B: short "<label> for <entity>"
                    r"|^\s*(phone|email|ein|fein|address|payroll|naics|carrier)\s+for\s+\S",
                    re.I,
                ),
                "agency_fact",
            ),
            (re.compile(r"\b(total\s+premium|sum\s+premium|premium\s+for)\b", re.I), lookup.handle),
            (re.compile(r"^\s*(what|who|find|lookup|search)\b", re.I), lookup.handle),
            # Renewal worksheet — MUST precede the broad renewal/revenue route so that
            # "prepare a renewal worksheet for <client>" does not fall through to revenue.handle.
            (
                re.compile(
                    r"\b(prepare|create|build|generate)\s+(a\s+)?renewal\s+worksheet\b",
                    re.I,
                ),
                "renewal_worksheet",
            ),
            # Renewal queue + open-exact — MUST precede the broad renewal/revenue
            # route. Queue precedes open so "show me the renewal queue" isn't
            # captured by the open verb.
            (
                re.compile(
                    r"\b(get\s+)?renewal\s+queue\b"
                    r"|\bupcoming\s+renewals?\b"
                    r"|\brenewals?\s+due\b"
                    r"|\brenewals?\s+(this|next)\s+(week|month)\b"
                    r"|\bwork\s+the\s+renewals?\b",
                    re.I,
                ),
                "renewal_queue",
            ),
            (
                re.compile(
                    r"\b(open|pull\s+up)\s+(the\s+)?(exact\s+)?renewal\b"
                    r"|\bopen\s+renewal\s+for\s+policy\b",
                    re.I,
                ),
                "renewal_open",
            ),
            # NowCerts approval-gated writeback — propose / show / confirm.
            # Placed before the broad revenue route. ("approve … write-back" is a
            # full phrase, so parse_approval_token — which needs an exact token —
            # never intercepts it.)
            (
                re.compile(
                    r"\bpropose\s+(a\s+)?(nowcerts\s+)?(renewal\s+)?write-?back\b"
                    r"|\bpropose\s+nowcerts\b"
                    r"|\bstage\s+(a\s+)?nowcerts\s+write-?back\b",
                    re.I,
                ),
                "renewal_wb_propose",
            ),
            (
                re.compile(
                    r"\bshow\s+(me\s+)?(the\s+)?proposed\s+(nowcerts\s+)?(changes|write-?back)\b"
                    r"|\b(list|show)\s+proposed\s+(nowcerts\s+)?write-?back\b",
                    re.I,
                ),
                "renewal_wb_show",
            ),
            (
                re.compile(
                    r"\b(approve|confirm)\s+(the\s+)?(proposed\s+)?(nowcerts\s+)?write-?back\b",
                    re.I,
                ),
                "renewal_wb_confirm",
            ),
            (
                re.compile(r"\b(expir(?:e|ing|y)|renewal[-\s]?audit|renewals?|cross-?sell|revenue|opportunit)", re.I),
                revenue.handle,
            ),
            # Data quality BEFORE reports — intent LLM sometimes rewrites "data quality" as "kpi".
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
        if handler == "agency_intake":
            from hermes.commands.agency_intake import handle as ai_handle
            return ai_handle(client, text, supa=self.supa, **self._slack_ctx)
        if handler == "agency_fact":
            from hermes.commands.fact_retriever import handle as fact_handle
            return fact_handle(client, text, supa=self.supa)
        if handler == "renewal_worksheet":
            from hermes.commands.renewal_worksheet import handle as rw_handle
            return rw_handle(client, text, supa=self.supa)
        if handler == "renewal_queue":
            from hermes.commands.renewal_desk import queue_handle
            return queue_handle(client, text, supa=self.supa)
        if handler == "renewal_open":
            from hermes.commands.renewal_desk import open_handle
            return open_handle(client, text, supa=self.supa)
        if handler == "renewal_research":
            from hermes.commands.renewal_desk import research_handle
            return research_handle(client, text, supa=self.supa)
        if handler == "renewal_wb_propose":
            from hermes.commands.renewal_writeback import propose_handle
            return propose_handle(client, text, supa=self.supa)
        if handler == "renewal_wb_show":
            from hermes.commands.renewal_writeback import show_handle
            return show_handle(client, text, supa=self.supa)
        if handler == "renewal_wb_confirm":
            from hermes.commands.renewal_writeback import confirm_handle
            return confirm_handle(client, text, supa=self.supa)
        if handler == "renewal_case_create":
            from hermes.commands.renewal_cases import create_case_handle
            return create_case_handle(client, text, supa=self.supa)
        if handler == "renewal_tasks_create":
            from hermes.commands.renewal_cases import create_tasks_handle
            return create_tasks_handle(client, text, supa=self.supa)
        if handler == "change_proposals":
            from hermes.commands.change_proposals import handle as cp_handle
            return cp_handle(client, text, supa=self.supa)
        if (
            callable(handler)
            and getattr(handler, "__module__", "") == "hermes.commands.data_entry"
            and getattr(handler, "__name__", "") == "handle"
        ):
            return handler(client, text, **self._slack_ctx)
        return handler(client, text)

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

    def dispatch(
        self,
        client: "EspoClient",
        line: str,
        *,
        _allow_intent: bool = True,
        confirmed: bool = False,
    ) -> DispatchResult:
        text = line.strip()
        if not text:
            return DispatchResult(False, "Empty command.")
        # Process approval tokens before route matching.
        approval = parse_approval_token(text)
        if approval:
            return self._handle_approval(client, approval)
        # Never route "data quality" through OpenAI intent as "kpi" — handle explicitly first.
        if _looks_like_data_quality(text):
            return self._call_handler("data_quality", client, text)
        for pattern, handler in self._routes:
            if pattern.search(text):
                result = self._call_handler(handler, client, text)
                if (
                    self.use_openai
                    and _allow_intent
                    and _should_retry_with_intent(text, result)
                ):
                    intent_result = self._dispatch_from_intent(client, text)
                    if intent_result is not None:
                        return intent_result
                self._capture_write_intent(result)
                return result
        if self.use_openai and _allow_intent:
            from hermes.core.nl_agent import ask as nl_ask

            return nl_ask(client, text, confirmed=confirmed)
        return DispatchResult(
            True,
            _clarification_message(text),
        )

    def _dispatch_from_intent(self, client: "EspoClient", text: str) -> DispatchResult | None:
        from hermes.core.intent_openai import command_from_intent

        command = command_from_intent(text)
        if not command:
            return None
        normalized = command.strip().lower()
        if normalized in {"clarify", "clarification", "none", "unknown"}:
            return DispatchResult(True, _clarification_message(text))
        if normalized == text.lower():
            return None
        if _intent_translation_needs_clarification(text, normalized):
            return DispatchResult(True, _clarification_message(text))
        return self.dispatch(client, command, _allow_intent=False)
