"""Render the renewal task card (markdown) shown in the EspoCRM Task description.

The card is a self-contained packet: the facts Gretchen has, the ordered steps,
the premium decision guide, and the definition of done. She never has to infer a
next step — she looks up the answer.
"""
from __future__ import annotations

from . import config


def _money(v) -> str:
    try:
        return f"${float(v):,.0f}"
    except (TypeError, ValueError):
        return "—"


def build_card(renewal: dict) -> str:
    account = renewal.get("accountName") or renewal.get("name") or "this client"
    carrier = renewal.get("carrier") or "—"
    lob = renewal.get("line_of_business") or "—"
    current = _money(renewal.get("current_premium"))
    proposed = _money(renewal.get("renewal_proposed_premium"))
    renewal_premium = _money(renewal.get("renewal_premium"))
    exp = renewal.get("expiration_date") or "—"
    urgency = renewal.get("urgency") or "—"
    std = f"{config.BAND_STANDARD_MAX:.0f}"
    rev = f"{config.BAND_REVIEW_MAX:.0f}"

    return f"""**Renewal prep — {account}**
{lob} · {carrier} · expires {exp} · urgency: {urgency}

Why this matters: every renewal we touch early is a client we keep. This is retention work.

**The facts you have**
- Expiring premium: {current}
- Carrier renewal proposal: {proposed}
- Renewal premium: {renewal_premium if renewal_premium != '—' else '_enter the agent quote once the market is worked_'}
- Line of business: {lob}

**Do these in order**
1. Pull the renewal declaration from the carrier portal
2. Confirm the account details still match and complete the Renewal Worksheet
3. Enter the **Renewal Premium** agent quote on this record — the retained/remarket % change still calculates from that quote
4. Read the % change, then follow the guide below
5. Set **Pipeline Stage** / **Disposition** to match what you did, add notes, then mark this task **Completed**

**Decision guide — once Renewal Premium is in, what does the % change say?**

| Premium change | What to do | Stage |
|---|---|---|
| Flat or down | Send the good-news renewal email | Outreach Sent then Renewed - Won when bound |
| Up to {std}% | Send the standard renewal email | Outreach Sent then Renewed - Won when bound |
| {std}-{rev}% | Hold the email. Flag Lamar in #the-boss. Shop it. | Quote Requested |
| {rev}%+ | Flag Lamar URGENT. Pull 2 remarket quotes first. | Quote Requested |

**If they're shopping or leaving:** set the renewal **Disposition**, and put what they actually said in
**Renewal Notes** ("client states..."). That note is how we learn why we lose people — never skip it.

**Done when:** pipeline stage/disposition set, renewal premium entered, email sent (or Lamar flagged),
AMS updated in NowCerts, and this task marked Completed. Won/lost auto-files the
worksheet to the client folder.
"""
