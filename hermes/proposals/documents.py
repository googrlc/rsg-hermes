"""Generate a proposal and file it into the client's Nextcloud Proposals/ folder.

Ties store + generator + Nextcloud together: render the HTML (always), optionally
render a PDF, file whichever the caller asked for into ``Clients/{client}/Proposals/``
(idempotent folder provisioning, reusing the quotes' filer), and stamp the URLs
back onto the proposal row.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from hermes.integrations.nextcloud_client import NextcloudClient
from hermes.proposals import generator, store

if TYPE_CHECKING:
    from hermes.integrations.supabase_client import SupabaseClient

PROPOSALS_CATEGORY = "Proposals"


def _sanitize(part: str) -> str:
    return "".join(c if (c.isalnum() or c in "-_") else "-" for c in (part or "").strip()) or "x"


def build_filename(proposal: dict[str, Any], ext: str) -> str:
    client = _sanitize(str(proposal.get("insured_name") or "Client"))
    ptype = _sanitize(str(proposal.get("proposal_type") or "Proposal"))
    tag = _sanitize(str(proposal.get("created_at") or proposal.get("id") or "")[:10])
    stem = f"Proposal_{client}_{ptype}"
    if tag:
        stem = f"{stem}_{tag}"
    return f"{stem}.{ext}"


def generate_and_file(
    supa: "SupabaseClient",
    proposal: dict[str, Any],
    *,
    fmt: str = "html",
    file_to_nextcloud: bool = True,
) -> dict[str, Any]:
    """Render *proposal* (fmt: 'html' | 'pdf' | 'both') and stamp/file it.

    Always renders and stores the HTML + total on the row. Files copies into
    Nextcloud when configured. Returns ``{"proposal": <row>, "warnings": [...]}``.
    """
    quotes = store.load_quotes(supa, list(proposal.get("quote_ids") or []))
    html_str, total = generator.render_html(proposal, quotes)
    warnings: list[str] = []

    doc_url = doc_path = doc_name = pdf_url = pdf_path = None
    want_pdf = fmt in ("pdf", "both")
    want_html_file = fmt in ("html", "both")

    pdf_bytes = None
    if want_pdf:
        try:
            pdf_bytes = generator.render_pdf(html_str)
        except generator.PdfUnavailable as exc:
            warnings.append(str(exc))

    if file_to_nextcloud:
        client_name = proposal.get("insured_name") or proposal.get("client_identifier") or "Unknown Client"
        try:
            nc = NextcloudClient()
            nc.ensure_client_folders(client_name)  # idempotent
            if want_html_file or not pdf_bytes:
                name = build_filename(proposal, "html")
                filed = nc.file_document(
                    content=html_str.encode("utf-8"), filename=name,
                    content_type="text/html; charset=utf-8",
                    client=client_name, category=PROPOSALS_CATEGORY,
                )
                doc_url, doc_path, doc_name = filed["url"], filed["path"], name
            if pdf_bytes:
                name = build_filename(proposal, "pdf")
                filed = nc.file_document(
                    content=pdf_bytes, filename=name, content_type="application/pdf",
                    client=client_name, category=PROPOSALS_CATEGORY,
                )
                pdf_url, pdf_path = filed["url"], name
        except Exception as exc:  # noqa: BLE001 — never lose the proposal over a filing hiccup
            warnings.append(f"Proposal generated, but filing to Nextcloud failed: {exc}")

    updated = store.update_render(
        supa, str(proposal["id"]),
        content_html=html_str, total_premium=total,
        document_url=doc_url, document_path=doc_path, document_filename=doc_name,
        pdf_url=pdf_url, pdf_path=pdf_path,
    )
    return {"proposal": updated or proposal, "warnings": warnings, "html": html_str, "total": total}
