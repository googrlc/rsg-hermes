"""Hermes REST API — FastAPI wrapper around the Dispatcher."""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Literal

import uvicorn
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

log = logging.getLogger(__name__)

def _model_dict(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()  # type: ignore[no-any-return]


_WRITE_HINT = re.compile(
    r"^\s*(?:add|create|update|merge|move\s+opportunit(?:y|ie)|intake|new\s+lead|log\s+lead|met|talked|spoke|just\s+met)\b"
    r"|^\s*(?:research|enrich|investigate|look\s+up|web\s+research)\b.*\b(?:save|write|update|put|log|store)\b",
    re.I,
)


def requires_confirmation(command: str) -> bool:
    """Return true when a Hermes command may write to CRM or another system."""
    return bool(_WRITE_HINT.search(command.strip()))


def openapi_schema() -> dict[str, Any]:
    """Expose schema for backwards-compatibility tests."""
    return app.openapi()


app = FastAPI(
    title="Hermes API",
    description="EspoCRM coordination middleware — sync, lookup, data quality, and more.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_espo = None
_dispatcher = None
_supa = None


def _get_espo():
    global _espo
    if _espo is None:
        from hermes.core.client import EspoClient

        _espo = EspoClient()
    return _espo


def _get_dispatcher():
    global _dispatcher
    if _dispatcher is None:
        from hermes.core.dispatcher import Dispatcher

        use_openai = bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("HERMES_OPENAI_API_KEY"))
        _dispatcher = Dispatcher(use_openai=use_openai)
    return _dispatcher


def _get_supa():
    global _supa
    if _supa is None:
        from hermes.integrations.supabase_client import SupabaseClient

        _supa = SupabaseClient()
    return _supa


class CRMWriteDispatchRequest(BaseModel):
    entity_type: str
    entity_id: str | None = None
    payload: dict[str, Any]
    created_by_role: str = "dashboard"
    priority: int = 1


OpenClawTaskType = Literal["appetite-analyzer", "retention-risk-scout", "crm-manager"]


class OpenClawEnqueueRequest(BaseModel):
    """Hermes → OpenClaw Manager contract (service-role inserts only from this API)."""

    task_type: OpenClawTaskType
    payload: dict[str, Any]
    requested_by: str = "hermes"
    priority: int = 5
    notify_slack: bool = False


class AIEnrichmentDispatchRequest(OpenClawEnqueueRequest):
    """Alias for dashboard dispatch payloads that target openclaw_task_queue."""

    pass


class DispatchRequest(BaseModel):
    command: str | None = None
    confirm: bool = False
    crm_write: CRMWriteDispatchRequest | None = None
    ai_enrichment: AIEnrichmentDispatchRequest | None = None


class DispatchResponse(BaseModel):
    ok: bool
    message: str
    data: dict | None = None
    requires_confirmation: bool = False


class AsyncAcceptedResponse(BaseModel):
    ok: bool
    message: str
    task_id: str
    queue_name: str
    status: str


IntakeSource = Literal["cowork", "voice_tool", "manual_curl", "n8n"]
IntakeAgent = Literal["lamar", "gretchen"]
IntakeKind = Literal["full_intake", "task", "note", "update", "other"]


class IntakeDocument(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str | None = None
    extracted_data: dict[str, Any] | None = None
    raw_text: str | None = None
    source_file: str | None = None


class IntakeCoachingSnapshot(BaseModel):
    model_config = ConfigDict(extra="allow")

    covered_topics: list[str] = Field(default_factory=list)
    remaining_gaps: list[str] = Field(default_factory=list)
    flags_detected: list[dict[str, Any]] = Field(default_factory=list)


class IntakeSubmissionRequest(BaseModel):
    # Required
    idempotency_key: str = Field(..., min_length=1, max_length=512)
    source: IntakeSource
    agent: IntakeAgent
    captured_at: datetime

    # Optional with default
    intake_kind: IntakeKind = "full_intake"

    # Optional structured envelope fields
    client_identifier: str | None = None
    lob_code: str | None = None

    # Payload content — at least one of transcript/documents is required
    transcript: str | None = None
    documents: list[IntakeDocument] = Field(default_factory=list)
    coaching_snapshot: IntakeCoachingSnapshot | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _require_transcript_or_documents(self) -> "IntakeSubmissionRequest":
        has_transcript = bool(self.transcript and self.transcript.strip())
        has_documents = bool(self.documents)
        if not (has_transcript or has_documents):
            raise ValueError("at least one of `transcript` or `documents` is required")
        return self


class IntakeSubmissionResponse(BaseModel):
    submission_id: str
    status: str
    status_url: str
    created_at: str
    idempotent_replay: bool = False


class AgencyIntakeRequest(BaseModel):
    raw_text: str
    submitted_by: str | None = None
    source_type: str = "manual"
    source_ref: str | None = None


class AgencyIntakeResponse(BaseModel):
    ok: bool
    draft_id: str
    approval_prompt: str
    validation_warnings: list[str] = []
    payload_preview: dict | None = None
    requires_confirmation: bool = True


class AgencyIntakeApprovalRequest(BaseModel):
    draft_id: str
    token: str
    approver: str | None = None


class AgencyIntakeApprovalResponse(BaseModel):
    ok: bool
    draft_id: str
    token: str
    status: str
    summary: str
    enqueued_queue_ids: list[str] = []
    retrieval_row_ids: dict[str, list[str]] = {}
    error: str | None = None


class AgencyFactRequest(BaseModel):
    question: str | None = None
    entity: str | None = None
    fact_label: str | None = None
    include_restricted: bool = True


class AgencyFactResponse(BaseModel):
    ok: bool
    found: bool
    entity: str
    fact_label: str
    fact_value: str | None = None
    source: str
    confidence: str = "high"
    sensitivity: str = "standard"
    answer_text: str
    candidates: list[dict[str, Any]] = []
    notes: str | None = None


def _accept_openclaw_enqueue(body: OpenClawEnqueueRequest) -> JSONResponse:
    """Queue one OpenClaw task; Hermes is a pure producer (insert + 202)."""
    from hermes.integrations.openclaw_producer import enqueue_openclaw_task

    try:
        row = enqueue_openclaw_task(
            _get_supa(),
            task_type=body.task_type,
            payload=body.payload,
            requested_by=body.requested_by,
            priority=body.priority,
            notify_slack=body.notify_slack,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return JSONResponse(
        status_code=202,
        content=_model_dict(
            AsyncAcceptedResponse(
                ok=True,
                message="Task queued for OpenClaw (openclaw_task_queue).",
                task_id=str(row.get("id")),
                queue_name="openclaw_task_queue",
                status="PENDING",
            )
        ),
    )


@app.get("/health")
async def health():
    return {"status": "ok", "service": "hermes"}


@app.get("/hermes/ping", response_model=DispatchResponse)
async def hermes_ping():
    """Compatibility ping endpoint for WebUI connectors that call /hermes/ping."""
    return DispatchResponse(
        ok=True,
        message="Pong! How can I assist you with the CRM today?",
        data=None,
        requires_confirmation=False,
    )


@app.post("/dispatch", response_model=DispatchResponse)
async def dispatch(req: DispatchRequest):
    if not req.command or not req.command.strip():
        raise HTTPException(status_code=400, detail="Empty command.")
    if requires_confirmation(req.command) and not req.confirm:
        return DispatchResponse(
            ok=False,
            message="This command may write to CRM. Re-submit with confirm=true after approval.",
            data={},
            requires_confirmation=True,
        )
    try:
        espo = _get_espo()
        dispatcher = _get_dispatcher()
        result = dispatcher.dispatch(espo, req.command, confirmed=req.confirm)
        return DispatchResponse(ok=result.ok, message=result.message, data=result.data, requires_confirmation=False)
    except Exception as exc:
        log.exception("Dispatch failed for command: %s", req.command)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/hermes/dispatch", response_model=AsyncAcceptedResponse)
async def dashboard_dispatch(req: DispatchRequest):
    """Dashboard async dispatch entrypoint.

    - CRM mutations are queued into crm_write_queue and return HTTP 202.
    - AI enrichment jobs are queued into openclaw_task_queue and return HTTP 202.
    """
    if req.crm_write is not None:
        from hermes.operations.crm_queue_worker import enqueue_crm_write

        try:
            row = enqueue_crm_write(
                _get_supa(),
                entity_type=req.crm_write.entity_type,
                entity_id=req.crm_write.entity_id,
                payload=req.crm_write.payload,
                created_by_role=req.crm_write.created_by_role,
                priority=req.crm_write.priority,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return JSONResponse(
            status_code=202,
            content=_model_dict(AsyncAcceptedResponse(
                ok=True,
                message="CRM write queued for Hermes worker.",
                task_id=str(row.get("id")),
                queue_name="crm_write_queue",
                status="PENDING",
            )),
        )

    if req.ai_enrichment is not None:
        return _accept_openclaw_enqueue(req.ai_enrichment)

    if req.command and req.command.strip():
        # Backward compatible command execution for clients that still send command-only payloads.
        result = await dispatch(req)
        return JSONResponse(
            status_code=202,
            content=_model_dict(AsyncAcceptedResponse(
                ok=result.ok,
                message=result.message,
                task_id="inline-command",
                queue_name="command_dispatch",
                status="ACCEPTED",
            )),
        )
    raise HTTPException(status_code=400, detail="Provide either crm_write, ai_enrichment, or command.")


@app.post("/api/hermes/openclaw/enqueue")
async def openclaw_enqueue(req: OpenClawEnqueueRequest):
    """Dedicated OpenClaw producer endpoint (same contract as ai_enrichment on /api/hermes/dispatch)."""
    return _accept_openclaw_enqueue(req)


@app.get("/api/hermes/sync-health")
async def sync_health():
    """Queue-centric health snapshot for dashboard SyncHealthCheck component."""
    supa = _get_supa()
    crm_pending = supa.select("crm_write_queue", columns="id", params={"status": "eq.PENDING"}, limit=1000)
    crm_processing = supa.select("crm_write_queue", columns="id", params={"status": "eq.PROCESSING"}, limit=1000)
    crm_failed = supa.select("crm_write_queue", columns="id", params={"status": "eq.FAILED"}, limit=1000)

    openclaw_pending = supa.select("openclaw_task_queue", columns="id", params={"status": "eq.PENDING"}, limit=1000)
    openclaw_processing = supa.select("openclaw_task_queue", columns="id", params={"status": "eq.PROCESSING"}, limit=1000)
    openclaw_failed = supa.select("openclaw_task_queue", columns="id", params={"status": "eq.FAILED"}, limit=1000)

    latest_run = supa.select("sync_runs", params={"order": "created_at.desc"}, limit=1)
    latest = latest_run[0] if latest_run else {}

    return {
        "status": "ok",
        "crm_write_queue": {
            "pending": len(crm_pending),
            "processing": len(crm_processing),
            "failed": len(crm_failed),
        },
        "openclaw_task_queue": {
            "pending": len(openclaw_pending),
            "processing": len(openclaw_processing),
            "failed": len(openclaw_failed),
        },
        "latest_sync_run": {
            "id": latest.get("id"),
            "status": latest.get("status"),
            "workflow_name": latest.get("workflow_name"),
            "finished_at": latest.get("finished_at"),
        },
    }


# ---------------------------------------------------------------------------
# Document library — Agent OS reads these to render folders -> documents
# ---------------------------------------------------------------------------


class DocumentSaveRequest(BaseModel):
    title: str
    content: str
    doc_type: str = "other"
    account_name: str | None = None
    folder: str | None = None
    summary: str | None = None
    source: str | None = None
    created_by: str | None = None


@app.post("/api/documents/save")
async def documents_save(req: DocumentSaveRequest):
    """Save a document to the library (Supermemory + Drive mirror + index).

    ``account_name`` => client folder; otherwise it lands in the internal
    space under ``folder`` (default 'General').
    """
    from hermes.documents.store import DocumentStoreError, save_document

    try:
        row = save_document(
            title=req.title,
            content=req.content,
            doc_type=req.doc_type,
            account_name=req.account_name,
            folder=req.folder,
            summary=req.summary,
            source=req.source or "api",
            created_by=req.created_by,
            supa=_get_supa(),
        )
    except DocumentStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        log.exception("documents_save failed title=%s", req.title)
        raise HTTPException(status_code=502, detail=str(exc))
    return row


@app.get("/api/documents/folders")
async def documents_folders():
    """Folder tree for Agent OS: one entry per (space, name) with a count."""
    from hermes.documents.store import list_folders

    return {"folders": list_folders(_get_supa())}


@app.get("/api/documents")
async def documents_in_folder(space: str, name: str):
    """Documents in one folder. ``space`` is 'client' or 'internal';
    ``name`` is the account (client) or freeform folder (internal)."""
    from hermes.documents.store import list_documents

    if space not in ("client", "internal"):
        raise HTTPException(status_code=400, detail="space must be 'client' or 'internal'")
    return {"documents": list_documents(space=space, name=name, supa=_get_supa())}


@app.get("/api/documents/{doc_id}")
async def document_detail(doc_id: str):
    """One document index row (title, preview, drive_url, supermemory_id, …)."""
    from hermes.documents.store import get_document

    row = get_document(doc_id, _get_supa())
    if row is None:
        raise HTTPException(status_code=404, detail="document not found")
    return row


@app.post("/agency-intake", response_model=AgencyIntakeResponse)
async def agency_intake(req: AgencyIntakeRequest):
    """Stage an agency intake draft. Returns draft_id + approval prompt.

    Nothing is written to CRM yet — caller must POST /agency-intake/approve
    with an approval token (APPROVE ALL, APPROVE CRM ONLY, etc.).
    """
    from hermes.commands.agency_intake import AgencyIntakeError, stage_draft

    if not req.raw_text or not req.raw_text.strip():
        raise HTTPException(status_code=400, detail="raw_text is required")
    try:
        draft = stage_draft(
            _get_supa(),
            raw_text=req.raw_text,
            submitted_by=req.submitted_by,
            source_type=req.source_type,
            source_ref=req.source_ref,
        )
    except AgencyIntakeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        log.exception("agency_intake staging failed")
        raise HTTPException(status_code=500, detail=str(exc))
    return AgencyIntakeResponse(
        ok=True,
        draft_id=draft.draft_id,
        approval_prompt=draft.approval_prompt,
        validation_warnings=draft.validation_warnings,
        payload_preview=draft.payload,
    )


@app.post("/agency-intake/approve", response_model=AgencyIntakeApprovalResponse)
async def agency_intake_approve(req: AgencyIntakeApprovalRequest):
    """Apply an approval token to a staged agency intake draft.

    Same shared logic that the Slack interactive button calls.
    """
    from hermes.operations.agency_intake_approval import ApprovalError, approve_draft

    try:
        result = approve_draft(
            _get_supa(),
            draft_id=req.draft_id,
            token=req.token,
            approver=req.approver,
        )
    except ApprovalError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        log.exception("agency_intake_approve failed for draft=%s", req.draft_id)
        raise HTTPException(status_code=500, detail=str(exc))
    return AgencyIntakeApprovalResponse(
        ok=result.ok,
        draft_id=result.draft_id,
        token=result.token,
        status=result.status,
        summary=result.summary,
        enqueued_queue_ids=result.enqueued_queue_ids,
        retrieval_row_ids=result.retrieval_row_ids,
        error=result.error,
    )


@app.post("/agency-fact", response_model=AgencyFactResponse)
async def agency_fact(req: AgencyFactRequest):
    """Answer a structured fact-retrieval question with citation + confidence.

    Two call shapes:
      1. Natural-language: {"question": "What is JB Noble's EIN?"}
      2. Structured:       {"entity": "JB Noble", "fact_label": "EIN"}
    """
    from hermes.commands import fact_retriever

    entity = (req.entity or "").strip()
    fact_label = (req.fact_label or "").strip()
    if not entity or not fact_label:
        if not req.question or not req.question.strip():
            raise HTTPException(
                status_code=400,
                detail="Provide either {entity, fact_label} or question.",
            )
        parsed = fact_retriever.parse_question(req.question)
        if not parsed:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Couldn't parse fact label from question. Use shapes like "
                    "\"What is <entity>'s EIN?\" or pass entity+fact_label directly."
                ),
            )
        entity, fact_label = parsed

    try:
        answer = fact_retriever.retrieve(
            _get_espo(),
            _get_supa(),
            entity_name=entity,
            fact_label=fact_label,
            include_restricted=req.include_restricted,
        )
    except Exception as exc:
        log.exception("agency_fact failed entity=%s label=%s", entity, fact_label)
        raise HTTPException(status_code=500, detail=str(exc))

    return AgencyFactResponse(
        ok=True,
        found=answer.found,
        entity=answer.entity,
        fact_label=answer.fact_label,
        fact_value=answer.fact_value,
        source=answer.source,
        confidence=answer.confidence,
        sensitivity=answer.sensitivity,
        answer_text=answer.render(),
        candidates=answer.candidates,
        notes=answer.notes,
    )


def _require_intake_api_key(request: Request) -> None:
    """Validate ``X-RSG-API-Key`` header against ``RSG_INTAKE_API_KEY`` env."""
    expected = os.environ.get("RSG_INTAKE_API_KEY", "").strip()
    if not expected:
        # Misconfiguration: never silently accept; refuse with 503.
        log.error("RSG_INTAKE_API_KEY is unset — refusing /api/intake")
        raise HTTPException(status_code=503, detail="intake endpoint not configured")
    provided = request.headers.get("x-rsg-api-key", "")
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="invalid or missing X-RSG-API-Key")


def _intake_status_url(request: Request, submission_id: str) -> str:
    base = os.environ.get("HERMES_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if not base:
        base = str(request.base_url).rstrip("/")
    return f"{base}/api/intake/{submission_id}/status"


@app.post("/api/intake")
async def intake_submit(req: IntakeSubmissionRequest, request: Request):
    """Accept an intake submission and insert one row in ``intake_submissions``.

    On a fresh insert: returns 202 with ``submission_id``, ``status_url``, etc.
    On idempotent replay (same ``idempotency_key``): returns 200 with the
    existing row's state. The Phase 3 worker picks up ``status='received'``
    rows asynchronously; this endpoint never blocks on downstream processing.
    """
    from hermes.integrations.intake_submissions import (
        IntakeError,
        insert_submission,
    )
    from hermes.integrations.supabase_client import SupabaseClientError

    _require_intake_api_key(request)

    payload = {
        "transcript": req.transcript,
        "documents": [_model_dict(d) for d in req.documents],
        "coaching_snapshot": _model_dict(req.coaching_snapshot) if req.coaching_snapshot else None,
        "notes": req.notes,
    }

    try:
        row, is_new = insert_submission(
            _get_supa(),
            idempotency_key=req.idempotency_key,
            source=req.source,
            agent=req.agent,
            intake_kind=req.intake_kind,
            client_identifier=req.client_identifier,
            lob_code=req.lob_code,
            captured_at=req.captured_at,
            payload=payload,
        )
    except IntakeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except SupabaseClientError as exc:
        log.exception("intake_submissions insert failed key=%s", req.idempotency_key)
        raise HTTPException(status_code=502, detail=f"supabase write failed: {exc}")

    submission_id = str(row.get("id"))
    body = _model_dict(
        IntakeSubmissionResponse(
            submission_id=submission_id,
            status=str(row.get("status", "received")),
            status_url=_intake_status_url(request, submission_id),
            created_at=str(row.get("created_at", "")),
            idempotent_replay=not is_new,
        )
    )
    return JSONResponse(status_code=202 if is_new else 200, content=body)


@app.post("/command", response_model=DispatchResponse)
async def command(req: DispatchRequest):
    """Compatibility alias for older clients."""
    return await dispatch(req)


# ---------------------------------------------------------------------------
# Slack Events API webhook — Slack#crm-entry → n8n (Hermes Trigger) → here
# ---------------------------------------------------------------------------

_slack_signature_verifier = None
_slack_web_client = None


def _get_slack_signature_verifier():
    global _slack_signature_verifier
    if _slack_signature_verifier is None:
        secret = os.environ.get("SLACK_EVENTS_SIGNING_SECRET", "").strip()
        if not secret:
            return None
        from slack_sdk.signature import SignatureVerifier

        _slack_signature_verifier = SignatureVerifier(signing_secret=secret)
    return _slack_signature_verifier


def _get_slack_web_client():
    global _slack_web_client
    if _slack_web_client is None:
        token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
        if not token:
            return None
        from slack_sdk import WebClient

        _slack_web_client = WebClient(token=token)
    return _slack_web_client


def _process_crm_entry_text(text: str, *, channel_id: str, user_id: str | None, message_ts: str | None, thread_ts: str | None) -> None:
    """Run dispatcher and post the ack back to #crm-entry.

    Mirrors the Socket Mode handler's behavior: when the dispatcher returns
    `result.data["slack_blocks"]`, those interactive blocks (e.g. the agency
    intake approve buttons) are attached to the LAST message chunk so users
    can click rather than retype the approval token.
    """
    blocks: list[dict[str, Any]] | None = None
    try:
        espo = _get_espo()
        dispatcher = _get_dispatcher()
        dispatcher.set_slack_context(channel_id=channel_id, user_id=user_id, message_ts=message_ts)
        result = dispatcher.dispatch(espo, _strip_leading_slack_mention(text))
        ack = ("" if result.ok else ":warning: ") + (result.message or "")
        if isinstance(result.data, dict):
            candidate = result.data.get("slack_blocks")
            if isinstance(candidate, list) and candidate:
                blocks = candidate
    except Exception as exc:
        log.exception("Slack webhook dispatch failed channel=%s ts=%s", channel_id, message_ts)
        ack = f":warning: Hermes command failed: {exc}"
    web = _get_slack_web_client()
    if web is None:
        log.error("SLACK_BOT_TOKEN unset; cannot post ack to %s", channel_id)
        return
    chunks = _chunk_slack(ack)
    for idx, chunk in enumerate(chunks):
        # Only attach blocks to the final chunk so buttons aren't duplicated.
        post_kwargs: dict[str, Any] = {"channel": channel_id, "text": chunk, "thread_ts": thread_ts}
        if blocks and idx == len(chunks) - 1:
            post_kwargs["blocks"] = blocks
        try:
            web.chat_postMessage(**post_kwargs)
        except Exception:
            log.exception("Slack webhook ack post failed channel=%s ts=%s", channel_id, message_ts)
            return


def _strip_leading_slack_mention(text: str) -> str:
    return re.sub(r"^<@[^>]+>\s*", "", (text or "").strip()).strip()


def _chunk_slack(text: str, limit: int = 3500) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    rest = text
    while rest:
        parts.append(rest[:limit])
        rest = rest[limit:]
    return parts


@app.post("/api/hermes/slack/crm-entry")
async def slack_crm_entry_webhook(request: Request, background_tasks: BackgroundTasks):
    """Receive Slack Events API payload (forwarded by n8n) for the #crm-entry channel.

    n8n must forward the raw Slack request body and the `X-Slack-Signature` /
    `X-Slack-Request-Timestamp` headers verbatim so Hermes can verify the
    signature against `SLACK_EVENTS_SIGNING_SECRET`.
    """
    from hermes.integrations.slack_dedupe import CRM_ENTRY_CHANNEL, claim_event

    raw_body = await request.body()
    signature = request.headers.get("x-slack-signature", "")
    timestamp = request.headers.get("x-slack-request-timestamp", "")

    verifier = _get_slack_signature_verifier()
    if verifier is None:
        log.error("SLACK_EVENTS_SIGNING_SECRET not configured; refusing webhook")
        raise HTTPException(status_code=503, detail="Slack webhook not configured")
    if not signature or not timestamp or not timestamp.isdigit():
        raise HTTPException(status_code=401, detail="Missing Slack signature headers")
    if not verifier.is_valid(body=raw_body, timestamp=timestamp, signature=signature):
        raise HTTPException(status_code=401, detail="Invalid Slack signature")

    try:
        payload = json.loads(raw_body.decode("utf-8") or "{}")
    except ValueError:
        raise HTTPException(status_code=400, detail="Malformed JSON")

    if payload.get("type") == "url_verification":
        return PlainTextResponse(payload.get("challenge", ""))

    if payload.get("type") != "event_callback":
        return {"ok": True, "ignored": "non-event-callback"}

    event_id = payload.get("event_id") or ""
    if event_id and not claim_event(f"slack_event_id:{event_id}"):
        log.info("slack webhook Slack-retry of event_id=%s — skipping", event_id)
        return {"ok": True, "ignored": "slack-retry"}

    event = payload.get("event") or {}
    if event.get("type") != "message":
        return {"ok": True, "ignored": "non-message"}
    if event.get("bot_id") or event.get("subtype") in ("bot_message", "message_changed", "message_deleted"):
        return {"ok": True, "ignored": "bot-or-subtype"}
    hermes_bot_user_id = os.environ.get("HERMES_BOT_USER_ID", "")
    if hermes_bot_user_id and event.get("user") == hermes_bot_user_id:
        return {"ok": True, "ignored": "self-post"}
    if event.get("channel") != CRM_ENTRY_CHANNEL:
        return {"ok": True, "ignored": "wrong-channel"}
    text = event.get("text") or ""
    if "Hermes:" not in text or "MODULE:" not in text:
        return {"ok": True, "ignored": "no-hermes-block"}

    event_ts = event.get("ts") or ""
    if not claim_event(f"crm_entry_ts:{event_ts}"):
        log.info("crm_entry_ts=%s already handled by other transport — skipping", event_ts)
        return {"ok": True, "ignored": "cross-transport-duplicate"}

    thread_ts = event.get("thread_ts") or event.get("ts")
    background_tasks.add_task(
        _process_crm_entry_text,
        text,
        channel_id=event.get("channel"),
        user_id=event.get("user"),
        message_ts=event.get("ts"),
        thread_ts=thread_ts,
    )
    return {"ok": True, "queued": True, "event_id": event_id}


def main() -> int:
    load_dotenv()
    logging.basicConfig(level=os.environ.get("HERMES_API_LOG_LEVEL", "INFO"))
    parser = argparse.ArgumentParser(description="Hermes private HTTP API")
    parser.add_argument("--host", default=os.environ.get("HERMES_API_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("HERMES_API_PORT", "8484")))
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)
    return 0
