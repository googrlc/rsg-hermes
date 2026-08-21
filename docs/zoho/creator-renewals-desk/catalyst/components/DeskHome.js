import { useEffect, useMemo, useState } from 'react';
import { accountType, getDesk, hasPipelineDeal } from '../api';
import KpiStrip from './KpiStrip';
import Worklist from './Worklist';

export default function DeskHome({ route, onNavigate }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState(route.q || '');

  const filters = useMemo(
    () => ({
      window: route.window || '',
      risk: route.risk || '',
      stage: route.stage || '',
      type: route.type || '',
      q: route.q || '',
    }),
    [route.window, route.risk, route.stage, route.type, route.q],
  );

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getDesk({
      window: filters.window,
      risk: filters.risk,
      stage: filters.stage,
      q: filters.q,
    })
      .then((payload) => {
        if (!cancelled) {
          setData(payload);
          setError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [filters.window, filters.risk, filters.stage, filters.q]);

  const leftoverCount =
    (data && data.leftovers) ||
    ((data && data.rows) || []).filter((row) => !hasPipelineDeal(row)).length;
  const leftoverReason = data && data.leftover_reason;
  const rows = useMemo(() => {
    const all = ((data && data.rows) || []).filter(hasPipelineDeal);
    if (filters.type === 'Personal') return all.filter((row) => accountType(row.Line_of_Business) === 'Personal');
    if (filters.type === 'Commercial') return all.filter((row) => accountType(row.Line_of_Business) === 'Commercial');
    return all;
  }, [data, filters.type]);

  function applySearch(event) {
    event.preventDefault();
    onNavigate({ view: 'desk', ...filters, q });
  }

  function setFilter(key, value) {
    onNavigate({ view: 'desk', ...filters, [key]: filters[key] === value ? '' : value });
  }

  return (
    <section>
      <div className="list-toolbar">
        <h1>
          Renewals
          {data ? <span className="count">{rows.length} of {data.total}</span> : null}
        </h1>
        <form className="search" onSubmit={applySearch}>
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search client, policy, carrier"
            aria-label="Search worklist"
          />
          <button type="submit">Search</button>
        </form>
      </div>

      {error ? (
        <div className="panel error">
          <strong>Connect Zoho CRM in Catalyst</strong>
          <p>{error.message}</p>
          <ol className="setup-steps">
            <li>
              Open{' '}
              <a href="https://console.catalyst.zoho.com" target="_blank" rel="noreferrer">
                Catalyst Console
              </a>
              {' '}→ project <strong>Renewals-Desk</strong> → <strong>Cloud Scale</strong> →{' '}
              <strong>Connections</strong>.
            </li>
            <li>Create Connection → Default Service → <strong>Zoho CRM</strong>.</li>
            <li>
              Connection Name: <code>zoho_crm</code> (link name must stay <code>zoho_crm</code>).
            </li>
            <li>
              Scopes: <code>ZohoCRM.modules.ALL</code>, <code>ZohoCRM.settings.ALL</code>,{' '}
              <code>ZohoCRM.users.READ</code>.
            </li>
            <li>Create and Connect → pick the RSG org → authorize. Then refresh this page.</li>
          </ol>
        </div>
      ) : null}

      <KpiStrip
        kpis={data ? data.kpis : null}
        active={{ window: filters.window, risk: filters.risk }}
        onSelect={setFilter}
        onOpen={(view) => onNavigate({ view })}
      />

      <div className="type-filters" role="group" aria-label="Account type">
        {['All', 'Commercial', 'Personal'].map((name) => (
          <button
            key={name}
            type="button"
            className={name === 'Commercial' ? 'commercial' : name === 'Personal' ? 'personal' : undefined}
            aria-pressed={(filters.type || 'All') === name}
            onClick={() => setFilter('type', name === 'All' ? '' : name)}
          >
            {name}
          </button>
        ))}
      </div>

      {loading ? <p className="muted">Loading records…</p> : null}
      {leftoverCount ? (
        <p className="empty-hint leftover-banner">
          {leftoverReason ||
            `${leftoverCount} desk row(s) have no Renewals pipeline Deal and are hidden. Run hermes --sync-zoho-renewals.`}
        </p>
      ) : null}
      {data && data.empty_reason ? <p className="empty-hint">{data.empty_reason}</p> : null}

      {data ? (
        <Worklist
          rows={rows}
          stages={data.stages || []}
          stage={filters.stage}
          onStage={(stage) => setFilter('stage', stage)}
          onOpen={(id) => onNavigate({ view: 'card', id })}
        />
      ) : null}
    </section>
  );
}
