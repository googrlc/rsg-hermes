export default function Scorecard({ card, compact }) {
  if (!card) return null;
  return (
    <div className={`scorecard${compact ? ' compact' : ''}`} aria-label="Renewal scorecard">
      <span className="health-chip">{card.health}% health</span>
      {card.rails.map((rail) => (
        <span className={`rail ${rail.state}`} key={rail.key}>
          <span aria-hidden="true">{rail.mark}</span>{' '}
          {compact ? <span className="muted">{rail.label}</span> : <b>{rail.label}</b>}
        </span>
      ))}
    </div>
  );
}
