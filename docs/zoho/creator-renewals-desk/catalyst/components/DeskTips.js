const STORAGE_KEY = 'rsg.renewalsDesk.tipsOpen';

const ALWAYS = [
  {
    title: 'What this list is',
    body: 'This desk is a projection. NowCerts is the book of record. CRM follows AMS. Do not invent a policy here.',
  },
  {
    title: 'This screen never writes NowCerts',
    body: 'Request terms / Update AMS creates an AMS Write Queue job. Lamar approves it. Hermes is the only writer.',
  },
];

const BY_VIEW = {
  desk: [
    {
      title: 'Work the list',
      body: 'Filter by window (90 / 60 / 30 / personal / past due) or risk. Open a row to work the record. Premium change shows after you enter the renewal premium.',
    },
    {
      title: 'If a cancel is on this list',
      body: 'That means AMS and/or CRM is stale. Open NowCerts first. Do not queue an AMS job for a mid-term cancel.',
    },
  ],
  card: [
    {
      title: 'Send the client email from CRM',
      body: 'From defaults to Gretchen (gretchen@risksolutionsgroup.net). Replies go to her. Do not send from a personal mailbox. No yes/no form — ask them to reply “no changes” or list updates.',
    },
    {
      title: 'Premium',
      body: 'Enter Premium Renewal on the record. Change $ and % calculate from current. Empty renewal is blank, not −100%. Commission is not on this desk — the engine prices it after AMS and recon. This does not write NowCerts.',
    },
    {
      title: 'One way through',
      body: 'Use Next. Open the Zoho task for this stage, mark it Completed, then Next advances one stage. It will not skip. Closed needs Premium Renewal and a Disposition; then the Renewal locks.',
    },
    {
      title: 'Tasks after close',
      body: 'Won or Lost creates two Zoho tasks: send the thank-you email from CRM, and enter or update the data in NowCerts by hand. Download policies: watch NowCerts, do not type over AMS. The desk does not write AMS.',
    },
    {
      title: 'Leave without changing anything',
      body: 'Cancel, the Renewals breadcrumb, or Escape goes back to the worklist. Nothing is saved, queued, or dismissed.',
    },
    {
      title: 'Stage rules',
      body: 'No skipping. Closed needs a Disposition. Moving backward needs producer confirmation.',
    },
    {
      title: 'Dismiss is not delete',
      body: 'Dismiss takes it off the worklist and keeps tonight’s refresh from putting it back. Use this for mid-term cancels.',
    },
    {
      title: 'AMS Write Queue',
      body: 'Expected Result is required — what should be true in NowCerts after Hermes runs. Prepare options does not mutate AMS.',
    },
    {
      title: 'Do not “fix” a cancel here',
      body: 'If the policy cancelled mid-term, fix status in NowCerts (download or enter it), then Dismiss. Never Update AMS to change lifecycle.',
    },
  ],
  needs: [
    {
      title: 'Do not guess',
      body: 'Match the event to a Policy / Account in NowCerts. Do not create a policy from CRM.',
    },
    {
      title: 'Cancelled but still in-force dates',
      body: 'Hermes sends that here on purpose — dirty data. Confirm cancel date and status in NowCerts, then dismiss the desk row.',
    },
  ],
  'ams-pending': [
    {
      title: 'Approve is not a NowCerts write',
      body: 'Approve only queues the job for Hermes. Do not approve a job that tries to cancel or change policy lifecycle.',
    },
  ],
  'ams-failed': [
    {
      title: 'No silent retry',
      body: 'Read the error. Fix the facts in NowCerts if needed. Re-queue a new job from the renewal record — do not invent a retry here.',
    },
  ],
};

function DirtyDataSteps() {
  return (
    <ol className="tips-steps">
      <li>
        Open the policy in <strong>NowCerts</strong>. That answer wins.
      </li>
      <li>
        AMS already Cancelled / Flat Cancel → <strong>Dismiss</strong> the desk row. Tonight’s refresh should drop it.
      </li>
      <li>
        AMS still Active → enter or download the cancel <strong>in NowCerts</strong>, then Dismiss. Do not queue Update AMS.
      </li>
      <li>Not sure → leave it on Needs Verification. Do not guess.</li>
    </ol>
  );
}

export function tipsAreOpenByDefault() {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === '1';
  } catch {
    return false;
  }
}

export function persistTipsOpen(open) {
  try {
    window.localStorage.setItem(STORAGE_KEY, open ? '1' : '0');
  } catch {
    /* ignore */
  }
}

export default function DeskTips({ view }) {
  const tips = BY_VIEW[view] || BY_VIEW.desk;

  return (
    <aside className="desk-tips" aria-label="Help and training">
      <h2>Help & training</h2>
      <p className="tips-lede">Work happens in Zoho CRM. This desk only shows the next step.</p>

      <section className="tips-block">
        <h3>If AMS or CRM looks wrong</h3>
        <DirtyDataSteps />
      </section>

      {ALWAYS.map((item) => (
        <section key={item.title} className="tips-block">
          <h3>{item.title}</h3>
          <p>{item.body}</p>
        </section>
      ))}

      {tips.map((item) => (
        <section key={item.title} className="tips-block">
          <h3>{item.title}</h3>
          <p>{item.body}</p>
        </section>
      ))}
    </aside>
  );
}
