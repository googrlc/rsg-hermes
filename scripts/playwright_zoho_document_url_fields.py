#!/usr/bin/env python3
"""Create Zoho Nextcloud URL fields using a logged-in Playwright CRM session.

After you sign into CRM (including CRM Plus at crmplus.zoho.com), this script
calls the Settings API with the browser cookies, CSRF token, and X-CRM-ORG
header. Same payloads as ``ensure_zoho_document_url_fields.py`` — no
``ZohoCRM.settings.ALL`` OAuth scope required.

Usage:
  pip install playwright
  playwright install chromium
  PYTHONPATH=packages/rsg-hermes-core:. \\
    python scripts/playwright_zoho_document_url_fields.py
  PYTHONPATH=packages/rsg-hermes-core:. \\
    python scripts/playwright_zoho_document_url_fields.py --apply
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from hermes_integrations.zoho_document_fields import DOCUMENT_URL_FIELDS
from hermes_integrations.zoho_settings_ensure import (
    crm_csrf_from_cookie_header,
    crm_org_from_url,
    crm_origin_from_url,
    decode_settings_response,
    list_module_api_names,
    process_module,
    settings_url,
)

SETUP_URL = "https://crm.zoho.com/crm/settings/modules"
DEFAULT_STORAGE = Path("state/zoho-playwright.json")


class PlaywrightZohoSettingsClient:
    """Settings API through the CRM origin, using the browser's session cookies."""

    def __init__(self, page: Any, *, api_version: str = "v8") -> None:
        self.page = page
        self.api_version = api_version

    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        origin = crm_origin_from_url(self.page.url)
        org = crm_org_from_url(self.page.url)
        url = settings_url(origin, path, query, version=self.api_version)
        result = self.page.evaluate(
            """async ({ method, url, body, org }) => {
              const csrf = (() => {
                const parts = document.cookie.split(';');
                const wanted = ['crmcsr', 'crmcsrfparam', 'CSRF_TOKEN'];
                const map = {};
                for (const chunk of parts) {
                  const i = chunk.indexOf('=');
                  if (i < 0) continue;
                  map[chunk.slice(0, i).trim()] = decodeURIComponent(chunk.slice(i + 1).trim());
                }
                for (const name of wanted) {
                  if (map[name]) return map[name];
                }
                return '';
              })();
              const headers = { 'Content-Type': 'application/json', 'Accept': 'application/json' };
              if (csrf) headers['X-ZCSRF-TOKEN'] = 'crmcsrfparam=' + csrf;
              if (org) headers['X-CRM-ORG'] = org;
              const res = await fetch(url, {
                method,
                credentials: 'include',
                headers,
                body: body == null ? undefined : JSON.stringify(body),
              });
              return { status: res.status, text: await res.text() };
            }""",
            {"method": method, "url": url, "body": body, "org": org},
        )
        return decode_settings_response(method, url, int(result["status"]), str(result.get("text") or ""))


def _playwright_sync():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit(
            "Playwright is not installed. From the repo root:\n"
            "  source .venv/bin/activate\n"
            "  pip install playwright\n"
            "  playwright install chromium\n"
            "Then re-run this script."
        ) from exc
    return sync_playwright


def maybe_fill_login(page: Any) -> None:
    """Pre-fill Zoho sign-in when ZOHO_EMAIL / ZOHO_PASSWORD are set. Never print them."""
    email = (os.environ.get("ZOHO_EMAIL") or "").strip()
    password = os.environ.get("ZOHO_PASSWORD") or ""
    if not email:
        return
    try:
        box = page.get_by_role("textbox", name="Email address or mobile number")
        if box.count() == 0:
            return
        box.first.fill(email)
        next_btn = page.get_by_role("button", name="Next")
        if next_btn.count():
            next_btn.first.click()
        if password:
            page.get_by_role("textbox", name="Enter password").first.fill(password)
            for label in ("Sign in", "Next"):
                btn = page.get_by_role("button", name=label)
                if btn.count():
                    btn.first.click()
                    break
    except Exception as exc:  # noqa: BLE001 — login UI varies; keep going for manual sign-in
        print(f"Could not pre-fill sign-in ({exc}). Finish login in the browser window.")


def wait_for_crm(page: Any, timeout_ms: int) -> None:
    page.wait_for_url(
        lambda url: bool(url) and "crm.zoho." in url and "/signin" not in url and "accounts.zoho." not in url,
        timeout=timeout_ms,
    )
    crm_origin_from_url(page.url)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Log into Zoho CRM in Playwright, then create Nextcloud URL fields."
    )
    parser.add_argument("--apply", action="store_true", help="Create fields and patch layouts (default is dry-run).")
    parser.add_argument("--module", action="append", dest="modules", help="Limit to module API name(s).")
    parser.add_argument("--headed", action="store_true", default=True, help="Show the browser (default).")
    parser.add_argument("--headless", action="store_true", help="No window (only works with a saved storage state).")
    parser.add_argument("--login-timeout", type=int, default=300, help="Seconds to wait for you to finish sign-in.")
    parser.add_argument(
        "--storage-state",
        default=str(DEFAULT_STORAGE),
        help="Cookie jar to reuse / save after login (gitignored under state/).",
    )
    parser.add_argument("--start-url", default=SETUP_URL, help="CRM page to open first.")
    args = parser.parse_args()

    selected = args.modules or list(DOCUMENT_URL_FIELDS)
    unknown = [m for m in selected if m not in DOCUMENT_URL_FIELDS]
    if unknown:
        print(f"Unknown module(s): {unknown}. Known: {', '.join(DOCUMENT_URL_FIELDS)}", file=sys.stderr)
        return 1

    headed = not args.headless
    storage = Path(args.storage_state)
    sync_playwright = _playwright_sync()

    print("Opening Zoho CRM in Playwright.", flush=True)
    print("If you see Sign in, complete it (including 2FA) in that window.", flush=True)
    if args.apply:
        print("Mode: APPLY — will create missing Website fields and patch layouts.", flush=True)
    else:
        print("Mode: dry-run — will only list missing fields. Pass --apply to create them.", flush=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed)
        context_kwargs: dict[str, Any] = {"viewport": {"width": 1400, "height": 900}}
        if storage.exists():
            context_kwargs["storage_state"] = str(storage)
            print(f"Reusing login from {storage}")
        context = browser.new_context(**context_kwargs)
        page = context.new_page()
        page.goto(args.start_url, wait_until="domcontentloaded")
        maybe_fill_login(page)
        try:
            wait_for_crm(page, args.login_timeout * 1000)
        except Exception:
            print(
                "Still on Zoho sign-in when the timer ran out. Re-run after you can "
                "complete login, or pass --storage-state from a previous headed run.",
                file=sys.stderr,
                flush=True,
            )
            browser.close()
            return 2

        storage.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(storage))
        print(f"Saved CRM session to {storage}")
        print(f"CRM origin: {crm_origin_from_url(page.url)}")

        cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in context.cookies())
        csrf = crm_csrf_from_cookie_header(cookie_header)
        print("CSRF cookie:" + (" present" if csrf else " missing (GET may still work)"))

        client = PlaywrightZohoSettingsClient(page)
        try:
            present = list_module_api_names(client)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            browser.close()
            return 3
        print(f"Modules in this org: {len(present)}")
        for module in selected:
            process_module(client, module, apply=args.apply, present=present)

        context.storage_state(path=str(storage))
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
