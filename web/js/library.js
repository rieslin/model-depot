// The model library: the roster grouped by base model, each group opened by a black block; the index of base models
// down the left, whose red marker follows the group in view; the search and the eye switch for rated covers in the
// top bar; the type tabs in the bar over the roster; and the dossier of the selected model on the right.
import { api } from './api.js';
import { h, fill, clear, formatBytes } from './dom.js';
import { t, currentLang } from './i18n.js';
import { familyCode, familyName, familyArch } from './families.js';
import { mosaicOf, loadImage } from './mosaic.js';
import { Dossier } from './dossier.js';
import { MASK_FROM, levelName, icon, baseName, recall, remember, titleOf } from './ui.js';
import { decode, snap, onScreen, pace } from './motion.js';

// What the library lists: the models one picks to make an image, under four tabs. A diffusion model (the model alone,
// without its text encoder and VAE, as Flux and Qwen come) is a Checkpoint here, though its card still says which it
// is. Text encoders, upscalers and ControlNet models are listed in the inventory only; files of other kinds nowhere.
const TAB_OF = { checkpoint: 'checkpoint', diffusion_model: 'checkpoint', lora: 'lora', embedding: 'embedding', vae: 'vae' };
const TAB_ORDER = ['checkpoint', 'lora', 'embedding', 'vae'];
const KIND_ORDER = ['checkpoint', 'diffusion_model', 'lora', 'embedding', 'vae'];
const KEYS = { kind: 'md.kind', mask: 'md.mask', foldersKept: 'md.folders_kept', folds: 'md.folds' };
const askedFolders = new Set();     // missing folders already put to the user in this visit
let askedInbox = false;             // the inbox folders are looked at once a visit (the page opened or reloaded)
const CARD_ASPECT = 5 / 8;

// Where a type tab's red bar is drawn, in the tab row's own coordinates (the bar is the tab's ::after).
function inkBox(tab) {
  const s = getComputedStyle(tab, '::after');
  const left = parseFloat(s.left) || 0;
  const right = parseFloat(s.right) || 0;
  return { left: tab.offsetLeft + left, width: tab.offsetWidth - left - right, bottom: parseFloat(s.bottom) || 0 };
}

const CJK = /[㐀-鿿豈-﫿]/;

// A run of text that holds Chinese is set apart, so the tracked capitals around it do not space its characters out.
function cjk(text) {
  return CJK.test(text) ? h('span', { class: 'cjk' }, text) : text;
}

// One card a file (copies of one file, the same fingerprint, are one card). The versions of one civitai page are laid
// out together under its name: authors often gather different models under one page, a set of costumes for one, so
// they are not stacked into one card (the user's choice of the drafts, 2026-10-07).
function stackKey(m) {
  return m.civitai ? 'm' + m.civitai.model_id : 's' + (m.sha256 || m.comfy_name);
}

function stackUp(models) {
  const stacks = new Map();
  for (const m of models) {
    const key = stackKey(m);
    let stack = stacks.get(key);
    if (!stack) { stack = { key, versions: [] }; stacks.set(key, stack); }
    if (!m.sha256 || !stack.versions.some((v) => v.sha256 === m.sha256)) stack.versions.push(m);
  }
  for (const s of stacks.values()) {
    // the newest version leads: civitai numbers versions in the order they were published
    s.versions.sort((a, b) => ((b.civitai && b.civitai.version_id) || 0) - ((a.civitai && a.civitai.version_id) || 0));
    s.lead = s.versions[0];
  }
  return [...stacks.values()];
}

// How many cards a list of models makes, all told and by tab.
function cardCounts(models) {
  const keys = { '': new Set() };
  for (const m of models) {
    const key = (m.family || 'unknown') + '|' + (m.sha256 || m.comfy_name);
    keys[''].add(key);
    const tab = TAB_OF[m.kind];
    (keys[tab] || (keys[tab] = new Set())).add(key);
  }
  return Object.fromEntries(Object.entries(keys).map(([k, v]) => [k, v.size]));
}

function haystack(m) {
  return [m.title, m.version_name, m.comfy_name, m.base_model, familyName(m.family), familyCode(m.family),
    (m.trigger_words || []).join(' ')].join(' ').toLowerCase();
}

export class Library {
  constructor(shell, tools, nav, ctx) {
    this.shell = shell;
    this.tools = tools;
    this.nav = nav;
    this.ctx = ctx;
    this.models = [];
    this.revision = -1;
    this.query = '';
    this.kind = TAB_ORDER.includes(recall(KEYS.kind, '')) ? recall(KEYS.kind, '') : '';
    this.onlyNew = false;           // only the models with a new version on civitai
    this.updatesRunning = null;     // whether a check for new versions was under way at the last look
    this.mask = recall(KEYS.mask, '1') === '1';
    // the groups ("g:<family>") and sets ("s:<stack key>") folded away, as the user left them
    try { this.folds = new Set(JSON.parse(recall(KEYS.folds, '[]'))); } catch (e) { this.folds = new Set(); }
    this.selected = null;           // the model the dossier shows
    this.picked = new Set();        // the models selected; two or more put the selection's actions in the dossier's place
    this.anchor = null;             // where a Shift-click or Shift-arrow run starts
    this.cursor = null;             // the card the arrow keys move from
    this.swept = false;             // a box was just drawn: the click its release fires is not a click on a card
    this.onKeys = (e) => this.keys(e);
    this.coverStamps = new Map();   // models whose cover changed in this visit: the browser fetches theirs again
    this.current = null;            // the group in view
    this.groups = [];
    this.alive = true;
    this.timer = null;
    this.onScroll = () => this.scheduleSpy();
    this.onResize = () => { this.scheduleSpy(); this.markOverflow(); };
    this.onFocus = () => this.rescan();
    this.working = h('span', { class: 'working', hidden: true, role: 'status' });
  }

  // ---- building ------------------------------------------------------------------------------------------------
  mount(sha) {
    this.search = h('input', { type: 'search', placeholder: t('lib.search'), 'aria-label': t('lib.search_label'), autocomplete: 'off',
      spellcheck: false, oninput: () => { this.query = this.search.value; this.clearBtn.hidden = !this.query; this.render(true); } });
    this.clearBtn = h('button', { class: 'clear', type: 'button', title: t('lib.search_clear'), 'aria-label': t('lib.search_clear'), hidden: true,
      onclick: () => { this.search.value = ''; this.query = ''; this.clearBtn.hidden = true; this.render(true); this.search.focus(); } },
      icon('i-x'));
    fill(this.tools, h('label', { class: 'search' }, icon('i-search'), this.search, this.clearBtn));
    this.typeBar = h('div', { class: 'types', role: 'group', 'aria-label': t('lib.type_label'), onscroll: () => this.markOverflow() });
    // The eye for covers rated R and above, as civitai has it: an open eye while they show, a struck-through one while
    // they are hidden behind the mosaic. One switch, drawn twice: in the top bar, and in the bar over the roster on a
    // phone; CSS shows one of the two.
    const eyeButton = (where) => h('button', { class: 'eye-toggle ' + where, type: 'button', 'aria-label': t('lib.covers_hide'),
      onclick: () => this.setMask(!this.mask) });
    this.maskBtns = [eyeButton('in-top'), eyeButton('in-bar')];
    this.drawEyes();
    this.note = h('p', { class: 'roster-note', role: 'status', hidden: true });
    this.flash = h('p', { class: 'roster-flash', role: 'status', hidden: true });

    this.railList = h('ul', { class: 'rail-list', onscroll: () => this.placeMarker() });
    this.marker = h('span', { class: 'rail-marker', 'aria-hidden': 'true' });
    this.rail = h('nav', { class: 'rail', 'aria-label': t('lib.index_label') }, this.railList, this.marker);
    this.list = h('div', { class: 'roster-list' }, h('p', { class: 'empty' }, t('lib.loading')));
    this.tools.append(this.maskBtns[0]);
    this.foldAll = h('button', { class: 'fold-all', type: 'button', hidden: true, onclick: () => this.foldAllSets() });
    // the models civitai has a new version of, only: offered while there are any (the user's choice, 2026-10-14)
    this.onlyNewBtn = h('button', { class: 'fold-all only-new', type: 'button', hidden: true, onclick: () => this.setOnlyNew(!this.onlyNew) });
    this.bar = h('div', { class: 'roster-bar' }, this.typeBar, this.onlyNewBtn, this.foldAll, this.maskBtns[1]);
    this.roster = h('main', { class: 'roster', id: 'roster' }, this.bar, this.flash, this.note, this.list);
    this.dossierEl = h('aside', { class: 'dossier', 'aria-label': t('dos.samples') });
    this.selPane = h('aside', { class: 'dossier sel-pane open', 'aria-label': t('sel.title') });
    this.libEl = h('div', { class: 'lib' }, this.rail, this.roster, h('div', { class: 'lib-rule', 'aria-hidden': 'true' }), this.dossierEl, this.selPane);
    fill(this.shell, this.libEl);
    this.dossier = new Dossier(this.dossierEl, { ...this.ctx, mask: () => this.mask, onClose: () => { location.hash = '#/'; },
      onRemoved: (name, sha) => this.removed(name, sha), setOf: (sha) => this.setOf(sha),
      baseModels: () => this.baseModels(), onChanged: (sha, cover) => this.changed(sha, cover) });
    this.wireCards();

    // Wider than a phone, the roster scrolls in its own column (its scrollbar at its own right edge, the dossier's at
    // the dossier's); on a phone the page scrolls as a whole. Both are listened to, as the window can be resized.
    window.addEventListener('scroll', this.onScroll, { passive: true });
    this.roster.addEventListener('scroll', this.onScroll, { passive: true });
    window.addEventListener('resize', this.onResize);
    window.addEventListener('focus', this.onFocus);
    document.addEventListener('keydown', this.onKeys);
    this.pendingSelect = sha || null;
    this.rescan();
    this.refresh(true);
  }

  // Look at the model folders again when the library is opened or reloaded, and when the window comes back to the
  // front: files moved away in the file manager leave the list (their records stay, and come back with them), new
  // ones appear. Unchanged files are only looked at, not read, so this takes seconds.
  rescan() {
    const now = Date.now();
    if (now - (Library.lastRescan || 0) < 30000) return;
    Library.lastRescan = now;
    api('POST', '/api/v1/scan', {}).catch(() => {});
  }

  // Is the roster its own scrolling column (screens wider than a phone; see app.css)?
  ownScroll() {
    return !window.matchMedia('(max-width: 760px)').matches;
  }

  scrollY() {
    return this.ownScroll() ? this.roster.scrollTop : window.scrollY;
  }

  scrollToY(y) {
    if (this.ownScroll()) this.roster.scrollTop = y;
    else window.scrollTo({ top: y });
  }

  unmount() {
    this.alive = false;
    if (this.timer) clearTimeout(this.timer);
    window.removeEventListener('scroll', this.onScroll);
    window.removeEventListener('resize', this.onResize);
    window.removeEventListener('focus', this.onFocus);
    document.removeEventListener('keydown', this.onKeys);
    this.working.remove();
    clear(this.tools);
  }

  // ---- data ----------------------------------------------------------------------------------------------------
  async refresh(first) {
    if (!this.alive) return;
    if (this.timer) clearTimeout(this.timer);
    let busy = false;
    try {
      const progress = await api('GET', '/api/ui/progress');
      if (!this.alive) return;
      const p = progress.progress;
      busy = p.busy;
      const hash = p.hash;
      this.checkFolders(p.scan);
      if (first) this.checkInbox();
      this.updatesFinished(p.updates);
      this.working.hidden = !(p.busy && hash.done_files < hash.files);
      this.working.textContent = this.working.hidden ? '' : t('lib.working', { done: hash.done_files, total: hash.files });
      if (first || progress.revision !== this.revision) {
        const data = await api('GET', '/api/v1/models');
        if (!this.alive) return;
        this.models = data.models.filter((m) => TAB_OF[m.kind]);
        this.revision = data.revision;
        if (this.ctx.onCount) this.ctx.onCount(cardCounts(this.models)['']);
        this.render(false);
      }
    } catch (e) {
      if (first) fill(this.list, h('p', { class: 'empty' }, t('lib.load_failed', { error: e.message })));
    }
    if (this.alive) this.timer = setTimeout(() => this.refresh(false), busy ? 3000 : 8000);
  }

  // ---- drawing -------------------------------------------------------------------------------------------------
  matching() {
    const terms = this.query.toLowerCase().split(/\s+/).filter(Boolean);
    if (!terms.length) return this.models;
    return this.models.filter((m) => { const hay = haystack(m); return terms.every((term) => hay.includes(term)); });
  }

  render(userAction) {
    const scrollY = this.scrollY();
    const found = this.matching();
    this.drawTypes(found);
    const newer = found.filter((m) => m.has_update);
    if (this.onlyNew && !newer.length) this.onlyNew = false;
    this.drawOnlyNew(newer.length);
    const ofKind = this.kind ? found.filter((m) => TAB_OF[m.kind] === this.kind) : found;
    const shown = this.onlyNew ? ofKind.filter((m) => m.has_update) : ofKind;
    const byFamily = new Map();
    for (const m of shown) {
      const key = m.family || 'unknown';
      if (!byFamily.has(key)) byFamily.set(key, []);
      byFamily.get(key).push(m);
    }
    const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });
    this.groups = [...byFamily.entries()].map(([family, models]) => ({
      family,
      stacks: stackUp(models).sort((a, b) => (KIND_ORDER.indexOf(a.lead.kind) - KIND_ORDER.indexOf(b.lead.kind))
        || collator.compare(a.lead.title || '', b.lead.title || '')),
    }));
    for (const g of this.groups) g.n = g.stacks.reduce((n, s) => n + s.versions.length, 0);
    this.groups.sort((a, b) => (a.family === 'unknown') - (b.family === 'unknown') || b.n - a.n || a.family.localeCompare(b.family));
    this.stackOf = new Map();
    this.modelOf = new Map();
    for (const g of this.groups) for (const s of g.stacks) for (const m of s.versions) if (m.sha256) { this.stackOf.set(m.sha256, s); this.modelOf.set(m.sha256, m); }
    // a selection keeps what is still shown
    for (const sha of [...this.picked]) if (!this.modelOf.has(sha)) this.picked.delete(sha);

    this.note.hidden = !this.query;
    this.note.textContent = this.query ? t('lib.results', { n: this.groups.reduce((n, g) => n + g.n, 0) }) : '';
    if (!this.models.length) {
      fill(this.list, h('div', { class: 'empty' }, h('p', null, t('lib.empty_library')),
        h('a', { class: 'btn btn-line btn-small', href: '#/settings' }, t('nav.settings'))));
    } else if (!shown.length) {
      fill(this.list, h('div', { class: 'empty' }, h('p', null, t('lib.no_match')),
        h('button', { class: 'btn btn-line btn-small', type: 'button', onclick: () => this.clearFilters() }, t('lib.clear_filters'))));
    } else {
      fill(this.list, this.groups.map((g) => this.drawGroup(g)));
    }
    this.drawFoldAll();
    this.drawRail();
    this.scrollToY(userAction ? 0 : scrollY);
    this.renderPicks();
    this.scheduleSpy();

    if (this.pendingSelect !== null) {
      const sha = this.pendingSelect;
      this.pendingSelect = null;
      if (sha) this.select(sha, { scroll: true });
      else this.autoSelect();
    } else if (!this.selected) {
      this.autoSelect();
    }
  }

  autoSelect() {
    // On a wide screen the dossier is always in view: start it on the first model rather than empty.
    if (window.matchMedia('(max-width: 1180px)').matches) { this.dossier.show(null); return; }
    const lead = this.groups.length && this.groups[0].stacks.find((s) => s.lead.sha256);
    const first = lead && lead.lead;
    if (first) {
      history.replaceState(null, '', '#/m/' + first.sha256);
      this.select(first.sha256, { scroll: false });
    } else {
      this.dossier.show(null);
    }
  }

  clearFilters() {
    this.search.value = '';
    this.query = '';
    this.clearBtn.hidden = true;
    this.onlyNew = false;
    this.setKind('');
  }

  setOnlyNew(on) {
    this.onlyNew = on;
    this.render(true);
  }

  drawOnlyNew(n) {
    this.onlyNewBtn.hidden = !n && !this.onlyNew;
    this.onlyNewBtn.textContent = this.onlyNew ? t('lib.all_again') : t('lib.only_new', { n });
    this.onlyNewBtn.setAttribute('aria-pressed', String(this.onlyNew));
  }

  // A check for new versions (started on the inventory page) has ended: one line over the roster says what it found.
  updatesFinished(u) {
    if (!u) return;
    const was = this.updatesRunning;
    this.updatesRunning = u.running;
    if (was && !u.running && !u.error && !u.stopped) {
      const n = u.counts ? u.counts.newer : 0;
      this.flashText(n ? t('lib.updates_done', { n }) : t('lib.updates_done_none'));
    }
  }

  setKind(kind) {
    this.kind = kind;
    remember(KEYS.kind, kind);
    this.render(true);
  }

  drawTypes(found) {
    const counts = cardCounts(found);
    if (this.kind && !counts[this.kind] && !this.models.some((m) => TAB_OF[m.kind] === this.kind)) this.kind = '';
    const was = this.typeBar.querySelector('.type-tab[aria-pressed="true"]');
    const from = was ? { kind: was.dataset.kind, ...inkBox(was) } : null;
    const tab = (kind, label, n) => h('button', { class: 'type-tab', type: 'button', 'aria-pressed': String(this.kind === kind),
      'data-kind': kind, onclick: () => this.setKind(kind) }, label, h('span', { class: 'n' }, String(n)));
    const present = TAB_ORDER.filter((k) => this.models.some((m) => TAB_OF[m.kind] === k));
    fill(this.typeBar, tab('', t('lib.type_all'), counts['']), present.map((k) => tab(k, t('kind.' + k), counts[k] || 0)));
    const now = this.typeBar.querySelector('.type-tab[aria-pressed="true"]');
    if (from && now && now.dataset.kind !== from.kind) this.moveInk(from, now);
    requestAnimationFrame(() => this.markOverflow());
  }

  // The red bar of where you are travels from the tab left to the one chosen, then is that tab's own again.
  moveInk(from, now) {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const to = inkBox(now);
    if (!to.width) return;
    const bar = h('span', { class: 'type-ink', 'aria-hidden': 'true' });
    Object.assign(bar.style, { left: to.left + 'px', width: to.width + 'px', bottom: to.bottom + 'px' });
    this.typeBar.append(bar);
    now.classList.add('inking');
    const done = () => { bar.remove(); now.classList.remove('inking'); };
    bar.animate([{ transform: 'translateX(' + (from.left - to.left) + 'px) scaleX(' + (from.width / to.width) + ')' }, { transform: 'none' }],
      { duration: pace(300), easing: 'cubic-bezier(0.16, 1, 0.3, 1)' }).finished.then(done, done);
  }

  // The tab row scrolls sideways when the window is narrow; a fade at its end says there is more.
  markOverflow() {
    const bar = this.typeBar;
    if (bar) bar.classList.toggle('more', bar.scrollWidth - bar.scrollLeft - bar.clientWidth > 2);
  }

  drawGroup(group) {
    const perTab = {};
    for (const s of group.stacks) perTab[TAB_OF[s.lead.kind]] = (perTab[TAB_OF[s.lead.kind]] || 0) + s.versions.length;
    const arch = familyArch(group.family);
    // Chinese words in this line keep their natural spacing; only the Latin codes are tracked out (see DESIGN.md).
    const parts = [
      group.family === 'unknown' ? [cjk(t('fam.unknown'))] : [cjk(t('lib.meta_base')), '  ' + familyName(group.family)],
      arch ? [cjk(t('lib.meta_arch', { arch }))] : null,
      ...TAB_ORDER.filter((k) => perTab[k]).map((k) => [cjk(t('kind.' + k)), ' ' + perTab[k]]),
    ].filter(Boolean);
    const meta = parts.flatMap((p, i) => (i ? ['  ·  ', ...p] : p));
    const key = 'g:' + group.family;
    const head = h('header', { class: 'group-head', id: 'g-' + group.family },
      h('h2', { class: 'group-name' }, familyCode(group.family)),
      h('span', { class: 'group-count' }, String(group.n)),
      h('p', { class: 'group-meta' }, meta), this.foldButton(key));
    group.head = head;
    return h('section', { class: 'group' + (this.isFolded(key) ? ' is-folded' : ''), 'aria-label': familyName(group.family), 'data-fold': key }, head,
      h('div', { class: 'cards' }, group.stacks.map((s) => this.drawStack(s))));
  }

  // The versions of one civitai page: a row headed by the page's name and their number, each version a card of its own.
  drawStack(stack) {
    if (stack.versions.length < 2) return this.drawCard(stack.lead);
    const key = 's:' + stack.key;
    return h('section', { class: 'set' + (this.isFolded(key) ? ' is-folded' : ''), 'aria-label': stack.lead.title, 'data-fold': key },
      h('h3', { class: 'set-head' }, this.foldButton(key), h('span', { class: 'set-name' }, titleOf(stack.lead, currentLang())), h('span', { class: 'set-n' }, String(stack.versions.length))),
      stack.versions.map((v) => this.drawCard(v, true)));
  }

  // ---- folding groups and sets (the user's choice of the drafts, 2026-10-08): a folded group keeps a one-line banner,
  // a folded set its heading; remembered in this browser; a search shows everything it finds, folded or not ----------
  isFolded(key) {
    return !this.query && this.folds.has(key);
  }

  foldButton(key) {
    const shut = this.isFolded(key);
    return h('button', { class: 'fold-btn', type: 'button', 'aria-expanded': String(!shut), title: t(shut ? 'lib.unfold' : 'lib.fold'),
      'aria-label': t(shut ? 'lib.unfold' : 'lib.fold'), onclick: (e) => { e.stopPropagation(); this.setFold(key, !this.folds.has(key)); } }, icon('i-next'));
  }

  setFold(key, shut, keep) {
    if (shut) this.folds.add(key); else this.folds.delete(key);
    const section = this.list.querySelector('[data-fold="' + CSS.escape(key) + '"]');
    if (section) {
      section.classList.toggle('is-folded', this.isFolded(key));
      const button = section.querySelector(':scope > header > .fold-btn, :scope > .set-head > .fold-btn');
      if (button) {
        button.setAttribute('aria-expanded', String(!this.isFolded(key)));
        button.title = t(this.isFolded(key) ? 'lib.unfold' : 'lib.fold');
        button.setAttribute('aria-label', button.title);
      }
    }
    if (!keep) this.saveFolds();
  }

  saveFolds() {
    remember(KEYS.folds, JSON.stringify([...this.folds].slice(-800)));
    this.drawFoldAll();
    this.scheduleSpy();
  }

  // One button over the roster folds every set shown, or opens them all again.
  foldAllSets() {
    const keys = this.setKeys();
    const shut = !keys.every((k) => this.folds.has(k));
    for (const k of keys) this.setFold(k, shut, true);
    this.saveFolds();
  }

  setKeys() {
    return [...this.list.querySelectorAll('.set[data-fold]')].map((s) => s.dataset.fold);
  }

  drawFoldAll() {
    const keys = this.setKeys();
    this.foldAll.hidden = !keys.length || Boolean(this.query);
    this.foldAll.textContent = t(keys.length && keys.every((k) => this.folds.has(k)) ? 'lib.unfold_sets' : 'lib.fold_sets');
  }

  // A model opened from elsewhere, or by the keys, is never left inside a folded group or set.
  unfoldTo(card) {
    let section = card && card.closest('.is-folded');
    while (section) {
      this.setFold(section.dataset.fold, false, true);
      section = card.closest('.is-folded');
    }
    this.saveFolds();
  }

  coverSrc(m) {
    const stamp = this.coverStamps.get(m.sha256);
    return stamp ? m.cover + '?v=' + stamp : m.cover;
  }

  drawCard(m, inSet = false) {
    const masked = this.mask && m.cover && m.cover_level >= MASK_FROM;
    const kind = t('kind.' + m.kind);
    let cover;
    if (m.hash_pending) {
      cover = h('span', { class: 'cover ph' }, h('b', null, kind), h('small', null, t('lib.pending')));
    } else if (m.cover && !masked) {
      cover = h('img', { class: 'cover', src: this.coverSrc(m), alt: '', loading: 'lazy', decoding: 'async', 'data-rated': m.cover_level >= MASK_FROM });
    } else if (m.cover) {
      cover = h('span', { class: 'cover', 'data-kind': kind });
      this.lazyMosaic(cover, this.coverSrc(m));
    } else {
      cover = h('span', { class: 'cover ph' }, h('b', null, kind), h('small', null, baseName(m.comfy_name)));
    }
    // The entry number is the short fingerprint civitai itself shows (AutoV2: the first ten characters of the SHA-256).
    // Below it, what tells two entries apart: the kind when every type is listed, the version when one type is.
    const code = m.sha256 ? m.sha256.slice(0, 10).toUpperCase() : t('lib.pending');
    const meta = !m.identified_by ? [t('lib.unidentified'), formatBytes(m.bytes)]
      : this.kind && !inSet ? [formatBytes(m.bytes), m.version_name] : [kind, formatBytes(m.bytes)];
    const title = titleOf(m, currentLang());
    return h('button', { class: 'card' + (m.hash_pending ? ' waiting' : ''), type: 'button', 'data-sha': m.sha256 || '',
      title: m.hash_pending ? t('dos.pending') : title + (m.version_name ? ' · ' + m.version_name : ''), disabled: m.hash_pending },
      cover,
      m.sha256 ? h('span', { class: 'pick-box', title: t('sel.pick'), 'aria-hidden': 'true' }) : null,
      m.cover_level >= MASK_FROM ? h('span', { class: 'level', title: t('lib.rated', { level: levelName(m.cover_level) }) }, levelName(m.cover_level)) : null,
      h('span', { class: 'strip' },
        // a new version on civitai: a yellow plate at the cover's lower left (the user's choice, 2026-10-14)
        m.update_label ? h('span', { class: 'new-plate', title: t('lib.new_plate_title', { version: m.update_label }) },
          h('b', null, t('lib.new_plate')), h('i', null, m.update_label)) : null,
        h('span', { class: 'name' }, (inSet && m.version_name) || title || baseName(m.comfy_name)),
        h('span', { class: 'code' }, code),
        h('span', { class: 'meta' }, meta.filter(Boolean).join(' · '))));
  }

  // A masked cover: the picture is fetched only when the card comes near the screen, and never shown unmasked.
  lazyMosaic(slot, src) {
    if (!this.mosaicObserver) {
      this.mosaicObserver = new IntersectionObserver((entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          this.mosaicObserver.unobserve(entry.target);
          const target = entry.target;
          loadImage(target.dataset.src).then((img) => {
            const canvas = mosaicOf(img, CARD_ASPECT);
            canvas.className = 'cover';
            target.replaceWith(canvas);
          }).catch(() => {
            target.className = 'cover ph';
            fill(target, h('b', null, target.dataset.kind || ''), h('small', null, t('dos.sample_failed_short')));
          });
        }
      }, { rootMargin: '600px 0px' });
    }
    slot.dataset.src = src;
    this.mosaicObserver.observe(slot);
  }

  drawRail() {
    fill(this.railList, this.groups.map((g) => {
      const item = h('button', { class: 'rail-item', type: 'button', 'data-family': g.family,
        'aria-label': familyName(g.family) + ' ' + g.n,
        onclick: () => g.head && g.head.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' }) },
        h('span', { class: 'code' }, familyCode(g.family)), h('span', { class: 'n' }, String(g.n)));
      g.railItem = item;
      return h('li', null, item);
    }));
    this.current = null;
    this.tellWindow();
  }

  // The Windows app's loading screen walks down this index (desktop/loader-src): the window keeps the base models of
  // the whole library, as the index shows them unfiltered, for the next start.
  tellWindow() {
    const view = window.chrome && window.chrome.webview;
    if (!view || this.query || this.kind) return;
    const families = this.groups.filter((g) => g.family !== 'unknown')
      .map((g) => [familyCode(g.family), familyName(g.family), familyArch(g.family) || '']);
    const text = JSON.stringify(families);
    if (!families.length || text === this.told) return;
    this.told = text;
    view.postMessage({ cmd: 'families', families });
  }

  // ---- where you are -------------------------------------------------------------------------------------------
  scheduleSpy() {
    if (this.spyQueued) return;
    this.spyQueued = true;
    requestAnimationFrame(() => { this.spyQueued = false; this.spy(); });
  }

  spy() {
    if (!this.groups.length) { this.current = null; this.placeMarker(); return; }
    const narrow = window.matchMedia('(max-width: 760px)').matches;
    const topH = narrow ? this.rail.getBoundingClientRect().bottom : this.bar.getBoundingClientRect().bottom;
    let current = this.groups[0];
    for (const g of this.groups) {
      if (g.head && g.head.getBoundingClientRect().top <= topH + 40) current = g;
      else break;
    }
    if (current !== this.current) {
      if (this.current && this.current.railItem) this.current.railItem.removeAttribute('aria-current');
      this.current = current;
      if (current.railItem) {
        current.railItem.setAttribute('aria-current', 'true');
        current.railItem.scrollIntoView({ block: 'nearest', inline: 'nearest' });
      }
    }
    this.placeMarker();
  }

  placeMarker() {
    const item = this.current && this.current.railItem;
    if (!item) { this.marker.style.setProperty('--marker-y', '-40px'); return; }
    const y = item.offsetTop - this.railList.scrollTop + item.offsetHeight / 2 - 10;
    this.marker.style.setProperty('--marker-y', y + 'px');
  }

  // ---- the selected model --------------------------------------------------------------------------------------
  // The dossier shows one model (the address names it); the selection may hold more. A model opened from elsewhere (a
  // link, the dossier itself) is selected alone.
  select(sha, { scroll } = {}) {
    const changed = sha !== this.selected;
    this.selected = sha;
    if (!this.picked.has(sha) || !this.multi()) { this.picked = new Set(sha ? [sha] : []); this.anchor = sha; }
    this.cursor = sha;
    this.renderPicks();
    const card = this.cardOf(sha);
    if (card && scroll && card.closest('.is-folded')) this.unfoldTo(card);
    if (card && scroll) {
      const r = card.getBoundingClientRect();
      if (r.top < this.bar.getBoundingClientRect().bottom || r.bottom > window.innerHeight) card.scrollIntoView({ block: 'center' });
    }
    if (changed && !this.multi()) snap(card);       // the notch snaps on
    this.dossier.show(sha);
    this.dossierEl.classList.toggle('open', Boolean(sha));
  }

  deselect() {
    this.selected = null;
    this.picked.clear();
    this.renderPicks();
    this.dossierEl.classList.remove('open');
    if (window.matchMedia('(max-width: 1180px)').matches) this.dossier.show(null);
    else this.autoSelect();
  }

  cardOf(sha) {
    return sha ? this.list.querySelector('.card[data-sha="' + sha + '"]') : null;
  }

  // ---- selecting several ----------------------------------------------------------------------------------------
  // A click selects a card alone and opens it; Ctrl+click or the corner box adds it or takes it out; Shift+click adds
  // the run from the last card clicked. A box drawn from the space between the cards selects what it covers. Two or
  // more selected put their count and actions where the dossier was.
  multi() {
    return this.picked.size >= 2;
  }

  order() {
    // (the cards of a folded group or set are out of reach of the keys and of Ctrl+A)
    return [...this.list.querySelectorAll('.card[data-sha]:not([disabled])')].filter((c) => !c.closest('.is-folded'))
      .map((c) => c.dataset.sha).filter(Boolean);
  }

  wireCards() {
    this.list.addEventListener('click', (e) => {
      const card = e.target.closest('.card');
      if (!card || !card.dataset.sha || this.swept) return;
      this.pickCard(card.dataset.sha, e, Boolean(e.target.closest('.pick-box')));
    });
    this.wireMarquee();
  }

  pickCard(sha, e, box) {
    if (e.shiftKey && !box) {
      const keys = this.order();
      const a = keys.indexOf(this.anchor || this.selected);
      const b = keys.indexOf(sha);
      if (a >= 0 && b >= 0) for (const k of keys.slice(Math.min(a, b), Math.max(a, b) + 1)) this.picked.add(k);
      this.cursor = sha;
      this.renderPicks();
      return;
    }
    if (box || e.ctrlKey || e.metaKey) { this.togglePick(sha); return; }
    this.picked = new Set([sha]);
    this.anchor = sha;
    if (location.hash === '#/m/' + sha) this.select(sha);
    else location.hash = '#/m/' + sha;
  }

  // Add a model to the selection or take it out; the one left alone is opened in the dossier.
  togglePick(sha) {
    if (this.picked.has(sha)) this.picked.delete(sha); else this.picked.add(sha);
    this.anchor = sha;
    this.cursor = sha;
    if (this.picked.size === 1) {
      const only = [...this.picked][0];
      if (only !== this.selected) { location.hash = '#/m/' + only; return; }
    }
    this.renderPicks();
  }

  // Back to the one model the dossier shows.
  collapse() {
    this.picked = new Set(this.selected ? [this.selected] : []);
    this.anchor = this.selected;
    this.renderPicks();
  }

  // The ticks on the cards, the notch on the one the dossier shows (while only it is selected), and the pane.
  renderPicks() {
    const multi = this.multi();
    this.roster.dataset.selecting = String(multi);
    for (const card of this.list.querySelectorAll('.card[data-sha]')) {
      const sha = card.dataset.sha;
      card.classList.toggle('is-checked', this.picked.has(sha));
      if (sha === this.selected && !multi) card.setAttribute('aria-current', 'true');
      else card.removeAttribute('aria-current');
    }
    this.libEl.classList.toggle('selecting', multi);
    if (multi) this.drawSelection();
  }

  drawSelection() {
    if (!this.selParts) {
      const button = (label, iconId, onclick, solid) => h('button', { class: 'btn ' + (solid ? 'btn-solid' : 'btn-line'), type: 'button', onclick },
        icon(iconId), h('span', null, label));
      this.selParts = {
        n: h('b', { class: 'sel-n' }),
        what: h('span', { class: 'sel-what' }),
        note: h('p', { class: 'sel-note', role: 'status' }),
        fill: button(t('sel.fill'), 'i-retry', () => this.fillPicked(), true),
      };
      const p = this.selParts;
      fill(this.selPane, h('div', { class: 'sel' },
        h('div', { class: 'sel-head' }, h('span', { class: 'sel-title' }, t('sel.title')), p.n, p.what),
        h('div', { class: 'sel-actions' },
          button(t('sel.all'), 'i-check', () => { for (const k of this.order()) this.picked.add(k); this.renderPicks(); }),
          button(t('sel.clear'), 'i-x', () => this.collapse()),
          p.fill,
          button(t('dos.trash'), 'i-trash', () => this.trashPicked())),
        p.note));
    }
    const models = [...this.picked].map((s) => this.modelOf.get(s)).filter(Boolean);
    this.selParts.n.textContent = String(models.length);
    this.selParts.what.textContent = formatBytes(models.reduce((n, m) => n + (m.bytes || 0), 0));
  }

  say(text, bad) {
    const note = this.selParts && this.selParts.note;
    if (!note) return;
    note.className = 'sel-note' + (bad ? ' bad' : '');
    note.textContent = text;
  }

  // Filling in the selected models' details: their civitai pages, example images and the local model's reading.
  async fillPicked() {
    this.say('');
    try {
      await api('POST', '/api/ui/fill', { action: 'start', shas: [...this.picked] });
      this.say(t('sel.fill_started', { n: this.picked.size }));
    } catch (e) {
      this.say(e.message, true);
    }
  }

  // Deleting the selected models: every file goes to the recycle bin, listed first model by model.
  async trashPicked() {
    const shas = [...this.picked].filter((s) => this.modelOf.has(s));
    if (!shas.length) return;
    let plans;
    try {
      plans = await Promise.all(shas.map(async (sha) => ({ sha, files: (await api('GET', '/api/ui/models/' + sha + '/trash')).files })));
    } catch (e) {
      this.say(e.message, true);
      return;
    }
    if (!this.trashDialog) {
      this.trashDialog = h('dialog', { class: 'confirm', 'aria-labelledby': 'trash-many-title' });
      document.body.append(this.trashDialog);
    }
    const dialog = this.trashDialog;
    const note = h('p', { class: 'confirm-msg', role: 'status' });
    const movable = (f) => f.inside && f.recycle_bin;
    const count = plans.filter((p) => p.files.some(movable)).length;
    const go = h('button', { class: 'btn btn-solid', type: 'button', disabled: !count }, icon('i-trash'), t('dos.trash_confirm'));
    // as the card names it: a version of a set by its own name
    const nameOf = (sha) => {
      const m = this.modelOf.get(sha);
      if (this.setOf(sha) && m.version_name) return m.version_name;
      return m.title + (m.version_name ? ' · ' + m.version_name : '');
    };
    const list = plans.map((p) => h('li', null, h('div', { class: 'confirm-file' }, h('span', { class: 'what' },
      h('span', { class: 'model' }, nameOf(p.sha)),
      p.files.map((f) => {
        const blocked = !f.inside ? t('dos.trash_outside') : !f.recycle_bin ? t('dos.trash_no_bin') : null;
        return [h('span', { class: 'path' + (blocked ? ' off' : '') }, f.path, ' ', h('small', { class: 'size' }, formatBytes(f.bytes))),
          blocked ? h('small', { class: 'bad' }, blocked) : null];
      })))));
    go.onclick = async () => {
      go.disabled = true;
      note.className = 'confirm-msg';
      note.textContent = '';
      let done = 0;
      try {
        for (const p of plans) {
          const paths = p.files.filter(movable).map((f) => f.path);
          if (!paths.length) continue;
          await api('POST', '/api/ui/models/' + p.sha + '/trash', { paths });
          done += 1;
        }
        dialog.close();
      } catch (e) {
        note.className = 'confirm-msg bad';
        note.textContent = e.message;
      }
      if (done) {
        this.picked.clear();
        location.hash = '#/';
        this.flashText(t('sel.trashed', { n: done }));
        this.refresh(false);
      }
    };
    fill(dialog,
      h('h2', { id: 'trash-many-title' }, t('sel.trash_title', { n: shas.length })),
      h('p', { class: 'confirm-lead' }, t('dos.trash_lead')),
      h('ul', { class: 'confirm-files' }, list),
      note,
      h('div', { class: 'confirm-actions' },
        h('button', { class: 'btn btn-line', type: 'button', onclick: () => dialog.close() }, t('common.cancel')), go));
    dialog.showModal();
  }

  flashText(text) {
    this.flash.hidden = false;
    this.flash.textContent = text;
    clearTimeout(this.flashTimer);
    this.flashTimer = setTimeout(() => { this.flash.hidden = true; }, 8000);
  }

  // A box drawn with the mouse from anywhere in the roster selects the cards it covers (with Shift or Ctrl, adds them);
  // the roster scrolls when the pointer nears its top or bottom; a press that moves less than 5 px is a click; Esc
  // puts the selection back. Only where the roster scrolls in its own column, and not by touch.
  wireMarquee() {
    const EDGE = 40;
    const SLIP = 5;
    const grid = this.roster;
    let drag = null;
    const finish = (cancel) => {
      const d = drag;
      if (!d) return;
      drag = null;
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      window.removeEventListener('pointercancel', cancelled);
      if (!d.started) return;
      cancelAnimationFrame(d.raf);
      window.removeEventListener('keydown', keys, true);
      d.box.remove();
      grid.classList.remove('sweeping');
      this.swept = true;
      setTimeout(() => { this.swept = false; }, 0);
      if (cancel) { this.picked = d.before; this.anchor = d.anchor; }
      const only = this.picked.size === 1 ? [...this.picked][0] : null;
      if (only && only !== this.selected) location.hash = '#/m/' + only;     // a box over one card opens it
      else this.renderPicks();
    };
    const sweep = () => {
      const d = drag;
      if (!d || !d.started) return;
      const r = grid.getBoundingClientRect();
      const cx = Math.min(Math.max(d.x, r.left), r.left + grid.clientWidth) - r.left + grid.scrollLeft;
      const cy = Math.min(Math.max(d.y, r.top), r.top + grid.clientHeight) - r.top + grid.scrollTop;
      const x = Math.min(d.x0, cx), y = Math.min(d.y0, cy), w = Math.abs(cx - d.x0), hgt = Math.abs(cy - d.y0);
      Object.assign(d.box.style, { left: x + 'px', top: y + 'px', width: w + 'px', height: hgt + 'px' });
      const next = new Set(d.base);
      for (const card of this.list.querySelectorAll('.card[data-sha]:not([disabled])')) {
        const c = card.getBoundingClientRect();
        const l = c.left - r.left + grid.scrollLeft;
        const top = c.top - r.top + grid.scrollTop;
        if (l < x + w && l + c.width > x && top < y + hgt && top + c.height > y) next.add(card.dataset.sha);
      }
      if (next.size === this.picked.size && [...next].every((k) => this.picked.has(k))) return;
      this.picked = next;
      this.renderPicks();
    };
    const tick = () => {
      const d = drag;
      if (!d || !d.started) return;
      d.raf = requestAnimationFrame(tick);
      const r = grid.getBoundingClientRect();
      const top = r.top + EDGE;
      const bottom = r.top + grid.clientHeight - EDGE;
      const v = d.y < top ? -Math.min(EDGE, top - d.y) : d.y > bottom ? Math.min(EDGE, d.y - bottom) : 0;
      if (v) grid.scrollTop += v / EDGE * 14;
      if (grid.scrollTop !== d.scrolled) { d.scrolled = grid.scrollTop; sweep(); }
    };
    const move = (e) => {
      const d = drag;
      if (!d || e.pointerId !== d.id) return;
      d.x = e.clientX;
      d.y = e.clientY;
      if (!d.started) {
        if (Math.hypot(d.x - d.sx, d.y - d.sy) < SLIP) return;
        d.before = new Set(this.picked);
        d.anchor = this.anchor;
        d.base = d.add ? new Set(this.picked) : new Set();
        const r = grid.getBoundingClientRect();
        d.x0 = d.sx - r.left + grid.scrollLeft;
        d.y0 = d.sy - r.top + grid.scrollTop;
        d.box = h('div', { class: 'marquee', 'aria-hidden': 'true' });
        grid.append(d.box);
        grid.classList.add('sweeping');
        window.addEventListener('keydown', keys, true);
        d.started = true;
        d.scrolled = grid.scrollTop;
        d.raf = requestAnimationFrame(tick);
      }
      sweep();
    };
    const up = (e) => { if (drag && e.pointerId === drag.id) finish(false); };
    const cancelled = (e) => { if (drag && e.pointerId === drag.id) finish(true); };
    const keys = (e) => { if (e.key === 'Escape' && drag && drag.started) { e.preventDefault(); e.stopPropagation(); finish(true); } };
    grid.addEventListener('pointerdown', (e) => {
      this.swept = false;
      if (drag || e.button !== 0 || e.pointerType === 'touch' || !this.ownScroll() || document.querySelector('dialog[open]')) return;
      if (e.target.closest('button:not(.card), a, input, .roster-bar, .empty')) return;
      const r = grid.getBoundingClientRect();
      if (e.clientX > r.left + grid.clientWidth || e.clientY > r.top + grid.clientHeight) return;      // the scrollbar
      drag = { id: e.pointerId, sx: e.clientX, sy: e.clientY, x: e.clientX, y: e.clientY, add: e.shiftKey || e.ctrlKey || e.metaKey, started: false };
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', up);
      window.addEventListener('pointercancel', cancelled);
    });
  }

  // Keys in the roster: the arrows move the selection (with Shift, it runs from where it started), Ctrl+A selects every
  // card shown, Ctrl+Space adds the card or takes it out, Delete deletes what is selected, Esc goes back to one.
  keys(e) {
    if (document.querySelector('dialog[open]') || document.querySelector('.intake.open')) return;
    const target = e.target;
    if (target.closest && target.closest('input, textarea, select, [contenteditable="true"], .dossier, .intake')) return;
    if (e.key === 'Escape') {
      if (this.multi()) { e.stopPropagation(); this.collapse(); }
      return;
    }
    const keys = this.order();
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'a') {
      e.preventDefault();
      for (const k of keys) this.picked.add(k);
      this.renderPicks();
      return;
    }
    const at = keys.indexOf(this.cursor || this.selected);
    const onControl = target.closest && target.closest('button, a') && !target.closest('.card');
    if ((e.ctrlKey || e.metaKey) && e.key === ' ' && at >= 0 && !onControl) {
      e.preventDefault();
      if (!e.repeat) this.togglePick(keys[at]);
      return;
    }
    if (e.ctrlKey || e.metaKey || e.altKey || onControl) return;
    if (e.key === 'Delete') {
      e.preventDefault();
      if (!e.repeat && this.picked.size) this.trashPicked();
      return;
    }
    if (at < 0) return;
    let next = -1;
    if (e.key === 'ArrowLeft') next = at - 1;
    else if (e.key === 'ArrowRight') next = at + 1;
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = keys.length - 1;
    else if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
      // the nearest card in the row above or below
      const cur = this.cardOf(keys[at]).getBoundingClientRect();
      const down = e.key === 'ArrowDown';
      const mid = cur.left + cur.width / 2;
      let best = null;
      for (const card of this.list.querySelectorAll('.card[data-sha]:not([disabled])')) {
        if (card.closest('.is-folded')) continue;
        const r = card.getBoundingClientRect();
        const gap = down ? r.top - cur.bottom : cur.top - r.bottom;
        if (gap < -1) continue;
        const score = gap * 4 + Math.abs(r.left + r.width / 2 - mid);
        if (!best || score < best.score) best = { score, sha: card.dataset.sha };
      }
      if (best) next = keys.indexOf(best.sha);
    } else return;
    e.preventDefault();
    if (next < 0 || next >= keys.length) return;
    const card = this.cardOf(keys[next]);
    if (e.shiftKey) {
      let from = keys.indexOf(this.anchor || this.selected);
      if (from < 0) from = at;
      this.picked = new Set(keys.slice(Math.min(from, next), Math.max(from, next) + 1));
      this.cursor = keys[next];
      this.renderPicks();
    } else {
      this.picked = new Set([keys[next]]);
      this.anchor = keys[next];
      location.hash = '#/m/' + keys[next];
    }
    if (card) { card.scrollIntoView({ block: 'nearest' }); card.focus({ preventScroll: true }); }
  }

  setMask(on) {
    this.mask = on;
    remember(KEYS.mask, on ? '1' : '0');
    this.drawEyes();
    this.render(false);
    if (on) {
      if (this.selected) this.dossier.show(this.selected, true);
      return;
    }
    // the covers come out of hiding: the ones in view are decoded as they appear
    for (const img of this.roster.querySelectorAll('.card img.cover[data-rated]')) if (onScreen(img)) decode(img, { cells: [16, 8, 4], step: 90 });
    if (this.selected) this.dossier.revealCovers();
  }

  // The eye shows the state (struck through: the covers are hidden); its tooltip says what a click does.
  drawEyes() {
    for (const button of this.maskBtns) {
      fill(button, icon(this.mask ? 'i-eye-off' : 'i-eye'));
      button.setAttribute('aria-pressed', String(this.mask));
      button.title = t(this.mask ? 'lib.covers_show' : 'lib.covers_hide');
    }
  }

  // The base models the library already names, offered when a model's base model is edited.
  baseModels() {
    return [...new Set(this.models.map((m) => m.base_model).filter(Boolean))].sort((a, b) => a.localeCompare(b));
  }

  // The dossier changed a model (its details, its pictures, its cover): read the list again.
  changed(sha, cover) {
    if (cover && sha) this.coverStamps.set(sha, Date.now());
    this.refresh(false);
  }

  // The civitai page this model shares with other versions here: its name; null for a model on its own.
  setOf(sha) {
    const stack = this.stackOf && this.stackOf.get(sha);
    return stack && stack.versions.length > 1 ? { title: titleOf(stack.lead, currentLang()), n: stack.versions.length } : null;
  }

  // ---- folders of the settings that no longer exist ------------------------------------------------------------
  // After a scan, folders the settings list but the disk no longer has are put to the user once: removed from the
  // settings, or kept (a drive unplugged for now) and not asked about again (the user's wish, 2026-10-06).
  checkFolders(scan) {
    if (!scan || scan.running || !scan.finished_at) return;
    let kept;
    try { kept = new Set(JSON.parse(recall(KEYS.foldersKept, '[]'))); } catch (e) { kept = new Set(); }
    const key = (path) => String(path).toLowerCase();
    const missing = (scan.folders || []).filter((f) => !f.exists && !kept.has(key(f.path)) && !askedFolders.has(key(f.path)));
    if (!missing.length || document.querySelector('dialog[open]')) return;
    missing.forEach((f) => askedFolders.add(key(f.path)));
    this.askFolders(missing, kept, key);
  }

  askFolders(missing, kept, key) {
    if (!this.foldersDialog) {
      this.foldersDialog = h('dialog', { class: 'confirm', 'aria-labelledby': 'folders-title' });
      document.body.append(this.foldersDialog);
    }
    const dialog = this.foldersDialog;
    const many = missing.length > 1;
    const chosen = new Set(missing.map((f) => f.path));          // to remove; the others are kept
    const note = h('p', { class: 'confirm-msg', role: 'status' });
    const go = h('button', { class: 'btn btn-solid', type: 'button' }, t('lib.folders_remove'));
    const sync = () => { go.disabled = !chosen.size; };
    const typeName = (type) => (t('type.' + type) !== 'type.' + type ? t('type.' + type) : t('type.other', { name: type }));
    const keep = (paths) => {
      paths.forEach((p) => kept.add(key(p)));
      remember(KEYS.foldersKept, JSON.stringify([...kept]));
    };
    const list = missing.map((f) => {
      const box = many ? h('input', { type: 'checkbox', checked: true,
        onchange: (e) => { if (e.target.checked) chosen.add(f.path); else chosen.delete(f.path); sync(); } }) : null;
      return h('li', null, h(many ? 'label' : 'div', { class: 'confirm-file' }, box,
        h('span', { class: 'what' }, h('span', { class: 'path' }, f.path),
          h('small', null, (f.comfy_types || []).map(typeName).join(currentLang() === 'zh' ? '、' : ', ')))));
    });
    go.onclick = async () => {
      go.disabled = true;
      note.className = 'confirm-msg';
      note.textContent = '';
      try {
        const answer = await api('POST', '/api/ui/folders/forget', { paths: [...chosen] });
        keep(missing.map((f) => f.path).filter((p) => !chosen.has(p)));
        dialog.close();
        this.flashText(t('lib.folders_removed', { n: answer.removed.length }));
        this.refresh(false);
      } catch (e) {
        note.className = 'confirm-msg bad';
        note.textContent = e.message;
        sync();
      }
    };
    fill(dialog,
      h('h2', { id: 'folders-title' }, t('lib.folders_title')),
      h('p', { class: 'confirm-lead' }, t('lib.folders_lead')),
      h('ul', { class: 'confirm-files' }, list),
      note,
      h('div', { class: 'confirm-actions' },
        h('button', { class: 'btn btn-line', type: 'button', onclick: () => { keep(missing.map((f) => f.path)); dialog.close(); } },
          t('lib.folders_keep')), go));
    // Esc keeps them for now: they are asked about again at the next visit
    sync();
    dialog.showModal();
  }

  // ---- the inbox folders --------------------------------------------------------------------------------------
  // Model files dropped into the inbox folders (the setup's models folder, and the one next to the program) are listed
  // with where each would go; only the ticked ones are moved. Asked once a visit.
  async checkInbox() {
    if (askedInbox) return;
    askedInbox = true;
    let plan;
    try { plan = await api('GET', '/api/ui/inbox'); } catch (e) { return; }
    if (!this.alive || document.querySelector('dialog[open]')) return;
    if (plan.items.length || plan.progress.running) this.askInbox(plan);
  }

  askInbox(plan) {
    if (!this.inboxDialog) {
      this.inboxDialog = h('dialog', { class: 'confirm sort', 'aria-labelledby': 'inbox-title' });
      document.body.append(this.inboxDialog);
    }
    const dialog = this.inboxDialog;
    const chosen = new Set(plan.items.map((i) => i.path));
    const note = h('p', { class: 'confirm-msg', role: 'status' });
    const gauge = h('div', { class: 'inbox-gauge', hidden: true }, h('i'));
    const go = h('button', { class: 'btn btn-solid', type: 'button' });
    const later = h('button', { class: 'btn btn-line', type: 'button', onclick: () => dialog.close() }, t('lib.inbox_later'));
    const stop = h('button', { class: 'btn btn-line', type: 'button', hidden: true,
      onclick: () => api('POST', '/api/ui/inbox', { action: 'stop' }).catch(() => {}) }, t('lib.inbox_stop'));
    const boxes = [];
    const sync = () => {
      go.textContent = t('lib.inbox_move', { n: chosen.size });
      go.disabled = !chosen.size;
    };
    const kindOf = (item) => (item.comfy_type === 'llm' ? t('lib.inbox_llm') : t('kind.' + item.kind));
    const rows = plan.items.map((item) => {
      const box = h('input', { type: 'checkbox', checked: true,
        onchange: (e) => { if (e.target.checked) chosen.add(item.path); else chosen.delete(item.path); sync(); } });
      boxes.push(box);
      // the base model's code, when the header tells it (a VAE's often does not); a language model's architecture
      const code = item.comfy_type === 'llm' ? (item.arch || 'GGUF').toUpperCase()
        : item.family && item.family !== 'unknown' ? familyCode(item.family) : null;
      return h('li', null, h('label', { class: 'confirm-file' }, box,
        h('span', { class: 'what' }, h('span', { class: 'path' }, item.name),
          h('small', { class: 'dest' }, code ? [h('b', null, code), ' · '] : null, kindOf(item), ' → ',
            item.comfy_type === 'llm' ? t('lib.inbox_llm_dest') : item.rel)),
        h('small', { class: 'size' }, formatBytes(item.bytes))));
    });
    const reasons = { not_model: 'lib.inbox_not_model', unknown: 'lib.inbox_unknown', no_folder: 'lib.inbox_no_folder' };
    const left = plan.skipped.map((item) => h('li', { class: 'off' }, h('div', { class: 'confirm-file' },
      h('span', { class: 'what' }, h('span', { class: 'path' }, item.name), h('small', null, t(reasons[item.reason]))),
      h('small', { class: 'size' }, formatBytes(item.bytes)))));
    const total = plan.items.length + plan.skipped.length;
    const bytes = [...plan.items, ...plan.skipped].reduce((n, i) => n + i.bytes, 0);

    const follow = async () => {
      let state;
      try { state = (await api('GET', '/api/ui/inbox')).progress; } catch (e) { state = null; }
      if (!state) { setTimeout(follow, 1500); return; }
      if (state.running) {
        gauge.hidden = false;
        gauge.firstChild.style.width = (state.bytes_total ? 100 * state.bytes_done / state.bytes_total : 0) + '%';
        note.className = 'confirm-msg';
        note.textContent = t('lib.inbox_moving', { name: state.current || '', done: Math.min(state.done + 1, state.total), total: state.total });
        setTimeout(follow, 700);
        return;
      }
      if (state.failed.length) {
        gauge.hidden = true;
        note.className = 'confirm-msg bad';
        note.textContent = t('lib.inbox_failed', { n: state.failed.length, names: state.failed.map((f) => f.name).join(currentLang() === 'zh' ? '、' : ', ') });
        stop.hidden = true;
        later.hidden = false;
        later.textContent = t('lib.inbox_close');
      } else {
        dialog.close();
      }
      if (state.moved) this.flashText(t('lib.inbox_moved', { n: state.moved }));
      this.refresh(false);
    };
    const moving = () => {
      boxes.forEach((b) => { b.disabled = true; });
      go.disabled = true;
      later.hidden = true;
      stop.hidden = false;
      follow();
    };
    go.onclick = async () => {
      go.disabled = true;
      note.className = 'confirm-msg';
      note.textContent = '';
      try {
        await api('POST', '/api/ui/inbox', { action: 'start', paths: [...chosen] });
        moving();
      } catch (e) {
        note.className = 'confirm-msg bad';
        note.textContent = e.message;
        sync();
      }
    };
    fill(dialog,
      h('h2', { id: 'inbox-title' }, t('lib.inbox_title')),
      h('p', { class: 'confirm-name' }, t('lib.inbox_name', { n: total, size: formatBytes(bytes) })),
      h('p', { class: 'confirm-lead' }, t('lib.inbox_lead')),
      h('ul', { class: 'confirm-files' }, rows, left),
      gauge, note,
      h('div', { class: 'confirm-actions' }, later, stop, go));
    sync();
    dialog.showModal();
    if (plan.progress.running) moving();
  }

  // A model has gone to the recycle bin: say so over the roster, and read the list again.
  removed(name, sha) {
    // a version gone from a set leaves the dossier on the set's next version; otherwise on the first model
    const stack = this.stackOf && this.stackOf.get(sha);
    const rest = stack ? stack.versions.filter((v) => v.sha256 !== sha) : [];
    location.hash = rest.length ? '#/m/' + rest[0].sha256 : '#/';
    this.flashText(t('lib.trashed', { name }));
    this.refresh(false);
  }
}
