// Start-up and the views: the model library (the main page), the settings page (also the first-run setup), and the
// inventory of the background work. The download drawer belongs to no view: it opens over whichever is shown.
import { api } from './api.js';
import { mountWindowButtons } from './desktop.js';
import { h, clear, fill } from './dom.js';
import { loadStrings, setLang, currentLang, t } from './i18n.js';
import { openAbout } from './about.js';
import { IntakeDrawer } from './intake.js';
import { Library } from './library.js';
import { renderInventory, stopInventory } from './inventory.js';
import { renderSetup } from './setup.js';
import { icon } from './ui.js';

const shell = document.getElementById('shell');
const nav = document.getElementById('top-nav');
const tools = document.getElementById('top-tools');
const tagline = document.getElementById('tagline');
let state = null;
let library = null;
let intake = null;
let shown = null;           // the view on screen
let previewLang = false;    // the settings page shows a language that is not saved yet

async function loadState() {
  state = await api('GET', '/api/ui/state');
}

function route() {
  if (!state || !state.configured) return { view: 'setup' };
  const hash = location.hash;
  if (hash === '#/settings') return { view: 'settings' };
  if (hash === '#/inventory') return { view: 'inventory' };
  const m = /^#\/m\/([0-9a-f]{64})$/.exec(hash);
  return { view: 'library', sha: m ? m[1] : null };
}

function setTagline(count) {
  const text = t('lib.tagline');
  fill(tagline, /[㐀-鿿]/.test(text) ? h('span', { class: 'cjk' }, text) : text,
    count === undefined || count === null ? '' : ' / ' + count);
}

function drawNav(view) {
  const items = [];
  if (state && state.configured) {
    if (intake) items.push(intake.button);
    items.push(h('a', { class: 'icon-btn', href: '#/inventory', title: t('nav.inventory'), 'aria-label': t('nav.inventory'),
      'aria-current': view === 'inventory' ? 'page' : null }, icon('i-list')));
    items.push(h('a', { class: 'icon-btn', href: '#/settings', title: t('nav.settings'), 'aria-label': t('nav.settings'),
      'aria-current': view === 'settings' ? 'page' : null }, icon('i-gear')));
  }
  items.push(h('button', { class: 'icon-btn', type: 'button', title: t('about.title'), 'aria-label': t('about.title'),
    onclick: () => openAbout(state ? state.version : '') }, icon('i-info')));
  const lang = (value, label) => h('button', { type: 'button', 'aria-pressed': String(currentLang() === value), lang: value,
    onclick: () => changeLanguage(value) }, label);
  items.push(h('div', { class: 'lang', role: 'group', 'aria-label': t('lib.language') }, lang('zh', '中'), lang('en', 'EN')));
  const working = library && view === 'library' ? library.working : null;
  fill(nav, working, items);
}

async function changeLanguage(value) {
  if (value === currentLang()) return;
  setLang(value);
  if (state && state.configured) {
    try {
      await api('PUT', '/api/ui/config', { language: value });
      await loadState();
    } catch (e) { /* the page still switches; the choice is just not remembered */ }
  }
  if (intake) intake.relabel();
  mountWindowButtons();
  teardown();
  draw();
}

function teardown() {
  stopInventory();
  if (library) { library.unmount(); library = null; }
  shown = null;
}

function draw() {
  // #/download?link=<address>: another program (or a bookmark) asks for the download drawer with this link read
  if (location.hash.startsWith('#/download')) {
    const link = new URLSearchParams(location.hash.split('?')[1] || '').get('link');
    location.hash = '#/';
    if (link && intake) setTimeout(() => intake.readLink(link), 0);
    return;
  }
  const r = route();
  if (previewLang && r.view !== 'settings') {
    // left the settings without saving: back to the saved language
    previewLang = false;
    if (state.language && currentLang() !== state.language) {
      setLang(state.language);
      setTagline(null);
      if (intake) intake.relabel();
      mountWindowButtons();
    }
  }
  if (r.view === 'library' && shown === 'library' && library) {
    if (r.sha) library.select(r.sha, { scroll: true });
    else library.deselect();
    return;
  }
  teardown();
  shown = r.view;
  setTagline(null);
  clear(tools);
  if (r.view === 'library') {
    library = new Library(shell, tools, nav, { state, onCount: setTagline, openLink: (url) => intake && intake.readLink(url) });
    drawNav('library');
    library.mount(r.sha);
    return;
  }
  drawNav(r.view);
  const page = h('div', { class: 'page' });
  fill(shell, page);
  window.scrollTo({ top: 0 });
  shell.scrollTop = 0;        // wider than a phone, the pages scroll inside the shell (app.css)
  if (r.view === 'inventory') {
    renderInventory(page);
    return;
  }
  renderSetup(page, {
    state,
    lang: currentLang(),
    firstRun: r.view === 'setup',
    setLanguage: (value) => {
      setLang(value);
      previewLang = r.view === 'settings';
      setTagline(null);
      drawNav(r.view);
      if (intake) intake.relabel();
      mountWindowButtons();
    },
    onSaved: async () => {
      await loadState();
      previewLang = false;
      setLang(state.language);
      if (intake) intake.relabel();
      mountWindowButtons();
      teardown();
      if (location.hash === '#/') draw();
      else location.hash = '#/';
    },
    onCancel: () => { location.hash = '#/'; },
  });
}

window.addEventListener('hashchange', draw);
window.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && shown === 'library' && /^#\/m\//.test(location.hash) && window.matchMedia('(max-width: 1180px)').matches
      && !document.querySelector('dialog[open]') && !(intake && intake.isOpen) && !document.querySelector('.dossier.editing')) {
    location.hash = '#/';
  }
});

async function boot() {
  mountWindowButtons();       // first, so the window can be moved and closed even when the rest fails; labelled again below
  try {
    await loadStrings();
    await loadState();
    setLang(state.language);
    mountWindowButtons();
    intake = new IntakeDrawer({
      enabled: () => Boolean(state && state.configured),
      kindOf: (type) => (state && state.kind_by_type && state.kind_by_type[type]) || 'other',
      onDone: () => { if (library) library.refresh(false); },
      onShow: () => { if (library) library.refresh(false); },
    });
    draw();
  } catch (e) {
    fill(shell, h('div', { class: 'page' }, h('p', { class: 'bad' }, String(e.message || e))));
  }
}

boot();
