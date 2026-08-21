import { useEffect, useState } from 'react';
import { getNeedsVerification, isoDate } from '../api';

export default function NeedsVerification() {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    getNeedsVerification()
      .then((payload) => setRows(payload.rows || []))
      .catch(setError);
  }, []);

  return (
    <section>
      <div className="list-toolbar">
        <h1>
          Needs Verification
          {rows ? <span className="count">{rows.length}</span> : null}
        </h1>
      </div>
      <p className="muted">Match the event to a Policy / Account. Do not invent a policy.</p>
      {error ? <p className="panel error">{error.message}</p> : null}
      {!rows ? <p className="muted">Loading records…</p> : null}
      {rows && rows.length === 0 ? <p className="empty-hint">No records found.</p> : null}
      {rows && rows.length > 0 ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Client Name</th>
                <th>Policy Number</th>
                <th>Renewal Event Date</th>
                <th>Eligibility Reason</th>
                <th>Normalized Status</th>
                <th>Branch</th>
                <th>Segment</th>
                <th>NowCerts Insured GUID</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <td>
                    <span className="record-link">{row.Client_Name || '—'}</span>
                  </td>
                  <td className="mono">{row.Policy_Number || '—'}</td>
                  <td>{isoDate(row.Renewal_Event_Date)}</td>
                  <td>{row.Eligibility_Reason || '—'}</td>
                  <td>{row.Normalized_Status || '—'}</td>
                  <td>{row.Branch || '—'}</td>
                  <td>{row.Segment || '—'}</td>
                  <td className="mono">{row.NowCerts_Insured_GUID || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
