"""Generate the renewal worksheet PDF and file it to Nextcloud.

``generate_pdf_handle`` resolves the exact policy, renders the worksheet PDF
(reportlab), and files it to the client's Nextcloud folder
(Clients/{client}/Renewal Reviews/). If a renewal case exists for the policy,
its ``nextcloud_path`` is updated so the workspace links to the filed document.

Both the "generate renewal pdf …" and "file … to nextcloud" verbs route here —
a generated PDF with nowhere to go isn't useful, so the two steps run together.
When Nextcloud isn't configured yet, the PDF is still generated and the caller
is told it wasn't filed (nothing is lost, nothing half-writes).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hermes.commands.renewal_cases import _resolve_or_error
from hermes.core.dispatch import DispatchResult
from hermes.renewals import cases, pdf, resolve

if TYPE_CHECKING:
    from hermes.integrations.nextcloud_client import NextcloudClient
    from hermes.integrations.supabase_client import SupabaseClient
    from hermes.integrations.nowcerts_client import NowCertsClient


def generate_pdf_handle(
    text: str,
    *,
    supa: "SupabaseClient | None" = None,
    nowcerts: "NowCertsClient | None" = None,
    nextcloud: "NextcloudClient | None" = None,
) -> DispatchResult:
    if supa is None:
        return DispatchResult(False, "Generating a renewal PDF needs Supabase (to resolve the policy).")

    resolved = _resolve_or_error(text, supa=supa, nowcerts=nowcerts)
    if isinstance(resolved, DispatchResult):
        return resolved
    p = resolved.policy or {}
    policy_number = p.get("policyNumber")

    try:
        pdf_bytes = pdf.build_renewal_pdf(p)
    except pdf.PdfUnavailableError as exc:
        return DispatchResult(False, str(exc))

    filename = pdf.default_filename(p)
    kb = max(1, len(pdf_bytes) // 1024)

    nc = nextcloud
    if nc is None:
        from hermes.integrations.nextcloud_client import NextcloudClient

        nc = NextcloudClient()

    if not nc.is_configured():
        return DispatchResult(
            True,
            f"📄 Generated the renewal worksheet PDF for policy #{policy_number} (~{kb} KB), "
            "but Nextcloud isn't configured yet (set NEXTCLOUD_URL), so it wasn't filed.",
            {"policy_number": policy_number, "pdf_bytes": len(pdf_bytes), "filed": False},
        )

    client_name = p.get("accountName") or (resolved.candidate or {}).get("client_name") or policy_number
    try:
        res = nc.file_document(
            content=pdf_bytes,
            filename=filename,
            content_type="application/pdf",
            client=str(client_name),
            category="Renewal Reviews",
        )
    except Exception as exc:
        return DispatchResult(False, f"Generated the PDF but filing to Nextcloud failed ({exc}).")

    # Link the filed document to the renewal case (shared agency CRM schema), if one exists.
    linked = False
    try:
        case = cases.get_case_by_policy(supa, str(policy_number)) if policy_number else None
        if case:
            cases.link_document(
                supa,
                case_id=case["id"],
                title=filename,
                nextcloud_path=res["path"],
                nextcloud_url=res.get("url"),
                insured_id=case.get("insured_database_id"),
                content_type="application/pdf",
            )
            # Keep the case's folder pointer current on the shared cases table.
            supa.update(cases.CASES_TABLE, case["id"], {"nextcloud_folder_url": res.get("url") or res["path"]})
            linked = True
    except Exception:
        linked = False

    return DispatchResult(
        True,
        f"📄 Filed renewal worksheet — {client_name} (policy #{policy_number})\n"
        f"- Nextcloud: `{res['path']}`"
        + ("\n- Linked to the renewal case." if linked else ""),
        {"policy_number": policy_number, "filed": True, "path": res["path"], "url": res.get("url"),
         "case_linked": linked},
    )
