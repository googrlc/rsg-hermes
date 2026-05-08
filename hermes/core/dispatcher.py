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

        self._init_supabase()

        self._routes: list[tuple[re.Pattern[str], Handler | str]] = [
            (
                re.compile(r"^\s*(research|enrich|investigate|look\s+up|web\s+research)\s+(business|account|company)?\b", re.I),
                business_research.handle,
            ),
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
        confirmed: bool = False,
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
                result = self._call_handler(handler, client, text)
                if (
                    self.use_openai
                    and _allow_intent
                    and _should_retry_with_intent(text, result)
                ):
                    intent_result = self._dispatch_from_intent(client, text)
                    if intent_result is not None:
                        return intent_result
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
