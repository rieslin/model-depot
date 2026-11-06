// About Model Depot: the version, what the program does, its license, and the copyright line. Opened from the top bar.
import { h, fill } from './dom.js';
import { t } from './i18n.js';
import { icon } from './ui.js';

let dialog = null;

// `state`: what /api/ui/state gave (the version, the license's address, the copyright line); null if it could not be read.
export function openAbout(state) {
  const s = state || {};
  const version = s.version || '';
  if (!dialog) {
    dialog = h('dialog', { class: 'about', 'aria-labelledby': 'about-title' });
    dialog.addEventListener('click', (e) => { if (e.target === dialog) dialog.close(); });    // a click on the backdrop
    document.body.append(dialog);
  }
  fill(dialog,
    h('button', { class: 'icon-btn about-close', type: 'button', title: t('about.close'), 'aria-label': t('about.close'),
      onclick: () => dialog.close() }, icon('i-x')),
    h('div', { class: 'about-body' },
      h('h2', { id: 'about-title', class: 'wordmark' }, 'MODEL DEP', h('span', { class: 'o' }, 'O'), 'T'),
      h('p', { class: 'about-version' }, /[㐀-鿿]/.test(t('about.version')) ? h('span', { class: 'cjk' }, t('about.version')) : t('about.version'),
        ' ' + version),
      h('p', null, t('about.overview_1')),
      h('p', null, t('about.overview_2')),
      h('p', { class: 'about-license' }, h('span', null, t('about.license')),
        h('a', { href: s.license_url, target: '_blank', rel: 'noopener noreferrer' }, t('about.license_name')))),
    h('p', { class: 'about-foot' }, s.copyright || ''));
  dialog.showModal();
}
