"""RSG renewal task automation.

Turns Renewal records (auto-created by n8n WF1) into actionable, fully-specified
tasks for Gretchen, and closes the loop when she completes them: files the
worksheet to the client folder and routes won/lost notifications to Slack.

Entry points:
- sweep.run()        -> cron job that creates the tasks (hermes --renewal-sweep)
- complete.handle()  -> POST /renewals/complete webhook handler

This package is the reference shape for future Hermes task-automation modules:
a cron `sweep` that mints EspoCRM Tasks from a source entity, a `card` that
renders the self-contained task packet, and a `complete` webhook handler that
reacts to task completion. New modules should mirror this layout.
"""
