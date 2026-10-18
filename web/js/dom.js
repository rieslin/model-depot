// Small helpers for building the page without a framework.

export function h(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key.startsWith('on')) node.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === 'class') node.className = value;
    else if (key in node && key !== 'list') node[key] = value;
    else node.setAttribute(key, value === true ? '' : value);
  }
  append(node, children);
  return node;
}

function append(node, children) {
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    if (Array.isArray(child)) append(node, child);
    else node.append(child instanceof Node ? child : String(child));
  }
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

// Replace a node's content. Unlike the browser's own append(), null and false children are skipped.
export function fill(node, ...children) {
  clear(node);
  append(node, children);
  return node;
}

export function formatBytes(n) {
  if (n === null || n === undefined) return '';
  if (n >= 1024 ** 3) return (n / 1024 ** 3).toFixed(n >= 100 * 1024 ** 3 ? 0 : 1) + ' GB';
  if (n >= 1024 ** 2) return (n / 1024 ** 2).toFixed(n >= 100 * 1024 ** 2 ? 0 : 1) + ' MB';
  if (n >= 1024) return Math.round(n / 1024) + ' KB';
  return n + ' B';
}

export function formatDuration(seconds) {
  if (seconds === null || seconds === undefined || !isFinite(seconds)) return '';
  const s = Math.max(0, Math.round(seconds));
  const m = Math.floor(s / 60);
  if (m >= 60) return Math.floor(m / 60) + ':' + String(m % 60).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0');
  return m + ':' + String(s % 60).padStart(2, '0');
}
