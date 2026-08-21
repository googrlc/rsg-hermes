import { useEffect, useState } from 'react';
import { approveAms, getAmsQueue, isoDate, lookupName } from '../api';

export default function AmsQueue({ view, setBanner }) {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);
  const failed = view === 'ams-failed';

  useEffect(() => {
    getAmsQueue(failed ? 'failed' : 'pending')
      .then((payload) => setRows(payload.rows || []))
      .catch(setError);
  }, [failed]);

  async function approve(id) {
    try {
      const result = await approveAms(id);
      setBanner(`Approved. Hermes will drain this job. Approved by ${result.approved_by}.`);
      const payload = await getAmsQueue(failed ? 'failed' : 'pending');
      setRows(payload.rows || []);
    } catch (err) {
      setBanner(err.message);
    }
  }

  return (
    <section>
      <div className="list-toolbar">
        <h1>
          {failed ? 'AMS Failed' : 'AMS Pending'}
          {rows ? <span className="count">{rows.length}</span> : null}
        </h1>
      </div>
      <p className="muted">
        {failed
          ? 'No silent retry from this desk. Re-queue a new job from the renewal record.'
          : 'Approve queues the job for Hermes. This never calls NowCerts.'}
      </p>
      {error ? <p className="panel error">{error.message}</p> : null}
      {!rows ? <p className="muted">Loading records…</p> : null}
      {rows && rows.length === 0 ? <p className="empty-hint">No records found.</p> : null}
      {rows && rows.length > 0 ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Related Renewal</th>
                <th>Action</th>
                <th>Status</th>
                <th>Approved By</th>
                <th>Last Error</th>
                <th>Created Time</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <td>
                    <span className="record-link">{row.Name || '—'}</span>
                  </td>
                  <td>{lookupName(row.Related_Renewal) || '—'}</td>
                  <td>{row.Action || '—'}</td>
                  <td>{row.Status || '—'}</td>
                  <td>{row.Approved_By || '—'}</td>
                  <td>{row.Last_Error || '—'}</td>
                  <td>{isoDate(row.Created_Time)}</td>
                  <td>
                    {!failed ? (
                      <button type="button" onClick={() => approve(row.id)}>
                        Approve
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
