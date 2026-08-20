import { useMemo, useState } from "react";
import "./desk.css";

const ROWS = [
  {
    id: "acme-bop",
    type: "Commercial",
    insured: "Acme Trucking LLC",
    renewal: "BOP + Auto",
    carrier: "Travelers",
    due: "Sep 15",
    status: "In review",
    statusKind: "normal",
  },
  {
    id: "wilson-home",
    type: "Personal",
    insured: "James Wilson",
    renewal: "Home + Auto",
    carrier: "Safeco",
    due: "Sep 18",
    status: "Contacted",
    statusKind: "normal",
  },
  {
    id: "acme-wc",
    type: "Commercial",
    insured: "Acme Trucking LLC",
    renewal: "Workers Comp",
    carrier: "Travelers",
    due: "Sep 8",
    status: "Nearing deadline",
    statusKind: "attention",
  },
  {
    id: "wilson-umb",
    type: "Personal",
    insured: "James Wilson",
    renewal: "Umbrella",
    carrier: "Safeco",
    due: "Aug 12",
    status: "Overdue",
    statusKind: "overdue",
  },
];

function TypePill({ type }) {
  const commercial = type === "Commercial";
  return (
    <span className={`type-pill ${commercial ? "commercial" : "personal"}`}>
      <span aria-hidden="true">{commercial ? "🏢" : "🏠"}</span>
      {type}
    </span>
  );
}

export default function App() {
  const [filter, setFilter] = useState("All");
  const [selected, setSelected] = useState("wilson-umb");

  const rows = useMemo(() => {
    if (filter === "All") return ROWS;
    return ROWS.filter((row) => row.type === filter);
  }, [filter]);

  const kpis = useMemo(
    () => ({
      d90: 12,
      d60: 7,
      d30: 4,
      personal: ROWS.filter((r) => r.type === "Personal").length,
      verify: 2,
      ams: 1,
    }),
    []
  );

  return (
    <section className="desk">
      <header>
        <h1>Renewals Desk</h1>
        <p className="sub">
          Work the book. Hermes writes the AMS. Gretchen talks to the client.
        </p>
      </header>

      <div className="kpi-strip" role="navigation">
        <div className="kpi">
          <span className="label">90 days</span>
          <span className="value">{kpis.d90}</span>
        </div>
        <div className="kpi">
          <span className="label">60 days</span>
          <span className="value">{kpis.d60}</span>
        </div>
        <div className="kpi">
          <span className="label">30 days</span>
          <span className="value">{kpis.d30}</span>
        </div>
        <div className="kpi">
          <span className="label">Personal</span>
          <span className="value">{kpis.personal}</span>
        </div>
        <div className="kpi recon">
          <span className="label">Needs verification</span>
          <span className="value">{kpis.verify}</span>
        </div>
        <div className="kpi ams">
          <span className="label">Pending AMS</span>
          <span className="value">{kpis.ams}</span>
        </div>
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
          Type is Commercial or Personal. Status is separate. Blue is never
          “healthy” and green is never “done.” Layout fixtures only until CRM
          is wired. Hermes is the only NowCerts writer.
        </p>
        <table className="worklist-table">
          <thead>
            <tr>
              <th className="type-col" scope="col">
                Type
              </th>
              <th scope="col">Insured</th>
              <th scope="col">Renewal</th>
              <th scope="col">Carrier</th>
              <th scope="col">Due</th>
              <th scope="col">Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                key={row.id}
                className={row.type === "Commercial" ? "row-commercial" : "row-personal"}
                aria-selected={selected === row.id}
                onClick={() => setSelected(row.id)}
              >
                <td>
                  <TypePill type={row.type} />
                </td>
                <td className="insured">{row.insured}</td>
                <td>{row.renewal}</td>
                <td className="muted">{row.carrier}</td>
                <td>{row.due}</td>
                <td>
                  <span
                    className={
                      row.statusKind === "attention"
                        ? "status-pill attention"
                        : row.statusKind === "overdue"
                          ? "status-pill overdue"
                          : "status-pill"
                    }
                  >
                    {row.status}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
