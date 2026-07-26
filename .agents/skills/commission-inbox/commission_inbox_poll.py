#!/usr/bin/env python3
"""
Deterministic commission-inbox poller (build spec §9). Runs headless on a schedule
(launchd) OR on demand. Uses the Slack Web API + Supabase REST directly — NO MCP,
no LLM in the loop — so it cannot hang and does not depend on interactive auth.

Flow per run:
  read #commission-inbox -> for each attached file: sha256 dedupe -> parse with the
  repo parsers (npx tsx scripts/parse_statement.ts) -> stage a batch + rows via
  Supabase REST -> post a review card. Unknown format -> 'needs_mapping' card.
  NOTHING is committed to commission_transactions — that stays a human approval
  (commit_ingest_batch), unchanged.

Creds (1Password, vault rsg_infrastructure):
  Slack bot token   op://.../HermesGretch_Slack_bot_token/text        (bot: agency_assistant)
  Supabase svc key  op://.../supabase_rsg_infastructure/service_role_key

  --eod   also post the end-of-day summary (files today, committed, still-pending, book totals)
"""
import argparse, hashlib, json, os, subprocess, sys, tempfile, urllib.parse, urllib.request
from datetime import datetime, timezone

CHANNEL = "C0BFXEZL1BP"                                  # #commission-inbox
SUPA_URL = "https://wibscqhkvpijzqbhjphg.supabase.co"
REPO = "/Users/lamarcoates/Documents/GitHub/rsg-commission-tracker"
STATE = os.path.expanduser("~/.hermes/commission_ingest_state.json")


def op(path):
    try:
        return subprocess.run(["op", "read", path], capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:
        return ""


SLACK = op("op://rsg_infrastructure/HermesGretch_Slack_bot_token/text")
SVC = op("op://rsg_infrastructure/supabase_rsg_infastructure/service_role_key")


# ---- tiny HTTP helpers (stdlib only) ---------------------------------------
def _req(url, method="GET", headers=None, data=None):
    r = urllib.request.Request(url, method=method, headers=headers or {}, data=data)
    with urllib.request.urlopen(r, timeout=30) as resp:
        return resp.status, resp.read()


def slack_get(method, params):
    url = f"https://slack.com/api/{method}?" + urllib.parse.urlencode(params)
    _, body = _req(url, headers={"Authorization": f"Bearer {SLACK}"})
    return json.loads(body, strict=False)   # Slack embeds raw newlines in some fields


def slack_post(method, form):
    data = urllib.parse.urlencode(form).encode()
    _, body = _req(f"https://slack.com/api/{method}", "POST",
                   {"Authorization": f"Bearer {SLACK}", "Content-type": "application/x-www-form-urlencoded"}, data)
    return json.loads(body, strict=False)


def slack_download(url_private):
    _, body = _req(url_private, headers={"Authorization": f"Bearer {SLACK}"})
    return body


def supa(method, path, body=None, prefer=None):
    headers = {"apikey": SVC, "Authorization": f"Bearer {SVC}", "Content-Type": "application/json"}
    if prefer:
        headers["Prefer"] = prefer
    data = json.dumps(body).encode() if body is not None else None
    status, raw = _req(f"{SUPA_URL}/rest/v1/{path}", method, headers, data)
    return status, (json.loads(raw) if raw else None)


# ---- state ------------------------------------------------------------------
def load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {}


def save_state(s):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(s, open(STATE, "w"), indent=2)


def money(x):
    return "—" if x is None else f"${float(x):,.2f}"


# ---- core -------------------------------------------------------------------
def process_file(f, msg, state):
    fid, fname = f.get("id"), f.get("name", "unknown")
    blob = slack_download(f["url_private_download"] if f.get("url_private_download") else f["url_private"])
    h = hashlib.sha256(blob).hexdigest()
    if h in state:
        return f"skip (dedupe): {fname}"

    # DB-side dedupe guard too
    st, rows = supa("GET", f"commission_ingest_batches?content_hash=eq.{h}&select=id")
    if rows:
        state[h] = {"file": fname, "batch_id": rows[0]["id"], "ts": msg.get("ts")}
        return f"skip (already staged): {fname}"

    with tempfile.NamedTemporaryFile(suffix="_" + fname, delete=False) as tmp:
        tmp.write(blob); tmp_path = tmp.name

    try:
        out = subprocess.run(["npx", "tsx", "scripts/parse_statement.ts", tmp_path],
                             cwd=REPO, capture_output=True, text=True, timeout=180)
        parsed = json.loads(out.stdout.strip().splitlines()[-1]) if out.stdout.strip() else {"parserKey": None}
    except Exception as e:
        parsed = {"parserKey": None, "error": str(e)}
    finally:
        os.unlink(tmp_path)

    uploader = msg.get("user", "")
    if not parsed.get("parserKey"):
        # Unknown format -> needs_mapping batch + card (do NOT guess columns).
        st, b = supa("POST", "commission_ingest_batches",
                     {"content_hash": h, "source_file": fname, "slack_channel": CHANNEL,
                      "slack_file_id": fid, "slack_message_ts": msg.get("ts"), "kind": "unknown",
                      "extraction_method": (fname.rsplit(".", 1)[-1].lower() if "." in fname else None),
                      "ingest_status": "needs_mapping", "uploaded_by": uploader,
                      "flags": ["unrecognized format — no parser yet"]},
                     prefer="return=representation")
        bid = b[0]["id"] if b else "?"
        state[h] = {"file": fname, "batch_id": bid, "ts": msg.get("ts"), "status": "needs_mapping"}
        slack_post("chat.postMessage", {"channel": CHANNEL,
                   "text": f":warning: *{fname}* — I don't have a parser for this format yet "
                           f"(sheets: {parsed.get('sheetNames')}). Staged as `needs_mapping` (batch `{str(bid)[:8]}`). "
                           f"I'll build a parser for this carrier — ping me."})
        return f"needs_mapping: {fname}"

    hdr, txns, cc = parsed["header"], parsed["transactions"], parsed["crossCheck"]
    carrier = hdr["carrier_name"]
    st, b = supa("POST", "commission_ingest_batches",
                 {"content_hash": h, "source_file": fname, "slack_channel": CHANNEL,
                  "slack_file_id": fid, "slack_message_ts": msg.get("ts"),
                  "carrier_name": carrier, "canonical_carrier": carrier, "kind": "statement",
                  "parser_key": parsed["parserKey"], "extraction_method": hdr.get("source_format"),
                  "is_ocr": False, "row_count": hdr.get("row_count"),
                  "parsed_total_premium": cc.get("parsed_total_premium"),
                  "parsed_total_commission": cc.get("parsed_total_commission"),
                  "stated_total_premium": hdr.get("carrier_stated_total_premium"),
                  "stated_total_commission": hdr.get("carrier_stated_total_commission"),
                  "stated_net_due": hdr.get("carrier_stated_net_due"),
                  "ingest_status": "pending_review", "uploaded_by": uploader},
                 prefer="return=representation")
    if not b:
        return f"ERROR staging batch for {fname}: {b}"
    bid = b[0]["id"]

    staging = [{"batch_id": bid, "carrier_name": carrier, "policy_number": t["policy_number"],
                "insured_name": t["insured_name"], "lob": t["lob"], "segment": t["segment"],
                "transaction_code": t["transaction_code"], "transaction_type": t["transaction_type"],
                "transaction_date": t["transaction_date"], "month_key": t["month_key"],
                "gross_premium": t["gross_premium"], "commission_rate": t["commission_rate"],
                "commission_amount": t["commission_amount"], "fee_type": t["fee_type"],
                "fee_amount": t["fee_amount"], "raw_row": t["raw_row"]} for t in txns]
    supa("POST", "commission_transactions_staging", staging, prefer="return=minimal")

    state[h] = {"file": fname, "carrier": carrier, "batch_id": bid, "ts": msg.get("ts"), "status": "pending_review"}
    post_card(fname, bid, carrier, txns, cc)
    return f"staged: {fname} -> batch {str(bid)[:8]} ({len(txns)} rows)"


def post_card(fname, bid, carrier, txns, cc):
    pols = [t["policy_number"] for t in txns if t["policy_number"]]
    inbook = {}
    if pols:
        q = "in.(" + ",".join('"' + p + '"' for p in pols) + ")"
        _, led = supa("GET", f"commission_ledger?policy_number={urllib.parse.quote(q)}&select=policy_number,expected_commission")
        inbook = {r["policy_number"]: r["expected_commission"] for r in (led or [])}

    # as_earned caveat straight from the carrier profile
    _, prof = supa("GET", f"carrier_commission_profile?carrier_name=eq.{urllib.parse.quote(carrier)}&select=payment_model")
    pm = prof[0]["payment_model"] if prof else None

    rows = "\n".join(
        f"| {t['policy_number']} | {(t['insured_name'] or '')[:22]} | {t['lob'] or ''} | "
        f"{money(t['commission_amount'])} | "
        f"{'✅ exp '+money(inbook[t['policy_number']]) if t['policy_number'] in inbook else '⚠️ not in book'} |"
        for t in txns)
    flags = []
    if any(t["policy_number"] not in inbook for t in txns):
        flags.append("• Some policies aren't in the ledger (statement-only) — add to book or confirm.")
    if pm == "as_earned":
        flags.append("• *As-earned carrier* — a monthly statement is a partial of the full-term expected, not a short.")
    if cc.get("commission_matches") is None:
        flags.append("• No carrier summary total to cross-check against.")
    flags.append("• Original archives to Nextcloud on approval, before commit.")

    msg = (f":receipt: *Review — {carrier}* `{fname}`\n"
           f"Batch `{str(bid)[:8]}` · {len(txns)} policies · _nothing is written until you approve_\n\n"
           f"Parsed commission: *{money(cc.get('parsed_total_commission'))}* · premium {money(cc.get('parsed_total_premium'))}\n\n"
           f"| Policy | Client | LOB | Comm | In book? |\n|---|---|---|---|---|\n{rows}\n\n"
           + "\n".join(flags) +
           f"\n\n*Approve* → I commit the {len(txns)} rows + reconcile {carrier}. *Reject* → discarded.")
    slack_post("chat.postMessage", {"channel": CHANNEL, "text": msg})


def eod_summary():
    today = datetime.now(timezone.utc).date().isoformat()
    _, pend = supa("GET", "commission_ingest_batches?ingest_status=eq.pending_review&select=source_file,carrier_name,parsed_total_commission")
    _, comm = supa("GET", f"commission_ingest_batches?ingest_status=eq.committed&reviewed_at=gte.{today}&select=source_file")
    _, book = supa("GET", "v_book_summary?select=*")
    lines = [":moon: *Commission inbox — end of day*"]
    lines.append(f"Committed today: {len(comm or [])}")
    if pend:
        lines.append(f"*Still pending your approval: {len(pend)}* — " +
                     ", ".join(f"{p['source_file']} ({money(p.get('parsed_total_commission'))})" for p in pend))
    else:
        lines.append("Nothing pending approval. :white_check_mark:")
    slack_post("chat.postMessage", {"channel": CHANNEL, "text": "\n".join(lines)})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--eod", action="store_true")
    a = ap.parse_args()
    if not SLACK or not SVC:
        sys.exit("missing creds (Slack bot token / Supabase service key from 1Password)")

    hist = slack_get("conversations.history", {"channel": CHANNEL, "limit": 100})
    if not hist.get("ok"):
        sys.exit(f"slack history failed: {hist.get('error')}")

    state = load_state()
    results = []
    for m in reversed(hist.get("messages", [])):        # oldest-first
        for f in m.get("files", []):
            try:
                results.append(process_file(f, m, state))
            except Exception as e:
                results.append(f"ERROR {f.get('name')}: {e}")
    save_state(state)
    for r in results:
        print(r)
    if a.eod:
        eod_summary()
        print("posted EOD summary")


if __name__ == "__main__":
    main()
