import { useEffect, useMemo, useState } from 'react';
import { getCard, sendClientEmail } from '../api';

function Field({ label, children }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
    </label>
  );
}

export default function ClientEmail({ recordId, data, setData, setForm, setBanner, compact }) {
  const email = data.email || {};
  const drafts = email.drafts || [];
  const recipients = email.recipients || [];
  const fromAddresses = email.from_addresses || [];
  const [templateId, setTemplateId] = useState(email.default_template || 'information_review');
  const [toId, setToId] = useState(recipients[0] ? recipients[0].id : '');
  const [fromEmail, setFromEmail] = useState(
    (email.default_from && email.default_from.email) || (fromAddresses[0] && fromAddresses[0].email) || '',
  );
  const [subject, setSubject] = useState('');
  const [content, setContent] = useState('');
  const [sending, setSending] = useState(false);
  const [preview, setPreview] = useState(!compact);

  const draft = useMemo(
    () => drafts.find((item) => item.id === templateId) || drafts[0] || null,
    [drafts, templateId],
  );
  const recipient = recipients.find((item) => item.id === toId) || recipients[0] || null;
  const from = fromAddresses.find((item) => item.email === fromEmail) || email.default_from || fromAddresses[0] || null;

  useEffect(() => {
    if (!draft) return;
    setSubject(draft.subject);
    setContent(draft.body);
  }, [draft]);

  async function send() {
    if (!recipient) {
      setBanner('Add a Contact email in CRM first.');
      return;
    }
    if (!from) {
      setBanner('No CRM From mailbox is available.');
      return;
    }
    setSending(true);
    try {
      const result = await sendClientEmail(recordId, {
        template_id: templateId,
        contact_id: recipient.id,
        contact_module: recipient.module,
        to_email: recipient.email,
        to_name: recipient.name,
        from_email: from.email,
        from_name: from.user_name || 'Risk Solutions Group',
        subject,
        content,
      });
      setBanner(`Email sent to ${result.to}.`);
      const payload = await getCard(recordId);
      setData(payload);
      setForm((prev) => ({
        ...prev,
        Last_Contact_Date: result.last_contact_date || prev.Last_Contact_Date,
        Recommended_Action: result.recommended_action || 'SEND_CLIENT_EMAIL',
        Desk_Stage: result.desk_stage || prev.Desk_Stage,
      }));
    } catch (err) {
      setBanner(err.message);
    } finally {
      setSending(false);
    }
  }

  return (
    <div className={compact ? 'email-compact' : 'crm-section'}>
      {compact ? <h3>Send renewal review</h3> : <h2>Send client email</h2>}
      <div className={compact ? '' : 'section-body'}>
        {!recipients.length ? <p className="chip-msg warn">No email on the Contact. Add it in CRM.</p> : null}
        <div className="grid-3">
          <Field label="Template">
            <select value={templateId} onChange={(e) => setTemplateId(e.target.value)}>
              {drafts.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="To">
            <select value={toId} onChange={(e) => setToId(e.target.value)} disabled={!recipients.length}>
              {recipients.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name} · {item.email}
                </option>
              ))}
            </select>
          </Field>
          <Field label="From">
            <select value={fromEmail} onChange={(e) => setFromEmail(e.target.value)}>
              {fromAddresses.map((item) => (
                <option key={item.email} value={item.email}>
                  {item.user_name ? `${item.user_name} · ${item.email}` : item.email}
                </option>
              ))}
            </select>
          </Field>
        </div>
        {preview ? (
          <>
            <Field label="Subject">
              <input value={subject} onChange={(e) => setSubject(e.target.value)} />
            </Field>
            <Field label="Body">
              <textarea rows={compact ? 8 : 14} value={content} onChange={(e) => setContent(e.target.value)} />
            </Field>
          </>
        ) : null}
        <div className="action-buttons">
          <button type="button" className="secondary" onClick={() => setPreview((open) => !open)}>
            {preview ? 'Hide preview' : 'Preview email'}
          </button>
          <button type="button" className="secondary" onClick={send} disabled={sending || !recipients.length || !from}>
            {sending ? 'Sending…' : 'Send email'}
          </button>
        </div>
      </div>
    </div>
  );
}
