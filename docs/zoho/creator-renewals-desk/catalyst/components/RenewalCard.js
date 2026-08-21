import { useEffect, useState } from 'react';
import {
  getCard,
  patchRenewal,
  enqueueAms,
  dismissRenewal,
  isoDate,
  moneyInput,
  lookupName,
  money,
} from '../api';
import CurrentAction from './CurrentAction';
import Scorecard from './Scorecard';
import { WORK_STEPS, workStepIndex, statusChip, daysLabel } from '../workflow';
import { mergeCheckpointStates, scorecard, storedDeskStage } from '../operating';

export default function RenewalCard({ id, onNavigate, setBanner }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState(null);
  const [ams, setAms] = useState({ expected_result: '', note: '' });

  function applyCard(payload, preservePremium) {
    setData(payload);
    const r = payload.renewal;
    setForm((prev) => ({
      Desk_Stage: r.Desk_Stage || 'Identified',
      Disposition: r.Disposition || '',
      Recommended_Action: r.Recommended_Action || 'SEND_CLIENT_EMAIL',
      Strategy_Notes: r.Strategy_Notes || '',
      Last_Contact_Date: isoDate(r.Last_Contact_Date) === '—' ? '' : isoDate(r.Last_Contact_Date),
      Premium_Current: preservePremium && prev ? prev.Premium_Current : moneyInput(r.Premium_Current),
      Premium_Renewal: preservePremium && prev ? prev.Premium_Renewal : moneyInput(r.Premium_Renewal),
      Is_Download: Boolean(r.Is_Download),
      producer_confirmed: (prev && prev.producer_confirmed) || false,
    }));
  }

  useEffect(() => {
    let cancelled = false;
    getCard(id)
      .then((payload) => {
        if (cancelled) return;
        applyCard(payload, false);
        setError(null);
      })
      .catch((err) => {
        if (!cancelled) setError(err);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  useEffect(() => {
    function onKey(event) {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      onNavigate({ view: 'desk' });
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onNavigate]);

  const action = data && data.next && data.next.action;
  useEffect(() => {
    if (action !== 'open_task' && action !== 'open_closeout_task') return undefined;
    const timer = window.setInterval(() => {
      if (document.hidden) return;
      getCard(id)
        .then((payload) => applyCard(payload, true))
        .catch(() => {});
    }, 4000);
    return () => window.clearInterval(timer);
  }, [id, action]);

  const recordId = (data && data.renewal && data.renewal.id) || id;

  async function savePremium() {
    setSaving(true);
    try {
      await patchRenewal(recordId, {
        Premium_Current: form.Premium_Current,
        Premium_Renewal: form.Premium_Renewal,
      });
      const payload = await getCard(recordId);
      applyCard(payload, true);
      setBanner('');
    } catch (err) {
      setBanner(err.message);
    } finally {
      setSaving(false);
    }
  }

  async function runAms(actionName) {
    try {
      await enqueueAms(recordId, { ...ams, action: actionName });
      setAms({ expected_result: '', note: '' });
      setBanner('Queued for Hermes. This screen does not write NowCerts.');
    } catch (err) {
      setBanner(err.message);
    }
  }

  async function runDismiss() {
    try {
      await dismissRenewal(recordId);
      onNavigate({ view: 'desk' });
    } catch (err) {
      setBanner(err.message);
    }
  }

  if (error) {
    return (
      <div className="panel error">
        <p>{error.message}</p>
        <button type="button" className="secondary" onClick={() => onNavigate({ view: 'desk' })}>
          Back to worklist
        </button>
      </div>
    );
  }
  if (!data || !form) return <p className="muted">Loading…</p>;

  const r = data.renewal;
  const related = data.related || {};
  const chip = statusChip(r);
  const index = workStepIndex(r.Desk_Stage || r.Stage);
  const osCard = (data.os && data.os.scorecard) || scorecard(storedDeskStage(r), mergeCheckpointStates(r, data.tasks || []));
  const owner = r.Owner && typeof r.Owner === 'object' ? r.Owner.name || r.Owner.email : r.Owner;
  const dealId = (related.deal && related.deal.id) || (r.Deal_Id && r.Deal_Id.id) || r.Deal_Id;
  const docs = [
    r.Document_URL && { label: 'Document', href: r.Document_URL },
    r.Primary_Folder_URL && { label: 'Primary folder', href: r.Primary_Folder_URL },
    related.account && related.account.Nextcloud_Folder_URL && { label: 'Nextcloud', href: related.account.Nextcloud_Folder_URL },
  ].filter(Boolean);

  return (
    <article className="workflow">
      <header className="wf-header">
        <button type="button" className="zbtn-link breadcrumb" onClick={() => onNavigate({ view: 'desk' })}>
          Worklist
        </button>
        <div className="wf-title-row">
          <div>
            <h1>{r.Client_Name || 'Renewal'}</h1>
            <p className="wf-meta">
              {r.Policy_Number || 'No policy'} · {r.Carrier || 'No carrier'} · {isoDate(r.Expiration_Date)} · {daysLabel(r.Days_To_Expiration)}
            </p>
          </div>
          <span className={`status-chip ${chip.tone}`}>{chip.label}</span>
        </div>
        <div className="progress-block">
          <Scorecard card={osCard} />
        </div>
        {!dealId && !related.deal ? (
          <p className="empty-hint leftover-banner">
            Sync miss: no Renewals pipeline Deal. Run <code>hermes --sync-zoho-renewals</code>.
            Do not leave this as a desk-only leftover.
          </p>
        ) : null}
      </header>

      <div className="wf-grid">
        <aside className="wf-rail" aria-label="Workflow">
          <ol>
            {WORK_STEPS.map((step, i) => {
              let state = 'upcoming';
              if (r.Desk_Stage === 'Closed' || i < index) state = 'done';
              else if (i === index) state = 'current';
              return (
                <li key={step.stage} className={state}>
                  <span className="wf-mark">{state === 'done' ? '✓' : i + 1}</span>
                  <span>{step.label}</span>
                </li>
              );
            })}
          </ol>
        </aside>

        <div className="wf-main">
          <CurrentAction
            recordId={recordId}
            data={data}
            form={form}
            setData={setData}
            setForm={setForm}
            setBanner={setBanner}
            saving={saving}
            onSavePremium={savePremium}
          />
        </div>

        <aside className="wf-facts">
          <h2>Policy</h2>
          <dl>
            <div>
              <dt>Insured</dt>
              <dd>{r.Client_Name || lookupName(r.Account_Name) || '—'}</dd>
            </div>
            <div>
              <dt>Policy</dt>
              <dd>{r.Policy_Number || '—'}</dd>
            </div>
            <div>
              <dt>Carrier</dt>
              <dd>{r.Carrier || '—'}</dd>
            </div>
            <div>
              <dt>Expires</dt>
              <dd>{isoDate(r.Expiration_Date)}</dd>
            </div>
            <div>
              <dt>Days left</dt>
              <dd>{daysLabel(r.Days_To_Expiration)}</dd>
            </div>
            <div>
              <dt>Current premium</dt>
              <dd>{money(r.Premium_Current)}</dd>
            </div>
            <div>
              <dt>LOB</dt>
              <dd>{r.Line_of_Business || '—'}</dd>
            </div>
            <div>
              <dt>Owner</dt>
              <dd>{owner || '—'}</dd>
            </div>
          </dl>
          <h2>Hermes / AI</h2>
          <p className="muted">{r.Strategy_Notes || r.Recommended_Action || 'No invented quotes. Desk drafts; Gretchen sends.'}</p>
          <h2>Documents</h2>
          {docs.length ? (
            <ul className="activity">
              {docs.map((item) => (
                <li key={item.href}>
                  <a href={item.href}>{item.label}</a>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">No Document_URL / folder on this card yet.</p>
          )}
          <h2>Activity</h2>
          <ul className="activity">
            {(data.tasks || []).map((task) => (
              <li key={task.id || task.Subject}>
                {task.Status || task.status}: {task.Subject}
              </li>
            ))}
          </ul>
        </aside>
      </div>

      <details className="wf-more">
        <summary>More</summary>
        <div className="more-grid">
          <label className="field">
            <span>Strategy notes</span>
            <textarea
              rows={3}
              value={form.Strategy_Notes}
              onChange={(e) => setForm({ ...form, Strategy_Notes: e.target.value })}
            />
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={form.producer_confirmed}
              onChange={(e) => setForm({ ...form, producer_confirmed: e.target.checked })}
            />
            Producer confirmation
          </label>
          <div>
            <h3>AMS queue</h3>
            <textarea
              rows={2}
              value={ams.expected_result}
              onChange={(e) => setAms({ ...ams, expected_result: e.target.value })}
              placeholder="Expected result in NowCerts"
            />
            <div className="action-buttons">
              {Object.entries((data.vocab && data.vocab.actions) || {}).map(([name, label]) => (
                <button key={name} type="button" className="secondary" onClick={() => runAms(name)}>
                  {label}
                </button>
              ))}
            </div>
          </div>
          <div>
            <h3>Related</h3>
            <p className="muted">
              {related.account ? lookupName(r.Account_Name) : r.Account_Name_Text || 'No account'} ·{' '}
              {related.deal ? related.deal.Stage || 'Deal' : 'No deal'}
            </p>
            <button type="button" className="danger" onClick={runDismiss}>
              Dismiss from worklist
            </button>
          </div>
        </div>
      </details>
    </article>
  );
}
