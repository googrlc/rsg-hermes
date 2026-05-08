"""Hermes REST API — FastAPI wrapper around the Dispatcher."""

from __future__ import annotations

import argparse
import logging
import os
import re
from typing import Any

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

log = logging.getLogger(__name__)

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


class DispatchRequest(BaseModel):
    command: str
    confirm: bool = False


class DispatchResponse(BaseModel):
    ok: bool
    message: str
    data: dict | None = None
    requires_confirmation: bool = False


@app.get("/health")
async def health():
    return {"status": "ok", "service": "hermes"}


@app.post("/dispatch", response_model=DispatchResponse)
async def dispatch(req: DispatchRequest):
    if not req.command.strip():
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
