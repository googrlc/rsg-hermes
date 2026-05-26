# NowCerts / Momentum → EspoCRM CSV Import Mapping

**Source AMS:** NowCerts / Momentum
**Target:** EspoCRM at `rrespocrm-rsg-u69864.vm.elestio.app`
**Mode:** Upsert (insert if new, merge if matched) with AMS-lock awareness
**Generated from:** live `/api/v1/Metadata?key=entityDefs.{Entity}` (Espo Metadata API)

---

## 0. EspoCRM Metadata API quirk

When working against this Espo build, fetch metadata via the **`key=` query parameter**, not as a path. The path form falls through to Espo's related-records router and returns a misleading `Action GET 'listLinked' does not exist in controller 'Metadata'` 404.

```
❌ GET /api/v1/Metadata/entityDefs/Policy        → 404 listLinked
✅ GET /api/v1/Metadata?key=entityDefs.Policy    → 200 OK (full defs JSON)
```

Useful keys:

| Endpoint | Returns |
|---|---|
| `?key=entityDefs.Policy` | Fields, links, formula, indexes, sort |
| `?key=entityDefs.Account` | Same for Account |
| `?key=entityDefs.Contact` | Same for Contact |
| `?key=entityDefs.Policy.fields.status` | Just the `status` enum + style + kanbanOrder |
| `?key=entityDefs.Policy.fields.status.options` | Just the option array |
| `?key=clientDefs.Policy` | UI/view config |
| `?key=scopes.Policy` | Entity capabilities (stream, kanban, etc.) |
| *(no `key=`)* | Entire metadata blob (~509 KB) |

---

## 1. Policy mapping (NowCerts column → Espo `Policy` field)

| NowCerts CSV column | Espo field | Type | Notes |
|---|---|---|---|
| `databaseId` *(or `id`)* | `momentumPolicyId` | varchar | **DEDUP KEY** — unique index, READONLY in Espo, never overwrite once set |
| `insuredDatabaseId` | `insuredMomentumId` | varchar | **INSURED LOOKUP KEY** — used to resolve `account` / `contact` link, READONLY |
| `policyNumber` | `policy_number` | varchar | |
| `carrierName` | `carrier` | varchar(255) | |
| `lineOfBusiness` | `line_of_business_raw` | varchar(500) | Original — READONLY, preserve as-is |
| *(normalized)* | `line_of_business` | varchar(500) | Cleaned label, set by your normalization pass |
| `effectiveDate` | `effective_date` | date | YYYY-MM-DD |
| `expirationDate` | `expiration_date` | date | YYYY-MM-DD — drives renewal automation |
| `bindDate` | `bind_date` | date | |
| `cancellationDate` | `cancellation_date` | date | |
| `reinstatementDate` | `reinstatement_date` | date | |
| `premium` *(or `policyPremium`)* | `premium_amount` | currency | Strip `$` and commas |
| `commissionRate` | `commission_rate` | float | If value > 1, divide by 100 (formula handles this too) |
| `coverageAmount` | `coverage_amount` | currency | |
| `deductible` | `deductible` | currency | |
| `agencyFee` | `agency_fee` | currency | |
| `businessType` | `business_type` | varchar(100) | |
| `billingType` | `billing_type` | **enum** | Must match exact options — see §2 |
| `policyTerm` *(months)* | `policy_term` | int | 6 or 12 |
| `cancellationReason` | `cancellation_reason` | text | |
| `policyStatus` *(or `status`)* | `status` | **enum** | Must match exact options — see §2 |
| `notes` | `policy_notes` | text | |
| `vin` | `vin` | varchar(100) | Auto policies only |
| `propertyAddress` | `propertyAddress` | text | Home policies only |

### Do NOT write to (READONLY / Espo-managed)

- `commissionAmount`, `premiumAtRisk` — computed by `beforeSaveScript`
- `momentum_last_synced`, `sync_status` — set by sync workflow
- `acceptedByAmsAt`, `acceptedByAmsBy`, `amsLockState`, `amsLockReason` — AMS feedback loop
- `daysRemaining`, `statusLabel`, `urgencyIcon`, `carrierPortalUrl` — UI-computed
- `last_contact_method`, `last_contact_date`, `email_sequence_started` — outreach automation

---

## 2. Enum option keys (live from metadata — exact strings)

### `Policy.status` — 10 options

```
"" | "Active" | "Up for Renewal" | "Renewing" | "Renewed" | "Expired"
"Cancelled" | "Flat Cancel" | "Pending Cancel" | "Non-Renewed" | "Lapsed"
```

**NowCerts → Espo normalization** (case-insensitive, trim whitespace):

| NowCerts value | Espo `status` |
|---|---|
| `active`, `in force`, `inforce`, `bound` | `Active` |
| `up for renewal`, `renewal pending` | `Up for Renewal` |
| `renewing`, `in renewal` | `Renewing` |
| `renewed` | `Renewed` |
| `expired` | `Expired` |
| `cancelled`, `canceled` | `Cancelled` |
| `flat cancel`, `flat-cancel`, `flat cancelled` | `Flat Cancel` |
| `pending cancel`, `pending cancellation`, `cxl pending` | `Pending Cancel` |
| `non-renewed`, `non renewed`, `nonrenewed` | `Non-Renewed` |
| `lapsed` | `Lapsed` |
| *anything else / blank* | `""` (empty), and set `sync_status="Error"` for review |

### `Policy.billing_type` — 4 options

```
"" | "Direct Bill" | "Agency Bill" | "Direct Bill 100" | "Agency Bill 100"
```

| NowCerts value | Espo `billing_type` |
|---|---|
| `direct bill`, `direct`, `db` | `Direct Bill` |
| `agency bill`, `agency`, `ab` | `Agency Bill` |
| `direct bill 100`, `db 100`, `db100` | `Direct Bill 100` |
| `agency bill 100`, `ab 100`, `ab100` | `Agency Bill 100` |
| *blank / unknown* | `""` |

### Reference enums you should NOT write (READONLY, computed)

- `sync_status`: `"" | "Synced" | "Pending" | "Error" | "Skipped"` — set programmatically post-write
- `amsLockState`: `"" | "Unlocked" | "Pending AMS" | "Locked by AMS" | "Rejected by AMS"` — AMS owns this
- `statusLabel`: `"" | "ACTIVE" | "UNKNOWN" | "RENEWAL WINDOW" | "CRITICAL RENEWAL" | "EXPIRED"` — formula-driven
- `urgency`: `"" | "Low" | "Medium" | "High" | "Critical"` — n8n sets this

---

## 3. Dedup & insured-link resolution (per-row algorithm)

```
FOR each CSV row:

  # ─── Step A: Find existing Policy by Momentum ID ─────────────────────
  GET /api/v1/Policy
    ?where[0][type]=equals
    &where[0][attribute]=momentumPolicyId
    &where[0][value]={csv.databaseId}
    &select=id,amsLockState,momentumPolicyId
    &maxSize=1

  IF total == 0:    action = "create"   ; existing = null
  IF total == 1:    action = "update"   ; existing = list[0]
  IF total >  1:    action = "ERROR"    ; LOG duplicate momentumPolicyId

  # ─── Step B: Resolve insured Account link ─────────────────────────────
  IF csv.insuredDatabaseId is present:
    GET /api/v1/Account
      ?where[0][type]=equals
      &where[0][attribute]=momentum_client_id
      &where[0][value]={csv.insuredDatabaseId}
      &select=id,name
      &maxSize=1

    IF total == 1: payload.accountId = account.id
    IF total == 0:
        # Either skip-create or queue for Account import first
        EITHER:  (a) leave accountId null, set sync_status="Pending"
        OR:      (b) create a stub Account with momentum_client_id + name, then link
    payload.insuredMomentumId = csv.insuredDatabaseId   # ONLY on create — readonly after

  # ─── Step C: Write ────────────────────────────────────────────────────
  IF action == "create":
      POST /api/v1/Policy with full payload (including momentumPolicyId, insuredMomentumId)
  IF action == "update":
      payload = apply_lock_filter(payload, existing.amsLockState)   # see §4
      REMOVE payload.momentumPolicyId, payload.insuredMomentumId    # readonly post-create
      PUT /api/v1/Policy/{existing.id} with filtered payload
```

### Dedup invariants

- `momentumPolicyId` and `insuredMomentumId` are `readOnly: true` in defs — Espo will reject changes once set. Only send them on **create (POST)**, never on **update (PUT)**.
- Indexes confirm uniqueness intent: `indexes.momentumPolicyId` and `indexes.insuredMomentumId` exist on Policy.

---

## 4. AMS-lock-safe PUT filter

When `existing.amsLockState == "Locked by AMS"`, the AMS owns the policy core — your import must not overwrite those fields, or the next AMS sync will reject the change and flip `amsLockState` to `"Rejected by AMS"`.

### Locked-core field set (strip from PUT payload when locked)

```js
const LOCKED_CORE_FIELDS = [
  // Identity & terms — AMS-authoritative
  "policy_number",
  "carrier",
  "line_of_business",
  "line_of_business_raw",
  "status",
  "effective_date",
  "expiration_date",
  "bind_date",
  "cancellation_date",
  "cancellation_reason",
  "reinstatement_date",
  // Money — AMS-authoritative
  "premium_amount",
  "commission_rate",
  "coverage_amount",
  "deductible",
  "agency_fee",
  // Classification — AMS-authoritative
  "business_type",
  "billing_type",
  "policy_term",
  // Account/insured linkage (always readonly post-create regardless of lock)
  "accountId", "contactId",
];

// Fields that ARE still safe to update when locked
// (CRM-side enrichment, not AMS-owned):
const ALWAYS_WRITABLE = [
  "policy_notes",          // CSR notes
  "vin",                   // we can enrich VIN even when locked
  "propertyAddress",
  "assignedUserId",        // book reassignment
  "teamsIds",
];
```

### Filter function

```js
function applyLockFilter(payload, amsLockState) {
  if (amsLockState !== "Locked by AMS") return payload;   // open for edit

  const filtered = {};
  for (const [key, value] of Object.entries(payload)) {
    if (LOCKED_CORE_FIELDS.includes(key)) continue;       // drop locked field
    filtered[key] = value;
  }
  return filtered;
}
```

### Per-state behavior

| State | Behavior |
|---|---|
| `Unlocked` *(or empty)* | Full PUT, all fields writable |
| `Pending AMS` | **Skip the update**, log row to "deferred" queue — AMS is currently processing a prior correction, writing now creates a race |
| `Locked by AMS` | Apply `applyLockFilter` — only enrichment fields go through |
| `Rejected by AMS` | **Skip the update**, surface to CSR queue — prior correction was rejected, needs human review before more writes |

### n8n implementation sketch

```
[Read CSV row]
   ↓
[HTTP: GET Policy where momentumPolicyId=X]
   ↓
[IF found]──→[IF amsLockState in (Pending AMS, Rejected by AMS)]──→[Slack alert + skip]
   │                                                          │
   │                                                          └──→[Function: applyLockFilter] ──→ [HTTP PUT]
   │
   └──→ [HTTP: GET Account where momentum_client_id=Y]
        ↓
        [Function: build payload with momentumPolicyId + insuredMomentumId + accountId]
        ↓
        [HTTP POST /Policy]
```

---

## 5. Account mapping (NowCerts insured → Espo `Account`)

Same dedup pattern — `momentum_client_id` is the unique key, READONLY post-create.

| NowCerts CSV column | Espo field | Type | Notes |
|---|---|---|---|
| `databaseId` *(insured)* | `momentum_client_id` | text | **DEDUP KEY**, READONLY |
| `commercialName` *or* `firstName + lastName` | `name` | varchar | Standard Espo field |
| `fein` | `fein` | varchar | |
| `csrName` | `csrName` | text | |
| `agentOfRecordDate` | `agent_of_record_date` | date | READONLY (set once, AMS-driven) |
| `agentOfAgencyCode` | `agent_of_agency_code` | text | |
| `accountType` *(commercial/personal/etc)* | `account_type` | **enum** | See enum below |
| `industry` | `industry` | enum | Standard Espo industry list — 51 options, must match exactly |
| `sicCode` | `sic_code` | text | |
| `naics` | `intel_naics` | text | |
| `website` | `websiteUrl` | url | |
| `linkedinUrl` | `linkedin_url` | url | |
| `annualRevenue` | `annual_revenue` | currency | |
| `numberOfEmployees` | `numberOfEmployees` | int | |
| `phone` | `phoneNumber` | phone | Standard field |
| `email` | `emailAddress` | email | Standard field |
| `billingAddressStreet` | `billingAddressStreet` | varchar | Standard |
| `billingAddressCity` | `billingAddressCity` | varchar | Standard |
| `billingAddressState` | `billingAddressState` | varchar | Standard |
| `billingAddressPostalCode` | `billingAddressPostalCode` | varchar | Standard |
| `clientSinceDate` | `client_since` | date | |
| `referralSource` | `referral_source` | **enum** | See enum below |

### Account enum options (live)

- **`account_type`**: `"" | "Prospect" | "Commercial Lines" | "Personal Lines" | "Group Benefits" | "Medicare" | "Life Insurance" | "Carrier" | "MGA"`
- **`account_status`**: `"" | "Active" | "Urgent" | "Renewing" | "At Risk" | "Inactive"`
- **`type`** *(standard Espo)*: `"" | "Commercial Lines" | "Personal Lines" | "Group Benefits" | "Prospect"`
- **`stage`**: `"" | "New" | "Qualified" | "Proposal" | "Negotiation" | "Closed Won" | "Closed Lost"`
- **`businessEntity`**: `"" | "Sole Proprietor" | "LLC" | "Corporation" | "S-Corp" | "Partnership" | "Non-Profit" | "Other"`
- **`referral_source`**: `"" | "Referral" | "Google" | "Social Media" | "Cold Outreach" | "Walk-in" | "NowCerts Import" | "Other"` → **use `"NowCerts Import"` for catch-up rows**
- **`preferred_contact`**: `"" | "Phone" | "Email" | "Text"`
- **`accountCategory`**: `"" | "Personal" | "Commercial" | "Carrier" | "MGA"`

### Do NOT write to (READONLY / Espo-managed)

- `total_carrier_premium`, `totalAnnualPremium`, `total_active_premium`, `activePolicyCount`, `policyCountActive`
- `account_score`, `score_breakdown`, `score_total`, `score_tier`, `days_to_renewal`, `gapCount`
- `intel_pack_last_run`

### Account-side lock?

There is **no `amsLockState` on Account** — Account writes are always allowed. The lock model only applies to Policy. Account PUTs just go through; collision risk is handled by AMS sync overwriting on the next pull.

---

## 6. Contact mapping (NowCerts contact → Espo `Contact`)

> **Naming inconsistency to watch out for:** the dedup field is `momentumClientId` (**camelCase**) on Contact, but `momentum_client_id` (**snake_case**) on Account. They are separate fields — don't reuse the same value across both entities. A NowCerts "insured" becomes an Account, while NowCerts "contacts" become Contacts with their own NowCerts contact IDs.

### Field mapping

| NowCerts CSV column | Espo field | Type | Notes |
|---|---|---|---|
| `databaseId` *(contact-level)* | `momentumClientId` | varchar | **DEDUP KEY**, READONLY — note: no DB-level unique index, enforce in workflow |
| `firstName` | `firstName` | varchar | Standard field |
| `lastName` | `lastName` | varchar | Standard field, required-ish |
| `middleName` | `middleName` | varchar | |
| `salutation` | `salutationName` | enum | `"" \| "Mr." \| "Ms." \| "Mrs." \| "Dr."` — must match exactly (with period) |
| `title` *(commercial)* | `title` | varchar | Job title at the account |
| `dateOfBirth` | `dateOfBirth` | date | YYYY-MM-DD |
| `phone` *(primary)* | `phoneNumber` | phone | |
| `email` | `emailAddress` | email | |
| `mobilePhone` | `phoneNumberData` *(typed)* | phoneData | Use Espo's multi-phone format; see below |
| `addressStreet` | `addressStreet` | varchar | Standard |
| `addressCity` | `addressCity` | varchar | |
| `addressState` | `addressState` | varchar | |
| `addressPostalCode` | `addressPostalCode` | varchar | |
| `csrName` | `csrName` | varchar | READONLY |
| `originalLeadSource` | `originalLeadSource` | varchar | READONLY |
| `clientType` | `clientType` | **enum** | See enum below |
| `contactType` | `contactType` | **enum** | See enum below |
| `householdRole` | `householdRole` | **enum** | See enum below |
| `relationshipToAccount` | `relationshipToAccount` | **enum** | See enum below |
| `description` *(notes)* | `description` | text | Standard field |
| `accountDatabaseId` *(parent insured)* | `accountId` | link | **Lookup**: find Account where `momentum_client_id = csv.accountDatabaseId`, set primary account |

### Multi-phone format (when NowCerts gives you `homePhone`, `workPhone`, `mobilePhone`)

```json
{
  "phoneNumber": "+15551234567",
  "phoneNumberData": [
    { "phoneNumber": "+15551234567", "type": "Mobile", "primary": true },
    { "phoneNumber": "+15555550100", "type": "Home", "primary": false },
    { "phoneNumber": "+15555550101", "type": "Office", "primary": false }
  ]
}
```

### Contact enum options (live)

- **`clientType`**: `"" | "Personal" | "Commercial"` → derive from parent Account's `account_type`
- **`contactType`**: `"" | "Client" | "Prospect" | "Spouse" | "Dependent" | "Business Owner"`
- **`contactRole`**: `"" | "Underwriter"` *(only Underwriter today — used to link Contact as `underwriter` on a Policy)*
- **`householdRole`**: `"" | "Primary" | "Spouse" | "Dependent" | "Co-insured"` → Personal Lines only
- **`relationshipToAccount`**: `"" | "Head of Household" | "Secondary" | "Employee"`
- **`contactCategory`**: `"" | "Client" | "Prospect" | "Carrier" | "Other"`
- **`medicarePlanType`**: `"" | "Supplement" | "Advantage" | "Part D"`
- **`lifePolicyType`**: `"" | "Term" | "Whole" | "Universal"`
- **`lifeHealthClass`**: `"" | "Preferred Plus" | "Preferred" | "Standard Plus" | "Standard" | "Substandard"`
- **`salutationName`** *(standard)*: `"" | "Mr." | "Ms." | "Mrs." | "Dr."` — **include the period**

### Derive `clientType` + `contactType` + `householdRole` from NowCerts hints

```js
function inferContactClassification(csvRow, parentAccount) {
  // clientType comes from parent account type
  const clientType =
    parentAccount?.account_type === "Personal Lines" ? "Personal" :
    parentAccount?.account_type === "Commercial Lines" ? "Commercial" : "";

  // contactType — best-guess from NowCerts role + relationship
  let contactType = "";
  const rel = (csvRow.relationship || "").toLowerCase();
  if (clientType === "Commercial") {
    contactType = csvRow.isOwner ? "Business Owner" : "Client";
  } else {
    if (rel === "spouse")     contactType = "Spouse";
    else if (rel === "child" || rel === "dependent") contactType = "Dependent";
    else if (csvRow.isPrimary) contactType = "Client";
    else contactType = "Client";
  }

  // householdRole — Personal Lines only
  let householdRole = "";
  if (clientType === "Personal") {
    if (csvRow.isPrimary)             householdRole = "Primary";
    else if (rel === "spouse")        householdRole = "Spouse";
    else if (rel === "child" || rel === "dependent") householdRole = "Dependent";
    else                              householdRole = "Co-insured";
  }

  return { clientType, contactType, householdRole };
}
```

### Contact dedup & link resolution (per-row)

```
FOR each contact CSV row:

  # ─── Step A: Find existing Contact by Momentum ID ────────────────────
  GET /api/v1/Contact
    ?where[0][type]=equals
    &where[0][attribute]=momentumClientId          # camelCase! not snake_case
    &where[0][value]={csv.databaseId}
    &select=id,momentumClientId,accountId
    &maxSize=1

  IF total == 0:    action = "create"
  IF total >= 1:    action = "update" ; existing = list[0]

  # ─── Step B: Resolve parent Account link ─────────────────────────────
  IF csv.accountDatabaseId is present:
    GET /api/v1/Account
      ?where[0][type]=equals
      &where[0][attribute]=momentum_client_id      # snake_case on Account!
      &where[0][value]={csv.accountDatabaseId}
      &select=id&maxSize=1

    IF total == 1: payload.accountId = account.id   # primary account link
    IF total == 0:
        # Parent insured missing — same options as Policy: skip-with-pending OR stub
        log warning, set payload.accountId = null

  # ─── Step C: Build payload ───────────────────────────────────────────
  payload.firstName, lastName, etc.
  payload = { ...payload, ...inferContactClassification(csv, account) }

  # ─── Step D: Write ───────────────────────────────────────────────────
  IF action == "create":
      payload.momentumClientId = csv.databaseId    # ONLY on create
      POST /api/v1/Contact
  IF action == "update":
      REMOVE payload.momentumClientId, payload.csrName, payload.originalLeadSource   # readonly
      PUT /api/v1/Contact/{existing.id}
```

### Contact lock model

There is **no `amsLockState` on Contact** (same as Account). Contact PUTs always go through; no lock filter needed.

### Linking a Contact as a Policy's `underwriter`

If the CSV row represents an underwriter (carrier-side person):

1. Set `contactRole = "Underwriter"` on the Contact.
2. Link the Contact to the carrier `Account` (which should have `account_type = "Carrier"`).
3. On the Policy side, set `underwriterId = contact.id` (NOT `contactId` — those are different links: `contact` = insured-side, `underwriter` = carrier-side).

---

## 7. Recommended import order

Run in this sequence — links depend on prior step:

1. **Accounts first** → upsert by `momentum_client_id`. Build an in-memory lookup table `{nowcerts_insured_id → espo_account_id}`.
2. **Contacts second** → upsert by `momentumClientId`. Resolve `accountId` from the lookup table built in step 1.
3. **Policies last** → upsert by `momentumPolicyId`. Resolve `accountId` from the same lookup, apply AMS-lock filter on PUT.

---

## 8. Open questions / decisions needed before kickoff

1. **Sample CSV rows** — need 2–3 rows from each NowCerts export to confirm column names match assumptions above. NowCerts has variants depending on which "Export to Excel" path you use.
2. **`Pending AMS` / `Rejected AMS` behavior** — confirm skip vs queue vs alert.
3. **Orphan-row policy** — Policy with no matching Account, or Contact with no matching Account: stub-create the Account, or skip with `sync_status="Pending"`?
