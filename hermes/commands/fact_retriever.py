"""Agency-memory query handler — answers "what is X's Y?" with a citation.

Runtime executor for the `crm-fact-retriever` skill. Resolves questions
against this strict hierarchy:

  1. Canonical book        (Supabase canonical_clients / canonical_policies)
  2. client_facts          (Supabase retrieval table)
  3. client_notes          (summary/full_text)
  4. client_documents      (summary)
  5. quote_facts           (per-quote financial detail)
  6. policy_facts          (per-policy detail)

Stops at the first confident answer. Never invents data.
Every answer includes source + confidence.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from hermes.commands.renewal_worksheet import escape_ilike
from hermes.core.dispatcher import DispatchResult
from hermes.integrations import retrieval_client

if TYPE_CHECKING:
    from hermes.integrations.supabase_client import SupabaseClient

log = logging.getLogger(__name__)


# Map natural-language phrases (lower-cased) to canonical fact labels.
# Order matters when there's overlap — first match wins.
FACT_LABEL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bein\b|\bfein\b|federal\s+(employer|tax)\s+id"), "EIN"),
    (re.compile(r"\bdob\b|date\s+of\s+birth|birthdate|birthday"), "Date of Birth"),
    (re.compile(r"\bemail\b|email\s+address"), "Email"),
    (re.compile(r"\bphone\b|phone\s+number|cell|mobile"), "Phone"),
    (re.compile(r"\baddress\b|street|mailing"), "Address"),
    (re.compile(r"annual\s+revenue|gross\s+revenue|sales\b"), "Annual Revenue"),
    (re.compile(r"\bpayroll\b"), "Estimated Payroll"),
    (re.compile(r"employee\s+count|number\s+of\s+employees|headcount"), "Employee Count"),
    (re.compile(r"\bnaics\b"), "NAICS"),
    (re.compile(r"renewal\s+date|expir(es|ation)\b|x-?date"), "Renewal Date"),
    (re.compile(r"effective\s+date"), "Effective Date"),
    (re.compile(r"current\s+carrier|carrier\b"), "Carrier"),
    (re.compile(r"premium\b"), "Premium"),
    (re.compile(r"principal\b|owner\b|sole\s+member"), "Principal"),
    (re.compile(r"spouse\b"), "Spouse"),
    (re.compile(r"decision\s+maker|decision-maker|hr\s+contact"), "Decision Maker"),
    (re.compile(r"quote\s+number|quote\s+#"), "Quote Number"),
    (re.compile(r"policy\s+number|policy\s+#"), "Policy Number"),
]


# CRM canonical-field lookup map: fact_label → (entity, espo_field).
# Honors the canonical convention from hermes-training/espocrm/field_dictionary.md.
# Facts the canonical NowCerts book answers directly, as
# label -> [(table, column), ...] in preference order. Anything absent here
# (EIN, revenue, payroll, NAICS, date of birth) has no canonical column and
# falls through to client_facts, which is where intake records it.
CANONICAL_BOOK_FIELDS: dict[str, list[tuple[str, str]]] = {
    "Phone": [("canonical_clients", "phone"), ("canonical_clients", "cell_phone")],
    "Email": [("canonical_clients", "email")],
    "Address": [("canonical_clients", "address_line1")],
    "Effective Date": [("canonical_policies", "effective_date")],
    # "Renewal Date" maps to expiration_date: in insurance, the renewal/x-date IS
    # the policy expiration date (when the policy expires and renews).
    "Renewal Date": [("canonical_policies", "expiration_date")],
}

# The column each canonical table matches an entity name against.
_CANONICAL_NAME_COLUMN: dict[str, str] = {
    "canonical_clients": "insured_name",
    "canonical_policies": "client_name",
}


@dataclass
class FactAnswer:
    found: bool
    entity: str
    fact_label: str
    fact_value: str | None
    source: str
    confidence: str = "high"
    sensitivity: str = "standard"
    notes: str | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)
        # Populated when multiple plausible entities exist (e.g. two "JB Noble").

    def render(self) -> str:
        if not self.found:
            return (
                f"I do not have {self.entity}'s {self.fact_label} in the CRM "
                "canonical field, client_facts, structured notes, or indexed "
                "documents.\n"
                "Want me to open an intake to capture it?"
            )
        sensitivity_tag = (
            "  (RESTRICTED — handle accordingly)"
            if self.sensitivity == "restricted"
            else ""
        )
        return (
            f"{self.entity}'s {self.fact_label} is {self.fact_value}.\n"
            f"Source: {self.source}\n"
            f"Confidence: {self.confidence}{sensitivity_tag}"
        )


_QUESTION_RE = re.compile(
    r"^\s*(?:what\s+is|whats|what's|who\s+is|whos|who's|find|lookup|tell\s+me)\s+(.*?)(?:\?|$)",
    re.I,
)
_POSSESSIVE_RE = re.compile(r"^(.*?)['']s\s+(.+)$")
_FOR_RE = re.compile(r"^(.+?)\s+for\s+(.+)$", re.I)


def parse_question(text: str) -> tuple[str, str] | None:
    """Extract (entity_name, fact_label) from a natural-language question.

    Supported shapes:
      "What is JB Noble's EIN?"
      "What is Joseph Washington's phone number?"
      "Who is the principal for 3D Pumps LLC?"
      "Phone for Jarod Mattison"
      "Find EIN for 3D Pumps"
    """
    raw = (text or "").strip().rstrip(".?!")
    if not raw:
        return None

    match = _QUESTION_RE.match(raw)
    body = match.group(1).strip() if match else raw

    # "JB Noble's EIN"  → entity="JB Noble", fact="EIN"
    poss = _POSSESSIVE_RE.match(body)
    if poss:
        entity = poss.group(1).strip()
        rest = poss.group(2).strip()
        label = _classify_fact_label(rest)
        if entity and label:
            return entity, label

    # "EIN for 3D Pumps"  → entity="3D Pumps", fact="EIN"
    for_match = _FOR_RE.match(body)
    if for_match:
        left = for_match.group(1).strip()
        right = for_match.group(2).strip()
        label = _classify_fact_label(left)
        if label and right:
            return right, label

    # Fallback: scan the whole body for any known label, treat remainder as entity.
    label = _classify_fact_label(body)
    if label:
        leftover = _FACT_LABEL_WORDS_RE.sub("", body, count=1).strip()
        leftover = re.sub(r"\b(the|of|for|a|an)\b", "", leftover, flags=re.I).strip()
        if leftover:
            return leftover, label

    return None


def _classify_fact_label(text: str) -> str | None:
    """Match a free-text phrase to a canonical fact_label."""
    if not text:
        return None
    low = text.lower()
    for pattern, label in FACT_LABEL_PATTERNS:
        if pattern.search(low):
            return label
    return None


_FACT_LABEL_WORDS_RE = re.compile(
    r"\b(ein|fein|federal\s+(employer|tax)\s+id|dob|date\s+of\s+birth|birthdate|birthday|"
    r"email(\s+address)?|phone(\s+number)?|cell|mobile|address|street|mailing|"
    r"annual\s+revenue|gross\s+revenue|sales|payroll|employee\s+count|"
    r"number\s+of\s+employees|headcount|naics|renewal\s+date|expir(es|ation)|x-?date|"
    r"effective\s+date|current\s+carrier|carrier|premium|principal|owner|"
    r"sole\s+member|spouse|decision\s+maker|hr\s+contact|quote\s+(number|#)|"
    r"policy\s+(number|#))\b",
    re.I,
)


def _try_canonical_book(
    supa: "SupabaseClient",
    entity_name: str,
    fact_label: str,
) -> tuple[FactAnswer | None, list[dict[str, Any]]]:
    """Look the answer up in the canonical NowCerts book.

    Returns (answer_or_none, candidates_inspected).
    """
    targets = CANONICAL_BOOK_FIELDS.get(fact_label)
    if not targets:
        return None, []

    candidates: list[dict[str, Any]] = []
    for table, column in targets:
        name_column = _CANONICAL_NAME_COLUMN[table]
        rows = supa.select(
            table,
            columns=f"{name_column},{column}",
            params={name_column: f"ilike.*{escape_ilike(entity_name)}*"},
            limit=5,
        )
        for row in rows:
            value = row.get(column)
            candidates.append({
                "table": table,
                "name": row.get(name_column),
                "field": column,
                "value": value,
            })
            if value not in (None, ""):
                return (
                    FactAnswer(
                        found=True,
                        entity=row.get(name_column) or entity_name,
                        fact_label=fact_label,
                        fact_value=str(value),
                        source=f"canonical book {table}.{column}",
                        confidence="high",
                        sensitivity="standard",
                    ),
                    candidates,
                )
    return None, candidates


def _try_client_facts(
    supa: "SupabaseClient",
    entity_name: str,
    fact_label: str,
    *,
    include_restricted: bool = True,
) -> FactAnswer | None:
    """Look up the answer in client_facts (preferring active rows)."""
    entities = retrieval_client.search_entities(supa, name=entity_name, limit=5)
    if not entities:
        return None

    candidates: list[dict[str, Any]] = []
    for ent in entities:
        rows = retrieval_client.search_facts(
            supa,
            entity_id=str(ent.get("id")),
            fact_label=fact_label,
            include_restricted=include_restricted,
            limit=1,
        )
        candidates.append({"entity": ent.get("entity_name"), "rows": len(rows)})
        if rows:
            row = rows[0]
            return FactAnswer(
                found=True,
                entity=ent.get("entity_name") or entity_name,
                fact_label=fact_label,
                fact_value=str(row.get("fact_value")),
                source=_format_fact_source(row),
                confidence=row.get("confidence") or "high",
                sensitivity=row.get("sensitivity") or "standard",
            )

    if len(entities) > 1:
        return FactAnswer(
            found=False,
            entity=entity_name,
            fact_label=fact_label,
            fact_value=None,
            source="ambiguous",
            confidence="low",
            notes=f"Found {len(entities)} possible entities matching {entity_name!r}.",
            candidates=[{"entity_name": e.get("entity_name"), "id": e.get("id")} for e in entities],
        )
    return None


def _format_fact_source(row: dict[str, Any]) -> str:
    source = row.get("source") or "client_facts"
    source_date = row.get("source_date")
    if source_date:
        return f"client_facts ({source}, {source_date})"
    return f"client_facts ({source})"


def _try_quote_facts(
    supa: "SupabaseClient", entity_name: str, fact_label: str
) -> FactAnswer | None:
    """Answer quote-specific questions from quote_facts."""
    if fact_label not in {"Quote Number", "Premium", "Carrier", "Effective Date"}:
        return None
    entities = retrieval_client.search_entities(supa, name=entity_name, limit=3)
    if not entities:
        return None
    for ent in entities:
        rows = supa.select(
            "quote_facts",
            params={"entity_id": f"eq.{ent.get('id')}", "order": "created_at.desc"},
            limit=10,
        )
        if not rows:
            continue
        if fact_label == "Quote Number":
            quotes = ", ".join(
                f"{r.get('quote_number')} ({r.get('line_of_business')}, "
                f"{r.get('carrier') or 'no carrier'})"
                for r in rows
            )
            return FactAnswer(
                found=True,
                entity=ent.get("entity_name") or entity_name,
                fact_label=fact_label,
                fact_value=quotes,
                source=f"quote_facts ({len(rows)} rows)",
                confidence="high",
            )
        if fact_label == "Premium":
            row = rows[0]
            return FactAnswer(
                found=True,
                entity=ent.get("entity_name") or entity_name,
                fact_label=f"Premium ({row.get('line_of_business')})",
                fact_value=f"${row.get('premium')} (carrier: {row.get('carrier') or '—'}; quote #{row.get('quote_number')})",
                source="quote_facts",
                confidence="high",
            )
    return None


def _try_policy_facts(
    supa: "SupabaseClient", entity_name: str, fact_label: str
) -> FactAnswer | None:
    if fact_label not in {"Renewal Date", "Policy Number", "Carrier", "Premium"}:
        return None
    entities = retrieval_client.search_entities(supa, name=entity_name, limit=3)
    if not entities:
        return None
    for ent in entities:
        rows = supa.select(
            "policy_facts",
            params={"entity_id": f"eq.{ent.get('id')}", "order": "expiration_date.desc"},
            limit=10,
        )
        if not rows:
            continue
        if fact_label == "Renewal Date":
            value = ", ".join(
                f"{r.get('line_of_business')} {r.get('expiration_date')} ({r.get('carrier')})"
                for r in rows if r.get("expiration_date")
            )
            if value:
                return FactAnswer(
                    found=True,
                    entity=ent.get("entity_name") or entity_name,
                    fact_label=fact_label,
                    fact_value=value,
                    source="policy_facts",
                    confidence="high",
                )
        if fact_label == "Policy Number":
            policies = ", ".join(
                f"{r.get('policy_number')} ({r.get('line_of_business')}, {r.get('carrier')})"
                for r in rows
            )
            return FactAnswer(
                found=True,
                entity=ent.get("entity_name") or entity_name,
                fact_label=fact_label,
                fact_value=policies,
                source="policy_facts",
                confidence="high",
            )
    return None


def retrieve(
    supa: "SupabaseClient | None",
    *,
    entity_name: str,
    fact_label: str,
    include_restricted: bool = True,
) -> FactAnswer:
    """Run the retrieval cascade. Returns a FactAnswer (found=True or False)."""
    inspected_candidates: list[dict[str, Any]] = []

    if supa is not None:
        # 1. canonical book
        try:
            answer, candidates = _try_canonical_book(supa, entity_name, fact_label)
            inspected_candidates.extend(candidates)
            if answer:
                return answer
        except Exception:
            log.exception("canonical book lookup failed for %s / %s", entity_name, fact_label)

        # 2. client_facts
        try:
            answer = _try_client_facts(
                supa, entity_name, fact_label, include_restricted=include_restricted
            )
            if answer:
                return answer
        except Exception:
            log.exception("client_facts lookup failed for %s / %s", entity_name, fact_label)

        # 5. quote_facts
        try:
            answer = _try_quote_facts(supa, entity_name, fact_label)
            if answer:
                return answer
        except Exception:
            log.exception("quote_facts lookup failed for %s / %s", entity_name, fact_label)

        # 6. policy_facts
        try:
            answer = _try_policy_facts(supa, entity_name, fact_label)
            if answer:
                return answer
        except Exception:
            log.exception("policy_facts lookup failed for %s / %s", entity_name, fact_label)

    # Steps 3 (client_notes search) + 4 (client_documents search) intentionally
    # skipped at the runtime layer — those require a free-text index that the
    # current schema does not enable. The skill documents them as future work.

    return FactAnswer(
        found=False,
        entity=entity_name,
        fact_label=fact_label,
        fact_value=None,
        source="not_found",
        confidence="low",
        candidates=inspected_candidates,
    )


def handle(
    text: str,
    *,
    supa: "SupabaseClient | None" = None,
    include_restricted: bool = True,
) -> DispatchResult:
    """Dispatcher entry point.

    Returns a DispatchResult so this skill can be wired into Hermes'
    natural-language router alongside `lookup`, `intake`, etc.
    """
    parsed = parse_question(text)
    if not parsed:
        return DispatchResult(
            False,
            "I couldn't tell what fact to look up. Try: \"what is "
            "<entity>'s EIN/phone/email/address/renewal date?\"",
        )
    entity_name, fact_label = parsed
    answer = retrieve(
        supa,
        entity_name=entity_name,
        fact_label=fact_label,
        include_restricted=include_restricted,
    )
    return DispatchResult(
        ok=answer.found,
        message=answer.render(),
        data={
            "entity": answer.entity,
            "fact_label": answer.fact_label,
            "fact_value": answer.fact_value,
            "source": answer.source,
            "confidence": answer.confidence,
            "sensitivity": answer.sensitivity,
            "found": answer.found,
            "candidates": answer.candidates,
            "notes": answer.notes,
        },
    )
