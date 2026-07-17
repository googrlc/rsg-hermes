"""Render a standard proposal from selected carrier quotes.

Groups the included ``opportunity_quotes`` rows by line of business, shows the
carrier option(s) under each line, and rolls up a package total (the lowest
premium per line). Works for a single line or many, commercial or personal.

HTML is always produced. PDF is optional and only works when a renderer
(WeasyPrint) is installed in the image — ``render_pdf`` raises ``PdfUnavailable``
otherwise so callers can fall back to HTML cleanly.
"""
from __future__ import annotations

import html
from collections import OrderedDict
from typing import Any

AGENCY_NAME = "Risk Solutions Group"


class PdfUnavailable(RuntimeError):
    """Raised when a PDF render is requested but no renderer is installed."""


def _esc(v: Any) -> str:
    return html.escape("" if v is None else str(v))


def _money(v: Any) -> str:
    try:
        return "${:,.0f}".format(float(v))
    except (TypeError, ValueError):
        return "—"


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def group_by_lob(quotes: list[dict[str, Any]]) -> "OrderedDict[str, list[dict[str, Any]]]":
    """Group quotes by line of business, preserving first-seen order."""
    groups: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for q in quotes:
        lob = (q.get("line_of_business") or "Other").strip() or "Other"
        groups.setdefault(lob, []).append(q)
    return groups


def package_total(groups: "OrderedDict[str, list[dict[str, Any]]]") -> float:
    """Lowest-premium option per line, summed = the recommended package total."""
    total = 0.0
    for _lob, qs in groups.items():
        prems = [p for p in (_num(q.get("premium")) for q in qs) if p is not None]
        if prems:
            total += min(prems)
    return round(total, 2)


def render_html(proposal: dict[str, Any], quotes: list[dict[str, Any]]) -> tuple[str, float]:
    """Return ``(html, total_premium)`` for a proposal + its included quotes."""
    groups = group_by_lob(quotes)
    total = package_total(groups)
    insured = _esc(proposal.get("insured_name") or "Client")
    title = _esc(proposal.get("title") or f"{insured} — Insurance Proposal")
    ptype = _esc(proposal.get("proposal_type") or "New Business")
    segment = _esc(proposal.get("segment") or "")
    notes = proposal.get("notes")
    created = _esc(str(proposal.get("created_at") or "")[:10])

    sections = []
    for lob, qs in groups.items():
        # Best (lowest) premium in this line, to flag the recommended option.
        prems = [(_num(q.get("premium")), i) for i, q in enumerate(qs)]
        best_i = min((p for p in prems if p[0] is not None), default=(None, -1))[1]
        rows = []
        for i, q in enumerate(qs):
            rec = ' class="rec"' if i == best_i else ""
            star = " ★" if i == best_i else ""
            rows.append(
                f"<tr{rec}><td>{_esc(q.get('carrier') or '—')}{star}</td>"
                f"<td class='num'>{_money(q.get('premium'))}</td>"
                f"<td>{_esc(q.get('effective_date') or '—')}</td>"
                f"<td>{_esc(q.get('expiration_date') or '—')}</td>"
                f"<td>{_esc(q.get('quote_number') or '—')}</td></tr>"
            )
        sections.append(
            f"<section class='lob'><h2>{_esc(lob)}</h2>"
            f"<table><thead><tr><th>Carrier</th><th class='num'>Premium</th>"
            f"<th>Effective</th><th>Expiration</th><th>Quote #</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></section>"
        )

    notes_block = (
        f"<section class='notes'><h2>Recommendation</h2><p>{_esc(notes).replace(chr(10), '<br>')}</p></section>"
        if notes else ""
    )

    return (
        f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
  :root{{--ink:#1a2230;--soft:#5a6472;--line:#e4e7ec;--brand:#0f4c81;--rec:#eef6ee}}
  *{{box-sizing:border-box}} body{{font:15px/1.55 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:var(--ink);margin:0;background:#fff}}
  .wrap{{max-width:820px;margin:0 auto;padding:44px 40px}}
  .head{{display:flex;justify-content:space-between;align-items:flex-start;border-bottom:3px solid var(--brand);padding-bottom:18px;margin-bottom:26px}}
  .brand{{font-weight:700;font-size:20px;color:var(--brand);letter-spacing:.3px}}
  .eyebrow{{text-transform:uppercase;letter-spacing:.12em;font-size:11px;color:var(--soft);margin-bottom:4px}}
  h1{{font-size:26px;margin:2px 0 6px}} h2{{font-size:17px;margin:26px 0 10px;color:var(--brand)}}
  .meta{{color:var(--soft);font-size:13px}}
  table{{width:100%;border-collapse:collapse;margin-top:6px;font-size:14px}}
  th,td{{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line)}}
  th{{font-size:11.5px;text-transform:uppercase;letter-spacing:.05em;color:var(--soft)}}
  td.num,th.num{{text-align:right;font-variant-numeric:tabular-nums}}
  tr.rec td{{background:var(--rec)}}
  .total{{margin-top:28px;padding:16px 18px;background:#f6f8fb;border:1px solid var(--line);border-radius:8px;display:flex;justify-content:space-between;align-items:center}}
  .total .big{{font-size:24px;font-weight:700;color:var(--brand)}}
  .notes p{{color:var(--ink)}} .foot{{margin-top:34px;padding-top:16px;border-top:1px solid var(--line);color:var(--soft);font-size:12px}}
  @media print{{.wrap{{padding:0}} body{{-webkit-print-color-adjust:exact;print-color-adjust:exact}}}}
</style></head><body><div class="wrap">
  <div class="head"><div><div class="eyebrow">{ptype}{(' · ' + segment) if segment else ''}</div>
    <h1>{title}</h1><div class="meta">Prepared for {insured}{(' · ' + created) if created else ''}</div></div>
    <div class="brand">{_esc(AGENCY_NAME)}</div></div>
  {''.join(sections) or '<p class="meta">No quotes selected.</p>'}
  <div class="total"><div><div class="eyebrow">Recommended package total</div>
    <div class="meta">Lowest option per line{(' · ' + str(len(groups)) + ' line(s)') if groups else ''}</div></div>
    <div class="big">{_money(total)}</div></div>
  {notes_block}
  <div class="foot">★ marks the lowest-premium option in each line. Premiums are as quoted and subject to
    carrier underwriting and final terms. Prepared by {_esc(AGENCY_NAME)}.</div>
</div></body></html>""",
        total,
    )


def render_pdf(html_str: str) -> bytes:
    """Render proposal HTML to PDF bytes. Raises ``PdfUnavailable`` if no renderer."""
    try:
        from weasyprint import HTML  # type: ignore
    except Exception as exc:  # ImportError or native-lib load failure
        raise PdfUnavailable(
            "PDF renderer (WeasyPrint) is not installed in this image — HTML is available; "
            "add weasyprint to enable PDF."
        ) from exc
    return HTML(string=html_str).write_pdf()
