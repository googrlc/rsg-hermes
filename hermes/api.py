"""Hermes REST API — FastAPI wrapper around the Dispatcher."""

from __future__ import annotations

import argparse
import logging
import os
import re
from typing import Any, Literal

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

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


@app.post("/command", response_model=DispatchResponse)
async def command(req: DispatchRequest):
    """Compatibility alias for older clients."""
    return await dispatch(req)


def main() -> int:
    load_dotenv()
    logging.basicConfig(level=os.environ.get("HERMES_API_LOG_LEVEL", "INFO"))
    parser = argparse.ArgumentParser(description="Hermes private HTTP API")
    parser.add_argument("--host", default=os.environ.get("HERMES_API_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("HERMES_API_PORT", "8484")))
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)
    return 0
