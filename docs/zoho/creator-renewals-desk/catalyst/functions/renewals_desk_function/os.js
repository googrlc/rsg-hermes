// Catalyst OS attach helpers for renewals_desk_function.
// Merge into the live Advanced I/O function. Do not stand up a second desk.
// Hermes remains the only NowCerts writer.
// Completing a checkpoint does NOT advance Desk_Stage. Continue / POST /next
// still advances when the stage CRM task is Completed (taskIsDone).
// Persist Checkpoint_State on the Zoho Renewals record. No Supabase OS table.

const os = require("./operating");

function lookupId(value) {
  if (!value) return "";
  if (typeof value === "object") return String(value.id || "");
  return String(value);
}

function hasPipelineDeal(row) {
  return Boolean(lookupId(row && (row.Deal_Id || row.Related_Deal)));
}

function attachOsToRenewal(renewal, tasks) {
  const stage = os.storedDeskStage(renewal || {});
  const states = os.mergeCheckpointStates(renewal || {}, tasks || []);
  return {
    scorecard: os.scorecard(stage, states),
    checkpoints: os.checkpointsForStage(stage, states),
    next: os.nextRequiredAction(stage, states),
    desk_stage: stage,
    operating_label: os.operatingLabel(stage),
    states,
  };
}

function attachOsToDeskRow(row) {
  const osPayload = attachOsToRenewal(row, row.tasks || []);
  return Object.assign({}, row, {
    Desk_Stage: osPayload.desk_stage,
    os: osPayload,
  });
}

function attachOsToCard(card) {
  const renewal = card.renewal || {};
  const osPayload = attachOsToRenewal(renewal, card.tasks || []);
  renewal.Desk_Stage = osPayload.desk_stage;
  return Object.assign({}, card, {
    renewal,
    os: osPayload,
    next: card.next || osPayload.next,
  });
}

function completeCheckpointOnCard(card, key, body) {
  const renewal = card.renewal || {};
  const stage = os.storedDeskStage(renewal);
  const states = os.mergeCheckpointStates(renewal, card.tasks || []);
  const result = os.completeCheckpoint(stage, states, key, {
    actor: (body && body.actor) || "user",
    disposition: body && body.disposition,
  });
  // Caller must:
  // 1. PUT Renewals Checkpoint_State = result.checkpoint_state (desk-owned JSON)
  // 2. If result.task_complete, mark the matching CRM task Status=Completed
  //    (subject = result.title or result.aliases). That is the live taskIsDone gate.
  // 3. Do NOT write Desk_Stage here. Continue / POST /next still advances.
  // 4. Do not write NowCerts.
  return result;
}

function attachOsToDeskPayload(payload) {
  const rows = (payload.rows || []).map(attachOsToDeskRow);
  const linked = rows.filter(hasPipelineDeal);
  const leftovers = rows.length - linked.length;
  return Object.assign({}, payload, {
    rows: linked,
    leftovers,
    leftover_reason: leftovers
      ? "Desk rows without a Renewals pipeline Deal (Deal_Id / Related_Deal) are hidden. Run hermes --sync-zoho-renewals."
      : "",
  });
}

module.exports = {
  attachOsToRenewal,
  attachOsToDeskRow,
  attachOsToCard,
  attachOsToDeskPayload,
  completeCheckpointOnCard,
  hasPipelineDeal,
};
