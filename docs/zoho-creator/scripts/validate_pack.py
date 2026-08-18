#!/usr/bin/env python3
"""Validate Zoho Creator recon pack consistency."""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ERRORS: list[str] = []

VERDICTS = {
    "clean_match",
    "stale_renewal_queue",
    "stale_crm",
    "duplicate_policy",
    "pending_sync",
    "rewrite_detected",
    "status_mismatch",
    "financial_discrepancy",
    "missing_in_crm",
    "missing_in_ams",
    "cancel_reason_gap",
    "lineage_orphan",
}


def read_csv(name: str) -> list[dict[str, str]]:
    with (ROOT / name).open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def err(msg: str) -> None:
    ERRORS.append(msg)


def main() -> int:
    forms = {
        "Policy_Master": read_csv("forms_policy_master.csv"),
        "Policy_Status_History": read_csv("forms_policy_status_history.csv"),
        "Renewal_Queue": read_csv("forms_renewal_queue.csv"),
        "Policy_Audit": read_csv("forms_policy_audit.csv"),
        "Audit_Exceptions": read_csv("forms_audit_exceptions.csv"),
    }
    picklists = read_csv("picklists.csv")
    views = read_csv("views.csv")
    workflows = read_csv("workflows.csv")

    fields_by_form: dict[str, set[str]] = {}
    picklist_keys_used: set[str] = set()
    for form, rows in forms.items():
        names = [r["Deluge_Name"] for r in rows]
        if len(names) != len(set(names)):
            err(f"{form} has duplicate Deluge_Name")
        fields_by_form[form] = set(names)
        if names[0] != "Name":
            err(f"{form} first field should be Name, got {names[0]}")
        for row in rows:
            if row["Form"] != form:
                err(f"{form} csv row Form={row['Form']}")
            key = row.get("Picklist_Key") or ""
            if row["Data_Type"].startswith("Picklist") and not key:
                err(f"{form}.{row['Deluge_Name']} picklist missing Picklist_Key")
            if key:
                picklist_keys_used.add(key)

    pm = fields_by_form["Policy_Master"]
    if len(pm) < 50:
        err(f"Policy_Master field count {len(pm)} < 50")

    required = {
        "Policy_Number",
        "NowCerts_Policy_GUID",
        "Policy_Status",
        "Cancellation_Class",
        "Last_Verdict",
        "Last_Confidence",
        "Approved_To_Push",
        "Rewrite_Of",
        "Premium",
        "CRM_Premium",
    }
    missing = required - pm
    if missing:
        err(f"Policy_Master missing {missing}")

    plist_keys = {r["picklist_key"] for r in picklists}
    for key in picklist_keys_used:
        if key not in plist_keys:
            err(f"Picklist_Key {key} not in picklists.csv")

    verdict_values = {r["value"] for r in picklists if r["picklist_key"] == "verdict"}
    if verdict_values != VERDICTS:
        err(f"verdict picklist mismatch extra={verdict_values-VERDICTS} missing={VERDICTS-verdict_values}")
    if len(verdict_values) != 12:
        err(f"expected 12 verdicts, got {len(verdict_values)}")

    cancel_class = {r["value"] for r in picklists if r["picklist_key"] == "cancellation_class"}
    expected_cc = {"Non Pay", "Rewrite", "Insured Request", "Underwriter", "Other"}
    if cancel_class != expected_cc:
        err(f"cancellation_class mismatch {cancel_class}")

    samples = json.loads((ROOT / "tests" / "sample_records.json").read_text())
    status_vals = {r["value"] for r in picklists if r["picklist_key"] == "policy_status"}
    seen_verdicts = set()
    for rec in samples["policy_master"]:
        ev = rec["expected_verdict"]
        seen_verdicts.add(ev)
        if ev not in VERDICTS:
            err(f"seed {rec['seed_id']} unknown verdict {ev}")
        st = rec.get("Policy_Status") or ""
        if st and st not in status_vals:
            err(f"seed {rec['seed_id']} bad Policy_Status {st}")
        crm_st = rec.get("CRM_Status") or ""
        if crm_st and crm_st not in status_vals:
            err(f"seed {rec['seed_id']} bad CRM_Status {crm_st}")
    if seen_verdicts != VERDICTS:
        err(f"sample seeds missing verdicts {VERDICTS - seen_verdicts}")

    deluge_text = ""
    for path in sorted((ROOT / "deluge").glob("*.dg")):
        deluge_text += path.read_text()
    for verdict in VERDICTS:
        if verdict not in deluge_text:
            err(f"Deluge missing verdict string {verdict}")
    for form in forms:
        if form not in deluge_text and form != "Policy_Status_History":
            # history form is inserted by name
            pass
    if "Policy_Status_History" not in deluge_text:
        err("Deluge missing Policy_Status_History insert")
    if "zoho.crm.createRecord(\"Accounts\"" in deluge_text:
        err("Deluge must not create CRM Accounts")

    spec = (ROOT / "ZIA_AI_BUILD_INSTRUCTIONS.md").read_text()
    for verdict in VERDICTS:
        if f"`{verdict}`" not in spec and verdict not in spec:
            err(f"spec missing verdict {verdict}")

    view_forms = {r["Form"] for r in views}
    if not view_forms <= set(forms):
        err(f"views.csv unknown forms {view_forms - set(forms)}")

    upload = ROOT / "zia-upload"
    for required_upload in (
        "RSG_Policy_Reconciliation_Zia_Pack.json",
        "RSG_Policy_Reconciliation_Build.xlsx",
        "ZIA_UPLOAD.json",
        "ZIA_UPLOAD.xlsx",
        "ZIA_UPLOAD.csv",
        "00_READ_ME_FIRST.csv",
        "forms_all.csv",
        "deluge_scripts.csv",
        "sample_policy_master.csv",
        "picklists.csv",
    ):
        if not (upload / required_upload).exists():
            err(f"zia-upload missing {required_upload}")
    for req_pdf in (
        "ZIA_UPLOAD.pdf",
        "PRD_RSG_Policy_Reconciliation.pdf",
        "BRD_RSG_Policy_Reconciliation.pdf",
        "RFP_RSG_Policy_Reconciliation.pdf",
        "PROCESS_DIAGRAMS_RSG_Policy_Reconciliation.pdf",
    ):
        if not (ROOT / req_pdf).exists():
            err(f"root {req_pdf} missing")
        if not (ROOT / "requirements" / req_pdf).exists():
            err(f"requirements/{req_pdf} missing")

    pack = json.loads((upload / "RSG_Policy_Reconciliation_Zia_Pack.json").read_text())
    if "zia_prompt" not in pack or "deluge" not in pack:
        err("JSON pack missing zia_prompt or deluge")
    if set(pack["deluge"]) != {p.name for p in (ROOT / "deluge").glob("*.dg")}:
        err("JSON pack deluge files drift from deluge/")

    xlsx_size = (upload / "RSG_Policy_Reconciliation_Build.xlsx").stat().st_size
    json_size = (upload / "RSG_Policy_Reconciliation_Zia_Pack.json").stat().st_size
    if json_size > 100 * 1024 * 1024:
        err("JSON pack exceeds 100MB (still ok as csv-only if needed)")
    print(f"Policy_Master fields: {len(pm)}")
    print(f"verdicts: {len(verdict_values)}")
    print(f"seeds: {len(samples['policy_master'])}")
    print(f"xlsx bytes: {xlsx_size}")
    print(f"json bytes: {json_size}")
    if ERRORS:
        print("FAIL")
        for e in ERRORS:
            print(" -", e)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
