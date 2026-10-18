// Calls to the server. Every call says which language the answer's sentences should be in.
import { currentLang } from './i18n.js';

export class ApiError extends Error {
  constructor(message, code, status, data) {
    super(message);
    this.code = code;
    this.status = status;
    this.data = data;
  }
}

async function send(method, path, headers, body) {
  let resp;
  try {
    resp = await fetch(path, { method, headers, body });
  } catch (e) {
    throw new ApiError(String(e.message || e), 'network', 0, null);
  }
  let data = null;
  try {
    data = await resp.json();
  } catch (e) {
    data = null;
  }
  if (!resp.ok) throw new ApiError((data && data.error) || resp.statusText, data && data.code, resp.status, data);
  return data;
}

export function api(method, path, body) {
  const headers = { 'X-Lang': currentLang() };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  return send(method, path, headers, body === undefined ? undefined : JSON.stringify(body));
}

// A file sent as it is (a picture added to a model's examples), its name in a header.
export function upload(path, file, type) {
  return send('POST', path, { 'X-Lang': currentLang(), 'Content-Type': type || file.type || 'application/octet-stream',
    'X-File-Name': encodeURIComponent(file.name || '') }, file);
}
