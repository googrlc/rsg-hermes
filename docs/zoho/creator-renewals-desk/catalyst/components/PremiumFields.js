import { formatChange } from '../api';

function Field({ label, children }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
    </label>
  );
}

function changeTone(direction) {
  if (!direction || direction === 'flat') return direction || 'unknown';
  return direction === 'increase' ? 'warn' : 'good';
}

export default function PremiumFields({ form, setForm, renewalInputId }) {
  const change = formatChange(form.Premium_Current, form.Premium_Renewal);
  const tone = changeTone(change.direction);
  return (
    <>
      <div className="money-grid">
        <Field label="Premium Current">
          <input
            inputMode="decimal"
            value={form.Premium_Current}
            onChange={(e) => setForm({ ...form, Premium_Current: e.target.value })}
            placeholder="Expiring premium"
            autoComplete="off"
          />
        </Field>
        <Field label="Premium Renewal">
          <input
            id={renewalInputId}
            inputMode="decimal"
            value={form.Premium_Renewal}
            onChange={(e) => setForm({ ...form, Premium_Renewal: e.target.value })}
            placeholder="Quoted renewal premium"
            autoComplete="off"
          />
        </Field>
        <div className={`change-preview ${tone}`}>
          <span className="label">{change.word || 'Premium change'}</span>
          <span className="value">{change.text}</span>
        </div>
      </div>
    </>
  );
}
