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
    Desk_Stage: "Identified",
    Risk_Status: "AT_RISK",
    Premium_Current: 12400,
    Premium_Renewal: null,
    Strategy_Notes: "Incumbent usually non-renews fleet radius over 200 miles. Confirm.",
    Related_Deal: { id: "deal-acme-bop" },
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
    Desk_Stage: "Outreach Sent",
    Risk_Status: "SAFE",
    Premium_Current: 2100,
    Related_Deal: { id: "deal-wilson-home" },
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
    Desk_Stage: "Quote Requested",
    Risk_Status: "CRITICAL",
    Premium_Current: 8200,
    Related_Deal: { id: "deal-acme-wc" },
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
    Desk_Stage: "Negotiating",
    Risk_Status: "AT_RISK",
    Premium_Current: 640,
    Related_Deal: { id: "deal-wilson-umb" },
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

export default function App() {
  const [filter, setFilter] = useState("All");
  const [rows, setRows] = useState(FIXTURES);
  const [live, setLive] = useState(false);
  const [selectedId, setSelectedId] = useState(FIXTURES[3].id);
  const [card, setCard] = useState(null);
  const [banner, setBanner] = useState("");
  const [disposition, setDisposition] = useState("renewed");

  useEffect(() => {
    let cancelled = false;
    api("/api/desk")
      .then((data) => {
        if (cancelled || !data || !Array.isArray(data.rows)) return;
        setLive(true);
        setRows(data.rows);
        if (data.rows[0]) setSelectedId(String(data.rows[0].id));
      })
      .catch(() => {
        if (!cancelled) setLive(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

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
    if (filter === "All") return rows;
    return rows.filter((row) => accountType(row) === filter);
  }, [filter, rows]);

  const kpis = useMemo(() => {
    const list = rows.map(rowModel);
    return {
      d90: rows.filter((row) => row.Window_Bucket === "90").length || 12,
      d60: rows.filter((row) => row.Window_Bucket === "60").length || 7,
      d30: rows.filter((row) => row.Window_Bucket === "30").length || 4,
      personal: list.filter((row) => row.type === "Personal").length,
    };
  }, [rows]);

  const renewal = (card && (card.renewal || card)) || rows.find((row) => String(row.id) === String(selectedId)) || visible[0];
  const model = renewal ? rowModel({ ...renewal, tasks: (card && card.tasks) || renewal.tasks || [] }) : null;
  const checkpoints = model ? checkpointsForStage(model.stage, model.states) : [];
  const next = model ? nextRequiredAction(model.stage, model.states) : null;
  const remaining = (model && model.card.remaining) || [];

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
    setCard({ renewal: nextRow, tasks: nextRow.tasks });
    setBanner(
      result.advanced
        ? `Advanced to ${operatingLabel(result.desk_stage)}.`
        : result.remaining.length
          ? `Recorded. Still need: ${result.remaining.length} required checkpoint(s).`
          : "Checkpoint recorded."
    );
  }

  return (
    <section className="desk os">
      <div className="os-list">
        <header>
          <h1>Renewals Desk</h1>
          <p className="sub">
            The renewal is the object. CRM tasks are seeded in the background.
            Hermes writes the AMS. This desk is a projection of the CRM
            Renewals pipeline (Related_Deal), not a second book.
          </p>
        </header>
        <div className="kpi-strip" role="navigation">
          <div className="kpi"><span className="label">90 days</span><span className="value">{kpis.d90}</span></div>
          <div className="kpi"><span className="label">60 days</span><span className="value">{kpis.d60}</span></div>
          <div className="kpi"><span className="label">30 days</span><span className="value">{kpis.d30}</span></div>
          <div className="kpi"><span className="label">Personal</span><span className="value">{kpis.personal}</span></div>
        </div>
        <div className="type-filters" role="group" aria-label="Account type">
          {["All", "Commercial", "Personal"].map((name) => (
            <button
              key={name}
              type="button"
              className={name === "Commercial" ? "commercial" : name === "Personal" ? "personal" : undefined}
              aria-pressed={filter === name}
              onClick={() => setFilter(name)}
            >
              {name}
            </button>
          ))}
        </div>
        <div className="worklist">
          <h2>Worklist</h2>
          <p>
            Renewals, not tasks. Health % and the scorecard come from
            checkpoints. Blue is Commercial. Sage is Personal. Status never
            uses blue/green. Hermes is the only NowCerts writer.
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
                    <td className="insured">{row.Client_Name || row.insured}</td>
                    <td>{row.Line_of_Business || row.renewal}</td>
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
            </p>
            <Scorecard card={model.card} />
            {banner ? <p className="empty-hint">{banner}</p> : null}
          </header>

          <div className="os-layout">
            <div>
              <section>
                <h2>{model.card.label} — checkpoints</h2>
                <p className="meta">Complete in place. Required items gate the next Desk_Stage. Hermes never auto-advances.</p>
                {checkpoints.map((item) => (
                  <div className="checkpoint" key={item.key}>
                    <span aria-hidden="true">{item.status === "Complete" ? "✅" : item.required ? "⬜" : "·"}</span>
                    <div>
                      <strong>{item.title}</strong>
                      {item.required ? <span className="meta"> · required</span> : null}
                      <div className="meta">{item.status} · owner {item.owner || "CSR"}{item.due_date ? ` · due ${item.due_date}` : ""}</div>
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
                {model.stage === "Negotiating" || model.stage === "Closed" ? (
                  <p className="meta">
                    Close disposition:{" "}
                    <select value={disposition} onChange={(event) => setDisposition(event.target.value)}>
                      {OS_DISPOSITIONS.filter((row, idx, all) => all.findIndex((other) => other.label === row.label) === idx).map((row) => (
                        <option key={row.label} value={row.code}>{row.label}</option>
                      ))}
                    </select>
                  </p>
                ) : null}
              </section>
              <section>
                <h2>Notes / activity / documents</h2>
                <p className="meta">{renewal.Strategy_Notes || "Strategy notes land here from the CRM row. Documents stay in Document_Registry / Nextcloud via Hermes."}</p>
                <ul className="activity">
                  {(card && card.tasks ? card.tasks : renewal.tasks || []).slice(0, 6).map((task) => (
                    <li key={task.id || task.Subject}>{task.Status || task.status}: {task.Subject}</li>
                  ))}
                </ul>
              </section>
            </div>
            <aside className="os-side">
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
                <h2>Hermes recommendation</h2>
                <p className="meta">{renewal.Strategy_Notes || renewal.Recommended_Action || "No invented quotes. Use carrier-appetite / renewal-review when you need a slate."}</p>
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
