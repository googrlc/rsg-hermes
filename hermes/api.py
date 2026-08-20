"""Hermes REST API — FastAPI wrapper around the Dispatcher."""

from __future__ import annotations

import argparse
import base64
import binascii
import logging
import os
import re
import threading
from datetime import date
from typing import Any, Literal

import uvicorn
from dotenv import load_dotenv
from fastapi import APIRouter, BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field, model_validator

from hermes_core import book as ams_book
from hermes_core import surfaces
from hermes_app import deps

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


# The hub's own routes hang off this router rather than off `app` directly, so
# the app factory in hermes/services.py can compose a process out of any subset
# of routers — hub alone, one app alone, or everything (the default). It is
# included at the BOTTOM of this file, after every route below is defined.
router = APIRouter()

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

# The cockpit UI that used to be mounted here — hermes/webui/{cockpit,index,
# workspace}.html at /command-center/ — is gone. The RSG Agency Portal is the
# agency's one screen now, and two CRMs answering the same questions from the
# same tables is how they start disagreeing.
#
# What is NOT gone: every /api/command-center/* endpoint below. The portal is
# built on five of them (renewals, tasks, tasks/{id}/complete, retention, ask),
# so the path prefix stays exactly as it is. It reads as a leftover; it is load-
# bearing. Renaming it is a portal outage, not a tidy-up.
#
# The Command Center intake lane keeps its own pages — they are part of the
# intake subsystem, not the cockpit, and are mounted by its router below.

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

# Per-app routers (docs/repo-split-plan.md, Phase 2). Each owns its own routes,
# models and helpers; this file is the shell that mounts them. Not wrapped in
# try/except like the two below — a finance router that fails to import is a
# broken deploy, not a degraded one, and must not start serving 404s quietly.
#
# `cases` is absent because it left: /api/cases, /api/case-templates,
# /api/casework, /api/tasks and /api/queue are served by googrlc/rsg-hermes-cases
# on 8802, and the MCP bridge routes them there (HERMES_CASES_URL). Mounting a
# second copy here would give those paths two implementations against one set of
# tables, drifting apart from the day it was added.
from hermes.routers import carriers as _carriers_router
from hermes.routers import finance as _finance_router
from hermes.routers import intake as _intake_router
from hermes.routers import renewals as _renewals_router

app.include_router(_finance_router.router)
app.include_router(_carriers_router.router)
app.include_router(_renewals_router.router)
app.include_router(_intake_router.router)

# General document extractor (OCR-aware quote-field extraction) — POST /api/extract.
try:
    from hermes.command_center.extract_api import router as _extract_router

    app.include_router(_extract_router)
except Exception:  # pragma: no cover - surfaced in logs, never fatal
    log.exception("extract routes unavailable")

# The clients, the bearer gate and the agency_crm_users guard live in
# hermes/routers/deps.py so a route can move to a router module without leaving
# its dependencies behind. These delegators keep every call site in this file —
# and the tests that patch them — working unchanged.
_dispatcher = None
_dispatcher_lock = threading.Lock()


def _get_dispatcher():
    """The natural-language Dispatcher — the hub's own, not shared plumbing.

    It lives here rather than in hermes_app.deps because building one imports
    hermes.agent, and a shared layer that reaches into an app would put the hub
    inside every other app repo.
    """
    global _dispatcher
    if _dispatcher is None:
        with _dispatcher_lock:
            if _dispatcher is None:
                from hermes.agent.dispatcher import Dispatcher

                use_openai = bool(
                    os.environ.get("OPENAI_API_KEY") or os.environ.get("HERMES_OPENAI_API_KEY")
                )
                _dispatcher = Dispatcher(use_openai=use_openai)
    return _dispatcher


def _get_supa():
    return deps.get_supa()


def _get_nowcerts():
    return deps.get_nowcerts()


def _require_hermes_token(request: Request) -> None:
    return deps.require_hermes_token(request)


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


# "intake_gate" is the RSG intake gate (rsg-cptintake) — the guarded gateway that
# synthesizes an intake from PDFs, transcripts and operator facts and submits the
# result here. Named rather than folded into "manual_curl" so the pipeline can tell
# a reviewed, cited intake apart from a hand-rolled request.


















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


@router.get("/")
def root():
    """What this service is, and where the screen went.

    This process serves no UI any more. Someone landing here has followed an old
    bookmark to the cockpit, so say where the CRM actually is rather than 404ing
    at them — and if nobody configured the portal's address, say that plainly
    instead of inventing a URL."""
    return {
        "service": "rsg-hermes-api",
        "ui": "none — the CRM is the RSG Agency Portal",
        "portal": surfaces.portal_url() or "unset (HERMES_PORTAL_URL)",
        "docs": "/docs",
    }


@router.get("/health")
def health():
    return {"status": "ok", "service": "hermes"}


def attach_monolith_healthz(target_app: FastAPI | None = None) -> None:
    """Attach GET /healthz on the unsplit app (idempotent).

    Split services get /healthz from hermes_app.service.bare_app. The monolith
    builds its routes before create_app runs, so we register here once.
    """
    from hermes_app.role import (
        ROLE_WRITE_IN,
        current_role,
        inferred_db_user,
        modules_for,
        modules_loaded_names,
        nowcerts_creds_present,
    )
    from hermes_app.service import _mirror_lag_seconds

    app_obj = target_app if target_app is not None else app
    if getattr(app_obj.state, "hermes_healthz_attached", False):
        return

    role = current_role("all")
    loaded = modules_loaded_names(modules_for(role, "all"))
    app_obj.state.hermes_role = role
    app_obj.state.hermes_modules = list(loaded)
    app_obj.state.hermes_healthz_attached = True

    @app_obj.get("/healthz")
    def healthz() -> dict[str, object]:
        payload: dict[str, object] = {
            "role": role,
            "service": "all",
            "modules_loaded": loaded,
            "nowcerts": nowcerts_creds_present(),
            "db_user": inferred_db_user(role),
            "mirror_lag_seconds": None,
        }
        if role == ROLE_WRITE_IN:
            payload["mirror_lag_seconds"] = _mirror_lag_seconds()
        return payload


@router.get("/hermes/ping", response_model=DispatchResponse)
def hermes_ping():
    """Compatibility ping endpoint for WebUI connectors that call /hermes/ping."""
    return DispatchResponse(
        ok=True,
        message="Pong! How can I assist you with the CRM today?",
        data=None,
        requires_confirmation=False,
    )


@router.post("/dispatch", response_model=DispatchResponse)
def dispatch(req: DispatchRequest):
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


@router.post("/api/hermes/dispatch", response_model=AsyncAcceptedResponse)
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


@router.get("/api/command-center/renewals")
def command_center_renewals():
    """Live Renewals Cockpit data (Command Center, Phase 1).

    Server-side, service-role read of ``project_85_renewals`` aggregated into
    urgency buckets + the next-90-day list. No anon key ever reaches the browser.

    Human corrections are overlaid before anything is counted, and renewals a
    person removed are dropped — so the buckets, the premium totals and the list
    all agree with what the desk actually decided.
    """
    from hermes.renewals.tracker import attach_policy_dates, summarize_renewals
    from hermes.renewals import corrections as corr

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
    rows = [r for r in corr.apply(supa, rows)
            if not corr.is_dismissed(corr.PROJECTION, r)]
    # The renewal table holds an expiration date and nothing else about the term.
    # Effective date, carrier and line come off the book so the desk can see the
    # whole policy period, not just the end of it.
    return summarize_renewals(attach_policy_dates(supa, rows))


@router.get("/api/command-center/lapse-check")
def command_center_lapse_check():
    """Past-due-but-still-active renewals, kept OFF the forward renewals pipeline.

    The renewals board only carries the forward window (June-1 floor → +120 days);
    expired-but-active policies route here instead — likely silent lapses to
    confirm in NowCerts. Derived from ``renewal_candidates`` (needs_verification)."""
    from hermes.renewals.candidate_refresh import lapse_check

    return lapse_check(_get_supa())


@router.get("/api/command-center/tasks")
def command_center_tasks():
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


@router.post("/api/command-center/tasks/{task_id}/complete")
def command_center_complete_task(task_id: str):
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


@router.get("/api/command-center/skills")
def command_center_skills():
    """List Hermes's capabilities — live tools + domain playbooks."""
    from hermes.agent.skills_catalog import catalog

    return catalog()


class FileSaveRequest(BaseModel):
    title: str
    content: str
    kind: str = "note"
    content_type: str = "text/markdown"
    file_ext: str = "md"


@router.post("/api/command-center/files")
def command_center_save_file(req: FileSaveRequest):
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


@router.get("/api/command-center/files")
def command_center_list_files():
    """List files Hermes has created (newest first)."""
    from hermes.operations.files_store import list_files

    return {"files": list_files(_get_supa())}


@router.get("/api/command-center/files/{file_id}/download")
def command_center_download_file(file_id: str):
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


@router.post("/api/command-center/ask")
def command_center_ask(req: AskRequest):
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
    from hermes.agent.nl_agent import ask as nl_ask

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


@router.get("/api/command-center/retention")
def command_center_retention():
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


@router.post("/api/command-center/save-list")
def command_center_build_save_list(req: SaveListRequest):
    """Build + stage a retention save-list (top at-risk renewals → DRAFT outreach).

    Writes DRAFT rows only; nothing is auto-sent. The sole write action in Phase 2.
    """
    from hermes.operations.save_list import create_save_list

    return create_save_list(_get_supa(), limit=req.limit, within_days=req.within_days)


@router.get("/api/command-center/save-list")
def command_center_list_save_list():
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
    expected_close_date: str | None = None   # CRM-owned forecast (YYYY-MM-DD)
    source: str = "manual"
    created_by: str | None = None

    @model_validator(mode="after")
    def _need_client(self):
        if not (self.client_identifier or self.insured_name):
            raise ValueError("client_identifier or insured_name is required")
        return self


@router.post("/api/opportunities")
def create_opportunity_endpoint(req: OpportunityCreateRequest, background_tasks: BackgroundTasks):
    """Create (or return existing) a pipeline opportunity for ANY client — new,
    inactive, or a cross-sell on a current client. Idempotent per
    (client_identifier, line_of_business); the smart create logic (identifier,
    dedup, insured link) lives in one place so every cockpit writes correctly.
    """
    from hermes_core import opportunities as opp

    if req.assigned_to_email:
        _require_users(_get_supa(), [("assigned_to_email", req.assigned_to_email)])
    ci = req.client_identifier or opp.make_client_identifier(req.insured_name, req.fein)
    # Whether this is new business is not a matter of opinion — it depends on
    # whether they are already a client, which is exactly what having a NowCerts
    # insured id means. Left to a default, everything gets typed New Business and
    # the board can no longer tell growth of the book from genuinely new names.
    otype = (req.opportunity_type or "").strip() or opp.derive_opportunity_type(
        _get_supa(), req.insured_id, req.line_of_business
    )
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
            expected_close_date=req.expected_close_date,
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
    if created:
        opp.log_event(
            _get_supa(), str(row.get("id")), event_type=opp.EVENT_CREATED,
            actor_email=req.created_by,
            summary=f"Opened — {otype} on {req.line_of_business}"
                    + (f" (from {req.source})" if req.source and req.source != "manual" else ""),
            details={"source": req.source, "opportunity_type": otype},
        )
    return {"ok": True, "created": created, "opportunity": row}


@router.get("/api/opportunities")
def list_opportunities_endpoint(stage: str | None = None, status: str | None = "open", limit: int = 100):
    """List pipeline opportunities (default open), newest-updated first.

    Each row carries a derived ``projected_close_date`` (+ the ``projected_close_basis``
    it came from) so a board can be read as a forecast rather than a pile — see
    ``opportunities.projected_close``.
    """
    from hermes_core import opportunities as opp

    try:
        rows = opp.list_opportunities(_get_supa(), stage=stage, status=status, limit=limit)
    except Exception as exc:
        log.exception("list opportunities failed")
        raise HTTPException(status_code=502, detail=str(exc))
    return {"opportunities": opp.with_projected_close(rows), "count": len(rows)}


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
    expected_close_date: str | None = None
    # The bound policy number — required before a won deal can be filed in the AMS.
    policy_number: str | None = None
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


@router.patch("/api/opportunities/{opportunity_id}")
def update_opportunity_endpoint(opportunity_id: str, req: OpportunityUpdateRequest):
    """Edit an opportunity's fields in the CRM. Supabase-only — an opportunity is
    worked in the CRM and does not write back to the AMS until it's Bound/Won or
    Lost. Only the fields present in the request are changed; setting ``stage``
    also re-derives ``status``."""
    from hermes_core import opportunities as opp

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
        dup = _duplicate_deal_detail(exc)
        if dup:
            # 409, not 502: nothing is broken, the change is simply refused.
            raise HTTPException(status_code=409, detail=dup)
        log.exception("update opportunity failed: %s", opportunity_id)
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True, "opportunity": row}


def _duplicate_deal_detail(exc: Exception) -> str | None:
    """Turn Postgres 23505 on the opportunities unique index into a sentence.

    The operator saw the raw driver error — `duplicate key value violates unique
    constraint "uq_opportunities_client_lob_type"` with the key tuple inline —
    while trying to correct a deal's type. That message names the constraint
    rather than the situation, and the situation is specific: this client already
    has a deal on this line in that type, so the row being edited is a duplicate
    of it. Retyping cannot resolve that; one of the two has to go.
    """
    text = str(exc)
    if "23505" not in text and "duplicate key" not in text:
        return None
    m = re.search(r"\)=\(([^)]*)\)", text)
    if not m:
        return "A deal like this already exists for that client and line of business."
    parts = [p.strip() for p in m.group(1).split(",")]
    if len(parts) >= 3:
        client, lob, otype = parts[0], parts[1], parts[2]
        return (f"{client} already has a {lob} deal of type {otype}. This one is a "
                f"duplicate of it — open that deal instead, or delete this row. "
                f"Changing the type cannot merge them.")
    return "A deal like this already exists for that client and line of business."


@router.get("/api/opportunities/{opportunity_id}/events")
def list_opportunity_events_endpoint(opportunity_id: str, limit: int = 200):
    """A deal's timeline — notes, stage moves, creation, AMS filings, newest first.

    The pipeline was the one working surface with no history: `description` was
    overwritten by each edit and stage moves were applied in place, so "who moved
    this to Lost, and when, and why" had no answer on the records where it matters
    most."""
    from hermes_core import opportunities as opp

    try:
        return {"events": opp.list_events(_get_supa(), opportunity_id, limit=limit)}
    except Exception as exc:
        log.exception("opportunity events read failed: %s", opportunity_id)
        raise HTTPException(status_code=502, detail=str(exc))


class OpportunityNoteRequest(BaseModel):
    body: str
    author_email: str | None = None


@router.post("/api/opportunities/{opportunity_id}/notes")
def add_opportunity_note_endpoint(opportunity_id: str, req: OpportunityNoteRequest):
    """Write down what happened on a deal. Appended to the same timeline the stage
    moves land on, so one list answers "what happened here"."""
    from hermes_core import opportunities as opp

    text = str(req.body or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="a note needs a body")
    supa = _get_supa()
    rows = []
    try:
        rows = supa.select("opportunities", columns="id", params={"id": f"eq.{opportunity_id}"}, limit=1)
    except Exception:
        rows = []                      # malformed uuid → not found, not a 502
    if not rows:
        raise HTTPException(status_code=404, detail="opportunity not found")
    note = opp.log_event(supa, opportunity_id, summary=text,
                         event_type=opp.EVENT_NOTE, actor_email=req.author_email)
    if note is None:
        raise HTTPException(status_code=502, detail="the note could not be saved")
    return {"ok": True, "note": note}


@router.delete("/api/opportunities/{opportunity_id}")
def delete_opportunity_endpoint(opportunity_id: str):
    """Delete an opportunity from the CRM. Supabase-only — opportunities never write
    to the AMS, so there's nothing to unwind in NowCerts. Any attached quotes are
    removed automatically (opportunity_quotes FK is ON DELETE CASCADE)."""
    try:
        _get_supa().delete("opportunities", opportunity_id)
    except Exception as exc:
        log.exception("delete opportunity failed: %s", opportunity_id)
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True, "deleted": opportunity_id}






















@router.get("/api/cross-sell")
def cross_sell_search_endpoint(q: str = "", limit: int = 25):
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


@router.post("/api/opportunities/{opportunity_id}/send-to-nowcerts")
def send_opportunity_quote(opportunity_id: str, req: SendQuoteRequest):
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
    # Who dragged the card. The portal has always sent this and the model has
    # always dropped it, so every move was attributed to 'cockpit-stage-move'.
    moved_by: str | None = None


@router.post("/api/opportunities/{opportunity_id}/stage")
def update_opportunity_stage(opportunity_id: str, req: StageUpdateRequest):
    """Move an opportunity to a new pipeline stage (Kanban drag). Syncs status
    (won/lost). The move itself is Supabase-only; when it lands on a terminal stage
    (Bound/Won or Lost) it QUEUES an approval-gated writeback to NowCerts — nothing
    hits the AMS until the opportunity-writeback executor drains it."""
    from hermes_core import opportunities as opp

    supa = _get_supa()
    stage = (req.stage or "").strip()
    try:
        # advance_stage accepts any non-empty stage (NowCerts owns the vocabulary).
        row = opp.advance_stage(
            supa, opportunity_id, stage,
            lost_reason=req.lost_reason,
            moved_by=req.moved_by or req.approved_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        log.exception("stage update failed: %s", opportunity_id)
        raise HTTPException(status_code=502, detail=str(exc))

    # Terminal → queue the AMS writeback (Bound/Won or Lost). Best-effort: a queue
    # hiccup must not fail the stage move.
    #
    # This one only ever syncs the STAGE of an opportunity that already exists in
    # NowCerts. It is right for both won and lost: that record is there either way
    # and leaving it open forever is its own kind of lie.
    status = str(row.get("status") or "")
    queued = None
    if status in ("won", "lost") and row.get("nowcerts_opportunity_id"):
        try:
            from hermes.sync.opportunity_writeback import stage_writeback

            job = stage_writeback(supa, row, approved_by=req.approved_by or "cockpit-stage-move", stage=stage)
            queued = bool(job)
        except Exception:
            log.exception("opportunity writeback staging failed: %s", opportunity_id)

    # WON → the insured and the policy have to exist in the system of record. This
    # is the path that was missing: a deal opened in the CRM (a cross-sell, or a
    # converted lead) carries no nowcerts_opportunity_id, so nothing above fires
    # and NowCerts never heard it was won.
    #
    # LOST stages nothing here, ever. A lost deal was never coverage; it stays in
    # the CRM with its x-date, which is next year's remarket list.
    won_queued = None
    won_blocked = None
    if status == "won":
        from hermes.sync.opportunity_won import NotPushable, stage_won

        actor = req.moved_by or req.approved_by
        try:
            stage_won(supa, row, approved_by=req.approved_by or "cockpit-stage-move")
            won_queued = True
            opp.log_event(supa, opportunity_id, event_type=opp.EVENT_AMS, actor_email=actor,
                          summary="Queued for NowCerts — insured and policy to be filed")
        except NotPushable as exc:
            # Not an error — the deal moved, it just is not ready to be filed.
            # Say what is missing so it can be fixed rather than silently dropped.
            won_blocked = str(exc)
            opp.log_event(supa, opportunity_id, event_type=opp.EVENT_AMS, actor_email=actor,
                          summary=f"NOT filed in NowCerts — {exc}")
        except Exception:
            log.exception("won-deal staging failed: %s", opportunity_id)

    return {
        "ok": True, "opportunity": row, "writeback_queued": queued,
        "ams_queued": won_queued, "ams_blocked": won_blocked,
    }


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


@router.post("/api/opportunities/{opportunity_id}/quotes")
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


@router.get("/api/opportunities/{opportunity_id}/quotes")
def list_opportunity_quotes_endpoint(opportunity_id: str):
    """Quotes attached to one opportunity (newest first)."""
    from hermes.quotes import store as quote_store

    rows = quote_store.list_quotes(_get_supa(), opportunity_id=opportunity_id)
    return {"quotes": rows, "count": len(rows)}


@router.get("/api/quotes")
def list_quotes_endpoint(insured_id: str | None = None, limit: int = 500):
    """All carrier quotes — the Quotes module (grouped by opportunity). Pass
    insured_id to get one client's quotes across their opportunities."""
    from hermes.quotes import store as quote_store

    try:
        rows = quote_store.list_quotes(_get_supa(), insured_id=insured_id, limit=limit)
    except Exception as exc:
        log.exception("list quotes failed")
        raise HTTPException(status_code=502, detail=str(exc))
    return {"quotes": rows, "count": len(rows)}


@router.post("/api/quotes/{quote_id}/document")
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


@router.post("/api/quotes/{quote_id}/send-to-nowcerts")
def send_quote_to_nowcerts(quote_id: str, req: SendQuoteRequest):
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


@router.post("/api/proposals")
def create_proposal_endpoint(req: ProposalCreateRequest):
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


@router.get("/api/proposals")
def list_proposals_endpoint(insured_id: str | None = None, limit: int = 500):
    """All proposals (newest first), or one client's when insured_id is given."""
    from hermes.proposals import store as prop_store

    try:
        rows = prop_store.list_proposals(_get_supa(), insured_id=insured_id, limit=limit)
    except Exception as exc:
        log.exception("list proposals failed")
        raise HTTPException(status_code=502, detail=str(exc))
    return {"proposals": rows, "count": len(rows)}


@router.get("/api/proposals/{proposal_id}")
def get_proposal_endpoint(proposal_id: str):
    from hermes.proposals import store as prop_store

    row = prop_store.get_proposal(_get_supa(), proposal_id)
    if not row:
        raise HTTPException(status_code=404, detail="proposal not found")
    return {"proposal": row}


@router.get("/api/proposals/{proposal_id}/view", response_class=HTMLResponse)
def view_proposal_endpoint(proposal_id: str):
    """The rendered proposal HTML — open in a tab or print to PDF from the browser."""
    from hermes.proposals import store as prop_store

    row = prop_store.get_proposal(_get_supa(), proposal_id)
    if not row:
        raise HTTPException(status_code=404, detail="proposal not found")
    return HTMLResponse(content=row.get("content_html") or "<p>Not yet rendered.</p>")


@router.post("/api/proposals/{proposal_id}/regenerate")
def regenerate_proposal_endpoint(proposal_id: str, req: ProposalRegenerateRequest):
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


@router.post("/api/proposals/{proposal_id}/status")
def set_proposal_status_endpoint(proposal_id: str, req: ProposalStatusRequest):
    from hermes.proposals import store as prop_store

    row = prop_store.set_status(_get_supa(), proposal_id, req.status)
    return {"ok": True, "proposal": row}


@router.get("/api/clients/search")
def search_clients_endpoint(q: str, limit: int = 20):
    """Search the canonical book by insured name — powers the New-Opportunity client
    picker (active OR inactive clients). Returns the NowCerts guid + display fields.
    """
    query = (q or "").strip()
    if len(query) < 2:
        return {"clients": [], "count": 0}
    try:
        rows = _get_supa().select(
            "canonical_clients",
            columns="nowcerts_insured_guid,insured_name,client_type,city,state,email,phone,active",
            params={"insured_name": f"ilike.*{query}*", "order": "insured_name.asc"},
            limit=limit,
        )
    except Exception as exc:
        log.exception("client search failed: %s", query)
        raise HTTPException(status_code=502, detail=str(exc))
    return {"clients": rows, "count": len(rows)}


# ── Cases + Tasks (workflow) — sanctioned create/list for any cockpit ──
def _active_user_emails(supa) -> set[str]:
    return deps.active_user_emails(supa)


def _require_users(supa, pairs: list[tuple[str, str | None]]) -> None:
    return deps.require_users(supa, pairs)


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
    from hermes_integrations.nextcloud_deck import DeckClient

    return DeckClient()


@router.get("/api/deck/boards")
def deck_boards_endpoint():
    """Boards, and the stacks (lists) on each — what a caller needs to address a card."""
    from hermes_integrations.nextcloud_deck import DeckError

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


@router.post("/api/deck/cards")
def deck_create_card_endpoint(req: DeckCardRequest):
    """Add a card. Idempotent by title within the list, so a job that runs twice
    doesn't leave two identical cards."""
    from hermes_integrations.nextcloud_deck import DeckError

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


@router.get("/api/agency-users")
def list_agency_users_endpoint(assignable: bool = False):
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




































# ---------------------------------------------------------------------------
# Deleting case work.
#
# A task or case created by mistake had no way out: cancelling leaves it in the
# list forever, and the portal is the only surface most of this work has. So a
# real delete — with a named actor and a row in portal_write_log carrying the
# record as it was, because "it disappeared and nobody knows who" is how a
# shared queue stops being trusted.
# ---------------------------------------------------------------------------






























# Case attachments (issue #195). Case-level only, deliberately: every task already
# belongs to a case or a client, so a second home for documents would just be a
# place for them to hide. A renewal worksheet attached to a task but invisible on
# its case is worse than no attachment feature at all.
#
# Filing category follows the case type, so a renewal's paperwork lands in the
# client's "Renewal Reviews" folder rather than a generic dump.






# ── Book reads + Workspace KPIs (power the CRM cockpit views) ──
# ---------------------------------------------------------------------------
# Client corrections.
#
# The CRM is a read-only mirror of NowCerts, which leaves nobody able to fix a
# wrong phone number without opening the AMS. An override is a human correction
# that outranks the synced value until the source catches up — the same
# mechanism the commission surface already uses, so corrections are visible,
# attributed, reversible, and reconciled rather than silently overwriting a
# mirror that the next sync would clobber anyway.
#
# This does NOT write to NowCerts. Write-back is a separate decision.
# ---------------------------------------------------------------------------
CLIENT_ENTITY_TYPE = "canonical_clients"
CLIENT_OVERRIDABLE_FIELDS = frozenset({
    "insured_name", "client_type", "email", "phone",
    "address", "city", "state", "zip", "notes",
})
# `active` is deliberately absent from both allowlists. canonical_clients.active
# is recomputed by a database trigger from the client's own policies, in the same
# transaction as any bind/cancel/insert/delete — so it is not a field anyone
# sets. An override on it would not change the value, it would only paint over
# it at read time: the screen would say active while the book said otherwise,
# which is worse than not offering the control at all.
#
# The lever is the policy. Cancel a client's last active policy in NowCerts and
# the flag follows on its own; there is nothing to set here.


class ClientOverrideRequest(BaseModel):
    field_name: str
    value: Any = None
    approved_by: str
    reason: str | None = None


@router.post("/api/clients/{insured_guid}/override")
def override_client_field(insured_guid: str, req: ClientOverrideRequest):
    """Correct a client field in the portal.

    Keyed on the NowCerts insured GUID so the correction survives a re-seed of
    the mirror. Retires itself once NowCerts reports the same value.
    """
    from hermes_core.overrides.store import set_override

    supa = _get_supa()
    _require_users(supa, [("approved_by", req.approved_by)])

    field_name = (req.field_name or "").strip()
    if field_name not in CLIENT_OVERRIDABLE_FIELDS:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name!r} is not overridable; allowed: {sorted(CLIENT_OVERRIDABLE_FIELDS)}",
        )

    try:
        rows = supa.select("canonical_clients", columns="*",
                           params={"nowcerts_insured_guid": f"eq.{insured_guid}"}, limit=1)
    except Exception:
        rows = []
    if not rows:
        raise HTTPException(status_code=404, detail="client not found")

    try:
        row = set_override(
            supa,
            entity_type=CLIENT_ENTITY_TYPE,
            entity_key=insured_guid,
            field_name=field_name,
            override_value=req.value,
            # The value the SOURCE currently reports — reconciliation compares
            # against this to decide whether the AMS has caught up.
            original_value=rows[0].get(field_name),
            approved_by=req.approved_by,
            reason=req.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "override": row}


def _apply_client_overrides(supa, records: list[dict]) -> list[dict]:
    """Overlay active corrections onto client rows. Best-effort: a correction is
    an enrichment, and losing it must not take the client list down."""
    from hermes_core.overrides.core import apply_overrides
    from hermes_core.overrides.store import active_overrides

    try:
        ovr = active_overrides(supa, CLIENT_ENTITY_TYPE)
    except Exception:  # noqa: BLE001
        log.exception("client overrides lookup failed; serving uncorrected")
        return records
    if not ovr:
        return records
    return apply_overrides(
        records, ovr, entity_type=CLIENT_ENTITY_TYPE, key_field="nowcerts_insured_guid"
    )


# ---------------------------------------------------------------------------
# Policy corrections.
#
# Same mechanism as client corrections, one rule tighter: the identifiers a
# policy carries out of NowCerts — policy_guid, nowcerts_insured_guid,
# renewed_policy, policy_number — are not overridable. They are how a row is
# matched back to the AMS and how a renewal is tied to the term it replaced.
# "Correcting" one does not fix the record, it detaches it from the source and
# from its own lineage. They are shown, and shown read-only.
# ---------------------------------------------------------------------------
POLICY_ENTITY_TYPE = "canonical_policies"
POLICY_OVERRIDABLE_FIELDS = frozenset({
    "carrier", "lines_of_business", "status",
    "effective_date", "expiration_date",
    "premium_amount", "annualized_premium",
})


class PolicyOverrideRequest(BaseModel):
    field_name: str
    value: Any = None
    approved_by: str
    reason: str | None = None


@router.post("/api/policies/{policy_guid}/override")
def override_policy_field(policy_guid: str, req: PolicyOverrideRequest):
    """Correct a policy field in the portal. Keyed on the NowCerts policy GUID,
    so the correction survives a re-seed of the mirror. Not an AMS write."""
    from hermes_core.overrides.store import set_override

    supa = _get_supa()
    _require_users(supa, [("approved_by", req.approved_by)])

    field_name = (req.field_name or "").strip()
    if field_name not in POLICY_OVERRIDABLE_FIELDS:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name!r} is not overridable; allowed: {sorted(POLICY_OVERRIDABLE_FIELDS)}",
        )

    try:
        rows = ams_book.select_policies(
            supa, columns="*", params={"policy_guid": f"eq.{policy_guid}"}, limit=1,
        )
    except Exception:  # noqa: BLE001
        rows = []
    if not rows:
        raise HTTPException(status_code=404, detail="policy not found")

    try:
        row = set_override(
            supa,
            entity_type=POLICY_ENTITY_TYPE,
            entity_key=policy_guid,
            field_name=field_name,
            override_value=req.value,
            original_value=rows[0].get(field_name),
            approved_by=req.approved_by,
            reason=req.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "override": row}


def _apply_policy_overrides(supa, records: list[dict]) -> list[dict]:
    """Overlay active corrections onto policy rows. Best-effort, like clients."""
    from hermes_core.overrides.core import apply_overrides
    from hermes_core.overrides.store import active_overrides

    try:
        ovr = active_overrides(supa, POLICY_ENTITY_TYPE)
    except Exception:  # noqa: BLE001
        log.exception("policy overrides lookup failed; serving uncorrected")
        return records
    if not ovr:
        return records
    return apply_overrides(
        records, ovr, entity_type=POLICY_ENTITY_TYPE, key_field="policy_guid"
    )


# ---------------------------------------------------------------------------
# Pushing a correction on to NowCerts.
#
# A CRM override fixes what the portal shows. It does not fix the AMS, and for a
# wrong phone number the AMS never catches up on its own. These endpoints are the
# other half: they take the same fields, keyed on the record's NowCerts GUID, and
# write them to the system of record with read-before / verify / receipt.
#
# The human gate is the portal's confirmation step — the approval is stamped on
# the queue row from `approved_by`. That is a deliberate departure from the
# renewal writeback's separate approve-later inbox: the renewal executor's cron
# is not enabled, so a row parked for approval would wait forever.
# ---------------------------------------------------------------------------
class AmsPushRequest(BaseModel):
    fields: dict[str, Any]
    approved_by: str
    reason: str | None = None


def _ams_push(object_type: str, object_id: str, req: AmsPushRequest):
    from hermes.ams import writeback

    supa = _get_supa()
    _require_users(supa, [("approved_by", req.approved_by)])
    if not req.fields:
        raise HTTPException(status_code=400, detail="no fields to push")
    try:
        return writeback.push(
            supa, _get_nowcerts(), object_type=object_type, object_id=object_id,
            fields=req.fields, actor=req.approved_by, note=req.reason,
        )
    except ValueError as exc:      # unknown/unpushable field, missing id
        raise HTTPException(status_code=400, detail=str(exc))




class PolicyCreateRequest(BaseModel):
    """Create a policy in NowCerts. The AMS owns what a client has BOUND, so this
    writes there rather than into a mirror that the next sync would overwrite."""

    insured_id: str                      # the NowCerts insured GUID
    policy_number: str
    carrier: str | None = None
    lines_of_business: str | None = None
    effective_date: str | None = None
    expiration_date: str | None = None
    premium_amount: float | None = None
    approved_by: str
    reason: str | None = None


@router.post("/api/policies")
def create_policy_endpoint(req: PolicyCreateRequest):
    """Add a policy to a client, in NowCerts.

    Deliberately NOT a write to canonical_policies: that table is a mirror under a
    two-writer freeze, and a row invented there would be tombstoned by the next
    import. The insured GUID is verified against the AMS before anything is
    created, so a typo'd id cannot spawn an orphan policy.
    """
    from hermes.ams import writeback

    supa = _get_supa()
    _require_users(supa, [("approved_by", req.approved_by)])
    nowcerts = _get_nowcerts()

    if writeback._read(nowcerts, writeback.OBJECT_TYPE_CLIENT, req.insured_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"could not confirm insured {req.insured_id} in NowCerts — "
                   f"refusing to create a policy against an unknown client",
        )

    payload: dict[str, Any] = {
        "InsuredDatabaseId": req.insured_id,
        "Number": req.policy_number,
        "IsQuote": False,
    }
    if req.carrier:
        payload["CarrierName"] = req.carrier
    if req.lines_of_business:
        payload["LineOfBusinessName"] = req.lines_of_business
    if req.effective_date:
        payload["EffectiveDate"] = str(req.effective_date)
    if req.expiration_date:
        payload["ExpirationDate"] = str(req.expiration_date)
    if req.premium_amount is not None:
        payload["Premium"] = float(req.premium_amount)

    try:
        created = nowcerts.insert_policy(payload)
    except Exception as exc:  # noqa: BLE001
        log.exception("create policy failed: %s", req.policy_number)
        raise HTTPException(status_code=502, detail=f"NowCerts refused the policy: {exc}")

    from hermes_core.overrides.store import write_log

    write_log(supa, entity_type="nowcerts_policy", entity_key=req.policy_number,
              action="ams_create", actor=req.approved_by, before=None, after=payload,
              note=req.reason)
    return {"ok": True, "policy": created, "sent": payload}



class IntakeAmsWriteLogRequest(BaseModel):
    object_id: str | None = None
    insured_database_id: str | None = None
    action: str = "create"
    approved_by: str | None = None
    actor: str | None = None
    adopted: bool = False
    verified: bool | None = None
    fingerprint: str | None = None
    source: str | None = "cptintake_gateway"


@router.post("/api/ams/intake-write-log")
def intake_ams_write_log(req: IntakeAmsWriteLogRequest, request: Request):
    """Gateway callback: record an AMS create/adopt into portal_write_log + queue."""
    _require_hermes_token(request)
    from hermes.ams.intake_audit import record_intake_ams_write

    try:
        return record_intake_ams_write(_get_supa(), _model_dict(req))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/ams/failed-pushes")
def list_failed_pushes(limit: int = 50):
    """Corrections that never reached NowCerts.

    Worth surfacing rather than leaving in a table: the CRM shows the corrected
    value either way, so a push that failed is invisible on the one screen
    somebody would go to to check it.
    """
    from hermes.ams import writeback

    try:
        rows = writeback.list_failed(_get_supa(), limit=limit)
    except Exception:  # noqa: BLE001 — a banner must not take the portal down
        log.exception("failed-push lookup failed")
        return {"failed": [], "count": 0, "unavailable": True}
    return {"failed": rows, "count": len(rows)}


class AmsRetryRequest(BaseModel):
    retried_by: str


@router.post("/api/ams/failed-pushes/{queue_id}/retry")
def retry_failed_push(queue_id: str, req: AmsRetryRequest):
    """Re-drive one failed push from its own queue row. Safe to repeat — both AMS
    endpoints upsert on DatabaseId."""
    from hermes.ams import writeback

    supa = _get_supa()
    _require_users(supa, [("retried_by", req.retried_by)])
    try:
        return writeback.retry(supa, _get_nowcerts(), queue_id=queue_id, actor=req.retried_by)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/clients/{insured_guid}/push-to-ams")
def push_client_to_ams(insured_guid: str, req: AmsPushRequest):
    """Write a client's corrected fields to NowCerts, keyed on the insured GUID."""
    from hermes.ams.writeback import OBJECT_TYPE_CLIENT

    return _ams_push(OBJECT_TYPE_CLIENT, insured_guid, req)


@router.post("/api/policies/{policy_guid}/push-to-ams")
def push_policy_to_ams(policy_guid: str, req: AmsPushRequest):
    """Write a policy's corrected fields to NowCerts, keyed on the policy GUID."""
    from hermes.ams.writeback import OBJECT_TYPE_POLICY

    return _ams_push(OBJECT_TYPE_POLICY, policy_guid, req)


@router.get("/api/clients")
def list_clients_endpoint(limit: int = 500):
    """Full canonical client book, with any portal corrections applied."""
    supa = _get_supa()
    rows = supa.select(
        "canonical_clients",
        columns="nowcerts_insured_guid,insured_name,client_type,city,state,email,phone,active",
        params={"order": "insured_name.asc"}, limit=limit,
    )
    return {"clients": _apply_client_overrides(supa, rows), "count": len(rows)}


@router.get("/api/clients/{insured_guid}")
def client_360_endpoint(insured_guid: str):
    """Client 360 — the insured's record plus their whole book: policies,
    opportunities, and cases, keyed on the NowCerts insured GUID."""
    from hermes_core import opportunities as opp

    supa = _get_supa()

    def sel(table, cols, params):
        try:
            return supa.select(table, columns=cols, params=params, limit=500)
        except Exception:
            return []

    client = _apply_client_overrides(
        supa, sel("canonical_clients", "*", {"nowcerts_insured_guid": f"eq.{insured_guid}"})
    )
    try:
        policies = ams_book.select_policies(
            supa,
            # renewed_policy is required by _collapse_to_current_terms to group a
            # successor term with its predecessor.
            columns="policy_guid,policy_number,renewed_policy,nowcerts_insured_guid,carrier,"
                    "lines_of_business,status,active,effective_date,expiration_date,"
                    "annualized_premium,premium_amount",
            params={"nowcerts_insured_guid": f"eq.{insured_guid}", "order": "expiration_date.asc"},
            limit=500,
        )
    except Exception:  # noqa: BLE001 — a 360 view degrades rather than 500s
        policies = []
    # Correct first, then collapse: a corrected expiration date is what decides
    # which term is the current one.
    policies, _policy_prior_terms = _collapse_to_current_terms(
        _apply_policy_overrides(supa, policies)
    )
    opportunities = opp.with_projected_close(sel(
        "opportunities",
        "id,line_of_business,opportunity_type,stage,status,premium_estimate,carrier,quote_number,"
        # The date columns the projected close is derived from, plus the x-date —
        # a deal on a client's page is worked from the same dates as one on the board.
        "next_action,expected_close_date,needed_by,effective_date,expiration_date",
        {"insured_id": f"eq.{insured_guid}", "order": "updated_at.desc"},
    ))
    cases = sel(
        "agency_crm_cases", "id,case_number,title,case_type,status,priority,created_at",
        {"insured_database_id": f"eq.{insured_guid}", "order": "created_at.desc"},
    )
    tasks = sel(
        "agency_crm_tasks", "id,title,status,priority,due_at,assigned_to_email,case_id",
        {"insured_database_id": f"eq.{insured_guid}", "order": "due_at.asc"},
    )
    documents = _client_documents_list(client[0] if client else None)
    return {
        "client": client[0] if client else None,
        "policies": policies, "opportunities": opportunities, "cases": cases, "tasks": tasks,
        "documents": documents,
        "editable_fields": sorted(CLIENT_OVERRIDABLE_FIELDS),
        "editable_policy_fields": sorted(POLICY_OVERRIDABLE_FIELDS),
        "counts": {
            "policies": len(policies), "opportunities": len(opportunities),
            "cases": len(cases), "tasks": len(tasks),
            "documents": len(documents),
        },
    }


def _client_documents_list(client_row: dict[str, Any] | None) -> list[dict[str, Any]]:
    """List files in the client's Nextcloud tree (Intake + other categories)."""
    if not client_row:
        return []
    name = client_row.get("insured_name") or client_row.get("commercial_name")
    if not name:
        return []
    try:
        from hermes_integrations.nextcloud_client import NextcloudClient, _sanitize_segment

        nc = NextcloudClient()
        if not nc.is_configured():
            return []
        base = f"Clients/{_sanitize_segment(str(name))}"
        out: list[dict[str, Any]] = []
        for entry in nc.list_dir(base):
            if entry.get("is_dir"):
                for child in nc.list_dir(entry.get("path") or f"{base}/{entry.get('name')}"):
                    if child.get("is_dir"):
                        continue
                    out.append({
                        "name": child.get("name"),
                        "path": child.get("path"),
                        "size": child.get("size"),
                        "uploaded_at": child.get("modified"),
                        "source": entry.get("name"),
                    })
            else:
                out.append({
                    "name": entry.get("name"),
                    "path": entry.get("path"),
                    "size": entry.get("size"),
                    "uploaded_at": entry.get("modified"),
                    "source": "root",
                })
        return out
    except Exception:  # noqa: BLE001
        log.exception("client 360 documents list failed for %s", name)
        return []


class ClientDocumentUploadRequest(BaseModel):
    filename: str
    content_base64: str
    content_type: str = "application/pdf"
    folder: str = "Intake"


@router.post("/api/clients/{insured_guid}/documents")
def upload_client_document(insured_guid: str, req: ClientDocumentUploadRequest, request: Request):
    """Manual upload into the client's Nextcloud folder (PDF-of-record landing zone)."""
    _require_hermes_token(request)
    import base64 as _b64

    from hermes_integrations.nextcloud_client import NextcloudClient, NextcloudError, _sanitize_segment

    rows = _get_supa().select(
        "canonical_clients",
        columns="nowcerts_insured_guid,insured_name",
        params={"nowcerts_insured_guid": f"eq.{insured_guid}"},
        limit=1,
    )
    if not rows:
        raise HTTPException(status_code=404, detail="client not found")
    name = rows[0].get("insured_name") or insured_guid
    try:
        raw = _b64.b64decode(req.content_base64)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="invalid content_base64") from exc
    folder = _sanitize_segment(req.folder or "Intake")
    fname = _sanitize_segment(req.filename or "upload.bin")
    rel = f"Clients/{_sanitize_segment(str(name))}/{folder}/{fname}"
    nc = NextcloudClient()
    try:
        nc.ensure_client_folders(str(name))
        path = nc.put_file(rel, raw, content_type=req.content_type or "application/octet-stream")
    except NextcloudError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True, "path": path, "insured_guid": insured_guid}


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


@router.get("/api/policies")
def list_policies_endpoint(limit: int = 1000, include_history: bool = False):
    """Canonical policy book (read-only mirror), soonest-expiring first.

    By default, renewal-overlap pairs and duplicate imports are collapsed to one
    current term per coverage (see ``_collapse_to_current_terms``) so a renewing
    policy shows once, not as two "active" rows. Pass ``include_history=true`` for
    the raw, uncollapsed book. Each policy is stamped with the account/insured
    name it belongs to (looked up from canonical_clients by NowCerts insured GUID)."""
    supa = _get_supa()
    rows = ams_book.select_policies(
        supa,
        columns="policy_guid,policy_number,renewed_policy,nowcerts_insured_guid,carrier,lines_of_business,status,active,"
                "effective_date,expiration_date,premium_amount,annualized_premium,agency_commission_amount,state",
        params={"order": "expiration_date.asc"}, limit=limit,
    )
    rows = _apply_policy_overrides(supa, rows)
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


# ── Finance (commissions) ────────────────────────────────────────────────────
# The commission surface moved to hermes/routers/finance.py — the first app
# split out under docs/repo-split-plan.md Phase 2. Mounted at app creation.


# ── Carriers + Renewals ──────────────────────────────────────────────────────
# Moved to hermes/routers/carriers.py and hermes/routers/renewals.py
# (docs/repo-split-plan.md, Phase 2). Mounted at app creation.

@router.get("/api/workspace-stats")
def workspace_stats_endpoint():
    """KPI tile counts for the Workspace home."""
    supa = _get_supa()

    def _rows(table, cols, params=None):
        try:
            return supa.select(table, columns=cols, params=params, limit=100000)
        except Exception:
            return []

    # Source matters here in a way it does not for row-level reads: these two
    # numbers are the headline book size and revenue on the Workspace home. On
    # the mirror they run ~30% high (450 vs 340 policies, $1.67M vs $1.27M
    # measured 2026-08-03), and the mirror is what gets served for the first
    # ~minute after every restart while the AMS pull warms. Reporting that as
    # settled fact is how a deploy silently rewrites the agency's book value, so
    # the flag rides along and the UI marks the tiles provisional.
    try:
        policies, book_source = ams_book.select_policies_with_source(
            supa, columns="annualized_premium,premium_amount", limit=100000
        )
    except Exception:  # noqa: BLE001 — a KPI tile degrades rather than 500s
        policies, book_source = [], ams_book.SOURCE_MIRROR
    annualized = sum(
        float(p.get("annualized_premium") or p.get("premium_amount") or 0) for p in policies
    )
    return {
        "clients": len(_rows("canonical_clients", "nowcerts_insured_guid")),
        "policies": len(policies),
        "annualized_premium": round(annualized, 2),
        "book_source": book_source,
        "provisional": book_source != ams_book.SOURCE_LIVE,
        "renewals": len(_rows("project_85_renewals", "id")),
        "pipeline": len(_rows("opportunities", "id", {"status": "eq.open"})),
        "open_cases": len(_rows("agency_crm_cases", "id", {"status": "eq.open"})),
        "open_tasks": len(_rows("agency_crm_tasks", "id", {"status": "neq.completed"})),
        "commissions": len(_rows("commission_ledger", "id")),
    }


@router.get("/api/hermes/sync-health")
def sync_health():
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


@router.get("/api/hermes/book-sync")
def book_sync_health(request: Request, max_pages: int = 50):
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
# Read-only proxy to NowCerts InsuredList. See integrations/nowcerts_client.py.
# ---------------------------------------------------------------------------


@router.get("/api/ams/search-insured")
def ams_search_insured(
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

    from hermes_integrations.nowcerts_client import NowCertsClientError

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


@router.post("/api/documents/save")
def documents_save(req: DocumentSaveRequest):
    """Save a document to the library (Supermemory + index).

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


@router.get("/api/documents/folders")
def documents_folders():
    """Folder tree for Agent OS: one entry per (space, name) with a count."""
    from hermes.documents.store import list_folders

    return {"folders": list_folders(_get_supa())}


@router.get("/api/documents")
def documents_in_folder(space: str, name: str):
    """Documents in one folder. ``space`` is 'client' or 'internal';
    ``name`` is the account (client) or freeform folder (internal)."""
    from hermes.documents.store import list_documents

    if space not in ("client", "internal"):
        raise HTTPException(status_code=400, detail="space must be 'client' or 'internal'")
    return {"documents": list_documents(space=space, name=name, supa=_get_supa())}


@router.get("/api/documents/{doc_id}")
def document_detail(doc_id: str):
    """One document index row (title, preview, supermemory_id, …)."""
    from hermes.documents.store import get_document

    row = get_document(doc_id, _get_supa())
    if row is None:
        raise HTTPException(status_code=404, detail="document not found")
    return row


class NextcloudEnsureFoldersRequest(BaseModel):
    paths: list[str]
    confirm: bool = False


class NextcloudUploadRequest(BaseModel):
    title: str
    account_name: str
    line_type: Literal["commercial", "personal"]
    category: str
    content_base64: str
    content_type: str = "application/pdf"


class DocumentRegistryUploadRequest(BaseModel):
    """Metadata + file for the Nextcloud → Zoho Document_Registry pipeline."""

    account_name: str
    document_type: str
    policy_type: str
    line_of_business: str
    renewal_cycle: str
    file_name: str
    content_base64: str
    carrier: str = ""
    document_name: str = ""
    content_type: str = "application/pdf"
    effective_date: str = ""
    expiration_date: str = ""
    account_id: str = ""
    policy_id: str = ""
    uploaded_by: str = ""
    status: str = "Active"
    write_to_zoho: bool | None = None


_NEXTCLOUD_LINE_ROOTS = {
    "commercial": "Commercial Lines",
    "personal": "Personal Lines",
}
_NEXTCLOUD_CATEGORIES = {
    "commercial": {
        "00 Intake Documents",
        "Policies",
        "Applications & Quotes",
        "Endorsements",
        "Certificates",
        "Claims",
        "Billing",
        "Correspondence",
    },
    "personal": {
        "00 Intake Documents",
        "Auto",
        "Home",
        "Umbrella",
        "Flood",
        "Other Personal",
        "Claims",
        "Billing",
        "Correspondence",
    },
}
_NEXTCLOUD_MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def _validated_nextcloud_path(raw: str) -> str:
    raw_path = (raw or "").strip()
    if raw_path.startswith("/"):
        raise HTTPException(status_code=400, detail=f"invalid Nextcloud path: {raw!r}")
    path = raw_path.strip("/")
    parts = path.split("/")
    if (
        not path
        or "\\" in path
        or "\x00" in path
        or any(part in ("", ".", "..") for part in parts)
    ):
        raise HTTPException(status_code=400, detail=f"invalid Nextcloud path: {raw!r}")
    return path


@router.get("/api/nextcloud/folders")
def nextcloud_list_folder(path: str = ""):
    """List one real Nextcloud WebDAV folder, not the document index."""
    from hermes_integrations.nextcloud_client import NextcloudClient, NextcloudError

    nc = NextcloudClient()
    if not nc.is_configured():
        raise HTTPException(status_code=503, detail="Nextcloud is not configured")
    clean = path.strip().strip("/")
    if clean:
        clean = _validated_nextcloud_path(clean)
    try:
        exists = nc.path_exists(clean)
        entries = nc.list_dir(clean) if exists else []
    except NextcloudError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"path": clean, "exists": exists, "entries": entries}


@router.post("/api/nextcloud/folders/ensure")
def nextcloud_ensure_folders(req: NextcloudEnsureFoldersRequest):
    """Preview or idempotently create exact Nextcloud folder paths."""
    from hermes_integrations.nextcloud_client import NextcloudClient, NextcloudError

    paths = list(dict.fromkeys(_validated_nextcloud_path(p) for p in req.paths))
    if not paths:
        raise HTTPException(status_code=400, detail="at least one path is required")
    if len(paths) > 100:
        raise HTTPException(status_code=400, detail="at most 100 paths per request")
    if not req.confirm:
        return {
            "ok": True,
            "requires_confirmation": True,
            "operation": "ensure_nextcloud_folders",
            "paths": paths,
        }

    nc = NextcloudClient()
    if not nc.is_configured():
        raise HTTPException(status_code=503, detail="Nextcloud is not configured")
    results = []
    try:
        for path in paths:
            existed = nc.path_exists(path)
            nc.ensure_dirs(path)
            verified = nc.path_exists(path)
            results.append({
                "path": path,
                "status": "existing" if existed else "created",
                "verified": verified,
            })
    except NextcloudError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    if not all(item["verified"] for item in results):
        raise HTTPException(status_code=502, detail={"message": "folder read-back failed", "results": results})
    return {"ok": True, "requires_confirmation": False, "results": results}


@router.post("/api/nextcloud/upload")
def nextcloud_upload(req: NextcloudUploadRequest):
    """Upload one approved PDF into the standardized RSG client tree."""
    from hermes_integrations.nextcloud_client import NextcloudClient, NextcloudError

    if not req.title.strip() or not req.title.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="title must be a non-empty .pdf filename")
    if not req.account_name.strip():
        raise HTTPException(status_code=400, detail="account_name is required")
    if req.category not in _NEXTCLOUD_CATEGORIES[req.line_type]:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported {req.line_type} category: {req.category}",
        )
    if req.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="only application/pdf is supported")
    try:
        content = base64.b64decode(req.content_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="content_base64 is invalid")
    if not content.startswith(b"%PDF-"):
        raise HTTPException(status_code=400, detail="content is not a PDF")
    if len(content) > _NEXTCLOUD_MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="PDF exceeds 25 MiB")

    nc = NextcloudClient()
    if not nc.is_configured():
        raise HTTPException(status_code=503, detail="Nextcloud is not configured")
    try:
        result = nc.file_document(
            content=content,
            filename=req.title,
            content_type=req.content_type,
            client=req.account_name,
            category=req.category,
            client_root=_NEXTCLOUD_LINE_ROOTS[req.line_type],
            overwrite=False,
        )
        verified = nc.path_exists(result["path"])
    except NextcloudError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    if not verified:
        raise HTTPException(status_code=502, detail="upload read-back failed")
    return {**result, "verified": True}


@router.post("/api/document-registry/upload")
def document_registry_upload(req: DocumentRegistryUploadRequest):
    """PUT a file into the Agency Documents tree, then write Zoho Document_Registry.

    Folder path is derived from metadata (never typed). The CRM record is
    created only after Nextcloud returns a URL (golden rule). Zoho writes
    still require ``HERMES_WRITE_TO_ZOHO=1`` unless ``write_to_zoho`` is true.
    """
    from hermes.documents.registry import DocumentRegistryError, register_document

    if not req.account_name.strip():
        raise HTTPException(status_code=400, detail="account_name is required")
    if not req.file_name.strip():
        raise HTTPException(status_code=400, detail="file_name is required")
    try:
        content = base64.b64decode(req.content_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="content_base64 is invalid")
    if not content:
        raise HTTPException(status_code=400, detail="file content is required")
    if len(content) > _NEXTCLOUD_MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file exceeds 25 MiB")

    try:
        return register_document(
            content=content,
            file_name=req.file_name,
            account_name=req.account_name,
            document_type=req.document_type,
            policy_type=req.policy_type,
            line_of_business=req.line_of_business,
            renewal_cycle=req.renewal_cycle,
            carrier=req.carrier,
            document_name=req.document_name,
            content_type=req.content_type,
            effective_date=req.effective_date,
            expiration_date=req.expiration_date,
            account_id=req.account_id,
            policy_id=req.policy_id,
            uploaded_by=req.uploaded_by,
            status=req.status or "Active",
            write_to_zoho=req.write_to_zoho,
        )
    except DocumentRegistryError as exc:
        msg = str(exc)
        status = 503 if "not configured" in msg.lower() else 502
        if "required" in msg.lower() or "unknown line" in msg.lower():
            status = 400
        raise HTTPException(status_code=status, detail=msg)


@router.get("/api/document-registry/search")
def document_registry_search(
    account_name: str = "",
    document_type: str = "",
    carrier: str = "",
    policy_type: str = "",
    renewal_cycle: str = "",
    line_of_business: str = "",
    status: str = "",
):
    """Search Zoho Document_Registry by metadata. Returns clickable Nextcloud URLs."""
    from hermes.documents.registry import DocumentRegistryError, search_documents
    from hermes_integrations.zoho_client import ZohoClientError

    try:
        rows = search_documents(
            account_name=account_name,
            document_type=document_type,
            carrier=carrier,
            policy_type=policy_type,
            renewal_cycle=renewal_cycle,
            line_of_business=line_of_business,
            status=status,
        )
    except DocumentRegistryError as exc:
        msg = str(exc)
        code = 400 if "at least one search filter" in msg else 502
        raise HTTPException(status_code=code, detail=msg)
    except ZohoClientError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {"ok": True, "count": len(rows), "records": rows}



@router.post("/agency-fact", response_model=AgencyFactResponse)
def agency_fact(req: AgencyFactRequest):
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








@router.post("/command", response_model=DispatchResponse)
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
        from hermes_core.llm_client import get_client

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


@router.post("/api/hermes/tts")
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



# Every hub route above is now defined; mount them. This goes last so the
# relative order matches what it has always been — the domain routers were
# included near the top of this file, and the hub's own routes were registered
# after them as the module body ran.
app.include_router(router)


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

    from hermes.services import ALL, SERVICES, create_app, current_service
    from hermes_app.role import assert_role_config

    parser = argparse.ArgumentParser(description="Hermes private HTTP API")
    parser.add_argument("--host", default=os.environ.get("HERMES_API_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument(
        "--service",
        default=current_service(),
        choices=[ALL, *sorted(SERVICES)],
        help="Which app this process serves. Defaults to HERMES_SERVICE, or "
             "'all' — the whole API in one process, as it has always run. "
             "Naming one app gives it its own event loop, threadpool and "
             "restart, so it cannot be stalled or taken down by the others.",
    )
    args = parser.parse_args()

    role = assert_role_config(args.service, enforce_credentials=True)
    log.info("HERMES_ROLE=%s HERMES_SERVICE=%s", role, args.service)

    served = create_app(args.service, enforce_credentials=True)
    # Port precedence: an explicit --port always wins. After that a NAMED service
    # uses its registered port, ahead of HERMES_API_PORT.
    #
    # That ordering is the whole point. The services share one .env on the box,
    # and it sets HERMES_API_PORT. Letting the env var outrank the registry made
    # all five services bind the same port — the exact collision the registry
    # exists to prevent, and it only showed up on deploy because nothing in the
    # test suite runs main() against a populated environment.
    #
    # HERMES_API_PORT still applies to the unsplit app, which has no registered
    # port of its own.
    if args.port:
        port = args.port
    elif args.service != ALL:
        port = SERVICES[args.service].port
    else:
        port = int(os.environ.get("HERMES_API_PORT") or 8484)
    log.info("serving %s on %s:%s", args.service, args.host, port)
    uvicorn.run(served, host=args.host, port=port)
    return 0
