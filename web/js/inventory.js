// The inventory: how many files, how many recognised, which are not, and how far the background work is.
// Deliberately bare: it is here to check the scan, not to be the model library page.
import { api } from './api.js';
import { h, clear, fill, formatBytes, formatDuration } from './dom.js';
import { t, currentLang } from './i18n.js';

const STATUS_KEYS = { civitai: 'inv.status_civitai', local: 'inv.status_local', no_check: 'inv.status_no_check', unidentified: 'inv.status_unidentified',
  waiting: 'inv.status_waiting', error: 'inv.status_error' };
const STATUS_CLASS = { civitai: 'good', local: 'good', no_check: 'dim', unidentified: 'warn', waiting: 'dim', error: 'bad' };
// Files of other kinds (detection and face-swap models of custom nodes and the like) are not shown anywhere, here
// neither: the counts below leave them out too. The program still keeps them in its index.
const KIND_ORDER = ['checkpoint', 'diffusion_model', 'lora', 'embedding', 'vae', 'text_encoder', 'upscaler', 'controlnet'];
const HIDDEN_KIND = 'other';

// The counts without the hidden kind.
function shownSummary(s) {
  const hidden = s.by_kind[HIDDEN_KIND];
  if (!hidden) return s;
  const out = { ...s, by_kind: { ...s.by_kind } };
  delete out.by_kind[HIDDEN_KIND];
  for (const key of ['files', 'bytes', 'civitai', 'local', 'identified', 'no_check', 'unidentified', 'waiting', 'error']) out[key] -= hidden[key] || 0;
  return out;
}

let timer = null;
let current = 0;      // goes up each time the view is drawn or left; an older view's refresh loop then stops itself

export function stopInventory() {
  current += 1;
  if (timer) clearTimeout(timer);
  timer = null;
}

export function renderInventory(root) {
  stopInventory();
  const mine = current;
  const view = { revision: -1, files: [], summary: null, progress: null, status: 'unidentified', kind: '', query: '',
    message: null, loading: false, fetchedAt: 0, fillNeeds: null, fillRunning: false, fillReading: 0, needsAt: 0,
    needsLoading: false };

  // What filling in the details would do: counted on the server over every model, so read when the page opens and
  // when something changed it, not with every refresh.
  async function loadNeeds() {
    if (view.needsLoading) return;
    view.needsLoading = true;
    view.needsAt = Date.now();
    try {
      const data = await api('GET', '/api/ui/fill');
      if (mine !== current) return;
      view.fillNeeds = data.needs;
      if (view.progress) { view.progress.fill = data.progress; drawWork(); }
    } catch (e) { /* the counts stay as they were */ } finally {
      view.needsLoading = false;
    }
  }

  const numbers = h('div', { class: 'numbers' });
  const work = h('div');
  const kinds = h('div');
  const filters = h('div', { class: 'bar' });
  const table = h('div');
  const message = h('p');

  fill(root,
    h('h1', null, t('inv.title')),
    numbers,
    h('h2', null, t('inv.progress')),
    work,
    message,
    h('h2', null, t('inv.by_kind')),
    kinds,
    h('h2', null, t('inv.files')),
    filters,
    table,
  );

  const act = async (fn, done) => {
    try {
      await fn();
      view.message = done ? { text: done, cls: 'dim' } : null;
    } catch (e) {
      view.message = { text: e.message, cls: 'bad' };
    }
    drawMessage();
    tick(true);
  };

  function drawMessage() {
    clear(message);
    if (view.message) { message.className = view.message.cls; message.append(view.message.text); }
  }

  function drawNumbers() {
    const s = view.summary;
    if (!s) return;
    const box = (label, value, extra) => h('div', null, h('b', null, String(value)), label, extra ? h('div', { class: 'dim' }, extra) : null);
    fill(numbers,
      box(t('inv.n_files'), s.files, formatBytes(s.bytes)),
      box(t('inv.n_identified'), s.identified, t('inv.n_identified_split', { civitai: s.civitai, local: s.local })),
      box(t('inv.n_unidentified'), s.unidentified),
      s.no_check ? box(t('inv.n_no_check'), s.no_check) : null,
      box(t('inv.n_waiting'), s.waiting),
      s.error ? box(t('inv.n_error'), s.error) : null,
    );
  }

  function drawWork() {
    const p = view.progress;
    if (!p) return;
    const lines = [];
    // scan
    const scan = p.scan;
    lines.push(h('p', null, h('b', null, t('inv.scan') + ' '),
      scan.running ? t('inv.scan_running')
        : scan.error ? h('span', { class: 'bad' }, scan.error)
          : scan.finished_at ? t('inv.scan_done', { time: new Date(scan.finished_at * 1000).toLocaleTimeString() }) : t('inv.scan_never')));
    const missing = (scan.folders || []).filter((f) => !f.exists);
    if (missing.length) lines.push(h('p', { class: 'warn' }, t('inv.folders_missing', { list: missing.map((f) => f.path).join(' ; ') })));
    // fingerprints
    const hash = p.hash;
    const hashText = t('inv.hash_counts', { done: hash.done_files, total: hash.files, done_bytes: formatBytes(hash.done_bytes), total_bytes: formatBytes(hash.bytes) });
    const current = hash.current.map((c) => c.name + ' ' + Math.floor(100 * c.done / Math.max(1, c.bytes)) + '%').join(' ; ');
    lines.push(h('div', { class: 'bar' }, h('b', null, t('inv.hash')),
      h('progress', { max: Math.max(1, hash.bytes), value: hash.done_bytes }), hashText,
      hash.running && !hash.paused && hash.speed ? h('span', { class: 'dim' }, t('inv.hash_speed', { speed: formatBytes(hash.speed), eta: formatDuration(hash.eta) })) : null,
      hash.paused ? h('span', { class: 'warn' }, t('inv.hash_paused')) : null,
      hash.done_files < hash.files || hash.paused
        ? h('button', { onclick: () => act(() => api('POST', '/api/ui/hashing', { paused: !hash.paused })) }, hash.paused ? t('inv.resume') : t('inv.pause'))
        : null));
    if (current) lines.push(h('p', { class: 'dim' }, t('inv.hash_current', { name: current })));
    if (hash.errors) lines.push(h('p', { class: 'bad' }, t('inv.hash_errors', { n: hash.errors })));
    // civitai
    const civ = p.civitai;
    lines.push(h('p', null, h('b', null, t('inv.civitai') + ' '),
      t('inv.civitai_counts', { found: civ.found, not_found: civ.not_found, bundled: civ.bundled, pending: civ.pending }),
      civ.running ? ' ' + t('inv.civitai_running') : '',
      civ.last_error ? h('span', { class: 'bad' }, ' ' + t('inv.civitai_error', { code: civ.last_error })) : null,
      civ.waiting_until ? h('span', { class: 'dim' }, ' ' + t('inv.civitai_retry', { time: new Date(civ.waiting_until * 1000).toLocaleTimeString() })) : null));
    // covers
    lines.push(h('p', null, h('b', null, t('inv.covers') + ' '), t('inv.covers_counts', { ready: p.covers.ready, failed: p.covers.failed }),
      p.covers.running ? ' ' + t('inv.covers_running') : ''));
    // filling in every model's page, example images and notes at once
    const fl = p.fill;
    const needs = view.fillNeeds;
    let state = '';
    if (fl.running) state = t('inv.fill_running', fl);
    else if (needs) {
      state = !needs.pages && !needs.samples && !needs.notes ? t('inv.fill_complete')
        : t(needs.reader_ready ? 'inv.fill_needs' : 'inv.fill_needs_rules', needs);
    }
    const last = fl.running || !fl.finished_at ? null
      : fl.error ? h('span', { class: 'bad' }, t('inv.fill_error', { code: fl.error }))
        : t(fl.stopped ? 'inv.fill_stopped' : 'inv.fill_done', { time: new Date(fl.finished_at * 1000).toLocaleTimeString() });
    lines.push(h('div', { class: 'bar' }, h('b', null, t('inv.fill')), state,
      fl.running
        ? h('button', { onclick: () => act(() => api('POST', '/api/ui/fill', { action: 'stop' })) }, t('inv.fill_stop'))
        : h('button', { onclick: () => act(() => api('POST', '/api/ui/fill', { action: 'start' })) }, t('inv.fill_start'))));
    const after = [fl.reading ? t('inv.fill_reading', { n: fl.reading }) : null, last,
      fl.failed ? t('inv.fill_failed', { n: fl.failed }) : null].filter(Boolean);
    const sep = currentLang() === 'zh' ? '；' : '; ';
    if (after.length) lines.push(h('p', { class: 'dim' }, after.flatMap((x, i) => (i ? [sep, x] : [x]))));
    // the counts of what is missing are read again when a run ends, and while the local model works through its queue
    if (view.fillRunning && !fl.running) loadNeeds();
    if (!fl.running && fl.reading !== view.fillReading && Date.now() - view.needsAt > 10000) loadNeeds();
    view.fillRunning = fl.running;
    view.fillReading = fl.reading;
    // checking every civitai page for new versions: only when the user asks (2026-10-14)
    const up = p.updates;
    if (up) {
      const counts = up.counts || { newer: 0, entries: 0 };
      const found = !up.checked_at && !up.finished_at ? t('inv.updates_never')
        : counts.newer || counts.entries ? t('inv.updates_found', counts) : t('inv.updates_none');
      lines.push(h('div', { class: 'bar' }, h('b', null, t('inv.updates')), up.running ? t('inv.updates_running', up) : found,
        up.running
          ? h('button', { onclick: () => act(() => api('POST', '/api/ui/updates', { action: 'stop' })) }, t('inv.updates_stop'))
          : h('button', { onclick: () => act(() => api('POST', '/api/ui/updates', { action: 'start' })) }, t('inv.updates_start'))));
      const minutes = Math.max(1, Math.ceil((up.pages || 0) * 2 / 60));
      const when = up.checked_at ? new Date(up.checked_at * 1000).toLocaleString() : null;
      const about = up.running ? null
        : up.error ? h('span', { class: 'bad' }, t('inv.updates_error', { code: up.error }))
          : up.stopped && up.finished_at ? t('inv.updates_stopped', { time: new Date(up.finished_at * 1000).toLocaleTimeString() })
            : when ? t('inv.updates_last', { time: when, pages: up.pages, minutes }) : t('inv.updates_first', { pages: up.pages, minutes });
      const tail = [about, up.failed && !up.running ? t('inv.updates_failed', { n: up.failed }) : null].filter(Boolean);
      if (tail.length) lines.push(h('p', { class: 'dim' }, tail.flatMap((x, i) => (i ? [sep, x] : [x]))));
    }
    lines.push(h('div', { class: 'actions' },
      h('button', { onclick: () => act(() => api('POST', '/api/v1/scan', {}), t('inv.rescan_started')) }, t('inv.rescan')),
      h('button', { onclick: () => act(() => api('POST', '/api/ui/recheck', { all: false }), t('inv.recheck_started')) }, t('inv.recheck')),
      h('button', { onclick: () => { if (window.confirm(t('inv.rebuild_confirm'))) act(() => api('POST', '/api/ui/rebuild', {}), t('inv.rebuild_started')); } }, t('inv.rebuild'))));
    fill(work, lines);
  }

  function drawKinds() {
    const s = view.summary;
    if (!s) return;
    const names = Object.keys(s.by_kind).sort((a, b) => KIND_ORDER.indexOf(a) - KIND_ORDER.indexOf(b));
    fill(kinds, h('table', null,
      h('thead', null, h('tr', null, h('th', null, t('inv.col_kind')), h('th', { class: 'num' }, t('inv.n_files')),
        h('th', { class: 'num' }, t('inv.col_size')), h('th', { class: 'num' }, t('inv.n_identified')),
        h('th', { class: 'num' }, t('inv.n_unidentified')), h('th', { class: 'num' }, t('inv.n_no_check')),
        h('th', { class: 'num' }, t('inv.n_waiting')))),
      h('tbody', null, names.map((k) => {
        const row = s.by_kind[k];
        return h('tr', null, h('td', null, t('kind.' + k)), h('td', { class: 'num' }, row.files),
          h('td', { class: 'num' }, formatBytes(row.bytes)), h('td', { class: 'num' }, row.identified),
          h('td', { class: 'num' }, row.unidentified), h('td', { class: 'num' }, row.no_check || 0),
          h('td', { class: 'num' }, row.waiting + row.error));
      }))));
  }

  // The filters are built once: redrawing them with every refresh would take the cursor out of the search box.
  filters.append(
    h('label', null, t('inv.filter_status'),
      h('select', { onchange: (e) => { view.status = e.target.value; drawTable(); } },
        ['unidentified', 'no_check', 'local', 'civitai', 'waiting', 'error', ''].map((s) => h('option', { value: s, selected: view.status === s }, s ? t(STATUS_KEYS[s]) : t('inv.all'))))),
    h('label', null, t('inv.filter_kind'),
      h('select', { onchange: (e) => { view.kind = e.target.value; drawTable(); } },
        h('option', { value: '' }, t('inv.all')),
        KIND_ORDER.map((k) => h('option', { value: k }, t('kind.' + k))))),
    h('input', { type: 'text', placeholder: t('inv.search'), oninput: (e) => { view.query = e.target.value; drawTable(); } }),
  );

  function drawTable() {
    const q = view.query.trim().toLowerCase();
    const rows = view.files.filter((f) => (!view.status || f.status === view.status) && (!view.kind || f.kind === view.kind)
      && (!q || (f.name + ' ' + (f.title || '') + ' ' + f.folder).toLowerCase().includes(q)));
    fill(table,
      h('p', { class: 'dim' }, t('inv.shown', { n: rows.length, total: view.files.length })),
      h('table', null,
        h('thead', null, h('tr', null, h('th', null, t('inv.col_name')), h('th', null, t('inv.col_kind')),
          h('th', null, t('inv.col_family')), h('th', { class: 'num' }, t('inv.col_size')), h('th', null, t('inv.col_status')),
          h('th', null, t('inv.col_model')), h('th', null, t('inv.col_folder')))),
        h('tbody', null, rows.map((f) => h('tr', null,
          h('td', { class: 'path' }, f.name, f.shadowed ? h('span', { class: 'warn' }, ' ' + t('inv.shadowed')) : null),
          h('td', null, t('kind.' + f.kind), h('div', { class: 'dim' }, f.comfy_type)),
          h('td', null, f.family === 'unknown' ? '' : f.family, f.base_model ? h('div', { class: 'dim' }, f.base_model) : null),
          h('td', { class: 'num' }, formatBytes(f.bytes)),
          h('td', { class: STATUS_CLASS[f.status] }, t(STATUS_KEYS[f.status]), f.error ? h('div', null, f.error) : null),
          h('td', null, f.status === 'civitai' || f.status === 'local'
            ? [f.civitai_url ? h('a', { href: f.civitai_url, target: '_blank', rel: 'noreferrer' }, f.title) : f.title,
              f.version_name ? h('div', { class: 'dim' }, f.version_name) : null]
            : f.bundled ? h('span', { class: 'dim' }, t('inv.bundled', { n: f.bundled })) : ''),
          h('td', { class: 'path dim' }, f.folder))))));
  }

  async function tick(force) {
    if (mine !== current) return;
    if (timer) clearTimeout(timer);
    timer = null;
    try {
      const data = await api('GET', '/api/ui/progress');
      if (mine !== current) return;
      view.summary = shownSummary(data.summary);
      view.progress = data.progress;
      drawNumbers();
      drawWork();
      drawKinds();
      // The list is re-read when the library changed, but not more than every few seconds while work is going on.
      const due = force === true || (data.revision !== view.revision && Date.now() - view.fetchedAt > 5000);
      if (due && !view.loading) {
        view.loading = true;
        try {
          const inv = await api('GET', '/api/ui/inventory');
          if (mine !== current) return;
          view.files = inv.files.filter((f) => f.kind !== HIDDEN_KIND);
          view.revision = inv.revision;
          view.fetchedAt = Date.now();
          drawTable();
        } finally {
          view.loading = false;
        }
      }
    } catch (e) {
      view.message = { text: e.message, cls: 'bad' };
      drawMessage();
    }
    if (mine === current) timer = setTimeout(tick, view.progress && view.progress.busy ? 1000 : 4000);
  }

  tick(true);
  loadNeeds();
}
