// Catalyst OS attach helpers for renewals_desk_function.
// Merge into the live Advanced I/O function. Do not stand up a second desk.
// Hermes remains the only NowCerts writer. Completing a checkpoint may advance
// Desk_Stage / Stage one step only when required checkpoints are done.

const os = require("./operating");

function attachOsToRenewal(renewal, tasks) {
  const stage = os.storedDeskStage(renewal || {});
  const states = os.statesFromTasks(tasks || []);
  return {
    scorecard: os.scorecard(stage, states),
    checkpoints: os.checkpointsForStage(stage, states),
    next: os.nextRequiredAction(stage, states),
    desk_stage: stage,
    operating_label: os.operatingLabel(stage),
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
    next: osPayload.next,
  });
}

function completeCheckpointOnCard(card, key, body) {
  const renewal = card.renewal || {};
  const stage = os.storedDeskStage(renewal);
  const states = os.statesFromTasks(card.tasks || []);
  const result = os.completeCheckpoint(stage, states, key, {
    actor: (body && body.actor) || "user",
    disposition: body && body.disposition,
  });
  return result;
}

module.exports = {
  attachOsToRenewal,
  attachOsToDeskRow,
  attachOsToCard,
  completeCheckpointOnCard,
};
