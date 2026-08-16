"""SharePoint MCP — HTTP facade (Streamable HTTP at /mcp).

Mirrors the rsg-hermes MCP bridge shape so Copilot Studio and remote agents can
reach SharePoint without a local stdio process. Tool logic lives in tools.py;
this file is transport + optional bearer auth only.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from deploy.sharepoint_mcp.tools import MCP_TOOLS, call_tool

log = logging.getLogger("rsg-sharepoint-mcp")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

AUTH_TOKEN = os.environ.get("API_SERVER_KEY", "").strip()
MCP_PROTOCOL_VERSION = "2024-11-05"
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_NAME = "rsg-sharepoint-mcp"
SERVER_VERSION = "1.0.0"

_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Authorization, Content-Type, Accept, Mcp-Session-Id, MCP-Protocol-Version, Last-Event-ID",
    "Access-Control-Expose-Headers": "Mcp-Session-Id, MCP-Protocol-Version",
    "Access-Control-Max-Age": "86400",
}

app = FastAPI(title="SharePoint MCP", docs_url=None, redoc_url=None)


def _check_auth(request: Request) -> bool:
    if not AUTH_TOKEN:
        return True
    candidates: list[str] = []
    auth = (request.headers.get("authorization") or "").strip()
    if auth:
        parts = auth.split(None, 1)
        if len(parts) == 2 and parts[0].lower() in ("bearer", "token"):
            candidates.append(parts[1].strip())
        else:
            candidates.append(auth)
    for header in ("x-api-key", "x-api-token", "api-key", "x-auth-token"):
        value = (request.headers.get(header) or "").strip()
        if value:
            candidates.append(value)
    return any(hmac.compare_digest(c, AUTH_TOKEN) for c in candidates)


def _json(obj: dict) -> Response:
    return JSONResponse(content=obj, headers=_CORS_HEADERS)


def _unauthorized() -> Response:
    body = {"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": "Unauthorized"}}
    return JSONResponse(content=body, status_code=401, headers=_CORS_HEADERS)


def _wants_sse(request: Request | None) -> bool:
    if request is None:
        return False
    accept = (request.headers.get("accept") or "").lower()
    if "text/event-stream" in accept:
        return True
    if "application/json" in accept:
        return False
    return False


def _respond(rid: Any, payload: dict[str, Any], request: Request | None = None) -> Response:
    body = {"jsonrpc": "2.0", "id": rid, **payload}
    if _wants_sse(request):
        return Response(
            content=f"event: message\r\ndata: {json.dumps(body)}\r\n\r\n",
            media_type="text/event-stream",
            headers=_CORS_HEADERS,
        )
    return _json(body)


def _result(rid: Any, result: dict[str, Any], request: Request | None = None) -> Response:
    return _respond(rid, {"result": result}, request)


def _error(rid: Any, code: int, message: str, request: Request | None = None) -> Response:
    return _respond(rid, {"error": {"code": code, "message": message}}, request)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    site = os.environ.get("SHAREPOINT_SITE_URL", "")
    return {"status": "ok", "site_configured": "yes" if site else "no"}


@app.post("/mcp")
@app.post("/api/mcp")
async def mcp_post(request: Request) -> Response:
    if not _check_auth(request):
        return _unauthorized()
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _error(None, -32700, "Parse error", request)

    method = body.get("method")
    rid = body.get("id")
    params = body.get("params") or {}

    if method == "initialize":
        asked = (params.get("protocolVersion") or "").strip()
        agreed = asked if asked in SUPPORTED_PROTOCOL_VERSIONS else MCP_PROTOCOL_VERSION
        return _result(
            rid,
            {
                "protocolVersion": agreed,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
            request,
        )
    if method in ("notifications/initialized", "initialized"):
        return Response(status_code=202, headers=_CORS_HEADERS)
    if method == "ping":
        return _result(rid, {}, request)
    if method == "tools/list":
        return _result(rid, {"tools": MCP_TOOLS}, request)
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            text = call_tool(str(name), args)
        except KeyError:
            return _result(
                rid,
                {"content": [{"type": "text", "text": f"Unknown tool: {name}"}], "isError": True},
                request,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("tool %s failed", name)
            return _result(
                rid,
                {"content": [{"type": "text", "text": f"Error: {exc}"}], "isError": True},
                request,
            )
        is_error = text.startswith("SharePoint error:") or text.startswith("Error:")
        return _result(
            rid,
            {"content": [{"type": "text", "text": text}], "isError": is_error},
            request,
        )

    return _error(rid, -32601, f"Method not found: {method}", request)


@app.options("/mcp")
@app.options("/api/mcp")
async def mcp_options() -> Response:
    return Response(status_code=204, headers=_CORS_HEADERS)


@app.get("/mcp")
@app.get("/api/mcp")
async def mcp_stream(request: Request) -> Response:
    if not _check_auth(request):
        return _unauthorized()

    async def _keepalive():
        try:
            while True:
                if await request.is_disconnected():
                    return
                yield b": keepalive\r\n\r\n"
                await asyncio.sleep(15)
        except asyncio.CancelledError:
            return

    return StreamingResponse(
        _keepalive(),
        media_type="text/event-stream",
        headers={**_CORS_HEADERS, "Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.delete("/mcp")
@app.delete("/api/mcp")
async def mcp_delete(request: Request) -> Response:
    if not _check_auth(request):
        return _unauthorized()
    return Response(status_code=204, headers=_CORS_HEADERS)
