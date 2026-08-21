import { accountType, isoDate, money, formatChange, statusKind } from '../api';
import { scorecard, storedDeskStage } from '../operating';

function ChangeCell({ current, next }) {
  const change = formatChange(current, next);
  const tone =
    !change.direction || change.direction === 'flat'
      ? change.direction || 'unknown'
      : change.direction === 'increase'
        ? 'warn'
        : 'good';
  return <span className={`change ${tone}`}>{change.text}</span>;
}

function TypePill({ lob }) {
  const type = accountType(lob);
  const commercial = type === 'Commercial';
  return (
    <span className={`type-pill ${commercial ? 'commercial' : 'personal'}`}>
      <span aria-hidden="true">{commercial ? '🏢' : '🏠'}</span>
      {type}
    </span>
  );
}

export default function Worklist({ rows, stages, stage, onStage, onOpen }) {
  return (
    <div className="worklist">
      <div className="worklist-head">
        <span className="muted">Expiration date</span>
        <label>
          <span className="muted">Desk Stage</span>{' '}
          <select value={stage || ''} onChange={(e) => onStage(e.target.value)}>
            <option value="">All</option>
            {stages.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Type</th>
              <th>Client Name</th>
              <th>Policy Number</th>
              <th>Carrier</th>
              <th>Line of Business</th>
              <th>Expiration Date</th>
              <th>Days</th>
              <th>Window Bucket</th>
              <th>Premium Current</th>
              <th>Premium Renewal</th>
              <th>Premium Change</th>
              <th>Risk Status</th>
              <th>Health</th>
              <th>Desk Stage</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const type = accountType(row.Line_of_Business);
              const kind = statusKind(row);
              const health = (row.os && row.os.scorecard && row.os.scorecard.health) || scorecard(storedDeskStage(row), {}).health;
              return (
                <tr
                  key={row.id}
                  className={type === 'Commercial' ? 'row-commercial' : 'row-personal'}
                  onClick={() => onOpen(row.id)}
                >
                  <td>
                    <TypePill lob={row.Line_of_Business} />
                  </td>
                  <td>
                    <span className="record-link">{row.Client_Name || '—'}</span>
                  </td>
                  <td className="mono">{row.Policy_Number || '—'}</td>
                  <td>{row.Carrier || '—'}</td>
                  <td>{row.Line_of_Business || '—'}</td>
                  <td>{isoDate(row.Expiration_Date)}</td>
                  <td>{row.Days_To_Expiration == null ? '—' : row.Days_To_Expiration}</td>
                  <td>
                    <span
                      className={`pill window ${row.Window_Bucket || ''} ${
                        kind === 'overdue' ? 'overdue' : kind === 'attention' ? 'attention' : ''
                      }`}
                    >
                      {row.Window_Bucket || '—'}
                    </span>
                  </td>
                  <td>{money(row.Premium_Current)}</td>
                  <td>{money(row.Premium_Renewal)}</td>
                  <td>
                    <ChangeCell current={row.Premium_Current} next={row.Premium_Renewal} />
                  </td>
                  <td>
                    <span
                      className={`pill risk ${(row.Risk_Status || '').toLowerCase()} ${
                        kind === 'overdue' ? 'overdue' : kind === 'attention' ? 'attention' : ''
                      }`}
                    >
                      {row.Risk_Status || '—'}
                    </span>
                  </td>
                  <td className="health-chip">{health}%</td>
                  <td>{row.Desk_Stage || row.Stage || 'Identified'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
