// The dossier: the selected model's examples come first (the author's, then the pictures added here), then its card
// (the recommended settings), what the example in view was made with, the trigger words, the training facts of a model
// civitai does not know, the notes, the way to civitai and to the file, where the files are, and the authors' notes.
// Opening a model whose civitai page has not been read yet reads it: the author's notes for the whole model are there,
// and the card is drafted again from them.
//
// "Edit details" turns the whole dossier into one form: the name and the base model, the card, the trigger words and
// the notes, each field marked once it differs from the suggestion, with the way back to it (the user's choice of the
// drafts, 2026-10-03). Pictures are added from a file or dropped on the dossier, and any still example can be the cover.
import { api, upload } from './api.js';
import { h, fill, formatBytes } from './dom.js';
import { t, currentLang } from './i18n.js';
import { familyName, familyCode } from './families.js';
import { mosaicOf, loadImage } from './mosaic.js';
import { safeFragment } from './sanitize.js';
import { inLanguage, linesInLanguage } from './lang.js';
import { MASK_FROM, levelName, icon, copyText, recall, remember, titleOf } from './ui.js';
import { decode, readout, rise, fade, moving, pace } from './motion.js';

const HERO_ASPECT = 384 / 330;
const ARRIVAL = 640;          // the arrival's length at a pace of 1: the sixth part starts at 400 and rises for 240
const KEY_OPTIONAL = 'md.trig_optional';       // '1': the one-click copy takes the optional trigger words too
const KEY_INFO = 'md.viewer_info';             // '0': the larger view hides the panel of the example's settings
const KEY_OTHER = 'md.other_params';           // '1': that panel's "Other settings" is kept open
const THUMB_ASPECT = 70 / 97;
const PICTURE_TYPES = { png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', webp: 'image/webp' };

const CARD_LABEL = {
  sampler: 'dos.cf_sampler', scheduler: 'dos.cf_scheduler', steps: 'dos.cf_steps', cfg: 'dos.cf_cfg',
  clip_skip: 'dos.cf_clip_skip', size: 'dos.cf_size', width: 'dos.cf_size', height: 'dos.cf_size', weight: 'dos.cf_weight',
  weight_range: 'dos.cf_weight_range', weight_min: 'dos.cf_weight_range', weight_max: 'dos.cf_weight_range',
  base_models: 'dos.cf_base_models', use: 'dos.cf_use', trigger: 'dos.cf_trigger', notes: 'dos.cf_notes',
  prompt_prefix: 'dos.cf_prompt_prefix', negative_prefix: 'dos.cf_negative_prefix', trigger_words: 'dos.triggers',
  title: 'dos.f_name', base_model: 'dos.f_base_model',
};
const SOURCE_LABEL = {
  training: 'dos.src_training', civitai: 'dos.src_civitai', samples: 'dos.src_samples', description: 'dos.src_description',
  imported: 'dos.src_imported', user: 'dos.src_user',
};
const DRAFTED = ['training', 'civitai', 'samples', 'description', 'imported'];     // the card's layers under the user's

// The card's fields in the form, by kind of card: a grid of short values, then the longer texts.
const EDIT_GRID = {
  checkpoint: [
    { field: 'sampler', control: 'sampler' }, { field: 'scheduler', control: 'scheduler' },
    { field: 'steps', control: 'int' }, { field: 'cfg', control: 'number' }, { field: 'clip_skip', control: 'int' },
    { pair: ['width', 'height'], label: 'size', control: 'int', join: ' × ' },
  ],
  lora: [
    { field: 'weight', control: 'number' }, { pair: ['weight_min', 'weight_max'], label: 'weight_range', control: 'number', join: ' ~ ' },
    { field: 'base_models', control: 'list', wide: true },
  ],
  embedding: [{ field: 'use', control: 'use' }, { field: 'trigger', control: 'line' }],
};
const EDIT_TEXTS = { checkpoint: ['prompt_prefix', 'negative_prefix'], lora: [], embedding: [] };
const NUMBER_FIELDS = new Set(['steps', 'cfg', 'clip_skip', 'width', 'height', 'weight', 'weight_min', 'weight_max']);

function shortDate(seconds) {
  if (!seconds) return '';
  const d = new Date(seconds * 1000);
  return d.toLocaleDateString(currentLang() === 'zh' ? 'zh-CN' : 'en-GB', { year: 'numeric', month: 'short', day: 'numeric' });
}

function firstStill(items) {
  const i = (items || []).findIndex((s) => s.type !== 'video');
  return i < 0 ? 0 : i;
}

let retry = 0;        // goes up with every retry, so the browser asks again instead of reusing a failed answer

function sampleSrc(sha, index) {
  return '/api/ui/models/' + sha + '/samples/' + index + (retry ? '?retry=' + retry : '');
}

// The examples as one list: the author's in civitai's order, then the pictures added here in the order added.
function galleryOf(d) {
  const items = (d.samples || []).map((s, i) => ({ ...s, src: sampleSrc(d.sha256, i), sample: i }));
  for (const p of d.images || []) {
    items.push({ type: 'image', url: p.url, src: p.url, width: p.width, height: p.height, level: null, params: p.params,
      image: p.id, local: true });
  }
  return items;
}

function thumbFailed() {
  return h('span', { class: 'note failed', title: t('dos.sample_failed_short') }, icon('i-x'));
}

function fileName(path) {
  return String(path || '').split(/[\\/]/).pop();
}

// civitai reads the settings of a ComfyUI picture from its workflow; a setting fed by another node comes back as that
// node (an object) instead of a value, and is not shown.
function plain(value) {
  return typeof value === 'number' || typeof value === 'string' ? value : null;
}

// An example's size: as its generation data states it, or else the picture's own.
function sizeOf(p, sample) {
  const stated = p && typeof p.Size === 'string' && p.Size.match(/^\s*(\d+)\s*[x×*]\s*(\d+)\s*$/);
  if (stated) return stated[1] + ' × ' + stated[2];
  const width = p ? Number(plain(p.width)) : 0;
  const height = p ? Number(plain(p.height)) : 0;
  if (width > 0 && height > 0) return width + ' × ' + height;
  return sample && sample.width && sample.height ? sample.width + ' × ' + sample.height : null;
}

// The larger view's panel. Generation data comes in civitai's keys (prompt, cfgScale) and in WebUI's (CFG scale,
// Hires upscale), as each example was made; the first key present is the one shown.
const firstOf = (p, ...keys) => {
  for (const k of keys) if (plain(p[k]) !== null && p[k] !== '') return p[k];
  return null;
};
const MAIN_PARAMS = [
  ['dos.p_sampler', ['sampler', 'Sampler']], ['dos.p_scheduler', ['scheduler', 'Schedule type']],
  ['dos.p_steps', ['steps', 'Steps']], ['dos.p_cfg', ['cfgScale', 'cfg', 'CFG scale']],
  ['dos.p_seed', ['seed', 'Seed']], ['dos.p_size', []], ['dos.p_clip_skip', ['clipSkip', 'Clip skip']],
  ['dos.p_denoise', ['denoise']],
];
const HIRES_PARAMS = [
  ['dos.p_upscaler', ['Hires upscaler']], ['dos.p_upscale', ['Hires upscale']], ['dos.p_steps', ['Hires steps']],
  ['dos.p_denoise', ['Denoising strength']],
];
// shown elsewhere in the panel, or of no use read as text (a workflow, hashes, lists of files)
const NOT_OTHER = new Set(['prompt', 'negativePrompt', 'Size', 'width', 'height', 'comfy', 'hashes', 'resources',
  'civitaiResources', 'additionalResources', 'models', 'vaes', 'upscalers', 'Template', 'Negative Template', 'workflow',
  ...MAIN_PARAMS.flatMap(([, keys]) => keys), ...HIRES_PARAMS.flatMap(([, keys]) => keys)]);
const RESOURCE_TYPE = { model: 'Checkpoint', checkpoint: 'Checkpoint', lora: 'LoRA', locon: 'LoRA', lycoris: 'LyCORIS',
  embed: 'Embedding', embedding: 'Embedding', textualinversion: 'Embedding', vae: 'VAE' };

// Whether an example (a video aside) carries the settings it was made with.
const hasParams = (s) => Boolean(s && s.type !== 'video' && s.params && typeof s.params === 'object' && Object.keys(s.params).length);

// The models and LoRAs an example names, with their weights: WebUI's list, or the one civitai's generator writes.
// Each carries the key it can be matched to a file here by (depot/library.py resource_keys): WebUI's short hash, or
// the civitai version's number; one named only by its file name has none.
function resourcesOf(p, matches) {
  const list = Array.isArray(p.resources) && p.resources.length ? p.resources
    : Array.isArray(p.additionalResources) ? p.additionalResources : [];
  const short = (name) => String(name).split(/[\\/]/).pop().replace(/\.(safetensors|ckpt|pt|gguf)$/i, '');
  const hashes = p.hashes && typeof p.hashes === 'object' ? p.hashes : {};
  const hashKey = (hash) => (typeof hash === 'string' && hash.trim() ? 'h:' + hash.trim().toLowerCase() : null);
  const out = list.filter((r) => r && typeof r === 'object' && plain(r.name) !== null).map((r) => {
    const type = String(r.type || '').toLowerCase();
    return {
      name: short(r.name),
      type: RESOURCE_TYPE[type] || String(r.type || ''),
      weight: plain(r.weight) !== null ? r.weight : plain(r.strength),
      key: hashKey(r.hash) || hashKey(hashes[(type === 'model' ? 'model' : type + ':' + r.name)]),
    };
  });
  if (plain(p.Model) !== null && !out.some((r) => r.type === 'Checkpoint')) {
    out.unshift({ name: short(p.Model), type: 'Checkpoint', weight: null, key: hashKey(p['Model hash']) || hashKey(hashes.model) });
  }
  // civitai's generator names versions by number only: the name is the file's here, or the version's
  if (!out.length && Array.isArray(p.civitaiResources)) {
    for (const r of p.civitaiResources) {
      const vid = r && Number.parseInt(r.modelVersionId, 10);
      if (!vid) continue;
      const here = matches && matches['v:' + vid];
      out.push({
        name: here ? here.title + (here.version_name ? ' · ' + here.version_name : '') : plain(r.modelVersionName) || t('dos.res_version', { id: vid }),
        type: RESOURCE_TYPE[String(r.type || '').toLowerCase()] || String(r.type || ''),
        weight: plain(r.weight) !== null ? r.weight : plain(r.strength),
        key: 'v:' + vid,
      });
    }
  }
  return out.sort((a, b) => (a.type === 'Checkpoint' ? 0 : 1) - (b.type === 'Checkpoint' ? 0 : 1));
}

// The settings in WebUI's "parameters" text, which WebUI and Forge read back from their prompt box.
function parametersText(p, sample) {
  const lines = [];
  if (plain(p.prompt)) lines.push(String(p.prompt));
  if (plain(p.negativePrompt)) lines.push('Negative prompt: ' + p.negativePrompt);
  const pairs = [];
  const add = (key, value) => {
    if (value === null || value === undefined || value === '') return;
    const text = String(value);
    pairs.push(key + ': ' + (/[,:"\n]/.test(text) ? JSON.stringify(text) : text));
  };
  add('Steps', firstOf(p, 'steps', 'Steps'));
  add('Sampler', firstOf(p, 'sampler', 'Sampler'));
  add('Schedule type', firstOf(p, 'Schedule type', 'scheduler'));
  add('CFG scale', firstOf(p, 'cfgScale', 'cfg', 'CFG scale'));
  add('Seed', firstOf(p, 'seed', 'Seed'));
  const size = sizeOf(p, sample);
  if (size) add('Size', size.replace(' × ', 'x'));
  add('Clip skip', firstOf(p, 'clipSkip', 'Clip skip'));
  for (const [k, v] of Object.entries(p)) {
    // the ones written above are left out; the hires settings and WebUI's own keys follow as they are
    if (NOT_OTHER.has(k) && !HIRES_PARAMS.some(([, keys]) => keys.includes(k))) continue;
    if (plain(v) !== null) add(k, v);
  }
  if (pairs.length) lines.push(pairs.join(', '));
  return lines.join('\n');
}

// What a dropped or chosen file is, from its type or else its name: a PNG, JPEG or WebP, or null.
function pictureType(file) {
  if (/^image\/(png|jpeg|webp)$/.test(file.type)) return file.type;
  return PICTURE_TYPES[String(file.name || '').split('.').pop().toLowerCase()] || null;
}

function hasFiles(e) {
  return Boolean(e.dataTransfer && [...e.dataTransfer.types].includes('Files'));
}

// A picture dropped anywhere but on the dossier must not open in place of the page (in the Windows app as well).
let dropsGuarded = false;
function guardDrops() {
  if (dropsGuarded) return;
  dropsGuarded = true;
  window.addEventListener('dragover', (e) => {
    if (hasFiles(e) && !e.defaultPrevented) { e.preventDefault(); e.dataTransfer.dropEffect = 'none'; }
  });
  window.addEventListener('drop', (e) => { if (hasFiles(e)) e.preventDefault(); });
}

// ---- the form's values: a card value as the text in its field, and back -------------------------------------------
function toInput(value) {
  if (value === undefined || value === null) return '';
  return Array.isArray(value) ? value.join(', ') : String(value);
}

// The value a field's text stands for: null when empty, NaN when a number cannot be read.
function fromInput(field, text) {
  const s = String(text || '').trim();
  if (!s) return null;
  if (NUMBER_FIELDS.has(field)) {
    const n = Number(s.replace(',', '.'));
    if (!Number.isFinite(n)) return NaN;
    return field === 'clip_skip' ? -Math.abs(n) : n;      // ComfyUI's way of writing it, as the card keeps it
  }
  if (field === 'base_models') {
    const list = s.split(',').map((x) => x.trim()).filter(Boolean);
    return list.length ? list : null;
  }
  return s;
}

function same(a, b) {
  const norm = (v) => (v === undefined || v === '' || (Array.isArray(v) && !v.length) ? null : v);
  return JSON.stringify(norm(a)) === JSON.stringify(norm(b));
}

// The value a field would have without the user's, and the layer it comes from.
function suggestionOf(card, field) {
  const layers = (card && card.layers) || {};
  let value = null;
  let source = null;
  for (const name of DRAFTED) {
    const v = (layers[name] || {})[field];
    if (v !== undefined && v !== null && v !== '' && !(Array.isArray(v) && !v.length)) { value = v; source = name; }
  }
  return { value, source };
}

function triggerList(words) {
  return (words || []).map((w) => ({ text: String(w.text || '').trim(), required: w.required !== false })).filter((w) => w.text);
}

function shorten(text, n = 24) {
  const s = String(text || '');
  return s.length > n ? s.slice(0, n - 1) + '…' : s;
}

export class Dossier {
  constructor(root, ctx) {
    this.root = root;
    this.ctx = ctx;
    this.token = 0;
    this.detail = null;
    this.index = 0;
    this.revealed = new Set();      // examples shown unmasked in this visit: "<sha>/<index>"
    this.pageReading = null;        // the model whose civitai page is being read
    this.pageError = null;          // {sha, message} of the last reading that failed
    this.editing = null;            // the form's state while the details are being edited
    this.media = null;              // {sha, text, bad}: what adding a picture or choosing the cover came to
    this.actionNote = null;         // {sha, text, bad}: under the buttons (the details saved)
    this.adding = false;
    this.coverBusy = false;
    this.viewer = document.getElementById('viewer');
    this.viewer.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowLeft') { e.preventDefault(); this.step(-1, true); }
      if (e.key === 'ArrowRight') { e.preventDefault(); this.step(1, true); }
    });
    // Pictures dropped on the dossier are added to the model's examples.
    guardDrops();
    this.dragDepth = 0;
    root.addEventListener('dragenter', (e) => {
      if (!this.canDrop(e)) return;
      e.preventDefault();
      this.dragDepth += 1;
      this.showDrop(true);
    });
    root.addEventListener('dragover', (e) => {
      if (!this.canDrop(e)) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = 'copy';
    });
    root.addEventListener('dragleave', (e) => {
      if (!this.canDrop(e)) return;
      this.dragDepth = Math.max(0, this.dragDepth - 1);
      if (!this.dragDepth) this.showDrop(false);
    });
    root.addEventListener('drop', (e) => {
      if (!this.canDrop(e)) return;
      e.preventDefault();
      this.dragDepth = 0;
      this.showDrop(false);
      this.addFiles([...e.dataTransfer.files]);
    });
  }

  async show(sha, redraw) {
    const token = ++this.token;
    if (!sha) {
      this.detail = null;
      this.endEdit();
      fill(this.root, h('div', { class: 'placeholder' }, t('dos.empty')));
      return;
    }
    if (redraw && this.detail && this.detail.sha256 === sha) { this.draw(true); return; }
    let detail;
    try {
      detail = await api('GET', '/api/v1/models/' + sha);
    } catch (e) {
      if (token === this.token) fill(this.root, h('div', { class: 'placeholder' }, e.message));
      return;
    }
    if (token !== this.token) return;
    if (!this.detail || this.detail.sha256 !== sha) this.endEdit();      // another model: an unsaved edit is dropped
    this.detail = detail;
    this.index = firstStill(this.items());
    this.draw(false);
    this.root.scrollTop = 0;
    // The first time a model is opened, its civitai page is read: one request, as a visit to the page would be.
    if (detail.page && detail.page.state === 'missing' && !(this.pageError && this.pageError.sha === sha)) this.readPage(false);
    else this.askReader();
  }

  // The examples of the model shown (galleryOf), made again whenever the detail changes.
  items() {
    if (this.itemsOf !== this.detail || this.itemsRetry !== retry) {
      this.itemsOf = this.detail;
      this.itemsRetry = retry;
      this.itemList = this.detail ? galleryOf(this.detail) : [];
    }
    return this.itemList;
  }

  // Have the local model read the notes when it can and has not yet, and follow its reading until it is done.
  async askReader() {
    const d = this.detail;
    const r = d && d.card && d.card.reader;
    if (!r) return;
    if (r.can_read) {
      try { await api('POST', '/api/ui/models/' + d.sha256 + '/notes', {}); } catch (e) { return; }
    } else if (!['queued', 'reading', 'waiting_gpu'].includes(r.state)) {
      return;
    }
    this.followReader(d.sha256);
  }

  followReader(sha) {
    if (this.readerTimer) clearTimeout(this.readerTimer);
    this.readerTimer = setTimeout(async () => {
      if (!this.detail || this.detail.sha256 !== sha) return;
      let detail;
      try { detail = await api('GET', '/api/v1/models/' + sha); } catch (e) { return; }
      if (!this.detail || this.detail.sha256 !== sha) return;
      const before = JSON.stringify(this.detail.card);
      this.detail = detail;
      if (JSON.stringify(detail.card) !== before) this.refresh();
      const r = detail.card && detail.card.reader;
      if (r && ['queued', 'reading', 'waiting_gpu'].includes(r.state)) this.followReader(sha);
    }, 3000);
  }

  async readPage(refresh, retry) {
    const sha = this.detail && this.detail.sha256;
    if (!sha || this.pageReading === sha) return;
    this.pageReading = sha;
    this.pageError = null;
    this.refresh();
    try {
      const detail = await api('POST', '/api/ui/models/' + sha + '/page', { refresh: Boolean(refresh), retry: Boolean(retry) });
      if (this.detail && this.detail.sha256 === sha) this.detail = detail;
    } catch (e) {
      this.pageError = { sha, message: e.message };
    } finally {
      if (this.pageReading === sha) this.pageReading = null;
      if (this.detail && this.detail.sha256 === sha) this.refresh();
    }
    if (this.detail && this.detail.sha256 === sha) this.askReader();
  }

  masked(index) {
    const s = this.items()[index];
    return Boolean(s && !s.local && this.ctx.mask() && s.level >= MASK_FROM && !this.revealed.has(this.detail.sha256 + '/' + index));
  }

  isCover(s) {
    const c = this.detail && this.detail.cover_from;
    if (!c || !s) return false;
    return s.local ? c.image === s.image : c.sample === s.sample;
  }

  // ---- the whole panel -----------------------------------------------------------------------------------------
  draw(keepScroll) {
    clearTimeout(this.refreshTimer);      // whatever drawing was waiting is drawn now
    const d = this.detail;
    const scroll = this.root.scrollTop;
    const items = this.items();
    const sample = items[this.index] || null;
    // a version that shares its civitai page with others here goes by its own name, as its card does
    const set = this.ctx.setOf && d.version_name ? this.ctx.setOf(d.sha256) : null;
    const body = this.editing ? this.drawEditing(keepScroll) : h('div', { class: 'dossier-body' + (keepScroll ? ' still' : '') },
      this.drawHero(),
      items.length || d.cover ? this.drawThumbs() : null,
      this.mediaNote(),
      h('h2', { class: 'dos-name' }, set ? d.version_name : titleOf(d, currentLang())),
      set ? h('p', { class: 'dos-set' }, set.title) : null,
      h('div', { class: 'dos-sub' },
        h('span', null, [set ? null : d.version_name, d.base_model || familyName(d.family)].filter(Boolean).join(' · ')),
        h('span', { class: 'source' }, this.sourceText())),
      this.drawUpdate(),
      this.drawCard(),
      items.length ? this.drawParams(sample) : null,
      this.drawTriggers(),
      this.drawTraining(),
      this.drawNotes(),
      this.drawActions(),
      sample && sample.params && plain(sample.params.prompt) ? this.drawPrompt(sample.params) : null,
      this.drawFiles(),
      // notes written in both languages show the interface's language; notes in one language show whole
      d.description ? this.fold(t('dos.about_version'), inLanguage(safeFragment(d.description), currentLang())) : null,
      d.model_description ? this.fold(t('dos.about_model'), inLanguage(safeFragment(d.model_description), currentLang())) : null);
    // A redraw keeps the pictures already on the page, and a decode under way with them (a picture made anew would load
    // again and blink): the thumbnails strip whichever example is shown, so that it also stays where it was scrolled
    // to (only the mark moves); the hero while it shows the same example, its buttons and count brought up to date.
    const old = keepScroll ? this.root.querySelector(':scope > .dossier-body') : null;
    const oldStrip = old && old.querySelector(':scope > .dos-thumbs');
    const left = oldStrip ? oldStrip.scrollLeft : 0;      // moving or making the strip anew resets where it was scrolled
    if (old) {
      for (const part of this.decodeNext ? ['.dos-thumbs'] : ['.dos-hero', '.dos-thumbs']) {
        const was = old.querySelector(':scope > ' + part);
        const now = body.querySelector(':scope > ' + part);
        if (!was || !now || was.dataset.key !== now.dataset.key) continue;
        if (part === '.dos-hero') {
          for (const live of ['.hero-tools', '.dos-count']) {
            const fresh = now.querySelector(':scope > ' + live);
            const stale = was.querySelector(':scope > ' + live);
            if (stale) stale.remove();
            if (fresh) was.append(fresh);
          }
        }
        now.replaceWith(was);
      }
    }
    fill(this.root,
      h('div', { class: 'dos-close' }, h('button', { type: 'button', onclick: () => this.ctx.onClose() }, icon('i-x'), t('dos.close'))),
      body);
    if (keepScroll) this.root.scrollTop = scroll;
    const strip = body.querySelector(':scope > .dos-thumbs');
    if (strip && oldStrip) strip.scrollLeft = left;
    this.markThumb(body);
    // A model's arrival is the one authored moment: its example decoded, its name wiped in. A redraw of the same model
    // (its card read, a setting changed) shows no motion, except a new example, which is decoded in turn.
    if (!keepScroll) this.enter(body);
    else if (this.decodeNext) decode(body.querySelector('.dos-hero img'));
    this.decodeNext = false;
  }

  sourceText() {
    const d = this.detail;
    const named = { civitai: t('dos.source_civitai'), local: t('dos.source_local') }[d.identified_by];
    return named || (d.training ? t('dos.source_training') : t('dos.source_none'));
  }

  // A redraw nobody asked for (the civitai page read, the card read by the local model) waits until the arrival has
  // played and the example has been decoded, so that the one authored moment is not cut short; an example that does not
  // arrive holds it back three seconds at most. While the details are edited it waits for the form to close.
  refresh() {
    clearTimeout(this.refreshTimer);
    if (!this.detail || this.editing) return;
    const now = performance.now();
    const ends = this.arrivalEnds || 0;
    if (now < ends || (moving(this.root.querySelector('.dos-hero img')) && now < ends + 3000)) {
      this.refreshTimer = setTimeout(() => this.refresh(), 120);
      return;
    }
    this.draw(true);
  }

  // The arrival, as the recorded draft of scheme 3 did it (the user's choice, 2026-10-03): the example decoded, the name
  // wiped in, and the first six of the other parts rising into place, each a little after the one before. The last
  // part is in place ARRIVAL ms (at a pace of 1) after it begins.
  enter(body) {
    this.arrivalEnds = performance.now() + pace(ARRIVAL);
    decode(body.querySelector('.dos-hero img'));
    readout(body.querySelector('.dos-name'), 120);
    [...body.children].filter((c) => !c.matches('.dos-hero, .dos-name')).slice(0, 6).forEach((c, i) => rise(c, 200 + i * 40));
  }

  // The covers were shown again (the eye switch): an example that was masked is decoded as it appears.
  revealCovers() {
    const s = this.detail && this.items()[this.index];
    this.decodeNext = Boolean(s && s.level >= MASK_FROM);
    this.draw(true);
  }

  // ---- the examples --------------------------------------------------------------------------------------------
  drawHero() {
    const d = this.detail;
    const items = this.items();
    const shown = items[this.index];
    // what the hero shows, so that a redraw showing the same keeps it (draw)
    const key = [d.sha256, this.index, shown ? shown.src : d.cover || '', this.masked(this.index) || (this.ctx.mask() && d.cover_level >= MASK_FROM),
      retry].join('|');
    const hero = h('div', { class: 'dos-hero', 'data-key': key });
    if (!items.length) {
      if (d.cover) {
        const masked = this.ctx.mask() && d.cover_level >= MASK_FROM;
        if (masked) this.putMosaic(hero, d.cover, HERO_ASPECT);
        else hero.append(h('img', { src: d.cover, alt: '' }));
      } else {
        hero.classList.add('no-samples');
        hero.append(h('div', { class: 'note' }, icon('i-image'), h('span', null, t('dos.samples_empty')),
          h('span', { class: 'note-actions' }, h('button', { class: 'btn btn-line btn-small', type: 'button', onclick: () => this.pickImages() },
            icon('i-plus'), t('dos.add_samples')))));
      }
      return hero;
    }
    const s = shown;
    const tools = this.heroTools(s);
    if (s.type === 'video') {
      hero.append(h('div', { class: 'note' }, icon('i-play'), t('dos.sample_video'),
        d.civitai ? h('a', { href: d.civitai.url, target: '_blank', rel: 'noopener noreferrer' }, t('dos.open_civitai')) : null));
    } else if (this.masked(this.index)) {
      const reveal = h('button', { class: 'reveal', type: 'button', onclick: () => {
        this.revealed.add(d.sha256 + '/' + this.index);
        this.decodeNext = true;
        this.draw(true);
      } },
        t('dos.sample_reveal') + ' · ' + levelName(s.level));
      this.putMosaic(hero, s.src, HERO_ASPECT, 10, () => reveal.remove());
      hero.append(reveal);
    } else {
      const img = h('img', { src: s.src, alt: '', decoding: 'async' });
      const expand = h('button', { class: 'expand', type: 'button', title: t('dos.view_full'), 'aria-label': t('dos.view_full'), onclick: () => this.openViewer() },
        icon('i-expand'));
      img.addEventListener('error', () => { expand.remove(); tools.querySelectorAll('.cover-btn').forEach((b) => b.remove()); img.replaceWith(this.failedNote(s)); });
      hero.append(img, expand);
      hero.addEventListener('click', (e) => { if (e.target === img) this.openViewer(); });
    }
    hero.append(tools);
    if (items.length > 1) hero.append(h('span', { class: 'dos-count' }, (this.index + 1) + ' / ' + items.length));
    return hero;
  }

  // Over the example in view: whether it is the cover, or the button that makes it so; for a picture added here, the
  // button that takes it out again.
  heroTools(s) {
    const tools = h('div', { class: 'hero-tools' });
    if (s.type !== 'video' && !this.masked(this.index)) tools.append(this.coverControl(s, 'hero-btn'));
    if (s.local) {
      tools.append(h('button', { class: 'hero-btn icon-only', type: 'button', title: t('dos.image_remove'), 'aria-label': t('dos.image_remove'),
        onclick: () => this.removeImage(s) }, icon('i-trash')));
    }
    return tools;
  }

  coverControl(s, cls) {
    if (this.isCover(s)) return h('span', { class: cls + ' on' }, icon('i-image'), t('dos.cover_is'));
    return h('button', { class: cls + ' cover-btn', type: 'button', disabled: this.coverBusy, onclick: () => this.setCover(s) },
      icon('i-image'), t('dos.cover_set'));
  }

  // An example that cannot be fetched: what happened, what to do, a retry, and the way to the original page.
  failedNote(s) {
    const d = this.detail;
    return h('div', { class: 'note failed' },
      h('p', null, t('dos.sample_failed')),
      h('span', { class: 'note-actions' },
        h('button', { class: 'btn btn-line btn-small', type: 'button', onclick: () => { retry += 1; this.draw(true); } }, t('dos.retry')),
        d.civitai && !(s && s.local) ? h('a', { class: 'btn btn-line btn-small', href: d.civitai.url, target: '_blank', rel: 'noopener noreferrer' }, t('dos.open_civitai')) : null));
  }

  // The example in view is marked in the strip, and the strip scrolled to it when it lies outside (stepping through the
  // examples in the larger view, or with the arrow keys).
  markThumb(body) {
    const strip = body.querySelector(':scope > .dos-thumbs');
    if (!strip) return;
    const thumbs = strip.querySelectorAll('.dos-thumb');
    thumbs.forEach((thumb, i) => thumb.setAttribute('aria-current', String(i === this.index)));
    const current = thumbs[this.index];
    if (!current) return;
    const s = strip.getBoundingClientRect(), c = current.getBoundingClientRect();
    if (c.left < s.left) strip.scrollLeft -= s.left - c.left + 4;
    else if (c.right > s.right) strip.scrollLeft += c.right - s.right + 4;
  }

  drawThumbs() {
    const d = this.detail;
    const items = this.items();
    // what the strip shows, so that a redraw keeps it (draw); which example is in view is marked by markThumb
    const key = [d.sha256, retry, items.map((s, i) => s.src + (this.masked(i) ? '#' : '') + (this.isCover(s) ? '*' : '') + (hasParams(s) ? '+' : '')).join(',')].join('|');
    return h('div', { class: 'dos-thumbs', role: 'group', 'aria-label': t('dos.samples'), 'data-key': key }, items.map((s, i) => {
      const button = h('button', { class: 'dos-thumb', type: 'button', 'aria-current': String(i === this.index),
        'aria-label': t('dos.sample_n', { n: i + 1, total: items.length }),
        onclick: () => { if (i === this.index) return; this.index = i; this.decodeNext = true; this.draw(true); } });
      if (s.type === 'video') {
        button.append(h('span', { class: 'note' }, icon('i-play')));
      } else if (this.masked(i)) {
        this.putMosaic(button, s.src, THUMB_ASPECT, 6, null, true);
      } else {
        const img = h('img', { src: s.src, alt: '', loading: 'lazy', decoding: 'async' });
        img.addEventListener('error', () => img.replaceWith(thumbFailed()));
        button.append(img);
      }
      const tags = [this.isCover(s) ? h('span', { class: 'thumb-tag' }, t('dos.cover_is')) : null,
        s.local ? h('span', { class: 'thumb-tag line' }, t('dos.local')) : null].filter(Boolean);
      if (tags.length) button.append(h('span', { class: 'thumb-tags' }, tags));
      // which examples carry what they were made with (many on civitai do not)
      if (hasParams(s)) {
        button.append(h('span', { class: 'thumb-tag params', 'aria-hidden': 'true' }, t('dos.params_tag')));
        button.title = t('dos.sample_has_params');
      }
      return button;
    }), h('button', { class: 'dos-add', type: 'button', title: t('dos.add_samples'), onclick: () => this.pickImages() },
      icon('i-plus'), t('dos.add')));
  }

  putMosaic(slot, src, aspect, cols = 10, onFail = null, small = false) {
    loadImage(src).then((img) => {
      const canvas = mosaicOf(img, aspect, cols, 0.24);
      slot.prepend(canvas);
      fade(canvas);
    }).catch(() => {
      if (onFail) onFail();
      slot.prepend(small ? thumbFailed() : this.failedNote());
    });
  }

  // What adding a picture or choosing the cover came to, under the examples.
  mediaNote() {
    const m = this.media;
    if (!m || !this.detail || m.sha !== this.detail.sha256) return null;
    return h('p', { class: 'dos-msg media-msg' + (m.bad ? ' bad' : ''), role: 'status' }, m.text);
  }

  setMedia(text, bad) {
    this.media = text ? { sha: this.detail.sha256, text, bad: Boolean(bad) } : null;
    const body = this.root.querySelector(':scope > .dossier-body');
    if (!body) return;
    const old = body.querySelector(':scope > .media-msg');
    const now = this.mediaNote();
    if (old) old.remove();
    if (!now) return;
    const after = body.querySelector(':scope > .dos-thumbs') || body.querySelector(':scope > .dos-hero');
    if (after) after.after(now);
    readout(now, 0, { duration: 200 });
  }

  step(by, inViewer) {
    const items = this.items();
    if (items.length < 2) return;
    this.goTo((this.index + by + items.length) % items.length, inViewer);
  }

  goTo(index, inViewer) {
    if (index === this.index) return;
    this.index = index;
    this.decodeNext = !inViewer;        // in the viewer the larger picture is the one decoded
    this.draw(true);
    if (inViewer) this.fillViewer();
  }

  openViewer() {
    this.fillViewer();
    if (!this.viewer.open) this.viewer.showModal();
  }

  // The larger view: the picture between two large arrows, a strip of all the examples under it, and in the bar the
  // cover (the user's choice of the drafts, 2026-10-03).
  fillViewer() {
    const d = this.detail;
    const items = this.items();
    const s = items[this.index];
    let stage;
    if (!s || s.type === 'video') stage = h('p', null, t('dos.sample_video'));
    else if (this.masked(this.index)) stage = h('p', null, levelName(s.level));
    else stage = h('img', { src: s.src, alt: d.title });
    const many = items.length > 1;
    const oldStrip = this.viewer.querySelector('.viewer-strip');
    const left = oldStrip ? oldStrip.scrollLeft : 0;
    const nav = (by, cls, key, id) => h('button', { class: 'viewer-nav ' + cls, type: 'button', title: t(key), 'aria-label': t(key),
      onclick: () => this.step(by, true) }, icon(id));
    const oldInfo = this.viewer.querySelector('.viewer-info');
    const infoTop = oldInfo ? oldInfo.scrollTop : 0;
    const showInfo = recall(KEY_INFO, '1') !== '0';
    const infoBtn = h('button', { type: 'button', class: 'bar-btn' + (showInfo ? ' on' : ''), 'aria-pressed': String(showInfo),
      onclick: () => { remember(KEY_INFO, showInfo ? '0' : '1'); this.fillViewer(); } }, icon('i-list'), h('span', null, t('dos.info_panel')));
    fill(this.viewer,
      h('div', { class: 'viewer-bar' },
        h('span', { class: 'title' }, d.title + (many ? '  ·  ' + t('dos.sample_n', { n: this.index + 1, total: items.length }) : '')),
        infoBtn,
        s && s.type !== 'video' && !this.masked(this.index) ? this.coverControl(s, 'bar-btn') : null,
        d.civitai ? h('a', { href: d.civitai.url, target: '_blank', rel: 'noopener noreferrer', title: t('dos.open_civitai'), 'aria-label': t('dos.open_civitai') }, icon('i-external')) : null,
        h('button', { type: 'button', title: t('dos.close'), 'aria-label': t('dos.close'), onclick: () => this.viewer.close() }, icon('i-x'))),
      h('div', { class: 'viewer-main' + (showInfo ? ' with-info' : '') },
        h('div', { class: 'viewer-left' },
          h('div', { class: 'viewer-stage', onclick: (e) => { if (e.target === e.currentTarget) this.viewer.close(); } }, stage,
            many ? nav(-1, 'prev', 'dos.view_prev', 'i-prev') : null,
            many ? nav(1, 'next', 'dos.view_next', 'i-next') : null),
          many ? this.viewerStrip(items) : null),
        showInfo ? this.viewerInfo(s) : null));
    const info = this.viewer.querySelector('.viewer-info');
    if (info && oldInfo && oldInfo.dataset.key === info.dataset.key) info.scrollTop = infoTop;
    const strip = this.viewer.querySelector('.viewer-strip');
    if (strip) {
      strip.scrollLeft = left;
      const current = strip.querySelector('[aria-current="true"]');
      if (current) {
        const r = strip.getBoundingClientRect(), c = current.getBoundingClientRect();
        if (c.left < r.left || c.right > r.right) strip.scrollLeft += c.left + c.width / 2 - (r.left + r.width / 2);
      }
    }
    if (stage.tagName === 'IMG') decode(stage, { cells: [40, 20, 10, 5] });
  }

  viewerStrip(items) {
    return h('div', { class: 'viewer-strip', role: 'group', 'aria-label': t('dos.samples') }, items.map((s, i) => {
      let face;
      if (s.type === 'video') face = h('span', { class: 'tile' }, icon('i-play'));
      else if (this.masked(i)) face = h('span', { class: 'tile' }, levelName(s.level));
      else face = h('img', { src: s.src, alt: '', loading: 'lazy', decoding: 'async' });
      return h('button', { type: 'button', 'aria-current': String(i === this.index), 'aria-label': t('dos.sample_n', { n: i + 1, total: items.length }),
        onclick: () => this.goTo(i, true) }, face);
    }));
  }

  // Beside the larger picture, what that example was made with: the prompts with their copy buttons, the settings, the
  // models and LoRAs it names, hires fix, and the rest as written; one button copies it all in WebUI's format.
  viewerInfo(s) {
    const p = s && s.type !== 'video' && s.params && typeof s.params === 'object' ? s.params : null;
    const key = s ? String(s.src || s.url || s.id || this.index) : '';
    const panel = h('aside', { class: 'viewer-info', 'data-key': key, 'aria-label': t('dos.params') });
    const cell = (label, value) => (value === null || value === undefined || value === ''
      ? null : h('div', null, h('dt', null, t(label)), label === 'dos.p_seed'
        ? h('dd', null, this.seedCopy(value)) : h('dd', { title: String(value) }, String(value))));
    // two to a row; an odd last one takes the whole row
    const grid = (cells) => {
      const shown = cells.filter(Boolean);
      if (shown.length % 2) shown[shown.length - 1].classList.add('wide');
      return shown.length ? h('dl', { class: 'dos-grid' }, shown) : null;
    };
    const text = (label, value) => this.textBlock(label, value);
    if (!p || !Object.keys(p).length) {
      return fill(panel, h('h3', { class: 'dos-label' }, t('dos.params')), h('p', { class: 'dos-none' }, t('dos.params_none')));
    }
    const main = MAIN_PARAMS.map(([label, keys]) => cell(label, keys.length ? firstOf(p, ...keys) : sizeOf(p, s)));
    const hires = HIRES_PARAMS.map(([label, keys]) => cell(label, firstOf(p, ...keys)));
    const resources = resourcesOf(p, this.detail.resource_matches);
    const others = Object.entries(p).filter(([k, v]) => !NOT_OTHER.has(k) && plain(v) !== null && v !== '')
      .sort(([a], [b]) => a.localeCompare(b));
    const copyAll = this.copyButton(() => parametersText(p, s), 'dos.copy_params', 'copy-all');
    copyAll.title = t('dos.copy_params_tip');
    return fill(panel,
      h('div', { class: 'dos-label-row' }, h('h3', { class: 'dos-label' }, t('dos.params')), copyAll),
      plain(p.prompt) ? text('dos.prompt', p.prompt) : null,
      plain(p.negativePrompt) ? text('dos.negative', p.negativePrompt) : null,
      grid(main),
      resources.length ? h('section', null, h('h3', { class: 'dos-label' }, t('dos.resources')),
        h('ul', { class: 'resources' }, resources.map((r) => this.resourceRow(r)))) : null,
      hires.some(Boolean) ? h('section', null, h('h3', { class: 'dos-label' }, t('dos.hires')), grid(hires)) : null,
      others.length ? this.otherParams(others) : null);
  }

  // One model an example names (the user's choice of the drafts, 2026-10-08): one that is here is a row that opens its
  // dossier, an arrow at its end; one known not to be here is greyed, with a link to its civitai page when the version
  // is known; one named only by its file name cannot be told either way and stays as it was.
  resourceRow(r) {
    const matches = this.detail.resource_matches || {};
    const here = r.key ? matches[r.key] : null;
    const weight = h('span', { class: 'weight' }, r.weight === null || r.weight === undefined ? '' : String(r.weight));
    const parts = [h('span', { class: 'name', title: r.name }, r.name), h('span', { class: 'type' }, r.type), weight];
    if (here) {
      const label = t('dos.res_open', { name: here.title });
      return h('li', { class: 'here' }, h('button', { type: 'button', title: label, 'aria-label': label, onclick: () => {
        this.viewer.close();
        location.hash = '#/m/' + here.sha256;
      } }, parts, h('span', { class: 'go' }, icon('i-go'))));
    }
    if (!r.key) return h('li', null, parts, h('span', { class: 'go' }));
    const vid = r.key.startsWith('v:') ? r.key.slice(2) : null;
    return h('li', { class: 'away', title: t('dos.res_away') }, parts, vid
      ? h('a', { class: 'go', href: 'https://civitai.com/model-versions/' + vid, target: '_blank', rel: 'noopener noreferrer',
        title: t('dos.open_civitai'), 'aria-label': t('dos.open_civitai') }, icon('i-external'))
      : h('span', { class: 'go' }));
  }

  // "Other settings" stays as the user last left it, open or folded.
  otherParams(others) {
    const fold = this.fold(t('dos.other_params'), h('dl', { class: 'other-params' },
      others.map(([k, v]) => h('div', null, h('dt', null, k), h('dd', null, String(v))))), recall(KEY_OTHER, '0') === '1');
    fold.addEventListener('toggle', () => remember(KEY_OTHER, fold.open ? '1' : '0'));
    return fold;
  }

  // ---- adding pictures, choosing the cover --------------------------------------------------------------------------
  pickImages() {
    if (!this.picker) {
      this.picker = h('input', { type: 'file', accept: 'image/png,image/jpeg,image/webp', multiple: true, hidden: true });
      this.picker.addEventListener('change', () => {
        const files = [...this.picker.files];
        this.picker.value = '';
        this.addFiles(files);
      });
      document.body.append(this.picker);
    }
    this.picker.click();
  }

  canDrop(e) {
    return Boolean(this.detail && hasFiles(e));
  }

  showDrop(on) {
    let overlay = this.root.querySelector(':scope > .dos-drop');
    if (!on) { if (overlay) overlay.remove(); return; }
    if (!overlay) {
      overlay = h('div', { class: 'dos-drop', 'aria-hidden': 'true' }, h('b', null, icon('i-plus'), t('dos.drop')));
      this.root.append(overlay);
    }
    // over the part of the dossier in view, wherever it is scrolled to
    overlay.style.top = this.root.scrollTop + 'px';
    overlay.style.height = this.root.clientHeight + 'px';
  }

  async addFiles(files) {
    const d = this.detail;
    if (!d || this.adding || !files.length) return;
    const sha = d.sha256;
    const errors = [];
    const usable = [];
    for (const file of files) {
      const type = pictureType(file);
      if (type) usable.push({ file, type });
      else errors.push(t('dos.add_failed', { name: file.name, reason: t('err.image_unreadable') }));
    }
    this.adding = true;
    let latest = null;
    for (const [i, p] of usable.entries()) {
      if (this.detail && this.detail.sha256 === sha) this.setMedia(t('dos.adding', { n: i + 1, total: usable.length }));
      try {
        latest = await upload('/api/ui/models/' + sha + '/images', p.file, p.type);
      } catch (e) {
        errors.push(t('dos.add_failed', { name: p.file.name, reason: e.message }));
      }
    }
    this.adding = false;
    if (latest && this.ctx.onChanged) this.ctx.onChanged(sha, true);
    if (!this.detail || this.detail.sha256 !== sha) return;
    if (latest) {
      const before = this.items().length;
      this.detail = latest;
      if (this.items().length > before) { this.index = before; this.decodeNext = true; }       // the first one added
    }
    this.media = errors.length ? { sha, text: errors.join(' '), bad: true } : null;
    this.draw(true);
  }

  async removeImage(s) {
    const d = this.detail;
    try {
      const detail = await api('DELETE', '/api/ui/models/' + d.sha256 + '/images/' + s.image);
      if (this.ctx.onChanged) this.ctx.onChanged(d.sha256, true);
      if (!this.detail || this.detail.sha256 !== d.sha256) return;
      this.detail = detail;
      this.index = Math.max(0, Math.min(this.index, this.items().length - 1));
      this.decodeNext = true;
      this.media = null;
      this.draw(true);
      if (this.viewer.open) { if (this.items().length) this.fillViewer(); else this.viewer.close(); }
    } catch (e) {
      this.setMedia(e.message, true);
    }
  }

  async setCover(s) {
    const d = this.detail;
    if (this.coverBusy) return;
    this.coverBusy = true;
    this.root.querySelectorAll('.cover-btn').forEach((b) => { b.disabled = true; });
    try {
      const detail = await api('POST', '/api/ui/models/' + d.sha256 + '/cover', s.local ? { image: s.image } : { sample: s.sample });
      if (this.ctx.onChanged) this.ctx.onChanged(d.sha256, true);
      if (this.detail && this.detail.sha256 === d.sha256) {
        this.detail = detail;
        this.media = null;
      }
    } catch (e) {
      if (this.detail && this.detail.sha256 === d.sha256) this.media = { sha: d.sha256, text: e.message, bad: true };
    } finally {
      this.coverBusy = false;
    }
    if (!this.detail || this.detail.sha256 !== d.sha256) return;
    this.draw(true);
    if (this.viewer.open) this.fillViewer();
  }

  // ---- the card: the recommended settings, each with where it comes from --------------------------------------------
  drawCard() {
    const d = this.detail;
    const card = d.card || {};
    if (!card.kind) return null;            // VAEs, upscalers and the like have no card
    const v = card.values || {};
    const src = card.sources || {};
    const tag = (source) => (source ? h('span', { class: 'src' }, t(SOURCE_LABEL[source])) : null);
    const cell = (field, value, source, wide) => h('div', { class: wide ? 'wide' : null },
      h('dt', null, t(CARD_LABEL[field])), h('dd', { title: String(value) }, String(value)), tag(source));
    const cells = [];
    if (card.kind === 'checkpoint') {
      for (const field of ['sampler', 'scheduler', 'steps', 'cfg', 'clip_skip']) {
        if (v[field] !== undefined) cells.push(cell(field, v[field], src[field]));
      }
      if (v.width && v.height) cells.push(cell('size', v.width + ' × ' + v.height, src.width));
    } else if (card.kind === 'lora') {
      if (v.weight !== undefined) cells.push(cell('weight', v.weight, src.weight));
      if (v.weight_min !== undefined && v.weight_max !== undefined) {
        cells.push(cell('weight_range', v.weight_min + ' ~ ' + v.weight_max, src.weight_min));
      }
      if (v.base_models && v.base_models.length) cells.push(cell('base_models', v.base_models.join(' / '), src.base_models, true));
    } else if (card.kind === 'embedding') {
      if (v.use) cells.push(cell('use', t(v.use === 'negative' ? 'dos.cf_use_negative' : 'dos.cf_use_positive'), src.use));
      if (v.trigger) cells.push(cell('trigger', v.trigger, src.trigger));
    }
    const texts = ['prompt_prefix', 'negative_prefix'].filter((field) => v[field]).map((field) =>
      h('div', { class: 'card-text' },
        h('div', { class: 'card-text-head' }, h('span', { class: 'label' }, t(CARD_LABEL[field])), tag(src[field]),
          this.copyButton(v[field])),
        h('p', { class: 'prompt' }, v[field])));
    // A model civitai does not know, with nothing on its card yet: the first fields to fill, shown empty.
    if (!cells.length && !texts.length && !d.identified_by) {
      const first = { checkpoint: ['sampler', 'steps', 'cfg'], lora: ['weight'], embedding: ['use'] }[card.kind] || [];
      for (const field of first) cells.push(h('div', null, h('dt', null, t(CARD_LABEL[field])), h('dd', { class: 'unset' }, t('dos.unset'))));
    }
    const state = card.state === 'confirmed' ? 'dos.card_confirmed' : card.state === 'suggested' ? 'dos.card_suggested' : null;
    const evidence = card.evidence || {};
    const used = evidence.samples && evidence.samples.with_settings;
    const excerpt = linesInLanguage((evidence.description && evidence.description.excerpt) || [], currentLang());
    const page = this.drawPageLine();
    return h('section', { class: 'dos-cardsec' },
      h('div', { class: 'dos-label-row' }, h('h3', { class: 'dos-label' }, t('dos.card')),
        state ? h('span', { class: 'card-state' + (card.state === 'confirmed' ? ' ok' : '') }, t(state)) : null),
      cells.length ? h('dl', { class: 'dos-grid dos-card' }, cells) : null,
      texts,
      !cells.length && !texts.length ? h('p', { class: 'dos-none' }, t('dos.card_empty')) : null,
      used || page ? h('div', { class: 'card-foot' },
        used ? h('span', null, t('dos.card_basis', { used, total: evidence.samples.images })) : null, page) : null,
      this.drawReaderLine(card.reader),
      excerpt.length ? this.fold(t('dos.card_excerpt'), h('ul', { class: 'excerpt' }, excerpt.map((line) => h('li', null, line)))) : null);
  }

  // Who read the author's notes behind the card: the local model (named), or the rules, with the reason in small red
  // type when no local model is set up or the graphics card is below the lowest tier (the user's rule, 2026-10-02).
  drawReaderLine(r) {
    if (!r || !r.has_notes) return null;
    const line = (key, cls, params) => h('p', { class: 'reader-line' + (cls ? ' ' + cls : ''), role: 'status' }, t(key, params));
    if (r.state === 'reading') return line('dos.reader_reading', 'busy');
    if (r.state === 'queued') return line('dos.reader_queued', 'busy');
    if (r.state === 'waiting_gpu') return line('dos.reader_waiting_gpu', '');
    if (r.by === 'ai') return line('dos.reader_ai', '', { model: r.model || '' });
    if (r.state === 'failed') {
      const p = line('dos.reader_failed', 'bad');
      // a reading kept as failed is not asked again on opening; the user may ask
      if (r.stuck && r.can_retry) {
        p.append(' ', h('button', { class: 'link-btn', type: 'button', onclick: async () => {
          const d = this.detail;
          try { await api('POST', '/api/ui/models/' + d.sha256 + '/notes', { retry: true }); } catch (e) { return; }
          this.followReader(d.sha256);
        } }, t('dos.retry')));
      }
      return p;
    }
    if (!r.ready) {
      const key = { vram_low: 'dos.reader_vram_low', files_missing: 'dos.reader_files_missing' }[r.reason] || 'dos.reader_not_set_up';
      return line(key, 'bad', { gb: r.min_vram_gb });
    }
    return null;
  }

  // Whether the model's civitai page has been read (the author's notes for the whole model come from it).
  // ---- a new version on civitai (the user's choice of the drafts, 2026-10-14): under the title, before the card -----
  // Shown once the page has been checked. The newer version's files, the one in this file's precision chosen; the
  // download goes through the drawer, which offers to keep this version or move it to the recycle bin. Below, the
  // page's other new entries, which are not new versions of this one, and the switch to stop checking this model.
  drawUpdate() {
    const d = this.detail;
    const u = d.update;
    if (!u) return null;
    const day = (iso) => (iso ? shortDate(Date.parse(iso) / 1000) : '');
    const parts = [h('div', { class: 'dos-label-row' }, h('h3', { class: 'dos-label' }, t('dos.update')),
      u.checked_at ? h('span', { class: 'card-state' }, t('dos.update_checked', { date: shortDate(u.checked_at) })) : null)];
    if (!u.muted && u.newer) {
      const n = u.newer;
      const chosen = this.updateChoice && this.updateChoice.sha === d.sha256 && n.versions.some((v) => v.id === this.updateChoice.id)
        ? this.updateChoice.id : n.default;
      const pick = n.versions.find((v) => v.id === chosen) || n.versions[0];
      parts.push(
        h('div', { class: 'upd-head' }, h('span', { class: 'upd-name' }, n.name),
          h('span', { class: 'upd-sub' }, t('dos.update_published', { base: n.base_model || '', date: day(n.published_at) }))),
        h('p', { class: 'field-label' }, t('dos.update_files')),
        h('div', { class: 'choices', role: 'radiogroup', 'aria-label': t('dos.update_files') }, n.versions.map((v) =>
          h('button', { class: 'choice' + (v.id === chosen ? ' on' : ''), type: 'button', role: 'radio', 'aria-checked': String(v.id === chosen),
            onclick: () => { this.updateChoice = { sha: d.sha256, id: v.id }; this.draw(true); } },
          h('span', { class: 'box' }), h('span', { class: 'plate' }, familyCode(d.family)),
          h('span', { class: 'main' }, v.name, h('small', null, day(v.published_at))),
          h('span', { class: 'end' }, v.bytes ? formatBytes(v.bytes) : '')))),
        h('p', { class: 'upd-note' }, t('dos.update_note')),
        h('div', { class: 'upd-actions' },
          h('button', { class: 'btn btn-solid', type: 'button', onclick: () => this.ctx.openLink && this.ctx.openLink(pick.url) },
            icon('i-download'), t('dos.update_download')),
          h('button', { class: 'btn btn-line', type: 'button', onclick: () => this.setUpdates({ ignore: n.ids }) }, t('dos.update_ignore'))),
        n.description ? this.fold(t('dos.update_about'), inLanguage(safeFragment(n.description), currentLang())) : null);
    } else if (!u.muted) {
      parts.push(h('p', { class: 'dos-none' }, t('dos.update_none')));
    }
    if (!u.muted && u.entries.length) {
      const rows = u.entries.map((e) => h('div', { class: 'entry' },
        h('span', { class: 'e-name' }, e.name, h('small', null, [e.base_model, day(e.published_at),
          e.also.length ? t('dos.entry_also', { list: e.also.join(' & ') }) : null].filter(Boolean).join(' · '))),
        h('button', { class: 'link-btn', type: 'button', onclick: () => this.ctx.openLink && this.ctx.openLink(e.url) }, t('dos.entry_download'))));
      if (u.entries_total > u.entries.length) rows.push(h('p', { class: 'ent-more' }, t('dos.entries_more', { n: u.entries_total - u.entries.length })));
      parts.push(this.fold(t('dos.entries', { n: u.entries_total }), [h('p', { class: 'ent-note' }, t('dos.entries_note')), ...rows]));
    }
    parts.push(h('p', { class: 'upd-mute' }, h('label', { class: 'opt-check' },
      h('input', { type: 'checkbox', checked: Boolean(u.muted), onchange: (e) => this.setUpdates({ muted: e.target.checked }) }),
      t('dos.update_mute'))));
    return h('section', { class: 'upd' }, parts);
  }

  async setUpdates(body) {
    const sha = this.detail && this.detail.sha256;
    if (!sha) return;
    try {
      const detail = await api('PUT', '/api/ui/models/' + sha + '/updates', body);
      if (this.detail && this.detail.sha256 === sha) {
        this.detail = detail;
        this.draw(true);
      }
      if (this.ctx.onChanged) this.ctx.onChanged(sha);
    } catch (e) {
      this.actionNote = { sha, text: e.message, bad: true };
      this.draw(true);
    }
  }

  drawPageLine() {
    const d = this.detail;
    const page = d.page || { state: 'none' };
    if (page.state === 'none') return null;
    const link = (key, refresh) => h('button', { class: 'link-btn', type: 'button', onclick: () => this.readPage(refresh, true) }, t(key));
    if (this.pageReading === d.sha256) return h('span', { class: 'reading', role: 'status' }, t('dos.page_reading'));
    if (this.pageError && this.pageError.sha === d.sha256) {
      return h('span', { class: 'bad', role: 'status' }, t('dos.page_failed', { reason: this.pageError.message }), ' ',
        link('dos.retry', page.state === 'ready'));
    }
    if (page.state === 'ready') return h('span', null, t('dos.page_read_at', { date: shortDate(page.fetched_at) }), ' ', link('dos.page_reread', true));
    if (page.state === 'not_found') return h('span', null, t('dos.page_gone'));
    return link('dos.page_read', false);
  }

  // ---- what the example was made with ---------------------------------------------------------------------------
  drawParams(sample) {
    const d = this.detail;
    const items = this.items();
    const p = (sample && sample.params) || null;
    const size = sizeOf(p, sample);
    const cell = (label, value, wide, seed) => {
      const shown = plain(value);
      const empty = shown === null || shown === '';
      return h('div', { class: wide ? 'wide' : seed && !empty && String(shown).length > 9 ? 'long' : null }, h('dt', null, label),
        seed && !empty ? h('dd', null, this.seedCopy(shown))
          : h('dd', { title: empty ? '' : String(shown) }, empty ? '·' : String(shown)));
    };
    const cells = p
      ? [cell(t('dos.p_sampler'), p.sampler), cell(t('dos.p_steps'), p.steps), cell(t('dos.p_cfg'), plain(p.cfgScale) !== null ? p.cfgScale : p.cfg),
        cell(t('dos.p_size'), size), cell(t('dos.p_seed'), p.seed, false, true), cell(t('dos.p_file'), formatBytes(d.bytes))]
      : [cell(t('dos.p_file'), formatBytes(d.bytes)), cell(t('dos.p_size'), size, true)];
    return h('section', null,
      h('h3', { class: 'dos-label' }, t('dos.params'), items.length > 1 ? h('small', null, (this.index + 1) + ' / ' + items.length) : null),
      h('dl', { class: 'dos-grid' }, cells),
      p ? null : h('p', { class: 'dos-none', style: 'margin-top:8px' }, sample ? t('dos.params_none') : t('dos.samples_none')));
  }

  // The prompt and the negative prompt, each with its own copy button.
  drawPrompt(p) {
    return this.fold(t('dos.prompt'), [this.textBlock('dos.prompt', p.prompt),
      plain(p.negativePrompt) ? this.textBlock('dos.negative', p.negativePrompt) : null]);
  }

  textBlock(label, value) {
    return h('div', { class: 'card-text' },
      h('div', { class: 'card-text-head' }, h('span', { class: 'label' }, t(label)), this.copyButton(String(value))),
      h('p', { class: 'prompt' }, String(value)));
  }

  // A seed is copied by a click on it.
  seedCopy(value) {
    const text = String(value);
    const span = h('span', null, text);
    const button = h('button', { type: 'button', class: 'cell-copy', title: t('dos.copy_seed'), 'aria-label': t('dos.copy_seed') + ' ' + text },
      span, icon('i-copy'));
    let copied = null;
    button.addEventListener('click', async () => {
      if (await copyText(text)) {
        span.textContent = t('dos.copied');
        readout(span, 0, { duration: 200 });
        clearTimeout(copied);
        copied = setTimeout(() => { copied = null; span.textContent = text; }, 1600);
      }
    });
    return button;
  }

  // ---- trigger words, and the way out --------------------------------------------------------------------------
  // The trigger words in the author's order, the optional ones (an outfit, a pose) marked. When there are both kinds,
  // the one-click copy takes the required words only, or all of them with "include optional" ticked; the choice is
  // remembered, and the words it leaves out are greyed (the user's choice of the drafts, 2026-10-02).
  drawTriggers() {
    const d = this.detail;
    const card = d.card || {};
    const fromCard = card.values && card.values.trigger_words;
    const words = fromCard ? fromCard.map((w) => ({ text: w.text, optional: w.required === false }))
      : (d.trigger_words || []).map((text) => ({ text, optional: false }));
    const source = fromCard && card.sources ? card.sources.trigger_words : null;
    const required = words.filter((w) => !w.optional);
    const mixed = required.length > 0 && required.length < words.length;
    let all = mixed && recall(KEY_OPTIONAL, '0') === '1';
    let tools = null;
    if (mixed) {
      const copy = this.copyButton(() => (all ? words : required).map((w) => w.text).join(', '),
        () => (all ? t('dos.copy_all_n', { n: words.length }) : t('dos.copy_required', { n: required.length })), 'copy-all');
      const box = h('input', { type: 'checkbox', checked: all });
      box.addEventListener('change', () => {
        all = box.checked;
        remember(KEY_OPTIONAL, all ? '1' : '0');
        section.classList.toggle('with-optional', all);
        copy.relabel();
      });
      tools = h('div', { class: 'trig-tools' }, h('label', { class: 'opt-check' }, box, t('dos.include_optional')), copy);
    } else if (words.length > 1) {
      tools = this.copyButton(words.map((w) => w.text).join(', '), 'dos.copy_all', 'copy-all');
    }
    const section = h('section', { class: 'triggers' + (mixed && !all ? '' : ' with-optional') },
      h('div', { class: 'dos-label-row' },
        h('h3', { class: 'dos-label' }, t('dos.triggers'), source && source !== 'civitai' ? h('span', { class: 'src' }, t(SOURCE_LABEL[source])) : null),
        tools),
      words.length
        ? words.map((w) => h('div', { class: 'trigger' + (w.optional ? ' optional' : '') }, h('span', null, w.text),
          w.optional ? h('em', { class: 'opt' }, t('dos.optional')) : null, this.copyButton(w.text)))
        : h('p', { class: 'dos-none' }, t('dos.triggers_none')));
    return section;
  }

  // A button that copies `text` (or what the function gives at the click). Its label is a string key or a function;
  // relabel() writes it again after the state behind it changed.
  copyButton(text, label = 'dos.copy', extra = '') {
    const labelText = () => (typeof label === 'function' ? label() : t(label));
    const span = h('span', null, labelText());
    const button = h('button', { type: 'button', class: 'copy-btn' + (extra ? ' ' + extra : '') }, icon('i-copy'), span);
    let copied = null;
    button.addEventListener('click', async () => {
      if (await copyText(typeof text === 'function' ? text() : text)) {
        span.textContent = t('dos.copied');
        readout(span, 0, { duration: 200 });
        clearTimeout(copied);
        copied = setTimeout(() => { copied = null; span.textContent = labelText(); }, 1600);
      }
    });
    button.relabel = () => { if (!copied) span.textContent = labelText(); };
    return button;
  }

  // ---- what the training wrote into a model civitai does not know, and the notes ------------------------------------
  drawTraining() {
    const tr = this.detail.training;
    if (!tr) return null;
    const cell = (key, value, cls) => (value ? h('div', { class: cls || null }, h('dt', null, t(key)), h('dd', { title: String(value) }, String(value))) : null);
    const epochs = tr.epoch && tr.epochs ? tr.epoch + ' / ' + tr.epochs : tr.epoch || tr.epochs;
    const network = tr.network_dim && tr.network_alpha ? cell('dos.tr_network', tr.network_dim + ' / ' + tr.network_alpha)
      : cell('dos.tr_network_dim', tr.network_dim);
    const cells = [cell('dos.tr_base_checkpoint', tr.base_checkpoint ? fileName(tr.base_checkpoint) : null, 'wide long'),
      cell('dos.tr_tool', tr.tool, 'long'), cell('dos.tr_epochs', epochs), cell('dos.tr_steps', tr.steps),
      cell('dos.tr_resolution', tr.resolution, 'long'), cell('dos.tr_images', tr.images), network].filter(Boolean);
    if (!cells.length) return null;
    return h('section', null, h('h3', { class: 'dos-label' }, t('dos.training')), h('dl', { class: 'dos-grid' }, cells));
  }

  drawNotes() {
    const d = this.detail;
    const card = d.card || {};
    if (!card.kind) return null;
    const notes = card.values && card.values.notes;
    if (!notes && d.identified_by) return null;       // a model civitai knows has its author's notes below
    const source = card.sources && card.sources.notes;
    return h('section', { class: 'dos-notes' },
      h('h3', { class: 'dos-label' }, t('dos.cf_notes'), source && source !== 'user' ? h('span', { class: 'src' }, t(SOURCE_LABEL[source])) : null),
      notes ? h('p', { class: 'notes-text' }, notes) : h('p', { class: 'dos-none' }, t('dos.notes_none')));
  }

  drawActions() {
    const d = this.detail;
    const note = this.actionNote && this.actionNote.sha === d.sha256 ? this.actionNote : null;
    const message = h('p', { class: 'dos-msg' + (note && note.bad ? ' bad' : ''), role: 'status' }, note ? note.text : '');
    this.actionNote = null;
    const buttons = [h('button', { class: 'btn ' + (d.civitai ? 'btn-line' : 'btn-solid'), type: 'button', onclick: () => this.startEdit() },
      icon('i-edit'), t('dos.edit'))];
    if (d.civitai) {
      buttons.push(h('a', { class: 'btn btn-solid', href: d.civitai.url, target: '_blank', rel: 'noopener noreferrer' },
        icon('i-external'), t('dos.open_civitai')));
    }
    if (this.ctx.state.local && d.files && d.files.length) {
      buttons.push(h('button', { class: 'btn btn-line', type: 'button', onclick: async () => {
        message.className = 'dos-msg';
        message.textContent = '';
        try {
          await api('POST', '/api/ui/open-folder', { sha256: d.sha256, path: d.files[0].path });
        } catch (e) {
          message.className = 'dos-msg bad';
          message.textContent = e.message;
          readout(message, 0, { duration: 240 });
        }
      } }, icon('i-folder'), t('dos.open_folder')));
    }
    if (d.files && d.files.length) {
      buttons.push(h('button', { class: 'btn btn-line', type: 'button', onclick: () => this.confirmTrash(message) },
        icon('i-trash'), t('dos.trash')));
    }
    if (note) setTimeout(() => readout(message, 0, { duration: 240 }), 0);
    return h('div', null, h('div', { class: 'dos-actions' }, buttons), message);
  }

  // ---- editing the details: the whole dossier as one form -----------------------------------------------------------
  startEdit() {
    const d = this.detail;
    const card = d.card || {};
    const v = card.values || {};
    const values = { notes: toInput(v.notes) };
    for (const spec of EDIT_GRID[card.kind] || []) for (const field of spec.pair || [spec.field]) values[field] = toInput(v[field]);
    for (const field of EDIT_TEXTS[card.kind] || []) values[field] = toInput(v[field]);
    this.editing = { sha: d.sha256, title: d.title || '', base_model: d.base_model || '', values,
      triggers: triggerList(v.trigger_words), error: null, saving: false };
    this.root.classList.add('editing');
    this.draw(true);
    const name = this.root.querySelector('.ed-head input');
    if (name) {
      this.root.scrollTop = Math.max(0, name.closest('.ed-head').offsetTop - 24);
      name.focus({ preventScroll: true });
    }
  }

  endEdit() {
    this.editing = null;
    this.root.classList.remove('editing');
  }

  cancelEdit() {
    this.endEdit();
    this.draw(true);
  }

  drawEditing(keepScroll) {
    const d = this.detail;
    const e = this.editing;
    const card = d.card || {};
    const items = this.items();
    const grid = (EDIT_GRID[card.kind] || []).map((spec) => this.editCardCell(spec, card));
    return h('div', { class: 'dossier-body' + (keepScroll ? ' still' : '') },
      this.drawHero(),
      items.length || d.cover ? this.drawThumbs() : null,
      this.mediaNote(),
      this.drawEditHead(),
      card.kind ? h('section', { class: 'dos-cardsec' }, h('h3', { class: 'dos-label' }, t('dos.card')),
        grid.length ? h('div', { class: 'dos-grid dos-card ed-grid' }, grid) : null,
        (EDIT_TEXTS[card.kind] || []).map((field) => this.editTextBlock(field, card, 3))) : null,
      card.kind === 'lora' ? this.drawEditTriggers(card) : null,
      card.kind ? h('section', null, h('h3', { class: 'dos-label' }, t('dos.cf_notes')), this.editTextBlock('notes', card, 4, true)) : null,
      e.error ? h('p', { class: 'dos-msg bad ed-error', role: 'alert' }, e.error) : null,
      h('div', { class: 'edit-bar' }, h('span', { class: 't' }, t('dos.editing')),
        h('button', { class: 'btn btn-line', type: 'button', onclick: () => this.cancelEdit() }, t('common.cancel')),
        h('button', { class: 'btn btn-solid', type: 'button', disabled: e.saving, onclick: () => this.saveEdit() }, t('dos.save'))));
  }

  // The line under a field: where its suggested value comes from or, once the field differs from it, "Edited" and the
  // way back. `state`: {changed(), source() (a string key or null), restoreText(), restore()}.
  metaLine(state) {
    const meta = h('div', { class: 'ed-meta' });
    const update = () => {
      if (state.changed()) {
        fill(meta, h('span', { class: 'edited' }, t('dos.src_user')),
          h('button', { class: 'link-btn', type: 'button', onclick: () => { state.restore(); update(); } }, state.restoreText()));
      } else {
        const source = state.source();
        fill(meta, source ? h('span', null, t(source)) : null);
      }
    };
    meta.update = update;
    update();
    return meta;
  }

  // A field's control, bound to the form's state: what is typed is kept there at once.
  control(kind, field, label) {
    const e = this.editing;
    let node;
    if (kind === 'sampler' || kind === 'scheduler' || kind === 'use') {
      const names = kind === 'use' ? ['positive', 'negative']
        : (this.ctx.state && this.ctx.state[kind === 'sampler' ? 'samplers' : 'schedulers']) || [];
      const current = e.values[field];
      const list = current && !names.includes(current) ? [current, ...names] : names;
      const name = (n) => (kind === 'use' ? t(n === 'negative' ? 'dos.cf_use_negative' : 'dos.cf_use_positive') : n);
      node = h('select', { class: 'ed-input ed-select' }, h('option', { value: '' }, t('dos.unset')), list.map((n) => h('option', { value: n }, name(n))));
      node.value = current;
    } else {
      const numeric = NUMBER_FIELDS.has(field);
      node = h('input', { class: 'ed-input', type: 'text', inputmode: numeric ? 'decimal' : null, placeholder: t('dos.unset'),
        maxLength: numeric ? 12 : 2000, spellcheck: false });
      node.value = e.values[field];
    }
    node.setAttribute('aria-label', label);
    const keep = () => { e.values[field] = node.value; };
    node.addEventListener('input', keep);
    node.addEventListener('change', keep);
    return node;
  }

  editCardCell(spec, card) {
    const e = this.editing;
    const fields = spec.pair || [spec.field];
    const label = t(CARD_LABEL[spec.label || spec.field]);
    const sug = fields.map((field) => suggestionOf(card, field));
    const controls = fields.map((field) => this.control(spec.control, field, label));
    const meta = this.metaLine({
      changed: () => fields.some((field, i) => !same(fromInput(field, e.values[field]), sug[i].value)),
      source: () => (sug[0].source ? SOURCE_LABEL[sug[0].source] : null),
      restoreText: () => (sug.some((s) => s.value !== null)
        ? t('dos.restore_suggested', { value: shorten(sug.map((s) => toInput(s.value)).join(spec.join || '')) }) : t('dos.undo_edit')),
      restore: () => fields.forEach((field, i) => { e.values[field] = toInput(sug[i].value); controls[i].value = e.values[field]; }),
    });
    for (const c of controls) { c.addEventListener('input', meta.update); c.addEventListener('change', meta.update); }
    const joined = controls.length > 1 ? h('div', { class: 'ed-two' }, controls[0], h('span', { 'aria-hidden': 'true' }, spec.join.trim()), controls[1]) : controls[0];
    return h('div', { class: spec.wide ? 'wide' : null }, h('span', { class: 'ed-cap' }, label), joined, meta);
  }

  editTextBlock(field, card, rows, bare) {
    const e = this.editing;
    const label = t(CARD_LABEL[field]);
    const sug = suggestionOf(card, field);
    const area = h('textarea', { class: 'ed-area', rows, maxLength: 8000, placeholder: field === 'notes' ? t('dos.notes_hint') : t('dos.unset'), spellcheck: false });
    area.value = e.values[field];
    area.setAttribute('aria-label', label);
    area.addEventListener('input', () => { e.values[field] = area.value; });
    const meta = this.metaLine({
      changed: () => !same(fromInput(field, e.values[field]), sug.value),
      source: () => (sug.source ? SOURCE_LABEL[sug.source] : null),
      restoreText: () => (sug.value !== null ? t('dos.restore_suggested', { value: shorten(sug.value) }) : t('dos.undo_edit')),
      restore: () => { e.values[field] = toInput(sug.value); area.value = e.values[field]; },
    });
    area.addEventListener('input', meta.update);
    return h('div', { class: 'ed-block' }, bare ? null : h('span', { class: 'ed-cap' }, label), area, meta);
  }

  // The name, the type (decided by the folder the file is in, so shown, not edited) and the base model.
  drawEditHead() {
    const d = this.detail;
    const e = this.editing;
    const original = d.original || { title: d.title, base_model: null };
    const name = h('input', { class: 'ed-input ed-big', type: 'text', maxLength: 200, spellcheck: false });
    name.value = e.title;
    name.setAttribute('aria-label', t('dos.f_name'));
    name.addEventListener('input', () => { e.title = name.value; });
    const nameMeta = this.metaLine({
      changed: () => e.title.trim() !== (original.title || ''),
      source: () => null,
      restoreText: () => t('dos.restore_original', { value: shorten(original.title) }),
      restore: () => { e.title = original.title || ''; name.value = e.title; },
    });
    name.addEventListener('input', nameMeta.update);
    const base = h('input', { class: 'ed-input', type: 'text', maxLength: 200, list: 'dos-base-models', placeholder: t('dos.unset'), spellcheck: false });
    base.value = e.base_model;
    base.setAttribute('aria-label', t('dos.f_base_model'));
    base.addEventListener('input', () => { e.base_model = base.value; });
    const baseMeta = this.metaLine({
      changed: () => e.base_model.trim() !== (original.base_model || ''),
      source: () => null,
      restoreText: () => (original.base_model ? t('dos.restore_original', { value: shorten(original.base_model) }) : t('dos.undo_edit')),
      restore: () => { e.base_model = original.base_model || ''; base.value = e.base_model; },
    });
    base.addEventListener('input', baseMeta.update);
    const choices = this.ctx.baseModels ? this.ctx.baseModels() : [];
    return h('div', { class: 'ed-head' },
      h('span', { class: 'ed-cap' }, t('dos.f_name')), name, nameMeta,
      h('div', { class: 'ed-pair' },
        h('div', null, h('span', { class: 'ed-cap' }, t('dos.f_kind')), h('div', { class: 'ed-input ed-static' }, t('kind.' + d.kind))),
        h('div', null, h('span', { class: 'ed-cap' }, t('dos.f_base_model')), base, baseMeta)),
      h('datalist', { id: 'dos-base-models' }, choices.map((b) => h('option', { value: b }))));
  }

  // The trigger words as rows to change, mark optional, take out or add to.
  drawEditTriggers(card) {
    const e = this.editing;
    const sug = suggestionOf(card, 'trigger_words');
    const suggested = triggerList(sug.value);
    const rows = h('div', { class: 'ed-rows' });
    const meta = this.metaLine({
      changed: () => !same(triggerList(e.triggers), suggested),
      source: () => (sug.source ? SOURCE_LABEL[sug.source] : null),
      restoreText: () => (suggested.length ? t('dos.restore_suggested', { value: shorten(suggested.map((w) => w.text).join(', ')) }) : t('dos.undo_edit')),
      restore: () => { e.triggers = suggested.map((w) => ({ ...w })); draw(); },
    });
    const draw = (focusLast) => {
      fill(rows, e.triggers.map((w, i) => {
        const word = h('input', { class: 'ed-word', type: 'text', maxLength: 500, placeholder: t('dos.cf_trigger'), spellcheck: false });
        word.value = w.text;
        word.setAttribute('aria-label', t('dos.cf_trigger'));
        word.addEventListener('input', () => { w.text = word.value; meta.update(); });
        const box = h('input', { type: 'checkbox', checked: !w.required });
        box.addEventListener('change', () => { w.required = !box.checked; meta.update(); });
        return h('div', { class: 'trigger edit' }, word, h('label', { class: 'opt-check' }, box, t('dos.optional')),
          h('button', { type: 'button', title: t('dos.trigger_remove'), 'aria-label': t('dos.trigger_remove'),
            onclick: () => { e.triggers.splice(i, 1); draw(); meta.update(); } }, icon('i-x')));
      }));
      if (focusLast) {
        const words = rows.querySelectorAll('.ed-word');
        if (words.length) words[words.length - 1].focus();
      }
    };
    draw();
    return h('section', { class: 'ed-triggers' },
      h('h3', { class: 'dos-label' }, t('dos.triggers')), rows, meta,
      h('button', { class: 'btn btn-line btn-small ed-add', type: 'button',
        onclick: () => { e.triggers.push({ text: '', required: true }); draw(true); meta.update(); } }, icon('i-plus'), t('dos.trigger_add')));
  }

  // Save what the form holds: the name and the base model, and every field of the card (a field that is the
  // suggestion again, or empty, gives the user's value up).
  async saveEdit() {
    const d = this.detail;
    const e = this.editing;
    if (!e || e.saving) return;
    const sha = d.sha256;
    const card = d.card || {};
    const values = {};
    let bad = null;
    if (card.kind) {
      const fields = [...(EDIT_GRID[card.kind] || []).flatMap((s) => s.pair || [s.field]), ...(EDIT_TEXTS[card.kind] || []), 'notes'];
      for (const field of fields) {
        const value = fromInput(field, e.values[field]);
        if (Number.isNaN(value)) { bad = bad || field; continue; }
        values[field] = value === null || same(value, suggestionOf(card, field).value) ? null : value;
      }
      if (card.kind === 'lora') {
        const list = triggerList(e.triggers);
        values.trigger_words = !list.length || same(list, triggerList(suggestionOf(card, 'trigger_words').value)) ? null : list;
      }
    }
    if (bad) {
      e.error = t('dos.bad_value', { field: t(CARD_LABEL[bad]) });
      this.draw(true);
      return;
    }
    const user = (card.layers && card.layers.user) || {};
    const cardChanged = Object.entries(values).some(([field, value]) => !same(value, user[field] === undefined ? null : user[field]));
    const original = d.original || {};
    const title = e.title.trim();
    const base = e.base_model.trim();
    const wantTitle = title && title !== original.title ? title : null;
    const wantBase = base && base !== (original.base_model || '') ? base : null;
    const info = {};
    if (wantTitle !== ((d.user && d.user.title) || null)) info.title = wantTitle;
    if (wantBase !== ((d.user && d.user.base_model) || null)) info.base_model = wantBase;
    e.saving = true;
    e.error = null;
    this.root.querySelectorAll('.edit-bar .btn-solid').forEach((b) => { b.disabled = true; });
    try {
      if (Object.keys(info).length) await api('PUT', '/api/ui/models/' + sha + '/info', info);
      if (cardChanged) await api('PUT', '/api/v1/models/' + sha + '/card', { values });
      const detail = await api('GET', '/api/v1/models/' + sha);
      if (this.ctx.onChanged) this.ctx.onChanged(sha, false);
      if (!this.detail || this.detail.sha256 !== sha || this.editing !== e) return;
      this.detail = detail;
      this.endEdit();
      this.actionNote = { sha, text: t('dos.saved'), bad: false };
      this.draw(true);
    } catch (err) {
      e.saving = false;
      const field = err.data && err.data.field;
      e.error = field && CARD_LABEL[field] ? t('dos.bad_value', { field: t(CARD_LABEL[field]) }) : err.message;
      if (this.editing === e) this.draw(true);
    }
  }

  // ---- deleting: what goes to the recycle bin is listed first, file by file ------------------------------------------
  async confirmTrash(message) {
    const d = this.detail;
    let plan;
    try {
      plan = (await api('GET', '/api/ui/models/' + d.sha256 + '/trash')).files;
    } catch (e) {
      message.className = 'dos-msg bad';
      message.textContent = e.message;
      readout(message, 0, { duration: 240 });
      return;
    }
    if (!this.trashDialog) {
      this.trashDialog = h('dialog', { class: 'confirm', 'aria-labelledby': 'confirm-title' });
      document.body.append(this.trashDialog);
    }
    const dialog = this.trashDialog;
    const many = plan.length > 1;
    const chosen = new Set(plan.filter((f) => f.inside && f.recycle_bin).map((f) => f.path));
    const note = h('p', { class: 'confirm-msg', role: 'status' });
    const go = h('button', { class: 'btn btn-solid', type: 'button' }, icon('i-trash'), t('dos.trash_confirm'));
    const sync = () => { go.disabled = !chosen.size; };
    const list = plan.map((f) => {
      const blocked = !f.inside ? t('dos.trash_outside') : !f.recycle_bin ? t('dos.trash_no_bin') : null;
      const box = many ? h('input', { type: 'checkbox', checked: chosen.has(f.path), disabled: Boolean(blocked),
        onchange: (e) => { if (e.target.checked) chosen.add(f.path); else chosen.delete(f.path); sync(); } }) : null;
      const beside = f.sidecars.map(fileName).join(currentLang() === 'zh' ? '、' : ', ');
      return h('li', { class: blocked ? 'off' : null }, h(many ? 'label' : 'div', { class: 'confirm-file' }, box,
        h('span', { class: 'what' }, h('span', { class: 'path' }, f.path), h('small', { class: 'size' }, formatBytes(f.bytes)),
          beside ? h('small', null, t('dos.trash_beside', { list: beside })) : null,
          blocked ? h('small', { class: 'bad' }, blocked) : null)));
    });
    go.onclick = async () => {
      go.disabled = true;
      note.className = 'confirm-msg';
      note.textContent = '';
      try {
        await api('POST', '/api/ui/models/' + d.sha256 + '/trash', { paths: [...chosen] });
        dialog.close();
        if (this.ctx.onRemoved) this.ctx.onRemoved(d.title + (d.version_name ? ' · ' + d.version_name : ''), d.sha256);
      } catch (e) {
        note.className = 'confirm-msg bad';
        note.textContent = e.message;
        sync();
      }
    };
    fill(dialog,
      h('h2', { id: 'confirm-title' }, t('dos.trash_title')),
      h('p', { class: 'confirm-name' }, d.title + (d.version_name ? ' · ' + d.version_name : '')),
      h('p', { class: 'confirm-lead' }, t('dos.trash_lead')),
      many ? h('p', { class: 'confirm-lead' }, t('dos.trash_copies', { n: plan.length })) : null,
      h('ul', { class: 'confirm-files' }, list),
      note,
      h('div', { class: 'confirm-actions' },
        h('button', { class: 'btn btn-line', type: 'button', onclick: () => dialog.close() }, t('common.cancel')), go));
    sync();
    dialog.showModal();
  }

  // ---- files and notes -----------------------------------------------------------------------------------------
  drawFiles() {
    const d = this.detail;
    const checks = { match: t('dos.check_match'), mismatch: t('dos.check_mismatch'), unknown: t('dos.check_unknown') };
    const items = (d.files || []).map((f) => h('div', { class: 'file' },
      h('div', { class: 'path' }, f.path),
      h('div', { class: 'facts' },
        h('span', null, formatBytes(f.bytes)),
        h('span', { class: f.check === 'mismatch' ? 'warn' : null }, checks[f.check] || checks.unknown),
        f.shadowed ? h('span', { class: 'warn' }, t('dos.shadowed')) : null)));
    if (!d.identified_by && d.bundled_in && d.bundled_in.count) items.unshift(h('p', { class: 'dos-none', style: 'margin:0 0 8px' }, t('dos.bundled', { n: d.bundled_in.count })));
    return this.fold(t('dos.files') + ' · ' + (d.files || []).length, items, true);
  }

  fold(title, content, open) {
    return h('details', { class: 'fold', open: Boolean(open) }, h('summary', null, title), h('div', { class: 'body' }, content));
  }
}
