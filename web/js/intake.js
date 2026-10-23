// The download drawer (INTAKE): paste civitai links, read each one into a preview, choose the version, the files and
// the folder, and follow the queue. It slides in over the library from the right; pasting a civitai link anywhere in
// the program opens it and starts reading.
import { api } from './api.js';
import { h, fill, formatBytes, formatDuration } from './dom.js';
import { t, currentLang } from './i18n.js';
import { familyCode, familyName } from './families.js';
import { mosaicOf, loadImage } from './mosaic.js';
import { MASK_FROM, levelName, icon } from './ui.js';
import { scramble, pace } from './motion.js';

const RUNNING = ['running', 'verifying'];
const WAITING = ['queued', 'paused', 'retrying'];
const LINK_HINT = /civitai\.(com|red|green)\b|huggingface\.co\/|hf\.co\//i;
const COVER_ASPECT = 5 / 8;
const THUMB_ASPECT = 70 / 97;
const FILE_PLATES = { 'model': 'MODEL', 'pruned model': 'PRUNED', 'vae': 'VAE', 'text encoder': 'TEXT ENC',
  'diffusion model': 'DIFFUSION', 'negative': 'NEG', 'training data': 'DATA', 'config': 'CONFIG', 'archive': 'ZIP' };
const LOGIN_ERRORS = ['login_required', 'token_rejected'];
// Why a folder is suggested (see depot/folders.py).
const WHY = { last: 'dl.why_last', library: 'dl.why_library', name: 'dl.why_name', type_root: 'dl.why_type_root',
  first: 'dl.why_first', chosen: 'dl.why_chosen' };
const CJK = /[㐀-鿿豈-﫿]/;

function masking() {
  try { return localStorage.getItem('md.mask') !== '0'; } catch (e) { return true; }
}

function cjk(text) {
  return CJK.test(text) ? h('span', { class: 'cjk' }, text) : text;
}

// "a（b）、c（d）" in Chinese, "a (b), c (d)" in English. A pair with no second part is just its first.
function listOf(pairs) {
  const zh = currentLang() === 'zh';
  return pairs.map(([a, b]) => (!b ? a : zh ? a + '（' + b + '）' : a + ' (' + b + ')')).join(zh ? '、' : ', ');
}

function nVersions(n) {
  return n === 1 ? t('dl.n_versions_one') : t('dl.n_versions', { n });
}

// The entry number a model is filed under: civitai's short fingerprint (AutoV2), as the library's cards show it.
function entryCode(sha256) {
  return sha256.slice(0, 10).toUpperCase();
}

function fileMeta(f) {
  return [f.format, f.fp, f.size, formatBytes(f.bytes)].filter(Boolean).join(' · ');
}

// Is this text just the family's code or name again ("Pony" beside the PONY plate)?
function sameAsFamily(text, family) {
  const squash = (s) => String(s || '').toLowerCase().replace(/[\s._-]+/g, '');
  return squash(text) === squash(familyCode(family)) || squash(text) === squash(familyName(family));
}

function folderLabel(folder) {
  return folder.rel ? [h('span', { class: 'root' }, folder.root_label + ' \\ '), h('b', null, folder.rel)] : [h('b', null, folder.root_label)];
}

let nextKey = 0;

export class IntakeDrawer {
  constructor(ctx) {
    this.ctx = ctx;                 // { enabled: () => bool, onDone: () => void, onShow: (sha) => void }
    this.records = [];              // the pasted links, newest last
    this.current = null;            // the record that is open
    this.items = [];                // the queue as the server lists it
    this.rows = new Map();          // queue rows by download id
    this.folders = {};              // folder choices by ComfyUI type
    this.revealed = new Set();      // example images shown unmasked in this visit
    this.isOpen = false;
    this.timer = null;
    this.button = h('button', { class: 'nav-dl', type: 'button', 'aria-pressed': 'false', 'aria-controls': 'intake',
      onclick: () => this.toggle() });
    this.root = h('aside', { class: 'intake', id: 'intake', inert: true });
    document.body.append(this.root);
    document.addEventListener('paste', (e) => this.onPaste(e));
    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && this.isOpen && !document.querySelector('dialog[open]')) {
        e.preventDefault();
        this.close();
      }
    });
    this.build();
    this.refresh();
  }

  // ---- the frame -------------------------------------------------------------------------------------------------
  build() {
    this.rows.clear();
    this.field = h('textarea', { rows: 1, placeholder: t('dl.paste'), 'aria-label': t('dl.paste_label'), spellcheck: false,
      onkeydown: (e) => { if (e.key === 'Enter' && !e.isComposing && !e.shiftKey) { e.preventDefault(); this.read(); } } });
    this.note = h('p', { class: 'paste-note', role: 'status', hidden: true });
    this.headName = h('h2', null, 'INTAKE');
    this.headCount = h('span', { class: 'count' }, '0');
    this.headMeta = h('p', { class: 'intake-meta' });
    this.recordsEl = h('div', { class: 'records' });
    this.pendingLabel = h('div', { class: 'sec-label' });
    this.queueLabel = h('div', { class: 'sec-label' });
    this.queueList = h('div', { class: 'queue' });
    fill(this.root,
      h('header', { class: 'intake-head' }, this.headName, this.headCount, h('span'),
        h('button', { class: 'intake-close', type: 'button', title: t('dl.close'), 'aria-label': t('dl.close'), onclick: () => this.close() }, icon('i-x')),
        this.headMeta,
        h('div', { class: 'paste' }, this.field, h('button', { class: 'btn', type: 'button', onclick: () => this.read() }, t('dl.read'))),
        h('p', { class: 'paste-hint' }, t('dl.paste_hint')),
        this.note),
      h('div', { class: 'intake-body' },
        h('section', { class: 'pending', 'aria-label': t('dl.pending') }, this.pendingLabel, this.recordsEl),
        h('section', { 'aria-label': t('dl.queue') }, this.queueLabel, this.queueList)));
    this.root.setAttribute('aria-label', t('dl.open'));
    this.renderAll();
  }

  relabel() {
    this.build();
  }

  renderAll() {
    this.renderRecords();
    this.renderQueue();
    this.renderHead();
  }

  enabled() {
    return this.ctx.enabled();
  }

  open() {
    if (!this.enabled()) return;
    this.isOpen = true;
    this.root.inert = false;
    this.root.classList.add('open');
    this.button.setAttribute('aria-pressed', 'true');
    document.body.classList.add('intake-on');
    this.refresh();
    if (!this.records.length) setTimeout(() => this.field.focus({ preventScroll: true }), 60);
  }

  close() {
    this.isOpen = false;
    this.root.classList.remove('open');
    this.root.inert = true;
    this.button.setAttribute('aria-pressed', 'false');
    document.body.classList.remove('intake-on');
  }

  toggle() {
    if (this.isOpen) this.close();
    else this.open();
  }

  setNote(text) {
    this.note.hidden = !text;
    this.note.textContent = text || '';
  }

  // ---- counts: the block, and the button in the top bar ----------------------------------------------------------
  counts() {
    const c = { confirm: this.records.length, running: 0, queued: 0, attention: 0, done: 0 };
    for (const item of this.items) {
      if (RUNNING.includes(item.state)) c.running += 1;
      else if (WAITING.includes(item.state)) c.queued += 1;
      else if (item.state === 'failed') c.attention += 1;
      else if (item.state === 'done') c.done += 1;
    }
    return c;
  }

  renderHead() {
    const c = this.counts();
    // The parts before the rule add up to the big number: what is still to be done. What is already in the library
    // stands apart after it.
    this.headCount.textContent = String(c.confirm + c.running + c.queued + c.attention);
    const parts = [];
    if (c.confirm) parts.push(t('dl.title_meta_confirm', { n: c.confirm }));
    if (c.running) parts.push(t('dl.title_meta_running', { n: c.running }));
    if (c.queued) parts.push(t('dl.title_meta_queued', { n: c.queued }));
    if (c.attention) parts.push(t('dl.title_meta_attention', { n: c.attention }));
    if (!parts.length) parts.push(t('dl.title_meta_empty'));
    fill(this.headMeta, parts.flatMap((p, i) => (i ? ['  ·  ', cjk(p)] : [cjk(p)])),
      c.done ? h('span', { class: 'done' }, cjk(t('dl.title_meta_done', { n: c.done }))) : null);
    this.renderButton(c);
  }

  renderButton(c) {
    const n = c.running + c.queued + c.attention;
    const running = this.items.find((i) => i.state === 'running');
    const pct = running && running.bytes_total ? Math.min(100, (running.bytes_done / running.bytes_total) * 100) : 0;
    fill(this.button, icon('i-download'), h('span', null, t('dl.open')),
      n ? h('span', { class: 'n' }, String(n)) : null,
      h('span', { class: 'gauge', style: '--p:' + (running ? (pct / 100).toFixed(4) : 0) }));
    this.button.title = t('dl.open_label');
  }

  // ---- reading pasted links --------------------------------------------------------------------------------------
  onPaste(e) {
    if (!this.enabled()) return;
    const target = e.target instanceof Element ? e.target : null;
    if (target === this.field) {
      setTimeout(() => this.read(), 0);         // pasted into the field: read it straight away
      return;
    }
    if (target && target.closest('input, textarea, select, [contenteditable=""], [contenteditable="true"]')) return;
    const text = (e.clipboardData && e.clipboardData.getData('text')) || '';
    if (!LINK_HINT.test(text)) return;
    e.preventDefault();
    this.open();
    this.field.value = text;
    this.read();
  }

  // A civitai link given from elsewhere (the dossier's "download new version"): read as if pasted.
  readLink(url) {
    if (!this.enabled()) return;
    this.open();
    this.field.value = url;
    this.read();
  }

  async read() {
    const text = this.field.value.trim();
    if (!text) return;
    let parsed;
    try {
      parsed = await api('POST', '/api/ui/intake/parse', { text });
    } catch (e) {
      this.setNote(e.message);
      return;
    }
    this.field.value = '';
    // Words around a link are not worth a remark; links that could not be used are. With no link at all, say so.
    const found = parsed.links.filter((l) => !l.error);
    const skipped = parsed.links.filter((l) => l.error && (l.error !== 'unrecognized' || !found.length));
    const notes = [];
    if (!found.length) notes.push(t('dl.nothing_found'));
    if (skipped.length) notes.push(t('dl.skipped', { list: listOf(skipped.map((l) => [l.input, l.message])) }));
    this.setNote(notes.join(' '));
    const fresh = [];
    for (const link of parsed.links.filter((l) => !l.error)) {
      const same = this.records.find((r) => (link.hf ? r.link.hf === link.hf : r.link.model_id === link.model_id
        && r.link.version_id === link.version_id && r.link.file_id === link.file_id));
      if (same) { this.current = same.key; continue; }
      const rec = { key: ++nextKey, link, status: 'loading', preview: null, error: null, files: new Set(), folder: null,
        folderChosen: false, othersOpen: false, pickOpen: false, image: null, note: null, retire: false };
      this.records.push(rec);
      fresh.push(rec);
    }
    if (fresh.length) this.current = fresh[0].key;
    this.renderRecords();
    this.renderHead();
    for (const rec of fresh) await this.load(rec);      // one after another: civitai is asked one request at a time
  }

  async load(rec) {
    rec.status = 'loading';
    rec.error = null;
    this.renderRecords();
    try {
      const preview = await api('POST', '/api/ui/intake/preview', rec.link.hf ? { hf: rec.link.hf }
        : { model_id: rec.link.model_id, version_id: rec.link.version_id, file_id: rec.link.file_id });
      if (!this.records.includes(rec)) return;
      rec.preview = preview;
      rec.status = 'ready';
      this.chooseVersion(rec, preview.selected);
    } catch (e) {
      rec.status = 'error';
      rec.error = e.message;
    }
    this.renderRecords();
    this.renderHead();
  }

  version(rec) {
    const versions = rec.preview ? rec.preview.versions : [];
    return versions.find((v) => v.id === rec.versionId) || versions[0] || null;
  }

  chooseVersion(rec, versionId) {
    rec.versionId = versionId;
    const v = this.version(rec);
    if (!v) return;
    const usable = (f) => f && !f.in_library && !f.queued;
    rec.files = new Set(v.chosen.filter((id) => usable(v.files.find((f) => f.id === id))));
    if (!rec.folderChosen) rec.folder = v.suggestion ? Object.assign({}, v.suggestion) : null;
    rec.image = v.cover;
    rec.othersOpen = false;
    rec.retire = false;           // another version is chosen: the versions in the library are kept until said otherwise
    rec.pickOpen = false;
  }

  remove(rec) {
    const i = this.records.indexOf(rec);
    if (i < 0) return;
    this.records.splice(i, 1);
    if (this.current === rec.key) {
      const next = this.records[i] || this.records[i - 1];
      this.current = next ? next.key : null;
    }
    this.renderRecords();
    this.renderHead();
  }

  // ---- the records waiting for a decision ------------------------------------------------------------------------
  renderRecords() {
    const ready = this.records.filter((r) => r.status === 'ready' && this.canDownload(r));
    fill(this.pendingLabel, t('dl.pending'), h('span', { class: 'n' }, String(this.records.length)),
      ready.length > 1 ? h('span', { class: 'aside' }, h('button', { class: 'btn btn-line btn-small', type: 'button',
        onclick: () => this.downloadAll() }, icon('i-download'), t('dl.download_all', { n: ready.length }))) : null);
    if (!this.records.length) {
      fill(this.recordsEl, h('p', { class: 'intake-empty' }, t('dl.empty')));
      return;
    }
    if (!this.records.some((r) => r.key === this.current)) this.current = this.records[0].key;
    fill(this.recordsEl, this.records.map((rec) => (rec.key === this.current && rec.status === 'ready'
      ? this.drawRecord(rec) : this.drawLine(rec))));
    // A folder list that has just opened shows the chosen folder in its middle.
    const chosen = this.recordsEl.querySelector('.pick-list.fresh [aria-current="true"]');
    if (chosen) chosen.parentElement.scrollTop = Math.max(0, chosen.offsetTop - chosen.parentElement.clientHeight / 2);
  }

  drawLine(rec) {
    const p = rec.preview;
    const v = p ? this.version(rec) : null;
    let thumb;
    if (rec.status === 'ready' && v && v.cover !== null && v.cover !== undefined) thumb = this.picture(v, v.cover, COVER_ASPECT, 'line-thumb', 5);
    else thumb = h('span', { class: 'line-thumb ph' });
    let title;
    let sub;
    let state;
    let actions;
    if (rec.status === 'loading') {
      title = rec.link.input;
      sub = null;
      state = h('span', { class: 'state blink' }, t('dl.reading'));
      actions = [h('button', { class: 'icon-x', type: 'button', title: t('dl.remove'), 'aria-label': t('dl.remove'), onclick: () => this.remove(rec) }, icon('i-x'))];
    } else if (rec.status === 'error') {
      title = rec.link.input;
      sub = null;
      state = h('span', { class: 'state bad' }, t('dl.read_failed', { message: rec.error }));
      actions = [h('button', { class: 'btn btn-line btn-small', type: 'button', onclick: () => this.load(rec) }, t('dl.retry_read')),
        h('button', { class: 'icon-x', type: 'button', title: t('dl.remove'), 'aria-label': t('dl.remove'), onclick: () => this.remove(rec) }, icon('i-x'))];
    } else {
      title = p.model.name;
      sub = [t('kind.' + p.model.kind), familyCode(v.family), formatBytes(this.chosenBytes(rec)),
        p.versions.length > 1 ? nVersions(p.versions.length) : (sameAsFamily(v.name, v.family) ? null : v.name)]
        .filter(Boolean).join(' · ');
      state = rec.folder ? h('span', { class: 'state', title: rec.folder.path }, t('dl.to_folder', { folder: rec.folder.rel ? rec.folder.root_label + ' \\ ' + rec.folder.rel : rec.folder.root_label })) : null;
      actions = [h('button', { class: 'btn btn-line btn-small', type: 'button', disabled: !this.canDownload(rec),
        onclick: (e) => { e.stopPropagation(); this.download(rec); } }, t('dl.download')),
      h('button', { class: 'icon-x', type: 'button', title: t('dl.remove'), 'aria-label': t('dl.remove'),
        onclick: (e) => { e.stopPropagation(); this.remove(rec); } }, icon('i-x'))];
    }
    const line = h('div', { class: 'rec-line' + (rec.status === 'ready' ? ' openable' : ''), 'data-key': rec.key },
      thumb, h('div', { class: 't' }, h('b', null, title), sub ? h('small', null, sub) : null), state, h('span', { class: 'acts' }, actions));
    if (rec.status === 'ready') {
      line.tabIndex = 0;
      line.setAttribute('role', 'button');
      line.addEventListener('click', () => { this.current = rec.key; this.renderRecords(); });
      line.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); this.current = rec.key; this.renderRecords(); } });
    }
    return line;
  }

  drawRecord(rec) {
    const p = rec.preview;
    const v = this.version(rec);
    const media = h('div', { class: 'rec-media' }, this.drawHero(rec, v), this.drawThumbs(rec, v));
    // What is known now, said both ways: whether the library has this version. Whether its creator requires a login
    // civitai only says when the download starts; until then the line tells what the settings hold.
    const main = v.files.find((f) => v.chosen.includes(f.id)) || v.files[0];
    const facts = [];
    const present = p.source === 'hf' ? ['dl.file_present', 'dl.file_absent'] : ['dl.version_present', 'dl.version_absent'];
    facts.push(main && main.in_library ? h('li', { class: 'good' }, t(present[0])) : h('li', null, t(present[1])));
    if (v.early_access) facts.push(h('li', { class: 'warn' }, t('dl.ea_note')));
    if (p.model.mode) facts.push(h('li', { class: 'warn' }, t('dl.archived')));
    if (v.downloads !== null && v.downloads !== undefined) facts.push(h('li', null, t('dl.n_downloads', { n: v.downloads.toLocaleString() })));
    if (p.source === 'hf') {
      facts.push(main && main.sha256 ? h('li', null, t('dl.hf_check')) : h('li', { class: 'warn' }, t('dl.hf_unchecked')));
      if (!v.comfy_type) facts.push(h('li', { class: 'warn' }, t('dl.type_unknown')));
    } else {
      facts.push(h('li', { class: p.has_token ? null : 'warn' },
        t('dl.login_check', { key: t(p.has_token ? 'dl.key_set' : 'dl.key_missing') })));
    }
    const chosen = this.chosenBytes(rec);
    const blocked = this.blockReason(rec);
    const body = h('div', { class: 'rec-main' },
      h('div', { class: 'rec-link' }, icon('i-link'), h('span', null, rec.link.input)),
      h('h3', { class: 'rec-name' }, p.model.name),
      h('div', { class: 'rec-sub' },
        p.model.creator ? h('span', null, t('dl.by', { name: p.model.creator })) : null,
        h('span', null, t('kind.' + p.model.kind)),
        h('span', null, p.source === 'hf' ? v.name : nVersions(p.versions.length)),
        p.source === 'hf'
          ? h('a', { href: p.model.url, target: '_blank', rel: 'noopener noreferrer' }, t('dl.open_hf'), icon('i-external'))
          : h('a', { href: p.model.url + '?modelVersionId=' + v.id, target: '_blank', rel: 'noopener noreferrer' }, t('dl.open_civitai'), icon('i-external'))),
      // a Hugging Face file has no versions to choose from: its repository is named in the line above
      p.source === 'hf' ? null : [h('p', { class: 'field-label' }, t('dl.version')), this.drawVersions(rec, v)],
      h('p', { class: 'field-label' }, t('dl.files')), h('div', { class: 'choices' }, v.files.map((f) => this.drawFile(rec, f))),
      h('p', { class: 'field-label' }, t('dl.save_to')), this.drawFolder(rec, v),
      this.drawKept(rec, v),
      h('ul', { class: 'facts' }, facts),
      h('div', { class: 'rec-actions' },
        h('button', { class: 'btn btn-solid', type: 'button', disabled: Boolean(blocked) || rec.busy, onclick: () => this.download(rec) },
          icon('i-download'), t('dl.download'), chosen ? h('span', { class: 'size' }, formatBytes(chosen)) : null),
        h('button', { class: 'btn btn-line', type: 'button', onclick: () => this.remove(rec) }, t('dl.remove'))),
      blocked || rec.note ? h('p', { class: 'rec-note' + (rec.noteBad ? ' bad' : '') }, rec.note || blocked) : null);
    return h('div', { class: 'rec-wrap', 'data-key': rec.key }, h('article', { class: 'record', 'aria-current': 'true' }, media, body));
  }

  // The version in use, and the others folded away under it.
  drawVersions(rec, v) {
    const p = rec.preview;
    const label = (x) => {
      const bits = [];
      if (x.id === p.linked_version) bits.push(t('dl.version_linked'));
      else if (x.id === p.versions[0].id) bits.push(t('dl.version_newest'));
      if (x.base_model && !sameAsFamily(x.base_model, x.family)) bits.push(x.base_model);
      if (x.published_at) bits.push(x.published_at.slice(0, 10));
      return bits.join(' · ');
    };
    const row = (x, on) => {
      const main = x.files.find((f) => x.chosen.includes(f.id)) || x.files[0];
      return h('button', { class: 'choice' + (on ? ' on' : ''), type: 'button', role: 'radio', 'aria-checked': String(on),
        onclick: () => { if (!on) { this.chooseVersion(rec, x.id); this.renderRecords(); } } },
        h('span', { class: 'box' }), h('span', { class: 'plate' }, familyCode(x.family)),
        h('span', { class: 'main' }, x.name, h('small', null, label(x))),
        h('span', { class: 'end' }, x.early_access ? h('span', { class: 'ea' }, t('dl.early_access')) : null,
          main && main.in_library ? h('span', { class: 'good' }, t('dl.file_in_library')) : null,
          main ? formatBytes(main.bytes) : ''));
    };
    const others = p.versions.filter((x) => x.id !== v.id);
    const parts = [row(v, true)];
    if (others.length) {
      // Folded, the line still names the other versions and their base models.
      const names = listOf(others.map((x) => [x.name, x.base_model]));
      const more = t(others.length === 1 ? 'dl.versions_more_one' : 'dl.versions_more', { n: others.length, list: names });
      parts.push(h('button', { class: 'more-versions', type: 'button', 'aria-expanded': String(rec.othersOpen),
        title: rec.othersOpen ? null : more, onclick: () => { rec.othersOpen = !rec.othersOpen; this.renderRecords(); } },
      h('span', null, rec.othersOpen ? t('dl.versions_less') : more)));
      if (rec.othersOpen) parts.push(h('div', { class: 'other-versions', role: 'radiogroup' }, others.map((x) => row(x, false))));
    }
    return h('div', { class: 'choices', role: 'radiogroup', 'aria-label': t('dl.version') }, parts);
  }

  drawFile(rec, f) {
    const blocked = f.in_library || f.queued;
    const on = rec.files.has(f.id);
    let end = null;
    if (f.in_library) end = h('a', { class: 'good', href: f.sha256 ? '#/m/' + f.sha256 : '#/', onclick: () => this.close() }, t('dl.file_in_library'));
    else if (f.queued) end = h('span', null, t('dl.file_queued'));
    else if (!f.model_file) end = h('span', null, t('dl.file_not_model'));
    const plate = FILE_PLATES[String(f.type || '').toLowerCase()] || String(f.type || 'FILE').toUpperCase();
    return h('label', { class: 'choice' + (blocked ? ' off' : '') },
      h('input', { type: 'checkbox', checked: on, disabled: blocked,
        onchange: (e) => { if (e.target.checked) rec.files.add(f.id); else rec.files.delete(f.id); this.renderRecords(); } }),
      h('span', { class: 'plate line' }, plate),
      h('span', { class: 'main' }, f.name, h('small', null, fileMeta(f))),
      h('span', { class: 'end' }, end));
  }

  drawFolder(rec, v) {
    const why = rec.folder ? t(WHY[rec.folder.reason] || 'dl.why_chosen', { n: rec.folder.n, total: rec.folder.total }) : t('dl.no_folder');
    const row = h('div', { class: 'folder' + (rec.folder ? '' : ' none') }, icon('i-folder'),
      h('span', { class: 'path', title: rec.folder ? rec.folder.path : '' }, rec.folder ? folderLabel(rec.folder) : t('dl.pick_title')),
      h('button', { type: 'button', 'aria-expanded': String(rec.pickOpen), onclick: () => this.togglePicker(rec, v) }, t('dl.change')));
    return [row, h('p', { class: 'why' }, why), rec.pickOpen ? this.drawPicker(rec, v) : null];
  }

  // Other versions of this model that the library has: kept by default (the user chose this, 2026-10-02), or sent to
  // the recycle bin once this download is done and its fingerprint checks out.
  keptVersions(rec, v) {
    return rec.preview.versions.filter((x) => x.id !== v.id && x.files.some((f) => f.in_library));
  }

  drawKept(rec, v) {
    const kept = this.keptVersions(rec, v);
    if (!kept.length) return null;
    const list = listOf(kept.map((x) => [x.name, null]));
    const row = (value, label) => h('button', { class: 'choice plain' + (rec.retire === value ? ' on' : ''), type: 'button', role: 'radio',
      'aria-checked': String(rec.retire === value), onclick: () => { rec.retire = value; this.renderRecords(); } },
    h('span', { class: 'box' }), h('span', { class: 'main' }, label));
    return [h('p', { class: 'field-label' }, t('dl.retire_label')),
      h('div', { class: 'choices', role: 'radiogroup', 'aria-label': t('dl.retire_label') },
        row(false, t('dl.retire_keep', { list })), row(true, t('dl.retire_replace', { list })))];
  }

  async togglePicker(rec, v) {
    rec.pickOpen = !rec.pickOpen;
    rec.pickFresh = rec.pickOpen;
    const type = v.comfy_type || '';
    if (rec.pickOpen && !this.folders[type]) {
      try {
        this.folders[type] = (await api('GET', '/api/ui/intake/folders?comfy_type=' + encodeURIComponent(type))).folders;
      } catch (e) {
        rec.note = e.message;
        rec.noteBad = true;
        rec.pickOpen = false;
      }
    }
    this.renderRecords();
  }

  drawPicker(rec, v) {
    const all = this.folders[v.comfy_type || ''] || [];
    const pick = (f) => {
      rec.folder = { path: f.path, root_label: f.root_label, rel: f.rel, reason: 'chosen', n: 0, total: 0 };
      rec.folderChosen = true;
      rec.pickOpen = false;
      this.renderRecords();
    };
    const row = (f) => {
      const depth = f.rel ? f.rel.split(/[\\/]/).length : 0;
      const name = f.rel ? f.rel.split(/[\\/]/).pop() : f.root_label;
      const on = rec.folder && rec.folder.path === f.path;
      return h('button', { class: 'pick-row' + (depth ? '' : ' root'), type: 'button', 'aria-current': on ? 'true' : null,
        title: t('dl.pick_choose') + ' · ' + f.path, style: '--depth:' + depth, onclick: () => pick(f) },
      h('span', { class: 'name' }, name), f.files ? h('span', { class: 'n' }, t('dl.pick_n', { n: f.files })) : null,
      on ? icon('i-check') : null);
    };
    const own = all.filter((f) => f.group === 'type');
    const other = all.filter((f) => f.group !== 'type');
    const parent = rec.folder || (own[0] ? { path: own[0].path, root_label: own[0].root_label, rel: own[0].rel } : null);
    const input = h('input', { type: 'text', placeholder: t('dl.pick_new_name'), 'aria-label': t('dl.pick_new_name'),
      onkeydown: (e) => { if (e.key === 'Enter') { e.preventDefault(); create(); } } });
    const create = async () => {
      const name = input.value.trim();
      if (!name || !parent) return;
      try {
        const made = await api('POST', '/api/ui/intake/folders', { parent: parent.path, name });
        delete this.folders[v.comfy_type || ''];
        const sep = made.path.includes('\\') ? '\\' : '/';
        const rel = made.path.split(sep).slice(-1)[0];
        pick({ path: made.path, root_label: parent.root_label, rel: parent.rel ? parent.rel + sep + rel : rel });
      } catch (e) {
        rec.note = e.message;
        rec.noteBad = true;
        this.renderRecords();
      }
    };
    const fresh = rec.pickFresh;
    rec.pickFresh = false;
    return h('div', { class: 'picker' },
      h('p', { class: 'pick-title' }, t('dl.pick_title')),
      h('div', { class: 'pick-list' + (fresh ? ' fresh' : ''), role: 'group', 'aria-label': t('dl.pick_title') },
        own.length ? h('p', { class: 'pick-group' }, t('dl.pick_type', { type: t('kind.' + this.ctx.kindOf(v.comfy_type)) })) : null,
        own.map(row),
        other.length ? h('p', { class: 'pick-group' }, t('dl.pick_other')) : null,
        other.map(row)),
      parent ? h('div', { class: 'pick-new' },
        h('span', { class: 'label' }, t('dl.pick_new'), h('small', null, t('dl.pick_new_in', { folder: parent.rel ? parent.root_label + ' \\ ' + parent.rel : parent.root_label }))),
        input, h('button', { class: 'btn btn-line btn-small', type: 'button', onclick: create }, t('dl.pick_create'))) : null);
  }

  // ---- example images --------------------------------------------------------------------------------------------
  // One example image: a picture, a mosaic when it is rated R or above and masking is on, or a dotted placeholder.
  picture(v, index, aspect, cls, cols) {
    const image = (v.images || []).find((x) => x.index === index);
    if (!image || image.type === 'video') return h('span', { class: cls + ' ph' }, image ? icon('i-play') : null);
    const key = v.id + '/' + index;
    const masked = masking() && image.level >= MASK_FROM && !this.revealed.has(key);
    if (!masked) {
      const img = h('img', { class: cls, src: image.url, alt: '', loading: 'lazy', decoding: 'async' });
      img.addEventListener('error', () => img.replaceWith(h('span', { class: cls + ' ph' }, h('small', null, t('dos.sample_failed_short')))));
      return img;
    }
    const slot = h('span', { class: cls + ' masked' });
    loadImage(image.url).then((img) => {
      const canvas = mosaicOf(img, aspect, cols, 0.24);
      canvas.className = 'mosaic';
      slot.prepend(canvas);
    }).catch(() => { slot.classList.add('ph'); });
    return slot;
  }

  drawHero(rec, v) {
    const index = rec.image === null || rec.image === undefined ? v.cover : rec.image;
    if (index === null || index === undefined) {
      return h('div', { class: 'rec-hero ph' }, h('b', null, t('kind.' + rec.preview.model.kind)), h('small', null, t('dos.samples_none')));
    }
    const image = v.images.find((x) => x.index === index);
    const hero = h('div', { class: 'rec-hero' }, this.picture(v, index, COVER_ASPECT, 'hero-img', 9));
    const key = v.id + '/' + index;
    if (image && image.type !== 'video' && masking() && image.level >= MASK_FROM && !this.revealed.has(key)) {
      hero.append(h('button', { class: 'reveal', type: 'button', onclick: () => { this.revealed.add(key); this.renderRecords(); } },
        t('dos.sample_reveal') + ' · ' + levelName(image.level)));
    }
    if (image && image.level >= MASK_FROM) hero.append(h('span', { class: 'level' }, levelName(image.level)));
    return hero;
  }

  drawThumbs(rec, v) {
    const stills = (v.images || []).slice(0, 4);
    if (stills.length < 2) return null;
    const current = rec.image === null || rec.image === undefined ? v.cover : rec.image;
    return h('div', { class: 'rec-thumbs', role: 'group', 'aria-label': t('dos.samples') }, stills.map((image) => h('button', {
      class: 'rec-thumb', type: 'button', 'aria-current': String(image.index === current),
      'aria-label': t('dos.sample_n', { n: image.index + 1, total: v.images.length }),
      onclick: () => { rec.image = image.index; this.renderRecords(); } }, this.picture(v, image.index, THUMB_ASPECT, 'thumb-img', 5))));
  }

  // ---- adding to the queue ---------------------------------------------------------------------------------------
  chosenBytes(rec) {
    const v = this.version(rec);
    if (!v) return 0;
    return v.files.filter((f) => rec.files.has(f.id)).reduce((sum, f) => sum + (f.bytes || 0), 0);
  }

  blockReason(rec) {
    const v = this.version(rec);
    if (!v) return t('dl.nothing_chosen');
    if (!rec.files.size) {
      const usable = v.files.filter((f) => !f.in_library && !f.queued);
      return usable.length ? t('dl.nothing_chosen') : t('dl.all_in_library');
    }
    if (!rec.folder) return t('dl.no_folder');
    return null;
  }

  canDownload(rec) {
    return rec.status === 'ready' && !this.blockReason(rec) && !rec.busy;
  }

  async download(rec) {
    if (!this.canDownload(rec)) return false;
    const v = this.version(rec);
    rec.busy = true;
    rec.note = null;
    this.renderRecords();
    try {
      const result = await api('POST', '/api/ui/downloads', rec.preview.source === 'hf'
        ? { hf: rec.preview.hf, folder: rec.folder.path, comfy_type: v.comfy_type }
        : { model_id: rec.preview.model.id, version_id: v.id, file_ids: [...rec.files], folder: rec.folder.path,
          retire: rec.retire ? this.keptVersions(rec, v).flatMap((x) => x.files.filter((f) => f.in_library && f.sha256).map((f) => f.sha256)) : [] });
      this.items = result.items;
      const notes = result.skipped.map((s) => s.message).join(' ');
      if (result.added.length) {
        this.setNote([t('dl.added'), notes].filter(Boolean).join(' '));
        this.fold(rec);
      } else {
        rec.busy = false;
        rec.note = notes;
        rec.noteBad = false;
        this.renderRecords();
      }
      this.renderQueue();
      this.renderHead();
      this.schedule(400);
      return true;
    } catch (e) {
      rec.busy = false;
      rec.note = e.message;
      rec.noteBad = true;
      this.renderRecords();
      return false;
    }
  }

  async downloadAll() {
    for (const rec of this.records.slice()) {
      if (this.canDownload(rec)) await this.download(rec);
    }
  }

  // The record folds away into the queue.
  fold(rec) {
    const el = this.recordsEl.querySelector('[data-key="' + rec.key + '"]');
    if (!el || matchMedia('(prefers-reduced-motion: reduce)').matches) { this.remove(rec); return; }
    el.classList.add('folding');
    setTimeout(() => this.remove(rec), pace(300));    // a timer, not an animation frame: it also runs in a hidden tab
  }

  // ---- the queue -------------------------------------------------------------------------------------------------
  schedule(delay) {
    clearTimeout(this.timer);
    const active = this.items.some((i) => RUNNING.includes(i.state) || i.state === 'queued' || i.state === 'retrying');
    const wait = delay !== undefined ? delay : active ? 1000 : this.isOpen ? 5000 : 15000;
    this.timer = setTimeout(() => this.refresh(), wait);
  }

  async refresh() {
    if (this.enabled()) {
      try {
        const data = await api('GET', '/api/ui/downloads');
        const before = new Map(this.items.map((i) => [i.id, i.state]));
        this.items = data.items;
        this.renderQueue();
        this.renderHead();
        if (this.items.some((i) => i.state === 'done' && before.has(i.id) && before.get(i.id) !== 'done') && this.ctx.onDone) this.ctx.onDone();
      } catch (e) { /* the next round tries again */ }
    }
    this.schedule();
  }

  async act(item, action) {
    try {
      const data = await api('POST', '/api/ui/downloads/' + item.id + '/' + action, {});
      this.items = data.items;
      this.renderQueue();
      this.renderHead();
    } catch (e) {
      this.setNote(e.message);
    }
    this.schedule(300);
  }

  async clearDone() {
    try {
      this.items = (await api('POST', '/api/ui/downloads/clear', {})).items;
    } catch (e) { /* shown at the next refresh */ }
    this.renderQueue();
    this.renderHead();
  }

  renderQueue() {
    const done = this.items.filter((i) => i.state === 'done').length;
    fill(this.queueLabel, t('dl.queue'), h('span', { class: 'n' }, String(this.items.length)),
      h('span', { class: 'aside' }, t('dl.queue_note'),
        done ? h('button', { class: 'btn btn-line btn-small', type: 'button', onclick: () => this.clearDone() }, t('dl.clear_done')) : null));
    const seen = new Set();
    let previous = null;
    for (const item of this.items) {
      seen.add(item.id);
      const sig = [item.state, item.error, item.message && item.state !== 'retrying' ? item.message : '', item.dest_name, item.sha256,
        JSON.stringify(item.retired), masking()].join('|');
      let row = this.rows.get(item.id);
      if (!row || row.sig !== sig) {
        const el = this.drawRow(item);
        // a download filed while the queue was on screen: its entry number decodes where the percentage stood
        const filed = row && item.state === 'done' && !row.sig.startsWith('done|');
        if (row) row.el.replaceWith(el);
        row = { el, sig };
        this.rows.set(item.id, row);
        if (filed) scramble(el.querySelector('.entry-code'));
      }
      this.updateRow(row.el, item);
      const want = previous ? previous.nextSibling : this.queueList.firstChild;
      if (want !== row.el) this.queueList.insertBefore(row.el, want);
      previous = row.el;
    }
    for (const [id, row] of this.rows) {
      if (!seen.has(id)) { row.el.remove(); this.rows.delete(id); }
    }
    const empty = this.queueList.querySelector('.intake-empty');
    if (!this.items.length && !empty) this.queueList.append(h('p', { class: 'intake-empty' }, t('dl.queue_empty')));
    if (this.items.length && empty) empty.remove();
  }

  drawRow(item) {
    let thumb;
    if (item.cover) {
      const masked = masking() && item.cover_level >= MASK_FROM;
      if (masked) {
        thumb = h('span', { class: 'thumb masked' });
        loadImage(item.cover).then((img) => {
          const canvas = mosaicOf(img, COVER_ASPECT, 5, 0.24);
          canvas.className = 'mosaic';
          thumb.prepend(canvas);
        }).catch(() => thumb.classList.add('ph'));
      } else {
        thumb = h('img', { class: 'thumb', src: item.cover, alt: '', loading: 'lazy' });
        thumb.addEventListener('error', () => thumb.replaceWith(h('span', { class: 'thumb ph' })));
      }
    } else {
      thumb = h('span', { class: 'thumb ph' });
    }
    const meta = [item.version_name, t('kind.' + item.kind), sameAsFamily(item.version_name, item.family) ? null : familyCode(item.family),
      item.bytes_total ? formatBytes(item.bytes_total) : null].filter(Boolean).join(' · ');
    // A folder that is no longer among the model folders in Settings has no root to name: its whole path is shown.
    const dest = !item.dest_root_label ? item.dest_rel || item.dest_dir
      : item.dest_rel ? item.dest_root_label + ' \\ ' + item.dest_rel : item.dest_root_label;
    const button = (label, iconId, action, wide) => h('button', { class: wide ? 'wide' : null, type: 'button', title: label,
      'aria-label': label, onclick: () => this.act(item, action) }, icon(iconId), wide ? label : null);
    let acts = [];
    if (item.state === 'running') acts = [button(t('dl.pause'), 'i-pause', 'pause'), button(t('dl.cancel'), 'i-x', 'remove')];
    else if (item.state === 'queued') acts = [button(t('dl.pause'), 'i-pause', 'pause'), button(t('dl.cancel'), 'i-x', 'remove')];
    else if (item.state === 'paused') acts = [button(t('dl.resume'), 'i-play', 'resume'), button(t('dl.cancel'), 'i-x', 'remove')];
    else if (item.state === 'retrying') acts = [button(t('dl.retry'), 'i-retry', 'retry'), button(t('dl.cancel'), 'i-x', 'remove')];
    else if (item.state === 'failed') {
      // A key that may not go to this download address will not go on a retry either.
      acts = item.error === 'key_not_sent' ? [] : [button(t('dl.retry'), 'i-retry', 'retry', true)];
      if (LOGIN_ERRORS.includes(item.error)) acts.push(h('a', { class: 'wide', href: '#/settings', onclick: () => this.close() }, icon('i-gear'), t('dl.settings')));
      acts.push(button(t('dl.remove_row'), 'i-x', 'remove'));
    } else if (item.state === 'done') {
      acts = [h('a', { class: 'wide', href: item.sha256 ? '#/m/' + item.sha256 : '#/', onclick: () => { this.close(); if (this.ctx.onShow) this.ctx.onShow(item.sha256); } },
        t('dl.show')), button(t('dl.remove_row'), 'i-x', 'remove')];
    }
    // Once filed, the row carries the entry number the new card in the library carries, where the percentage stood.
    const entry = item.state === 'done' && item.sha256
      ? h('p', { class: 'entry', title: t('dl.entry_code') }, h('span', { class: 'sr-only' }, t('dl.entry_code') + ' '),
        h('span', { class: 'entry-code' }, entryCode(item.sha256)))
      : null;
    return h('div', { class: 'q-row st-' + item.state, 'data-id': item.id },
      thumb,
      h('div', { class: 't' }, h('b', { title: item.title || item.file_name }, item.title || item.file_name),
        h('small', null, meta), h('small', { class: 'dest', title: item.dest_dir }, t('dl.to_folder', { folder: dest })),
        item.replaces && item.state !== 'done' ? h('small', { class: 'dest' }, t('dl.retire_pending')) : null),
      h('div', { class: 'q-prog', 'aria-label': t('dl.st_running') },
        h('div', { class: 'q-head' }, h('span', { class: 'pct' }), h('span', { class: 'rate' })),
        h('div', { class: 'bar' }, h('i')),
        entry,
        h('p', { class: 'word' }),
        this.drawRetired(item)),
      h('div', { class: 'q-acts' }, acts));
  }

  // What became of the versions a finished download replaced.
  drawRetired(item) {
    const r = item.retired;
    if (item.state !== 'done' || !r) return null;
    if (r.error) return h('p', { class: 'retired bad' }, t('dl.retire_failed'));
    if (!r.moved || !r.moved.length) return null;
    return h('p', { class: 'retired' }, t('dl.retired', { list: listOf(r.moved.map((name) => [name, null])) }));
  }

  updateRow(el, item) {
    const total = item.bytes_total || 0;
    const pct = total ? Math.min(100, Math.floor((item.bytes_done / total) * 1000) / 10) : 0;
    const showBar = ['running', 'paused', 'retrying', 'verifying'].includes(item.state) && total > 0;
    el.querySelector('.bar').hidden = !showBar;
    el.querySelector('.bar i').style.setProperty('--p', item.state === 'verifying' ? 1 : (pct / 100).toFixed(4));
    const head = el.querySelector('.q-head');
    head.hidden = item.state !== 'running';
    if (item.state === 'running') {
      el.querySelector('.pct').textContent = pct.toFixed(pct < 10 ? 1 : 0) + '%';
      el.querySelector('.rate').textContent = item.speed > 0
        ? t('dl.st_speed', { speed: formatBytes(item.speed), eta: item.eta !== null ? formatDuration(item.eta) : '…' })
        : t('dl.st_starting');
    }
    const word = el.querySelector('.word');
    let text = '';
    let cls = 'word';
    if (item.state === 'queued') text = t('dl.st_queued');
    else if (item.state === 'verifying') { text = t('dl.st_verifying'); cls += ' blink'; }
    else if (item.state === 'paused') text = t('dl.st_paused') + (pct ? ' · ' + pct.toFixed(0) + '%' : '');
    else if (item.state === 'retrying') text = item.message || '';
    else if (item.state === 'failed') { text = item.message || ''; cls += ' bad'; }
    else if (item.state === 'done') { text = item.checked ? t('dl.st_done') : t('dl.st_done_unchecked'); cls += ' good'; }
    word.className = cls;
    word.hidden = !text;
    word.textContent = text;
  }
}
