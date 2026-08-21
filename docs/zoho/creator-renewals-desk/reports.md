# Integration reports

All reports are Zoho CRM integration reports inside Creator. They do not copy
rows into Creator forms.

## Worklist (`REPORT_WORKLIST`)

- Module: **Renewals**
- Criteria: `Dismissed` is false (or empty) **and** (`Related_Deal` or `Deal_Id`) is not empty.
  The desk is a 1:1 projection of Deals on the CRM **Renewals** pipeline.
  Hermes (`hermes --sync-zoho-renewals`) creates the missing side either way.
- Columns: Client_Name, Policy_Number, Carrier, Line_of_Business, Expiration_Date, Days_To_Expiration, Window_Bucket, Premium_Current, Premium_Renewal, Increase_Percent, Risk_Status, Desk_Stage, Recommended_Action
- Sort: Expiration_Date ascending
- Filters (quick): Window_Bucket, Risk_Status, Desk_Stage, Line_of_Business
- Row click: open Page `Card` with that record id
- Empty text: "No eligible renewals in this filter. Check Needs verification if the book looks short; check Last_Synced on Policies if last night's upsert did not run."

## Needs verification (`REPORT_NEEDS_VERIFICATION`)

- Module: **Renewal_Events**
- Criteria: `Eligibility` = `needs_verification`
- Columns: Client_Name, Policy_Number, Renewal_Event_Date, Eligibility_Reason, Normalized_Status, Branch, Segment, NowCerts_Insured_GUID, NowCerts_Policy_GUID
- Sort: Renewal_Event_Date ascending
- Purpose: identity reconciliation — match the event to a Policy / Account. Same idea as unmatched commission statement lines. Do not invent a policy.

## AMS pending (`REPORT_AMS_PENDING`)

- Module: **AMS_Write_Queue**
- Criteria: `Object_Type` = `renewal` AND (`Status` = `needs_approval` OR (`Status` = `queued` AND `Approved_By` is empty))
- Columns: Name, Related_Renewal, Action, Status, Approved_By, Approved_At, Last_Error, Created_Time
- Custom button **Approve**: sets Approved_By, Approved_At, Status=`queued`
- Payload is display-only. Edits go through the Card action form.

## AMS failed (`REPORT_AMS_FAILED`)

- Module: **AMS_Write_Queue**
- Criteria: `Object_Type` = `renewal` AND `Status` in (`failed`, `dead`)
- Columns: Name, Related_Renewal, Action, Status, Attempt_Count, Last_Error, Approved_By
- No silent retry from Creator. Hermes already backs off. A human re-queues by creating a new approved job from the Card.

## Open tasks (Card related list)

- Module: **Tasks**
- Criteria: Subject in the five default titles, related to this Account / Renewal
- Columns: Subject, Status, Due_Date, Owner
