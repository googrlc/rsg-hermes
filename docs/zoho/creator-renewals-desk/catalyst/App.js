import { useEffect, useMemo, useState } from "react";
import "./desk.css";
import {
  OS_DISPOSITIONS,
  checkpointsForStage,
  completeCheckpoint,
  nextRequiredAction,
  operatingLabel,
  scorecard,
  statesFromTasks,
  storedDeskStage,
} from "./operating";

const API = "/server/renewals_desk_function";

const KPI_TILES = [
  { key: "90", label: "90 days", group: "window" },
  { key: "60", label: "60 days", group: "window" },
  { key: "30", label: "30 days", group: "window" },
  { key: "personal", label: "Personal", group: "window" },
  { key: "past_due", label: "Past due", group: "window" },
  { key: "CRITICAL", label: "CRITICAL", group: "risk" },
  { key: "AT_RISK", label: "AT_RISK", group: "risk" },
  { key: "SAFE", label: "SAFE", group: "risk" },
  { key: "needs_verification", label: "Needs verification", group: "recon" },
  { key: "ams_pending", label: "Pending AMS", group: "ams" },
  { key: "ams_failed", label: "Failed AMS", group: "ams" },
];

const FIXTURES = [
  {
    id: "acme-bop",
    type: "Commercial",
    Client_Name: "Acme Trucking LLC",
    Policy_Number: "BOP-4411",
    Line_of_Business: "BOP",
    Carrier: "Travelers",
    Expiration_Date: "2026-09-15",
    Days_To_Expiration: 25,
    Window_Bucket: "30",
    Desk_Stage: "Identified",
    Risk_Status: "AT_RISK",
    Premium_Current: 12400,
    Premium_Renewal: null,
    Strategy_Notes: "Incumbent usually non-renews fleet radius over 200 miles. Confirm.",
    Related_Deal: { id: "deal-acme-bop" },
    Deal_Id: { id: "deal-acme-bop" },
    Owner: { name: "Gretchen" },
    tasks: [{ Subject: "Pull the expiring declaration and review exposures", Status: "Completed", Owner: { name: "Gretchen" } }],
  },
  {
    id: "wilson-home",
    type: "Personal",
    Client_Name: "James Wilson",
    Policy_Number: "HO-2291",
    Line_of_Business: "Homeowners",
    Carrier: "Safeco",
    Expiration_Date: "2026-09-18",
    Days_To_Expiration: 28,
    Window_Bucket: "personal",
    Desk_Stage: "Outreach Sent",
    Risk_Status: "SAFE",
    Premium_Current: 2100,
    Related_Deal: { id: "deal-wilson-home" },
    Deal_Id: { id: "deal-wilson-home" },
    Owner: { name: "Gretchen" },
    tasks: [
      { Subject: "Pull the expiring declaration and review exposures", Status: "Completed" },
      { Subject: "Record customer response", Status: "Not Started" },
    ],
  },
  {
    id: "acme-wc",
    type: "Commercial",
    Client_Name: "Acme Trucking LLC",
    Policy_Number: "WC-1188",
    Line_of_Business: "Workers Comp",
    Carrier: "Travelers",
    Expiration_Date: "2026-09-08",
    Days_To_Expiration: 18,
    Window_Bucket: "30",
    Desk_Stage: "Quote Requested",
    Risk_Status: "CRITICAL",
    Premium_Current: 8200,
    Related_Deal: { id: "deal-acme-wc" },
    Deal_Id: { id: "deal-acme-wc" },
    tasks: [{ Subject: "Request renewal terms from the carrier", Status: "Completed" }],
    statusKind: "attention",
  },
  {
    id: "wilson-umb",
    type: "Personal",
    Client_Name: "James Wilson",
    Policy_Number: "UMB-19",
    Line_of_Business: "Umbrella",
    Carrier: "Safeco",
    Expiration_Date: "2026-08-12",
    Days_To_Expiration: -9,
    Window_Bucket: "past_due",
    Desk_Stage: "Negotiating",
    Risk_Status: "AT_RISK",
    Premium_Current: 640,
    Related_Deal: { id: "deal-wilson-umb" },
    Deal_Id: { id: "deal-wilson-umb" },
    tasks: [{ Subject: "Send the renewal review and get the client's decision", Status: "Completed" }],
    statusKind: "overdue",
  },
];

function accountType(row) {
  const lob = String(row.Line_of_Business || row.renewal || "");
  if (row.type) return row.type;
  return /home|auto|umbrell|condo|dwelling|personal/i.test(lob) ? "Personal" : "Commercial";
}

function statusKind(row) {
  if (row.statusKind) return row.statusKind;
  const days = Number(row.Days_To_Expiration);
  if (!Number.isNaN(days) && days < 0) return "overdue";
  if (!Number.isNaN(days) && days <= 30) return "attention";
  return "normal";
}

function dueLabel(row) {
  if (row.due) return row.due;
  const days = Number(row.Days_To_Expiration);
  if (Number.isNaN(days)) return row.Expiration_Date || "—";
  if (days < 0) return `${Math.abs(days)} days past`;
  if (days === 0) return "Due today";
  return `${days} days left`;
}

function lookupId(value) {
  if (!value) return "";
  if (typeof value === "object") return String(value.id || "");
  return String(value);
}

function hasPipelineDeal(row) {
  return Boolean(lookupId(row && (row.Deal_Id || row.Related_Deal)));
}

function ownerName(value) {
  if (!value) return "";
  if (typeof value === "object") return value.name || value.email || "";
  return String(value);
}

async function api(path, opts) {
  const res = await fetch(`${API}${path}`, {
    headers: { Accept: "application/json", ...(opts && opts.body ? { "Content-Type": "application/json" } : {}) },
    ...opts,
  });
  const text = await res.text();
  let body = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = { error: text };
    }
  }
  if (!res.ok) {
    const err = new Error((body && (body.error || body.message)) || `Request failed (${res.status})`);
    err.status = res.status;
    throw err;
  }
  return body;
}

function TypePill({ type }) {
  const commercial = type === "Commercial";
  return (
    <span className={`type-pill ${commercial ? "commercial" : "personal"}`}>
      <span aria-hidden="true">{commercial ? "🏢" : "🏠"}</span>
      {type}
    </span>
  );
}

function Scorecard({ card, compact }) {
  if (!card) return null;
  return (
    <div className="scorecard" aria-label="Renewal scorecard">
      <span className="health-chip">{card.health}% health</span>
      {card.rails.map((rail) => (
        <span className={`rail ${rail.state}`} key={rail.key}>
          <span aria-hidden="true">{rail.mark}</span> {compact ? null : <b>{rail.label}</b>}
          {compact ? <span className="muted">{rail.label}</span> : null}
        </span>
      ))}
    </div>
  );
}

function rowModel(row) {
  const stage = storedDeskStage(row);
  const states = statesFromTasks(row.tasks || (row.os && row.os.tasks) || []);
  const card = (row.os && row.os.scorecard) || scorecard(stage, states);
  return { stage, states, card, type: accountType(row), kind: statusKind(row) };
}

function documentLinks(renewal, card) {
  const related = (card && card.related) || {};
  const account = related.account || {};
  const policy = related.policy || {};
  const items = [
    { label: "Document", href: renewal.Document_URL || policy.Document_URL },
    { label: "Primary folder", href: renewal.Primary_Folder_URL || policy.Primary_Folder_URL },
    { label: "Nextcloud", href: account.Nextcloud_Folder_URL || account.Nextcloud_Folder_Link },
  ];
  return items.filter((item) => item.href);
}

export default function App() {
  const [typeFilter, setTypeFilter] = useState("All");
  const [kpiFilter, setKpiFilter] = useState("");
  const [rows, setRows] = useState(FIXTURES);
  const [leftovers, setLeftovers] = useState([]);
  const [kpis, setKpis] = useState({
    90: 0,
    60: 0,
    30: 2,
    personal: 2,
    past_due: 1,
    CRITICAL: 1,
    AT_RISK: 3,
    SAFE: 1,
    needs_verification: 0,
    ams_pending: 0,
    ams_failed: 0,
  });
  const [bucketRows, setBucketRows] = useState(null);
  const [live, setLive] = useState(false);
  const [selectedId, setSelectedId] = useState(FIXTURES[3].id);
  const [card, setCard] = useState(null);
  const [banner, setBanner] = useState("");
  const [disposition, setDisposition] = useState("renewed");
  const [isDownload, setIsDownload] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api("/api/desk")
      .then((data) => {
        if (cancelled || !data || !Array.isArray(data.rows)) return;
        setLive(true);
        const all = data.rows;
        const unlinked = all.filter((row) => !hasPipelineDeal(row));
        setLeftovers(unlinked);
        setRows(linked);
        setKpis(data.kpis || {});
        const match = String(window.location.hash || "").match(/renewals\/([^/?#]+)/);
        if (match) setSelectedId(match[1]);
        else if (linked[0]) setSelectedId(String(linked[0].id));
        else setSelectedId("");
      })
      .catch(() => {
        if (!cancelled) setLive(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const tile = KPI_TILES.find((item) => item.key === kpiFilter);
    if (!live || !tile || (tile.group !== "recon" && tile.group !== "ams")) {
      setBucketRows(null);
      return undefined;
    }
    let cancelled = false;
    const path = tile.group === "recon"
      ? "/api/desk/needs-verification"
      : `/api/desk/ams?view=${tile.key === "ams_failed" ? "failed" : "pending"}`;
    api(path)
      .then((data) => {
        if (!cancelled) setBucketRows(Array.isArray(data.rows) ? data.rows : []);
      })
      .catch((err) => {
        if (!cancelled) setBanner(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [live, kpiFilter]);

  useEffect(() => {
    if (!live || !selectedId) {
      const row = rows.find((item) => String(item.id) === String(selectedId));
      setCard(row ? { renewal: row, tasks: row.tasks || [] } : null);
      return undefined;
    }
    let cancelled = false;
    api(`/api/desk/renewals/${encodeURIComponent(selectedId)}`)
      .then((data) => {
        if (!cancelled) setCard(data);
      })
      .catch((err) => {
        if (!cancelled) setBanner(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [live, selectedId, rows]);

  const visible = useMemo(() => {
    const tile = KPI_TILES.find((item) => item.key === kpiFilter);
    if (tile && (tile.group === "recon" || tile.group === "ams")) {
      return bucketRows || [];
    }
    return rows.filter((row) => {
      if (typeFilter !== "All" && accountType(row) !== typeFilter) return false;
      if (!tile) return true;
      if (tile.group === "window") return String(row.Window_Bucket || "") === tile.key;
      if (tile.group === "risk") return String(row.Risk_Status || "") === tile.key;
      return true;
    });
  }, [rows, typeFilter, kpiFilter, bucketRows]);

  const renewal = (card && (card.renewal || card)) || rows.find((row) => String(row.id) === String(selectedId)) || visible[0];
  const model = renewal ? rowModel({ ...renewal, tasks: (card && card.tasks) || renewal.tasks || [] }) : null;
  const checkpoints = model ? checkpointsForStage(model.stage, model.states) : [];
  const next = model ? nextRequiredAction(model.stage, model.states) : null;
  const remaining = (model && model.card.remaining) || [];
  const dealId = renewal ? lookupId(renewal.Deal_Id || renewal.Related_Deal) : "";
  const relatedDeal = card && card.related && card.related.deal;
  const docs = renewal ? documentLinks(renewal, card) : [];
  const owner = ownerName((renewal && renewal.Owner) || (card && card.related && card.related.account && card.related.account.Owner));
  const showClose = model && (model.stage === "Negotiating" || model.stage === "Closed");

  async function onComplete(key) {
    const states = model.states;
    const result = completeCheckpoint(model.stage, states, key, {
      actor: "user",
      disposition: key === "record_disposition" ? disposition : undefined,
    });
    if (!result.ok) {
      setBanner(result.error || "Could not complete checkpoint");
      return;
    }
    if (remaining.filter((item) => item !== key).length && result.advanced) {
      setBanner("Stage advanced while checkpoints remain — that must not happen.");
      return;
    }
    if (live) {
      try {
        await api(`/api/desk/renewals/${encodeURIComponent(renewal.id)}/checkpoints/${encodeURIComponent(key)}/complete`, {
          method: "POST",
          body: JSON.stringify({
            disposition: key === "record_disposition" ? disposition : undefined,
            is_download: isDownload,
            actor: "user",
          }),
        });
        const fresh = await api(`/api/desk/renewals/${encodeURIComponent(renewal.id)}`);
        setCard(fresh);
        setBanner(result.advanced ? `Advanced to ${operatingLabel(result.desk_stage)}.` : "Checkpoint recorded. CRM task updated in the background.");
        return;
      } catch (err) {
        setBanner(`${err.message} Local scorecard still updated; deploy the function patch to write CRM tasks.`);
      }
    }
    const nextRow = {
      ...renewal,
      Desk_Stage: result.desk_stage,
      Stage: result.desk_stage,
      tasks: Object.values(result.states).map((item) => ({
        Subject: item.title || item.key,
        Status: item.status,
        key: item.key,
      })),
    };
    setRows((current) => current.map((row) => (String(row.id) === String(renewal.id) ? { ...row, ...nextRow } : row)));
    setCard({ renewal: nextRow, tasks: nextRow.tasks, related: card && card.related });
    setBanner(
      result.advanced
        ? `Advanced to ${operatingLabel(result.desk_stage)}.`
        : result.remaining.length
          ? `Recorded. Still need: ${result.remaining.length} required checkpoint(s).`
          : "Checkpoint recorded."
    );
  }

  async function onClose() {
    if (live) {
      try {
        await api(`/api/desk/renewals/${encodeURIComponent(renewal.id)}/close`, {
          method: "POST",
          body: JSON.stringify({
            disposition,
            is_download: isDownload,
            Is_Download: isDownload,
          }),
        });
      } catch (err) {
        setBanner(err.message);
        return;
      }
    }
    await onComplete("record_disposition");
  }

  return (
    <section className="desk os">
      <div className="os-list">
        <header>
          <h1>Renewals Desk</h1>
          <p className="sub">
            Work the book. Hermes writes the AMS. Gretchen talks to the client.
            This desk is a 1:1 projection of the CRM Renewals pipeline
            (Deal_Id / Related_Deal), not a second book.
          </p>
        </header>
        <div className="kpi-strip" role="navigation" aria-label="Worklist filters">
          {KPI_TILES.map((tile) => (
            <button
              key={tile.key}
              type="button"
              className={`kpi ${tile.group} ${kpiFilter === tile.key ? "pressed" : ""}`}
              aria-pressed={kpiFilter === tile.key}
              onClick={() => setKpiFilter(kpiFilter === tile.key ? "" : tile.key)}
            >
              <span className="label">{tile.label}</span>
              <span className="value">{kpis[tile.key] != null ? kpis[tile.key] : "—"}</span>
            </button>
          ))}
        </div>
        <div className="type-filters" role="group" aria-label="Account type">
          {["All", "Commercial", "Personal"].map((name) => (
            <button
              key={name}
              type="button"
              className={name === "Commercial" ? "commercial" : name === "Personal" ? "personal" : undefined}
              aria-pressed={typeFilter === name}
              onClick={() => setTypeFilter(name)}
            >
              {name}
            </button>
          ))}
        </div>
        {leftovers.length ? (
          <p className="empty-hint leftover-banner">
            {leftovers.length} desk row{leftovers.length === 1 ? "" : "s"} have no Renewals
            pipeline Deal (example: Lombardo, Tiffany · 991540615). Hidden from this
            worklist. Run <code>hermes --sync-zoho-renewals</code> to create/link the Deal
            and set Deal_Id / Related_Deal.
          </p>
        ) : null}
        <div className="worklist">
          <h2>Worklist</h2>
          <p>
            Window and risk tiles filter this list. Health % is the scorecard
            on the renewal, not a KPI tile. Hermes is the only NowCerts writer.
          </p>
          <table className="worklist-table">
            <thead>
              <tr>
                <th className="type-col" scope="col">Type</th>
                <th scope="col">Insured</th>
                <th scope="col">Renewal</th>
                <th scope="col">Due</th>
                <th scope="col">Health</th>
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((row) => {
                const item = rowModel(row);
                const kind = item.kind;
                return (
                  <tr
                    key={row.id}
                    className={item.type === "Commercial" ? "row-commercial" : "row-personal"}
                    aria-selected={String(selectedId) === String(row.id)}
                    onClick={() => setSelectedId(String(row.id))}
                  >
                    <td><TypePill type={item.type} /></td>
                    <td className="insured">{row.Client_Name || row.insured || row.Name}</td>
                    <td>{row.Line_of_Business || row.renewal || row.Action || "—"}</td>
                    <td className="muted">{dueLabel(row)}</td>
                    <td className="health-chip">{item.card.health}%</td>
                    <td>
                      <span className={kind === "attention" ? "status-pill attention" : kind === "overdue" ? "status-pill overdue" : "status-pill"}>
                        {item.card.label}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {!visible.length ? (
            <p className="empty-hint">
              No eligible renewals in this filter. Check Needs verification if the book
              looks short; unlinked rows stay off the worklist until sync links a Deal.
            </p>
          ) : null}
        </div>
      </div>

      {renewal && model ? (
        <article className="renewal-card os-card">
          <header>
            <p className="kicker">
              <TypePill type={model.type} />
              <span>{renewal.Line_of_Business}</span>
              · <span>{renewal.Carrier}</span>
              <span className={`status-pill ${model.kind === "overdue" ? "overdue" : model.kind === "attention" ? "attention" : ""}`}>
                {model.card.label}
              </span>
            </p>
            <h1>{renewal.Client_Name}</h1>
            <p className="meta">
              Policy <strong>{renewal.Policy_Number}</strong>
              · x-date <strong>{renewal.Expiration_Date}</strong>
              · <span className="countdown">{dueLabel(renewal)}</span>
              · owner <strong>{owner || "—"}</strong>
            </p>
            <Scorecard card={model.card} />
            {!dealId && !relatedDeal ? (
              <p className="blockers leftover-banner">
                Sync miss: no Renewals pipeline Deal (Related panel would say “No deal”).
                Run <code>hermes --sync-zoho-renewals</code>. Do not leave this as a desk-only leftover.
              </p>
            ) : (
              <p className="meta">Pipeline Deal {dealId || lookupId(relatedDeal)}</p>
            )}
            {banner ? <p className="empty-hint">{banner}</p> : null}
          </header>

          <div className="os-layout">
            <div>
              <section>
                <h2>{model.card.label} — checkpoints</h2>
                <p className="meta">Complete in place. Hermes seeds and completes the matching Zoho task in the background. Required items gate the next stored stage. Hermes never auto-advances.</p>
                {checkpoints.map((item) => (
                  <div className="checkpoint" key={item.key}>
                    <span aria-hidden="true">{item.status === "Complete" ? "✅" : item.required ? "⬜" : "·"}</span>
                    <div>
                      <strong>{item.title}</strong>
                      {item.required ? <span className="meta"> · required</span> : null}
                      <div className="meta">{item.status} · owner {item.owner || owner || "CSR"}{item.due_date ? ` · due ${item.due_date}` : ""}</div>
                    </div>
                    {item.status === "Complete" ? (
                      <span className="meta">Done</span>
                    ) : (
                      <button type="button" className="complete" onClick={() => onComplete(item.key)}>
                        Complete
                      </button>
                    )}
                  </div>
                ))}
              </section>

              {showClose ? (
                <section>
                  <h2>Close renewal</h2>
                  <p className="meta">Live dispositions. Lost to Competitor / Cancelled / Marketed map onto these — they are not a second picklist.</p>
                  <label className="meta">
                    Disposition{" "}
                    <select value={disposition} onChange={(event) => setDisposition(event.target.value)}>
                      {OS_DISPOSITIONS.map((row) => (
                        <option key={row.code} value={row.code}>{row.label}</option>
                      ))}
                    </select>
                  </label>
                  {disposition === "renewed" || disposition === "rewritten" ? (
                    <div className="download-choice">
                      <label>
                        <input type="radio" name="is-download" checked={isDownload} onChange={() => setIsDownload(true)} />
                        {" "}Carrier download
                      </label>
                      <label>
                        <input type="radio" name="is-download" checked={!isDownload} onChange={() => setIsDownload(false)} />
                        {" "}Enter in NowCerts
                      </label>
                    </div>
                  ) : null}
                  <button type="button" className="complete" onClick={onClose}>Record close</button>
                </section>
              ) : null}

              <section>
                <h2>Activity</h2>
                <p className="meta">Last activity {renewal.Last_Activity_Time || "—"}</p>
                <ul className="activity">
                  {((card && card.tasks) || renewal.tasks || []).map((task) => (
                    <li key={task.id || task.Subject}>{task.Status || task.status}: {task.Subject}</li>
                  ))}
                </ul>
              </section>

              <section>
                <h2>Documents</h2>
                {docs.length ? (
                  <ul className="activity">
                    {docs.map((item) => (
                      <li key={item.href}><a href={item.href}>{item.label}</a></li>
                    ))}
                  </ul>
                ) : (
                  <p className="meta">No Document_URL / Primary_Folder_URL / Nextcloud folder on this card yet.</p>
                )}
              </section>
            </div>
            <aside className="os-side">
              <section>
                <h2>Producer / CSR</h2>
                <p><strong>{owner || "Unassigned"}</strong></p>
                <p className="meta">{renewal.Owner && renewal.Owner.email ? renewal.Owner.email : "Owner from the CRM row. CSR completes checkpoints here."}</p>
              </section>
              <section>
                <h2>Next required action</h2>
                <p><strong>{next ? next.title : "—"}</strong></p>
                <p className="meta">Owner: {next && next.owner_role === "csr" ? "CSR (Gretchen)" : "Producer"}</p>
                {remaining.length ? (
                  <p className="blockers">Blocked by {remaining.length} required checkpoint(s). Completing one does not skip the rest.</p>
                ) : (
                  <p className="meta">Required checkpoints for this stage are done.</p>
                )}
              </section>
              <section>
                <h2>Hermes / AI</h2>
                <p className="meta">{renewal.Strategy_Notes || renewal.Recommended_Action || "No invented quotes. Use carrier-appetite / renewal-review when you need a slate."}</p>
                {card && card.email && card.email.drafts && card.email.drafts[0] ? (
                  <p className="meta">Draft ready: {card.email.drafts[0].label}. Desk drafts; Gretchen sends.</p>
                ) : null}
              </section>
              <section>
                <h2>Background automation</h2>
                <p className="meta">Request terms / Update AMS still queue AMS_Write_Queue. Hermes drains NowCerts. The desk never writes AMS.</p>
              </section>
            </aside>
          </div>
        </article>
      ) : null}
    </section>
  );
}
