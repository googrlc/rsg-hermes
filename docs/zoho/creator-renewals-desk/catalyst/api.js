const API_BASE = process.env.REACT_APP_API_BASE || '/server/renewals_desk_function';

export class ApiError extends Error {
  constructor(status, payload) {
    super((payload && payload.error) || `Request failed (${status})`);
    this.status = status;
    this.payload = payload;
  }
}

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      Accept: 'application/json',
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      ...(options.headers || {}),
    },
    ...options,
  });
  const text = await response.text();
  let json = null;
  if (text) {
    try {
      json = JSON.parse(text);
    } catch {
      json = { error: text };
    }
  }
  if (!response.ok) throw new ApiError(response.status, json);
  return json;
}

export function getDesk(params = {}) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value) query.set(key, value);
  });
  const suffix = query.toString() ? `?${query}` : '';
  return request(`/api/desk${suffix}`);
}

export function getHealth() {
  return request('/api/health');
}

export function getCard(id) {
  return request(`/api/desk/renewals/${encodeURIComponent(id)}`);
}

export function patchRenewal(id, body) {
  return request(`/api/desk/renewals/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  });
}

export function closeRenewal(id, body) {
  return request(`/api/desk/renewals/${encodeURIComponent(id)}/close`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function nextRenewal(id, body) {
  return request(`/api/desk/renewals/${encodeURIComponent(id)}/next`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function sendClientEmail(id, body) {
  return request(`/api/desk/renewals/${encodeURIComponent(id)}/email`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function enqueueAms(id, body) {
  return request(`/api/desk/renewals/${encodeURIComponent(id)}/ams`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function dismissRenewal(id) {
  return request(`/api/desk/renewals/${encodeURIComponent(id)}/dismiss`, {
    method: 'POST',
    body: JSON.stringify({}),
  });
}

export function getNeedsVerification() {
  return request('/api/desk/needs-verification');
}

export function getAmsQueue(view) {
  return request(`/api/desk/ams?view=${encodeURIComponent(view)}`);
}

export function approveAms(id) {
  return request(`/api/desk/ams/${encodeURIComponent(id)}/approve`, {
    method: 'POST',
    body: JSON.stringify({}),
  });
}

export function patchTask(id, body) {
  return request(`/api/desk/tasks/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  });
}

export function completeCheckpoint(id, key, body) {
  return request(
    `/api/desk/renewals/${encodeURIComponent(id)}/checkpoints/${encodeURIComponent(key)}/complete`,
    {
      method: 'POST',
      body: JSON.stringify(body || {}),
    },
  );
}

export function lookupId(value) {
  if (!value) return '';
  if (typeof value === 'object') return String(value.id || '');
  return String(value);
}

export function hasPipelineDeal(row) {
  return Boolean(lookupId(row && (row.Deal_Id || row.Related_Deal)));
}

export function money(value) {
  if (value == null || value === '') return '—';
  const n = Number(value);
  if (Number.isNaN(n)) return String(value);
  return n.toLocaleString('en-US', { style: 'currency', currency: 'USD' });
}

export function moneyInput(value) {
  if (value == null || value === '') return '';
  const n = Number(String(value).replace(/[$,\s]/g, ''));
  return Number.isNaN(n) ? '' : String(n);
}

export function moneyChange(current, next) {
  const from = current == null || current === '' ? null : Number(String(current).replace(/[$,\s]/g, ''));
  const to = next == null || next === '' ? null : Number(String(next).replace(/[$,\s]/g, ''));
  if (from == null || Number.isNaN(from) || to == null || Number.isNaN(to)) {
    return { amount: null, percent: null, direction: null };
  }
  const amount = Math.round((to - from) * 100) / 100;
  const percent = from === 0 ? null : Math.round(((amount / from) * 100) * 10) / 10;
  let direction = 'flat';
  if (amount > 0) direction = 'increase';
  if (amount < 0) direction = 'decrease';
  return { amount, percent, direction };
}

export function formatChange(current, next) {
  const change = moneyChange(current, next);
  if (!change.direction) {
    return { text: '—', direction: null, word: '', amountText: '—', percentText: '' };
  }
  const amountText = change.amount > 0 ? `+${money(change.amount)}` : money(change.amount);
  const percentText =
    change.percent == null ? '' : `${change.percent > 0 ? '+' : ''}${change.percent.toFixed(1)}%`;
  const word =
    change.direction === 'increase' ? 'Increase' : change.direction === 'decrease' ? 'Decrease' : 'No change';
  return {
    ...change,
    word,
    amountText,
    percentText,
    text: percentText ? `${amountText} · ${percentText}` : amountText,
  };
}

export function isoDate(value) {
  if (!value) return '—';
  return String(value).slice(0, 10);
}

const COMMERCIAL_MARKER = 'commercial';
const PERSONAL_LOB_KEYWORDS = [
  'auto',
  'automobile',
  'home',
  'homeowner',
  'dwelling',
  'renter',
  'umbrella',
  'boat',
  'watercraft',
  'rv',
  'motorcycle',
  'personal',
  'ho3',
  'ho-3',
  'ho6',
  'ho-6',
  'condo',
];

export function lobText(lob) {
  if (lob && typeof lob === 'object') return String(lob.name || lob.Line_of_Business || '');
  return String(lob || '');
}

export function isPersonalLob(lob) {
  const text = lobText(lob).trim().toLowerCase();
  if (!text || text.includes(COMMERCIAL_MARKER)) return false;
  return PERSONAL_LOB_KEYWORDS.some((keyword) => text.includes(keyword));
}

export function accountType(lob) {
  return isPersonalLob(lob) ? 'Personal' : 'Commercial';
}

export function statusKind(row) {
  const window = String(row.Window_Bucket || '');
  const risk = String(row.Risk_Status || '').toUpperCase();
  const days = row.Days_To_Expiration;
  if (window === 'past_due' || risk === 'CRITICAL' || (typeof days === 'number' && days < 0)) {
    return 'overdue';
  }
  if (window === '30' || risk === 'AT_RISK') return 'attention';
  return 'normal';
}

export function lookupName(value) {
  if (!value) return '';
  if (typeof value === 'object') return value.name || value.Account_Name || '';
  return String(value);
}
