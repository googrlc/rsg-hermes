# EspoCRM Entity Schema

## Core Entities

| Entity | Meaning | Key Fields | Relationships |
|--------|---------|------------|---------------|
| **Account** | Companies & individuals (clients, prospects, carriers) | `name`, `account_status`, `account_type`, `industry`, `annual_premium`, `fein`, `assignedUserName` | has many Contacts, Opportunities (via Contact), Policies, Renewals, Commissions, ActivityLogs, Tasks, ClientNotes |
| **Contact** | People attached to an Account | `name`, `emailAddress`, `phoneNumber`, `contactType`, `clientType`, `householdRole`, `dateOfBirth` | belongs to Account(s) (many-to-many), has many Policies, Renewals, Commissions, ActivityLogs |
| **Lead** | Unqualified prospect not yet converted | `name`, `emailAddress`, `phoneNumber`, `source`, `insuranceInterest`, `priority`, `aiSummary`, `estimatedPremium` | optional link to source Opportunity |
| **Opportunity** | Revenue pipeline item (quote, deal) | `name`, `stage`, `amount`, `lineOfBusiness`, `businessType`, `closeDate`, `probability`, `assignedUserName` | has many Commissions, Policies, Quotes, OpportunityDrivers, OpportunityVehicles; optional recycledLead |
| **Policy** | Bound insurance policy | `policy_number`, `carrier`, `line_of_business`, `effective_date`, `expiration_date`, `premium`, `status`, `amsLockState` | belongs to Account, Contact, carrierAccount; has many Commissions, ActivityLogs, Renewals, Opportunities |
| **Renewal** | Upcoming policy renewal tracked by Project 85 | `stage`, `expiration_date`, `current_premium`, `urgency`, `line_of_business`, `carrier` | belongs to Account, Contact, Policy; optional newPolicy; has many Commissions, Tasks |
| **Commission** | Revenue tracking per policy/opportunity | `commissionType`, `commissionRate`, `estimatedCommission`, `effectiveDate`, `carrier` | belongs to Account, Contact, Opportunity, Policy, Renewal |
| **Task** | Action items and follow-ups | `name`, `status`, `dateStart`, `dateEnd`, `taskType`, `urgency`, `assignedUserName` | parent link (polymorphic to Account, Contact, Lead, Opportunity, etc.) |
| **ActivityLog** | Interaction history (calls, emails, changes) | `activityType`, `dateTime`, `direction`, `changeSummary`, `changeType`, `classification` | belongs to Account, Contact, Policy |
| **Quote** | Premium quote linked to Opportunity | `name` | belongs to Opportunity |

## Supporting Entities

| Entity | Purpose |
|--------|---------|
| **OpportunityDriver** | Driver details for auto/trucking opportunities |
| **OpportunityVehicle** | Vehicle details for auto/trucking opportunities |
| **ClientNote** | Free-text notes attached to an Account |
| **Meeting** / **Call** / **Email** | Standard EspoCRM activity entities linked to Accounts |

## Entity Field Counts (from live schema)

| Entity | Custom Fields |
|--------|--------------|
| Account | 289 |
| Opportunity | 144 |
| Policy | 56 |
| Commission | 35 |
| Contact | 31 |
| ActivityLog | 27 |
| Renewal | 28 |
| Lead | 18 |
| Task | 13 |
| OpportunityDriver | 7 |
| Quote | 4 |
| OpportunityVehicle | 3 |
