// Renewal OS rules — keep in lockstep with hermes/renewals/operating.py.
// Stored CRM value is still Desk_Stage / Stage. Labels are UI only.

export const DESK_STAGES = [
  "Identified",
  "Outreach Sent",
  "Quote Requested",
  "Proposal Sent",
  "Negotiating",
  "Closed",
];

export const OPERATING_STAGES = [
  { stage: "Identified", label: "Review Account" },
  { stage: "Outreach Sent", label: "Pre-Renewal Outreach" },
  { stage: "Quote Requested", label: "Market Renewal" },
  { stage: "Proposal Sent", label: "Build Renewal Options" },
  { stage: "Negotiating", label: "Present Renewal" },
  { stage: "Closed", label: "Close Renewal" },
];

export const OS_DISPOSITIONS = [
  { code: "renewed", label: "Renewed" },
  { code: "rewritten", label: "Rewritten" },
  { code: "rewritten", label: "Marketed" },
  { code: "do_not_renew", label: "Non-Renewed" },
  { code: "do_not_renew", label: "Cancelled" },
  { code: "lost_price", label: "Lost to Competitor" },
];

export const SCORECARD_RAILS = [
  { key: "account_reviewed", label: "Account Reviewed", rank: 0 },
  { key: "outreach_completed", label: "Outreach Completed", rank: 1 },
  { key: "markets_requested", label: "Markets Requested", rank: 2 },
  { key: "quotes_received", label: "Quotes Received", rank: 2 },
  { key: "proposal_pending", label: "Proposal Pending", rank: 3 },
  { key: "client_decision_pending", label: "Client Decision Pending", rank: 4 },
  { key: "closed", label: "Closed", rank: 5 },
];

export const CHECKPOINTS = [
  { key: "verify_customer_info", title: "Verify customer info", stage: "Identified", required: true },
  { key: "verify_policy_info", title: "Verify policy info", stage: "Identified", required: true, aliases: ["Pull the expiring declaration and review exposures", "Pull renewal declaration & review exposures"] },
  { key: "review_claims_history", title: "Review claims history", stage: "Identified", required: true },
  { key: "review_renewability", title: "Review renewability", stage: "Identified", required: true },
  { key: "review_current_premium", title: "Review current premium", stage: "Identified", required: true },
  { key: "review_renewal_timeline", title: "Review renewal timeline", stage: "Identified", required: true },
  { key: "send_questionnaire", title: "Send questionnaire", stage: "Outreach Sent", required: false },
  { key: "gather_exposure_changes", title: "Gather exposure changes", stage: "Outreach Sent", required: false },
  { key: "verify_contact_info", title: "Verify contact info", stage: "Outreach Sent", required: false },
  { key: "record_customer_response", title: "Record customer response", stage: "Outreach Sent", required: true },
  { key: "request_carrier_terms", title: "Request carrier terms", stage: "Quote Requested", required: false, aliases: ["Request renewal terms from the carrier", "Request renewal terms from carrier"] },
  { key: "request_alternative_quotes", title: "Request alternative quotes", stage: "Quote Requested", required: false },
  { key: "record_carrier_responses", title: "Record carrier responses", stage: "Quote Requested", required: true },
  { key: "follow_up_pending_markets", title: "Follow up pending markets", stage: "Quote Requested", required: false },
  { key: "analyze_carrier_response", title: "Analyze carrier response", stage: "Proposal Sent", required: false },
  { key: "compare_alternatives", title: "Compare alternatives", stage: "Proposal Sent", required: false },
  { key: "review_coverage_differences", title: "Review coverage differences", stage: "Proposal Sent", required: false },
  { key: "prepare_recommendations", title: "Prepare recommendations / proposal package", stage: "Proposal Sent", required: true, aliases: ["Build the renewal options and premium-change explanation", "Prepare renewal options / comparison"] },
  { key: "deliver_proposal", title: "Deliver proposal", stage: "Negotiating", required: false, aliases: ["Send the renewal review and get the client's decision", "Send renewal review to client"] },
  { key: "contact_customer", title: "Contact customer", stage: "Negotiating", required: false },
  { key: "record_customer_selection", title: "Record customer selection", stage: "Negotiating", required: true },
  { key: "record_final_premium", title: "Record final premium", stage: "Closed", required: false, aliases: ["Enter Premium Renewal and mark Won or Lost", "Update AMS (NowCerts) & file worksheet"] },
  { key: "record_disposition", title: "Record disposition", stage: "Closed", required: true },
  { key: "record_bound_carrier", title: "Record bound carrier", stage: "Closed", required: false },
  { key: "record_effective_date", title: "Record effective date", stage: "Closed", required: false },
  { key: "queue_ams_update", title: "Queue AMS update", stage: "Closed", required: false },
];

const COMPLETE = new Set(["complete", "completed", "done", "closed", "true", "1"]);

export function storedDeskStage(row) {
  const raw = row && (row.Desk_Stage || row.Stage || row.desk_stage);
  if (raw && typeof raw === "object") return String(raw.name || raw.value || "Identified");
  const label = String(raw || "").trim();
  return label || "Identified";
}

export function operatingLabel(stage) {
  const hit = OPERATING_STAGES.find((row) => row.stage === storedDeskStage({ Stage: stage }));
  return hit ? hit.label : storedDeskStage({ Stage: stage });
}

export function normalizeStatus(value) {
  const raw = String(value || "").trim();
  if (!raw) return "Not Started";
  const low = raw.toLowerCase();
  if (COMPLETE.has(low)) return "Complete";
  if (low.includes("block")) return "Blocked";
  if (low.includes("wait") || low.includes("pending")) return "Waiting";
  if (low.includes("progress") || low.includes("working")) return "In Progress";
  return "Not Started";
}

export function isComplete(status) {
  return normalizeStatus(status) === "Complete";
}

function aliasMap() {
  const out = {};
  CHECKPOINTS.forEach((spec) => {
    out[spec.title.toLowerCase()] = spec.key;
    out[spec.key.toLowerCase()] = spec.key;
    (spec.aliases || []).forEach((alias) => {
      out[alias.toLowerCase()] = spec.key;
    });
  });
  return out;
}

const ALIAS_TO_KEY = aliasMap();

export function checkpointKeyForTitle(title) {
  return ALIAS_TO_KEY[String(title || "").trim().toLowerCase()] || null;
}

export function statesFromTasks(tasks) {
  const out = {};
  (tasks || []).forEach((task) => {
    const key = checkpointKeyForTitle(task.Subject || task.key);
    if (!key) return;
    const status = normalizeStatus(task.Status || task.status);
    if (out[key] && isComplete(out[key].status) && !isComplete(status)) return;
    const owner = task.Owner || task.owner;
    out[key] = {
      key,
      status,
      owner: owner && typeof owner === "object" ? owner.name || owner.email : owner,
      due_date: task.Due_Date || task.due_date || null,
      completed_at: task.Closed_Time || task.completed_at || null,
      notes: task.Description || task.notes || "",
      task_id: task.id || task.task_id || null,
      title: task.Subject || key,
    };
  });
  return out;
}

export function remainingRequired(stage, states) {
  const stored = storedDeskStage({ Stage: stage });
  return CHECKPOINTS.filter((spec) => spec.stage === stored && spec.required).filter(
    (spec) => !isComplete((states && states[spec.key] && states[spec.key].status) || "")
  ).map((spec) => spec.key);
}

export function stageRank(stage) {
  const idx = DESK_STAGES.indexOf(storedDeskStage({ Stage: stage }));
  return idx < 0 ? 0 : idx;
}

function railState(done, active) {
  if (done) return "done";
  if (active) return "active";
  return "empty";
}

export function scorecard(stage, states) {
  const stored = storedDeskStage({ Stage: stage });
  const rank = stageRank(stored);
  const st = states || {};
  const stageDone = (atRank, name) => {
    if (rank > atRank) return true;
    if (rank < atRank) return false;
    return remainingRequired(name, st).length === 0;
  };
  const checkpointDone = (key, pastRank) => rank > pastRank || isComplete((st[key] || {}).status);
  const rails = {
    account_reviewed: railState(stageDone(0, "Identified"), rank === 0),
    outreach_completed: railState(stageDone(1, "Outreach Sent"), rank === 1),
    markets_requested: railState(checkpointDone("request_carrier_terms", 2) || rank > 2, rank === 2),
    quotes_received: railState(checkpointDone("record_carrier_responses", 2) || rank > 2, rank === 2),
    proposal_pending: railState(stageDone(3, "Proposal Sent"), rank === 3),
    client_decision_pending: railState(stageDone(4, "Negotiating"), rank === 4),
    closed: railState(stored === "Closed", false),
  };
  if (rank > 2) {
    rails.markets_requested = "done";
    rails.quotes_received = "done";
  }
  let completed = CHECKPOINTS.filter(
    (spec) => isComplete((st[spec.key] || {}).status) || stageRank(spec.stage) < rank
  ).length;
  let health = Math.round((100 * completed) / CHECKPOINTS.length);
  if (stored === "Closed") {
    health = 100;
    Object.keys(rails).forEach((key) => {
      rails[key] = "done";
    });
  }
  const mark = { done: "✅", active: "🟨", empty: "⬜" };
  return {
    health,
    stage: stored,
    label: operatingLabel(stored),
    remaining: remainingRequired(stored, st),
    rails: SCORECARD_RAILS.map((spec) => ({
      key: spec.key,
      label: spec.label,
      state: rails[spec.key],
      mark: mark[rails[spec.key]],
    })),
  };
}

export function nextStage(stage) {
  const rank = stageRank(stage);
  return rank >= DESK_STAGES.length - 1 ? null : DESK_STAGES[rank + 1];
}

export function completeCheckpoint(stage, states, key, opts) {
  const actor = (opts && opts.actor) || "user";
  const disposition = opts && opts.disposition;
  const stored = storedDeskStage({ Stage: stage });
  const spec = CHECKPOINTS.find((row) => row.key === key);
  const nextStates = { ...(states || {}) };
  if (!spec) {
    return { ok: false, advanced: false, desk_stage: stored, remaining: remainingRequired(stored, nextStates), error: `unknown checkpoint ${key}` };
  }
  nextStates[key] = { ...(nextStates[key] || { key }), status: "Complete" };
  const remaining = remainingRequired(stored, nextStates);
  const base = { ok: true, advanced: false, desk_stage: stored, remaining, states: nextStates, error: null };
  if (actor !== "user" || remaining.length) {
    return { ...base, scorecard: scorecard(stored, nextStates) };
  }
  const nxt = nextStage(stored);
  if (!nxt) return { ...base, scorecard: scorecard(stored, nextStates) };
  const onCurrent = spec.stage === stored;
  const closing = nxt === "Closed" && key === "record_disposition";
  if (!onCurrent && !closing) return { ...base, scorecard: scorecard(stored, nextStates) };
  if (nxt === "Closed" && !["renewed", "rewritten", "lost_price", "lost_coverage", "lost_no_response", "do_not_renew"].includes(String(disposition || ""))) {
    return { ...base, error: "Closed requires a Disposition", scorecard: scorecard(stored, nextStates) };
  }
  const here = stageRank(stored);
  const there = stageRank(nxt);
  if (there !== here + 1) {
    return { ok: false, advanced: false, desk_stage: stored, remaining, states: nextStates, error: "cannot skip desk stages", scorecard: scorecard(stored, nextStates) };
  }
  return {
    ok: true,
    advanced: true,
    desk_stage: nxt,
    remaining: remainingRequired(nxt, nextStates),
    states: nextStates,
    error: null,
    scorecard: scorecard(nxt, nextStates),
  };
}

export function checkpointsForStage(stage, states) {
  const stored = storedDeskStage({ Stage: stage });
  const st = states || {};
  return CHECKPOINTS.filter((spec) => spec.stage === stored).map((spec) => ({
    ...spec,
    status: normalizeStatus((st[spec.key] || {}).status),
    owner: (st[spec.key] || {}).owner || "CSR",
    due_date: (st[spec.key] || {}).due_date || null,
    notes: (st[spec.key] || {}).notes || "",
    task_id: (st[spec.key] || {}).task_id || null,
  }));
}

export function nextRequiredAction(stage, states) {
  const stored = storedDeskStage({ Stage: stage });
  if (stored === "Closed") return { key: "done", title: "Renewal closed", owner_role: "csr" };
  const remaining = remainingRequired(stored, states);
  if (remaining.length) {
    const spec = CHECKPOINTS.find((row) => row.key === remaining[0]);
    return { key: spec.key, title: spec.title, owner_role: "csr" };
  }
  const nxt = nextStage(stored);
  return { key: "advance", title: nxt ? `Advance to ${operatingLabel(nxt)}` : "Close renewal", next_stage: nxt, owner_role: "csr" };
}

const api = {
  DESK_STAGES,
  OPERATING_STAGES,
  OS_DISPOSITIONS,
  SCORECARD_RAILS,
  CHECKPOINTS,
  storedDeskStage,
  operatingLabel,
  scorecard,
  completeCheckpoint,
  statesFromTasks,
  remainingRequired,
  checkpointsForStage,
  nextRequiredAction,
};

export default api;
if (typeof module === "object" && module.exports) {
  module.exports = api;
}
