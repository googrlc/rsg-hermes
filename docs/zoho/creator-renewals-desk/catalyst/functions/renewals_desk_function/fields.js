// Ensure desk-owned CRM fields on the live Renewals module.
// Merge into renewals_desk_function. Call once on cold start / /api/health.
// Do not create Hermes_Renewal_ID (label DUPLICATE_DATA, no api_name).
// Do not create Deal_Id — live Catalyst derives it from Related_Deal.

const DESK_FIELDS = [
  {
    field_label: "Checkpoint State",
    api_name: "Checkpoint_State",
    data_type: "textarea",
  },
  {
    field_label: "Related Deal",
    api_name: "Related_Deal",
    data_type: "lookup",
    lookup: { module: { api_name: "Deals" } },
  },
];

function fieldNames(row) {
  return {
    api: String((row && (row.api_name || row.apiName)) || "").trim(),
    label: String((row && (row.field_label || row.display_label || row.fieldLabel)) || "")
      .trim()
      .toLowerCase(),
  };
}

function alreadyPresent(existing, spec) {
  const wantedApi = String(spec.api_name || "").trim();
  const wantedLabel = String(spec.field_label || "").trim().toLowerCase();
  return (existing || []).some((row) => {
    const names = fieldNames(row);
    return (wantedApi && names.api === wantedApi) || (wantedLabel && names.label === wantedLabel);
  });
}

function isDuplicateFieldError(err) {
  const text = String((err && err.message) || err || "");
  return /duplicate_data/i.test(text) || /already exists/i.test(text);
}

async function ensureRenewalDeskFields(crmRequest) {
  if (typeof crmRequest !== "function") {
    throw new Error("ensureRenewalDeskFields requires a CRM request function");
  }
  const listed = await crmRequest("GET", "settings/fields", { qs: { module: "Renewals" } });
  const existing = (listed && (listed.fields || listed.data)) || [];
  const created = [];
  for (const spec of DESK_FIELDS) {
    if (alreadyPresent(existing, spec)) continue;
    try {
      await crmRequest("POST", "settings/fields", {
        qs: { module: "Renewals" },
        body: { fields: [spec] },
      });
      created.push(spec.api_name);
      existing.push(spec);
    } catch (err) {
      if (isDuplicateFieldError(err)) continue;
      throw err;
    }
  }
  return {
    created,
    relatedDeal: "Related_Deal",
    checkpointState: "Checkpoint_State",
    skippedHermesRenewalId: true,
    skippedDealId: true,
  };
}

const api = { DESK_FIELDS, ensureRenewalDeskFields, alreadyPresent, isDuplicateFieldError };
module.exports = api;