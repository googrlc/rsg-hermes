#!/usr/bin/env python3
"""Policy CSV upsert + enrich for EspoCRM (RSG).

Reads NowCerts policy export CSVs, matches against live EspoCRM,
then: creates missing policies, fills blank fields on existing ones,
enriches matched Accounts (blank-fill only), creates missing Accounts.

NEVER overwrites a non-empty value on an existing record - the nightly
NowCerts sync owns those. This script only fills gaps and adds missing rows.

Usage:
  python3 policy_csv_upsert.py            # dry run, writes report, NO writes
  python3 policy_csv_upsert.py --live     # execute against EspoCRM

Stdlib only. API key read from ~/.rsg_espo_key.
"""
import argparse, csv, json, re, sys, time, urllib.request, urllib.error
from datetime import datetime
from pathlib import Path

BASE = "https://rrespocrm-rsg-u69864.vm.elestio.app/api/v1"
KEY_PATH = Path.home() / ".rsg_espo_key"
CSVS = [
    ("/Users/lamarcoates/Desktop/Policy Upload Prep/CommercialUPLOAD_READY.csv", "Commercial Lines"),
    ("/Users/lamarcoates/Desktop/Policy Upload Prep/PersonalUPLOAD_READY.csv", "Personal Lines"),
]
REPORT = Path.home() / "Desktop" / "Policy Upload Prep" / "upsert_report.json"

API_KEY = KEY_PATH.read_text().strip()
HEADERS = {"X-Api-Key": API_KEY, "Content-Type": "application/json"}

BILLING_MAP = {"DB - 100": "Direct Bill 100", "DB": "Direct Bill",
               "AB - 100": "Agency Bill 100", "AB": "Agency Bill",
               "Direct Bill": "Direct Bill", "Agency Bill": "Agency Bill",
               "Direct Bill 100": "Direct Bill 100", "Agency Bill 100": "Agency Bill 100"}
VALID_STATUS = {"Active", "Up for Renewal", "Renewing", "Renewed", "Expired",
                "Cancelled", "Flat Cancel", "Pending Cancel", "Non-Renewed", "Lapsed"}


def money(v):
    if not v: return None
    v = re.sub(r"[$,\s\"]", "", v)
    try:
        f = float(v)
        return f if f != 0 else None
    except ValueError:
        return None


def iso_date(v):
    if not v or not v.strip(): return None
    v = v.strip().split(" ")[0]
    for fmt in ("%m/%d/%y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            d = datetime.strptime(v, fmt)
            if d.year < 1950: d = d.replace(year=d.year + 100)
            return d.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def api(method, path, payload=None, retries=3):
    url = f"{BASE}/{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, headers=HEADERS, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode()[:300]
            if e.code in (429, 500, 502, 503) and attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"{method} {path} -> {e.code}: {body}")
        except urllib.error.URLError as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"{method} {path} -> {e}")


def fetch_all(entity, select):
    out, offset = [], 0
    while True:
        d = api("GET", f"{entity}?maxSize=200&offset={offset}&select={select}")
        out.extend(d.get("list", []))
        if len(out) >= d.get("total", 0) or not d.get("list"): break
        offset += 200
    return out


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def clean_phone(v):
    d = re.sub(r"\D", "", v or "")
    if len(d) == 11 and d.startswith("1"): d = d[1:]
    if len(d) != 10: return None
    return f"+1 {d[0:3]}-{d[3:6]}-{d[6:]}"


def write(method, path, payload):
    """Write with one retry dropping a field Espo rejects on validation."""
    try:
        return api(method, path, payload)
    except RuntimeError as ex:
        m = re.search(r'"field":"(\w+)"', str(ex))
        if m and m.group(1) in payload:
            p2 = {k: v for k, v in payload.items() if k != m.group(1)}
            if p2: return api(method, path, p2)
        raise


def build_policy_payload(row, account_id):
    lob = row.get("Lines Of Business", "").strip()
    num = row.get("Number", "").strip()
    insured = row.get("Insured", "").strip()
    prem = money(row.get("Premium Amount")) or money(row.get("Annualized Premium")) \
        or money(row.get("Current Term Amount")) or money(row.get("Total"))
    status = row.get("Status", "").strip()
    p = {
        "name": f"{insured} | {lob or 'Policy'} | {num}"[:100],
        "policy_number": num[:100] or None,
        "momentumPolicyId": row.get("Id", "").strip() or None,
        "insuredMomentumId": row.get("Insured Id (GUID)", "").strip() or None,
        "carrier": (row.get("Carrier", "").strip() or None),
        "line_of_business": lob or None,
        "business_type": (row.get("Business Type", "").strip() or None),
        "status": status if status in VALID_STATUS else None,
        "billing_type": BILLING_MAP.get(row.get("Billing Type", "").strip()),
        "effective_date": iso_date(row.get("Effective Date")),
        "expiration_date": iso_date(row.get("Expiration Date")),
        "bind_date": iso_date(row.get("Bind Date")),
        "cancellation_date": iso_date(row.get("Cancellation Date")),
        "premium_amount": prem,
        "agency_fee": money(row.get("Agency Fees Amount")),
        "commissionAmount": money(row.get("Agency Commission Amount")),
        "cancellation_reason": (row.get("Reason For Cancellation", "").strip() or None),
    }
    if account_id: p["accountId"] = account_id
    return {k: v for k, v in p.items() if v is not None}


def build_account_enrich(row, existing):
    """Fill ONLY blank fields on an existing account."""
    e = {}
    email = row.get("Insured E-mail address", "").strip()
    phone = clean_phone(row.get("Insured Phone", "").strip()
                        or row.get("Insured Cell Phone", "").strip())
    fn = (row.get("Primary Contact First Name", "").strip()
          or row.get("Insured First Name", "").strip()
          or row.get("Personal Insured First Name", "").strip())
    ln = (row.get("Primary Contact Last Name", "").strip()
          or row.get("Insured Last Name", "").strip()
          or row.get("Personal Insured Last Name", "").strip())
    if email and not existing.get("emailAddress"): e["emailAddress"] = email
    if phone and not existing.get("phoneNumber"): e["phoneNumber"] = phone
    if fn and not existing.get("primaryFirstName"): e["primaryFirstName"] = fn[:100]
    if ln and not existing.get("primaryLastName"): e["primaryLastName"] = ln[:100]
    if not existing.get("billingAddressStreet"):
        st = row.get("Insured Address Line 1", "").strip()
        st2 = row.get("Insured Address Line 2", "").strip()
        if st:
            e["billingAddressStreet"] = (st + ("\n" + st2 if st2 else ""))[:255]
            e["billingAddressCity"] = row.get("Insured City", "").strip()[:100] or None
            e["billingAddressState"] = row.get("Insured State", "").strip()[:100] or None
            e["billingAddressPostalCode"] = row.get("Insured ZIP", "").strip()[:40] or None
            e["billingAddressCountry"] = "USA"
    return {k: v for k, v in e.items() if v}


def build_account_create(row, acct_type):
    a = {"name": row.get("Insured", "").strip()[:249],
         "account_type": acct_type,
         "account_status": "Active"}
    a.update(build_account_enrich(row, {}))
    guid = row.get("Insured Id (GUID)", "").strip()
    if guid: a["momentum_client_id"] = guid
    return a


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="execute writes")
    args = ap.parse_args()
    live = args.live
    print(f"MODE: {'LIVE' if live else 'DRY RUN'}")

    print("Fetching existing policies...")
    pols = fetch_all("Policy", "momentumPolicyId,policy_number,name,accountId,status,"
                     "premium_amount,effective_date,expiration_date,carrier,"
                     "line_of_business,business_type,billing_type,commissionAmount,"
                     "agency_fee,insuredMomentumId,bind_date,cancellation_date")
    print(f"  {len(pols)} policies in EspoCRM")
    by_mid = {p["momentumPolicyId"]: p for p in pols if p.get("momentumPolicyId")}
    by_num = {}
    for p in pols:
        k = (norm(p.get("policy_number")), p.get("effective_date"))
        if k[0]: by_num.setdefault(k, p)

    print("Fetching existing accounts...")
    accts = fetch_all("Account", "name,momentum_client_id,emailAddress,phoneNumber,"
                      "primaryFirstName,primaryLastName,billingAddressStreet,account_type")
    print(f"  {len(accts)} accounts in EspoCRM")
    a_by_guid = {a["momentum_client_id"]: a for a in accts if a.get("momentum_client_id")}
    a_by_name = {}
    for a in accts: a_by_name.setdefault(norm(a.get("name")), a)

    report = {"mode": "LIVE" if live else "DRY", "ts": datetime.now().isoformat(),
              "policy_create": [], "policy_fill": [], "policy_skip": 0,
              "account_create": [], "account_enrich": [], "anomalies": [], "errors": []}
    new_acct_cache = {}

    for path, acct_type in CSVS:
        with open(path, encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
        print(f"\nProcessing {Path(path).name}: {len(rows)} rows ({acct_type})")
        for i, row in enumerate(rows):
            insured = row.get("Insured", "").strip()
            mid = row.get("Id", "").strip()
            guid = row.get("Insured Id (GUID)", "").strip()
            num = row.get("Number", "").strip()
            if not insured:
                report["anomalies"].append(f"row {i+2} {Path(path).name}: no insured name")
                continue

            # --- resolve account ---
            acct = a_by_guid.get(guid) or a_by_name.get(norm(insured)) \
                or new_acct_cache.get(norm(insured))
            if acct is None:
                payload = build_account_create(row, acct_type)
                report["account_create"].append(payload["name"])
                if live:
                    try:
                        created = write("POST", "Account", payload)
                        time.sleep(0.15)
                    except RuntimeError as ex:
                        if "-> 409" in str(ex):
                            m = re.search(r'"id":"(\w+)","name":"([^"]*)"', str(ex))
                            if m:
                                created = {"id": m.group(1), "name": m.group(2)}
                                report.setdefault("dup_matches", []).append(
                                    f"CSV '{insured}' = existing account '{m.group(2)}' ({m.group(1)})")
                            else:
                                report["errors"].append(f"Account 409 unparsed {insured}: {ex}")
                                created = None
                        else:
                            report["errors"].append(f"Account create {insured}: {ex}")
                            created = None
                else:
                    created = {"id": f"DRY-{len(new_acct_cache)}", **payload}
                if created:
                    new_acct_cache[norm(insured)] = created
                    if guid: a_by_guid[guid] = created
                    acct = created
            else:
                enrich = build_account_enrich(row, acct)
                if enrich and acct.get("id", "").startswith("DRY-") is False:
                    if acct["id"] not in [x["id"] for x in report["account_enrich"]]:
                        report["account_enrich"].append(
                            {"id": acct["id"], "name": acct.get("name"), "fields": list(enrich)})
                        if live:
                            try:
                                write("PUT", f"Account/{acct['id']}", enrich)
                                acct.update(enrich)
                                time.sleep(0.15)
                            except RuntimeError as ex:
                                report["errors"].append(f"Account enrich {insured}: {ex}")

            acct_id = acct.get("id") if acct else None
            if acct_id and acct_id.startswith("DRY-"): acct_id = None

            # --- resolve policy ---
            numkey = (norm(num), iso_date(row.get("Effective Date")))
            existing = by_mid.get(mid) if mid else None
            if existing is None:
                existing = by_num.get(numkey)
            payload = build_policy_payload(row, acct_id)
            if "E+" in num.upper():
                report["anomalies"].append(
                    f"Excel-mangled policy number '{num}' ({insured}) - fix in source")

            if existing is None:
                report["policy_create"].append(
                    {"name": payload.get("name"), "mid": mid, "premium": payload.get("premium_amount")})
                new_rec = dict(payload)
                new_rec["id"] = "PEND"
                if live:
                    try:
                        created_pol = write("POST", "Policy", payload)
                        new_rec["id"] = created_pol.get("id", "PEND")
                        time.sleep(0.15)
                    except RuntimeError as ex:
                        report["errors"].append(f"Policy create {num}: {ex}")
                if mid: by_mid[mid] = new_rec
                if numkey[0]: by_num.setdefault(numkey, new_rec)
            else:
                fill = {k: v for k, v in payload.items()
                        if k not in ("name", "status") and not existing.get(k)}
                if not existing.get("accountId") and acct_id:
                    fill["accountId"] = acct_id
                if fill and existing["id"] != "PEND":
                    report["policy_fill"].append(
                        {"id": existing["id"], "name": existing.get("name"), "fields": list(fill)})
                    if live:
                        try:
                            write("PUT", f"Policy/{existing['id']}", fill)
                            existing.update(fill)
                            time.sleep(0.15)
                        except RuntimeError as ex:
                            report["errors"].append(f"Policy fill {num}: {ex}")
                else:
                    report["policy_skip"] += 1

    # dedupe account creates (same insured across many rows)
    report["account_create"] = sorted(set(report["account_create"]))

    REPORT.write_text(json.dumps(report, indent=2))
    print("\n========== SUMMARY ==========")
    print(f"Policies to CREATE : {len(report['policy_create'])}")
    print(f"Policies to FILL   : {len(report['policy_fill'])}")
    print(f"Policies unchanged : {report['policy_skip']}")
    print(f"Accounts to CREATE : {len(report['account_create'])}")
    print(f"Accounts to ENRICH : {len(report['account_enrich'])}")
    print(f"Anomalies          : {len(report['anomalies'])}")
    print(f"Errors             : {len(report['errors'])}")
    print(f"Report -> {REPORT}")
    if report["errors"][:5]:
        print("First errors:", *report["errors"][:5], sep="\n  ")
    if not live:
        print("\nDRY RUN ONLY - nothing written. Re-run with --live to execute.")


if __name__ == "__main__":
    main()
