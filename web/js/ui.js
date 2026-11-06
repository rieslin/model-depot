// Small pieces shared by the library and the dossier.

// What the server says about itself (/api/ui/state): the page's settings (its "page" group of advanced settings), the
// limits of what can be typed or added, the base-model families, the sites links are read from. Set at start, and
// again whenever the state is read.
let known = { page: {}, limits: {}, hosts: {}, families: {}, kinds: [], extensions: [], config: {} };

export function setServerState(state) {
  if (state) known = state;
}

export function serverState() {
  return known;
}

export function pageSetting(key) {
  return known.page[key];
}

// civitai's rating from which pictures are masked unless the eye switch is off (R, as shipped).
export function maskFrom() {
  return known.page.mask_from;
}

export function levelName(level) {
  if (!level) return '';
  if (level >= 16) return 'XXX';
  if (level >= 8) return 'X';
  if (level >= 4) return 'R';
  if (level >= 2) return 'PG-13';
  return 'PG';
}

// A viewer's own choices (the type in view, the eye switch, copying the optional trigger words too), kept in this
// browser. Storage can be missing (a private window, blocked site data): then the default, and a choice holds for this
// visit only.
export function recall(key, fallback) {
  try {
    const value = localStorage.getItem(key);
    return value === null ? fallback : value;
  } catch (e) {
    return fallback;
  }
}

export function remember(key, value) {
  try { localStorage.setItem(key, value); } catch (e) { /* kept for this visit only */ }
}

export function icon(id) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('class', 'i');
  svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  use.setAttribute('href', '#' + id);
  svg.append(use);
  return svg;
}

// Put text on the clipboard. The page is served from this computer (a secure context), but over a plain address
// from another device the clipboard API is missing: then the old way.
export async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (e) { /* fall through */ }
  const area = document.createElement('textarea');
  area.value = text;
  area.setAttribute('readonly', '');
  area.style.position = 'fixed';
  area.style.opacity = '0';
  document.body.append(area);
  area.select();
  let ok = false;
  try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
  area.remove();
  return ok;
}

// The name a model goes by, in the interface's language: a name settled with both an English and a Chinese part
// (title_parts) shows the part in that language; otherwise the name as it is.
export function titleOf(m, lang) {
  const parts = m && m.title_parts;
  if (parts && parts.en && parts.zh) return lang === 'zh' ? parts.zh : parts.en;
  return m ? m.title : '';
}

export function baseName(rel) {
  const name = String(rel || '').split(/[\\/]/).pop();
  return name.replace(/\.[^.]+$/, '');
}
