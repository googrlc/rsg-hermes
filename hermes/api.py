"""Small private HTTP bridge for calling Hermes from tools like Open WebUI."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from dotenv import load_dotenv

from hermes.core.client import EspoClient, EspoClientError
from hermes.core.dispatcher import Dispatcher

log = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
MAX_BODY_BYTES = 64 * 1024

_WRITE_HINT = re.compile(
    r"^\s*(?:add|create|update|move\s+opportunit(?:y|ie)|intake|new\s+lead|log\s+lead|met|talked|spoke|just\s+met)\b"
    r"|^\s*(?:research|enrich|investigate|look\s+up|web\s+research)\b.*\b(?:save|write|update|put|log|store)\b",
    re.I,
)


def requires_confirmation(command: str) -> bool:
    """Return true when a Hermes command may write to CRM or another system."""
    return bool(_WRITE_HINT.search(command.strip()))


def openapi_schema() -> dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "RSG Hermes API",
            "version": "0.1.0",
            "description": "Private Tailnet API for asking Hermes to read CRM data and run confirmed RSG workflows.",
        },
        "paths": {
            "/health": {
                "get": {
                    "operationId": "hermes_health",
                    "summary": "Check Hermes API readiness",
                    "responses": {"200": {"description": "Hermes API is reachable"}},
                }
            },
            "/command": {
                "post": {
                    "operationId": "hermes_command",
                    "summary": "Run a Hermes CRM command",
                    "description": (
                        "Runs a Hermes natural-language command. Commands that may write CRM data require confirm=true."
                    ),
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["command"],
                                    "properties": {
                                        "command": {
                                            "type": "string",
                                            "description": "Hermes command, e.g. find Acme, renewal audit, create Task name=\"Call\" status=Inbox.",
                                        },
                                        "confirm": {
                                            "type": "boolean",
                                            "default": False,
                                            "description": "Set true only after the user explicitly confirms a write action.",
                                        },
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {"description": "Command ran"},
                        "400": {"description": "Bad request"},
                        "409": {"description": "Confirmation required for possible write action"},
                    },
                }
            },
        },
    }


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _auth_ok(headers: Any, token: str) -> bool:
    if not token:
        return True
    auth = str(headers.get("Authorization", ""))
    return auth == f"Bearer {token}"


class HermesApiHandler(BaseHTTPRequestHandler):
    server_version = "HermesApi/0.1"

    @property
    def api_token(self) -> str:
        return getattr(self.server, "api_token", "")

    @property
    def dispatcher(self) -> Dispatcher:
        return getattr(self.server, "dispatcher")

    @property
    def espo(self) -> EspoClient:
        return getattr(self.server, "espo")

    def log_message(self, fmt: str, *args: Any) -> None:
        log.info("%s - %s", self.address_string(), fmt % args)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _require_auth(self) -> bool:
        if _auth_ok(self.headers, self.api_token):
            return True
        self._send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "Unauthorized."})
        return False

    def do_GET(self) -> None:
        if self.path in {"/health", "/healthz"}:
            self._send_json(HTTPStatus.OK, {"ok": True, "service": "hermes-api"})
            return
        if self.path == "/openapi.json":
            self._send_json(HTTPStatus.OK, openapi_schema())
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found."})

    def do_POST(self) -> None:
        if self.path != "/command":
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found."})
            return
        if not self._require_auth():
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid Content-Length."})
            return
        if length <= 0 or length > MAX_BODY_BYTES:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Request body size is invalid."})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Request body must be valid JSON."})
            return
        command = str(payload.get("command") or "").strip() if isinstance(payload, dict) else ""
        confirm = bool(payload.get("confirm")) if isinstance(payload, dict) else False
        if not command:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "`command` is required."})
            return
        needs_confirmation = requires_confirmation(command)
        if needs_confirmation and not confirm:
            self._send_json(
                HTTPStatus.CONFLICT,
                {
                    "ok": False,
                    "requires_confirmation": True,
                    "message": "This command may write to CRM. Ask Lamar to confirm, then call again with confirm=true.",
                },
            )
            return
        try:
            result = self.dispatcher.dispatch(self.espo, command)
        except Exception as exc:
            log.exception("Hermes command failed")
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"ok": False, "error": "Hermes command failed.", "detail": str(exc)},
            )
            return
        self._send_json(
            HTTPStatus.OK if result.ok else HTTPStatus.BAD_REQUEST,
            {
                "ok": result.ok,
                "message": result.message,
                "data": result.data or {},
                "requires_confirmation": False,
            },
        )


class HermesApiServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], handler: type[BaseHTTPRequestHandler]) -> None:
        super().__init__(server_address, handler)
        self.api_token = os.environ.get("HERMES_API_TOKEN", "").strip()
        self.espo = EspoClient()
        self.dispatcher = Dispatcher(use_openai=bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("HERMES_OPENAI_API_KEY")))


def main() -> int:
    load_dotenv()
    logging.basicConfig(level=os.environ.get("HERMES_API_LOG_LEVEL", "INFO"))
    parser = argparse.ArgumentParser(description="Hermes private HTTP API")
    parser.add_argument("--host", default=os.environ.get("HERMES_API_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.environ.get("HERMES_API_PORT", DEFAULT_PORT)))
    args = parser.parse_args()
    try:
        server = HermesApiServer((args.host, args.port), HermesApiHandler)
    except EspoClientError as exc:
        print(exc)
        return 2
    print(f"Hermes API listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
