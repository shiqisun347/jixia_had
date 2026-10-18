export const phases = {
  intro: '主持开场',
  vote: '观众投票',
  review: '评委点评',
  results: '结果公布',
};
export const sideNames = {
  AFFIRMATIVE: '正方',
  NEGATIVE: '反方',
  DRAW: '平局',
};
export const escape = (v) =>
  String(v ?? '').replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c],
  );
export const nameOf = (content, id) =>
  content.participants.find((p) => p.id === id)?.name ?? '尚未公布';
export function toast(message) {
  const el = document.querySelector('#toast');
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (el.hidden = true), 4500);
}
export async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: 'same-origin',
    cache: 'no-store',
    signal: AbortSignal.timeout(8000),
    ...options,
    headers: { 'Content-Type': 'application/json', ...options.headers },
  });
  const data = await response.json();
  if (!response.ok) {
    const err = new Error(
      typeof data.detail === 'string' ? data.detail : '提交内容不完整，请检查后重试。',
    );
    err.status = response.status;
    throw err;
  }
  return data;
}
export const send = (path, body, method = 'POST') =>
  api(path, { method, body: JSON.stringify(body) });
export function brand() {
  return `<div class="brand"><img src="/assets/brand.jpeg" alt="稷下"><span><strong>稷下<span class="brand-dot">·</span>争鸣</strong><small>人与智能，共赴思想之约</small></span></div>`;
}
