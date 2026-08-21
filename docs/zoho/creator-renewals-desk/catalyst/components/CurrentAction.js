import { nextRenewal, getCard, completeCheckpoint as postCheckpoint, patchTask } from '../api';
import { crmRecordUrl, isEmbedded, openCrmRecord } from '../crmLaunch';
import { currentWorkStep, taskIsDone } from '../workflow';
import {
  checkpointsForStage,
  completeCheckpoint as markCheckpoint,
  mergeCheckpointStates,
  storedDeskStage,
} from '../operating';
import ClientEmail from './ClientEmail';
import CloseOut from './CloseOut';
import PremiumFields from './PremiumFields';

function taskTarget(step, data) {
  const fromList = (data.tasks || []).find(
    (task) => String(task.Subject || '').trim() === String(step.task_title || '').trim(),
  );
  const id = String((step && step.task_id) || (fromList && fromList.id) || '').trim();
  const url = (step && step.task_url) || crmRecordUrl('Tasks', id);
  return { id, url };
}

async function openTask(target, event) {
  if (event && target.url) {
    const zoho = window.ZOHO;
    if (zoho && zoho.CRM && zoho.CRM.UI && zoho.CRM.UI.Record && target.id) {
      event.preventDefault();
      try {
        await zoho.CRM.UI.Record.open({ Entity: 'Tasks', RecordID: target.id });
        return;
      } catch {
        window.open(target.url, isEmbedded() ? '_top' : '_blank');
        return;
      }
    }
    return;
  }
  if (target.id || target.url) await openCrmRecord('Tasks', target.id, target.url);
}

export default function CurrentAction({ recordId, data, form, setData, setForm, setBanner, saving, onSavePremium }) {
  const step = data.next || {};
  const action = step.action || '';
  const stage = (data.renewal && data.renewal.Desk_Stage) || form.Desk_Stage || 'Identified';
  const work = currentWorkStep(stage);
  const target = taskTarget(step, data);
  const opensTask = action === 'open_task' || action === 'open_closeout_task';
  const canContinue = action === 'advance_stage';
  const needsPremium = action === 'enter_premium';
  const needsClose = action === 'set_disposition' || action === 'close_and_update' || action === 'close_lost';
  const finished = action === 'done';
  const showEmail = stage === 'Proposal Sent' && !finished;
  const showPremium = (stage === 'Negotiating' || needsPremium) && !needsClose && !finished;
  const renewal = data.renewal || {};
  const states = mergeCheckpointStates(renewal, data.tasks || []);
  const checkpoints = checkpointsForStage(storedDeskStage(renewal), states);
  const title = finished
    ? 'Renewal closed'
    : opensTask && stage === 'Closed'
      ? 'Finish CRM follow-up'
      : needsPremium
        ? 'Enter renewal premium'
        : needsClose
          ? 'Close renewal'
          : canContinue
            ? `Continue to ${step.next_stage || 'next step'}`
            : work
              ? work.label
              : 'Current step';

  async function completeOnCard(key) {
    const result = markCheckpoint(storedDeskStage(renewal), states, key, { actor: 'user' });
    if (!result.ok) {
      setBanner(result.error || 'Could not complete checkpoint');
      return;
    }
    try {
      await postCheckpoint(recordId, key, { actor: 'user' });
    } catch {
      const subjects = [result.title, ...(result.aliases || [])].map((item) => String(item || '').trim().toLowerCase());
      const task = (data.tasks || []).find((row) => subjects.includes(String(row.Subject || '').trim().toLowerCase()));
      if (result.task_complete && task && task.id && !taskIsDone(task.Status)) {
        try {
          await patchTask(task.id, { Status: 'Completed' });
        } catch {
          /* function patch not live yet */
        }
      }
    }
    try {
      const card = await getCard(recordId);
      setData(card);
      setBanner(
        result.task_complete
          ? 'Stage task completed in the background. Continue is enabled.'
          : 'Checkpoint recorded on the renewal. Continue stays gated until the stage CRM task is done.',
      );
    } catch (err) {
      setBanner(err.message);
    }
  }

  async function continueNext() {
    try {
      const payload = await nextRenewal(recordId, {
        Premium_Current: form.Premium_Current,
        Premium_Renewal: form.Premium_Renewal,
        Disposition: form.Disposition,
        ams_path: form.Is_Download ? 'download' : 'manual',
      });
      const card = await getCard(recordId);
      setData(card);
      if (card.renewal) {
        setForm((prev) => ({
          ...prev,
          Desk_Stage: card.renewal.Desk_Stage || prev.Desk_Stage,
          Disposition: card.renewal.Disposition || prev.Disposition,
        }));
      }
      setBanner('');
      return payload;
    } catch (err) {
      setBanner(err.message);
      return null;
    }
  }

  return (
    <section className="action-card" aria-live="polite">
      <div className="action-kicker">{finished ? 'Done' : 'Now'}</div>
      <h2>{title}</h2>
      {opensTask ? <p className="action-task">{step.task_title || 'CRM task'}</p> : null}

      {checkpoints.length ? (
        <div className="checkpoint-list">
          <p className="muted">Complete on this card. Hermes marks the matching Zoho task. Continue stays disabled until that task is Completed.</p>
          {checkpoints.map((item) => (
            <div className="checkpoint" key={item.key}>
              <span aria-hidden="true">{item.status === 'Complete' ? '✅' : item.required ? '⬜' : '·'}</span>
              <div>
                <strong>{item.title}</strong>
                {item.required ? <span className="muted"> · required</span> : null}
                <div className="muted">{item.status}</div>
              </div>
              {item.status === 'Complete' ? (
                <span className="muted">Done</span>
              ) : (
                <button type="button" className="secondary" onClick={() => completeOnCard(item.key)}>
                  Complete
                </button>
              )}
            </div>
          ))}
        </div>
      ) : null}

      {showPremium ? (
        <PremiumFields form={form} setForm={setForm} renewalInputId="premium-renewal" />
      ) : null}

      {opensTask ? (
        <div className="action-buttons">
          {target.url ? (
            <a className="btn" href={target.url} target="_top" rel="noopener noreferrer" onClick={(event) => openTask(target, event)}>
              Open CRM task
            </a>
          ) : (
            <button type="button" disabled title="Task is still being created">
              Open CRM task
            </button>
          )}
          <button type="button" className="secondary" disabled title="Complete CRM task first">
            Continue
          </button>
        </div>
      ) : null}

      {canContinue ? (
        <div className="action-buttons">
          <button type="button" onClick={continueNext}>
            Continue
          </button>
        </div>
      ) : null}

      {needsPremium ? (
        <div className="action-buttons">
          <button type="button" onClick={onSavePremium} disabled={saving || !String(form.Premium_Renewal || '').trim()}>
            {saving ? 'Saving…' : 'Save premium'}
          </button>
        </div>
      ) : null}

      {needsClose ? (
        <CloseOut
          recordId={recordId}
          data={data}
          form={form}
          setData={setData}
          setForm={setForm}
          setBanner={setBanner}
          compact
        />
      ) : null}

      {finished ? <p className="action-done">This renewal is complete.</p> : null}

      {showEmail ? (
        <div className="action-extra">
          <ClientEmail
            recordId={recordId}
            data={data}
            setData={setData}
            setForm={setForm}
            setBanner={setBanner}
            compact
          />
        </div>
      ) : null}
    </section>
  );
}
