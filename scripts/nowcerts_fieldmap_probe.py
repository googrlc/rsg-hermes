#!/usr/bin/env python3
"""Round-trip probe for the NowCerts Insured create-path field map.

Confirms, against the *live* API, the open questions the OpenAPI schema alone
cannot answer:

  * the two `writeback.py` discrepancies — does `Zip` / `PhoneNumber` bind, or
    is the real key `zipCode` / `phone`?
  * the SIC key — `sicCode` vs `sic` vs `SIC`
  * NAICS vs NAIC — does an `naics` key exist on the write body at all?
  * DBA / Website casing
  * `typeOfBusiness` (entity type) — does an integer value round-trip?

Method (see field-maps/nowcerts-create-path-field-map.md §8): send every
candidate spelling at once, each carrying a DISTINCT sentinel value, as a single
Prospect (`type=1`) insured. Unknown keys drop silently, so whichever sentinel
survives a read-back names the winning key.

Safe by default: prints the payload and EXITS. It only touches NowCerts when you
pass --write, and it writes a Prospect (type=1) with a ZZ_FIELDTEST_ name so the
sentinel never pollutes the real insured book. Requires NOWCERTS_USERNAME /
NOWCERTS_PASSWORD in the environment (this is why it must run where the creds
live, not in a headless task session).

    python scripts/nowcerts_fieldmap_probe.py            # dry run, prints payload
    python scripts/nowcerts_fieldmap_probe.py --write    # POST + read back + report
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

# Candidate spellings per open question. Each sentinel value is unique so a
# read-back tells us exactly which key the API honored.
SENTINELS: dict[str, str] = {
    # §3 discrepancies — wrong-name vs canonical
    "zipCode": "30301",
    "Zip": "30302",
    "phone": "1110000001",
    "PhoneNumber": "2220000002",
    # the five formerly-deferred fields
    "dba": "DBA_LOWER",
    "DBA": "DBA_UPPER",
    "website": "https://lower.fieldtest",
    "Website": "https://upper.fieldtest",
    "sicCode": "SICKEY7371",
    "sic": "SICBARE7372",
    "SIC": "SICUPPER7373",
    "naic": "NAICBARE0001",
    "naics": "NAICSKEY0002",
}
# Integer-typed candidate, tested on its own (a sentinel string would be coerced).
TYPE_OF_BUSINESS_PROBE = 1


def build_payload(stamp: str) -> dict[str, object]:
    name = f"ZZ_FIELDTEST_{stamp}"
    payload: dict[str, object] = {
        "commercialName": name,
        "addressLine1": "1 Test Way",
        "city": "Atlanta",
        "state": "GA",
        "type": 1,  # Prospect — keeps sentinels out of the real book
        "typeOfBusiness": TYPE_OF_BUSINESS_PROBE,
    }
    payload.update(SENTINELS)
    return payload


def report(read_back: dict[str, object]) -> None:
    """Match each sentinel value against the read-back record's values."""
    # Flatten read-back values to strings for substring/equality matching.
    read_pairs = {k: ("" if v is None else str(v)) for k, v in read_back.items()}
    print("\n=== RESULT: which sentinel survived, and under which READ key ===")
    for write_key, sentinel in SENTINELS.items():
        hits = [rk for rk, rv in read_pairs.items() if sentinel in rv]
        verdict = f"landed → read key(s): {hits}" if hits else "DROPPED (or reader hides it)"
        print(f"  wrote {write_key:14} = {sentinel:22} {verdict}")
    tob = read_pairs.get("typeOfBusiness")
    print(f"\n  typeOfBusiness probe sent {TYPE_OF_BUSINESS_PROBE!r}; read back = {tob!r}")
    print(
        "\nInterpretation:\n"
        "  * If `Zip`/`PhoneNumber` DROPPED but `zipCode`/`phone` landed → fix\n"
        "    writeback.py CLIENT_FIELD_MAP: zip->zipCode, phone->phone.\n"
        "  * Whichever of sicCode/sic/SIC landed is the SIC write key.\n"
        "  * If `naics` DROPPED and only `naic` landed → NAICS is not writable on\n"
        "    /Insured/Insert; keep it in Supabase/CRM (do NOT map it onto naic).\n"
        "  * A null read-back is ambiguous: confirm against the Momentum UI before\n"
        "    concluding the write failed — the reader may just not surface the field."
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help="actually POST to NowCerts and read back")
    args = ap.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    payload = build_payload(stamp)

    print("=== Probe payload (single Prospect insured, type=1) ===")
    print(json.dumps(payload, indent=2))

    if not args.write:
        print("\nDry run. Re-run with --write to POST and read back.")
        return 0

    try:
        from hermes.sync.nowcerts_client import get_client
    except Exception as exc:  # pragma: no cover - import guard
        print(f"\nCannot import NowCerts client: {exc}", file=sys.stderr)
        return 2

    client = get_client()
    name = payload["commercialName"]
    print(f"\nPOST /api/Insured/Insert  ({name}) ...")
    client.create_insured(payload)

    print(f"Read back via search_insureds({name!r}) ...")
    matches = client.search_insureds(str(name), top=5)
    if not matches:
        print(
            "No record read back. Either the insert was rejected, or the read "
            "endpoint lags — check the Momentum UI for the sentinel record.",
            file=sys.stderr,
        )
        return 1

    record = matches[0]
    print("\n=== raw read-back record ===")
    print(json.dumps(record, indent=2, default=str))
    report(record)
    print(
        f"\nCleanup: the probe left a Prospect named {name}. Delete it in Momentum "
        "if you don't want the sentinel lingering."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
