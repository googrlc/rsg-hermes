"""RSG renewal automation.

Renewal work runs off ``renewal_candidates`` (rebuilt from the live NowCerts
book by ``hermes --renewal-refresh``, per the eligibility rule in
``eligibility.py``). The Zoho Catalyst Renewals Desk is the CRM workstation;
approved AMS instructions are staged in ``outbound_sync_queue`` and applied by
``executor.py`` under Job Contract v2.

The n8n-WF1-plus-EspoCRM-Task lane this package started as -- a cron sweep that
minted Espo Tasks and a webhook that reacted to their completion -- is gone;
cases and tasks now live in ``agency_crm_cases`` / ``agency_crm_tasks`` (see
``cases.py``).
"""
