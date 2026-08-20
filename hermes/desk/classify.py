"""Keyword classification for new Desk tickets (AUT-01 helper).

This is a deterministic first pass. Uncertain results stay with Service Intake.
"""

from __future__ import annotations

from dataclasses import dataclass

from hermes.desk.spec import AUTO_DRIVER_SUBTYPES, CATEGORIES

_CATEGORY_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Certificate Request", ("certificate of insurance", "certificate", " coi ", "cert holder", "additional insured")),
    ("Claims Assistance", ("claim number", "date of loss", "fnol", "adjuster", "claim")),
    ("Cancellations and Reinstatements", ("cancellation notice", "notice of cancellation", "reinstatement", "nonrenewal", "non-renewal")),
    ("Billing and Payments", ("past due", "amount due", "premium finance", "payment", "invoice", "billing")),
    ("Renewals", ("renewal questionnaire", "expiring", "renewal")),
    ("Policy Documents", ("dec page", "declaration page", "id card", "policy copy", "evidence of insurance")),
    ("New Business Support", ("new business", "submission", "appetite")),
    ("Licensing and Compliance", ("license", "eor", "surplus lines", "compliance")),
)

_SUBTYPE_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Add vehicle", ("add vehicle", "add a vehicle", "new vehicle", "add vin")),
    ("Remove vehicle", ("remove vehicle", "delete vehicle", "drop vehicle")),
    ("Replace vehicle", ("replace vehicle", "swap vehicle", "vehicle replacement")),
    ("Add driver", ("add driver", "add a driver", "new driver")),
    ("Remove driver", ("remove driver", "drop driver", "delete driver")),
    ("Change garaging", ("garaging", "garaged")),
    ("Change use", ("change of use", "vehicle use", "radius")),
)


@dataclass(frozen=True)
class Classification:
    category: str | None
    subtype: str | None
    confidence: str  # high | medium | low
    uncertain: bool
    evidence: tuple[str, ...]


def _haystack(subject: str, body: str) -> str:
    return f" {subject} \n {body} ".lower()


def classify_request(subject: str | None, body: str | None) -> Classification:
    text = _haystack(subject or "", body or "")
    evidence: list[str] = []
    category: str | None = None
    for name, hints in _CATEGORY_HINTS:
        for hint in hints:
            needle = hint if hint.startswith(" ") else hint
            if needle in text:
                category = name
                evidence.append(hint.strip())
                break
        if category:
            break

    subtype: str | None = None
    for name, hints in _SUBTYPE_HINTS:
        for hint in hints:
            if hint in text:
                subtype = name
                evidence.append(hint)
                break
        if subtype:
            break

    if subtype and subtype in AUTO_DRIVER_SUBTYPES:
        category = category or "Policy Change"

    if category is None:
        return Classification(None, subtype, "low", True, tuple(evidence))
    if category not in CATEGORIES:
        return Classification(None, subtype, "low", True, tuple(evidence))
    confidence = "high" if len(evidence) >= 2 or (category and subtype) else "medium"
    return Classification(category, subtype, confidence, False, tuple(evidence))
