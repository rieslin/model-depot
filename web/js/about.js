// About Model Depot: the version, what the program does, its license, and the copyright line. Opened from the top bar.
import { h, fill } from './dom.js';
import { t } from './i18n.js';
import { icon } from './ui.js';

const LICENSE = ['GNU General Public License 3.0', 'https://www.gnu.org/licenses/gpl-3.0.html'];

let dialog = null;

export function openAbout(version) {
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
        h('a', { href: LICENSE[1], target: '_blank', rel: 'noopener noreferrer' }, LICENSE[0]))),
    h('p', { class: 'about-foot' }, '©2026 ZEUS INSiGHTS'));
  dialog.showModal();
}
