"""Hermes REST API — FastAPI wrapper around the Dispatcher."""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
import re
from datetime import date, datetime, timedelta
from typing import Any, Literal

import uvicorn
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field, model_validator

from hermes.ams import book as ams_book

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
    description="Agency CRM coordination middleware — sync, lookup, data quality, and more.",
    version="0.1.0",
)

# CORS: restrict to an explicit allowlist read from HERMES_CORS_ALLOW_ORIGINS
# (comma-separated). Only browsers enforce CORS, so server-to-server callers
# (n8n, NowCerts webhooks, Slack) are unaffected, and the same-origin
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

# General document extractor (OCR-aware quote-field extraction) — POST /api/extract.
try:
    from hermes.command_center.extract_api import router as _extract_router

    app.include_router(_extract_router)
except Exception:  # pragma: no cover - surfaced in logs, never fatal
    log.exception("extract routes unavailable")

_dispatcher = None
_supa = None
_nowcerts = None


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


def _get_nowcerts():
    """The shared NowCertsClient. Reads NOWCERTS_USERNAME/PASSWORD from env.

    Delegates to ``nowcerts_client.get_client()`` rather than keeping a second
    singleton of its own: two singletons meant two tokens and two ~26s password
    grants in one process, and the API and the book reads each paying their own.
    """
    global _nowcerts
    if _nowcerts is None:
        from hermes.sync.nowcerts_client import get_client

        _nowcerts = get_client()
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


class DispatchRequest(BaseModel):
    command: str | None = None
    confirm: bool = False


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
async def cockpit(request: Request):
    """RSG Agency CRM cockpit — 8-view CRM UI served from hermes/webui/cockpit.html.
    Forwards the query string so theme params (e.g. ?u=gretchen) survive the redirect."""
    q = request.url.query
    return RedirectResponse(url="/command-center/cockpit.html" + (f"?{q}" if q else ""), status_code=307)


@app.get("/workspace")
async def workspace(request: Request):
    """RSG Master Workspace — the unified shell: every hub as a lane, each with its own
    scoped AI assistant (served from hermes/webui/workspace.html)."""
    q = request.url.query
    return RedirectResponse(url="/command-center/workspace.html" + (f"?{q}" if q else ""), status_code=307)


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
        dispatcher = _get_dispatcher()
        result = dispatcher.dispatch(req.command, confirmed=req.confirm)
        return DispatchResponse(ok=result.ok, message=result.message, data=result.data, requires_confirmation=False)
    except Exception as exc:
        log.exception("Dispatch failed for command: %s", req.command)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/hermes/dispatch", response_model=AsyncAcceptedResponse)
async def dashboard_dispatch(req: DispatchRequest):
    """Dashboard async dispatch entrypoint."""
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
    raise HTTPException(status_code=400, detail="Provide a command.")


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


@app.get("/api/command-center/lapse-check")
async def command_center_lapse_check():
    """Past-due-but-still-active renewals, kept OFF the forward renewals pipeline.

    The renewals board only carries the forward window (June-1 floor → +120 days);
    expired-but-active policies route here instead — likely silent lapses to
    confirm in NowCerts. Derived from ``renewal_candidates`` (needs_verification)."""
    from hermes.renewals.candidate_refresh import lapse_check

    return lapse_check(_get_supa())


@app.get("/api/command-center/tasks")
async def command_center_tasks():
    """Open team tasks (Gretchen/Lamar) in plain English, most urgent first."""
    # EspoCRM decommissioned 2026-07-23. Team tasks now live in Supabase
    # (agency_crm_tasks); read open tasks there, grouped by assignee, most urgent
    # first. No Espo call, so this polled endpoint can never hang the pool.
    supa = _get_supa()
    rows = supa.select(
        "agency_crm_tasks",
        columns="id,title,status,priority,due_at,assigned_to_email,case_id",
        params={"status": "not.in.(completed,cancelled,canceled,done)", "order": "due_at.asc.nullslast"},
        limit=200,
    )

    def _who(email):
        e = (email or "").lower()
        if "lamar" in e:
            return "Lamar"
        if "gretchen" in e:
            return "Gretchen"
        return (e.split("@")[0] or "Unassigned").title()

    tasks = [{**r, "assignee": _who(r.get("assigned_to_email"))} for r in rows]
    by_assignee: dict[str, list] = {}
    for t in tasks:
        by_assignee.setdefault(t["assignee"], []).append(t)
    return {"tasks": tasks, "count": len(tasks), "by_assignee": by_assignee, "source": "agency_crm_tasks"}


@app.post("/api/command-center/tasks/{task_id}/complete")
async def command_center_complete_task(task_id: str):
    """Mark a team task done in agency_crm_tasks."""
    from hermes.operations.team_queue import complete_task

    try:
        row = complete_task(_get_supa(), task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        log.exception("complete task failed: %s", task_id)
        raise HTTPException(status_code=502, detail=str(exc))
    if row is None:
        raise HTTPException(status_code=404, detail="task not found")
    return {"ok": True, "id": task_id, "status": "completed"}


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
    persona: str | None = None
    hub: str | None = None


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

    if not req.hub and is_renewal_intent(prompt):
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
        result = nl_ask(prompt, confirmed=False, persona=(req.persona or None), hub=(req.hub or None))
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
    opportunity_type: str | None = None      # NowCerts type; defaults to New Business
    prospect_type: str | None = None
    insured_type: str | None = None
    stage: str | None = None                 # defaults to the type's first stage
    premium_estimate: float | None = None
    carrier: str | None = None
    lead_source: str | None = None
    # referral_source is READ-ONLY — sourced from NowCerts by the sync, not set here.
    assigned_to: str | None = None           # LEGACY NowCerts display-name array
    assigned_to_email: str | None = None     # canonical owner (agency_crm_users FK)
    next_action: str | None = None
    description: str | None = None
    probability: int | None = None           # win %; defaults from stage
    likelihood: str | None = None            # NowCerts likelihood; defaults from probability
    disposition: str | None = None           # NowCerts Disposition (outcome)
    source: str = "manual"
    created_by: str | None = None

    @model_validator(mode="after")
    def _need_client(self):
        if not (self.client_identifier or self.insured_name):
            raise ValueError("client_identifier or insured_name is required")
        return self


@app.post("/api/opportunities")
async def create_opportunity_endpoint(req: OpportunityCreateRequest, background_tasks: BackgroundTasks):
    """Create (or return existing) a pipeline opportunity for ANY client — new,
    inactive, or a cross-sell on a current client. Idempotent per
    (client_identifier, line_of_business); the smart create logic (identifier,
    dedup, insured link) lives in one place so every cockpit writes correctly.
    """
    from hermes.intake import opportunities as opp

    if req.assigned_to_email:
        _require_users(_get_supa(), [("assigned_to_email", req.assigned_to_email)])
    ci = req.client_identifier or opp.make_client_identifier(req.insured_name, req.fein)
    otype = (req.opportunity_type or opp.TYPE_NEW_BUSINESS).strip()
    stage = req.stage.strip() if req.stage else None   # None → type's first stage
    try:
        row, created = opp.create_opportunity(
            _get_supa(),
            client_identifier=ci,
            line_of_business=req.line_of_business,
            opportunity_type=otype,
            insured_name=req.insured_name,
            insured_id=req.insured_id,
            prospect_type=req.prospect_type,
            insured_type=req.insured_type,
            stage=stage,
            premium_estimate=req.premium_estimate,
            carrier=req.carrier,
            lead_source=req.lead_source,
            assigned_to=req.assigned_to,
            assigned_to_email=req.assigned_to_email,
            next_action=req.next_action,
            description=req.description,
            probability=req.probability,
            likelihood=req.likelihood,
            disposition=req.disposition,
            source=req.source,
            created_by=req.created_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        log.exception("create opportunity failed: %s / %s", ci, req.line_of_business)
        raise HTTPException(status_code=502, detail=str(exc))

    # Prompt AMS pull-back (only for a freshly created, unlinked row): look up an
    # existing NowCerts insured and link + enrich it. Runs in the background so the
    # cockpit's create returns instantly; the row fills in a moment later. No
    # kick_executor here — a cockpit opportunity stages no create_insured job.
    if created and not row.get("insured_id"):
        def _prime(opp_row: dict) -> None:
            try:
                from hermes.intake.opportunity_priming import prime_new_opportunities

                prime_new_opportunities(_get_supa(), [opp_row], kick_executor=False)
            except Exception:
                log.exception("prime opportunity failed: %s", opp_row.get("id"))

        background_tasks.add_task(_prime, row)
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


class OpportunityUpdateRequest(BaseModel):
    """Editable opportunity fields. All optional — only provided fields are written.
    Supabase-only: worked in the CRM, never pushed to the AMS here."""
    insured_name: str | None = None
    line_of_business: str | None = None
    opportunity_type: str | None = None
    stage: str | None = None
    premium_estimate: float | None = None
    carrier: str | None = None
    likelihood: str | None = None
    disposition: str | None = None
    probability: int | None = None
    next_action: str | None = None
    next_action_date: str | None = None
    description: str | None = None
    assigned_to: str | None = None
    prospect_type: str | None = None
    needed_by: str | None = None
    effective_date: str | None = None
    expiration_date: str | None = None
    lost_reason: str | None = None
    referral_source: str | None = None
    lead_source: str | None = None
    assigned_to_email: str | None = None     # canonical owner (agency_crm_users FK)


# Fields a user may edit in the CRM. NowCerts ids and sync-control fields stay
# excluded (they mirror the AMS). referral_source / lead_source ARE editable — the
# first CRM edit flips sync_source='crm', after which the inbound AMS sync skips
# the row (see opportunity_sync), so a manual correction here sticks.
_OPP_EDITABLE = set(OpportunityUpdateRequest.model_fields.keys())


@app.patch("/api/opportunities/{opportunity_id}")
async def update_opportunity_endpoint(opportunity_id: str, req: OpportunityUpdateRequest):
    """Edit an opportunity's fields in the CRM. Supabase-only — an opportunity is
    worked in the CRM and does not write back to the AMS until it's Bound/Won or
    Lost. Only the fields present in the request are changed; setting ``stage``
    also re-derives ``status``."""
    from hermes.intake import opportunities as opp

    supa = _get_supa()
    fields = {k: v for k, v in req.model_dump(exclude_unset=True).items() if k in _OPP_EDITABLE}
    if not fields:
        raise HTTPException(status_code=400, detail="no editable fields provided")
    if fields.get("stage"):
        fields["status"] = opp.status_for_stage(str(fields["stage"]).strip())
    if fields.get("assigned_to_email"):
        _require_users(supa, [("assigned_to_email", fields["assigned_to_email"])])
    # Mark the row CRM-worked so the inbound AMS sync stops overwriting it — once
    # a deal is being worked in the CRM it doesn't go back to the AMS until terminal.
    fields["sync_source"] = "crm"
    try:
        row = supa.update("opportunities", opportunity_id, fields)
    except Exception as exc:
        log.exception("update opportunity failed: %s", opportunity_id)
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True, "opportunity": row}


@app.delete("/api/opportunities/{opportunity_id}")
async def delete_opportunity_endpoint(opportunity_id: str):
    """Delete an opportunity from the CRM. Supabase-only — opportunities never write
    to the AMS, so there's nothing to unwind in NowCerts. Any attached quotes are
    removed automatically (opportunity_quotes FK is ON DELETE CASCADE)."""
    try:
        _get_supa().delete("opportunities", opportunity_id)
    except Exception as exc:
        log.exception("delete opportunity failed: %s", opportunity_id)
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True, "deleted": opportunity_id}


@app.get("/api/leads")
async def list_leads_endpoint(limit: int = 200):
    """Leads = live NowCerts prospects (insureds with a prospectType). Read-only;
    a lead is promoted by creating an opportunity from it (POST /api/opportunities)."""
    from hermes.leads import list_prospects

    try:
        return list_prospects(_get_nowcerts(), limit=limit)
    except Exception as exc:
        log.exception("leads list failed")
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/cross-sell")
async def cross_sell_search_endpoint(q: str = "", limit: int = 25):
    """Search active clients (canonical mirror) to pull one into the pipeline as a
    cross-sell. Returns each client's current LOBs + premium; opening the cross-sell
    is a POST /api/opportunities with opportunity_type='Cross-selling'."""
    from hermes.cross_sell import search_cross_sell

    try:
        return search_cross_sell(_get_supa(), query=q, limit=limit)
    except Exception as exc:
        log.exception("cross-sell search failed: %s", q)
        raise HTTPException(status_code=502, detail=str(exc))


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
    try:
        rows = supa.select("opportunities", columns="*", params={"id": f"eq.{opportunity_id}"}, limit=1)
    except Exception:
        rows = []  # malformed id (opportunities.id is a uuid) → treat as not found
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


class StageUpdateRequest(BaseModel):
    stage: str
    lost_reason: str | None = None
    approved_by: str | None = None


@app.post("/api/opportunities/{opportunity_id}/stage")
async def update_opportunity_stage(opportunity_id: str, req: StageUpdateRequest):
    """Move an opportunity to a new pipeline stage (Kanban drag). Syncs status
    (won/lost). The move itself is Supabase-only; when it lands on a terminal stage
    (Bound/Won or Lost) it QUEUES an approval-gated writeback to NowCerts — nothing
    hits the AMS until the opportunity-writeback executor drains it."""
    from hermes.intake import opportunities as opp

    supa = _get_supa()
    stage = (req.stage or "").strip()
    try:
        # advance_stage accepts any non-empty stage (NowCerts owns the vocabulary).
        row = opp.advance_stage(supa, opportunity_id, stage, lost_reason=req.lost_reason)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        log.exception("stage update failed: %s", opportunity_id)
        raise HTTPException(status_code=502, detail=str(exc))

    # Terminal → queue the AMS writeback (Bound/Won or Lost). Best-effort: a queue
    # hiccup must not fail the stage move.
    queued = None
    if str(row.get("status") or "") in ("won", "lost") and row.get("nowcerts_opportunity_id"):
        try:
            from hermes.sync.opportunity_writeback import stage_writeback

            job = stage_writeback(supa, row, approved_by=req.approved_by or "cockpit-stage-move", stage=stage)
            queued = bool(job)
        except Exception:
            log.exception("opportunity writeback staging failed: %s", opportunity_id)
    return {"ok": True, "opportunity": row, "writeback_queued": queued}


# ── Opportunity quotes — carrier quotes (with PDF) attached to an opportunity ──
def _load_opportunity(supa, opportunity_id: str) -> dict[str, Any]:
    try:
        rows = supa.select("opportunities", columns="*", params={"id": f"eq.{opportunity_id}"}, limit=1)
    except Exception:
        rows = []  # malformed uuid → not found
    if not rows:
        raise HTTPException(status_code=404, detail="opportunity not found")
    return rows[0]


async def _file_quote_pdf(supa, quote: dict[str, Any], upload: "UploadFile") -> dict[str, Any] | None:
    """File an uploaded PDF into the client's Nextcloud Quotes/ folder. Returns a
    warning dict on failure (quote is still saved) or None on success/no-file."""
    if upload is None:
        return None
    data = await upload.read()
    if not data:
        return None
    from hermes.quotes import documents as quote_docs

    try:
        quote_docs.file_quote_pdf(
            supa, quote, content=data,
            original_filename=upload.filename,
            content_type=upload.content_type or "application/pdf",
        )
        return None
    except Exception as exc:  # noqa: BLE001 — never lose the quote over a filing hiccup
        log.exception("quote pdf filing failed for quote %s", quote.get("id"))
        return {"document_warning": f"Quote saved, but the PDF could not be filed to Nextcloud: {exc}"}


@app.post("/api/opportunities/{opportunity_id}/quotes")
async def create_quote_endpoint(
    opportunity_id: str,
    file: UploadFile | None = File(None),
    carrier: str | None = Form(None),
    line_of_business: str | None = Form(None),
    premium: str | None = Form(None),
    effective_date: str | None = Form(None),
    expiration_date: str | None = Form(None),
    quote_number: str | None = Form(None),
    notes: str | None = Form(None),
    created_by: str | None = Form(None),
):
    """Add a carrier quote to an opportunity, optionally attaching the quote PDF
    (filed into the client's Nextcloud Quotes/ folder). Multipart form."""
    from hermes.quotes import store as quote_store

    supa = _get_supa()
    opportunity = _load_opportunity(supa, opportunity_id)
    try:
        quote = quote_store.create_quote(
            supa, opportunity=opportunity, carrier=carrier, line_of_business=line_of_business,
            premium=premium, effective_date=effective_date, expiration_date=expiration_date,
            quote_number=quote_number, notes=notes, created_by=created_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        log.exception("create quote failed for opportunity %s", opportunity_id)
        raise HTTPException(status_code=502, detail=str(exc))
    warn = await _file_quote_pdf(supa, quote, file)
    fresh = quote_store.get_quote(supa, str(quote["id"])) or quote
    return {"ok": True, "quote": fresh, **(warn or {})}


@app.get("/api/opportunities/{opportunity_id}/quotes")
async def list_opportunity_quotes_endpoint(opportunity_id: str):
    """Quotes attached to one opportunity (newest first)."""
    from hermes.quotes import store as quote_store

    rows = quote_store.list_quotes(_get_supa(), opportunity_id=opportunity_id)
    return {"quotes": rows, "count": len(rows)}


@app.get("/api/quotes")
async def list_quotes_endpoint(insured_id: str | None = None, limit: int = 500):
    """All carrier quotes — the Quotes module (grouped by opportunity). Pass
    insured_id to get one client's quotes across their opportunities."""
    from hermes.quotes import store as quote_store

    try:
        rows = quote_store.list_quotes(_get_supa(), insured_id=insured_id, limit=limit)
    except Exception as exc:
        log.exception("list quotes failed")
        raise HTTPException(status_code=502, detail=str(exc))
    return {"quotes": rows, "count": len(rows)}


@app.post("/api/quotes/{quote_id}/document")
async def attach_quote_document_endpoint(quote_id: str, file: UploadFile = File(...)):
    """Attach (or replace) the quote PDF on an existing quote."""
    from hermes.quotes import store as quote_store

    supa = _get_supa()
    quote = quote_store.get_quote(supa, quote_id)
    if not quote:
        raise HTTPException(status_code=404, detail="quote not found")
    warn = await _file_quote_pdf(supa, quote, file)
    if warn:
        raise HTTPException(status_code=502, detail=warn["document_warning"])
    return {"ok": True, "quote": quote_store.get_quote(supa, quote_id)}


@app.post("/api/quotes/{quote_id}/send-to-nowcerts")
async def send_quote_to_nowcerts(quote_id: str, req: SendQuoteRequest):
    """Approved push: enqueue this carrier quote to NowCerts (Policy · IsQuote).
    Writes nothing synchronously — the quote executor completes it and stamps the
    quote number/guid back onto the quote row. approved_by must be a real user."""
    from hermes.quotes import store as quote_store
    from hermes.quotes.executor import stage_quote_row

    supa = _get_supa()
    _require_users(supa, [("approved_by", req.approved_by)])
    quote = quote_store.get_quote(supa, quote_id)
    if not quote:
        raise HTTPException(status_code=404, detail="quote not found")
    try:
        job = stage_quote_row(supa, quote=quote, approved_by=req.approved_by)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        log.exception("send quote failed: %s", quote_id)
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True, "queued": True, "queue_id": job.get("id"),
            "note": "Quote queued to NowCerts (approved). It writes when the quote executor runs."}


# ── Proposals — standard client-facing proposals assembled from carrier quotes ──
class ProposalCreateRequest(BaseModel):
    quote_ids: list[str] = Field(default_factory=list)
    insured_id: str | None = None
    insured_name: str | None = None
    client_identifier: str | None = None
    opportunity_id: str | None = None
    title: str | None = None
    segment: str | None = None                     # Personal | Commercial
    proposal_type: str = "New Business"
    notes: str | None = None
    fmt: Literal["html", "pdf", "both"] = "html"
    created_by: str | None = None

    @model_validator(mode="after")
    def _need_quotes(self):
        if not self.quote_ids:
            raise ValueError("select at least one quote for the proposal")
        return self


class ProposalRegenerateRequest(BaseModel):
    fmt: Literal["html", "pdf", "both"] = "html"


class ProposalStatusRequest(BaseModel):
    status: str


@app.post("/api/proposals")
async def create_proposal_endpoint(req: ProposalCreateRequest):
    """Create a proposal from selected carrier quotes, render it (LOB-grouped),
    and file it into the client's Nextcloud Proposals/ folder. fmt: html|pdf|both."""
    from hermes.proposals import documents as prop_docs
    from hermes.proposals import store as prop_store

    supa = _get_supa()
    try:
        proposal = prop_store.create_proposal(
            supa, insured_id=req.insured_id, insured_name=req.insured_name,
            client_identifier=req.client_identifier, opportunity_id=req.opportunity_id,
            quote_ids=req.quote_ids, title=req.title, segment=req.segment,
            proposal_type=req.proposal_type, notes=req.notes, created_by=req.created_by,
        )
        result = prop_docs.generate_and_file(supa, proposal, fmt=req.fmt)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        log.exception("create proposal failed")
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True, "proposal": result["proposal"], "warnings": result["warnings"]}


@app.get("/api/proposals")
async def list_proposals_endpoint(insured_id: str | None = None, limit: int = 500):
    """All proposals (newest first), or one client's when insured_id is given."""
    from hermes.proposals import store as prop_store

    try:
        rows = prop_store.list_proposals(_get_supa(), insured_id=insured_id, limit=limit)
    except Exception as exc:
        log.exception("list proposals failed")
        raise HTTPException(status_code=502, detail=str(exc))
    return {"proposals": rows, "count": len(rows)}


@app.get("/api/proposals/{proposal_id}")
async def get_proposal_endpoint(proposal_id: str):
    from hermes.proposals import store as prop_store

    row = prop_store.get_proposal(_get_supa(), proposal_id)
    if not row:
        raise HTTPException(status_code=404, detail="proposal not found")
    return {"proposal": row}


@app.get("/api/proposals/{proposal_id}/view", response_class=HTMLResponse)
async def view_proposal_endpoint(proposal_id: str):
    """The rendered proposal HTML — open in a tab or print to PDF from the browser."""
    from hermes.proposals import store as prop_store

    row = prop_store.get_proposal(_get_supa(), proposal_id)
    if not row:
        raise HTTPException(status_code=404, detail="proposal not found")
    return HTMLResponse(content=row.get("content_html") or "<p>Not yet rendered.</p>")


@app.post("/api/proposals/{proposal_id}/regenerate")
async def regenerate_proposal_endpoint(proposal_id: str, req: ProposalRegenerateRequest):
    """Re-render a proposal (picks up edited quotes/notes). fmt: html|pdf|both."""
    from hermes.proposals import documents as prop_docs
    from hermes.proposals import store as prop_store

    supa = _get_supa()
    row = prop_store.get_proposal(supa, proposal_id)
    if not row:
        raise HTTPException(status_code=404, detail="proposal not found")
    try:
        result = prop_docs.generate_and_file(supa, row, fmt=req.fmt)
    except Exception as exc:
        log.exception("regenerate proposal failed: %s", proposal_id)
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True, "proposal": result["proposal"], "warnings": result["warnings"]}


@app.post("/api/proposals/{proposal_id}/status")
async def set_proposal_status_endpoint(proposal_id: str, req: ProposalStatusRequest):
    from hermes.proposals import store as prop_store

    row = prop_store.set_status(_get_supa(), proposal_id, req.status)
    return {"ok": True, "proposal": row}


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


# ---------------------------------------------------------------------------
# Nextcloud Deck — shared boards.
#
# Read + one write, deliberately narrow. Deck is where cross-team work is
# tracked; the point of exposing it here is that a scheduled Hermes run can put
# a card on a board without a human driving a browser. The MCP bridge fronts
# these rather than talking to Nextcloud itself, so the app password stays in
# this process and is not copied into a second container.
# ---------------------------------------------------------------------------
def _deck():
    from hermes.integrations.nextcloud_deck import DeckClient

    return DeckClient()


@app.get("/api/deck/boards")
async def deck_boards_endpoint():
    """Boards, and the stacks (lists) on each — what a caller needs to address a card."""
    from hermes.integrations.nextcloud_deck import DeckError

    try:
        client = _deck()
        boards = client.list_boards()
        for b in boards:
            b["stacks"] = [
                {"id": st["id"], "title": st["title"], "cards": len(st["cards"])}
                for st in client.list_stacks(b["id"])
            ]
        return {"boards": boards, "count": len(boards)}
    except DeckError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        log.exception("deck boards failed")
        raise HTTPException(status_code=502, detail=str(exc))


class DeckCardRequest(BaseModel):
    """Board and list are given by *name* — callers hold names, not Deck ids."""

    board: str
    stack: str = "To Do"
    title: str
    description: str | None = None
    duedate: str | None = None       # ISO-8601, e.g. 2026-07-28T17:00:00+00:00
    skip_if_exists: bool = True


@app.post("/api/deck/cards")
async def deck_create_card_endpoint(req: DeckCardRequest):
    """Add a card. Idempotent by title within the list, so a job that runs twice
    doesn't leave two identical cards."""
    from hermes.integrations.nextcloud_deck import DeckError

    try:
        return _deck().create_card(
            board=req.board,
            stack=req.stack,
            title=req.title,
            description=req.description,
            duedate=req.duedate,
            skip_if_exists=req.skip_if_exists,
        )
    except DeckError as exc:
        # A bad board/list name is the caller's mistake, and the message lists
        # the real ones — that is a 400, not a gateway failure.
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        log.exception("deck card create failed")
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/agency-users")
async def list_agency_users_endpoint(assignable: bool = False):
    """Active CRM users — powers owner/assignee pickers (valid FK targets).

    ``assignable=true`` excludes service accounts. lc-rsg@ is the machine identity
    (0 tasks ever assigned to it, 5 created by it); offering "RSG Service" in an
    assignee dropdown invites someone to assign real work to a robot. It stays a
    valid created_by / approved_by / uploaded_by target, which is the whole point
    of it existing.
    """
    rows = _get_supa().select(
        "agency_crm_users", columns="email,display_name,role,active",
        params={"active": "eq.true", "order": "display_name.asc"}, limit=200,
    )
    if assignable:
        rows = [r for r in rows if str(r.get("role") or "") != "service"]
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


@app.get("/api/case-templates")
async def list_case_templates_endpoint():
    """The case-template menu (onboarding, off-boarding, endorsement, COI, ...).

    Static definitions, not a table — a checklist is code the agency reviews in a
    PR, not data somebody can quietly edit into meaninglessness.
    """
    from hermes.casework import templates as T

    return {"templates": T.list_templates()}


class CaseFromTemplateRequest(BaseModel):
    """Open a case from a template, with its whole checklist attached."""
    template_key: str
    owner_email: str
    insured_name: str | None = None
    insured_database_id: str | None = None
    policy_number: str | None = None
    created_by_email: str | None = None
    assigned_to_email: str | None = None   # default assignee for the checklist
    title: str | None = None               # override the template's title
    description: str | None = None
    priority: str | None = None
    due_at: str | None = None


@app.post("/api/cases/from-template")
async def create_case_from_template_endpoint(req: CaseFromTemplateRequest):
    """Create a case AND its checklist in one call.

    This is the whole point of templates: an onboarding that exists as a case
    with no tasks is the same half-onboarded client we already had. If the tasks
    cannot be written the case is rolled back, so a caller never ends up with a
    bare case it believes is a full checklist.
    """
    import uuid

    from hermes.casework import templates as T
    from hermes.renewals import cases as C

    tpl = T.get_template(req.template_key)
    if not tpl:
        raise HTTPException(
            status_code=404,
            detail=f"unknown template '{req.template_key}'; "
                   f"valid: {', '.join(sorted(T.CASE_TEMPLATES))}",
        )

    supa = _get_supa()
    creator = req.created_by_email or C._service_email()
    _require_users(supa, [
        ("owner_email", req.owner_email),
        ("created_by_email", creator),
        ("assigned_to_email", req.assigned_to_email),
    ])

    now = datetime.utcnow()
    case_type = tpl["case_type"]
    case_number = (
        f"{case_type[:3].upper()}-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
    )
    case_due = req.due_at or (
        (now + timedelta(days=tpl["due_days"])).isoformat() if tpl.get("due_days") else None
    )

    try:
        case = supa.insert("agency_crm_cases", C._compact({
            "case_type": case_type,
            "case_number": case_number,
            "template_key": req.template_key,
            "title": req.title or T.render_title(req.template_key, req.insured_name),
            "description": req.description or tpl.get("description"),
            "status": "open",
            "priority": req.priority or tpl.get("priority") or "medium",
            "owner_email": req.owner_email,
            "created_by_email": creator,
            "insured_name": req.insured_name,
            "insured_database_id": req.insured_database_id,
            "policy_number": req.policy_number,
            "due_at": case_due,
        }))
    except Exception as exc:
        log.exception("create case from template failed: %s", req.template_key)
        raise HTTPException(status_code=502, detail=str(exc))

    case_id = str(case.get("id"))
    try:
        created = C.create_tasks(
            supa,
            case_id=case_id,
            insured_database_id=req.insured_database_id,
            default_assignee_email=req.assigned_to_email or req.owner_email,
            created_by_email=creator,
            tasks=[{
                "title": t["title"],
                "description": t.get("description"),
                "priority": t.get("priority", "medium"),
                "is_required": bool(t.get("required")),
                "sort_order": i,
                "template_key": req.template_key,
                "due_at": (now + timedelta(days=t.get("due_days", 0))).isoformat(),
            } for i, t in enumerate(tpl["tasks"])],
        )
    except Exception as exc:
        # Roll the case back rather than leave a checklist-less shell behind.
        log.exception("template tasks failed for %s; rolling back case", case_number)
        try:
            supa.delete("agency_crm_cases", params={"id": f"eq.{case_id}"})
        except Exception:  # noqa: BLE001
            log.exception("rollback of case %s failed — orphaned case left behind", case_number)
        raise HTTPException(status_code=502, detail=f"checklist creation failed: {exc}")

    C.log_case_event(
        supa, case_id=case_id, event_type="case_created",
        summary=f"{tpl['label']} opened from template with {len(created)} tasks",
        actor_email=creator,
    )
    return {"ok": True, "case": case, "tasks": created, "task_count": len(created)}


@app.get("/api/cases/{case_id}/progress")
async def case_progress_endpoint(case_id: str):
    """Checklist progress plus whether every required task is satisfied."""
    rows = _get_supa().select(
        "v_case_progress", columns="*", params={"case_id": f"eq.{case_id}"}, limit=1
    )
    if not rows:
        raise HTTPException(status_code=404, detail="case not found")
    return rows[0]


class CaseCloseRequest(BaseModel):
    """Close a case. ``resolution`` is what goes to the AMS — the checklist does not."""
    resolution: str
    resolved_by_email: str | None = None
    push_to_ams: bool = True


@app.post("/api/cases/{case_id}/close")
async def close_case_endpoint(case_id: str, req: CaseCloseRequest):
    """Close a case, refusing while required tasks are open.

    The database enforces the same rule (trigger, migration 20260727000000) so
    closing straight through PostgREST cannot bypass it. This endpoint checks
    first anyway, to return a list of what is actually blocking rather than a
    raw constraint error.

    On close the resolution summary is pushed to the AMS; the per-task detail
    stays in the CRM, which is the system that needs it.
    """
    from hermes.casework import templates as T
    from hermes.renewals import cases as C

    supa = _get_supa()
    actor = req.resolved_by_email or C._service_email()
    _require_users(supa, [("resolved_by_email", req.resolved_by_email)])

    cases = supa.select("agency_crm_cases", columns="*", params={"id": f"eq.{case_id}"}, limit=1)
    if not cases:
        raise HTTPException(status_code=404, detail="case not found")
    case = cases[0]

    tasks = supa.select(
        "agency_crm_tasks", columns="*",
        params={"case_id": f"eq.{case_id}", "order": "sort_order.asc"}, limit=500,
    )
    blocking = [
        t for t in tasks
        if t.get("is_required") and t.get("status") not in ("completed", "cancelled")
    ]
    if blocking:
        raise HTTPException(status_code=409, detail={
            "error": "required tasks still open",
            "case_number": case.get("case_number"),
            "blocking": [{"id": t.get("id"), "title": t.get("title"),
                          "status": t.get("status")} for t in blocking],
            "hint": "complete them, or cancel the ones that did not apply to this case",
        })

    closed_at = datetime.utcnow().isoformat()
    try:
        updated = supa.update("agency_crm_cases", {
            "status": "closed",
            "closed_at": closed_at,
            "resolution": req.resolution,
            "resolved_by_email": actor,
        }, params={"id": f"eq.{case_id}"})
    except Exception as exc:
        log.exception("close case %s failed", case_id)
        raise HTTPException(status_code=502, detail=str(exc))

    case = (updated[0] if isinstance(updated, list) and updated else {**case,
            "status": "closed", "closed_at": closed_at, "resolution": req.resolution})
    summary = T.build_summary(case, tasks)

    ams = {"pushed": False, "reason": "not requested"}
    if req.push_to_ams:
        try:
            from hermes.casework.executor import push_case_summary_to_ams

            ams = push_case_summary_to_ams(supa, case=case, summary=summary)
            if ams.get("pushed"):
                supa.update("agency_crm_cases", {"ams_summary_sent_at": datetime.utcnow().isoformat()},
                            params={"id": f"eq.{case_id}"})
        except Exception as exc:  # noqa: BLE001
            # A closed case is closed. An AMS hiccup is a sync problem, not a
            # reason to refuse the close and make somebody redo the work.
            log.exception("AMS summary push failed for case %s", case_id)
            ams = {"pushed": False, "reason": str(exc)}

    C.log_case_event(
        supa, case_id=case_id, event_type="case_closed",
        summary=f"Closed: {req.resolution}", actor_email=actor,
    )
    return {"ok": True, "case": case, "summary": summary, "ams": ams}


@app.get("/api/cases")
async def list_cases_endpoint(
    status: str | None = None,
    case_type: str | None = None,
    limit: int = 100,
    include_progress: bool = False,
):
    """List cases, newest first.

    ``include_progress`` merges each case's checklist state from v_case_progress —
    how far through it is, whether every required task is satisfied
    (``can_close``), and how many are still blocking. That is the question anyone
    actually asks about a case, and answering it here avoids a per-case round trip
    from callers that can only make one request (the MCP bridge, a morning brief).

    One extra query for the whole page, not one per case.
    """
    params: dict[str, str] = {"order": "created_at.desc"}
    if status:
        params["status"] = f"eq.{status}"
    if case_type:
        params["case_type"] = f"eq.{case_type}"
    supa = _get_supa()
    rows = supa.select("agency_crm_cases", columns="*", params=params, limit=limit)

    if include_progress and rows:
        prog_params: dict[str, str] = {}
        if status:
            prog_params["status"] = f"eq.{status}"
        prog = supa.select("v_case_progress", columns="*", params=prog_params, limit=max(limit, len(rows)))
        by_id = {str(p.get("case_id")): p for p in prog}
        for r in rows:
            p = by_id.get(str(r.get("id")))
            if not p:
                continue
            r["progress"] = {
                "tasks_total": p.get("tasks_total"),
                "tasks_done": p.get("tasks_done"),
                "required_total": p.get("required_total"),
                "required_done": p.get("required_done"),
                "required_blocking": p.get("required_blocking"),
                "can_close": p.get("can_close"),
            }
    return {"cases": rows, "count": len(rows)}


@app.get("/api/cases/blocked")
async def list_blocked_cases_endpoint(limit: int = 100):
    """Open cases that cannot close yet, and the specific tasks blocking each.

    The morning-brief question: "what is stopping these from being finished?"
    Returns the blocking task titles, not just a count — a number tells you there
    is a problem, a title tells you what to do about it.
    """
    supa = _get_supa()
    prog = supa.select(
        "v_case_progress", columns="*",
        params={"status": "eq.open", "can_close": "is.false", "order": "opened_at.asc"},
        limit=limit,
    )
    out: list[dict[str, Any]] = []
    for p in prog:
        tasks = supa.select(
            "agency_crm_tasks", columns="id,title,status,due_at,assigned_to_email,is_required",
            params={"case_id": f"eq.{p.get('case_id')}", "is_required": "is.true",
                    "order": "sort_order.asc"},
            limit=100,
        )
        blocking = [t for t in tasks if t.get("status") not in ("completed", "cancelled")]
        out.append({
            "case_id": p.get("case_id"),
            "case_number": p.get("case_number"),
            "case_type": p.get("case_type"),
            "insured_name": p.get("insured_name"),
            "title": p.get("title"),
            "tasks_done": p.get("tasks_done"),
            "tasks_total": p.get("tasks_total"),
            "blocking": [{"title": t.get("title"), "assigned_to_email": t.get("assigned_to_email"),
                          "due_at": t.get("due_at")} for t in blocking],
        })
    return {"blocked_cases": out, "count": len(out)}


@app.get("/api/intake/queue")
async def intake_queue_endpoint(limit: int = 50):
    """Intake submissions waiting on a human, oldest first.

    Oldest first on purpose: the useful signal is what has been sitting, not what
    just arrived. ``oldest_age_days`` is surfaced because a queue that stopped
    moving looks identical to a busy one if you only report the count.
    """
    supa = _get_supa()
    rows = supa.select(
        "intake_submissions",
        columns="id,source,agent,intake_kind,client_identifier,lob_code,status,"
                "approval_token,retry_count,created_at,updated_at",
        params={"status": "eq.awaiting_approval", "order": "created_at.asc"},
        limit=limit,
    )
    failed = supa.select(
        "intake_submissions", columns="id,client_identifier,status,error_log,updated_at",
        params={"status": "eq.failed", "order": "updated_at.desc"}, limit=20,
    )
    oldest_age = None
    if rows:
        try:
            oldest = datetime.fromisoformat(str(rows[0].get("created_at")).replace("Z", "+00:00"))
            oldest_age = (datetime.now(oldest.tzinfo) - oldest).days
        except (ValueError, TypeError):
            oldest_age = None
    return {
        "awaiting_approval": rows,
        "awaiting_count": len(rows),
        "oldest_age_days": oldest_age,
        "failed_recent": failed,
        "failed_count": len(failed),
    }


class TaskCreateRequest(BaseModel):
    """Create a task. As of issue #195 a task has three legitimate shapes:
    case-linked (case_id), client-but-no-case (insured_database_id), or purely
    internal (neither) — "update commission percentage" is not client work and
    should not have to borrow somebody's case to exist."""
    case_id: str | None = None
    insured_database_id: str | None = None
    title: str
    description: str | None = None
    priority: str = "medium"
    assigned_to_email: str | None = None
    created_by_email: str | None = None
    due_at: str | None = None


@app.post("/api/tasks")
async def create_task_endpoint(req: TaskCreateRequest):
    """Create a task. assigned_to/created_by validated vs agency_crm_users.

    ``case_id`` is optional — omit it for internal work. Idempotent per title
    within the task's scope (its case, else its client, else the internal
    bucket), counting only OPEN tasks so a recurring chore isn't blocked forever
    by last month's completed copy.
    """
    from hermes.renewals import cases as C

    supa = _get_supa()
    creator = req.created_by_email or C._service_email()
    _require_users(supa, [("assigned_to_email", req.assigned_to_email), ("created_by_email", creator)])

    try:
        created = C.create_tasks(
            supa, case_id=req.case_id,
            insured_database_id=req.insured_database_id,
            tasks=[{"title": req.title, "description": req.description,
                    "assigned_to_email": req.assigned_to_email,
                    "priority": req.priority, "due_at": req.due_at}],
            created_by_email=creator,
        )
    except Exception as exc:
        log.exception("create task failed: %s", req.title)
        raise HTTPException(status_code=502, detail=str(exc))
    if not created:
        # Title already open in this scope (idempotent no-op).
        return {"ok": True, "created": False, "task": None}
    # Best-effort: ping the team chat (Nextcloud Talk) about the new task. Never
    # let a chat hiccup fail the task create — it's fire-and-forget.
    try:
        from hermes.operations.task_notify import notify_task_created

        notify_task_created(created[0], kind="task")
    except Exception:  # noqa: BLE001
        log.exception("task_notify failed for %s", req.title)
    return {"ok": True, "created": True, "task": created[0]}


@app.post("/api/tasks/digest")
async def post_task_digest():
    """Post the open-task digest to the team chat (Nextcloud Talk). Meant to be
    hit on a daily schedule (pg_cron / scheduler). No-op if NEXTCLOUD_TALK_TOKEN
    is unset."""
    from hermes.operations.task_notify import daily_task_digest

    return daily_task_digest(_get_supa())


@app.get("/api/tasks")
async def list_tasks_endpoint(
    case_id: str | None = None,
    insured_id: str | None = None,
    scope: str | None = None,
    open_only: bool = False,
    limit: int = 200,
):
    """List tasks.

    ``scope='internal'`` returns only standalone tasks (no case) — the queue of
    things that are nobody's client work but still somebody's job. Without it the
    internal items are buried among case tasks, which is how they get missed.
    """
    params: dict[str, str] = {"order": "created_at.desc"}
    if case_id:
        params["case_id"] = f"eq.{case_id}"
    if insured_id:
        params["insured_database_id"] = f"eq.{insured_id}"
    if scope == "internal":
        params["case_id"] = "is.null"
    elif scope == "case":
        params["case_id"] = "not.is.null"
    if open_only:
        from hermes.renewals.cases import TASK_STATUS_CLOSED

        params["status"] = f"not.in.({','.join(TASK_STATUS_CLOSED)})"
    rows = _get_supa().select("agency_crm_tasks", columns="*", params=params, limit=limit)
    return {"tasks": rows, "count": len(rows)}


class TaskUpdateRequest(BaseModel):
    """Editable task fields. All optional — only what's provided is written."""
    title: str | None = None
    description: str | None = None
    status: str | None = None
    priority: str | None = None
    assigned_to_email: str | None = None
    due_at: str | None = None
    case_id: str | None = None
    insured_database_id: str | None = None


@app.patch("/api/tasks/{task_id}")
async def update_task_endpoint(task_id: str, req: TaskUpdateRequest):
    """Update a task (issue #195 — tasks were create-only and view-only).

    ``completed_at`` is derived from ``status``, never accepted from the caller.
    A reassignment is validated against agency_crm_users: assigned_to_email is a
    real FK, so an unknown address fails at the database with a message nobody
    can act on.
    """
    from hermes.renewals import cases as C

    supa = _get_supa()
    fields = req.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="no fields provided")
    if not C.get_task(supa, task_id):
        raise HTTPException(status_code=404, detail="task not found")
    if fields.get("assigned_to_email"):
        _require_users(supa, [("assigned_to_email", fields["assigned_to_email"])])
    try:
        row = C.update_task(supa, task_id, fields)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        log.exception("update task failed: %s", task_id)
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True, "task": row}


class PushToAmsRequest(BaseModel):
    approved_by: str


class QueueRetryRequest(BaseModel):
    requeued_by: str
    run_now: bool = True


class CaseworkRunRequest(BaseModel):
    limit: int = 5
    dry_run: bool = False


@app.post("/api/cases/{case_id}/push-to-ams")
async def push_case_to_ams(case_id: str, req: PushToAmsRequest):
    """Approved push: log this case in the NowCerts task ledger. approved_by must be a user."""
    from hermes.casework.executor import stage_case_job

    supa = _get_supa()
    _require_users(supa, [("approved_by", req.approved_by)])
    try:
        rows = supa.select("agency_crm_cases", columns="*", params={"id": f"eq.{case_id}"}, limit=1)
    except Exception:
        rows = []
    if not rows:
        raise HTTPException(status_code=404, detail="case not found")
    try:
        job = stage_case_job(supa, case=rows[0], approved_by=req.approved_by)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        log.exception("push case failed: %s", case_id)
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True, "queued": True, "queue_id": job.get("id"),
            "note": "Case queued to NowCerts (approved). Writes when the casework executor runs."}


@app.post("/api/tasks/{task_id}/push-to-ams")
async def push_task_to_ams(task_id: str, req: PushToAmsRequest):
    """Approved push: log this task in the NowCerts task ledger (uses its case's insured)."""
    from hermes.casework.executor import stage_task_job

    supa = _get_supa()
    _require_users(supa, [("approved_by", req.approved_by)])
    try:
        rows = supa.select("agency_crm_tasks", columns="*", params={"id": f"eq.{task_id}"}, limit=1)
    except Exception:
        rows = []
    if not rows:
        raise HTTPException(status_code=404, detail="task not found")
    task = rows[0]
    insured_id, policy_number = None, None
    if task.get("case_id"):
        try:
            crows = supa.select("agency_crm_cases", columns="insured_database_id,policy_number",
                                params={"id": f"eq.{task['case_id']}"}, limit=1)
            if crows:
                insured_id = crows[0].get("insured_database_id")
                policy_number = crows[0].get("policy_number")
        except Exception:
            pass
    try:
        job = stage_task_job(supa, task=task, insured_database_id=insured_id,
                             policy_number=policy_number, approved_by=req.approved_by)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        log.exception("push task failed: %s", task_id)
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True, "queued": True, "queue_id": job.get("id"),
            "note": "Task queued to NowCerts (approved). Writes when the casework executor runs."}


@app.get("/api/queue/failed")
async def list_failed_ams_writebacks(limit: int = 100):
    """Service-request/client-task write-backs that failed or exhausted retries —
    the retry queue surfaced in the cockpit."""
    rows = _get_supa().select(
        "outbound_sync_queue", columns="*",
        params={"object_type": "in.(case,task)", "status": "in.(failed,dead)",
                "order": "updated_at.desc"}, limit=limit,
    )
    return {"jobs": rows, "count": len(rows)}


@app.post("/api/queue/{queue_id}/retry")
async def retry_ams_writeback(queue_id: str, req: QueueRetryRequest):
    """Retriable on command: re-open a failed/dead case or task write-back and
    (by default) run the executor now so it relays to NowCerts immediately."""
    from hermes.casework.executor import requeue_job, run_casework_executor

    supa = _get_supa()
    _require_users(supa, [("requeued_by", req.requeued_by)])
    try:
        job = requeue_job(supa, queue_id=queue_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        log.exception("requeue failed: %s", queue_id)
        raise HTTPException(status_code=502, detail=str(exc))
    run = run_casework_executor(supa=supa, limit=5) if req.run_now else {}
    return {"ok": True, "requeued": True, "queue_id": queue_id, "job": job, "run": run}


@app.post("/api/casework/run")
async def run_casework_writebacks(req: CaseworkRunRequest):
    """Run the case/task → NowCerts write-back executor on command (opt-in, no cron).
    ``dry_run`` previews without writing."""
    from hermes.casework.executor import run_casework_executor

    summary = run_casework_executor(supa=_get_supa(), limit=req.limit, dry_run=req.dry_run)
    return {"ok": True, **summary}


@app.post("/api/intake/run")
async def run_intake_writebacks(req: CaseworkRunRequest):
    """Drain approved intake routing intents to CRM (opportunities) + NowCerts (insured)
    on command (opt-in, no cron). ``dry_run`` previews without writing."""
    from hermes.command_center.intake_executor import run_intake_executor

    summary = run_intake_executor(supa=_get_supa(), limit=req.limit, dry_run=req.dry_run)
    return {"ok": True, **summary}


# Case attachments (issue #195). Case-level only, deliberately: every task already
# belongs to a case or a client, so a second home for documents would just be a
# place for them to hide. A renewal worksheet attached to a task but invisible on
# its case is worse than no attachment feature at all.
#
# Filing category follows the case type, so a renewal's paperwork lands in the
# client's "Renewal Reviews" folder rather than a generic dump.
_CASE_TYPE_CATEGORY = {
    "renewal": "Renewal Reviews",
    "marketing": "Quotes",
    "service": "Correspondence",
}
_CASE_DOC_MAX_BYTES = 25 * 1024 * 1024


@app.post("/api/cases/{case_id}/documents")
async def upload_case_document(
    case_id: str,
    file: UploadFile = File(...),
    uploaded_by: str = Form(""),
    category: str = Form(""),
    title: str = Form(""),
):
    """Attach a file to a case: Nextcloud for the bytes, a doc-link row for the CRM.

    Same path the renewal PDF filer already uses (file_document -> link_document),
    so a hand-attached document lands in the same client folder tree as a generated
    one instead of a parallel store.
    """
    from hermes.integrations.nextcloud_client import CLIENT_CATEGORIES, NextcloudClient
    from hermes.renewals import cases as C

    supa = _get_supa()
    rows = []
    try:
        rows = supa.select("agency_crm_cases", columns="*", params={"id": f"eq.{case_id}"}, limit=1)
    except Exception:
        rows = []  # malformed uuid -> not found
    if not rows:
        raise HTTPException(status_code=404, detail="case not found")
    case = rows[0]

    uploader = uploaded_by.strip() or C._service_email()
    _require_users(supa, [("uploaded_by", uploader)])

    if category and category not in CLIENT_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"unknown category '{category}'; must be one of {list(CLIENT_CATEGORIES)}",
        )

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="uploaded file is empty")
    if len(content) > _CASE_DOC_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"file is {len(content) // 1024 // 1024}MB; the limit is "
                   f"{_CASE_DOC_MAX_BYTES // 1024 // 1024}MB",
        )

    filename = (file.filename or "attachment").strip()
    folder = category or _CASE_TYPE_CATEGORY.get(
        str(case.get("case_type") or ""), "Correspondence"
    )
    client_name = case.get("insured_name") or None

    try:
        filed = NextcloudClient().file_document(
            content=content,
            filename=filename,
            content_type=file.content_type or "application/octet-stream",
            # No insured on the case -> Internal/Case Files rather than a client
            # folder. Guessing a client name would misfile it under someone.
            client=client_name,
            category=folder,
            internal_folder=None if client_name else "Case Files",
        )
    except Exception as exc:
        log.exception("case document upload failed: case=%s file=%s", case_id, filename)
        raise HTTPException(status_code=502, detail=f"Nextcloud upload failed: {exc}")

    try:
        link = C.link_document(
            supa,
            case_id=case_id,
            title=title.strip() or filename,
            nextcloud_path=filed["path"],
            nextcloud_url=filed.get("url"),
            insured_id=case.get("insured_database_id"),
            content_type=file.content_type,
            uploaded_by_email=uploader,
        )
    except Exception as exc:
        # The bytes are safely in Nextcloud; only the CRM link failed. Say so —
        # "upload failed" would send someone hunting for a file that is right there.
        log.exception("case document link failed: case=%s path=%s", case_id, filed["path"])
        raise HTTPException(
            status_code=502,
            detail=f"File stored at {filed['path']} but linking it to the case failed: {exc}",
        )

    # Keep the case's folder pointer current, as the renewal filer does.
    try:
        supa.update("agency_crm_cases", case_id,
                    {"nextcloud_folder_url": filed.get("url") or filed["path"]})
    except Exception:  # noqa: BLE001 — a pointer refresh must not fail the upload
        log.exception("case folder pointer update failed: %s", case_id)

    return {"ok": True, "document": link, "filed_to": filed["path"]}


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
    try:
        policies = ams_book.select_policies(
            supa,
            # renewed_policy is required by _collapse_to_current_terms to group a
            # successor term with its predecessor.
            columns="policy_guid,policy_number,renewed_policy,nowcerts_insured_guid,carrier,"
                    "lines_of_business,status,effective_date,expiration_date,"
                    "annualized_premium,premium_amount",
            params={"nowcerts_insured_guid": f"eq.{insured_guid}", "order": "expiration_date.asc"},
            limit=500,
        )
    except Exception:  # noqa: BLE001 — a 360 view degrades rather than 500s
        policies = []
    policies, _policy_prior_terms = _collapse_to_current_terms(policies)
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


def _term_sort_key(p: dict[str, Any]) -> tuple:
    """Latest term first: by effective, then expiration, then guid. Nulls sort oldest."""
    def _d(v: Any) -> date:
        try:
            return date.fromisoformat(str(v)[:10])
        except (ValueError, TypeError):
            return date.min
    return (_d(p.get("effective_date")), _d(p.get("expiration_date")), str(p.get("policy_guid") or ""))


def _collapse_to_current_terms(policies: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Collapse renewal-overlap pairs and duplicate imports to one current term per coverage.

    A renewal is a lineage: the expiring term and its successor briefly coexist
    (two "active" policies until the old one drops off), and the same policy can
    also appear twice from dual-source imports. Group by
    (insured, normalized LOB, lineage root) where the lineage root is
    ``renewed_policy`` (NowCerts' predecessor link) or the policy's own number —
    so a successor groups with its predecessor without ever merging two distinct
    policies. Keep the latest-effective term; stamp it with ``prior_terms``.
    Returns (current_terms, folded_count).
    """
    from collections import defaultdict

    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for p in policies:
        insured = str(p.get("nowcerts_insured_guid") or "")
        lob = str(p.get("lines_of_business") or p.get("line_of_business") or "").strip().lower()
        number = str(p.get("policy_number") or "").strip()
        root = str(p.get("renewed_policy") or "").strip() or number
        groups[(insured, lob, root)].append(p)

    current: list[dict[str, Any]] = []
    folded = 0
    for members in groups.values():
        members.sort(key=_term_sort_key, reverse=True)
        head = members[0]
        head["prior_terms"] = len(members) - 1
        current.append(head)
        folded += len(members) - 1
    current.sort(key=lambda p: (str(p.get("expiration_date") or "9999-12-31")))
    return current, folded


@app.get("/api/policies")
async def list_policies_endpoint(limit: int = 1000, include_history: bool = False):
    """Canonical policy book (read-only mirror), soonest-expiring first.

    By default, renewal-overlap pairs and duplicate imports are collapsed to one
    current term per coverage (see ``_collapse_to_current_terms``) so a renewing
    policy shows once, not as two "active" rows. Pass ``include_history=true`` for
    the raw, uncollapsed book. Each policy is stamped with the account/insured
    name it belongs to (looked up from canonical_clients by NowCerts insured GUID)."""
    supa = _get_supa()
    rows = ams_book.select_policies(
        supa,
        columns="policy_guid,policy_number,renewed_policy,nowcerts_insured_guid,carrier,lines_of_business,status,"
                "effective_date,expiration_date,premium_amount,annualized_premium,agency_commission_amount,state",
        params={"order": "expiration_date.asc"}, limit=limit,
    )
    folded = 0
    if not include_history:
        rows, folded = _collapse_to_current_terms(rows)
    # Attach the account name via a single lookup on the client mirror.
    try:
        clients = supa.select(
            "canonical_clients",
            columns="nowcerts_insured_guid,insured_name",
            limit=10000,
        )
        name_by_guid = {
            c.get("nowcerts_insured_guid"): c.get("insured_name")
            for c in clients if c.get("nowcerts_insured_guid")
        }
    except Exception:
        name_by_guid = {}
    for r in rows:
        r["insured_name"] = name_by_guid.get(r.get("nowcerts_insured_guid"))
    return {"policies": rows, "count": len(rows), "folded_prior_terms": folded,
            "collapsed": not include_history}


class CommissionRuleRequest(BaseModel):
    id: str | None = None
    carrier_name: str
    lob: str
    nb_percent: float | None = None
    renewal_percent: float | None = None
    commission_basis: str | None = "gross"
    active: bool = True


@app.get("/api/commission-rules")
async def list_commission_rules(limit: int = 500):
    """Commission terms — carrier/LOB → new-business % and renewal %."""
    rows = _get_supa().select(
        "commission_rules",
        columns="id,carrier_name,lob,nb_percent,renewal_percent,commission_basis,active",
        params={"order": "carrier_name.asc"}, limit=limit,
    )
    return {"rules": rows, "count": len(rows)}


@app.post("/api/commission-rules")
async def upsert_commission_rule(req: CommissionRuleRequest):
    """Add or update a commission term (carrier + LOB rate). Feeds expected
    commission when NowCerts doesn't carry an agency commission amount."""
    supa = _get_supa()
    payload = {k: v for k, v in {
        "carrier_name": (req.carrier_name or "").strip(),
        "lob": (req.lob or "").strip(),
        "nb_percent": req.nb_percent,
        "renewal_percent": req.renewal_percent,
        "commission_basis": req.commission_basis or "gross",
        "active": req.active,
    }.items() if v is not None}
    if not payload.get("carrier_name") or not payload.get("lob"):
        raise HTTPException(status_code=400, detail="carrier_name and lob are required")
    try:
        row = supa.update("commission_rules", req.id, payload) if req.id else supa.insert("commission_rules", payload)
    except Exception as exc:
        log.exception("commission rule upsert failed")
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True, "rule": row}


@app.get("/api/commissions")
async def list_commissions_endpoint(limit: int = 1000, status: str = "reconciled"):
    """Commission ledger, plus the context that keeps an empty result honest.

    Always returns ``counts_by_status`` (over the whole ledger) and ``coverage``
    (how much of the active book reaches the surface, and why the rest doesn't)
    — regardless of what ``status`` matches. Filtering to a status with no rows
    used to render a blank table that read as "no commission data exists", which
    was false for the entire life of the ledger. Pass ``status=all`` for everything.
    """
    from hermes.commissions.surface import commission_overview

    try:
        overview = commission_overview(_get_supa(), status=status, limit=limit)
    except Exception as exc:
        log.exception("commissions read failed")
        raise HTTPException(status_code=502, detail=str(exc))
    return overview.as_dict()


@app.get("/api/commissions/analytics")
async def commission_analytics_endpoint():
    """Whole-ledger rollups by carrier and by line of business (#236).

    The lens for "is the cockpit sufficient to replace the standalone tracker?"
    Per-carrier and per-LOB expected/actual/delta plus a status breakdown, over
    the entire ledger regardless of reconciliation status — a carrier with only
    `pending` rows still appears with its expected money.
    """
    from hermes.commissions.surface import commission_analytics

    try:
        return commission_analytics(_get_supa()).as_dict()
    except Exception as exc:
        log.exception("commissions analytics read failed")
        raise HTTPException(status_code=502, detail=str(exc))


class CommissionOverrideRequest(BaseModel):
    """A human correction to a commission row.

    ``approved_by`` must be an active agency_crm_users identity — an override is
    a named decision on money data, and the audit log records who made it.
    """
    field_name: str
    value: Any
    approved_by: str
    reason: str | None = None


@app.post("/api/commissions/{ledger_id}/override")
async def override_commission_field(ledger_id: str, req: CommissionOverrideRequest):
    """Correct a commission field in the portal.

    The override outranks the synced value until the AMS reports the same thing,
    at which point the nightly reconcile retires it automatically. Fix NowCerts
    by hand separately — this does NOT write to the AMS.
    """
    from hermes.commissions.surface import ENTITY_TYPE, OVERRIDABLE_FIELDS
    from hermes.overrides.store import set_override

    supa = _get_supa()
    _require_users(supa, [("approved_by", req.approved_by)])

    field_name = (req.field_name or "").strip()
    if field_name not in OVERRIDABLE_FIELDS:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name!r} is not overridable; allowed: {sorted(OVERRIDABLE_FIELDS)}",
        )

    try:
        rows = supa.select("commission_ledger", columns="*",
                           params={"id": f"eq.{ledger_id}"}, limit=1)
    except Exception:
        rows = []          # malformed uuid -> not found
    if not rows:
        raise HTTPException(status_code=404, detail="commission row not found")
    ledger = rows[0]

    policy_number = str(ledger.get("policy_number") or "").strip()
    if not policy_number:
        raise HTTPException(
            status_code=400,
            detail="row has no policy_number; overrides are keyed by it so they "
                   "survive a re-seed",
        )

    try:
        row = set_override(
            supa,
            entity_type=ENTITY_TYPE,
            entity_key=policy_number,
            field_name=field_name,
            override_value=req.value,
            original_value=ledger.get(field_name),   # the SOURCE value, for reconcile
            approved_by=req.approved_by,
            reason=req.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        log.exception("commission override failed: %s %s", ledger_id, field_name)
        raise HTTPException(status_code=502, detail=str(exc))

    return {"ok": True, "override": row,
            "note": "Portal value only — correct NowCerts separately. The override "
                    "retires itself once the AMS reports the same value."}


@app.delete("/api/commissions/overrides/{override_id}")
async def withdraw_commission_override(override_id: str, approved_by: str):
    """Withdraw an override — the correction was wrong or is no longer wanted."""
    from hermes.overrides.store import withdraw

    supa = _get_supa()
    _require_users(supa, [("approved_by", approved_by)])
    try:
        row = withdraw(supa, override_id, actor=approved_by)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        log.exception("override withdraw failed: %s", override_id)
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True, "override": row}


@app.get("/api/commissions/overrides")
async def list_commission_overrides(status: str = "active", limit: int = 500):
    """Active corrections, plus anything the sync flagged as conflicted."""
    from hermes.commissions.surface import ENTITY_TYPE

    params: dict[str, str] = {"entity_type": f"eq.{ENTITY_TYPE}",
                              "order": "approved_at.desc"}
    if status and status.lower() != "all":
        params["status"] = f"eq.{status}"
    rows = _get_supa().select("portal_overrides", columns="*", params=params, limit=limit)
    return {"overrides": rows, "count": len(rows)}


# ── Commission statements — upload, review, approve ──────────────────────────
@app.post("/api/commission-statements")
async def upload_commission_statement(
    file: UploadFile = File(...),
    uploaded_by: str = Form(...),
    carrier: str = Form(default=""),
    stated_total_premium: str = Form(default=""),
    stated_total_commission: str = Form(default=""),
):
    """Upload a carrier statement. Parses and STAGES it — writes no money.

    Returns a review card: what parsed, whether it matches the carrier's own
    stated totals, and where every line would land. Approve separately.

    Supply the carrier's stated totals when the statement prints them; the
    crosscheck is what stops a bad parse reaching the ledger.
    """
    from hermes.commissions.statements import stage_statement

    supa = _get_supa()
    _require_users(supa, [("uploaded_by", uploaded_by)])

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty file")

    try:
        staged = stage_statement(
            supa,
            content=content,
            filename=file.filename or "statement.csv",
            uploaded_by=uploaded_by,
            carrier=carrier.strip() or None,
            stated_premium=stated_total_premium.strip() or None,
            stated_commission=stated_total_commission.strip() or None,
        )
    except Exception as exc:
        log.exception("statement staging failed: %s", file.filename)
        raise HTTPException(status_code=502, detail=str(exc))
    return staged.as_dict()


@app.get("/api/commission-statements")
async def list_commission_batches(status: str = "", limit: int = 50):
    """Uploaded statement batches, newest first."""
    from hermes.commissions.statements import BATCHES_TABLE

    params: dict[str, str] = {"order": "created_at.desc"}
    if status.strip():
        params["ingest_status"] = f"eq.{status.strip()}"
    rows = _get_supa().select(BATCHES_TABLE, columns="*", params=params, limit=limit)
    return {"batches": rows, "count": len(rows)}


@app.get("/api/commission-statements/{batch_id}")
async def get_commission_batch(batch_id: str, lines: int = 100):
    """One batch plus its staged lines — the review detail."""
    from hermes.commissions.statements import BATCHES_TABLE, STAGING_TABLE

    supa = _get_supa()
    rows = supa.select(BATCHES_TABLE, columns="*", params={"id": f"eq.{batch_id}"}, limit=1)
    if not rows:
        raise HTTPException(status_code=404, detail="batch not found")
    staged = supa.select(STAGING_TABLE, columns="*",
                         params={"batch_id": f"eq.{batch_id}"}, limit=lines)
    return {"batch": rows[0], "lines": staged, "line_count": len(staged)}


class StatementDecision(BaseModel):
    approved_by: str
    reason: str | None = None


@app.post("/api/commission-statements/{batch_id}/approve")
async def approve_commission_statement(batch_id: str, req: StatementDecision):
    """Commit a reviewed batch: statement + transactions + link + rollup.

    This is the money gate. Refuses a batch that isn't pending review, parsed
    nothing, or failed its crosscheck.
    """
    from hermes.commissions.statements import commit_statement

    supa = _get_supa()
    _require_users(supa, [("approved_by", req.approved_by)])
    try:
        result = commit_statement(supa, batch_id=batch_id, approved_by=req.approved_by)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        log.exception("statement commit failed: %s", batch_id)
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True, **result.as_dict()}


@app.post("/api/commission-statements/{batch_id}/reject")
async def reject_commission_statement(batch_id: str, req: StatementDecision):
    """Reject a staged batch. The staged lines stay for diagnosis."""
    from hermes.commissions.statements import reject_statement

    supa = _get_supa()
    _require_users(supa, [("approved_by", req.approved_by)])
    try:
        row = reject_statement(supa, batch_id=batch_id,
                               reviewed_by=req.approved_by, reason=req.reason)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        log.exception("statement reject failed: %s", batch_id)
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True, "batch": row}


@app.get("/api/carriers")
async def list_carrier_appetite(
    limit: int = 500,
    carrier: str | None = None,
    state: str | None = None,
    lob: str | None = None,
    naics: str | None = None,
):
    """Carrier appetite reference — which carriers RSG can place a risk with, by
    line of business, state, and class code (read-only). Backs the Carrier Hub.
    Filter by carrier (partial), state (2-letter), lob (line_of_business,
    partial), or naics (exact NAICS code)."""
    params: dict[str, str] = {"order": "carrier.asc"}
    if carrier:
        params["carrier"] = f"ilike.*{carrier}*"
    if state:
        params["state"] = f"eq.{state.upper()}"
    if lob:
        params["line_of_business"] = f"ilike.*{lob}*"
    if naics:
        params["naics_code"] = f"eq.{naics}"
    rows = _get_supa().select(
        "carrier_appetite",
        columns="carrier,state,line_of_business,class_description,naics_code,sic_code,"
                "gl_class_code,wc_class_code,appetite_level,commission_percent,notes,"
                "source,last_verified",
        params=params, limit=limit,
    )
    return {"carriers": rows, "count": len(rows)}


_RENEWAL_LOST = {"cancelled", "non-renewed", "non-renewal", "lapsed", "expired", "flat cancel", "rewritten"}


def _renewal_outcome(r: dict) -> str:
    """Won (retained) / Lost / Open, from the candidate's lineage + status."""
    ns = str(r.get("normalized_status") or "").strip().lower()
    if r.get("successor_policy_number") or ns == "renewed":
        return "Won"
    if ns in _RENEWAL_LOST:
        return "Lost"
    return "Open"


# Personal-lines LOBs get a tight 30-day renewal window; everything else
# (commercial) gets 120 days. Keyed off line_of_business because the `segment`
# column mislabels every personal policy as commercial.
_PERSONAL_LOB_RE = re.compile(
    r"(personal auto|personalauto|personsl auto|homeowner|dwelling fire|"
    r"motorcycle|personal umbrella|condo owners)",
    re.I,
)
def _env_int(name: str, default: int) -> int:
    """Read an int from env, falling back to default on unset/blank/garbage."""
    try:
        return int((os.getenv(name) or "").strip() or default)
    except ValueError:
        log.warning("invalid int for %s=%r; using %d", name, os.getenv(name), default)
        return default


# Forward-look windows, tunable via env (set in the box .env, then restart).
_PERSONAL_WINDOW_DAYS = _env_int("RENEWAL_WINDOW_PERSONAL_DAYS", 30)
_COMMERCIAL_WINDOW_DAYS = _env_int("RENEWAL_WINDOW_COMMERCIAL_DAYS", 120)


def _renewal_window_days(lob: str | None) -> int:
    """Forward-look window for a renewal, by line of business."""
    return _PERSONAL_WINDOW_DAYS if _PERSONAL_LOB_RE.search(lob or "") else _COMMERCIAL_WINDOW_DAYS


@app.get("/api/renewals")
async def list_renewals_endpoint(limit: int = 1000):
    """Upcoming renewal worklist from renewal_candidates.

    Forward window only: personal lines +30 days, commercial +120 days. Rows on
    expired/inactive policies and non-events (eligibility_state='excluded') are
    dropped, so dead AMS deep-links and already-renewed future-dated rows never
    appear. Carries the NowCerts insured GUID (AMS deep-link) and a derived
    Won/Lost/Open outcome per renewal."""
    rows = _get_supa().select(
        "renewal_candidates",
        columns="insured_id,policy_number,client_name,line_of_business,renewal_event_date,"
                "expiration_date,normalized_status,successor_policy_number,risk_status,segment,"
                "in_working_queue,eligibility_state,premium_current,premium_renewal,policy_active",
        params={"order": "expiration_date.asc"}, limit=limit,
    )
    today = date.today()
    out: list[dict[str, Any]] = []
    for r in rows:
        if not r.get("policy_active"):
            continue
        if str(r.get("eligibility_state") or "").strip().lower() == "excluded":
            continue
        raw_exp = r.get("expiration_date")
        if not raw_exp:
            continue
        try:
            exp = date.fromisoformat(str(raw_exp)[:10])
        except ValueError:
            continue
        if exp < today or exp > today + timedelta(days=_renewal_window_days(r.get("line_of_business"))):
            continue
        r["outcome"] = _renewal_outcome(r)
        out.append(r)
    return {"renewals": out, "count": len(out)}


@app.get("/api/workspace-stats")
async def workspace_stats_endpoint():
    """KPI tile counts for the Workspace home."""
    supa = _get_supa()

    def _rows(table, cols, params=None):
        try:
            return supa.select(table, columns=cols, params=params, limit=100000)
        except Exception:
            return []

    try:
        policies = ams_book.select_policies(
            supa, columns="annualized_premium,premium_amount", limit=100000
        )
    except Exception:  # noqa: BLE001 — a KPI tile degrades rather than 500s
        policies = []
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
    """Queue-centric health snapshot for dashboard SyncHealthCheck component.

    Defensive: a missing or renamed table degrades that section to 'unavailable'
    and reports status='degraded' instead of 500-ing the whole health check."""
    supa = _get_supa()
    status = "ok"

    def _count(table: str, state: str) -> int | None:
        try:
            return len(supa.select(table, columns="id", params={"status": f"eq.{state}"}, limit=1000))
        except Exception as exc:  # noqa: BLE001
            log.warning("sync-health: %s (%s) unavailable: %s", table, state, exc)
            return None

    queued = _count("outbound_sync_queue", "queued")
    if queued is None:
        status = "degraded"
        queue = {"unavailable": "outbound_sync_queue not found in schema"}
    else:
        queue = {
            "queued": queued,
            "failed": _count("outbound_sync_queue", "failed"),
            "dead": _count("outbound_sync_queue", "dead"),
        }

    # Freshness: the most recent job the AMS executors actually finished.
    try:
        recent = supa.select(
            "outbound_sync_queue",
            columns="id,object_type,destination_system,status,updated_at",
            params={"status": "eq.completed", "order": "updated_at.desc"},
            limit=1,
        )
        latest = recent[0] if recent else {}
        latest_completed = {
            "id": latest.get("id"),
            "object_type": latest.get("object_type"),
            "destination_system": latest.get("destination_system"),
            "updated_at": latest.get("updated_at"),
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("sync-health: outbound_sync_queue history unavailable: %s", exc)
        status = "degraded"
        latest_completed = {"unavailable": str(exc)[:200]}

    return {
        "status": status,
        "outbound_sync_queue": queue,
        "latest_completed": latest_completed,
    }


# ---------------------------------------------------------------------------
# Book-sync health — compares the actual book of business in NowCerts against
# the canonical Supabase mirror. Read-only; complements /api/hermes/sync-health
# (queue depth).
# See hermes/book_sync/health.py.
# ---------------------------------------------------------------------------


@app.get("/api/hermes/book-sync")
async def book_sync_health(request: Request, max_pages: int = 50):
    """Drift report: policy counts, tombstones, per-carrier premium, rate drift.

    Gated by HERMES_API_TOKEN bearer (skipped if env var unset).

    Query params:
      max_pages: cap NowCerts pagination (default 50 → ~5000 policies).
    """
    _require_hermes_token(request)
    from hermes.book_sync import run_book_sync_health

    try:
        report = run_book_sync_health(
            nowcerts_client=_get_nowcerts(),
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

    Same shared logic the interactive approval button calls.
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
# ---------------------------------------------------------------------------
# Voice output — TTS endpoint. Returns the mp3; Slack upload removed 2026-07-26.
# ---------------------------------------------------------------------------

class TTSRequest(BaseModel):
    """Text-to-speech request. Returns the audio; delivery is the caller's job."""
    text: str = Field(..., min_length=1, max_length=4000)
    voice: str = Field(default="", description="Voice name. Defaults to en-US-AriaNeural.")


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
    """Generate a voice clip from text and return the audio.

    Uses Edge TTS (en-US-AriaNeural) by default — free, no API key.
    Falls back to the LiteLLM/OpenAI TTS API if edge-tts isn't installed.

    This used to upload the clip to a Slack channel via slack_sdk. Slack is
    retired, so that path could only ever 503; the endpoint now returns the mp3
    and the caller decides what to do with it. Nothing in the codebase called
    the Slack version.

    Auth: bearer token (HERMES_API_TOKEN) when configured.
    """
    _require_hermes_token(request)

    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    audio = await _generate_tts_audio(text, req.voice)
    if not audio:
        raise HTTPException(status_code=502, detail="TTS generation failed (no provider available)")

    return Response(
        content=audio,
        media_type="audio/mpeg",
        headers={"Content-Disposition": 'inline; filename="hermes_voice.mp3"',
                 "X-Hermes-Chars": str(len(text))},
    )



def main() -> int:
    load_dotenv()
    logging.basicConfig(level=os.environ.get("HERMES_API_LOG_LEVEL", "INFO"))

    if not os.environ.get("SERVICE_WEBHOOK_SECRET", "").strip():
        log.warning(
            "SERVICE_WEBHOOK_SECRET is not set — all service webhook "
            "requests will be rejected (401). "
            "Set it in .env and recreate the container with "
            "`docker compose up -d hermes-api` (restart does not reload env_file)."
        )

    parser = argparse.ArgumentParser(description="Hermes private HTTP API")
    parser.add_argument("--host", default=os.environ.get("HERMES_API_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("HERMES_API_PORT", "8484")))
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)
    return 0
