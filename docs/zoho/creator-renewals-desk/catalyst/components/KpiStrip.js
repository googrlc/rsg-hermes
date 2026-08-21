export default function KpiStrip({ kpis, active, onSelect, onOpen }) {
  const values = kpis || {};
  const tiles = [
    { key: '90', label: '90 days', value: values['90'], kind: 'window' },
    { key: '60', label: '60 days', value: values['60'], kind: 'window' },
    { key: '30', label: '30 days', value: values['30'], kind: 'window' },
    { key: 'personal', label: 'Personal', value: values.personal, kind: 'window', tone: 'personal' },
    { key: 'past_due', label: 'Past due', value: values.past_due, kind: 'window', tone: 'overdue' },
    { key: 'CRITICAL', label: 'CRITICAL', value: values.CRITICAL, kind: 'risk', tone: 'overdue' },
    { key: 'AT_RISK', label: 'AT_RISK', value: values.AT_RISK, kind: 'risk', tone: 'attention' },
    { key: 'SAFE', label: 'SAFE', value: values.SAFE, kind: 'risk', tone: 'safe' },
  ];

  return (
    <nav className="kpi-strip" aria-label="Renewal buckets">
      {tiles.map((tile) => (
        <button
          key={tile.key}
          type="button"
          className={`kpi ${tile.kind} ${tile.tone || ''} ${active[tile.kind] === tile.key ? 'active' : ''} ${
            tile.kind === 'risk' ? tile.key.toLowerCase() : ''
          }`}
          onClick={() => onSelect(tile.kind, tile.key)}
        >
          <span className="label">{tile.label}</span>
          <span className="value">{tile.value == null ? '—' : tile.value}</span>
        </button>
      ))}
      <button type="button" className="kpi recon" onClick={() => onOpen('needs')}>
        <span className="label">Needs verification</span>
        <span className="value">{values.needs_verification == null ? '—' : values.needs_verification}</span>
      </button>
      <button type="button" className="kpi ams" onClick={() => onOpen('ams-pending')}>
        <span className="label">Pending AMS</span>
        <span className="value">{values.ams_pending == null ? '—' : values.ams_pending}</span>
      </button>
      <button type="button" className="kpi ams" onClick={() => onOpen('ams-failed')}>
        <span className="label">Failed AMS</span>
        <span className="value">{values.ams_failed == null ? '—' : values.ams_failed}</span>
      </button>
    </nav>
  );
}
