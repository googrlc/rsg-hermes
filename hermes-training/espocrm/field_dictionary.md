# EspoCRM Field Dictionary

> Key fields per entity with types and enum values. Sourced from the live
> `entityDefs` JSON and `custom-fields-camelcase-audit.csv`.
>
> **Naming convention:** The codebase is actively migrating from camelCase to
> snake_case. All **new** fields MUST be snake_case. Existing fields may use
> either convention — always verify via schema lookup.

---

## Account (289 fields)

### Identity & Classification

| Field | Type | Notes |
|-------|------|-------|
| `name` | varchar | Primary display name |
| `account_status` | enum | `Active`, `Urgent`, `Renewing`, `At Risk`, `Inactive` |
| `account_type` | enum | `Prospect`, `Commercial Lines`, `Personal Lines`, `Group Benefits`, `Medicare`, `Life Insurance`, `Carrier`, `MGA` |
| `industry` | enum | 50+ values: Advertising, Agriculture, Automotive, Construction, Healthcare, Insurance, Manufacturing, Real Estate, Technology, Transportation, etc. |
| `businessEntity` | enum | `Sole Proprietor`, `LLC`, `Corporation`, `S-Corp`, `Partnership`, `Non-Profit`, `Other` |
| `fein` | varchar | Federal Employer ID Number — primary dedup key for businesses |

### Financial

| Field | Type | Notes |
|-------|------|-------|
| `annual_premium` | currency | Total annual premium across all policies |
| `annual_revenue` | currency | Client's business revenue |
| `activePolicyCount` | int | Number of active policies |

### Contact & Communication

| Field | Type | Notes |
|-------|------|-------|
| `emailAddress` | email | Primary email |
| `phoneNumber` | phone | Primary phone |
| `preferred_contact` | enum | `Phone`, `Email`, `Text` |
| `best_time_to_call` | text | Free-text preference |
| `last_contact_type` | enum | `Call`, `Email`, `Text`, `In Person` |
| `last_contact_outcome` | enum | `Reached`, `Voicemail`, `No Answer`, `Email Opened`, `Unresponsive` |
| `referral_source` | enum | `Referral`, `Google`, `Social Media`, `Cold Outreach`, `Walk-in`, `NowCerts Import`, `Other` |

### Intel Fields (AI-populated)

| Field | Type | Notes |
|-------|------|-------|
| `ai_assessment` | text | AI-generated account summary |
| `intel_confidence` | enum | `High`, `Medium`, `Low` |
| `intel_entity_type` | enum | `LLC`, `Corp`, `Sole Prop`, `Partnership`, `Other` |
| `bbb_rating` | text | Better Business Bureau rating |
| `assessment_date` | datetime | When AI assessment was last run |

### Personal Lines / Property

| Field | Type | Notes |
|-------|------|-------|
| `maritalStatus` | enum | `Single`, `Married`, `Divorced`, `Widowed`, `Separated` |
| `primaryGender` | enum | `Male`, `Female`, `Other`, `Prefer not to say` |
| `homeConstruction` | enum | `Frame`, `Masonry`, `Mixed`, `Log`, `Other` |
| `homeRoofMaterial` | enum | `Asphalt Shingle`, `Metal`, `Tile`, `Wood Shake`, `Other` |

### Medicare / Life

| Field | Type | Notes |
|-------|------|-------|
| `policyMedicarePlanType` | enum | `Medicare Advantage`, `Medicare Supplement`, `PDP` |
| `policyLifeType` | enum | `Term`, `Whole`, `Universal`, `Final Expense` |

### Claims History

| Field | Type | Notes |
|-------|------|-------|
| `last_claim_lob` | enum | `Auto`, `Home`, `Umbrella`, `Life`, `Medicare`, `Renters`, `Other` |
| `last_claim_status` | enum | `Open`, `Closed`, `Subrogation` |

---

## Contact (31 fields)

| Field | Type | Notes |
|-------|------|-------|
| `name` | varchar | Full name (firstName + lastName) |
| `emailAddress` | email | Primary email — dedup key for contacts |
| `phoneNumber` | phone | Primary phone |
| `contactType` | enum | Contact classification |
| `clientType` | enum | Client type classification |
| `contactRole` | enum | Role within the account |
| `householdRole` | enum | Household relationship |
| `dateOfBirth` | date | DOB |
| `aepSepDate` | date | Medicare Annual/Special Enrollment Period date |
| `daysUntil65` | int | Computed days until Medicare eligibility |
| `contactDriveFolderUrl` | url | Link to Google Drive folder |
| `irmaApplies` | bool | IRMAA (Medicare income-related adjustment) flag |
| `lifeAnnualPremium` | currency | Life insurance annual premium |

---

## Lead (18 fields)

| Field | Type | Notes |
|-------|------|-------|
| `name` | varchar | Full name |
| `emailAddress` | email | Primary email |
| `phoneNumber` | phone | Primary phone |
| `source` | enum | `Call`, `Email`, `Existing Customer`, `Client Referral`, `Partner Referral`, `Public Relations`, `Web Site`, `Campaign`, `Other` |
| `insuranceInterest` | enum | What type of insurance they need |
| `priority` | enum | `Hot`, `Warm`, `Cold` |
| `aiSummary` | text | AI-generated lead summary |
| `estimatedPremium` | currency | Estimated premium value |
| `currentCarrier` | varchar | Current insurance carrier |
| `currentlyInsured` | bool | Whether currently insured |
| `callbackDate` | date | Scheduled callback |
| `dateOfBirth` | date | DOB |
| `medicareEligible` | bool | Medicare eligibility flag |
| `medicarePartADate` | date | Part A effective date |
| `medicarePartBDate` | date | Part B effective date |
| `currentMedicarePlan` | varchar | Current Medicare plan |
| `intelPackRun` | bool | Whether intel pack has been generated |

---

## Opportunity (144 fields)

### Core Pipeline

| Field | Type | Notes |
|-------|------|-------|
| `name` | varchar | Opportunity name |
| `stage` | enum | **Strict order:** `Discovery` → `Quoting` → `Markets Out / Shopping` → `Proposal Presented` → `Negotiation` → `Closed Won` \| `Closed Lost` |
| `amount` | currency | Deal value |
| `lineOfBusiness` | enum | See Line of Business enum below |
| `businessType` | enum | `New Business`, `Renewal`, `Rewrite` |
| `probability` | int | Win probability percentage |
| `closeDate` | date | Expected close date |
| `leadSource` | enum | `Call`, `Email`, `Existing Customer`, `Client Referral`, `Partner Referral`, `Public Relations`, `Web Site`, `Campaign`, `Other` |
| `priority` | enum | `Hot`, `Warm`, `Cold` |
| `lostReason` | enum | `Price`, `Coverage`, `Service`, `Competitor Stole`, `Business Closed`, `Carrier Non-Renewed`, `Client Moved`, `Unknown`, `N/A` |
| `lastContactMethod` | enum | `Phone`, `Email`, `Text`, `No Response` |
| `aiSummary` | text | AI-generated opportunity summary |
| `bindDate` | date | Date coverage was bound |

### Auto / Personal Lines

| Field | Type | Notes |
|-------|------|-------|
| `autoVehicleCount` | int | Number of vehicles |
| `autoDriverCount` | int | Number of drivers |
| `autoYoungestDriverAge` | int | Age of youngest driver |
| `autoTotalVehicleValue` | currency | Total value of vehicles |
| `autoUseType` | enum | `Personal`, `Business`, `Commercial`, `Rideshare`, `Mixed` |
| `autoSR22Required` | bool | SR-22 filing required |
| `autoPriorAccidents` | int | Prior accident count |
| `autoPriorViolations` | int | Prior violation count |
| `autoGarageState` | varchar | State where vehicles are garaged |

### Commercial Auto / Trucking

| Field | Type | Notes |
|-------|------|-------|
| `caDotNumber` | varchar | DOT number |
| `caMcNumber` | varchar | MC number |
| `caBusType` | enum | `Trucking`, `Contractor`, `Fleet`, `Delivery`, `Other` |
| `caEquipmentType` | enum | `Owned`, `Leased`, `Rented`, `Mixed`, `Other` |
| `caEquipmentValue` | currency | Equipment value |
| `caRadius` | enum | `Local <50mi`, `Intermediate 50-200mi`, `Long haul 200+mi` |
| `caVehicleCount` | int | Commercial vehicle count |
| `caDriverCount` | int | Commercial driver count |
| `caCommodity` | varchar | Hauled commodity |

### Life & Health

| Field | Type | Notes |
|-------|------|-------|
| `lifeCoverageType` | enum | `Term`, `Whole`, `Universal`, `IUL` |
| `lifeTermLength` | enum | `10yr`, `15yr`, `20yr`, `30yr` |
| `lifeHealthClassTarget` | enum | `Preferred Plus`, `Preferred`, `Standard`, `Rated` |
| `healthCoverageType` | enum | `Term`, `Whole Life`, `Universal Life`, `Final Expense`, `Medicare Supplement`, `Medicare Advantage`, `Group`, `Individual`, `Other` |
| `healthRiskClass` | enum | `Preferred Plus`, `Preferred`, `Standard Plus`, `Standard`, `Substandard`, `Declined`, `Pending` |

### Medicare

| Field | Type | Notes |
|-------|------|-------|
| `medPlanType` | enum | `Advantage`, `Supplement`, `Part D` |

### Workflow Checklist (all bool)

`chkAppSubmitted`, `chkQuoteSubmitted`, `chkProposalSent`, `chkMvrsPulled`, `chkBound`, `chkDecPageDelivered`, `chkWelcomeLetter`, `chkScopeOfAppt`, `chkPlanPresented`, `chkCmsConfirmation`, `chkSignedAppReceived`, `chkUnderlyingConfirmed`, `chkUnderlyingLinked`

### Policy Stub

| Field | Type | Notes |
|-------|------|-------|
| `policyStubStatus` | enum | `Pending Sync`, `Synced` |

---

## Policy (56 fields)

| Field | Type | Notes |
|-------|------|-------|
| `policy_number` | varchar | Unique policy number |
| `carrier` | varchar | Insurance carrier name |
| `line_of_business` | enum | See Line of Business enum |
| `effective_date` | date | Coverage start date |
| `expiration_date` | date | Coverage end date |
| `premium` | currency | Policy premium |
| `status` | enum | Policy status |
| `billing_type` | enum | Billing method |
| `business_type` | varchar | Business type |
| `bind_date` | date | Date bound |
| `agency_fee` | currency | Agency fee amount |
| `cancellation_date` | date | If cancelled |
| `cancellation_reason` | text | Reason for cancellation |
| `amsLockState` | enum | `Pending Sync`, `Synced` — protects AMS-synced policies |
| `amsLockReason` | text | Reason for lock |
| `acceptedByAmsAt` | datetime | When AMS accepted the policy |
| `acceptedByAmsBy` | varchar | Who accepted in AMS |
| `carrierPortalUrl` | url | Link to carrier portal |

---

## Renewal (28 fields)

| Field | Type | Notes |
|-------|------|-------|
| `stage` | enum | **Strict order:** `Identified` → `Outreach Sent` → `Quote Requested` → `Proposal Sent` → `Negotiating` → `Renewed - Won` \| `Lost` |
| `expiration_date` | date | Policy expiration / renewal deadline |
| `current_premium` | currency | Current policy premium |
| `urgency` | enum | `Critical`, `High`, `Medium`, `Low` |
| `line_of_business` | enum | See Line of Business enum |
| `carrier` | varchar | Current carrier |
| `commission_rate` | float | Expected commission rate |
| `expected_commission` | currency | Expected commission amount |
| `last_contact_date` | date | Last outreach date |
| `last_contact_method` | enum | `Email`, `Call`, `Text`, `In person` |
| `lost_reason` | enum | `Price`, `Coverage`, `Unresponsive`, `Moved carrier`, `Other` |

---

## Commission (35 fields)

| Field | Type | Notes |
|-------|------|-------|
| `commissionType` | enum | Type of commission |
| `commissionRate` | float | Commission rate (decimal) |
| `estimatedCommission` | currency | Expected commission amount |
| `effectiveDate` | date | When commission becomes effective |
| `expectedPaymentDate` | date | When payment is expected |
| `carrier` | varchar | Carrier paying commission |
| `commissionNotes` | text | Free-text notes |
| `ledgerExternalId` | varchar | External ledger reference |
| `ledgerKey` | varchar | Ledger lookup key |
| `ledgerPayloadHash` | varchar | Hash for dedup |

---

## Task (13 fields)

| Field | Type | Notes |
|-------|------|-------|
| `name` | varchar | Task title |
| `status` | enum | `Not Started`, `Started`, `Completed`, `Cancelled`, `Deferred` |
| `dateStart` | datetime | Start date/time |
| `dateEnd` | datetime | Due date/time |
| `taskType` | enum | Task category |
| `taskSource` | enum | Where the task originated |
| `urgency` | enum | `Critical`, `High`, `Medium`, `Low` |
| `automationKey` | varchar | Key for automated task matching (read-only) |
| `syncSource` | enum | Sync origin system |
| `momentumLastSynced` | datetime | Last NowCerts Momentum sync |
| `momentumTaskId` | varchar | NowCerts Momentum task ID |
| `sourceActivityLogId` | varchar | Link to originating ActivityLog |
| `triageReason` | text | Reason for triage |
| `triageSummary` | text | Triage summary |

---

## Case (service requests — live 2026-07-10)

Service-request tickets (COI, endorsements, vehicle changes, etc.). One Case per
request, tied to a client Account. Written back to the NowCerts task ledger daily
(7pm ET) by `--espo-writeback`, idempotent via `momentumTaskId`.

| Field | Type | Notes |
|-------|------|-------|
| `name` | varchar | Short description of the request |
| `number` | autoincrement | Case number |
| `status` | enum | `New`, `In Progress`, `Pending`, `Closed`, `Cancelled` (default `New`) |
| `type` | enum | **Service Request Type** — 14 options: Certificate of Insurance (COI), Add Vehicle, Remove Vehicle / Delete Unit, Add Driver, Endorsement / Policy Change, Certificate Holder Add, Mortgagee Change, Lienholder Update, Auto ID Card, Billing / Payment, Cancellation Request, Renewal Review, Claim / FNOL, Other |
| `priority` | enum | `Low`, `Normal`, `High`, `Urgent` |
| `description` | text | Request detail |
| `account` | link | The client (belongsTo Account) — the write-back client link |
| `contacts` / `contact` | link | Related contact(s) |
| `momentumTaskId` | varchar | NowCerts task database_id — Espo→AMS write-back dedup key (writable; set by Hermes) |
| `momentumLastSynced` | datetime | Last write-back to the NowCerts ledger |

---

## ActivityLog (27 fields)

| Field | Type | Notes |
|-------|------|-------|
| `activityType` | enum | Type of activity |
| `dateTime` | datetime | When the activity occurred |
| `direction` | enum | Inbound/outbound |
| `changeSummary` | text | Description of the change |
| `changeType` | enum | Category of change |
| `changeEffectiveDate` | date | When the change takes effect |
| `classification` | enum | Activity classification |
| `duration` | int | Duration in minutes |
| `followUpTask` | varchar | Follow-up task reference |
| `loggedBy` | varchar | Who logged the activity |
| `notes` | text | Free-text notes |

---

## Key Enumerations (Cross-Entity)

### Line of Business
Used on Opportunity (`lineOfBusiness`), Policy (`line_of_business`), Renewal (`line_of_business`):

`Commercial Auto` | `Transportation / Trucking` | `General Liability` | `Workers Comp` | `Commercial Property` | `BOP` | `Professional Liability` | `Umbrella` | `Builders Risk` | `Inland Marine` | `Personal Auto` | `Homeowners` | `Renters` | `Condo` | `Dwelling Fire` | `Motorcycle` | `Boat` | `RV` | `Life` | `Health` | `Medicare` | `Group Benefits` | `Garagekeepers` | `Commercial Package` | `Other`
