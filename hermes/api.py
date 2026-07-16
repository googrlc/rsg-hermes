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
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pathlib import Path
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

# CORS: restrict to an explicit allowlist read from HERMES_CORS_ALLOW_ORIGINS
# (comma-separated). Only browsers enforce CORS, so server-to-server callers
# (n8n, EspoCRM webhooks, Slack) are unaffected, and the same-origin
# /command-center UI needs no cross-origin grant. Defaults to no cross-origin
# access (fail closed). Never pair a wildcard origin with credentials: modern
# Starlette reflects the request Origin instead of sending "*", which would let
# any site read token/cookie-authenticated endpoints.
_cors_origins = [
    o.strip()
    for o in os.environ.get("HERMES_CORS_ALLOW_ORIGINS", "").split(",")
    if o.strip()
]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Agency Command Center static UI (served at /command-center/).
# Lives under hermes/webui/ and is bind-mounted in the container, so edits go
# live; only new API routes need an `up -d hermes-api` recreate.
_WEBUI_DIR = Path(__file__).parent / "webui"
if _WEBUI_DIR.is_dir():
    app.mount(
        "/command-center",
        StaticFiles(directory=str(_WEBUI_DIR), html=True),
        name="command-center",
    )

# New Command Center intake lane (routes under /api/command-center/intake,
# page at /api/command-center/intake/page). Additive; failure to load must not
# take down the rest of the API.
try:
    from hermes.command_center.api_routes import (
        dashboard_router as _cc_dash_router,
        router as _cc_intake_router,
    )

    app.include_router(_cc_intake_router)
    app.include_router(_cc_dash_router)
except Exception:  # pragma: no cover - surfaced in logs, never fatal
    log.exception("command_center routes unavailable")

# Walker on-demand renewal API (no scheduler, no timers).
try:
    from hermes.walker.router import router as _walker_router
    app.include_router(_walker_router)
except Exception:  # pragma: no cover - surfaced in logs, never fatal
    log.exception("walker routes unavailable")

_espo = None
_dispatcher = None
_supa = None
_nowcerts = None


def _get_nowcerts():
    """Lazy singleton for NowCertsClient. Reads NOWCERTS_USERNAME/PASSWORD from env."""
    global _nowcerts
    if _nowcerts is None:
        from hermes.sync.nowcerts_client import NowCertsClient

        _nowcerts = NowCertsClient()
    return _nowcerts


def _require_hermes_token(request: Request) -> None:
    """Bearer-token gate for mutating / privileged endpoints.

    Reads HERMES_API_TOKEN from env. If unset, the gate is disabled (dev mode);
    log a warning so it's visible.
    """
    expected = os.environ.get("HERMES_API_TOKEN")
    if not expected:
        log.warning("HERMES_API_TOKEN not set; bearer gate disabled")
        return
    auth = request.headers.get("authorization") or request.headers.get("Authorization") or ""
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")


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


class DispatchRequest(BaseModel):
    command: str | None = None
    confirm: bool = False
    crm_write: CRMWriteDispatchRequest | None = None


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


@app.get("/")
async def root():
    """Redirect visitors hitting the root URL to the Command Center UI."""
    return RedirectResponse(url="/command-center/", status_code=307)


@app.get("/cockpit")
async def cockpit():
    """RSG Agency CRM cockpit — 8-view CRM UI served from hermes/webui/cockpit.html."""
    return RedirectResponse(url="/command-center/cockpit.html", status_code=307)


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
    raise HTTPException(status_code=400, detail="Provide either crm_write or command.")


@app.get("/api/command-center/renewals")
async def command_center_renewals():
    """Live Renewals Cockpit data (Command Center, Phase 1).

    Server-side, service-role read of ``project_85_renewals`` aggregated into
    urgency buckets + the next-90-day list. No anon key ever reaches the browser.
    """
    from hermes.operations.renewal_tracker import summarize_renewals

    supa = _get_supa()
    rows = supa.select(
        "project_85_renewals",
        columns=(
            "id,policy_number,client_name,expiration_date,premium_current,"
            "premium_renewal,increase_percentage,risk_status,ai_strategy_notes,last_contact_date"
        ),
        params={"order": "expiration_date.asc"},
        limit=1000,
    )
    return summarize_renewals(rows)


@app.get("/api/command-center/tasks")
async def command_center_tasks():
    """Open team tasks (Gretchen/Lamar) in plain English, most urgent first."""
    from hermes.operations.team_queue import group_by_assignee, list_open_tasks

    tasks = list_open_tasks(_get_espo())
    return {"tasks": tasks, "count": len(tasks), "by_assignee": group_by_assignee(tasks)}


@app.post("/api/command-center/tasks/{task_id}/complete")
async def command_center_complete_task(task_id: str):
    """Mark a team task done (writes status=Completed back to EspoCRM)."""
    from hermes.operations.team_queue import complete_task

    try:
        complete_task(_get_espo(), task_id)
    except Exception as exc:
        log.exception("complete task failed: %s", task_id)
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True, "id": task_id, "status": "Completed"}


@app.get("/api/command-center/skills")
async def command_center_skills():
    """List Hermes's capabilities — live tools + domain playbooks."""
    from hermes.operations.skills_catalog import catalog

    return catalog()


class FileSaveRequest(BaseModel):
    title: str
    content: str
    kind: str = "note"
    content_type: str = "text/markdown"
    file_ext: str = "md"


@app.post("/api/command-center/files")
async def command_center_save_file(req: FileSaveRequest):
    """Save a file Hermes created (note/report/answer/save-list) for the Files panel."""
    from hermes.operations.files_store import save_file

    if not (req.content or "").strip():
        raise HTTPException(status_code=400, detail="Empty content.")
    try:
        return save_file(
            _get_supa(),
            title=req.title,
            content=req.content,
            kind=req.kind,
            content_type=req.content_type,
            file_ext=req.file_ext,
        )
    except Exception as exc:
        log.exception("save file failed: %s", req.title)
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/command-center/files")
async def command_center_list_files():
    """List files Hermes has created (newest first)."""
    from hermes.operations.files_store import list_files

    return {"files": list_files(_get_supa())}


@app.get("/api/command-center/files/{file_id}/download")
async def command_center_download_file(file_id: str):
    """Download a Hermes file as an attachment."""
    from hermes.operations.files_store import download_filename, get_file

    row = get_file(_get_supa(), file_id)
    if not row:
        raise HTTPException(status_code=404, detail="File not found.")
    return Response(
        content=row.get("content") or "",
        media_type=row.get("content_type") or "text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{download_filename(row)}"'},
    )


class AskRequest(BaseModel):
    prompt: str


@app.post("/api/command-center/ask")
async def command_center_ask(req: AskRequest):
    """Ask Hermes from the Command Center command bar (Phase 3).

    Routes the prompt through the real dispatcher. Read-only posture: write-intent
    prompts are never auto-confirmed — they return a nudge to use the proper flow.
    """
    prompt = (req.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Empty prompt.")

    # Renewal/at-risk questions: answer conversationally from grounded data. The
    # dispatcher's "who/what/find" route would otherwise treat "who renews this
    # week" as a name search.
    from hermes.operations.command_center_qa import answer_question, is_renewal_intent

    if is_renewal_intent(prompt):
        try:
            cc_answer = answer_question(_get_supa(), prompt)
            if cc_answer is not None:
                return {"ok": True, "message": cc_answer, "source": "command-center"}
        except Exception:
            log.exception("command-center renewals answer failed; using dispatcher: %s", prompt)

    # Everything else → the conversational agent directly (knows it's Lamar at RSG;
    # has CRM lookups, reports, renewals, and web_research). Going straight to the
    # agent avoids the dispatcher's "who/what/find" route hijacking natural questions
    # into a name search. confirmed=False → any write intent is previewed, never run.
    from hermes.core.nl_agent import ask as nl_ask

    try:
        result = nl_ask(_get_espo(), prompt, confirmed=False)
    except Exception as exc:
        log.exception("command-center ask failed: %s", prompt)
        raise HTTPException(status_code=502, detail=str(exc))
    data = result.data if isinstance(result.data, dict) else None
    return {
        "ok": result.ok,
        "message": result.message,
        "data": result.data,
        "requires_confirmation": bool(data.get("requires_confirmation")) if data else False,
    }


@app.get("/api/command-center/retention")
async def command_center_retention():
    """Latest retention snapshot for the loud Retention card (Phase 2)."""
    supa = _get_supa()
    snap = supa.select("agency_snapshots", params={"order": "snapshot_date.desc"}, limit=1)
    s = snap[0] if snap else {}
    return {
        "retention_rate": s.get("retention_rate"),
        "snapshot_date": s.get("snapshot_date"),
        "benchmark": 84.0,
        "active_premium": s.get("active_premium"),
        "client_count": s.get("client_count"),
        "policy_count": s.get("policy_count"),
    }


class SaveListRequest(BaseModel):
    limit: int = 10
    within_days: int = 60


@app.post("/api/command-center/save-list")
async def command_center_build_save_list(req: SaveListRequest):
    """Build + stage a retention save-list (top at-risk renewals → DRAFT outreach).

    Writes DRAFT rows only; nothing is auto-sent. The sole write action in Phase 2.
    """
    from hermes.operations.save_list import create_save_list

    return create_save_list(_get_supa(), limit=req.limit, within_days=req.within_days)


@app.get("/api/command-center/save-list")
async def command_center_list_save_list():
    """Open (DRAFT) outreach awaiting human review/send."""
    from hermes.operations.save_list import list_open_drafts

    return {"drafts": list_open_drafts(_get_supa())}


# ── Opportunities (sales pipeline) — sanctioned create/list/search for any cockpit ──
class OpportunityCreateRequest(BaseModel):
    line_of_business: str
    client_identifier: str | None = None
    insured_name: str | None = None
    fein: str | None = None
    insured_id: str | None = None            # NowCerts insured guid
    prospect_type: str | None = None
    insured_type: str | None = None
    stage: str = "New"
    premium_estimate: float | None = None
    carrier: str | None = None
    lead_source: str | None = None
    assigned_to: str | None = None
    next_action: str | None = None
    source: str = "manual"
    created_by: str | None = None

    @model_validator(mode="after")
    def _need_client(self):
        if not (self.client_identifier or self.insured_name):
            raise ValueError("client_identifier or insured_name is required")
        return self


@app.post("/api/opportunities")
async def create_opportunity_endpoint(req: OpportunityCreateRequest):
    """Create (or return existing) a pipeline opportunity for ANY client — new,
    inactive, or a cross-sell on a current client. Idempotent per
    (client_identifier, line_of_business); the smart create logic (identifier,
    dedup, insured link) lives in one place so every cockpit writes correctly.
    """
    from hermes.intake import opportunities as opp

    ci = req.client_identifier or opp.make_client_identifier(req.insured_name, req.fein)
    stage = (req.stage or opp.STAGE_NEW).strip()
    if stage not in opp.STAGES:
        raise HTTPException(status_code=400, detail=f"Unknown stage '{stage}'; must be one of {list(opp.STAGES)}")
    try:
        row, created = opp.create_opportunity(
            _get_supa(),
            client_identifier=ci,
            line_of_business=req.line_of_business,
            insured_name=req.insured_name,
            insured_id=req.insured_id,
            prospect_type=req.prospect_type,
            insured_type=req.insured_type,
            stage=stage,
            premium_estimate=req.premium_estimate,
            carrier=req.carrier,
            lead_source=req.lead_source,
            assigned_to=req.assigned_to,
            next_action=req.next_action,
            source=req.source,
            created_by=req.created_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        log.exception("create opportunity failed: %s / %s", ci, req.line_of_business)
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True, "created": created, "opportunity": row}


@app.get("/api/opportunities")
async def list_opportunities_endpoint(stage: str | None = None, status: str | None = "open", limit: int = 100):
    """List pipeline opportunities (default open), newest-updated first."""
    from hermes.intake import opportunities as opp

    try:
        rows = opp.list_opportunities(_get_supa(), stage=stage, status=status, limit=limit)
    except Exception as exc:
        log.exception("list opportunities failed")
        raise HTTPException(status_code=502, detail=str(exc))
    return {"opportunities": rows, "count": len(rows)}


class SendQuoteRequest(BaseModel):
    approved_by: str


@app.post("/api/opportunities/{opportunity_id}/send-to-nowcerts")
async def send_opportunity_quote(opportunity_id: str, req: SendQuoteRequest):
    """Approved push: enqueue this opportunity to NowCerts as a quote (Policy · IsQuote).
    Writes nothing synchronously — the quote executor completes it and stamps the
    quote id/number back onto the opportunity. approved_by must be a real user."""
    from hermes.quotes.executor import stage_quote_job

    supa = _get_supa()
    _require_users(supa, [("approved_by", req.approved_by)])
    rows = supa.select("opportunities", columns="*", params={"id": f"eq.{opportunity_id}"}, limit=1)
    if not rows:
        raise HTTPException(status_code=404, detail="opportunity not found")
    try:
        job = stage_quote_job(supa, opportunity=rows[0], approved_by=req.approved_by)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        log.exception("send quote failed: %s", opportunity_id)
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True, "queued": True, "queue_id": job.get("id"),
            "note": "Quote queued to NowCerts (approved). It writes when the quote executor runs."}


@app.get("/api/clients/search")
async def search_clients_endpoint(q: str, limit: int = 20):
    """Search the canonical book by insured name — powers the New-Opportunity client
    picker (active OR inactive clients). Returns the NowCerts guid + display fields.
    """
    query = (q or "").strip()
    if len(query) < 2:
        return {"clients": [], "count": 0}
    try:
        rows = _get_supa().select(
            "canonical_clients",
            columns="nowcerts_insured_guid,insured_name,client_type,city,state,email,phone",
            params={"insured_name": f"ilike.*{query}*", "order": "insured_name.asc"},
            limit=limit,
        )
    except Exception as exc:
        log.exception("client search failed: %s", query)
        raise HTTPException(status_code=502, detail=str(exc))
    return {"clients": rows, "count": len(rows)}


# ── Cases + Tasks (workflow) — sanctioned create/list for any cockpit ──
def _active_user_emails(supa) -> set[str]:
    rows = supa.select(
        "agency_crm_users", columns="email",
        params={"active": "eq.true"}, limit=1000,
    )
    return {str(r.get("email")).lower() for r in rows if r.get("email")}


def _require_users(supa, pairs: list[tuple[str, str | None]]) -> None:
    """Reject any *_email that isn't an active agency_crm_users identity.

    This is the API-level guard for the FK that made CRM task creation fail
    silently — the cockpit picks emails from /api/agency-users, never free-typed.
    """
    valid = _active_user_emails(supa)
    for label, email in pairs:
        if email and email.lower() not in valid:
            raise HTTPException(
                status_code=400,
                detail=f"{label} '{email}' is not an active agency_crm_users identity",
            )


@app.get("/api/agency-users")
async def list_agency_users_endpoint():
    """Active CRM users — powers owner/assignee pickers (valid FK targets)."""
    rows = _get_supa().select(
        "agency_crm_users", columns="email,display_name,role,active",
        params={"active": "eq.true", "order": "display_name.asc"}, limit=200,
    )
    return {"users": rows, "count": len(rows)}


class CaseCreateRequest(BaseModel):
    title: str
    case_type: str = "service"          # renewal|service|claims|marketing|endorsement|...
    description: str | None = None
    priority: str = "medium"
    owner_email: str
    created_by_email: str | None = None
    insured_name: str | None = None
    insured_database_id: str | None = None   # NowCerts insured guid
    policy_number: str | None = None
    due_at: str | None = None


@app.post("/api/cases")
async def create_case_endpoint(req: CaseCreateRequest):
    """Create a general agency_crm_cases row (any case_type) for any cockpit.
    Owner/creator emails are validated against agency_crm_users (FK guard)."""
    import uuid

    from hermes.renewals import cases as C

    supa = _get_supa()
    creator = req.created_by_email or C._service_email()
    _require_users(supa, [("owner_email", req.owner_email), ("created_by_email", creator)])

    case_number = (
        f"{(req.case_type or 'CASE')[:3].upper()}-"
        f"{datetime.utcnow().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
    )
    try:
        case = supa.insert("agency_crm_cases", C._compact({
            "case_type": req.case_type,
            "case_number": case_number,
            "title": req.title,
            "description": req.description,
            "status": "open",
            "priority": req.priority or "medium",
            "owner_email": req.owner_email,
            "created_by_email": creator,
            "insured_name": req.insured_name,
            "insured_database_id": req.insured_database_id,
            "policy_number": req.policy_number,
            "due_at": req.due_at,
        }))
        C.log_case_event(
            supa, case_id=str(case.get("id")), event_type="case_created",
            summary=f"{req.case_type} case opened: {req.title}", actor_email=creator,
        )
    except Exception as exc:
        log.exception("create case failed: %s", req.title)
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True, "case": case}


@app.get("/api/cases")
async def list_cases_endpoint(status: str | None = None, case_type: str | None = None, limit: int = 100):
    """List cases, newest first."""
    params: dict[str, str] = {"order": "created_at.desc"}
    if status:
        params["status"] = f"eq.{status}"
    if case_type:
        params["case_type"] = f"eq.{case_type}"
    rows = _get_supa().select("agency_crm_cases", columns="*", params=params, limit=limit)
    return {"cases": rows, "count": len(rows)}


class TaskCreateRequest(BaseModel):
    case_id: str
    title: str
    description: str | None = None
    priority: str = "medium"
    assigned_to_email: str | None = None
    created_by_email: str | None = None
    due_at: str | None = None


@app.post("/api/tasks")
async def create_task_endpoint(req: TaskCreateRequest):
    """Create a task under a case. assigned_to/created_by validated vs agency_crm_users."""
    from hermes.renewals import cases as C

    supa = _get_supa()
    creator = req.created_by_email or C._service_email()
    _require_users(supa, [("assigned_to_email", req.assigned_to_email), ("created_by_email", creator)])

    try:
        created = C.create_tasks(
            supa, case_id=req.case_id,
            tasks=[{"title": req.title, "description": req.description,
                    "assigned_to_email": req.assigned_to_email}],
            created_by_email=creator,
        )
    except Exception as exc:
        log.exception("create task failed: %s", req.title)
        raise HTTPException(status_code=502, detail=str(exc))
    if not created:
        # Title already exists under this case (idempotent no-op).
        return {"ok": True, "created": False, "task": None}
    return {"ok": True, "created": True, "task": created[0]}


@app.get("/api/tasks")
async def list_tasks_endpoint(case_id: str | None = None, limit: int = 200):
    """List tasks, optionally scoped to a case."""
    params: dict[str, str] = {"order": "created_at.desc"}
    if case_id:
        params["case_id"] = f"eq.{case_id}"
    rows = _get_supa().select("agency_crm_tasks", columns="*", params=params, limit=limit)
    return {"tasks": rows, "count": len(rows)}


@app.get("/api/cases/{case_id}/documents")
async def case_documents_endpoint(case_id: str):
    """Nextcloud document links filed against a case (agency_crm_document_links)."""
    rows = _get_supa().select(
        "agency_crm_document_links", columns="*",
        params={"case_id": f"eq.{case_id}", "order": "created_at.desc"}, limit=200,
    )
    return {"documents": rows, "count": len(rows)}


# ── Book reads + Workspace KPIs (power the CRM cockpit views) ──
@app.get("/api/clients")
async def list_clients_endpoint(limit: int = 500):
    """Full canonical client book (read-only mirror)."""
    rows = _get_supa().select(
        "canonical_clients",
        columns="nowcerts_insured_guid,insured_name,client_type,city,state,email,phone",
        params={"order": "insured_name.asc"}, limit=limit,
    )
    return {"clients": rows, "count": len(rows)}


@app.get("/api/clients/{insured_guid}")
async def client_360_endpoint(insured_guid: str):
    """Client 360 — the insured's record plus their whole book: policies,
    opportunities, and cases, keyed on the NowCerts insured GUID."""
    supa = _get_supa()

    def sel(table, cols, params):
        try:
            return supa.select(table, columns=cols, params=params, limit=500)
        except Exception:
            return []

    client = sel("canonical_clients", "*", {"nowcerts_insured_guid": f"eq.{insured_guid}"})
    policies = sel(
        "canonical_policies",
        "policy_number,carrier,lines_of_business,status,effective_date,expiration_date,annualized_premium,premium_amount",
        {"nowcerts_insured_guid": f"eq.{insured_guid}", "order": "expiration_date.asc"},
    )
    opportunities = sel(
        "opportunities", "id,line_of_business,stage,status,premium_estimate,carrier,quote_number,next_action",
        {"insured_id": f"eq.{insured_guid}", "order": "updated_at.desc"},
    )
    cases = sel(
        "agency_crm_cases", "id,case_number,title,case_type,status,priority,created_at",
        {"insured_database_id": f"eq.{insured_guid}", "order": "created_at.desc"},
    )
    return {
        "client": client[0] if client else None,
        "policies": policies, "opportunities": opportunities, "cases": cases,
        "counts": {"policies": len(policies), "opportunities": len(opportunities), "cases": len(cases)},
    }


@app.get("/api/policies")
async def list_policies_endpoint(limit: int = 1000):
    """Canonical policy book (read-only mirror), soonest-expiring first."""
    rows = _get_supa().select(
        "canonical_policies",
        columns="policy_guid,policy_number,nowcerts_insured_guid,carrier,lines_of_business,status,"
                "effective_date,expiration_date,premium_amount,annualized_premium,agency_commission_amount,state",
        params={"order": "expiration_date.asc"}, limit=limit,
    )
    return {"policies": rows, "count": len(rows)}


@app.get("/api/commissions")
async def list_commissions_endpoint(limit: int = 1000):
    """Commission ledger (expected vs actual), newest statement first."""
    rows = _get_supa().select(
        "commission_ledger",
        columns="policy_number,client_name,carrier_name,lob,gross_premium,expected_commission,"
                "actual_commission,delta,reconciliation_status,statement_date",
        params={"order": "statement_date.desc"}, limit=limit,
    )
    return {"commissions": rows, "count": len(rows)}


@app.get("/api/workspace-stats")
async def workspace_stats_endpoint():
    """KPI tile counts for the Workspace home."""
    supa = _get_supa()

    def _rows(table, cols, params=None):
        try:
            return supa.select(table, columns=cols, params=params, limit=100000)
        except Exception:
            return []

    policies = _rows("canonical_policies", "annualized_premium,premium_amount")
    annualized = sum(
        float(p.get("annualized_premium") or p.get("premium_amount") or 0) for p in policies
    )
    return {
        "clients": len(_rows("canonical_clients", "nowcerts_insured_guid")),
        "policies": len(policies),
        "annualized_premium": round(annualized, 2),
        "renewals": len(_rows("project_85_renewals", "id")),
        "pipeline": len(_rows("opportunities", "id", {"status": "eq.open"})),
        "open_cases": len(_rows("agency_crm_cases", "id", {"status": "eq.open"})),
        "open_tasks": len(_rows("agency_crm_tasks", "id", {"status": "neq.completed"})),
        "commissions": len(_rows("commission_ledger", "id")),
    }


@app.get("/api/hermes/sync-health")
async def sync_health():
    """Queue-centric health snapshot for dashboard SyncHealthCheck component."""
    supa = _get_supa()
    crm_pending = supa.select("crm_write_queue", columns="id", params={"status": "eq.PENDING"}, limit=1000)
    crm_processing = supa.select("crm_write_queue", columns="id", params={"status": "eq.PROCESSING"}, limit=1000)
    crm_failed = supa.select("crm_write_queue", columns="id", params={"status": "eq.FAILED"}, limit=1000)

    latest_run = supa.select("sync_runs", params={"order": "created_at.desc"}, limit=1)
    latest = latest_run[0] if latest_run else {}

    return {
        "status": "ok",
        "crm_write_queue": {
            "pending": len(crm_pending),
            "processing": len(crm_processing),
            "failed": len(crm_failed),
        },
        "latest_sync_run": {
            "id": latest.get("id"),
            "status": latest.get("status"),
            "workflow_name": latest.get("workflow_name"),
            "finished_at": latest.get("finished_at"),
        },
    }


# ---------------------------------------------------------------------------
# Book-sync health — compares actual book of business across NowCerts, EspoCRM
# and Supabase. Read-only; complements /api/hermes/sync-health (queue depth).
# See hermes/book_sync/health.py.
# ---------------------------------------------------------------------------


@app.get("/api/hermes/book-sync")
async def book_sync_health(request: Request, max_pages: int = 50):
    """Drift report: policy counts, per-carrier premium, orphans, rate drift.

    Gated by HERMES_API_TOKEN bearer (skipped if env var unset).

    Query params:
      max_pages: cap NowCerts pagination (default 50 → ~5000 policies).
    """
    _require_hermes_token(request)
    from hermes.book_sync import run_book_sync_health

    try:
        report = run_book_sync_health(
            nowcerts_client=_get_nowcerts(),
            espo_client=_get_espo(),
            supa=_get_supa(),
            max_pages=max_pages,
        )
    except Exception as exc:  # pragma: no cover - defensive top-level
        log.exception("book-sync health failed")
        raise HTTPException(status_code=500, detail=f"book-sync failed: {exc}")

    return report.to_dict()


# ---------------------------------------------------------------------------
# AMS insured search — the search-before-insert gate used by the bridge's
# `ams_search_insured` tool (GET /api/ams/search-insured?name=&email=&fein=).
# Read-only proxy to NowCerts InsuredList. See sync/nowcerts_client.py.
# ---------------------------------------------------------------------------


@app.get("/api/ams/search-insured")
async def ams_search_insured(
    request: Request,
    name: str | None = None,
    email: str | None = None,
    fein: str | None = None,
):
    """Search the Momentum AMS for existing insureds by name, email, or FEIN.

    At least one of name/email/fein is required. Returns matching insureds so
    callers can dedup before creating one. Read-only.
    """
    _require_hermes_token(request)

    if not any([name, email, fein]):
        raise HTTPException(status_code=400, detail="Provide at least one of: name, email, fein")

    from hermes.sync.nowcerts_client import NowCertsClientError

    def _q(val: str) -> str:
        return val.replace("'", "''")  # OData single-quote escape

    filters: list[str] = []
    if email:
        filters.append(f"eMail eq '{_q(email)}'")
    if fein:
        filters.append(f"fein eq '{_q(fein)}'")
    if name:
        filters.append(f"commercialName eq '{_q(name)}'")

    nc = _get_nowcerts()
    matches: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        for flt in filters:
            body = nc._get("/api/InsuredList", params={"$filter": flt})
            rows = body if isinstance(body, list) else body.get("value", [])
            for r in rows:
                if not isinstance(r, dict):
                    continue
                gid = str(r.get("id") or "")
                if not gid or gid in seen:
                    continue
                seen.add(gid)
                matches.append({
                    "id": gid,
                    "commercialName": r.get("commercialName"),
                    "firstName": r.get("firstName"),
                    "lastName": r.get("lastName"),
                    "email": r.get("eMail"),
                    "fein": r.get("fein"),
                    "phone": r.get("phone"),
                })
    except NowCertsClientError as exc:
        log.exception("ams search-insured failed")
        raise HTTPException(status_code=502, detail=f"AMS search failed: {exc}")

    return {
        "query": {"name": name, "email": email, "fein": fein},
        "count": len(matches),
        "matches": matches,
    }


# ---------------------------------------------------------------------------
# CRM change proposals — staged EspoCRM field edits awaiting in-chat approval.
# Approve enqueues a crm_write_queue row; the hermes-crm-queue-worker commits to
# EspoCRM. Nothing here writes to EspoCRM directly. See operations/crm_proposals.py.
# ---------------------------------------------------------------------------


class CRMProposalCreateRequest(BaseModel):
    entity: str
    after: dict[str, Any]
    op: str = "upsert"
    match_key: str | None = None
    espocrm_id: str | None = None
    before: dict[str, Any] | None = None
    rationale: str | None = None
    confidence: float | None = None
    source: str | None = None
    proposed_by: str = "agent"


class CRMProposalApproveRequest(BaseModel):
    reviewer: str = "lamar"


class CRMProposalRejectRequest(BaseModel):
    reviewer: str = "lamar"
    reason: str | None = None


@app.post("/api/crm/proposals")
async def crm_proposals_create(req: CRMProposalCreateRequest):
    """Stage a proposed EspoCRM field edit (status=pending) for later approval.

    `after` must use EspoCRM field names (load the espocrm field-reference skill
    first). For op=upsert/update, espocrm_id is required; for op=create it must be
    absent. No EspoCRM write happens here.
    """
    from hermes.operations.crm_proposals import ProposalError, create_proposal
    try:
        return create_proposal(
            _get_supa(),
            entity=req.entity,
            after=req.after,
            op=req.op,
            match_key=req.match_key,
            espocrm_id=req.espocrm_id,
            before=req.before,
            rationale=req.rationale,
            confidence=req.confidence,
            source=req.source,
            proposed_by=req.proposed_by,
        )
    except ProposalError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    except Exception as exc:
        log.exception("crm_proposals_create failed")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/crm/proposals")
async def crm_proposals_list(status: str | None = None, limit: int = 50):
    """List proposals, optionally filtered by status. Pending first by default."""
    from hermes.operations.crm_proposals import list_proposals
    try:
        rows = list_proposals(_get_supa(), status=status, limit=limit)
        return {"proposals": rows, "count": len(rows)}
    except Exception as exc:
        log.exception("crm_proposals_list failed")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/crm/proposals/{proposal_id}/approve")
async def crm_proposals_approve(proposal_id: str, req: CRMProposalApproveRequest):
    """Approve a pending proposal: enqueue a crm_write_queue row.

    The hermes-crm-queue-worker commits the enqueued row to EspoCRM asynchronously
    (hooks/ACL/Stream fire through EspoClient). This endpoint is the in-chat
    committer — it never bypasses staging or the review gate.
    """
    from hermes.operations.crm_proposals import ProposalError, approve_proposal
    try:
        return approve_proposal(_get_supa(), proposal_id, reviewer=req.reviewer, espo=_get_espo())
    except ProposalError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    except Exception as exc:
        log.exception("crm_proposals_approve failed for %s", proposal_id)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/crm/proposals/{proposal_id}/reject")
async def crm_proposals_reject(proposal_id: str, req: CRMProposalRejectRequest):
    """Reject a pending (or not-yet-committed approved) proposal. No write occurs."""
    from hermes.operations.crm_proposals import ProposalError, reject_proposal
    try:
        return reject_proposal(_get_supa(), proposal_id, reviewer=req.reviewer, reason=req.reason)
    except ProposalError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    except Exception as exc:
        log.exception("crm_proposals_reject failed for %s", proposal_id)
        raise HTTPException(status_code=500, detail=str(exc))


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
    """One document index row (title, preview, supermemory_id, …)."""
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


@app.post("/renewals/complete")
async def renewals_complete_webhook(request: Request):
    """EspoCRM service.task_completed webhook for Renewal tasks.

    Auth is the shared X-Service-Webhook-Secret header (EspoCRM config
    serviceWebhookSecret == Hermes env SERVICE_WEBHOOK_SECRET).
    """
    from hermes.renewals import complete as renewals_complete

    if not renewals_complete.verify_secret(request.headers.get("X-Service-Webhook-Secret")):
        raise HTTPException(status_code=401, detail="bad webhook secret")
    return renewals_complete.handle(await request.json())


@app.post("/api/hermes/nowcerts-enrich")
async def nowcerts_enrich_webhook(request: Request):
    """EspoCRM webhook: enrich the linked NowCerts insured from an ACTIVE account.

    Auth: shared X-Service-Webhook-Secret header (== SERVICE_WEBHOOK_SECRET).
    Enrich-only — only accounts with lifecycle_status=Active and a
    momentum_client_id are pushed, via NowCerts upsert-by-DatabaseId, so it can
    never create a new insured. Add ?dry_run=1 to preview the payload without
    writing to the AMS. Accepts an EspoCRM webhook array or a single record.
    """
    from hermes.renewals import complete as renewals_complete

    if not renewals_complete.verify_secret(request.headers.get("X-Service-Webhook-Secret")):
        raise HTTPException(status_code=401, detail="bad webhook secret")

    body = await request.json()
    records = body if isinstance(body, list) else [body]
    ids = [
        (r.get("id") or r.get("accountId") or r.get("entityId"))
        for r in records
        if isinstance(r, dict) and (r.get("id") or r.get("accountId") or r.get("entityId"))
    ]
    if not ids:
        raise HTTPException(status_code=400, detail="no account id in webhook payload")

    dry = str(request.query_params.get("dry_run", "")).lower() in ("1", "true", "yes")

    from hermes.core.client import EspoClient
    from hermes.sync.enrich import enrich_insured_from_account
    from hermes.sync.nowcerts_client import NowCertsClient

    espo = EspoClient()
    nc = NowCertsClient()
    results = [enrich_insured_from_account(espo, nc, i, dry_run=dry) for i in ids]
    return {"count": len(results), "dry_run": dry, "results": results}



# ---------------------------------------------------------------------------
# Voice output — TTS endpoint for Slack voice clips.
# ---------------------------------------------------------------------------

class TTSRequest(BaseModel):
    """Text-to-speech request. Posts an audio clip to a Slack channel or DM."""
    text: str = Field(..., min_length=1, max_length=4000)
    channel: str = Field(
        default="",
        description="Slack channel ID or user ID. Defaults to HERMES_SENTINEL_SLACK_CHANNEL.",
    )
    voice: str = Field(
        default="",
        description="TTS voice name. Defaults to en-US-AriaNeural (Edge TTS, free).",
    )


async def _generate_tts_audio(text: str, voice: str) -> bytes | None:
    """Generate audio bytes from text. Uses edge-tts (free) if available,
    falls back to LiteLLM/OpenAI TTS API if configured.

    Returns MP3 bytes, or None on failure.
    """
    # Try edge-tts first (free, no API key).
    try:
        import edge_tts

        communicate = edge_tts.Communicate(text, voice or "en-US-AriaNeural")
        chunks: list[bytes] = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                chunks.append(chunk["data"])
        return b"".join(chunks)
    except ImportError:
        pass
    except Exception:
        log.exception("edge-tts generation failed; trying API fallback")

    # Fallback: TTS via LiteLLM (use the voice_output model group).
    try:
        from hermes.core.llm_client import get_client

        client = get_client()
        response = client.audio.speech.create(
            model=os.environ.get("HERMES_TTS_MODEL", "voice_output"),
            voice=voice or "alloy",
            input=text,
        )
        return response.content
    except Exception:
        log.exception("TTS API fallback also failed")
        return None


@app.post("/api/hermes/tts")
async def hermes_tts(req: TTSRequest, request: Request):
    """Generate a voice clip from text and post it to Slack.

    Uses Edge TTS (en-US-AriaNeural) by default — free, no API key.
    Falls back to the LiteLLM/OpenAI TTS API if edge-tts isn't installed.

    Auth: bearer token (HERMES_API_TOKEN) when configured.
    """
    _require_hermes_token(request)

    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    channel = req.channel.strip() or os.environ.get("HERMES_SENTINEL_SLACK_CHANNEL", "")
    if not channel:
        raise HTTPException(status_code=400, detail="no Slack channel configured")

    audio = await _generate_tts_audio(text, req.voice)
    if not audio:
        raise HTTPException(status_code=502, detail="TTS generation failed (no provider available)")

    # Post audio to Slack as a file upload.
    try:
        import io
        client = _get_slack_web_client()
        if client is None:
            raise HTTPException(status_code=503, detail="Slack web client not configured (SLACK_BOT_TOKEN missing)")

        client.files_upload_v2(
            channel=channel,
            file=io.BytesIO(audio),
            filename="hermes_voice.mp3",
            title="Hermes",
            initial_comment=text[:500],
        )
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("Slack voice post failed")
        raise HTTPException(status_code=502, detail=f"Slack post failed: {exc}")

    return {"ok": True, "channel": channel, "chars": len(text)}



def main() -> int:
    load_dotenv()
    logging.basicConfig(level=os.environ.get("HERMES_API_LOG_LEVEL", "INFO"))

    if not os.environ.get("SERVICE_WEBHOOK_SECRET", "").strip():
        log.warning(
            "SERVICE_WEBHOOK_SECRET is not set — all /renewals/complete and "
            "service webhook requests will be rejected (401). "
            "Set it in .env and recreate the container with "
            "`docker compose up -d hermes-api` (restart does not reload env_file)."
        )

    parser = argparse.ArgumentParser(description="Hermes private HTTP API")
    parser.add_argument("--host", default=os.environ.get("HERMES_API_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("HERMES_API_PORT", "8484")))
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)
    return 0
