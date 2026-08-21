import { useState } from 'react';
import { closeRenewal, getCard } from '../api';
import PremiumFields from './PremiumFields';

export default function CloseOut({ recordId, data, form, setData, setForm, setBanner, compact }) {
  const [disposition, setDisposition] = useState(form.Disposition || '');
  const [amsPath, setAmsPath] = useState(form.Is_Download ? 'download' : 'manual');
  const [working, setWorking] = useState(false);
  const vocab = data.vocab || {};
  const closed = (form.Desk_Stage || '') === 'Closed';
  const premiumOk = Boolean(String(form.Premium_Renewal || '').trim());
  const canClose = premiumOk && Boolean(disposition) && !working && !closed;

  async function close() {
    if (!canClose) return;
    const won = disposition === 'renewed' || disposition === 'rewritten';
    setWorking(true);
    try {
      await closeRenewal(recordId, {
        disposition,
        ams_path: won ? amsPath : 'none',
        Premium_Current: form.Premium_Current,
        Premium_Renewal: form.Premium_Renewal,
      });
      const card = await getCard(recordId);
      setData(card);
      setForm((prev) => ({
        ...prev,
        Desk_Stage: 'Closed',
        Disposition: disposition,
      }));
      setBanner('');
    } catch (err) {
      setBanner(err.message);
    } finally {
      setWorking(false);
    }
  }

  return (
    <div className={compact ? 'close-compact' : 'crm-section'}>
      {compact ? null : <h2>Close this renewal</h2>}
      <PremiumFields form={form} setForm={setForm} renewalInputId="premium-renewal" />
      <div className="field">
        <span>Disposition</span>
        <div className="choice-row">
          {(vocab.dispositions || []).map((item) => (
            <button
              key={item}
              type="button"
              className={`choice${disposition === item ? ' selected' : ''}`}
              onClick={() => {
                setDisposition(item);
                setForm({ ...form, Disposition: item });
              }}
            >
              {(vocab.disposition_labels && vocab.disposition_labels[item]) || item}
            </button>
          ))}
        </div>
      </div>
      {disposition === 'renewed' || disposition === 'rewritten' ? (
        <div className="choice-row">
          <button
            type="button"
            className={`choice${amsPath === 'download' ? ' selected' : ''}`}
            onClick={() => {
              setAmsPath('download');
              setForm({ ...form, Is_Download: true });
            }}
          >
            Carrier download
          </button>
          <button
            type="button"
            className={`choice${amsPath === 'manual' ? ' selected' : ''}`}
            onClick={() => {
              setAmsPath('manual');
              setForm({ ...form, Is_Download: false });
            }}
          >
            Enter in NowCerts
          </button>
        </div>
      ) : null}
      <div className="action-buttons">
        <button type="button" onClick={close} disabled={!canClose} title={!premiumOk ? 'Enter renewal premium first' : !disposition ? 'Choose a disposition' : undefined}>
          {working ? 'Closing…' : closed ? 'Closed' : 'Close renewal'}
        </button>
      </div>
    </div>
  );
}
