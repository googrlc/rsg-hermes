# EspoCRM Entity Relationships

## Relationship Graph

```
Account (Company/Individual)
 ├── Contacts[]           (hasMany)
 ├── Policies[]           (hasMany)
 ├── Renewals[]           (hasMany)
 ├── Commissions[]        (hasMany)
 ├── ActivityLogs[]       (hasMany)
 ├── Tasks[]              (hasChildren)
 ├── ClientNotes[]        (hasMany)
 ├── Calls[]              (hasChildren)
 ├── Emails[]             (hasChildren)
 └── Meetings[]           (hasChildren)

Contact (Person)
 ├── Accounts[]           (hasMany — many-to-many)
 ├── Policies[]           (hasMany)
 ├── Renewals[]           (hasMany)
 ├── Commissions[]        (hasMany)
 └── ActivityLogs[]       (hasMany)

Opportunity (Deal)
 ├── Commissions[]        (hasMany)
 ├── Policies[]           (hasMany)
 ├── Quotes[]             (hasMany)
 ├── OpportunityDrivers[] (hasMany)
 ├── OpportunityVehicles[](hasMany)
 └── recycledLead?        (belongsTo Lead)

Policy (Bound Coverage)
 ├── → Account            (belongsTo)
 ├── → Contact            (belongsTo)
 ├── → carrierAccount     (belongsTo Account)
 ├── → underwriter        (belongsTo Contact)
 ├── Commissions[]        (hasMany)
 ├── ActivityLogs[]       (hasMany)
 ├── Renewals[]           (hasMany)
 └── Opportunities[]      (hasMany)

Renewal
 ├── → Account            (belongsTo)
 ├── → Contact            (belongsTo)
 ├── → Policy             (belongsTo — current)
 ├── → newPolicy          (belongsTo — replacement)
 ├── Commissions[]        (hasMany)
 └── Tasks[]              (hasChildren)

Commission
 ├── → Account            (belongsTo)
 ├── → Contact            (belongsTo)
 ├── → Opportunity        (belongsTo)
 ├── → Policy             (belongsTo)
 └── → Renewal            (belongsTo)
```

## Link Reference Table

| Entity | Link Name | Target Entity | Type | Notes |
|--------|-----------|---------------|------|-------|
| **Account** | `contacts` | Contact | hasMany | People at this company |
| **Account** | `policies` | Policy | hasMany | Policies as policyholder |
| **Account** | `carrierPolicies` | Policy | hasMany | Policies where Account is the carrier |
| **Account** | `renewals` | Renewal | hasMany | Upcoming renewals |
| **Account** | `commissions` | Commission | hasMany | Commission records |
| **Account** | `activityLogs` | ActivityLog | hasMany | Interaction history |
| **Account** | `tasks` | Task | hasChildren | Action items |
| **Account** | `clientNotes` | ClientNote | hasMany | Free-text notes |
| **Account** | `calls` | Call | hasChildren | Phone call records |
| **Account** | `emails` | Email | hasChildren | Email records |
| **Account** | `meetings` | Meeting | hasChildren | Meeting records |
| **Account** | `lastContactBy` | User | belongsTo | Last user to contact |
| **Contact** | `accounts` | Account | hasMany | Many-to-many with accounts |
| **Contact** | `policies` | Policy | hasMany | Policies where Contact is named |
| **Contact** | `renewals` | Renewal | hasMany | Renewal records |
| **Contact** | `commissions` | Commission | hasMany | Commission records |
| **Contact** | `activityLogs` | ActivityLog | hasMany | Interaction history |
| **Contact** | `underwrittenPolicies` | Policy | hasMany | Policies where Contact is underwriter |
| **Lead** | `sourceOpportunity` | Opportunity | belongsTo | Opportunity that generated this lead |
| **Opportunity** | `commissions` | Commission | hasMany | Commission records |
| **Opportunity** | `policies` | Policy | hasMany | Resulting policies |
| **Opportunity** | `quotes` | Quote | hasMany | Premium quotes |
| **Opportunity** | `opportunityDrivers` | OpportunityDriver | hasMany | Driver details (auto/trucking) |
| **Opportunity** | `opportunityVehicles` | OpportunityVehicle | hasMany | Vehicle details (auto/trucking) |
| **Opportunity** | `recycledLead` | Lead | belongsTo | Lead recycled from lost opportunity |
| **Policy** | `account` | Account | belongsTo | Policyholder account |
| **Policy** | `contact` | Contact | belongsTo | Named insured contact |
| **Policy** | `carrierAccount` | Account | belongsTo | Carrier (as Account) |
| **Policy** | `underwriter` | Contact | belongsTo | Underwriting contact |
| **Policy** | `commissions` | Commission | hasMany | Commission records |
| **Policy** | `activityLogs` | ActivityLog | hasMany | Policy change history |
| **Policy** | `renewals` | Renewal | hasMany | Renewal records |
| **Policy** | `renewedFrom` | Renewal | hasMany | Renewals this policy replaced |
| **Policy** | `opportunities` | Opportunity | hasMany | Originating opportunities |
| **Renewal** | `account` | Account | belongsTo | Client account |
| **Renewal** | `contact` | Contact | belongsTo | Named contact |
| **Renewal** | `policy` | Policy | belongsTo | Current policy being renewed |
| **Renewal** | `newPolicy` | Policy | belongsTo | Replacement policy (after renewal) |
| **Renewal** | `commissions` | Commission | hasMany | Expected commissions |
| **Renewal** | `tasks` | Task | hasChildren | Follow-up tasks |
| **Commission** | `account` | Account | belongsTo | Client account |
| **Commission** | `contact` | Contact | belongsTo | Named contact |
| **Commission** | `opportunity` | Opportunity | belongsTo | Source deal |
| **Commission** | `policy` | Policy | belongsTo | Bound policy |
| **Commission** | `renewal` | Renewal | belongsTo | Renewal record |
| **ActivityLog** | `account` | Account | belongsTo | Client account |
| **ActivityLog** | `contact` | Contact | belongsTo | Related contact |
| **ActivityLog** | `policy` | Policy | belongsTo | Related policy |

## Common Traversal Paths

| Goal | Walk Path |
|------|-----------|
| Summarize account history | Account → Contacts → ActivityLogs + Policies → Commissions + Renewals |
| Assess renewal risk | Renewal → Policy → Account → check `account_status`, claim history, premium trend |
| Audit commission for deal | Opportunity → Commissions → compare `estimatedCommission` vs actual |
| Find cross-sell opportunities | Account → Policies (LOBs covered) → compare against full LOB list → identify gaps |
| Check carrier relationship | Account (type=Carrier) → carrierPolicies → see all policies underwritten |
| Contact's full exposure | Contact → Accounts[] → Policies[] → sum premiums per LOB |
