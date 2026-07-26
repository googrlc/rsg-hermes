---
name: dashboard-definitions
description: Dashboard definitions for Lamar's Revenue Command Desk — categories, data sources, filters, columns, sort order, grouping, and metrics for each dashboard type. Use when Lamar asks for a dashboard, pipeline view, or data summary from the CRM.
---

# Dashboard Definitions

The dashboard catalog for Lamar's Revenue Command Desk. Each
dashboard is a defined query against the CRM with specific columns,
filters, and sort order.

## When to use

- "Build my pipeline dashboard."
- "Show stale quotes by estimated revenue."
- "Build today's command dashboard."
- Any request for a data view, summary, or report.

## Dashboard categories

### 1. Today's Command Center

**Purpose:** Lamar's morning cockpit — the five things that matter today.
**Source:** the CRM (Tasks, Opportunities, Renewals)
**Columns:** Action | Client | Line of Business | Stage | Est. Premium | Est. Revenue | Due Date | Priority
**Sort:** Priority (revenue at risk first), then due date
**Filters:** Due date <= today + 7 days, status active
**Metrics:** Total revenue at risk, count of overdue items

### 2. Stale Quotes

**Purpose:** Surface quotes that are leaking revenue.
**Source:** CRM Opportunities (stages: Quoting, Markets Out, Proposal Presented)
**Columns:** Account | Line of Business | Stage | Est. Premium | Est. Revenue | Last Activity | Days Idle | Status
**Sort:** Days idle descending, then estimated revenue descending
**Filters:** Last activity > 3 business days, stage not in (Closed Won, Closed Lost)
**Metrics:** Total revenue at risk, count of stale quotes, avg days idle

### 3. Pipeline by Line of Business

**Purpose:** Show where the revenue is by LOB.
**Source:** CRM Opportunities
**Columns:** Line of Business | Count | Total Est. Premium | Total Est. Revenue | Weighted Revenue
**Sort:** Total est. revenue descending
**Group by:** lineOfBusiness
**Filters:** Stage not in (Closed Won, Closed Lost)
**Metrics:** Total pipeline value, weighted pipeline (by probability)

### 4. Missing Information

**Purpose:** Show what is blocking quote submission.
**Source:** CRM Opportunities with missing info flags
**Columns:** Account | Line of Business | Stage | Missing Items | Est. Premium | Days Blocked
**Sort:** Days blocked descending, then estimated premium descending
**Filters:** Has missing information, stage in (Discovery, Quoting)

### 5. Renewals by Urgency

**Purpose:** Surface renewals at risk, ordered by days to expiration.
**Source:** CRM Renewal records + Policy records
**Columns:** Account | Line of Business | Carrier | Current Premium | Renewal Premium | Increase % | Expiration Date | Days to X-date | Risk Status | Stage
**Sort:** Days to x-date ascending
**Filters:** Expiration date <= today + 90 days
**Metrics:** Total premium up for renewal, avg increase %, count at risk

### 6. High-Premium Opportunities

**Purpose:** Focus on the whale accounts.
**Source:** CRM Opportunities
**Columns:** Account | Line of Business | Stage | Est. Premium | Est. Revenue | Assigned To | Next Follow-Up
**Sort:** Est. premium descending
**Filters:** Est. premium > $5,000, stage not in (Closed Won, Closed Lost)
**Metrics:** Total premium in whale pipeline

### 7. Waiting on Client

**Purpose:** Show what is blocked by client inaction.
**Source:** CRM Tasks (status: Waiting on Client) + linked Opportunities
**Columns:** Account | Line of Business | What is Needed | Days Waiting | Est. Premium | Est. Revenue
**Sort:** Days waiting descending
**Filters:** Task status = Waiting on Client
**Metrics:** Total revenue waiting on client

### 8. COI Open Requests

**Purpose:** Track open certificate requests.
**Source:** CRM Tasks/Notes with COI tag
**Columns:** Account | Holder | Due Date | Special Wording | Status | Days Open
**Sort:** Due date ascending
**Filters:** Task type = COI, status not Completed

### 9. Lost / Dead Opportunities

**Purpose:** Learn from losses and identify re-marketing targets.
**Source:** CRM Opportunities (stage: Closed Lost)
**Columns:** Account | Line of Business | Est. Premium | Reason Lost | Date Closed | Assigned To
**Sort:** Est. premium descending
**Filters:** Stage = Closed Lost, close date in last 90 days
**Metrics:** Total lost premium, top reason for loss

### 10. Cross-Sell Opportunities

**Purpose:** Find revenue hiding in existing accounts.
**Source:** CRM Accounts + Opportunities (gap analysis)
**Columns:** Account | Has Auto | Has Home | Has Umbrella | Has Life | Has WC | Cross-Sell Target | Est. Revenue
**Sort:** Est. revenue descending
**Filters:** Account status = Active, missing LOB identified

## Output format for each dashboard

1. Dashboard Name
2. Purpose
3. Data Source (the CRM entity)
4. Filters
5. Columns
6. Sort Order
7. Grouping
8. Metrics
9. Hermes Command (the lookup/report command to run)
10. Example Output Table (with sample rows)
