const DESK_ORIGIN =
  process.env.REACT_APP_DESK_ORIGIN ||
  'https://renewals-desk-935150771.development.catalystserverless.com/app/index.html';

export function isEmbedded() {
  try {
    return window.self !== window.top;
  } catch {
    return true;
  }
}

function firstId(value) {
  if (value == null || value === '') return '';
  if (Array.isArray(value)) return String(value[0] || '');
  return String(value);
}

export function consumeCrmQuery() {
  const params = new URLSearchParams(window.location.search);
  const id = firstId(
    params.get('id') ||
      params.get('recordId') ||
      params.get('entityId') ||
      params.get('EntityId') ||
      params.get('renewal_id'),
  );
  const policy = firstId(params.get('policy') || params.get('Policy_Number'));
  const moduleName = params.get('module') || params.get('Entity') || params.get('moduleName') || '';
  const token = id || policy;
  if (!token) return null;
  if (moduleName && !/renewal|policy/i.test(moduleName) && !id) return null;
  const hash = `#/renewals/${encodeURIComponent(token)}`;
  const next = `${window.location.pathname}${hash}`;
  if (`${window.location.pathname}${window.location.hash}` !== next) {
    window.history.replaceState(null, '', next);
  }
  return { view: 'card', id: token };
}

export function parseRoute() {
  const fromQuery = consumeCrmQuery();
  if (fromQuery && !(window.location.hash || '').includes('/renewals/')) {
    return fromQuery;
  }
  const raw = (window.location.hash || '#/').replace(/^#/, '') || '/';
  const url = new URL(raw, 'http://desk.local');
  const parts = url.pathname.split('/').filter(Boolean);
  if (parts[0] === 'renewals' && parts[1]) {
    return { view: 'card', id: decodeURIComponent(parts[1]) };
  }
  if (parts[0] === 'needs-verification') return { view: 'needs' };
  if (parts[0] === 'ams' && parts[1] === 'failed') return { view: 'ams-failed' };
  if (parts[0] === 'ams') return { view: 'ams-pending' };
  return {
    view: 'desk',
    window: url.searchParams.get('window') || '',
    risk: url.searchParams.get('risk') || '',
    stage: url.searchParams.get('stage') || '',
    type: url.searchParams.get('type') || '',
    q: url.searchParams.get('q') || '',
  };
}

export function writeRoute(route) {
  if (route.view === 'card') {
    window.location.hash = `#/renewals/${route.id}`;
    return;
  }
  if (route.view === 'needs') {
    window.location.hash = '#/needs-verification';
    return;
  }
  if (route.view === 'ams-failed') {
    window.location.hash = '#/ams/failed';
    return;
  }
  if (route.view === 'ams-pending') {
    window.location.hash = '#/ams/pending';
    return;
  }
  const params = new URLSearchParams();
  ['window', 'risk', 'stage', 'type', 'q'].forEach((key) => {
    if (route[key]) params.set(key, route[key]);
  });
  const query = params.toString();
  window.location.hash = query ? `#/?${query}` : '#/';
}

export function listenCrmWidget(onRecord) {
  const zoho = window.ZOHO;
  if (!zoho || !zoho.embeddedApp) return () => {};
  const handler = (data) => {
    const entity = data && (data.Entity || data.module);
    const id = firstId(data && (data.EntityId || data.id));
    if (id) onRecord(id, entity || 'Renewals');
  };
  zoho.embeddedApp.on('PageLoad', handler);
  zoho.embeddedApp.init();
  return () => {};
}

export function deskOrigin() {
  return DESK_ORIGIN.replace(/\/$/, '');
}

export function crmLaunchUrls() {
  const origin = deskOrigin();
  const sep = origin.includes('?') ? '&' : '?';
  return {
    web_tab: origin,
    renewals_button: `${origin}${sep}id=\${Renewals.id}&module=Renewals`,
    renewals_button_alt: `${origin}#/renewals/\${Renewals.id}`,
    policies_button: `${origin}${sep}policy=\${Policies.Policy_Number}&module=Policies`,
  };
}

export function crmOrigin() {
  try {
    if (document.referrer) {
      const parsed = new URL(document.referrer);
      if (/zoho/i.test(parsed.hostname)) return parsed.origin;
    }
  } catch {
    /* stay on US CRM */
  }
  return 'https://crm.zoho.com';
}

export function crmRecordUrl(entity, recordId) {
  const id = String(recordId || '').trim();
  if (!id) return '';
  const moduleName = entity || 'Tasks';
  return `${crmOrigin()}/crm/tab/${encodeURIComponent(moduleName)}/${encodeURIComponent(id)}`;
}

export async function openCrmRecord(entity, recordId, fallbackUrl) {
  const id = String(recordId || '').trim();
  const url = fallbackUrl || crmRecordUrl(entity, id);
  if (!id && !url) return false;

  const zoho = window.ZOHO;
  if (zoho && zoho.CRM && zoho.CRM.UI && zoho.CRM.UI.Record && id) {
    try {
      await zoho.CRM.UI.Record.open({ Entity: entity || 'Tasks', RecordID: id });
      return true;
    } catch {
      /* fall through to a real navigation */
    }
  }

  if (!url) return false;
  const target = isEmbedded() ? '_top' : '_blank';
  try {
    const opened = window.open(url, target);
    if (opened) return true;
  } catch {
    /* iframe may block window.open */
  }

  const link = document.createElement('a');
  link.href = url;
  link.target = target;
  link.rel = 'noopener noreferrer';
  document.body.appendChild(link);
  link.click();
  link.remove();
  return true;
}
