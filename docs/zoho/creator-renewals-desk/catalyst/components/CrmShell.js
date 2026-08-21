import { useState } from 'react';
import { isEmbedded } from '../crmLaunch';
import DeskTips, { persistTipsOpen, tipsAreOpenByDefault } from './DeskTips';

const TABS = [
  { view: 'desk', label: 'Worklist' },
  { view: 'needs', label: 'Needs Verification' },
  { view: 'ams-pending', label: 'AMS Pending' },
  { view: 'ams-failed', label: 'AMS Failed' },
];

export default function CrmShell({ route, onNavigate, banner, setBanner, children }) {
  const embedded = isEmbedded();
  const active = route.view === 'card' ? 'desk' : route.view;
  const [tipsOpen, setTipsOpen] = useState(() => tipsAreOpenByDefault());

  function toggleTips() {
    setTipsOpen((open) => {
      const next = !open;
      persistTipsOpen(next);
      return next;
    });
  }

  return (
    <div className={`crm-app${embedded ? ' embedded' : ''}${tipsOpen ? ' tips-open' : ''}`}>
      <header className="crm-topbar">
        <div className="crm-brand">
          {embedded ? null : <span className="crm-mark" aria-hidden="true">Z</span>}
          <div>
            {embedded ? null : <div className="crm-product">CRM</div>}
            <div className="crm-module">Renewals Desk</div>
          </div>
        </div>
        <nav className="crm-tabs" aria-label="Desk views">
          {TABS.map((tab) => (
            <button
              key={tab.view}
              type="button"
              className={`crm-tab${active === tab.view ? ' active' : ''}`}
              onClick={() => onNavigate({ view: tab.view })}
            >
              {tab.label}
            </button>
          ))}
        </nav>
        <button
          type="button"
          className={`crm-tips-toggle${tipsOpen ? ' active' : ''}`}
          aria-pressed={tipsOpen}
          onClick={toggleTips}
        >
          {tipsOpen ? 'Hide help' : 'Help'}
        </button>
      </header>
      {banner ? (
        <div className="crm-banner" role="status">
          <span>{banner}</span>
          <button type="button" className="zbtn-link" onClick={() => setBanner('')}>
            Dismiss
          </button>
        </div>
      ) : null}
      <div className="crm-body">
        <main className="crm-main">{children}</main>
        {tipsOpen ? <DeskTips view={route.view} /> : null}
      </div>
    </div>
  );
}
