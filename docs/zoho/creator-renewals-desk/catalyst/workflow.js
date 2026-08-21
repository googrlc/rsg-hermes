export const WORK_STEPS = [
  { stage: 'Identified', label: 'Review account', taskKey: 'Identified' },
  { stage: 'Outreach Sent', label: 'Request terms', taskKey: 'Outreach Sent' },
  { stage: 'Quote Requested', label: 'Build options', taskKey: 'Quote Requested' },
  { stage: 'Proposal Sent', label: 'Contact client', taskKey: 'Proposal Sent' },
  { stage: 'Negotiating', label: 'Close renewal', taskKey: 'Negotiating' },
];

export function workStepIndex(stage) {
  const value = String(stage || 'Identified').trim() || 'Identified';
  if (value === 'Closed') return WORK_STEPS.length;
  const index = WORK_STEPS.findIndex((step) => step.stage === value);
  return index < 0 ? 0 : index;
}

export function currentWorkStep(stage) {
  const index = workStepIndex(stage);
  if (index >= WORK_STEPS.length) return null;
  return WORK_STEPS[index];
}

export function taskIsDone(status) {
  return ['completed', 'closed', 'complete', 'completed in crm'].includes(
    String(status || '')
      .trim()
      .toLowerCase(),
  );
}

export function statusChip(row) {
  if (!row) return { label: 'Not started', tone: 'idle' };
  if (row.Locked || row.Desk_Stage === 'Closed') return { label: 'Completed', tone: 'done' };
  const days = row.Days_To_Expiration;
  if (typeof days === 'number' && days < 0) return { label: 'Past due', tone: 'critical' };
  const risk = String(row.Risk_Status || '').toUpperCase();
  if (risk === 'CRITICAL' || row.Window_Bucket === 'past_due') return { label: 'Past due', tone: 'critical' };
  if (risk === 'AT_RISK' || row.Window_Bucket === '30') return { label: 'Needs action', tone: 'warn' };
  return { label: 'In progress', tone: 'progress' };
}

export function daysLabel(days) {
  if (days == null || days === '') return '—';
  const n = Number(days);
  if (Number.isNaN(n)) return '—';
  if (n < 0) return `${Math.abs(n)} days past`;
  if (n === 0) return 'Due today';
  return `${n} days left`;
}
